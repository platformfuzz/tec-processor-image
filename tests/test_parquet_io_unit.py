import pytest
from io import BytesIO
from unittest.mock import patch, MagicMock

pyarrow = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq  # noqa: E402

from processor.parquet_io import (  # noqa: E402
    PARQUET_COLUMNS,
    OUTPUT_COLUMNS,
    rows_to_parquet_bytes,
    write_parquet,
    build_output_key,
)
from processor import OutputError  # noqa: E402


# ---------------------------------------------------------------------------
# Helper: sample row fixture
# ---------------------------------------------------------------------------

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


def _sample_table():
    """Build a pyarrow Table with the correct schema for write_parquet."""
    import pyarrow as pa
    from datetime import datetime, timezone

    return pa.table(
        {
            "epoch": pa.array(
                [datetime(2024, 5, 29, 1, 0, 0, tzinfo=timezone.utc)],
                type=pa.timestamp("us", tz="UTC"),
            ),
            "sv": pa.array(["G01"], type=pa.string()),
            "id_arc": pa.array([1], type=pa.int32()),
            "lat_ipp": pa.array([-36.85], type=pa.float64()),
            "lon_ipp": pa.array([174.76], type=pa.float64()),
            "azi": pa.array([45.2], type=pa.float64()),
            "ele": pa.array([30.1], type=pa.float64()),
            "bias": pa.array([0.5], type=pa.float64()),
            "stec": pa.array([12.3], type=pa.float64()),
            "vtec": pa.array([8.7], type=pa.float64()),
            "veq": pa.array([9.1], type=pa.float64()),
        }
    )


# ---------------------------------------------------------------------------
# 1. rows_to_parquet_bytes produces bytes starting with PAR1 magic
# ---------------------------------------------------------------------------

def test_rows_to_parquet_bytes_round_trip():
    rows = [_sample_row()]
    payload = rows_to_parquet_bytes(rows)
    assert payload.startswith(b"PAR1")
    table = pyarrow.parquet.read_table(pyarrow.BufferReader(payload))
    assert table.column_names == list(PARQUET_COLUMNS)


def test_rows_to_parquet_bytes_par1_magic():
    """Parquet binary starts with PAR1 magic bytes."""
    payload = rows_to_parquet_bytes([_sample_row()])
    assert payload[:4] == b"PAR1"


# ---------------------------------------------------------------------------
# 2. Output readable by pyarrow.parquet.read_table without error
# ---------------------------------------------------------------------------

def test_rows_to_parquet_bytes_readable_by_pyarrow():
    """Output is readable by pyarrow without raising exceptions."""
    payload = rows_to_parquet_bytes([_sample_row()])
    # Should not raise
    table = pq.read_table(BytesIO(payload))
    assert table.num_rows == 1


def test_rows_to_parquet_bytes_multiple_rows_readable():
    """Multiple rows produce valid, readable Parquet."""
    rows = [_sample_row() for _ in range(10)]
    payload = rows_to_parquet_bytes(rows)
    table = pq.read_table(BytesIO(payload))
    assert table.num_rows == 10


# ---------------------------------------------------------------------------
# 3. Table has exactly 11 columns matching OUTPUT_COLUMNS
# ---------------------------------------------------------------------------

def test_parquet_schema_has_exactly_11_columns():
    """Parquet output contains exactly 11 columns matching OUTPUT_COLUMNS."""
    payload = rows_to_parquet_bytes([_sample_row()])
    table = pq.read_table(BytesIO(payload))
    assert len(table.column_names) == 11
    assert table.column_names == OUTPUT_COLUMNS


# ---------------------------------------------------------------------------
# 4. write_parquet with mocked S3 writes to correct key
# ---------------------------------------------------------------------------

@patch("processor.parquet_io.boto3")
def test_write_parquet_writes_to_correct_key(mock_boto3):
    """write_parquet calls S3 put_object with the deterministic key."""
    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3

    table = _sample_table()
    key = write_parquet(table, "my-bucket", "auck", 2024, 150, "auck1500")

    assert key == "processed/station=auck/year=2024/doy=150/auck1500.parquet"
    mock_s3.put_object.assert_called_once()
    call_kwargs = mock_s3.put_object.call_args[1]
    assert call_kwargs["Bucket"] == "my-bucket"
    assert call_kwargs["Key"] == "processed/station=auck/year=2024/doy=150/auck1500.parquet"
    # Body should be Parquet bytes starting with PAR1
    assert call_kwargs["Body"][:4] == b"PAR1"


