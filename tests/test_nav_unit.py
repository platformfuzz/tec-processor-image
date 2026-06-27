<<<<<<< HEAD
"""Unit tests for navigation helper behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
=======
"""Unit tests for navigation fetch helpers."""

from __future__ import annotations

import gzip
from pathlib import Path
>>>>>>> 4fab63a (fix: delegate nav download to pytecgg)

import pytest

from processor import NavFetchError
from processor.nav import compute_nav_doy, download_nav_file, fetch_nav_file, select_bkg_nav_filename
<<<<<<< HEAD


class TestComputeNavDoy:
    def test_simple_offset(self):
        nav_year, nav_doy = compute_nav_doy(2024, 100, 1)
        assert nav_year == 2024
        assert nav_doy == 99

    def test_rollback_to_previous_year(self):
        nav_year, nav_doy = compute_nav_doy(2024, 1, 1)
=======


def test_compute_nav_doy_rolls_back_year():
    nav_year, nav_doy = compute_nav_doy(2024, 1, 1)
    assert nav_year == 2023
    assert nav_doy == 365


def test_select_bkg_nav_filename_prefers_igs():
    files = [
        "brdc1500.24p.gz",
        "BRDC00WRD_R_20241500000_01D_MN.rnx.gz",
        "BRDC00IGS_R_20241500000_01D_MN.rnx.gz",
    ]
    chosen = select_bkg_nav_filename(files, 2024, 150)
    assert chosen == "BRDC00IGS_R_20241500000_01D_MN.rnx.gz"


def test_download_nav_file_uses_pytecgg_download(monkeypatch, tmp_path):
    called: list[tuple[int, list[int], Path]] = []

    def fake_download_nav_bkg(year: int, doys: list[int], output_path: Path) -> None:
        called.append((year, doys, output_path))
        path = output_path / "BRDC00IGS_R_20241500000_01D_MN.rnx.gz"
        output_path.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wb") as handle:
            handle.write(b"nav-data")

    monkeypatch.setattr("processor.nav._pytecgg_download_nav_bkg", fake_download_nav_bkg)

    nav_path = download_nav_file(2024, 150, tmp_path)

    assert called == [(2024, [150], tmp_path)]
    assert nav_path.exists()
    assert nav_path.suffix != ".gz"


def test_download_nav_file_raises_when_pytecgg_download_fails(monkeypatch, tmp_path):
    def fake_download_nav_bkg(_year: int, _doys: list[int], _output_path: Path) -> None:
        raise RuntimeError("network fail")

    monkeypatch.setattr("processor.nav._pytecgg_download_nav_bkg", fake_download_nav_bkg)

    with pytest.raises(NavFetchError, match="PyTECGg nav download failed"):
        download_nav_file(2024, 150, tmp_path)


def test_fetch_nav_file_uses_offset_and_tmp(monkeypatch, tmp_path):
    def fake_download_nav_file(nav_year: int, nav_doy: int, output_dir: Path) -> Path:
>>>>>>> 4fab63a (fix: delegate nav download to pytecgg)
        assert nav_year == 2023
        assert nav_doy == 365
        assert output_dir == Path("/tmp")
        return tmp_path / "nav.rnx"

<<<<<<< HEAD
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
=======
    monkeypatch.setattr("processor.nav.download_nav_file", fake_download_nav_file)
    result = fetch_nav_file(2024, 1, nav_day_offset=1)
    assert result == tmp_path / "nav.rnx"
>>>>>>> 4fab63a (fix: delegate nav download to pytecgg)
