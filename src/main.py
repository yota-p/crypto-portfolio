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
import pybit
import influxdb_client
from util.secretsmanager import get_secret
from util.mylog import create_logger
from util.ccxt_extension import gmocoin

# ccxt doesn't support gmocoin atm. use extension script from 3rd party
# https://note.com/nickel_plating/n/nc6fb71417e7e
ccxt.gmocoin = gmocoin


##################################################################
# Helper
##################################################################

def write_influxdb(write_api, write_precision, bucket, measurement_name, fields: list, tags={}, timestamp:int=None):
    point = influxdb_client.Point(measurement_name=measurement_name)
    if timestamp:
        point.time(timestamp, write_precision)
    for k, v in tags.items():
        point.tag(k, v)
    for k, v in fields.items(): 
        point.field(k, v)
    write_api.write(bucket=bucket, record=point)


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


def fetch_price_jpyusdt():
    # calculate jpy/usdt
    price_btcjpy = ccxt.bitflyer().fetch_ohlcv(f'BTC/JPY', '1m')[-1][4]
    price_btcusdt = ccxt.binance().fetch_ohlcv(f'BTC/USDT', '1m')[-1][4]
    price_jpyusdt = price_btcusdt / price_btcjpy

    return price_jpyusdt


##################################################################
# CeFi
##################################################################
def fetch_market_prices(symbols: set) -> pd.DataFrame:
    binance = ccxt.binance()  # use exchange covering most tokens

    # fetch market price for symbols
    markets = binance.load_markets()

    data = []
    for symbol in symbols:
        pair = f'{symbol}/USDT'
        if pair not in markets.keys():
            if symbol in ('USDT', 'LDUSDT', 'LDBUSD'):  # LDUSDT: Lending USDT for binance
                price_usd = 1.0  # Assuming stable coins are maintaining peg
                data.append({'symbol': symbol, 'price_usd': price_usd})
            elif symbol == 'JPY':
                data.append({'symbol': symbol, 'price_usd': fetch_price_jpyusdt()})
            else:
                raise ValueError(f'pair={pair} does not exist in exchange=binance')
        else:
            price_usd = binance.fetch_ohlcv(f'{symbol}/USDT', '1m')[-1][4]
            data.append({'symbol': symbol, 'price_usd': price_usd})

    df_prices = pd.DataFrame.from_dict(data).astype({'symbol': str, 'price_usd': float})
    return df_prices


def fetch_balances(exch_list: list, secrets) -> pd.DataFrame:
    balances = {}

    for exch in exch_list:
        if exch == 'bybit':
            # bybit's spot account can't be fetched from ccxt
            # https://github.com/ccxt/ccxt/blob/master/python/ccxt/bybit.py#L1490
            session = pybit.HTTP("https://api.bybit.com",
                        api_key=secrets[exch]['API_KEY'], api_secret=secrets[exch]['SECRET_KEY'],
                        spot=True)
            balance_bybit = session.get_wallet_balance()

            for d in balance_bybit['result']['balances']:
                balances[exch] = {d['coin']: d['total']}

        else:
            exchange = getattr(ccxt, exch)({
                'enableRateLimit': True,
                'apiKey': secrets[exch]['API_KEY'],
                'secret': secrets[exch]['SECRET_KEY'],
                })
            response = exchange.fetch_balance()
            balances[exch] = {}
            for k, v in response['total'].items():
                if v != 0.0:
                    balances[exch][k] = v

        # convert into dataframe
        data = []
        for exch, asset in balances.items():
            for symbol, amount in asset.items():
                data.append({'exchange': exch, 'symbol': symbol, 'amount': amount})

        df_wallet = pd.DataFrame.from_dict(data) \
            .astype({'exchange': str, 'symbol': str, 'amount': float})

    return df_wallet


def get_cefi_portfolio(exch_list, exch_secrets):
    df_wallet = fetch_balances(exch_list, exch_secrets)

    symbols = set(df_wallet['symbol'])
    df_prices = fetch_market_prices(symbols)

    # add price info to df
    df_wallet = pd.merge(df_wallet, df_prices, how='left', on='symbol')
    df_wallet['USD'] = df_wallet['amount'] * df_wallet['price_usd']

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
    # cefi wallet
    for index, row in df_wallet_cefi.iterrows():
        tags = {
            'location': 'cefi',
            'exchange': row['exchange'], 
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
            bucket='portfolio', 
            measurement_name='portfolio', 
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
            bucket='portfolio', 
            measurement_name='portfolio', 
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
            bucket='portfolio', 
            measurement_name='portfolio', 
            fields=fields, 
            tags=tags, 
            timestamp=timestamp
            )


def main(exch_list, exch_secrets, url, headless, chromedriver_path, os_default_download_path, data_store_path, influxdb_config):
    timestamp = int(time.time() * 1000)

    logger = getLogger('main')
    logger.info(f"iteration at {datetime.datetime.fromtimestamp(timestamp//1000).strftime('%Y%m%d-%H%M%S')}")

    try:
        # get portfolio data as dataframe
        df_wallet_cefi = get_cefi_portfolio(exch_list, exch_secrets)
        logger.debug('finished get_cefi_portfolio')

        df_wallet_defi, df_position_defi = get_defi_portfolio(url, headless, chromedriver_path, os_default_download_path, data_store_path)
        logger.debug('finished get_defi_portfolio')

        # add JPY
        price_jpyusdt = fetch_price_jpyusdt()
        df_wallet_cefi['JPY'] = df_wallet_cefi['USD'] / price_jpyusdt
        df_wallet_defi['JPY'] = df_wallet_defi['value'] / price_jpyusdt
        df_position_defi['JPY'] = df_position_defi['value'] / price_jpyusdt

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
    
    exch_list = config['exch_list']
    url = config['ApeBoard_DashboardUrl']
    headless = config['headless']
    os_default_download_path = config['os_default_download_path']  # default dl directory to search csv
    chromedriver_path = config['chromedriver_path']
    data_store_path = config['data_store_path']
    influxdb_secret_name = config['influxdb_secret_name']
    exchange_secret_name = config['exchange_secret_name']

    exch_secrets = get_secret(exchange_secret_name)

    # Execution parameter
    exec_name = datetime.datetime.now(tz.gettz('UTC')).strftime('%Y%m%d-%H%M%S')  # YYYYMMDD-HHMMSS
    env = 'dev' if debug else 'prd'

    # Logger
    # for cloudwatch
    hostname = socket.gethostname()
    file_name = pathlib.Path(__file__).stem
    log_group_name = f'crypto-portfolio/{env}'
    log_stream_name = f'{hostname}/{file_name}/{exec_name}'

    logger = create_logger(
        name='main',
        cloudwatchconfig={'log_group_name': log_group_name, 'log_stream_name': log_stream_name, 'region_name': 'us-east-2'},
        logrecord_constants={'hostname': hostname, 'env': env, 'exec_name': exec_name}
        )
    logger.info('created logger')

    # InfluxDB
    influxdb_secrets = get_secret(influxdb_secret_name)
    idb_client = influxdb_client.InfluxDBClient(
        url=influxdb_secrets['endpoint'],
        token=influxdb_secrets['token'],
        org=influxdb_secrets['org']
    )
    write_api = idb_client.write_api(write_options=influxdb_client.client.write_api.SYNCHRONOUS)
    influxdb_config = {
        'write_api': write_api,
        'write_precision': influxdb_client.domain.write_precision.WritePrecision.MS
    }

    kwargs = {
        'exch_list': exch_list,
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
