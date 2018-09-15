#!/usr/bin/env python
# -*- coding: utf-8 -*-

import boto3
import logging
import os
import pytz
from base64 import b64decode
from collections import defaultdict
from datetime import datetime, timedelta
from time import sleep
from datadog import initialize, api

logger = logging.getLogger()
log_level = logging.getLevelName(os.environ['logLevel'])
if not isinstance(log_level, int):
  log_level = logging.INFO
logger.setLevel(log_level)

sts = boto3.client('sts')
ec2 = boto3.client('ec2')
tz = pytz.timezone('UTC')
aws_account_name = os.environ['awsAccountName']
role_name = os.environ['userName']
metric_name = os.environ['metricName']

api_key = boto3.client('kms').decrypt(CiphertextBlob=b64decode(os.environ['kmsEncryptedDdApiKey']))['Plaintext'].decode('utf-8')
app_key = boto3.client('kms').decrypt(CiphertextBlob=b64decode(os.environ['kmsEncryptedDdAppKey']))['Plaintext'].decode('utf-8')
options = {
  'api_key': api_key,
  'app_key': app_key,
}
initialize(**options)


def get_events(region, role_name, start_time, end_time):
  logger.info('get_events (region: ' + region + ')')
  cloudtrail = boto3.client('cloudtrail', region_name=region)
  events = []
  next_token = ''

  while True:
    # [CloudTrail — Boto 3 Docs 1\.8\.2 documentation](https://boto3.readthedocs.io/en/latest/reference/services/cloudtrail.html)
    # > The rate of lookup requests is limited to one per second per account
    sleep(1)
    try:
      if next_token == '':
        response = cloudtrail.lookup_events(
          LookupAttributes=[
            {
              'AttributeKey': 'Username',
              'AttributeValue': role_name
            },
          ],
          StartTime=start_time,
          EndTime=end_time,
          MaxResults=50,
        )
        logger.debug(response)
      else:
        response = cloudtrail.lookup_events(
          LookupAttributes=[
            {
              'AttributeKey': 'Username',
              'AttributeValue': role_name
            },
          ],
          StartTime=start_time,
          EndTime=end_time,
          MaxResults=50,
          NextToken=next_token,
        )
        logger.debug(response)
    except Exception as e:
      logger.error("failed: %s", str(e))

    events.extend(response['Events'])
    try:
      next_token = response['NextToken']
    except:
      break

  logger.info('get_events result: ' + str(len(events)))
  return(events)


def calc_events(events):
  result = defaultdict(int)
  for event in events:
    key = 'event_name:' + event['EventName'] + '@' + 'event_source:' + event['EventSource']
    result[key] += 1
  return(result)


def post_datadog(aws_account_name, metric_name, aws_account, region, point_time, points):
  for k in points.keys():
    response = ''
    tags = k.split("@")
    tags.append('aws_account:' + aws_account)
    tags.append('region:' + region)
    tags.append('aws_account_name:' + aws_account_name)
    try:
      logger.info('post_datadog tags: ' + str(tags))
      response = api.Metric.send(
        metric=metric_name,
        type='count',
        points=(point_time, points[k]),
        host=aws_account,
        tags=tags,
      )
    except Exception as e:
      logger.error("failed: %s", str(e))
    finally:
      logger.info('post_datadog response: ' + str(response))


def lambda_handler(event, context):
  caller = sts.get_caller_identity()
  timestamp = datetime.now(tz)
  # [How CloudTrail Works \- AWS CloudTrail](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/how-cloudtrail-works.html)
  # > CloudTrail typically delivers log files within 15 minutes of account activity.
  # [CloudTrail — Boto 3 Docs 1\.8\.2 documentation](https://boto3.readthedocs.io/en/latest/reference/services/cloudtrail.html)
  # > StartTime (datetime) -- Specifies that only events that occur after or at the specified time are returned.
  # > EndTime (datetime) -- Specifies that only events that occur before or at the specified time are returned.
  start_time = datetime.strptime((timestamp + timedelta(minutes=-20)).strftime("%Y/%m/%d %H:%M:01"), "%Y/%m/%d %H:%M:%S")
  end_time = datetime.strptime((timestamp + timedelta(minutes=-15)).strftime("%Y/%m/%d %H:%M:00"), "%Y/%m/%d %H:%M:%S")
  point_time = int(end_time.timestamp())
  logger.info('start_time: ' + start_time.strftime("%Y/%m/%d %H:%M:%S") + ' /' + 'end_time: ' + end_time.strftime("%Y/%m/%d %H:%M:%S"))

  regions = ec2.describe_regions()
  for region in regions['Regions']:
    events = get_events(region['RegionName'], role_name, start_time, end_time)
    if events is not None:
      points = calc_events(events)
      if points is not None:
        post_datadog(aws_account_name, metric_name, caller['Account'], region['RegionName'], point_time, points)
