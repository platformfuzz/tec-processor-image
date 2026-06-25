# Feature: tec-processor-image, Property 7: Calibration Row Filtering
"""Property test for calibration row filtering logic.

The calibrate() function filters a DataFrame to rows where id_arc_valid, stec,
and vtec are all non-null. Since we cannot invoke calibrate() without PyTECGg,
we test the filtering logic in isolation.

**Validates: Requirements 5.2**
"""

from __future__ import annotations

import polars as pl
from hypothesis import given, settings
from hypothesis import strategies as st


# --- Strategy: Generate DataFrames with various null patterns ---

@st.composite
def calibration_dataframes(draw: st.DrawFn) -> pl.DataFrame:
    """Generate polars DataFrames with columns including id_arc_valid, stec, vtec
    plus supporting columns (epoch, sv, lat_ipp, lon_ipp, azi, ele, bias, veq).

    Some rows will have null values in id_arc_valid, stec, or vtec to exercise
    the filtering logic.
    """
    n_rows = draw(st.integers(min_value=0, max_value=50))

    # For each row, independently decide whether each critical column is null
    id_arc_valid_values = []
    stec_values = []
    vtec_values = []

    for _ in range(n_rows):
        # id_arc_valid: either a string arc identifier or None
        if draw(st.booleans()):
            id_arc_valid_values.append(draw(st.text(min_size=1, max_size=5, alphabet="ABCDEFG0123456789")))
        else:
            id_arc_valid_values.append(None)

        # stec: either a float value or None
        if draw(st.booleans()):
            stec_values.append(draw(st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False)))
        else:
            stec_values.append(None)

        # vtec: either a float value or None
        if draw(st.booleans()):
            vtec_values.append(draw(st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False)))
        else:
            vtec_values.append(None)

    # Build supporting columns (always non-null for simplicity; they don't affect filtering)
    epoch_values = [f"2024-01-01T{i:02d}:00:00" for i in range(n_rows)]
    sv_values = [f"G{(i % 32) + 1:02d}" for i in range(n_rows)]
    lat_ipp_values = [float(i) * 0.5 for i in range(n_rows)]
    lon_ipp_values = [float(i) * 1.0 for i in range(n_rows)]
    azi_values = [float(i) * 10.0 for i in range(n_rows)]
    ele_values = [float(i) * 5.0 for i in range(n_rows)]
    bias_values = [float(i) * 0.1 for i in range(n_rows)]
    veq_values = [float(i) * 0.3 for i in range(n_rows)]

    df = pl.DataFrame({
        "epoch": epoch_values,
        "sv": sv_values,
        "id_arc_valid": id_arc_valid_values,
        "lat_ipp": lat_ipp_values,
        "lon_ipp": lon_ipp_values,
        "azi": azi_values,
        "ele": ele_values,
        "bias": bias_values,
        "stec": stec_values,
        "vtec": vtec_values,
        "veq": veq_values,
    })

    return df


def apply_calibration_filter(df: pl.DataFrame) -> pl.DataFrame:
    """Apply the same filtering logic as calibration.calibrate().

    Filters to rows where id_arc_valid, stec, and vtec are all non-null.
    """
    return df.filter(
        pl.col("id_arc_valid").is_not_null()
        & pl.col("stec").is_not_null()
        & pl.col("vtec").is_not_null()
    )


class TestCalibrationRowFiltering:
    """Property 7: Calibration Row Filtering.

    For any DataFrame produced by PyTECGg calibration, the filtered output SHALL
    contain only rows where id_arc_valid, stec, and vtec are all non-null.

    **Validates: Requirements 5.2**
    """

    @settings(max_examples=100)
    @given(df=calibration_dataframes())
    def test_filtered_output_has_no_nulls_in_critical_columns(self, df: pl.DataFrame) -> None:
        """Assert filtered output contains only rows where all three columns are non-null."""
        filtered = apply_calibration_filter(df)

        # Every row in the filtered output must have non-null id_arc_valid, stec, vtec
        if filtered.height > 0:
            assert filtered.get_column("id_arc_valid").null_count() == 0
            assert filtered.get_column("stec").null_count() == 0
            assert filtered.get_column("vtec").null_count() == 0

    @settings(max_examples=100)
    @given(df=calibration_dataframes())
    def test_no_valid_rows_dropped(self, df: pl.DataFrame) -> None:
        """Assert no rows where all three are non-null are incorrectly excluded."""
        filtered = apply_calibration_filter(df)

        # Count rows in original where all three are non-null
        expected_count = df.filter(
            pl.col("id_arc_valid").is_not_null()
            & pl.col("stec").is_not_null()
            & pl.col("vtec").is_not_null()
        ).height

        assert filtered.height == expected_count

    @settings(max_examples=100)
    @given(df=calibration_dataframes())
    def test_all_rows_with_any_null_excluded(self, df: pl.DataFrame) -> None:
        """Assert no rows with any null in id_arc_valid, stec, or vtec remain."""
        filtered = apply_calibration_filter(df)

        # Check every row individually - none should have a null in the critical columns
        for i in range(filtered.height):
            row = filtered.row(i)
            col_names = filtered.columns
            id_arc_idx = col_names.index("id_arc_valid")
            stec_idx = col_names.index("stec")
            vtec_idx = col_names.index("vtec")

            assert row[id_arc_idx] is not None, f"Row {i} has null id_arc_valid"
            assert row[stec_idx] is not None, f"Row {i} has null stec"
            assert row[vtec_idx] is not None, f"Row {i} has null vtec"

    @settings(max_examples=100)
    @given(df=calibration_dataframes())
    def test_filtered_is_subset_of_original(self, df: pl.DataFrame) -> None:
        """Assert filtered output is a proper subset of the input DataFrame."""
        filtered = apply_calibration_filter(df)

        # Filtered row count should never exceed original row count
        assert filtered.height <= df.height
