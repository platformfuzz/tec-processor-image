<<<<<<< HEAD
"""Navigation file acquisition using PyTECGg downloader utilities."""
=======
"""Navigation file resolution using PyTECGg download utilities."""
>>>>>>> 4fab63a (fix: delegate nav download to pytecgg)

from __future__ import annotations

import re
<<<<<<< HEAD
=======
import shutil
>>>>>>> 4fab63a (fix: delegate nav download to pytecgg)
from datetime import date
from pathlib import Path
from typing import Callable

from processor import NavFetchError

<<<<<<< HEAD
BKG_BRDC_BASE_URL = "https://igs.bkg.bund.de/root_ftp/IGS/BRDC"


=======
>>>>>>> 4fab63a (fix: delegate nav download to pytecgg)
# Keep NavDownloadError as an alias for backward compatibility
NavDownloadError = NavFetchError


def _days_in_year(year: int) -> int:
    """Return number of days in a given year."""
    jan_1 = date(year, 1, 1)
    next_jan_1 = date(year + 1, 1, 1)
    return (next_jan_1 - jan_1).days


def compute_nav_doy(year: int, doy: int, offset: int) -> tuple[int, int]:
    """
    Compute navigation (year, doy) from observation date and offset.

    Rolls back to previous year when result < 1.

    Args:
        year: Observation year.
        doy: Observation day of year (1-366).
        offset: Number of days before observation DOY to fetch nav data.

    Returns:
        Tuple of (nav_year, nav_doy).
    """
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


<<<<<<< HEAD
def _require_pytecgg_nav_downloader() -> Callable[[int, list[int], Path], None]:
    try:
        from pytecgg.utils.download_rinex import download_nav_bkg
    except Exception as exc:  # pragma: no cover - import guard
        raise NavFetchError("PyTECGg nav downloader is unavailable") from exc
    return download_nav_bkg
=======
def _pytecgg_download_nav_bkg(year: int, doys: list[int], output_dir: Path) -> None:
    from pytecgg.utils.download_rinex import download_nav_bkg

    download_nav_bkg(year, doys, output_dir)
>>>>>>> 4fab63a (fix: delegate nav download to pytecgg)


def _choose_downloaded_nav_file(output_dir: Path, year: int, doy: int) -> Path:
    files = [p for p in output_dir.iterdir() if p.is_file()]
    if not files:
        raise NavFetchError(f"No navigation file downloaded for {year}/DOY {doy:03d}")

    names = [p.name for p in files]
    chosen_name = select_bkg_nav_filename(names, year, doy)
    if chosen_name:
        return output_dir / chosen_name

    # Fallback: pick latest modified file if filename patterns differ upstream.
    return max(files, key=lambda p: p.stat().st_mtime)


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
    """
    Fetch BKG BRDC navigation file for given observation date via PyTECGg.

    Args:
        year: Observation year.
        doy: Observation day of year (1-366).
        nav_day_offset: Days before observation DOY to fetch nav data (default 1).
        timeout_list: Kept for backward compatibility (unused).
        timeout_download: Kept for backward compatibility (unused).
        output_dir: Optional destination directory for nav download cache.

    Returns:
        Path to downloaded navigation file in /tmp.

    Raises:
        NavFetchError: on HTTP error, timeout, or no compatible file found.
    """
<<<<<<< HEAD
    _ = timeout_list
    _ = timeout_download
    nav_year, nav_doy = compute_nav_doy(year, doy, nav_day_offset)
    target_dir = output_dir or Path("/tmp")
    return _download_nav_for_day(nav_year, nav_doy, target_dir)
=======
    del timeout_list, timeout_download
    nav_year, nav_doy = compute_nav_doy(year, doy, nav_day_offset)
    return download_nav_file(nav_year, nav_doy, Path("/tmp"))
>>>>>>> 4fab63a (fix: delegate nav download to pytecgg)


def download_nav_file(nav_year: int, nav_doy: int, output_dir: Path) -> Path:
    """
    Download navigation RINEX for nav_year/nav_doy into output_dir and return local path.

    This is the legacy interface kept for backward compatibility with handler.py.
    """
<<<<<<< HEAD
    return _download_nav_for_day(nav_year, nav_doy, output_dir)
=======
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        _pytecgg_download_nav_bkg(nav_year, [nav_doy], output_dir)
    except Exception as exc:
        raise NavFetchError(
            f"PyTECGg nav download failed for {nav_year}/DOY {nav_doy:03d}: {exc}"
        ) from exc

    available = [file.name for file in output_dir.glob("*.gz")]
    filename = select_bkg_nav_filename(available, nav_year, nav_doy)
    if not filename:
        raise NavFetchError(f"No compatible BKG navigation file for {nav_year}/DOY {nav_doy:03d}")

    dest_gz = output_dir / filename
    if not dest_gz.exists() or dest_gz.stat().st_size == 0:
        raise NavFetchError(f"PyTECGg did not produce a usable nav file for {nav_year}/DOY {nav_doy:03d}")

    return _decompress_if_needed(dest_gz)
>>>>>>> 4fab63a (fix: delegate nav download to pytecgg)
