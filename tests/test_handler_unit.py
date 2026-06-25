import json

from processor import handler as processor_handler


class FakeS3Client:
    def __init__(self, raw_body: bytes = b"rinex-bytes"):
        self.raw_body = raw_body
        self.gets = []
        self.puts = []

    def get_object(self, **kwargs):
        self.gets.append(kwargs)
        return {"Body": _FakeBody(self.raw_body)}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        return {"ETag": "ok"}


class _FakeBody:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class FakeDdbClient:
    def __init__(self):
        self.updates = []

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        return {}


class FakeBoto3:
    def __init__(self, s3, ddb):
        self._s3 = s3
        self._ddb = ddb

    def client(self, service_name):
        if service_name == "s3":
            return self._s3
        if service_name == "dynamodb":
            return self._ddb
        raise ValueError(service_name)


def _sample_row():
    return {
        "epoch": "2024-05-29T01:00:00Z",
        "sv": "G01",
        "id_arc": 1,
        "lat_ipp": -36.85,
        "lon_ipp": 174.76,
        "azi": 45.2,
        "ele": 30.1,
        "bias": 0.5,
        "stec": 12.3,
        "vtec": 8.7,
        "veq": 9.1,
    }


def test_processor_handler_skips_s3_test_event(monkeypatch):
    fake_s3 = FakeS3Client()
    fake_ddb = FakeDdbClient()
    monkeypatch.setenv("DATA_LAKE_BUCKET", "lake-bucket")
    monkeypatch.setattr(processor_handler, "boto3", FakeBoto3(fake_s3, fake_ddb))

    event = {
        "Records": [
            {
                "messageId": "test-1",
                "body": json.dumps(
                    {
                        "Service": "Amazon S3",
                        "Event": "s3:TestEvent",
                        "Time": "2026-06-24T22:00:00.000Z",
                        "Bucket": "lake-bucket",
                    }
                ),
            }
        ]
    }

    result = processor_handler.handler(event, None)
    assert result == {"batchItemFailures": []}
    assert fake_s3.puts == []
    assert fake_s3.gets == []


def test_processor_handler_writes_parquet_on_success(monkeypatch):
    fake_s3 = FakeS3Client()
    fake_ddb = FakeDdbClient()
    monkeypatch.setenv("DATA_LAKE_BUCKET", "lake-bucket")
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs-table")
    monkeypatch.setenv("NAV_DAY_OFFSET", "1")
    monkeypatch.setattr(processor_handler, "boto3", FakeBoto3(fake_s3, fake_ddb))
    monkeypatch.setattr(processor_handler, "download_nav_file", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(processor_handler, "run_calibration", lambda *_args, **_kwargs: [_sample_row()])
    monkeypatch.setattr(processor_handler, "rows_to_parquet_bytes", lambda rows: b"PAR1" + json.dumps(rows).encode())

    event = {
        "Records": [
            {
                "messageId": "ok-1",
                "body": json.dumps(
                    {"key": "raw/rinexhourly/2024/150/auck1500.24o", "job_id": "job-1"}
                ),
            }
        ]
    }

    result = processor_handler.handler(event, None)
    assert result == {"batchItemFailures": []}
    assert len(fake_s3.gets) == 1
    assert fake_s3.gets[0]["Key"] == "raw/rinexhourly/2024/150/auck1500.24o"
    assert len(fake_s3.puts) == 1
    assert fake_s3.puts[0]["Key"] == "processed/station=auck/year=2024/doy=150/auck1500.parquet"
    assert fake_s3.puts[0]["ContentType"] == "application/vnd.apache.parquet"
    assert fake_ddb.updates


def test_processor_handler_reports_partial_failures(monkeypatch):
    fake_s3 = FakeS3Client()
    fake_ddb = FakeDdbClient()
    monkeypatch.setenv("DATA_LAKE_BUCKET", "lake-bucket")
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs-table")
    monkeypatch.setenv("NAV_DAY_OFFSET", "1")
    monkeypatch.setattr(processor_handler, "boto3", FakeBoto3(fake_s3, fake_ddb))
    monkeypatch.setattr(processor_handler, "download_nav_file", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(processor_handler, "run_calibration", lambda *_args, **_kwargs: [_sample_row()])
    monkeypatch.setattr(processor_handler, "rows_to_parquet_bytes", lambda _rows: b"PAR1")

    event = {
        "Records": [
            {
                "messageId": "ok-1",
                "body": json.dumps({"key": "raw/rinexhourly/2024/150/auck1500.24o"}),
            },
            {
                "messageId": "bad-1",
                "body": json.dumps({"key": "raw/rinexhourly/2024/150/12341500.24o"}),
            },
        ]
    }

    result = processor_handler.handler(event, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "bad-1"}]}
    assert len(fake_s3.puts) == 1


