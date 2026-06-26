"""PyTECGg calibration pipeline for a single observation file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import CalibrationError
from .parquet_io import PARQUET_COLUMNS


def _require_pytecgg() -> dict[str, Any]:
    """Import PyTECGg components or raise CalibrationError."""
    try:
        from pytecgg.context import GNSSContext
        from pytecgg.linear_combinations import calculate_linear_combinations
        from pytecgg.parsing import read_rinex_nav, read_rinex_obs
        from pytecgg.satellites import calculate_ipp, prepare_ephemeris, satellite_coordinates
        from pytecgg.tec_calibration import calculate_tec, calculate_vertical_equivalent, extract_arcs
    except Exception as exc:
        raise CalibrationError(
            "PyTECGg is required for calibration; this repository targets Python 3.13 runtime compatibility."
        ) from exc
    return {
        "GNSSContext": GNSSContext,
        "calculate_linear_combinations": calculate_linear_combinations,
        "read_rinex_nav": read_rinex_nav,
        "read_rinex_obs": read_rinex_obs,
        "calculate_ipp": calculate_ipp,
        "prepare_ephemeris": prepare_ephemeris,
        "satellite_coordinates": satellite_coordinates,
        "calculate_tec": calculate_tec,
        "calculate_vertical_equivalent": calculate_vertical_equivalent,
        "extract_arcs": extract_arcs,
    }


def calibrate(obs_path: Path, nav_path: Path):
    """
    Run PyTECGg calibration pipeline on RINEX observation + navigation files.

    Returns:
        DataFrame with columns [epoch, sv, id_arc, lat_ipp, lon_ipp,
        azi, ele, bias, stec, vtec, veq] or None if no valid rows.

    Raises:
        CalibrationError: if PyTECGg is not importable or calibration crashes
    """
    try:
        import polars as pl
    except Exception as exc:
        raise CalibrationError("polars is required for calibration data processing") from exc

    libs = _require_pytecgg()

    try:
        # Step 1: Read RINEX observation file
        obs_df, receiver_pos, rinex_version = libs["read_rinex_obs"](obs_path)
        if obs_df.is_empty():
            raise CalibrationError(f"No observations found in {obs_path.name}")

        # Step 2: Read RINEX navigation file
        nav = libs["read_rinex_nav"](nav_path)
        if not nav:
            raise CalibrationError(f"No navigation data found in {nav_path.name}")

        # Step 3: Create GNSS context
        ctx = libs["GNSSContext"](
            receiver_pos=receiver_pos,
            rinex_version=rinex_version,
            systems=["GPS", "GLONASS", "GALILEO", "BEIDOU"],
        )

        # Step 4: Prepare ephemeris
        ephem = libs["prepare_ephemeris"](nav, ctx)
        if not ephem:
            raise CalibrationError("Navigation ephemeris could not be prepared for calibration")

        # Step 5: Calculate linear combinations
        df_lc = libs["calculate_linear_combinations"](obs_df, ctx)
        if df_lc.is_empty():
            raise CalibrationError("Linear combinations could not be calculated from observations")

        # Step 6: Calculate satellite coordinates
        sat_coords = libs["satellite_coordinates"](df_lc["sv"], df_lc["epoch"], ephem)
        if sat_coords.is_empty():
            raise CalibrationError("Satellite coordinates could not be computed from navigation data")

        # Step 7: Join observation data with satellite coordinates
        df = df_lc.join(sat_coords, on=["sv", "epoch"], how="inner")
        if df.is_empty():
            raise CalibrationError("Observation and navigation timelines could not be aligned")

        # Step 8: Calculate ionospheric pierce point (IPP)
        df = libs["calculate_ipp"](df, ctx)

        # Step 9: Extract arcs
        df = libs["extract_arcs"](df, ctx)

        # Step 10: Calculate TEC
        df = libs["calculate_tec"](df, ctx)

        # Step 11: Calculate vertical equivalent
        df = libs["calculate_vertical_equivalent"](df, ctx)

    except CalibrationError:
        raise
    except Exception as exc:
        raise CalibrationError(f"Calibration pipeline failed: {exc}") from exc

    # Filter to rows with non-null id_arc_valid, stec, and vtec
    required_cols = {"id_arc_valid", "stec", "vtec"}
    missing = required_cols - set(df.columns)
    if missing:
        raise CalibrationError(f"Calibration output missing columns: {', '.join(sorted(missing))}")

    filtered = df.filter(
        pl.col("id_arc_valid").is_not_null()
        & pl.col("stec").is_not_null()
        & pl.col("vtec").is_not_null()
    )

    if filtered.is_empty():
        return None

    # Map id_arc_valid to integer id_arc
    arc_values = filtered.get_column("id_arc_valid").to_list()
    arc_map: dict[str, int] = {}
    next_id = 1
    for val in arc_values:
        key = str(val)
        if key not in arc_map:
            arc_map[key] = next_id
            next_id += 1

    arc_ids = [arc_map[str(v)] for v in arc_values]
    filtered = filtered.with_columns(pl.Series("id_arc", arc_ids, dtype=pl.Int64))

    # Handle veq column: use vtec as fallback if veq is missing or null
    if "veq" not in filtered.columns:
        filtered = filtered.with_columns(pl.col("vtec").alias("veq"))
    else:
        filtered = filtered.with_columns(
            pl.when(pl.col("veq").is_null()).then(pl.col("vtec")).otherwise(pl.col("veq")).alias("veq")
        )

    # Select exactly the 11 output columns
    output_columns = list(PARQUET_COLUMNS)
    available = set(filtered.columns)
    missing_output = set(output_columns) - available
    if missing_output:
        raise CalibrationError(f"Calibration output missing required columns: {', '.join(sorted(missing_output))}")

    result = filtered.select(output_columns)

    if result.is_empty():
        return None

    return result


def _arc_id_map(values: list[str | None]) -> dict[str, int]:
    """Build a mapping from arc identifier strings to sequential integers."""
    mapping: dict[str, int] = {}
    next_id = 1
    for value in values:
        if value is None:
            continue
        if value not in mapping:
            mapping[value] = next_id
            next_id += 1
    return mapping


def _rows_from_dataframe(df: Any) -> list[dict[str, Any]]:
    """Convert a polars DataFrame (PyTECGg output) to contract rows."""
    import polars as pl

    required = {"epoch", "sv", "id_arc_valid", "lat_ipp", "lon_ipp", "azi", "ele", "bias", "stec", "vtec", "veq"}
    missing = required - set(df.columns)
    if missing:
        raise CalibrationError(f"Calibration output missing columns: {', '.join(sorted(missing))}")

    filtered = df.filter(
        pl.col("id_arc_valid").is_not_null()
        & pl.col("stec").is_not_null()
        & pl.col("vtec").is_not_null()
    )
    if filtered.is_empty():
        raise CalibrationError("Calibration produced no valid TEC rows")

    arc_ids = _arc_id_map(filtered.get_column("id_arc_valid").to_list())
    rows: list[dict[str, Any]] = []
    for record in filtered.to_dicts():
        arc_key = record.get("id_arc_valid")
        if arc_key is None:
            continue
        epoch = record["epoch"]
        if hasattr(epoch, "isoformat"):
            epoch_text = epoch.isoformat().replace("+00:00", "Z")
        else:
            epoch_text = str(epoch)

        veq = record.get("veq")
        if veq is None:
            veq = record["vtec"]

        rows.append(
            {
                "epoch": epoch_text,
                "sv": str(record["sv"]),
                "id_arc": int(arc_ids[str(arc_key)]),
                "lat_ipp": round(float(record["lat_ipp"]), 4),
                "lon_ipp": round(float(record["lon_ipp"]), 4),
                "azi": round(float(record["azi"]), 1),
                "ele": round(float(record["ele"]), 1),
                "bias": round(float(record["bias"]), 2),
                "stec": round(float(record["stec"]), 2),
                "vtec": round(float(record["vtec"]), 2),
                "veq": round(float(veq), 2),
            }
        )

    if not rows:
        raise CalibrationError("Calibration produced no publishable TEC rows")
    return rows


def run_calibration(
    observation_path: Path,
    navigation_path: Path,
    station: str,
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run PyTECGg calibration and return rows matching the platform Parquet contract."""
    libs = _require_pytecgg()
    gnss_context = libs["GNSSContext"]

    try:
        obs_df, receiver_pos, rinex_version = libs["read_rinex_obs"](observation_path)
        if obs_df.is_empty():
            raise CalibrationError(f"No observations found in {observation_path.name}")

        nav = libs["read_rinex_nav"](navigation_path)
        if not nav:
            raise CalibrationError(f"No navigation data found in {navigation_path.name}")

        ctx = gnss_context(
            receiver_pos=receiver_pos,
            receiver_name=station,
            rinex_version=rinex_version,
            systems=["GPS", "GLONASS", "GALILEO", "BEIDOU"],
            h_ipp=float(parameters.get("H_IPP", 350_000)),
        )
        ephem = libs["prepare_ephemeris"](nav, ctx)
        if not ephem:
            raise CalibrationError("Navigation ephemeris could not be prepared for calibration")

        df_lc = libs["calculate_linear_combinations"](obs_df, ctx)
        if df_lc.is_empty():
            raise CalibrationError("Linear combinations could not be calculated from observations")

        sat_coords = libs["satellite_coordinates"](df_lc["sv"], df_lc["epoch"], ephem)
        if sat_coords.is_empty():
            raise CalibrationError("Satellite coordinates could not be computed from navigation data")

        df = df_lc.join(sat_coords, on=["sv", "epoch"], how="inner")
        if df.is_empty():
            raise CalibrationError("Observation and navigation timelines could not be aligned")

        df = libs["calculate_ipp"](df, ctx)
        df = libs["extract_arcs"](df, ctx)
        df = libs["calculate_tec"](df, ctx)
        df = libs["calculate_vertical_equivalent"](df, ctx)

    except CalibrationError:
        raise
    except Exception as exc:
        raise CalibrationError(f"Calibration pipeline failed: {exc}") from exc

    rows = _rows_from_dataframe(df)
    for column in PARQUET_COLUMNS:
        if any(column not in row or row[column] is None for row in rows):
            raise CalibrationError(f"Calibration row missing required field: {column}")
    return rows
