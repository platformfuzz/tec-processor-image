"""Download BKG navigation RINEX for calibration."""

from __future__ import annotations

import gzip
import re
import shutil
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from processor import NavFetchError

BKG_BRDC_BASE_URL = "https://igs.bkg.bund.de/root_ftp/IGS/BRDC"
USER_AGENT = "event-driven-serverless-platform-processor/1.0"


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


def _list_bkg_nav_files(year: int, doy: int, timeout: float = 30.0) -> list[str]:
    """List navigation files from BKG directory for given year/doy."""
    doy_str = f"{doy:03d}"
    dir_url = f"{BKG_BRDC_BASE_URL}/{year}/{doy_str}/"
    request = urllib.request.Request(dir_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise NavFetchError(f"BKG directory not accessible: {dir_url} ({exc.code})") from exc
    except urllib.error.URLError as exc:
        raise NavFetchError(f"BKG directory request failed: {dir_url} ({exc.reason})") from exc
    except TimeoutError as exc:
        raise NavFetchError(f"BKG directory listing timed out: {dir_url}") from exc

    return re.findall(r'href="([^"/]+\.gz)"', html)


def select_bkg_nav_filename(available_files: list[str], year: int, doy: int) -> str | None:
    """Pick the best BRDC navigation file for a given year/DOY."""
    doy_str = f"{doy:03d}"
    yy = str(year)[-2:]
    patterns = [
        r"^BRDC00IGS_R_.*_MN\.rnx\.gz$",
        r"^BRDC00.*_R_.*_MN\.rnx\.gz$",
        rf"^brdc{doy_str}0\.{yy}p\.gz$",
    ]
    for pattern in patterns:
        for filename in available_files:
            if re.match(pattern, filename):
                return filename
    return None


def _download_file(url: str, dest: Path, timeout: float = 120.0) -> None:
    """Download a file from url to dest with timeout enforcement."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp_path = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, tmp_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        tmp_path.replace(dest)
    except urllib.error.HTTPError as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise NavFetchError(f"BKG navigation download failed: {url} ({exc.code})") from exc
    except urllib.error.URLError as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise NavFetchError(f"BKG navigation download failed: {url} ({exc.reason})") from exc
    except TimeoutError as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise NavFetchError(f"BKG navigation download timed out: {url}") from exc


def _decompress_if_needed(path: Path) -> Path:
    """Decompress .gz file if needed, returning path to uncompressed file."""
    if not path.name.endswith(".gz"):
        return path
    dest = path.with_suffix("")
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    with gzip.open(path, "rb") as src, dest.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    return dest


def fetch_nav_file(
    year: int,
    doy: int,
    nav_day_offset: int = 1,
    timeout_list: float = 30.0,
    timeout_download: float = 120.0,
) -> Path:
    """
    Fetch BKG BRDC navigation file for given observation date.

    Computes the navigation year/doy by subtracting nav_day_offset from the
    observation doy (with year rollback), fetches the BKG directory listing,
    selects a compatible navigation file, and downloads it to /tmp.

    Args:
        year: Observation year.
        doy: Observation day of year (1-366).
        nav_day_offset: Days before observation DOY to fetch nav data (default 1).
        timeout_list: HTTP timeout in seconds for directory listing (default 30).
        timeout_download: HTTP timeout in seconds for file download (default 120).

    Returns:
        Path to downloaded navigation file in /tmp.

    Raises:
        NavFetchError: on HTTP error, timeout, or no compatible file found.
    """
    nav_year, nav_doy = compute_nav_doy(year, doy, nav_day_offset)

    # List available files from BKG directory
    available = _list_bkg_nav_files(nav_year, nav_doy, timeout=timeout_list)

    # Select the best compatible nav filename
    filename = select_bkg_nav_filename(available, nav_year, nav_doy)
    if not filename:
        raise NavFetchError(
            f"No compatible BKG navigation file for {nav_year}/DOY {nav_doy:03d}"
        )

    # Download to /tmp
    output_dir = Path("/tmp")
    output_dir.mkdir(parents=True, exist_ok=True)
    dest_gz = output_dir / filename

    # Skip download if file already exists with content
    if dest_gz.exists() and dest_gz.stat().st_size > 0:
        return _decompress_if_needed(dest_gz)

    url = f"{BKG_BRDC_BASE_URL}/{nav_year}/{nav_doy:03d}/{filename}"
    _download_file(url, dest_gz, timeout=timeout_download)

    return _decompress_if_needed(dest_gz)


def download_nav_file(nav_year: int, nav_doy: int, output_dir: Path) -> Path:
    """
    Download navigation RINEX for nav_year/nav_doy into output_dir and return local path.

    This is the legacy interface kept for backward compatibility with handler.py.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    available = _list_bkg_nav_files(nav_year, nav_doy)
    filename = select_bkg_nav_filename(available, nav_year, nav_doy)
    if not filename:
        raise NavFetchError(f"No compatible BKG navigation file for {nav_year}/DOY {nav_doy:03d}")

    dest_gz = output_dir / filename
    if dest_gz.exists() and dest_gz.stat().st_size > 0:
        return _decompress_if_needed(dest_gz)

    url = f"{BKG_BRDC_BASE_URL}/{nav_year}/{nav_doy:03d}/{filename}"
    _download_file(url, dest_gz)

    return _decompress_if_needed(dest_gz)
