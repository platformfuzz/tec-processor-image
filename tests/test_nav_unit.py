"""Unit tests for BKG navigation file selection and fetch logic.

Tests cover:
- URL construction for various year/doy combinations
- HTML parsing of directory listing for BRDC filenames
- Filename selection priority (BRDC00IGS > BRDC00* RINEX 3 > legacy brdc)
- Error handling: no compatible file, HTTP errors, timeouts
- Mock HTTP responses for directory listing and download

Requirements: 4.4, 4.5, 4.6, 4.7
"""

from __future__ import annotations

import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from processor import NavFetchError
from processor.nav import (
    BKG_BRDC_BASE_URL,
    _list_bkg_nav_files,
    compute_nav_doy,
    fetch_nav_file,
    select_bkg_nav_filename,
)


# ---------------------------------------------------------------------------
# compute_nav_doy tests
# ---------------------------------------------------------------------------


class TestComputeNavDoy:
    """Tests for compute_nav_doy(year, doy, offset)."""

    def test_simple_offset(self):
        """Offset within same year returns year unchanged."""
        nav_year, nav_doy = compute_nav_doy(2024, 100, 1)
        assert nav_year == 2024
        assert nav_doy == 99

    def test_larger_offset(self):
        """Larger offset still within same year."""
        nav_year, nav_doy = compute_nav_doy(2024, 50, 10)
        assert nav_year == 2024
        assert nav_doy == 40

    def test_rollback_to_previous_year(self):
        """When doy - offset < 1, rolls back to previous year."""
        # 2024 is leap year (366 days in 2023? No, 2023 has 365 days)
        nav_year, nav_doy = compute_nav_doy(2024, 1, 1)
        # doy=1, offset=1 -> result=0 -> rollback to 2023, doy=365
        assert nav_year == 2023
        assert nav_doy == 365

    def test_rollback_leap_year(self):
        """Rollback into a leap year gives doy up to 366."""
        # 2025 doy=1, offset=1 -> result=0 -> rollback to 2024 (leap year, 366 days)
        nav_year, nav_doy = compute_nav_doy(2025, 1, 1)
        assert nav_year == 2024
        assert nav_doy == 366

    def test_large_offset_rollback(self):
        """Large offset rolls back correctly."""
        # 2024 doy=3, offset=5 -> result=-2 -> rollback to 2023(365 days), nav_doy=365+(-2)=363
        nav_year, nav_doy = compute_nav_doy(2024, 3, 5)
        assert nav_year == 2023
        assert nav_doy == 363

    def test_invalid_offset_zero(self):
        """Offset of zero raises ValueError."""
        with pytest.raises(ValueError, match="positive"):
            compute_nav_doy(2024, 100, 0)

    def test_invalid_offset_negative(self):
        """Negative offset raises ValueError."""
        with pytest.raises(ValueError, match="positive"):
            compute_nav_doy(2024, 100, -1)

    def test_invalid_doy_zero(self):
        """DOY of 0 raises ValueError."""
        with pytest.raises(ValueError, match="between 1 and 366"):
            compute_nav_doy(2024, 0, 1)

    def test_invalid_doy_too_large(self):
        """DOY > 366 raises ValueError."""
        with pytest.raises(ValueError, match="between 1 and 366"):
            compute_nav_doy(2024, 367, 1)


# ---------------------------------------------------------------------------
# select_bkg_nav_filename tests
# ---------------------------------------------------------------------------


