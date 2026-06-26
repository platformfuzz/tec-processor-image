"""Unit tests for plot generation serializers."""

from __future__ import annotations

import pytest

from processor.plot_io import rows_to_interactive_plot_bytes, rows_to_static_plot_bytes


def _sample_rows(n: int = 4) -> list[dict]:
    base_rows = [
        {
            "epoch": f"2024-05-29T0{i}:00:00Z",
            "sv": f"G0{i + 1}",
            "id_arc": i + 1,
            "lat_ipp": -36.85 + i * 0.1,
            "lon_ipp": 174.76 + i * 0.1,
            "azi": 45.0 + i,
            "ele": 30.0 + i,
            "bias": 0.5,
            "stec": 12.0 + i,
            "vtec": 8.0 + i,
            "veq": 8.5 + i,
        }
        for i in range(n)
    ]
    return base_rows


def test_static_plot_returns_bytes():
    rows = _sample_rows()
    result = rows_to_static_plot_bytes(rows, station="auck", year=2024, doy=150)
    assert isinstance(result, bytes)


def test_static_plot_is_png():
    rows = _sample_rows()
    result = rows_to_static_plot_bytes(rows, station="auck", year=2024, doy=150)
    assert result[:8] == b"\x89PNG\r\n\x1a\n", "Expected PNG magic bytes"


def test_static_plot_has_nonzero_size():
    rows = _sample_rows()
    result = rows_to_static_plot_bytes(rows, station="auck", year=2024, doy=150)
    assert len(result) > 1000


def test_static_plot_raises_on_empty_rows():
    with pytest.raises(ValueError, match="no rows"):
        rows_to_static_plot_bytes([], station="auck", year=2024, doy=150)


def test_interactive_plot_returns_bytes():
    rows = _sample_rows()
    result = rows_to_interactive_plot_bytes(rows, station="auck", year=2024, doy=150)
    assert isinstance(result, bytes)


def test_interactive_plot_is_html():
    rows = _sample_rows()
    result = rows_to_interactive_plot_bytes(rows, station="auck", year=2024, doy=150)
    html = result.decode("utf-8")
    assert "<html>" in html.lower() or "<!doctype" in html.lower()


def test_interactive_plot_contains_plotlyjs_cdn():
    rows = _sample_rows()
    result = rows_to_interactive_plot_bytes(rows, station="auck", year=2024, doy=150)
    html = result.decode("utf-8")
    assert "plotly" in html.lower()


def test_interactive_plot_raises_on_empty_rows():
    with pytest.raises(ValueError, match="no rows"):
        rows_to_interactive_plot_bytes([], station="auck", year=2024, doy=150)


def test_static_plot_dpi_parameter():
    rows = _sample_rows()
    low_dpi = rows_to_static_plot_bytes(rows, station="auck", year=2024, doy=150, dpi=72)
    high_dpi = rows_to_static_plot_bytes(rows, station="auck", year=2024, doy=150, dpi=150)
    assert isinstance(low_dpi, bytes)
    assert isinstance(high_dpi, bytes)
