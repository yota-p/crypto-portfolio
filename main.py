'''
Run this script with:
$ nohup python main.py >> /tmp/portfolio.log 2>&1 &
$ ps aux | grep main.py
'''
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import traceback
import datetime
import time
import os
import pandas as pd
import json
import shutil
import schedule
import re
import ccxt
import pybit
import influxdb_client
from util.secretsmanager import get_secret
from util.ccxt_extension import gmocoin

# ccxt doesn't support gmocoin atm. use extension script from 3rd party
# https://note.com/nickel_plating/n/nc6fb71417e7e
ccxt.gmocoin = gmocoin


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

    driver.save_screenshot(filepath)

    # wait for screenshot file to be created
    start = time.time()
    while time.time() - start <= 30:
        if os.path.exists(filepath):
            break
        else:
            raise Exception('Failed to create screenshot')

    return filepath


##################################################################
# CeFi
##################################################################

def fetch_price_jpyusdt():
    # calculate jpy/usdt
    price_btcjpy = ccxt.bitflyer().fetch_ohlcv(f'BTC/JPY', '1m')[-1][4]
    price_btcusdt = ccxt.binance().fetch_ohlcv(f'BTC/USDT', '1m')[-1][4]
    price_jpyusdt = price_btcusdt / price_btcjpy

    return price_jpyusdt


def fetch_market_prices(symbols: set, price_jpyusdt) -> pd.DataFrame:
    binance = ccxt.binance()  # use exchange covering most tokens

    # fetch market price for symbols
    markets = binance.load_markets()

    data = []
    for symbol in symbols:
        pair = f'{symbol}/USDT'
        if pair not in markets.keys():
            if symbol in ('USDT', 'LDUSDT', 'LDBUSD'):  # LDUSDT: Lending USDT for binance
                price_usd = 1.0
                data.append({'symbol': symbol, 'price_usd': price_usd})
            elif symbol == 'JPY':
                price_usd = price_jpyusdt
                data.append({'symbol': symbol, 'price_usd': price_usd})
            else:
                raise ValueError(f'pair={pair} does not exist in exchange=binance')
        else:
            price_usd = binance.fetch_ohlcv(f'{symbol}/USDT', '1m')[-1][4]
            data.append({'symbol': symbol, 'price_usd': price_usd})

    df_prices = pd.DataFrame.from_dict(data).astype({'symbol': str, 'price_usd': float}).sort_values('symbol')   
    return df_prices


def fetch_balances(exch_list: list) -> pd.DataFrame:
    balances = {}

    for exch in exch_list:
        secrets = get_secret(f'exchange/bot')

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
            .astype({'exchange': str, 'symbol': str, 'amount': float})\
            .sort_values(['exchange', 'symbol'])

    return df_wallet


def cefi(timestamp, price_jpyusdt, exch_list, influxdb_config):
    df_wallet = fetch_balances(exch_list)

    symbols = set(df_wallet['symbol'])
    df_prices = fetch_market_prices(symbols, price_jpyusdt)

    # add price info to df
    df_wallet = pd.merge(df_wallet, df_prices, how='left', on='symbol')
    df_wallet['USD'] = df_wallet['amount'] * df_wallet['price_usd']
    df_wallet['JPY'] = df_wallet['USD'] / price_jpyusdt
    df_wallet.sort_values(['exchange', 'symbol'], inplace=True)

    print('price_jpyusdt=', price_jpyusdt)
    print('df_wallet=', df_wallet)

    for index, row in df_wallet.iterrows():
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
    print('wrote to InfluxDB')


##################################################################
# DeFi
##################################################################

def download_apeboard(driver, url, os_default_download_path, data_store_path):
    driver.get(url)
    WebDriverWait(driver, 60).until(EC.presence_of_all_elements_located)

    time.sleep(5)  # wait for the page to be loaded

    # # Close "Create a Profile?" dialog
    # elem = driver.find_element_by_css_selector("body > div.MuiModal-root.MuiDialog-root.e19a6ngc4.e1vojmsk6.css-cache-yky58e > div.MuiDialog-container.MuiDialog-scrollPaper.css-cache-ekeie0 > div > button")
    # elem.click()

    time.sleep(60)  # wait for the tokens to be loaded

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

    return driver, filepath_wallet, filepath_position


