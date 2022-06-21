from functools import wraps
import time
import datetime
from dateutil import tz
from contextlib import contextmanager
from logging import getLogger, Formatter, FileHandler, StreamHandler, DEBUG, ERROR, getLogRecordFactory, setLogRecordFactory
from util.secretsmanager import get_secret
from util.notification import slack_postmessage, slack_filesupload
import boto3
import watchtower
import json_log_formatter
from logging import LogRecord


class CustomisedJSONFormatter(json_log_formatter.JSONFormatter):
    def json_record(self, message: str, extra: dict, record: LogRecord) -> dict:
        '''log output to JSON format
        https://github.com/marselester/json-log-formatter
        https://tech.zeals.co.jp/entry/2020/07/06/113615
        '''
        # set all LogRecord attributes available
        # https://docs.python.org/ja/3/library/logging.html#logrecord-attributes
        extra['asctime'] = record.asctime
        extra['created'] = record.created
        extra['filename'] = record.filename
        extra['funcName'] = record.funcName
        extra['levelname'] = record.levelname
        extra['levelno'] = record.levelno
        extra['lineno'] = record.lineno
        extra['message'] = message
        extra['module'] = record.module
        extra['msecs'] = record.msecs
        extra['name'] = record.name
        extra['pathname'] = record.pathname
        extra['process'] = record.process
        extra['processName'] = record.processName
        extra['relativeCreated'] = record.relativeCreated
        extra['thread'] = record.thread
        extra['threadName'] = record.threadName

        if record.exc_info:
            extra['exc_info'] = self.formatException(record.exc_info)

        return extra


class SlackHandler(StreamHandler):
    def __init__(self, token, channel_id):
        super(SlackHandler, self).__init__()
        self.token = token
        self.channel_id = channel_id

    def emit(self, record):
        '''
        record: LogRecord
        LogRecord object contains attributes passed as logger.log(..., extra={'key': value})
        https://dev.classmethod.jp/articles/python-logger-kwarg-extra/
        '''

        # Check if attribute attach_file exists in LogRecord. 
        try: 
            attach_file = record.attach_file

        # If no attach_file was given specified, it will return AttributeError: 'LogRecord' object has no attribute 'attach_file'
        except AttributeError:
            attach_file = None

        msg = self.format(record)

        if attach_file:
            slack_filesupload(token=self.token, channels=[self.channel_id], file=attach_file, initial_comment=msg)
        else:
            slack_postmessage(token=self.token, channel=self.channel_id, text=msg)

    def setFormatter(self, formatter):
        self.formatter = formatter

    def setLevel(self, level):
        self.level = level