# ---------------------------------------------------------------------------
# 5. Calling write_parquet twice overwrites existing key (idempotent)
# ---------------------------------------------------------------------------

@patch("processor.parquet_io.boto3")
def test_write_parquet_idempotent_overwrite(mock_boto3):
    """Calling write_parquet twice overwrites existing key (idempotent)."""
    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3

    table = _sample_table()

    key1 = write_parquet(table, "my-bucket", "auck", 2024, 150, "auck1500")
    key2 = write_parquet(table, "my-bucket", "auck", 2024, 150, "auck1500")

    # Both calls should produce the same key
    assert key1 == key2
    # put_object should have been called twice (overwrite semantics)
    assert mock_s3.put_object.call_count == 2
    # Both calls target the same key
    for call in mock_s3.put_object.call_args_list:
        assert call[1]["Key"] == "processed/station=auck/year=2024/doy=150/auck1500.parquet"


# ---------------------------------------------------------------------------
# 6. Schema mismatch (wrong columns) raises OutputError
# ---------------------------------------------------------------------------

@patch("processor.parquet_io.boto3")
def test_write_parquet_schema_mismatch_raises_output_error(mock_boto3):
    """write_parquet raises OutputError when DataFrame has wrong columns."""
    import pyarrow as pa

    # Table with wrong columns
    bad_table = pa.table({"wrong_col": pa.array([1, 2, 3])})

    with pytest.raises(OutputError, match="schema mismatch"):
        write_parquet(bad_table, "my-bucket", "auck", 2024, 150, "auck1500")


@patch("processor.parquet_io.boto3")
def test_write_parquet_extra_column_raises_output_error(mock_boto3):
    """write_parquet raises OutputError when DataFrame has extra columns."""
    import pyarrow as pa
    from datetime import datetime, timezone

    # Table with 11 correct columns + 1 extra
    data = {col: pa.array([1.0], type=pa.float64()) for col in OUTPUT_COLUMNS}
    data["epoch"] = pa.array(
        [datetime(2024, 5, 29, tzinfo=timezone.utc)],
        type=pa.timestamp("us", tz="UTC"),
    )
    data["sv"] = pa.array(["G01"], type=pa.string())
    data["id_arc"] = pa.array([1], type=pa.int32())
    data["extra_col"] = pa.array([99.0], type=pa.float64())
    bad_table = pa.table(data)

    with pytest.raises(OutputError, match="schema mismatch"):
        write_parquet(bad_table, "my-bucket", "auck", 2024, 150, "auck1500")


# ---------------------------------------------------------------------------
# 7. Verify Snappy compression is used by checking Parquet metadata
# ---------------------------------------------------------------------------

def test_rows_to_parquet_bytes_uses_snappy_compression():
    """Parquet output uses Snappy compression (verified via metadata)."""
    payload = rows_to_parquet_bytes([_sample_row()])
    # Read the Parquet file metadata
    pf = pq.ParquetFile(BytesIO(payload))
    metadata = pf.metadata
    # Check row group column chunk compression
    row_group = metadata.row_group(0)
    for i in range(row_group.num_columns):
        col = row_group.column(i)
        assert col.compression == "SNAPPY", (
            f"Column {col.path_in_schema} uses {col.compression}, expected SNAPPY"
        )


# ---------------------------------------------------------------------------
# Additional: build_output_key correctness
# ---------------------------------------------------------------------------

def test_build_output_key_format():
    """build_output_key produces the expected deterministic path."""
    key = build_output_key("auck", 2024, 150, "auck1500")
    assert key == "processed/station=auck/year=2024/doy=150/auck1500.parquet"


def test_build_output_key_pads_doy():
    """build_output_key zero-pads doy to 3 digits."""
    key = build_output_key("braz", 2023, 5, "braz0050")
    assert key == "processed/station=braz/year=2023/doy=005/braz0050.parquet"
