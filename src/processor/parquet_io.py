"""Parquet encoding for calibrated TEC rows."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

try:
    import boto3
except Exception:  # pragma: no cover - Lambda runtime provides boto3
    boto3 = None  # type: ignore[assignment]

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except Exception:  # pragma: no cover - import guard for lightweight test envs
    pa = None  # type: ignore[assignment]
    pq = None  # type: ignore[assignment]

from . import OutputError

PARQUET_COLUMNS = (
    "epoch",
    "sv",
    "id_arc",
    "lat_ipp",
    "lon_ipp",
    "azi",
    "ele",
    "bias",
    "stec",
    "vtec",
    "veq",
)

OUTPUT_COLUMNS = [
    "epoch",
    "sv",
    "id_arc",
    "lat_ipp",
    "lon_ipp",
    "azi",
    "ele",
    "bias",
    "stec",
    "vtec",
    "veq",
]


_EPOCH_ISO_RE = re.compile(
    r"^(?P<year>\d{1,4})-(?P<month>\d{2})-(?P<day>\d{2})T(?P<time>.+)$"
)


def _normalize_epoch(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        match = _EPOCH_ISO_RE.match(text)
        if match:
            year = int(match.group("year"))
            text = (
                f"{year:04d}-{match.group('month')}-{match.group('day')}"
                f"T{match.group('time')}"
            )
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    raise TypeError(f"Unsupported epoch type: {type(value).__name__}")


def rows_to_parquet_bytes(rows: list[dict[str, Any]]) -> bytes:
    """Serialize contract rows to Apache Parquet bytes."""
    if pa is None or pq is None:
        raise RuntimeError("pyarrow is required to write parquet output")

    if not rows:
        table = pa.table({column: pa.array([], type=_column_type(column)) for column in PARQUET_COLUMNS})
    else:
        columns: dict[str, list[Any]] = {column: [] for column in PARQUET_COLUMNS}
        for row in rows:
            for column in PARQUET_COLUMNS:
                value = row.get(column)
                if column == "epoch":
                    value = _normalize_epoch(value)
                columns[column].append(value)
        table = pa.table(
            {
                "epoch": pa.array(columns["epoch"], type=pa.timestamp("us", tz="UTC")),
                "sv": pa.array(columns["sv"], type=pa.string()),
                "id_arc": pa.array(columns["id_arc"], type=pa.int32()),
                "lat_ipp": pa.array(columns["lat_ipp"], type=pa.float64()),
                "lon_ipp": pa.array(columns["lon_ipp"], type=pa.float64()),
                "azi": pa.array(columns["azi"], type=pa.float64()),
                "ele": pa.array(columns["ele"], type=pa.float64()),
                "bias": pa.array(columns["bias"], type=pa.float64()),
                "stec": pa.array(columns["stec"], type=pa.float64()),
                "vtec": pa.array(columns["vtec"], type=pa.float64()),
                "veq": pa.array(columns["veq"], type=pa.float64()),
            }
        )

    buffer = BytesIO()
    pq.write_table(table, buffer, compression="snappy")
    return buffer.getvalue()


def _column_type(column: str) -> pa.DataType:
    if column == "epoch":
        return pa.timestamp("us", tz="UTC")
    if column == "sv":
        return pa.string()
    if column == "id_arc":
        return pa.int32()
    return pa.float64()


def build_output_key(station: str, year: int, doy: int, source_stem: str) -> str:
    """Deterministic output key construction.

    Returns:
        S3 key in format: processed/station={station}/year={year}/doy={doy:03d}/{source_stem}.parquet
    """
    return f"processed/station={station}/year={year}/doy={doy:03d}/{source_stem}.parquet"


def write_parquet(
    df: Any,
    bucket: str,
    station: str,
    year: int,
    doy: int,
    source_stem: str,
) -> str:
    """Write DataFrame as Snappy-compressed Parquet to S3.

    Validates that the DataFrame columns match OUTPUT_COLUMNS, serializes to
    Parquet bytes with Snappy compression, and writes to S3 via put_object.

    Args:
        df: pandas DataFrame with calibrated TEC data
        bucket: S3 bucket name
        station: Station identifier (lowercase)
        year: Observation year
        doy: Observation day of year
        source_stem: Filename without extension

    Returns:
        The S3 key written.

    Raises:
        OutputError: on schema mismatch or S3 put failure.
    """
    if pa is None or pq is None:
        raise OutputError("pyarrow is required to write parquet output")

    # Validate DataFrame schema matches expected columns
    if isinstance(df, pa.Table):
        df_columns = df.column_names
    else:
        df_columns = list(df.columns)
    if df_columns != OUTPUT_COLUMNS:
        raise OutputError(
            f"DataFrame schema mismatch: expected {OUTPUT_COLUMNS}, got {df_columns}"
        )

    # Convert DataFrame to pyarrow Table and write as Parquet bytes
    try:
        if isinstance(df, pa.Table):
            table = df
        elif hasattr(df, "to_arrow"):
            # polars DataFrame
            table = df.to_arrow()
        else:
            # pandas DataFrame or compatible
            table = pa.Table.from_pandas(df)
    except Exception as exc:
        raise OutputError(f"Failed to convert DataFrame to Arrow table: {exc}") from exc

    buffer = BytesIO()
    pq.write_table(table, buffer, compression="snappy")
    parquet_bytes = buffer.getvalue()

    # Build deterministic output key
    output_key = build_output_key(station, year, doy, source_stem)

    # Write to S3 (overwrites existing key for idempotent reprocessing)
    if boto3 is None:
        raise OutputError("boto3 is required for S3 operations")
    try:
        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=bucket,
            Key=output_key,
            Body=parquet_bytes,
        )
    except Exception as exc:
        raise OutputError(f"S3 put_object failed for key '{output_key}': {exc}") from exc

    return output_key