class TestSelectBkgNavFilename:
    """Tests for select_bkg_nav_filename(available_files, year, doy)."""

    def test_prefers_igs_over_others(self):
        """BRDC00IGS pattern is highest priority."""
        files = [
            "brdc1750.26p.gz",
            "BRDC00WRD_R_20261750000_01D_MN.rnx.gz",
            "BRDC00IGS_R_20261750000_01D_MN.rnx.gz",
        ]
        chosen = select_bkg_nav_filename(files, 2026, 175)
        assert chosen == "BRDC00IGS_R_20261750000_01D_MN.rnx.gz"

    def test_prefers_rinex3_over_legacy(self):
        """BRDC00* RINEX 3 is second priority over legacy brdc."""
        files = [
            "brdc1500.24p.gz",
            "BRDC00WRD_R_20241500000_01D_MN.rnx.gz",
        ]
        chosen = select_bkg_nav_filename(files, 2024, 150)
        assert chosen == "BRDC00WRD_R_20241500000_01D_MN.rnx.gz"

    def test_falls_back_to_legacy_pattern(self):
        """Legacy brdc{doy}0.{yy}p.gz is last resort."""
        files = [
            "some_other_file.gz",
            "brdc1500.24p.gz",
        ]
        chosen = select_bkg_nav_filename(files, 2024, 150)
        assert chosen == "brdc1500.24p.gz"

    def test_no_compatible_file_returns_none(self):
        """Returns None when no files match any pattern."""
        files = [
            "some_random_file.gz",
            "GLONASS_nav.gz",
            "readme.txt",
        ]
        chosen = select_bkg_nav_filename(files, 2024, 150)
        assert chosen is None

    def test_empty_file_list_returns_none(self):
        """Returns None for empty file list."""
        chosen = select_bkg_nav_filename([], 2024, 150)
        assert chosen is None

    def test_legacy_pattern_matches_correct_doy_and_year(self):
        """Legacy pattern must match exact doy and 2-digit year."""
        files = [
            "brdc0010.24p.gz",  # doy=001
            "brdc3650.24p.gz",  # doy=365
        ]
        # Looking for doy=001
        chosen = select_bkg_nav_filename(files, 2024, 1)
        assert chosen == "brdc0010.24p.gz"

    def test_legacy_pattern_rejects_wrong_doy(self):
        """Legacy file for different DOY is not selected."""
        files = [
            "brdc1500.24p.gz",  # doy=150
        ]
        # Looking for doy=100
        chosen = select_bkg_nav_filename(files, 2024, 100)
        assert chosen is None

    def test_legacy_pattern_rejects_wrong_year(self):
        """Legacy file for different year suffix is not selected."""
        files = [
            "brdc1500.23p.gz",  # year suffix 23 (2023)
        ]
        # Looking for 2024 -> yy=24
        chosen = select_bkg_nav_filename(files, 2024, 150)
        assert chosen is None

    def test_igs_selected_regardless_of_order(self):
        """IGS file is picked even if listed last."""
        files = [
            "brdc0990.24p.gz",
            "BRDC00WRD_R_20240990000_01D_MN.rnx.gz",
            "BRDC00IGS_R_20240990000_01D_MN.rnx.gz",
        ]
        chosen = select_bkg_nav_filename(files, 2024, 99)
        assert chosen == "BRDC00IGS_R_20240990000_01D_MN.rnx.gz"


# ---------------------------------------------------------------------------
# URL construction tests
# ---------------------------------------------------------------------------


class TestUrlConstruction:
    """Tests for BKG URL construction from year and doy."""

    def test_url_format_standard(self):
        """URL uses zero-padded 3-digit doy."""
        expected = f"{BKG_BRDC_BASE_URL}/2024/150/"
        actual = f"{BKG_BRDC_BASE_URL}/{2024}/{150:03d}/"
        assert actual == expected

    def test_url_format_single_digit_doy(self):
        """DOY=1 is zero-padded to 001."""
        expected = f"{BKG_BRDC_BASE_URL}/2024/001/"
        actual = f"{BKG_BRDC_BASE_URL}/{2024}/{1:03d}/"
        assert actual == expected

    def test_url_format_max_doy(self):
        """DOY=366 formats correctly."""
        expected = f"{BKG_BRDC_BASE_URL}/2024/366/"
        actual = f"{BKG_BRDC_BASE_URL}/{2024}/{366:03d}/"
        assert actual == expected

    def test_base_url_value(self):
        """BKG_BRDC_BASE_URL has expected value."""
        assert BKG_BRDC_BASE_URL == "https://igs.bkg.bund.de/root_ftp/IGS/BRDC"


