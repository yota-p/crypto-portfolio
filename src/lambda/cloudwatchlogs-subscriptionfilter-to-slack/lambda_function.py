import base64
import json
import zlib
import datetime
from dateutil import tz
import os
import boto3
from botocore.exceptions import ClientError
from util.secretsmanager import get_secret
from util.notification import slack_postmessage


def lambda_handler(event, context):
    data = zlib.decompress(base64.b64decode(event['awslogs']['data']), 16+zlib.MAX_WBITS)
    data_dict = json.loads(data)

    print('event=', json.dumps(event))
    print('data=', json.dumps(data_dict))

    log_group = data_dict['logGroup']
    log_stream = data_dict['logStream']
    subscription_filters = '/'.join(data_dict['subscriptionFilters'])
    region = os.environ["AWS_REGION"]
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    link = f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#logsV2:log-groups/log-group/{log_group.replace('/', '$252F')}/log-events/{log_stream.replace('/', '$252F')}"

    slackauth = get_secret('slack/crypto-bot')

    for log_event in data_dict['logEvents']:
        log_dict = json.loads(json.dumps(log_event, ensure_ascii=False))
        log_dict['message'] = json.loads(log_dict['message'])
        print(log_dict)

        # compose message
        header = f'<{link}|:rotating_light: *CloudWatch Alarm | {subscription_filters} | {region} | {account_id}*>'
        body = log_dict['message']['message']
        if 'exc_info' in log_dict['message'].keys():  # add traceback
            body += f"\n```{log_dict['message']['exc_info']}```"
        footer = f"*Log group*\t{log_group}" \
            + f"\n*Log stream*\t{log_stream}" \
            + f"\n*Log level*\t{log_dict['message'].get('levelname')}" \
            + f"\n*Module*\t{log_dict['message'].get('module')}" \
            + f"\n*Host*\t{log_dict['message'].get('hostname')}" \
            + f"\n*Execution Name*\t{log_dict['message'].get('exec_name')}" \
            + f"\n*Log time*\t{datetime.datetime.fromtimestamp(log_dict['timestamp']//1000, tz.gettz('Asia/Tokyo'))}"

        try:
            slack_postmessage(
                token=slackauth['token'], 
                channel=slackauth['channel_id'], 
                text=f'{header}\n{body}\n{footer}'
                )
    
        except Exception as e:
            print(e)

    '''
    Sample data structure:
    data=
    {
        "messageType": "DATA_MESSAGE",
        "owner": "99999999999",
        "logGroup": "your-log-group",
        "logStream": "your-log-stream",
        "subscriptionFilters": [
            "python-loglevel-error"
        ],
        "logEvents": [
            {
                "id": "999999999999999999999999999999999999999",
                "timestamp": 1655700000000,
                "message": "{\"test1\": 1, \"test2\": \"aaa\", \"asctime\": \"2022-06-20T22:46:59+0000\", \"created\": 1655765219.0699968, \"filename\": \"mylog.py\", \"funcName\": \"<module>\", \"levelname\": \"ERROR\", \"levelno\": 40, \"lineno\": 250, \"message\": \"Caught Exception\", \"module\": \"mylog\", \"msecs\": 69.99683380126953, \"name\": \"mylog\", \"pathname\": \"/path/mylog.py\", \"process\": 11342, \"processName\": \"MainProcess\", \"relativeCreated\": 877.6206970214844, \"thread\": 139977257248384, \"threadName\": \"MainThread\", \"exc_info\": \"Traceback (most recent call last):\\n  File \\\"/path/mylog.py\\\", line 248, in <module>\\n    raise Exception('test exception')\\nException: test exception\"}"
            }
        ]
    }
    
    data['message']=
    {
        "id": "999999999999999999999999999999999999999",
        "timestamp": 1655700000000,
        "message": {
            "test1": 1,
            "test2": "aaa",
            "asctime": "2022-06-20T22:23:30+0000",
            "created": 1655700000.0000000,
            "filename": "mylog.py",
            "funcName": "<module>",
            "levelname": "ERROR",
            "levelno": 40,
            "lineno": 242,
            "message": "Hello ERROR",
            "module": "mylog",
            "msecs": 903.7761688232422,
            "name": "mylog",
            "pathname": "/path/mylog.py",
            "process": 10950,
            "processName": "MainProcess",
            "relativeCreated": 1013.8797760009766,
            "thread": 140415283336832,
            "threadName": "MainThread"
        }
    }
    '''
