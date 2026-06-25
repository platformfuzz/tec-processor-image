"""Shared test fixtures for the TEC processor test suite."""

from __future__ import annotations

import json
import os

import boto3
import pytest
from moto import mock_aws


@pytest.fixture()
def env_vars(monkeypatch):
    """Set standard environment variables for processor tests."""
    monkeypatch.setenv("DATA_LAKE_BUCKET", "test-data-lake")
    monkeypatch.setenv("JOBS_TABLE_NAME", "test-jobs-table")
    monkeypatch.setenv("NAV_DAY_OFFSET", "1")
    monkeypatch.setenv("SAVE_PARQUET", "true")
    monkeypatch.setenv("SAVE_CSV", "false")
    monkeypatch.setenv("SAVE_STATIC_PLOTS", "false")
    monkeypatch.setenv("SAVE_INTERACTIVE_PLOTS", "false")


@pytest.fixture()
def mock_s3():
    """Create a mocked S3 client with the test bucket already created."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-data-lake")
        yield client


@pytest.fixture()
def mock_dynamodb():
    """Create a mocked DynamoDB table with job_id as partition key."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="test-jobs-table",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


@pytest.fixture()
def sample_sqs_event():
    """Return a minimal SQS event with a single record containing a direct message."""
    return {
        "Records": [
            {
                "messageId": "msg-001",
                "body": json.dumps(
                    {"key": "raw/rinexhourly/2024/150/auck1500.24o"}
                ),
            }
        ]
    }


@pytest.fixture()
def sample_direct_payload():
    """Return a direct processor message payload."""
    return {
        "key": "raw/rinexhourly/2024/150/auck1500.24o",
        "bucket": "test-data-lake",
        "job_id": "job-abc-123",
        "trace_id": "550e8400-e29b-41d4-a716-446655440000",
        "parameters": {
            "NAV_DAY_OFFSET": 2,
            "SAVE_PARQUET": True,
        },
    }


@pytest.fixture()
def sample_s3_event_payload():
    """Return a payload in S3 event notification format."""
    return {
        "Records": [
            {
                "eventVersion": "2.1",
                "eventSource": "aws:s3",
                "eventTime": "2024-05-29T12:00:00.000Z",
                "s3": {
                    "bucket": {"name": "test-data-lake"},
                    "object": {"key": "raw/rinexhourly/2024/150/auck1500.24o"},
                },
            }
        ]
    }


@pytest.fixture()
def sample_sns_wrapped_payload():
    """Return a payload in SNS-wrapped S3 event format."""
    inner_s3_event = {
        "Records": [
            {
                "eventVersion": "2.1",
                "eventSource": "aws:s3",
                "eventTime": "2024-05-29T12:00:00.000Z",
                "s3": {
                    "bucket": {"name": "test-data-lake"},
                    "object": {"key": "raw/rinexhourly/2024/150/auck1500.24o"},
                },
            }
        ]
    }
    return {
        "Type": "Notification",
        "TopicArn": "arn:aws:sns:us-east-1:123456789012:s3-events",
        "Message": json.dumps(inner_s3_event),
    }


@pytest.fixture()
def env_params():
    """Return the default processing parameters dict."""
    return {
        "NAV_DAY_OFFSET": 1,
        "SAVE_PARQUET": True,
        "SAVE_CSV": False,
        "SAVE_STATIC_PLOTS": False,
        "SAVE_INTERACTIVE_PLOTS": False,
    }