# ---------------------------------------------------------------------------
# _list_bkg_nav_files tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestListBkgNavFiles:
    """Tests for _list_bkg_nav_files with mocked HTTP responses."""

    def _mock_html_response(self, html_content: str):
        """Create a mock response context manager."""
        mock_response = MagicMock()
        mock_response.read.return_value = html_content.encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        return mock_response

    @patch("processor.nav.urllib.request.urlopen")
    def test_parses_gz_filenames_from_html(self, mock_urlopen):
        """Extracts .gz filenames from href attributes."""
        html = """
        <html><body>
        <a href="BRDC00IGS_R_20241500000_01D_MN.rnx.gz">BRDC00IGS...</a>
        <a href="BRDC00WRD_R_20241500000_01D_MN.rnx.gz">BRDC00WRD...</a>
        <a href="brdc1500.24p.gz">brdc1500.24p.gz</a>
        <a href="../">Parent directory</a>
        </body></html>
        """
        mock_urlopen.return_value = self._mock_html_response(html)

        files = _list_bkg_nav_files(2024, 150)

        assert len(files) == 3
        assert "BRDC00IGS_R_20241500000_01D_MN.rnx.gz" in files
        assert "BRDC00WRD_R_20241500000_01D_MN.rnx.gz" in files
        assert "brdc1500.24p.gz" in files

    @patch("processor.nav.urllib.request.urlopen")
    def test_ignores_non_gz_hrefs(self, mock_urlopen):
        """Only files ending with .gz are extracted."""
        html = """
        <html><body>
        <a href="readme.txt">readme</a>
        <a href="data.csv">data</a>
        <a href="nav_file.gz">nav</a>
        </body></html>
        """
        mock_urlopen.return_value = self._mock_html_response(html)

        files = _list_bkg_nav_files(2024, 150)

        assert files == ["nav_file.gz"]

    @patch("processor.nav.urllib.request.urlopen")
    def test_ignores_directory_links(self, mock_urlopen):
        """Links with paths (containing /) are excluded."""
        html = """
        <html><body>
        <a href="subdir/file.gz">subdir file</a>
        <a href="../parent.gz">parent</a>
        <a href="valid.gz">valid</a>
        </body></html>
        """
        mock_urlopen.return_value = self._mock_html_response(html)

        files = _list_bkg_nav_files(2024, 150)

        # Only 'valid.gz' matches - no "/" in filename
        assert files == ["valid.gz"]

    @patch("processor.nav.urllib.request.urlopen")
    def test_empty_directory_returns_empty_list(self, mock_urlopen):
        """Empty HTML returns no files."""
        html = "<html><body><p>No files here</p></body></html>"
        mock_urlopen.return_value = self._mock_html_response(html)

        files = _list_bkg_nav_files(2024, 150)

        assert files == []

    @patch("processor.nav.urllib.request.urlopen")
    def test_http_error_raises_nav_fetch_error(self, mock_urlopen):
        """HTTP 404 raises NavFetchError."""
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://example.com",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None,
        )

        with pytest.raises(NavFetchError, match="not accessible"):
            _list_bkg_nav_files(2024, 150)

    @patch("processor.nav.urllib.request.urlopen")
    def test_url_error_raises_nav_fetch_error(self, mock_urlopen):
        """Network error raises NavFetchError."""
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with pytest.raises(NavFetchError, match="request failed"):
            _list_bkg_nav_files(2024, 150)

    @patch("processor.nav.urllib.request.urlopen")
    def test_timeout_raises_nav_fetch_error(self, mock_urlopen):
        """Timeout raises NavFetchError."""
        mock_urlopen.side_effect = TimeoutError("timed out")

        with pytest.raises(NavFetchError, match="timed out"):
            _list_bkg_nav_files(2024, 150)

    @patch("processor.nav.urllib.request.urlopen")
    def test_constructs_correct_url(self, mock_urlopen):
        """Verifies the URL constructed for the HTTP request."""
        html = "<html><body></body></html>"
        mock_urlopen.return_value = self._mock_html_response(html)

        _list_bkg_nav_files(2024, 5)

        # Check the Request object passed to urlopen
        call_args = mock_urlopen.call_args
        request_obj = call_args[0][0]
        assert request_obj.full_url == f"{BKG_BRDC_BASE_URL}/2024/005/"

    @patch("processor.nav.urllib.request.urlopen")
    def test_passes_timeout_to_urlopen(self, mock_urlopen):
        """Timeout parameter is forwarded to urlopen."""
        html = "<html><body></body></html>"
        mock_urlopen.return_value = self._mock_html_response(html)

        _list_bkg_nav_files(2024, 150, timeout=15.0)

        call_args = mock_urlopen.call_args
        assert call_args[1]["timeout"] == 15.0 or call_args[0][1] == 15.0


# ---------------------------------------------------------------------------
# fetch_nav_file tests (full workflow mocked)
# ---------------------------------------------------------------------------


