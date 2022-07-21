from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import datetime
from dateutil import tz
import time
import os
import argparse
import pandas as pd
import socket
import pathlib
import json
from logging import getLogger
import shutil
import schedule
import re
import ccxt
import influxdb_client
from util.secretsmanager import get_secret
from util.mylog import create_logger
from util.ccxt_extension import gmocoin
from util.influxdb import write_influxdb

# ccxt doesn't support gmocoin atm. use extension script from 3rd party
# https://note.com/nickel_plating/n/nc6fb71417e7e
ccxt.gmocoin = gmocoin


def create_screenshot(driver, prefix):
    page_width = driver.execute_script('return document.body.scrollWidth')
    page_height = driver.execute_script('return document.body.scrollHeight')
    driver.set_window_size(page_width, page_height)

    now = datetime.datetime.now()
    currenttime = now.strftime('%Y%m%d_%H%M%S')
    filepath = f"./screenshot/{prefix}_{currenttime}.png"

    try:
        driver.save_screenshot(filepath)
        time.sleep(30)  # wait for screenshot file to be created
    except:
        pass

    if os.path.exists(filepath):
        return filepath
    else:
        return None  # don't raise exception


def fetch_price_usdjpy():
    price_usdjpy = ccxt.ftx().fetch_ohlcv(f'USD/JPY', '1m')[-1][4]
    return price_usdjpy


##################################################################
# CeFi
##################################################################
def get_cefi_portfolio(exch_config, exch_secrets):
    data = []  # list of dict
    for exch, param in exch_config.items():

        base = param['base']  # base currency for the exchange

        for account in param['accounts']:
            # for ftx subaccounts
            if exch == 'ftx' and account != 'main':
                kwargs = {'headers': {'FTX-SUBACCOUNT': account}}
            else:
                kwargs = {}

            for market in param['markets']:
                exchange = getattr(ccxt, exch)({
                    'apiKey': exch_secrets[exch]['API_KEY'],
                    'secret': exch_secrets[exch]['SECRET_KEY'],
                    'enableRateLimit': True,
                    'rate_limit': param['rate_limit'],
                    'options': {'defaultType': market},
                    **kwargs
                    })
                markets = exchange.load_markets()
                response = exchange.fetchBalance()

                for symbol, amount in response['total'].items():
                    if amount != 0.0:
                        pair = f'{symbol}/{base}'
                        kwargs = {}
                        kwargs['since'] = int(time.time() * 1000) - 5 * 60 * 1000  # fetch last 5 min

                        # get price_base (base currency depends on exchange)
                        if pair in markets.keys():
                            price_base = exchange.fetch_ohlcv(pair, '1m', **kwargs)[-1][4]
                        else:
                            if symbol == base:  # base currency
                                price_base = 1.0  # Assuming stable coins are maintaining peg

                            elif f'{base}/{symbol}' in markets.keys():
                                price_base = 1.0 / exchange.fetch_ohlcv(f'{base}/{symbol}', '1m')[-1][4]

                            elif symbol.startswith('LD'):  # binance earn
                                if f"{symbol.replace('LD', '')}" == base:  # LDUSDT
                                    price_base = 1.0
                                elif f"{symbol.replace('LD', '')}/{base}" in markets.keys():  # LDBTC
                                    price_base = exchange.fetch_ohlcv(f"{symbol.replace('LD', '')}/{base}", '1m')[-1][4]
                                else:
                                    raise ValueError(f'pair={pair} does not exist in exchange={exch}')

                            else:
                                raise ValueError(f'pair={pair} does not exist in exchange={exch}')

                        # get price_usd
                        if base in ('USD', 'USDT'):
                            price_usd = price_base
                        elif base in ('JPY'):
                            price_usd = price_base / fetch_price_usdjpy()
                        else:
                            raise ValueError(f'base={base} cannot be converted to USD')

                        # convert into dataframe
                        data.append({
                            'exchange': exch, 
                            'account': account,
                            'market': market, 
                            'symbol': symbol, 
                            'amount': amount, 
                            'price_usd': price_usd,
                            'USD': amount * price_usd,
                            'JPY': amount * price_usd * fetch_price_usdjpy()
                            })

    df_wallet = pd.DataFrame.from_dict(data) \
        .astype({
            'exchange': str, 
            'account': str,
            'market': str, 
            'symbol': str, 
            'amount': float, 
            'price_usd': float, 
            'USD': float,
            'JPY': float
            })
    
    return df_wallet