def test_processor_handler_fails_when_calibration_fails(monkeypatch):
    fake_s3 = FakeS3Client()
    fake_ddb = FakeDdbClient()
    monkeypatch.setenv("DATA_LAKE_BUCKET", "lake-bucket")
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs-table")
    monkeypatch.setattr(processor_handler, "boto3", FakeBoto3(fake_s3, fake_ddb))
    monkeypatch.setattr(processor_handler, "download_nav_file", lambda *_args, **_kwargs: object())

    def _boom(*_args, **_kwargs):
        raise RuntimeError("calibration failed")

    monkeypatch.setattr(processor_handler, "run_calibration", _boom)

    event = {
        "Records": [
            {
                "messageId": "bad-cal",
                "body": json.dumps({"key": "raw/rinexhourly/2024/150/auck1500.24o", "job_id": "job-1"}),
            }
        ]
    }

    result = processor_handler.handler(event, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "bad-cal"}]}
    assert fake_s3.puts == []
    assert fake_ddb.updates
    assert fake_ddb.updates[-1]["ExpressionAttributeValues"][":status"]["S"] == "failed"


# --- Additional handler unit tests (task 4.13) ---


def test_handler_invalid_json_body(monkeypatch):
    """Invalid JSON in record body should appear in batchItemFailures.

    Validates: Requirements 3.10
    """
    fake_s3 = FakeS3Client()
    fake_ddb = FakeDdbClient()
    monkeypatch.setenv("DATA_LAKE_BUCKET", "lake-bucket")
    monkeypatch.setattr(processor_handler, "boto3", FakeBoto3(fake_s3, fake_ddb))

    event = {
        "Records": [
            {
                "messageId": "bad-json-1",
                "body": "this is not valid json {{{",
            }
        ]
    }

    result = processor_handler.handler(event, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "bad-json-1"}]}
    # No S3 operations should occur
    assert fake_s3.gets == []
    assert fake_s3.puts == []


def test_handler_bucket_mismatch_rejection(monkeypatch):
    """Record with bucket != DATA_LAKE_BUCKET should be rejected.

    Validates: Requirements 3.11
    """
    fake_s3 = FakeS3Client()
    fake_ddb = FakeDdbClient()
    monkeypatch.setenv("DATA_LAKE_BUCKET", "lake-bucket")
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs-table")
    monkeypatch.setattr(processor_handler, "boto3", FakeBoto3(fake_s3, fake_ddb))
    monkeypatch.setattr(processor_handler, "download_nav_file", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(processor_handler, "run_calibration", lambda *_args, **_kwargs: [_sample_row()])

    event = {
        "Records": [
            {
                "messageId": "mismatch-1",
                "body": json.dumps({
                    "key": "raw/rinexhourly/2024/150/auck1500.24o",
                    "bucket": "wrong-bucket",
                }),
            }
        ]
    }

    result = processor_handler.handler(event, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "mismatch-1"}]}
    # No S3 get/put should have been attempted for a rejected record
    assert fake_s3.gets == []
    assert fake_s3.puts == []


