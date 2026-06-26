"""Unit tests for CSV serialization."""

from __future__ import annotations

import csv
import io

import pytest

from processor.csv_io import rows_to_csv_bytes
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


def test_csv_bytes_returns_bytes():
    result = rows_to_csv_bytes([_sample_row()])
    assert isinstance(result, bytes)


def test_csv_bytes_utf8_decodable():
    result = rows_to_csv_bytes([_sample_row()])
    decoded = result.decode("utf-8")
    assert len(decoded) > 0


def test_csv_bytes_has_header_row():
    result = rows_to_csv_bytes([_sample_row()])
    lines = result.decode("utf-8").splitlines()
    assert lines[0] == ",".join(PARQUET_COLUMNS)


def test_csv_bytes_header_matches_parquet_columns():
    result = rows_to_csv_bytes([_sample_row()])
    reader = csv.DictReader(io.StringIO(result.decode("utf-8")))
    assert list(reader.fieldnames) == list(PARQUET_COLUMNS)


def test_csv_bytes_single_row_round_trip():
    row = _sample_row()
    result = rows_to_csv_bytes([row])
    reader = csv.DictReader(io.StringIO(result.decode("utf-8")))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["sv"] == "G01"
    assert rows[0]["epoch"] == "2024-05-29T01:00:00Z"


def test_csv_bytes_multiple_rows():
    rows = [_sample_row(sv="G01"), _sample_row(sv="G02"), _sample_row(sv="E03")]
    result = rows_to_csv_bytes(rows)
    reader = csv.DictReader(io.StringIO(result.decode("utf-8")))
    parsed = list(reader)
    assert len(parsed) == 3
    assert [r["sv"] for r in parsed] == ["G01", "G02", "E03"]


def test_csv_bytes_empty_rows_returns_header_only():
    result = rows_to_csv_bytes([])
    lines = result.decode("utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0] == ",".join(PARQUET_COLUMNS)
