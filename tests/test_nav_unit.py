"""Unit tests for navigation helper behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from processor import NavFetchError
from processor.nav import compute_nav_doy, download_nav_file, fetch_nav_file, select_bkg_nav_filename


class TestComputeNavDoy:
    def test_simple_offset(self):
        nav_year, nav_doy = compute_nav_doy(2024, 100, 1)
        assert nav_year == 2024
        assert nav_doy == 99

    def test_rollback_to_previous_year(self):
        nav_year, nav_doy = compute_nav_doy(2024, 1, 1)
        assert nav_year == 2023
        assert nav_doy == 365

    def test_invalid_offset(self):
        with pytest.raises(ValueError, match="positive"):
            compute_nav_doy(2024, 100, 0)


class TestSelectBkgNavFilename:
    def test_prefers_igs_over_others(self):
        files = [
            "brdc1750.26p.gz",
            "BRDC00WRD_R_20261750000_01D_MN.rnx.gz",
            "BRDC00IGS_R_20261750000_01D_MN.rnx.gz",
        ]
        chosen = select_bkg_nav_filename(files, 2026, 175)
        assert chosen == "BRDC00IGS_R_20261750000_01D_MN.rnx.gz"

    def test_falls_back_to_legacy_pattern(self):
        files = ["some_other_file.gz", "brdc1500.24p.gz"]
        chosen = select_bkg_nav_filename(files, 2024, 150)
        assert chosen == "brdc1500.24p.gz"

    def test_no_compatible_file_returns_none(self):
        files = ["some_random_file.gz", "readme.txt"]
        chosen = select_bkg_nav_filename(files, 2024, 150)
        assert chosen is None


@patch("processor.nav._download_nav_for_day")
def test_fetch_nav_file_computes_offset_and_calls_downloader(mock_download):
    expected = Path("/tmp/nav.rnx.gz")
    mock_download.return_value = expected

    result = fetch_nav_file(2024, 150, nav_day_offset=1)
    assert result == expected
    mock_download.assert_called_once_with(2024, 149, Path("/tmp"))


@patch("processor.nav._download_nav_for_day")
def test_fetch_nav_file_supports_custom_output_dir(mock_download, tmp_path):
    expected = tmp_path / "nav.rnx.gz"
    mock_download.return_value = expected

    result = fetch_nav_file(2024, 150, nav_day_offset=2, output_dir=tmp_path)
    assert result == expected
    mock_download.assert_called_once_with(2024, 148, tmp_path)


@patch("processor.nav._download_nav_for_day")
def test_download_nav_file_uses_pytecgg_adapter(mock_download, tmp_path):
    expected = tmp_path / "nav.rnx.gz"
    mock_download.return_value = expected

    result = download_nav_file(2024, 150, tmp_path)
    assert result == expected
    mock_download.assert_called_once_with(2024, 150, tmp_path)


@patch("processor.nav._download_nav_for_day")
def test_nav_fetch_error_propagates(mock_download):
    mock_download.side_effect = NavFetchError("download failed")

    with pytest.raises(NavFetchError, match="download failed"):
        fetch_nav_file(2024, 150, nav_day_offset=1)