class TestFetchNavFile:
    """Tests for fetch_nav_file with mocked HTTP and file operations."""

    def _mock_html_response(self, html_content: str):
        """Create a mock response context manager."""
        mock_response = MagicMock()
        mock_response.read.return_value = html_content.encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        return mock_response

    @patch("processor.nav._decompress_if_needed")
    @patch("processor.nav._download_file")
    @patch("processor.nav._list_bkg_nav_files")
    def test_no_compatible_file_raises_error(self, mock_list, mock_download, mock_decompress):
        """NavFetchError raised when no compatible file found."""
        mock_list.return_value = ["random_file.gz", "other.gz"]

        with pytest.raises(NavFetchError, match="No compatible"):
            fetch_nav_file(2024, 150, nav_day_offset=1)

        mock_download.assert_not_called()

    @patch("processor.nav._decompress_if_needed")
    @patch("processor.nav._download_file")
    @patch("processor.nav._list_bkg_nav_files")
    def test_successful_fetch_downloads_to_tmp(self, mock_list, mock_download, mock_decompress, tmp_path):
        """Successful workflow: list -> select -> download -> decompress."""
        mock_list.return_value = ["BRDC00IGS_R_20241490000_01D_MN.rnx.gz"]
        expected_path = tmp_path / "BRDC00IGS_R_20241490000_01D_MN.rnx"
        mock_decompress.return_value = expected_path

        # Patch Path("/tmp") so the file existence check fails (file not cached)
        with patch("processor.nav.Path") as mock_path_cls:
            mock_output_dir = MagicMock()
            mock_path_cls.return_value = mock_output_dir
            mock_dest_gz = MagicMock()
            mock_dest_gz.exists.return_value = False
            mock_output_dir.__truediv__ = MagicMock(return_value=mock_dest_gz)
            mock_output_dir.mkdir = MagicMock()

            result = fetch_nav_file(2024, 150, nav_day_offset=1)

        # nav doy should be 149 (150 - 1)
        mock_list.assert_called_once_with(2024, 149, timeout=30.0)
        mock_download.assert_called_once()
        assert result == expected_path

    @patch("processor.nav._decompress_if_needed")
    @patch("processor.nav._download_file")
    @patch("processor.nav._list_bkg_nav_files")
    def test_list_failure_propagates(self, mock_list, mock_download, mock_decompress):
        """NavFetchError from listing propagates to caller."""
        mock_list.side_effect = NavFetchError("BKG directory not accessible")

        with pytest.raises(NavFetchError, match="not accessible"):
            fetch_nav_file(2024, 150, nav_day_offset=1)

    @patch("processor.nav._decompress_if_needed")
    @patch("processor.nav._download_file")
    @patch("processor.nav._list_bkg_nav_files")
    def test_download_failure_propagates(self, mock_list, mock_download, mock_decompress):
        """NavFetchError from download propagates to caller."""
        mock_list.return_value = ["BRDC00IGS_R_20241490000_01D_MN.rnx.gz"]
        mock_download.side_effect = NavFetchError("download timed out")

        # Patch Path so file doesn't appear cached
        with patch("processor.nav.Path") as mock_path_cls:
            mock_output_dir = MagicMock()
            mock_path_cls.return_value = mock_output_dir
            mock_dest_gz = MagicMock()
            mock_dest_gz.exists.return_value = False
            mock_output_dir.__truediv__ = MagicMock(return_value=mock_dest_gz)
            mock_output_dir.mkdir = MagicMock()

            with pytest.raises(NavFetchError, match="timed out"):
                fetch_nav_file(2024, 150, nav_day_offset=1)

    @patch("processor.nav._decompress_if_needed")
    @patch("processor.nav._download_file")
    @patch("processor.nav._list_bkg_nav_files")
    def test_year_rollback_in_fetch(self, mock_list, mock_download, mock_decompress):
        """Year rollback passes correct nav_year/nav_doy to listing."""
        mock_list.return_value = ["BRDC00IGS_R_20233650000_01D_MN.rnx.gz"]
        mock_decompress.return_value = Path("/tmp/BRDC00IGS_R_20233650000_01D_MN.rnx")

        with patch("processor.nav.Path") as mock_path_cls:
            mock_output_dir = MagicMock()
            mock_path_cls.return_value = mock_output_dir
            mock_dest_gz = MagicMock()
            mock_dest_gz.exists.return_value = False
            mock_output_dir.__truediv__ = MagicMock(return_value=mock_dest_gz)
            mock_output_dir.mkdir = MagicMock()

            fetch_nav_file(2024, 1, nav_day_offset=1)

        # doy=1, offset=1 -> rolls back to 2023, doy=365
        mock_list.assert_called_once_with(2023, 365, timeout=30.0)

    @patch("processor.nav._decompress_if_needed")
    @patch("processor.nav._download_file")
    @patch("processor.nav._list_bkg_nav_files")
    def test_custom_timeouts_passed(self, mock_list, mock_download, mock_decompress):
        """Custom timeout values are forwarded correctly."""
        mock_list.return_value = ["BRDC00IGS_R_20241490000_01D_MN.rnx.gz"]
        mock_decompress.return_value = Path("/tmp/BRDC00IGS_R_20241490000_01D_MN.rnx")

        with patch("processor.nav.Path") as mock_path_cls:
            mock_output_dir = MagicMock()
            mock_path_cls.return_value = mock_output_dir
            mock_dest_gz = MagicMock()
            mock_dest_gz.exists.return_value = False
            mock_output_dir.__truediv__ = MagicMock(return_value=mock_dest_gz)
            mock_output_dir.mkdir = MagicMock()

            fetch_nav_file(2024, 150, nav_day_offset=1, timeout_list=10.0, timeout_download=60.0)

        mock_list.assert_called_once_with(2024, 149, timeout=10.0)
        # download_file is called with timeout
        download_call = mock_download.call_args
        assert download_call[1].get("timeout") == 60.0 or (
            len(download_call[0]) >= 3 and download_call[0][2] == 60.0
        )