##################################################################
# DeFi
##################################################################
def fetch_apeboard(driver, url, os_default_download_path, data_store_path):
    driver.get(url)
    WebDriverWait(driver, 60).until(EC.presence_of_all_elements_located)

    time.sleep(30)  # wait for the page to be loaded

    # Click Export
    elem = driver.find_element_by_css_selector("#__next > div > div.MuiBox-root.css-k008qs > div.MuiBox-root.css-95qt6b > div.MuiBox-root.css-xdk02r > div > div.MuiBox-root.css-8qb8m4 > div.css-1vqlvrt > button")
    elem.click()

    # Click Download (Default selected: Wallets)
    elem = driver.find_element_by_css_selector("body > div.MuiModal-root.MuiDialog-root.css-126xj0f > div.MuiDialog-container.MuiDialog-scrollPaper.css-ekeie0 > div > div.MuiDialogActions-root.MuiDialogActions-spacing.css-oe1mvf > button")
    elem.click()
    time.sleep(2)

    # Click Positions
    elem = driver.find_element_by_css_selector("body > div.MuiModal-root.MuiDialog-root.css-126xj0f > div.MuiDialog-container.MuiDialog-scrollPaper.css-ekeie0 > div > div.MuiDialogContent-root.css-1f1qmhw > div > div > button:nth-child(2)")
    elem.click()
    time.sleep(2)

    # Click Download
    elem = driver.find_element_by_css_selector("body > div.MuiModal-root.MuiDialog-root.css-126xj0f > div.MuiDialog-container.MuiDialog-scrollPaper.css-ekeie0 > div > div.MuiDialogActions-root.MuiDialogActions-spacing.css-oe1mvf > button")
    elem.click()
    time.sleep(2)

    # move downloaded files to ./data
    files = os.scandir(os_default_download_path)
    li = []
    for f in files:
        if re.search('Export.+.csv', f.name):
            li.append({'filename': f.name, 'timestamp': os.stat(f.path).st_mtime})
    file_timestamp_sorted = sorted(li, key=lambda x: x['timestamp'], reverse=True)

    filename_wallet = f"{file_timestamp_sorted[1]['filename']}"  # 2nd newest file
    filename_position = f"{file_timestamp_sorted[0]['filename']}"  # newest file

    # define destination filepath
    filepath_wallet = f'{data_store_path}/{filename_wallet}'
    filepath_position = f'{data_store_path}/{filename_position}'

    shutil.move(f'{os_default_download_path}/{filename_wallet}', f'{data_store_path}/{filename_wallet}')
    shutil.move(f'{os_default_download_path}/{filename_position}', f'{data_store_path}/{filename_position}')

    df_wallet_defi = pd.read_csv(filepath_wallet)
    df_position_defi = pd.read_csv(filepath_position)

    return driver, df_wallet_defi, df_position_defi


def get_defi_portfolio(url, headless, chromedriver_path, os_default_download_path, data_store_path):
    # create driver
    options = Options()
    # set headless to True, if you don't need to display browser
    if headless:
        options.add_argument('--headless')

    options.add_argument('--no-sandbox')  # for preventing crashes
    options.add_argument('--window-size=800,600')  # for preventing crashes

    # When using headless option, some websites detect this as a bot and return blank page.
    # Thus we specify user_agent to make headless undetectable
    # Ref: https://intoli.com/blog/making-chrome-headless-undetectable/
    user_agent = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/60.0.3112.50 Safari/537.36'
    options.add_argument(f'user-agent={user_agent}')
    driver = webdriver.Chrome(chromedriver_path, options=options)

    try:
        driver, df_wallet, df_position = fetch_apeboard(driver, url, os_default_download_path, data_store_path)
    except Exception:
        logger = getLogger('main')
        logger.exception('exception at get_defi_portfolio')

        filepath_screenshot = create_screenshot(driver, 'error')
        logger.error(f'saved screenshot to {filepath_screenshot}')

        driver.quit()
        raise
        # raise Exception(f'failed to fetch_apeboard. screenshot: {filepath_screenshot}')

    driver.quit()

    return df_wallet, df_position


def write_to_influxdb(timestamp, df_wallet_defi, df_position_defi, df_wallet_cefi, influxdb_config):
    measurement_name = 'portfolio'

    # cefi wallet
    for index, row in df_wallet_cefi.iterrows():
        tags = {
            'location': 'cefi',
            'exchange': row['exchange'], 
            'account': row['account'],
            'market': row['market'], 
            'symbol': row['symbol']
            }
        fields = {
            'amount': float(row['amount']),
            'USD': float(row['USD']),
            'JPY': float(row['JPY']),
            'price': float(row['price_usd'])
        }

        write_influxdb(
            write_api=influxdb_config['write_api'], 
            write_precision=influxdb_config['write_precision'], 
            bucket=influxdb_config['bucket'], 
            measurement_name=measurement_name, 
            fields=fields, 
            tags=tags, 
            timestamp=timestamp
            )

    # defi wallet
    for index, row in df_wallet_defi.iterrows():
        tags = {
            'location': 'defi',
            'type': 'wallet',
            'chain': str(row['chain']),
            'symbol': str(row['symbol'])
            }
        fields = {
            'amount': float(row['balance']),
            'USD': float(row['value']),
            'JPY': float(row['JPY']),
            'price': float(row['price']),
            'wallet_address': str(row['wallet_address']),
            'token_address': str(row['token_address'])
        }
        write_influxdb(
            write_api=influxdb_config['write_api'], 
            write_precision=influxdb_config['write_precision'], 
            bucket=influxdb_config['bucket'], 
            measurement_name=measurement_name, 
            fields=fields, 
            tags=tags, 
            timestamp=timestamp
            )

    # defi position
    for index, row in df_position_defi.iterrows():
        tags = {
            'location': 'defi',
            'type': 'position',
            'chain': str(row['chain']),
            'protocol': str(row['protocol']),
            'symbol': str(row['symbol'])
            }
        fields = {
            'amount': float(row['balance']),
            'USD': float(row['value']),
            'JPY': float(row['JPY']),
            'price': float(row['price']),
            'wallet_address': str(row['wallet_address']),
            'position': float(row['position']),
            'balance_type': str(row['balance_type']),
            'asset_type': str(row['asset_type'])
        }
        write_influxdb(
            write_api=influxdb_config['write_api'], 
            write_precision=influxdb_config['write_precision'], 
            bucket=influxdb_config['bucket'], 
            measurement_name=measurement_name, 
            fields=fields, 
            tags=tags, 
            timestamp=timestamp
            )


