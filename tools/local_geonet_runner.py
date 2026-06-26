"""Local-only runner for GeoNet AUCK hourly sample processing."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from processor.calibration import run_calibration
from processor.logic import derive_output_key
from processor.nav import fetch_nav_file
from processor.parquet_io import rows_to_parquet_bytes
from tools.geonet_samples import download_auck_hourly_sample

GEONET_HOURLY_KEY_RE = re.compile(
    r"^gnss/rinexhourly/(?P<year>\d{4})/(?P<doy>\d{3})/(?P<filename>[^/]+)$"
)
LOCAL_DESTINATION_PREFIX = "processed/local-geonet"


def _parse_geonet_hourly_key(key: str) -> tuple[int, int, str, str]:
    match = GEONET_HOURLY_KEY_RE.match(key)
    if not match:
        raise ValueError(f"Unsupported GeoNet hourly key: {key}")

    year = int(match.group("year"))
    doy = int(match.group("doy"))
    if doy < 1 or doy > 366:
        raise ValueError(f"Invalid DOY in GeoNet key: {doy}")

    filename = match.group("filename")
    source_stem = filename.removesuffix(".gz").rsplit(".", 1)[0]
    station = source_stem[:4]
    if len(station) != 4 or not station.isalpha():
        raise ValueError(f"Invalid station extracted from GeoNet filename: {filename}")

    return year, doy, station.lower(), source_stem


def run_local_auck_sample(output_dir: Path, *, nav_day_offset: int = 1) -> Path:
    """Download one AUCK sample, calibrate it, and write local Parquet output."""

    sample = download_auck_hourly_sample(output_dir / "input")
    year, doy, station, source_stem = _parse_geonet_hourly_key(sample.key)

    parameters: dict[str, Any] = {
        "NAV_DAY_OFFSET": nav_day_offset,
        "SAVE_PARQUET": True,
        "SAVE_CSV": False,
        "SAVE_STATIC_PLOTS": False,
        "SAVE_INTERACTIVE_PLOTS": False,
    }

    nav_path = fetch_nav_file(year, doy, nav_day_offset=nav_day_offset)
    rows = run_calibration(sample.local_path, nav_path, station, parameters)
    parquet_bytes = rows_to_parquet_bytes(rows)

    output_key = derive_output_key(station, year, doy, source_stem, LOCAL_DESTINATION_PREFIX)
    output_path = output_dir / output_key
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(parquet_bytes)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run local end-to-end processing against an AUCK GeoNet sample."
    )
    parser.add_argument(
        "--output-dir",
        default=".tmp/local-geonet-run",
        help="Directory for downloaded sample and generated Parquet output.",
    )
    parser.add_argument(
        "--nav-day-offset",
        type=int,
        default=1,
        help="Navigation day offset used for BKG fetch (default: 1).",
    )
    args = parser.parse_args()

    parquet_path = run_local_auck_sample(
        Path(args.output_dir),
        nav_day_offset=args.nav_day_offset,
    )
    print(f"wrote parquet: {parquet_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wrapper
    raise SystemExit(main())
