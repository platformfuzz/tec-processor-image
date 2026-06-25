# Feature: tec-processor-image, Property 10: Parquet Schema Invariance
"""Property-based tests for Parquet I/O module.

Validates: Requirements 6.2
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

import pytest

pyarrow = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq  # noqa: E402

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from processor.parquet_io import OUTPUT_COLUMNS, rows_to_parquet_bytes  # noqa: E402

# --- Hypothesis strategies for calibration output rows ---

# GNSS observation epochs use 4-digit years in ISO 8601 UTC form.
_epoch_st = st.datetimes(
    min_value=datetime(1980, 1, 1),
    max_value=datetime(2099, 12, 31, 23, 59, 59),
).map(lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ"))

# Satellite vehicle IDs like G01, G32, R01, E05
_sv_st = st.sampled_from(["G", "R", "E", "C", "J"]).flatmap(
    lambda constellation: st.integers(min_value=1, max_value=32).map(
        lambda num: f"{constellation}{num:02d}"
    )
)

_id_arc_st = st.integers(min_value=0, max_value=10000)
_lat_ipp_st = st.floats(min_value=-90.0, max_value=90.0, allow_nan=False, allow_infinity=False)
_lon_ipp_st = st.floats(min_value=-180.0, max_value=180.0, allow_nan=False, allow_infinity=False)
_azi_st = st.floats(min_value=0.0, max_value=360.0, allow_nan=False, allow_infinity=False)
_ele_st = st.floats(min_value=0.0, max_value=90.0, allow_nan=False, allow_infinity=False)
_bias_st = st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False)
_stec_st = st.floats(min_value=-500.0, max_value=500.0, allow_nan=False, allow_infinity=False)
_vtec_st = st.floats(min_value=-500.0, max_value=500.0, allow_nan=False, allow_infinity=False)
_veq_st = st.floats(min_value=-500.0, max_value=500.0, allow_nan=False, allow_infinity=False)


@st.composite
def calibration_row(draw: st.DrawFn) -> dict:
    """Generate a single valid calibration output row with all 11 columns."""
    return {
        "epoch": draw(_epoch_st),
        "sv": draw(_sv_st),
        "id_arc": draw(_id_arc_st),
        "lat_ipp": draw(_lat_ipp_st),
        "lon_ipp": draw(_lon_ipp_st),
        "azi": draw(_azi_st),
        "ele": draw(_ele_st),
        "bias": draw(_bias_st),
        "stec": draw(_stec_st),
        "vtec": draw(_vtec_st),
        "veq": draw(_veq_st),
    }


calibration_rows_st = st.lists(calibration_row(), min_size=1, max_size=50)


# Feature: tec-processor-image, Property 10: Parquet Schema Invariance
class TestParquetSchemaInvariance:
    """Property 10: Parquet Schema Invariance.

    **Validates: Requirements 6.2**
    """

    @settings(max_examples=100)
    @given(rows=calibration_rows_st)
    def test_parquet_has_exactly_11_columns_matching_output_columns(self, rows: list[dict]) -> None:
        """Written Parquet contains exactly 11 specified columns matching OUTPUT_COLUMNS."""
        parquet_bytes = rows_to_parquet_bytes(rows)

        # Read the Parquet bytes back
        table = pq.read_table(BytesIO(parquet_bytes))

        # Assert exactly 11 columns
        assert len(table.column_names) == 11
        # Assert columns match OUTPUT_COLUMNS exactly (same names, same order)
        assert table.column_names == OUTPUT_COLUMNS

    @settings(max_examples=100)
    @given(rows=calibration_rows_st)
    def test_parquet_bytes_start_with_magic(self, rows: list[dict]) -> None:
        """Written Parquet bytes start with PAR1 magic bytes."""
        parquet_bytes = rows_to_parquet_bytes(rows)

        # Assert Parquet magic bytes
        assert parquet_bytes.startswith(b"PAR1"), (
            f"Expected Parquet magic bytes b'PAR1', got {parquet_bytes[:4]!r}"
        )