def test_handler_ddb_update_failure_isolation(monkeypatch):
    """DynamoDB update failures should not cause record processing to fail.

    When DDB throws an exception, the record should still succeed if calibration
    works — only a warning should be logged.

    Validates: Requirements 8.5
    """

    class FailingDdbClient:
        def __init__(self):
            self.call_count = 0

        def update_item(self, **kwargs):
            self.call_count += 1
            raise RuntimeError("DynamoDB is down")

    fake_s3 = FakeS3Client()
    failing_ddb = FailingDdbClient()
    monkeypatch.setenv("DATA_LAKE_BUCKET", "lake-bucket")
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs-table")
    monkeypatch.setenv("NAV_DAY_OFFSET", "1")
    monkeypatch.setattr(processor_handler, "boto3", FakeBoto3(fake_s3, failing_ddb))
    monkeypatch.setattr(processor_handler, "download_nav_file", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(processor_handler, "run_calibration", lambda *_args, **_kwargs: [_sample_row()])
    monkeypatch.setattr(processor_handler, "rows_to_parquet_bytes", lambda rows: b"PAR1data")

    event = {
        "Records": [
            {
                "messageId": "ddb-fail-1",
                "body": json.dumps({
                    "key": "raw/rinexhourly/2024/150/auck1500.24o",
                    "job_id": "job-1",
                }),
            }
        ]
    }

    result = processor_handler.handler(event, None)
    # Record should succeed despite DDB failure (isolation)
    assert result == {"batchItemFailures": []}
    # S3 put should still have happened
    assert len(fake_s3.puts) == 1
    # DDB was attempted (at least "processing" and "completed" updates)
    assert failing_ddb.call_count >= 1


def test_handler_data_lake_bucket_unset_raises(monkeypatch):
    """Handler should raise RuntimeError immediately if DATA_LAKE_BUCKET is unset.

    This is a fail-fast check before processing any records.

    Validates: Requirements 6.6
    """
    fake_s3 = FakeS3Client()
    fake_ddb = FakeDdbClient()
    monkeypatch.delenv("DATA_LAKE_BUCKET", raising=False)
    monkeypatch.setattr(processor_handler, "boto3", FakeBoto3(fake_s3, fake_ddb))

    event = {
        "Records": [
            {
                "messageId": "msg-1",
                "body": json.dumps({"key": "raw/rinexhourly/2024/150/auck1500.24o"}),
            }
        ]
    }

    import pytest

    with pytest.raises(RuntimeError, match="DATA_LAKE_BUCKET is required"):
        processor_handler.handler(event, None)

    # No S3 operations should have been attempted
    assert fake_s3.gets == []
    assert fake_s3.puts == []


def test_handler_full_success_all_deps_mocked(monkeypatch):
    """Full success scenario exercising all mocked dependencies.

    Verifies that: S3 get is called for raw RINEX, nav fetch is called,
    calibration is invoked, Parquet is written to S3, and DDB is updated
    to 'processing' then 'completed'.

    Validates: Requirements 3.1, 3.5, 3.7, 3.8, 3.9, 8.4
    """
    fake_s3 = FakeS3Client(raw_body=b"RINEX-OBS-DATA")
    fake_ddb = FakeDdbClient()
    monkeypatch.setenv("DATA_LAKE_BUCKET", "lake-bucket")
    monkeypatch.setenv("JOBS_TABLE_NAME", "jobs-table")
    monkeypatch.setenv("NAV_DAY_OFFSET", "1")

    monkeypatch.setattr(processor_handler, "boto3", FakeBoto3(fake_s3, fake_ddb))

    nav_calls = []

    def mock_download_nav(*args, **kwargs):
        nav_calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(processor_handler, "download_nav_file", mock_download_nav)

    calibration_calls = []

    def mock_calibration(*args, **kwargs):
        calibration_calls.append((args, kwargs))
        return [_sample_row(), _sample_row()]

    monkeypatch.setattr(processor_handler, "run_calibration", mock_calibration)
    monkeypatch.setattr(processor_handler, "rows_to_parquet_bytes", lambda rows: b"PAR1parquet")

    event = {
        "Records": [
            {
                "messageId": "full-ok-1",
                "body": json.dumps({
                    "key": "raw/rinexhourly/2024/150/auck1500.24o",
                    "job_id": "job-42",
                    "trace_id": "trace-abc-123",
                }),
            }
        ]
    }

    result = processor_handler.handler(event, None)

    # No failures
    assert result == {"batchItemFailures": []}

    # S3 get was called with the correct bucket and key
    assert len(fake_s3.gets) == 1
    assert fake_s3.gets[0]["Bucket"] == "lake-bucket"
    assert fake_s3.gets[0]["Key"] == "raw/rinexhourly/2024/150/auck1500.24o"

    # S3 put was called with proper output key
    assert len(fake_s3.puts) == 1
    assert fake_s3.puts[0]["Bucket"] == "lake-bucket"
    assert fake_s3.puts[0]["Key"] == "processed/station=auck/year=2024/doy=150/auck1500.parquet"
    assert fake_s3.puts[0]["ContentType"] == "application/vnd.apache.parquet"
    assert fake_s3.puts[0]["Body"] == b"PAR1parquet"

    # Nav fetch was called
    assert len(nav_calls) == 1

    # Calibration was called
    assert len(calibration_calls) == 1

    # DDB was updated (processing + completed = 2 updates)
    assert len(fake_ddb.updates) == 2
    # First update: status = processing
    assert fake_ddb.updates[0]["ExpressionAttributeValues"][":status"]["S"] == "processing"
    # Second update: status = completed with output_key
    assert fake_ddb.updates[1]["ExpressionAttributeValues"][":status"]["S"] == "completed"
    assert fake_ddb.updates[1]["ExpressionAttributeValues"][":output_key"]["S"] == (
        "processed/station=auck/year=2024/doy=150/auck1500.parquet"
    )
