from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from util.secretsmanager import get_secret


def slack_postmessage(token, channel, text, **kwargs):
    '''
    https://api.slack.com/methods/chat.postMessage
    Example of kwargs:
    - attachments/blocks/text
    '''

    client = WebClient(token=token)
    try:
        result = client.chat_postMessage(
            channel=channel,
            text=text,
            **kwargs
        )
    except SlackApiError as e:
        print(f"Error posting message: {e}")


def slack_filesupload(token:str, **kwargs):
    '''
    https://api.slack.com/methods/files.upload
    Example of kwargs:
    - channels: list[str] -> list of channel name
    - file: str -> path of file to upload
    - initial_comment: str -> message
    '''
    client = WebClient(token=token)
    try:
            # Call the files.upload method using the WebClient
            # Uploading files requires the `files:write` scope
            result = client.files_upload(**kwargs)
    except SlackApiError as e:
        print(f"Error uploading message: {e}")


if __name__ == '__main__':
    secrets = get_secret('slack/crypto-bot')
    token = secrets['token']
    channel = secrets['channel_id']

    slack_postmessage(token, channel=channel, text=":Bell: Hello World ```import boto3\nprint('ok')```")
    slack_filesupload(token, channels=[channel], file='./util/notification.py')
