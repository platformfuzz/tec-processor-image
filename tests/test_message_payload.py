import json

import pytest

from processor.logic import extract_message_payload


def test_extract_direct_processor_payload():
    payload = extract_message_payload(json.dumps({"key": "raw/rinexhourly/2024/150/auck1500.24o"}))
    assert payload["key"] == "raw/rinexhourly/2024/150/auck1500.24o"


def test_extract_s3_event_notification():
    body = {
        "Records": [
            {
                "eventVersion": "2.1",
                "eventSource": "aws:s3",
                "eventTime": "2026-06-24T22:00:00.000Z",
                "s3": {
                    "bucket": {"name": "data-lake-abc123"},
                    "object": {"key": "raw/rinexhourly/2026/176/auck1760.26o"},
                },
            }
        ]
    }
    payload = extract_message_payload(json.dumps(body))
    assert payload["bucket"] == "data-lake-abc123"
    assert payload["key"] == "raw/rinexhourly/2026/176/auck1760.26o"
    assert payload["event_time"] == "2026-06-24T22:00:00.000Z"
    assert payload["job_id"] is None


def test_extract_s3_event_url_encoded_key():
    body = {
        "Records": [
            {
                "eventSource": "aws:s3",
                "s3": {
                    "bucket": {"name": "data-lake-abc123"},
                    "object": {"key": "raw/rinexhourly/2026/176/file+name.26o"},
                },
            }
        ]
    }
    payload = extract_message_payload(json.dumps(body))
    assert payload["key"] == "raw/rinexhourly/2026/176/file name.26o"


def test_extract_s3_test_event():
    body = {
        "Service": "Amazon S3",
        "Event": "s3:TestEvent",
        "Time": "2026-06-24T22:00:00.000Z",
        "Bucket": "data-lake-abc123",
    }
    payload = extract_message_payload(json.dumps(body))
    assert payload == {"_s3_test_event": True}


def test_extract_sns_envelope():
    inner = {
        "Records": [
            {
                "eventSource": "aws:s3",
                "s3": {
                    "bucket": {"name": "data-lake-abc123"},
                    "object": {"key": "raw/rinexhourly/2026/176/auck1760.26o"},
                },
            }
        ]
    }
    body = {"Type": "Notification", "TopicArn": "arn:aws:sns:...", "Message": json.dumps(inner)}
    payload = extract_message_payload(json.dumps(body))
    assert payload["key"] == "raw/rinexhourly/2026/176/auck1760.26o"


def test_extract_missing_key_raises():
    with pytest.raises(ValueError, match="missing required 'key'"):
        extract_message_payload("{}")
