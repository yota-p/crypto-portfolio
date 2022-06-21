def lambda_handler(event, context):
    
    from os import environ
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    from random import choice

    client = WebClient(token=environ['SLACK_TOKEN'])
    lunch_list = environ['LUNCH'].split(',')

    try:
        response = client.chat_postMessage(
            channel=environ['SLACK_CHANNEL'],
            text=f"今日のお昼ご飯は{choice(lunch_list)}です!"
        )
        print(response)
    except SlackApiError as e:
        # You will get a SlackApiError if "ok" is False
        assert e.response["error"]    # str like 'invalid_auth', 'channel_not_found'
    return 0