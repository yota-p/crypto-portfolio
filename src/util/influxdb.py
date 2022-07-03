import influxdb_client


def write_influxdb(write_api, write_precision, bucket, measurement_name, fields:dict={}, tags:dict={}, timestamp:int=None):
    point = influxdb_client.Point(measurement_name=measurement_name)
    if timestamp:
        point.time(timestamp, write_precision)
    for k, v in tags.items():
        point.tag(k, v)
    for k, v in fields.items(): 
        point.field(k, v)
    write_api.write(bucket=bucket, record=point)