def main(exch_config, exch_secrets, url, headless, chromedriver_path, os_default_download_path, data_store_path, influxdb_config):
    timestamp = int(time.time() * 1000)

    logger = getLogger('main')
    logger.info(f"iteration at {datetime.datetime.fromtimestamp(timestamp//1000).strftime('%Y%m%d-%H%M%S')}")

    try:
        # get portfolio data as dataframe
        df_wallet_cefi = get_cefi_portfolio(exch_config, exch_secrets)
        logger.debug('finished get_cefi_portfolio')

        df_wallet_defi, df_position_defi = get_defi_portfolio(url, headless, chromedriver_path, os_default_download_path, data_store_path)
        logger.debug('finished get_defi_portfolio')

        # add JPY
        price_usdjpy = fetch_price_usdjpy()
        df_wallet_cefi['JPY'] = df_wallet_cefi['USD'] * price_usdjpy
        df_wallet_defi['JPY'] = df_wallet_defi['value'] * price_usdjpy
        df_position_defi['JPY'] = df_position_defi['value'] * price_usdjpy

        # write to database
        write_to_influxdb(timestamp, df_wallet_defi, df_position_defi, df_wallet_cefi, influxdb_config)
        logger.debug('wrote to database')
    except Exception:
        logger.exception(f"caught Exception on iteration at {datetime.datetime.fromtimestamp(timestamp//1000).strftime('%Y%m%d-%H%M%S')}")


if __name__ == '__main__':
    # read argument
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', '-d', help='To repeat execution periodically or not. If false, the program will run immediately only once.', action='store_true')
    args = parser.parse_args()
    debug = args.debug

    # read parameters from config
    with open('../config/config.json') as f:
        config = json.load(f)
    
    exch_config = config['exch_config']
    url = config['ApeBoard_DashboardUrl']
    headless = config['headless']
    os_default_download_path = config['os_default_download_path']  # default dl directory to search csv
    chromedriver_path = config['chromedriver_path']
    data_store_path = config['data_store_path']
    influxdb_secret_name = config['influxdb_secret_name']
    exchange_secret_name = config['exchange_secret_name']

    exch_secrets = get_secret(exchange_secret_name)

    # Execution parameter
    bot_group = 'portfolio'
    env = 'dev' if debug else 'prd'
    bot_name = pathlib.Path(__file__).stem
    exec_name = datetime.datetime.now(tz.gettz('UTC')).strftime('%Y%m%d-%H%M%S')  # YYYYMMDD-HHMMSS
    hostname = socket.gethostname()

    # Logger
    # for cloudwatch
    log_group_name = f'{bot_group}/{env}'
    log_stream_name = f'{bot_name}/{exec_name}'

    logger = create_logger(
        name='main',
        cloudwatchconfig={'log_group_name': log_group_name, 'log_stream_name': log_stream_name, 'region_name': 'us-east-2'},
        logrecord_constants={'env': env, 'exec_name': exec_name, 'hostname': hostname}
        )
    logger.info('created logger')

    # InfluxDB
    bucket = bot_group
    influxdb_secrets = get_secret(influxdb_secret_name)
    idb_client = influxdb_client.InfluxDBClient(
        url=influxdb_secrets['endpoint'],
        token=influxdb_secrets['token'],
        org=influxdb_secrets['org']
    )
    write_api = idb_client.write_api(write_options=influxdb_client.client.write_api.SYNCHRONOUS)
    influxdb_config = {
        'write_api': write_api,
        'write_precision': influxdb_client.domain.write_precision.WritePrecision.MS,
        'bucket': bucket
    }

    kwargs = {
        'exch_config': exch_config,
        'exch_secrets': exch_secrets,
        'url': url, 
        'headless': headless, 
        'chromedriver_path': chromedriver_path, 
        'os_default_download_path': os_default_download_path, 
        'data_store_path': data_store_path, 
        'influxdb_config': influxdb_config
    }

    schedule.every().hour.at(':00').do(main, **kwargs)
    schedule.every().hour.at(':15').do(main, **kwargs)
    schedule.every().hour.at(':30').do(main, **kwargs)
    schedule.every().hour.at(':45').do(main, **kwargs)

    if debug:
        main(**kwargs)
        logger.info('finished debug run')
    else:
        while True:
            schedule.run_pending()
            time.sleep(10)  # sleep is required or cpu usage will max out
