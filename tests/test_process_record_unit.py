"""Unit tests for process_record orchestration function."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from processor.logic import process_record


@pytest.fixture
def valid_payload():
    return {
        "key": "raw/rinexhourly/2024/150/auck1500.24o",
        "bucket": "my-data-lake",
        "job_id": "job-123",
        "trace_id": "550e8400-e29b-41d4-a716-446655440000",
        "parameters": None,
    }


@pytest.fixture
def env_params():
    return {
        "NAV_DAY_OFFSET": 1,
        "SAVE_PARQUET": True,
        "SAVE_CSV": False,
        "SAVE_STATIC_PLOTS": False,
        "SAVE_INTERACTIVE_PLOTS": False,
    }


@pytest.fixture
def mock_calibration_rows():
    return [
        {
            "epoch": "2024-05-29T00:00:00Z",
            "sv": "G01",
            "id_arc": 1,
            "lat_ipp": -36.85,
            "lon_ipp": 174.76,
            "azi": 45.0,
            "ele": 30.0,
            "bias": 1.23,
            "stec": 10.5,
            "vtec": 8.2,
            "veq": 8.1,
        }
    ]


@patch("processor.logic.fetch_nav_file")
@patch("processor.logic.run_calibration")
@patch("processor.logic.rows_to_parquet_bytes")
def test_process_record_success(
    mock_parquet, mock_calibration, mock_nav, valid_payload, env_params, mock_calibration_rows
):
    """process_record returns output_key on successful processing."""
    mock_nav.return_value = Path("/tmp/nav_file.rnx")
    mock_calibration.return_value = mock_calibration_rows
    mock_parquet.return_value = b"PAR1fake_parquet_data"

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=b"rinex_data"))}

    with patch("processor.logic.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_s3
        result = process_record(valid_payload, "my-data-lake", env_params)

    assert result == "processed/station=auck/year=2024/doy=150/auck1500.parquet"
    mock_nav.assert_called_once_with(2024, 150, 1)
    mock_s3.get_object.assert_called_once_with(Bucket="my-data-lake", Key="raw/rinexhourly/2024/150/auck1500.24o")
    mock_s3.put_object.assert_called_once()


def test_process_record_bucket_mismatch(env_params):
    """process_record raises ValueError on bucket mismatch."""
    payload = {
        "key": "raw/rinexhourly/2024/150/auck1500.24o",
        "bucket": "wrong-bucket",
        "trace_id": None,
        "parameters": None,
    }

    with pytest.raises(ValueError, match="Unexpected source bucket"):
        process_record(payload, "my-data-lake", env_params)


def test_process_record_missing_key(env_params):
    """process_record raises ValueError when payload has no key."""
    payload = {"bucket": "my-data-lake", "trace_id": None, "parameters": None}

    with pytest.raises(ValueError, match="missing required 'key' field"):
        process_record(payload, "my-data-lake", env_params)


def test_process_record_invalid_key(env_params):
    """process_record raises ValueError for malformed raw key."""
    payload = {
        "key": "invalid/path/file.txt",
        "trace_id": None,
        "parameters": None,
    }

    with pytest.raises(ValueError, match="Malformed raw key"):
        process_record(payload, "my-data-lake", env_params)


@patch("processor.logic.fetch_nav_file")
@patch("processor.logic.run_calibration")
@patch("processor.logic.rows_to_parquet_bytes")
def test_process_record_generates_trace_id(
    mock_parquet, mock_calibration, mock_nav, env_params, mock_calibration_rows
):
    """process_record generates UUID v4 trace_id when not provided."""
    payload = {
        "key": "raw/rinexhourly/2024/150/auck1500.24o",
        "bucket": "my-data-lake",
        "trace_id": None,
        "parameters": None,
    }

    mock_nav.return_value = Path("/tmp/nav_file.rnx")
    mock_calibration.return_value = mock_calibration_rows
    mock_parquet.return_value = b"PAR1fake_parquet_data"

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=b"rinex_data"))}

    with patch("processor.logic.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_s3
        result = process_record(payload, "my-data-lake", env_params)

    assert result == "processed/station=auck/year=2024/doy=150/auck1500.parquet"


@patch("processor.logic.fetch_nav_file")
@patch("processor.logic.run_calibration")
@patch("processor.logic.rows_to_parquet_bytes")
def test_process_record_no_bucket_field_uses_default(
    mock_parquet, mock_calibration, mock_nav, env_params, mock_calibration_rows
):
    """process_record succeeds when payload has no bucket field (uses default)."""
    payload = {
        "key": "raw/rinexhourly/2024/150/auck1500.24o",
        "trace_id": "abc-123",
        "parameters": None,
    }

    mock_nav.return_value = Path("/tmp/nav_file.rnx")
    mock_calibration.return_value = mock_calibration_rows
    mock_parquet.return_value = b"PAR1fake_parquet_data"

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=b"rinex_data"))}

    with patch("processor.logic.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_s3
        result = process_record(payload, "my-data-lake", env_params)

    assert result == "processed/station=auck/year=2024/doy=150/auck1500.parquet"


@patch("processor.logic.fetch_nav_file")
@patch("processor.logic.run_calibration")
@patch("processor.logic.rows_to_parquet_bytes")
def test_process_record_message_params_override(
    mock_parquet, mock_calibration, mock_nav, mock_calibration_rows
):
    """process_record merges message parameters over env defaults."""
    payload = {
        "key": "raw/rinexhourly/2024/150/auck1500.24o",
        "bucket": "my-data-lake",
        "trace_id": "trace-1",
        "parameters": {"NAV_DAY_OFFSET": 3},
    }
    env = {
        "NAV_DAY_OFFSET": 1,
        "SAVE_PARQUET": True,
        "SAVE_CSV": False,
        "SAVE_STATIC_PLOTS": False,
        "SAVE_INTERACTIVE_PLOTS": False,
    }

    mock_nav.return_value = Path("/tmp/nav_file.rnx")
    mock_calibration.return_value = mock_calibration_rows
    mock_parquet.return_value = b"PAR1fake_parquet_data"

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=b"rinex_data"))}

    with patch("processor.logic.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_s3
        result = process_record(payload, "my-data-lake", env)

    assert result == "processed/station=auck/year=2024/doy=150/auck1500.parquet"
    # NAV_DAY_OFFSET=3 means fetch_nav_file should be called with offset 3
    mock_nav.assert_called_once_with(2024, 150, 3)
