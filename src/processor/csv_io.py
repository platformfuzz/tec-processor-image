"""CSV encoding for calibrated TEC rows."""

from __future__ import annotations

from io import StringIO
from typing import Any

from .parquet_io import PARQUET_COLUMNS


def rows_to_csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    """Serialize contract rows to UTF-8 CSV bytes.

    Uses Polars to produce a correctly-typed CSV that matches the
    11-column output contract (same column order as Parquet output).
    """
    try:
        import polars as pl
    except Exception as exc:
        raise RuntimeError("polars is required to write CSV output") from exc

    if not rows:
        header = ",".join(PARQUET_COLUMNS)
        return (header + "\n").encode("utf-8")

    df = pl.from_dicts(rows).select(list(PARQUET_COLUMNS))
    buf = StringIO()
    df.write_csv(buf)
    return buf.getvalue().encode("utf-8")
