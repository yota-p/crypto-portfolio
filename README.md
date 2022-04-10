# crypto-portfolio

This is a software to collect & visualize balance from CEX/DEX wallets.

## Requirements

- Create a InfluxDB server
- Create a Grafana server connected to InfluxDB
- Create a ApeBoard portfolio URL
- Python3 (This software is tested on Python 3.7, 3.8)

## Setup

### Packages

#### Python

Run `pip install -r requirements.txt`.

#### Chromium & Chromedriver

Add repository configuration.

```bash
sudo vi /etc/yum.repos.d/google-chrome.repo
```

Paste below & save.

```repo
[google-chrome]
name=google-chrome
baseurl=http://dl.google.com/linux/chrome/rpm/stable/$basearch
enabled=0
gpgcheck=1
gpgkey=https://dl-ssl.google.com/linux/linux_signing_key.pub
```

Install packages.

```bash
sudo yum update -y

sudo yum install python3 git -y
sudo yum install --enablerepo=google-chrome google-chrome-stable -y
google-chrome --version  # Google Chrome 94.0.4606.81

# Get Chromedriver for your Chrome version.
cd /home/ec2-user
wget https://chromedriver.storage.googleapis.com/94.0.4606.61/chromedriver_linux64.zip
unzip chromedriver_linux64.zip

# Install double byte character fonts for Chrome
sudo yum install ipa-gothic-fonts ipa-mincho-fonts ipa-pgothic-fonts ipa-pmincho-fonts -y

# clone repository & install requirements
git clone https://github.com/yota-p/moneyforward-binance-sync.git
cd moneyforward-binance-sync
pip3 install --user -r requirements.txt
cp ~/chromedriver ~/moneyforward-binance-sync
```

#### Configs

1. Copy json templates from `config/template/config.json` to `config/config.json`
2. Edit parameters for `config/config.json`

## Run

```bash
python main.py
```

To run this every 5 minutes, hit `crontab -e` and create a job.

```cron
*/5 * * * * cd /home/ec2-user/moneyforward-binance-sync; python main.py
```

## Tips

If Chromedriver exited before calling `driver.close()` or `driver.quit()`, it's process will remain and use up memory and cpu. If you with to cleanup, run

```bash
ps aux | grep chrome | grep -v grep | awk '{ print "kill -9", $2 }' | sh
```

Note that `driver.close()` only closes current window. Use `driver.quit()` to close Chromedriver handling multiple window.
