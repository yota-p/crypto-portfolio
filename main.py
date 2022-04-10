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


def download_apeboard(driver, url):
    driver.get(url)
    WebDriverWait(driver, 60).until(EC.presence_of_all_elements_located)

    time.sleep(5)  # wait for the page to be loaded

    # Close "Create a Profile?" dialog
    elem = driver.find_element_by_css_selector("body > div.MuiModal-root.MuiDialog-root.e19a6ngc4.e1vojmsk6.css-cache-yky58e > div.MuiDialog-container.MuiDialog-scrollPaper.css-cache-ekeie0 > div > button")
    elem.click()

    time.sleep(15)  # wait for the tokens to be loaded

    # Click Export
    elem = driver.find_element_by_css_selector("#__next > div.e1tgfzqa2.MuiBox-root.css-cache-1xwgazy > div.css-cache-1kek6j4.e1tgfzqa1 > div.MuiBox-root.css-cache-0 > div.css-cache-5sgei1.e1n8hxva4 > div.css-cache-qwdzsu.e1ev28fj11 > div.e1ev28fj10.eo8e0y70.MuiBox-root.css-cache-1pzc6d0 > button")
    elem.click()

    # Click Download (Default selected: Wallet)
    elem = driver.find_element_by_css_selector("body > div.MuiModal-root.MuiDialog-root.e1vojmsk6.css-cache-bm1ugi > div.MuiDialog-container.MuiDialog-scrollPaper.css-cache-ekeie0 > div > div.MuiDialogActions-root.MuiDialogActions-spacing.e1vojmsk5.css-cache-oglxqx > button")
    elem.click()

    # Click dropdown
    elem = driver.find_element_by_css_selector("body > div.MuiModal-root.MuiDialog-root.e1vojmsk6.css-cache-bm1ugi > div.MuiDialog-container.MuiDialog-scrollPaper.css-cache-ekeie0 > div > div.MuiDialogContent-root.e1vojmsk3.css-cache-7m0knx > div.MuiBox-root.css-cache-tdpf08 > div > div > div")
    elem.click()

    # Select "Positions" from dropdown
    elem = driver.find_element_by_xpath('//*[@id="menu-"]/div[3]/ul/li[2]')
    elem.click()

    # Click Download (Selected: Positions)
    elem = driver.find_element_by_css_selector("body > div.MuiModal-root.MuiDialog-root.e1vojmsk6.css-cache-bm1ugi > div.MuiDialog-container.MuiDialog-scrollPaper.css-cache-ekeie0 > div > div.MuiDialogActions-root.MuiDialogActions-spacing.e1vojmsk5.css-cache-oglxqx > button")
    elem.click()

    time.sleep(5)  # wait for download to end

    return driver


if __name__ == '__main__':
    with open('./config/config.json') as f:
        config = json.load(f)
    
    headless = config['headless']
    url = config['ApeBoard_DashboardUrl']
    os_default_download_path = config['os_default_download_path']  # default dl directory to search csv
    chromedriver_path = config['chromedriver_path']

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
    #chrome_service = fs.Service(executable_path=chromedriver_path)
    driver = webdriver.Chrome(chromedriver_path, options=options)

    # open browser & download
    try:
        driver = download_apeboard(driver, url)
    except Exception:
        filepath = create_screenshot(driver, 'error')
        print('Failed to sync. \n' + traceback.format_exc() + f'\nScreenshot: {filepath}')
    finally:
        driver.quit()

    files = os.scandir(os_default_download_path)
    li = []
    for f in files:
        li.append({'filename': f.name, 'timestamp': os.stat(f.path).st_mtime})
    file_timestamp_sorted = sorted(li, key=lambda x: x['timestamp'], reverse=True)

    filepath_wallet = f"{os_default_download_path}/{file_timestamp_sorted[1]['filename']}"
    filepath_position = f"{os_default_download_path}/{file_timestamp_sorted[0]['filename']}"

    df_wallet = pd.read_csv(filepath_wallet)
    df_position = pd.read_csv(filepath_position)

    print(df_wallet)
    print(df_position)

    # move downloaded files to ./data
    shutil.move(filepath_wallet, './data')
    shutil.move(filepath_position, './data')
