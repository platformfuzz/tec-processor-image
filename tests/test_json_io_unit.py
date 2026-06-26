"""Unit tests for JSON serialization."""

from __future__ import annotations

import json

from processor.json_io import rows_to_json_bytes
from processor.parquet_io import PARQUET_COLUMNS


def _sample_row(**overrides):
    base = {
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
    base.update(overrides)
    return base


def test_json_bytes_returns_bytes():
    result = rows_to_json_bytes([_sample_row()])
    assert isinstance(result, bytes)


def test_json_bytes_is_valid_json():
    result = rows_to_json_bytes([_sample_row()])
    parsed = json.loads(result)
    assert isinstance(parsed, list)


def test_json_bytes_single_row_round_trip():
    row = _sample_row()
    result = rows_to_json_bytes([row])
    parsed = json.loads(result)
    assert len(parsed) == 1
    assert parsed[0]["sv"] == "G01"
    assert parsed[0]["epoch"] == "2024-05-29T01:00:00Z"
    assert parsed[0]["stec"] == 12.3


def test_json_bytes_all_fields_preserved():
    row = _sample_row()
    result = rows_to_json_bytes([row])
    parsed = json.loads(result)
    for col in PARQUET_COLUMNS:
        assert col in parsed[0], f"Missing column: {col}"


def test_json_bytes_multiple_rows():
    rows = [_sample_row(sv="G01"), _sample_row(sv="G02"), _sample_row(sv="E03")]
    result = rows_to_json_bytes(rows)
    parsed = json.loads(result)
    assert len(parsed) == 3
    assert [r["sv"] for r in parsed] == ["G01", "G02", "E03"]


def test_json_bytes_empty_rows():
    result = rows_to_json_bytes([])
    parsed = json.loads(result)
    assert parsed == []


def test_json_bytes_epoch_preserved_as_string():
    row = _sample_row(epoch="2024-05-29T01:00:00Z")
    result = rows_to_json_bytes([row])
    parsed = json.loads(result)
    assert parsed[0]["epoch"] == "2024-05-29T01:00:00Z"


def test_json_bytes_utf8_encoded():
    result = rows_to_json_bytes([_sample_row()])
    decoded = result.decode("utf-8")
    assert len(decoded) > 0