def defi(timestamp, price_jpyusdt, url, headless, chromedriver_path, os_default_download_path, data_store_path, influxdb_config):
    # create driver
    options = Options()
    # set headless to True, if you don't need to display browser
    if headless:
        options.add_argument('--headless')
    # When using headless option, some websites detect this as a bot and return blank page.
    # Thus we specify user_agent to make headless undetectable
    # Ref: https://intoli.com/blog/making-chrome-headless-undetectable/
    user_agent = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/60.0.3112.50 Safari/537.36'
    options.add_argument(f'user-agent={user_agent}')
    driver = webdriver.Chrome(chromedriver_path, options=options)

    # open browser & download
    try:
        driver, filepath_wallet, filepath_position = download_apeboard(driver, url, os_default_download_path, data_store_path)
    except Exception:
        filepath_screenshot = create_screenshot(driver, 'error')
        print('Failed to sync. \n' + traceback.format_exc() + f'\nScreenshot: {filepath_screenshot}')
        raise Exception()
    finally:
        driver.quit()

    df_wallet = pd.read_csv(filepath_wallet)
    df_position = pd.read_csv(filepath_position)

    print(filepath_wallet)
    print(df_wallet)
    print(filepath_position)
    print(df_position)

    df_wallet['JPY'] = df_wallet['value'] / price_jpyusdt
    df_position['JPY'] = df_position['value'] / price_jpyusdt

    # write wallet data
    for index, row in df_wallet.iterrows():
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

    # write portfolio data
    for index, row in df_position.iterrows():
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
    print('wrote to InfluxDB')


if __name__ == '__main__':
    # read parameters from config
    with open('./config/config.json') as f:
        config = json.load(f)
    
    test = config['test']
    headless = config['headless']
    url = config['ApeBoard_DashboardUrl']
    os_default_download_path = config['os_default_download_path']  # default dl directory to search csv
    chromedriver_path = config['chromedriver_path']
    exch_list = config['exch_list']
    data_store_path = config['data_store_path']
    influxdb_endpoint = config['influxdb_endpoint']
    influxdb_secret_name = config['influxdb_secret_name']

    # InfluxDB
    influxdb_secrets = get_secret(influxdb_secret_name)
    idb_client = influxdb_client.InfluxDBClient(
        url=influxdb_endpoint,
        token=influxdb_secrets['token'],
        org=influxdb_secrets['org']
    )
    write_api = idb_client.write_api(write_options=influxdb_client.client.write_api.SYNCHRONOUS)
    influxdb_config = {
        'write_api': write_api,
        'write_precision': influxdb_client.domain.write_precision.WritePrecision.MS
    }

    def task():
        timestamp = int(time.time() * 1000)
        price_jpyusdt = fetch_price_jpyusdt()

        for i in range(5):  # times to retry
            print(f'defi attempt {i} at {timestamp}')
            try:
                defi(timestamp, price_jpyusdt, url, headless, chromedriver_path, os_default_download_path, data_store_path, influxdb_config)
            except Exception:
                print(f'Exception at attempt {i} for defi:\n' + traceback.format_exc())
            break  # exit retry loop if task succeeded

        for i in range(5):  # times to retry
            print(f'cefi attempt {i} at {timestamp}')
            try:
                cefi(timestamp, price_jpyusdt, exch_list, influxdb_config)
            except Exception:
                print(f'Exception at attempt {i} for cefi:\n' + traceback.format_exc())
            break  # exit retry loop if task succeeded

    schedule.every().hour.at(':00').at(':15').at(':30').at(':45').do(task)

    if test:
        task()
    else:
        while True:
            schedule.run_pending()
            time.sleep(10)  # sleep is required or cpu usage will max out
