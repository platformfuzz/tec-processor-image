# Feature: tec-processor-image, Property 1: Partial Batch Failure Completeness
"""Property test for batch failure isolation.

Generate batches of N records with K successes and N-K failures.
Assert batchItemFailures contains exactly N-K entries.
Assert no successful record's messageId appears in failures.

**Validates: Requirements 3.1, 3.7, 3.8, 3.9**
"""

from __future__ import annotations

import json
import string
from unittest.mock import patch

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from processor import handler as processor_handler


# --- Fake AWS clients (same pattern as unit tests) ---


class _FakeBody:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class FakeS3Client:
    def __init__(self):
        self.gets = []
        self.puts = []

    def get_object(self, **kwargs):
        self.gets.append(kwargs)
        return {"Body": _FakeBody(b"rinex-bytes")}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        return {"ETag": "ok"}


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


# --- Hypothesis strategies ---

# Unique message IDs
_message_id = st.text(
    alphabet=string.ascii_lowercase + string.digits + "-",
    min_size=5,
    max_size=20,
)

# Valid station names (4 lowercase alpha chars)
_station = st.text(alphabet=string.ascii_lowercase, min_size=4, max_size=4)


@st.composite
def batch_with_failures(draw):
    """Generate a batch of N records (1-10) with K successes and N-K failures.

    Returns (records, success_ids, failure_ids) where:
      - records: list of SQS record dicts
      - success_ids: set of messageIds that should succeed
      - failure_ids: set of messageIds that should fail
    """
    n = draw(st.integers(min_value=1, max_value=10))

    # Generate N unique message IDs
    message_ids = draw(
        st.lists(
            _message_id,
            min_size=n,
            max_size=n,
            unique=True,
        )
    )

    # Randomly decide which records fail (at least 0, at most N)
    # Each record independently might fail
    fail_mask = draw(
        st.lists(st.booleans(), min_size=n, max_size=n)
    )

    records = []
    success_ids = set()
    failure_ids = set()

    for i, msg_id in enumerate(message_ids):
        should_fail = fail_mask[i]

        if should_fail:
            # Create a record with invalid JSON body to trigger a failure
            body = "<<<not valid json>>>"
            failure_ids.add(msg_id)
        else:
            # Create a record with a valid direct processor message
            station = draw(_station)
            year = draw(st.integers(min_value=2000, max_value=2099))
            doy = draw(st.integers(min_value=1, max_value=366))
            key = f"raw/rinexhourly/{year}/{doy:03d}/{station}{doy:03d}0.{year % 100:02d}o"
            body = json.dumps({"key": key})
            success_ids.add(msg_id)

        records.append({"messageId": msg_id, "body": body})

    return records, success_ids, failure_ids


# --- Property test ---


class TestPartialBatchFailureCompleteness:
    """Property 1: Partial Batch Failure Completeness.

    For any SQS event with N records where K records succeed and N-K records fail,
    the handler response SHALL contain a batchItemFailures array with exactly N-K
    entries, each containing the messageId of a failed record, and no successful
    record's messageId SHALL appear in that array.

    **Validates: Requirements 3.1, 3.7, 3.8, 3.9**
    """

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(data=batch_with_failures())
    def test_batch_failure_isolation(self, data, monkeypatch):
        """batchItemFailures contains exactly the failed records' messageIds."""
        records, success_ids, failure_ids = data

        # Set up environment
        monkeypatch.setenv("SOURCE_BUCKET", "test-source-bucket")
        monkeypatch.setenv("SOURCE_PREFIX", "raw/rinexhourly")
        monkeypatch.setenv("DESTINATION_BUCKET", "test-destination-bucket")
        monkeypatch.setenv("DESTINATION_PREFIX", "processed/tec")

        # Set up fake AWS clients
        fake_s3 = FakeS3Client()
        fake_ddb = FakeDdbClient()
        monkeypatch.setattr(processor_handler, "boto3", FakeBoto3(fake_s3, fake_ddb))

        # Mock download_nav_file and run_calibration to succeed for valid records
        monkeypatch.setattr(
            processor_handler, "download_nav_file", lambda *a, **kw: "/tmp/nav.file"
        )
        monkeypatch.setattr(
            processor_handler,
            "run_calibration",
            lambda *a, **kw: [
                {
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
            ],
        )
        monkeypatch.setattr(
            processor_handler, "rows_to_parquet_bytes", lambda rows: b"PAR1fake"
        )

        # Call the handler
        event = {"Records": records}
        result = processor_handler.handler(event, None)

        # Extract failure identifiers from response
        batch_failures = result.get("batchItemFailures", [])
        failed_identifiers = {f["itemIdentifier"] for f in batch_failures}

        # Assert exactly N-K entries in batchItemFailures
        assert len(batch_failures) == len(failure_ids), (
            f"Expected {len(failure_ids)} failures, got {len(batch_failures)}. "
            f"Expected failure IDs: {failure_ids}, got: {failed_identifiers}"
        )

        # Assert each failure identifier matches a failed record's messageId
        assert failed_identifiers == failure_ids, (
            f"Failure IDs mismatch. Expected: {failure_ids}, got: {failed_identifiers}"
        )

        # Assert no successful record's messageId appears in failures
        assert failed_identifiers.isdisjoint(success_ids), (
            f"Successful records found in failures: {failed_identifiers & success_ids}"
        )
