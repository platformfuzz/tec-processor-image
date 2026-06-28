"""Navigation file acquisition using PyTECGg downloader utilities."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Callable

from processor import NavFetchError

# Keep NavDownloadError as an alias for backward compatibility.
NavDownloadError = NavFetchError


def _days_in_year(year: int) -> int:
    """Return number of days in a given year."""
    jan_1 = date(year, 1, 1)
    next_jan_1 = date(year + 1, 1, 1)
    return (next_jan_1 - jan_1).days


def compute_nav_doy(year: int, doy: int, offset: int) -> tuple[int, int]:
    """Compute navigation (year, doy) from observation date and offset."""
    if offset <= 0:
        raise ValueError("NAV_DAY_OFFSET must be positive")
    if doy < 1 or doy > 366:
        raise ValueError("observation_doy must be between 1 and 366")

    nav_doy = doy - offset
    nav_year = year
    while nav_doy < 1:
        nav_year -= 1
        nav_doy += _days_in_year(nav_year)
    return nav_year, nav_doy


def select_bkg_nav_filename(available_files: list[str], year: int, doy: int) -> str | None:
    """Pick the best BRDC navigation file for a given year/DOY from local files."""
    doy_str = f"{doy:03d}"
    yy = str(year)[-2:]
    patterns = [
        r"^BRDC00IGS_R_.*_MN\.rnx\.gz$",
        r"^BRDC00.*_R_.*_MN\.rnx\.gz$",
        rf"^brdc{doy_str}0\.{yy}p\.gz$",
        r"^BRDC00IGS_R_.*_MN\.rnx$",
        r"^BRDC00.*_R_.*_MN\.rnx$",
        rf"^brdc{doy_str}0\.{yy}p$",
    ]
    for pattern in patterns:
        for filename in available_files:
            if re.match(pattern, filename):
                return filename
    return None


def _require_pytecgg_nav_downloader() -> Callable[[int, list[int], Path], None]:
    try:
        from pytecgg.utils.download_rinex import download_nav_bkg
    except Exception as exc:  # pragma: no cover - import guard
        raise NavFetchError("PyTECGg nav downloader is unavailable") from exc
    return download_nav_bkg


def _choose_downloaded_nav_file(output_dir: Path, year: int, doy: int) -> Path:
    files = [path for path in output_dir.iterdir() if path.is_file()]
    if not files:
        raise NavFetchError(f"No navigation file downloaded for {year}/DOY {doy:03d}")

    names = [path.name for path in files]
    chosen_name = select_bkg_nav_filename(names, year, doy)
    if chosen_name:
        return output_dir / chosen_name

    # Fallback when upstream filename patterns differ.
    return max(files, key=lambda path: path.stat().st_mtime)


def _download_nav_for_day(nav_year: int, nav_doy: int, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    downloader = _require_pytecgg_nav_downloader()
    try:
        downloader(nav_year, [nav_doy], output_dir)
    except Exception as exc:
        raise NavFetchError(f"PyTECGg NAV download failed for {nav_year}/DOY {nav_doy:03d}") from exc

    return _choose_downloaded_nav_file(output_dir, nav_year, nav_doy)


def fetch_nav_file(
    year: int,
    doy: int,
    nav_day_offset: int = 1,
    timeout_list: float = 30.0,
    timeout_download: float = 120.0,
    output_dir: Path | None = None,
) -> Path:
    """Fetch BKG BRDC navigation file for given observation date via PyTECGg."""
    _ = timeout_list
    _ = timeout_download
    nav_year, nav_doy = compute_nav_doy(year, doy, nav_day_offset)
    target_dir = output_dir or Path("/tmp")
    return _download_nav_for_day(nav_year, nav_doy, target_dir)


def download_nav_file(nav_year: int, nav_doy: int, output_dir: Path) -> Path:
    """Legacy interface kept for backward compatibility with handler.py."""
    return _download_nav_for_day(nav_year, nav_doy, output_dir)