def create_logger(
    name,
    filepath=None,
    slackauth=None,  # {'token': 'XXX', 'channel_id': 'XXX'}
    cloudwatchconfig=None,  # {'log_group_name': 'XXX', 'log_stream_name': 'XXX'}
    stream_handler_level=DEBUG,
    file_handler_level=DEBUG,
    slack_handler_level=ERROR,
    cloudwatch_handler_level=DEBUG,
    logrecord_constants={}  # constant value to add to logrecords
    ):
    '''
    This is a method to initialize logger for specified name.
    To initialize logger:
        create_logger('fizz', ...)
    To get logger:
        from logging import getLogger
        logger = getLogger('fizz')
    To log:
        logger.log(ERROR, 'Something occured')  # message only
        logger.log(ERROR, 'Something occured', extra={'attach_file': '/tmp/file.png'})  # sends file to Slack
    '''
    formatter = Formatter(f'%(asctime)s %(levelname)s %(message)s', datefmt='%Y-%m-%dT%H:%M:%S%z')
    jsonformatter = CustomisedJSONFormatter()

    logger = getLogger(name)
    logger.setLevel(DEBUG)

    # override LogRecordFactory to add same metrics to all LogRecords
    old_factory = getLogRecordFactory()
    def new_record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)

        for k, v in logrecord_constants.items():
            setattr(record, k, v)
            # equivalent to 
            # record.hostname = 'host1'
            # record.execname = '20220101-123456'

        return record

    setLogRecordFactory(new_record_factory)

    # for stream
    stream_handler = StreamHandler()
    stream_handler.setLevel(stream_handler_level)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # for file
    if filepath is not None:
        file_handler = FileHandler(filepath, '+w')
        file_handler.setLevel(file_handler_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # for slack
    if slackauth is not None:
        token = slackauth['token']
        channel_id = slackauth['channel_id']
        slack_handler = SlackHandler(token=token, channel_id=channel_id)
        slack_handler.setLevel(slack_handler_level)
        slack_handler.setFormatter(formatter)
        logger.addHandler(slack_handler)
    
    # for cloudwatch (log in json format)
    if cloudwatchconfig is not None:
        log_group_name = cloudwatchconfig['log_group_name']
        log_stream_name = cloudwatchconfig['log_stream_name']
        region_name = cloudwatchconfig['region_name']

        session = boto3.session.Session()
        client = session.client(service_name='logs', region_name=region_name)
        cloudwatch_handler = watchtower.CloudWatchLogHandler(
            log_group_name=log_group_name, 
            log_stream_name=log_stream_name,
            boto3_client=client
        )
        cloudwatch_handler.setLevel(cloudwatch_handler_level)
        cloudwatch_handler.setFormatter(jsonformatter)
        logger.addHandler(cloudwatch_handler)

    return logger

def dynamic_args(func0):
    '''
    Decorator to allow defining decorators with & without arguments
    https://qiita.com/koyopro/items/8ce097b07605ee487ab2
    '''
    def wrapper(*args, **kwargs):
        if len(args) != 0 and callable(args[0]):
            # if func passed as first arg: treat as decorator without args
            func = args[0]
            return wraps(func)(func0(func))
        else:
            # treat as decorator with args
            def _wrapper(func):
                return wraps(func)(func0(func, *args, **kwargs))
            return _wrapper
    return wrapper


@dynamic_args
def timer(f, level=DEBUG, name='main'):
    def _wrapper(*args, **kwargs):
        logger = getLogger(name)
        start = time.time()
        func_name = f.__qualname__
        # logger.log(level, f'Start {func_name}')

        result = f(*args, **kwargs)

        elapsed_time = int(time.time() - start)
        minutes, sec = divmod(elapsed_time, 60)
        hour, minutes = divmod(minutes, 60)

        logger.log(level, f'Function {func_name} Elapsed: {hour:0>2}:{minutes:0>2}:{sec:0>2}')
        return result
    return _wrapper


@contextmanager
def blocktimer(block_name, level=DEBUG, name='main'):
    logger = getLogger(name)
    start = time.time()
    # logger.log(level, f'Start {block_name}')

    yield

    elapsed_time = int(time.time() - start)
    minutes, sec = divmod(elapsed_time, 60)
    hour, minutes = divmod(minutes, 60)
    logger.log(level, f'Block {block_name} Elapsed: {hour:0>2}:{minutes:0>2}:{sec:0>2}')


if __name__ == '__main__':
    from pathlib import Path
    import socket

    # slack
    slackauth = get_secret('slack/crypto-bot')

    # cloudwatch
    hostname = socket.gethostname()
    file_name = Path(__file__).stem
    exec_name = '2020531-235959'
    log_group_name = 'crypto-bot/dev'
    log_stream_name = f'{hostname}/{file_name}/{exec_name}'

    logger = create_logger(
        'mylog',
        filepath='/tmp/mylog.log', 
        slackauth=slackauth, 
        cloudwatchconfig={'log_group_name': log_group_name, 'log_stream_name': log_stream_name, 'region_name': 'us-east-2'},
        logrecord_constants={'test1': 1, 'test2': 'aaa'}
        )

    # logger.debug(msg='Hello DEBUG')
    # logger.info(msg='Hello INFO')
    # logger.warning(msg='Hello WARNING')
    # logger.error(msg='Hello ERROR')
    # logger.critical(msg='Hello CRITICAL')
    # logger.info('Sign up', extra={'referral_code': '52d6ce'})
    # logger.info(msg='Hello World2', extra={'attach_file': '/tmp/mylog.log'})

    try:
        raise Exception('test exception')
    except Exception:
        logger.exception(f'Caught Exception', exc_info=True)
