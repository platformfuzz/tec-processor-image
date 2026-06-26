import pytest

from processor.logic import compute_nav_doy, derive_output_key, parse_raw_key, require_output_format, validate_processing_params


def test_parse_raw_key_valid():
    year, doy, station, stem = parse_raw_key("raw/rinexhourly/2024/150/auck1500.24o")
    assert (year, doy, station, stem) == (2024, 150, "auck", "auck1500")


def test_parse_raw_key_valid_geonet_prefix():
    year, doy, station, stem = parse_raw_key(
        "gnss/rinexhourly/2026/175/AUKT00NZL_R_20261750000_01H_30S_MO.rnx.gz"
    )
    assert (year, doy, station, stem) == (2026, 175, "aukt", "AUKT00NZL_R_20261750000_01H_30S_MO.rnx")


@pytest.mark.parametrize(
    "bad_key",
    [
        "raw/rinexhourly/2024/15/auck1500.24o",
        "raw/rinexhourly/2024/400/auck1500.24o",
        "raw/rinexhourly/2024/150/12341500.24o",
        "raw/rinexhourly/2024/150",
    ],
)
def test_parse_raw_key_invalid(bad_key):
    with pytest.raises(ValueError):
        parse_raw_key(bad_key)


def test_derive_output_key():
    key = derive_output_key("auck", 2024, 150, "auck1500", "processed/tec")
    assert key == "processed/tec/station=auck/year=2024/doy=150/auck1500.parquet"


def test_derive_output_key_csv_extension():
    key = derive_output_key("auck", 2024, 150, "auck1500", "processed/tec", extension="csv")
    assert key == "processed/tec/station=auck/year=2024/doy=150/auck1500.csv"


def test_derive_output_key_json_extension():
    key = derive_output_key("auck", 2024, 150, "auck1500", "processed/tec", extension="json")
    assert key == "processed/tec/station=auck/year=2024/doy=150/auck1500.json"


def test_derive_output_key_png_extension():
    key = derive_output_key("auck", 2024, 150, "auck1500", "processed/tec", extension="png")
    assert key == "processed/tec/station=auck/year=2024/doy=150/auck1500.png"


def test_derive_output_key_html_extension():
    key = derive_output_key("auck", 2024, 150, "auck1500", "processed/tec", extension="html")
    assert key == "processed/tec/station=auck/year=2024/doy=150/auck1500.html"


def test_compute_nav_doy_rolls_back_year():
    nav_year, nav_doy = compute_nav_doy(1, 2024, 1)
    assert nav_year == 2023
    assert nav_doy == 365


def test_require_output_format_no_flags_raises():
    with pytest.raises(ValueError, match="No output format enabled"):
        require_output_format({"SAVE_PARQUET": False, "SAVE_CSV": False})


def test_require_output_format_parquet_passes():
    require_output_format({"SAVE_PARQUET": True})


def test_require_output_format_csv_passes():
    require_output_format({"SAVE_CSV": True})


def test_require_output_format_json_passes():
    require_output_format({"SAVE_JSON": True})


def test_require_output_format_static_plots_passes():
    require_output_format({"SAVE_STATIC_PLOTS": True})


def test_require_output_format_interactive_plots_passes():
    require_output_format({"SAVE_INTERACTIVE_PLOTS": True})


def test_require_output_format_multiple_flags_passes():
    require_output_format({"SAVE_PARQUET": True, "SAVE_JSON": True, "SAVE_CSV": True})


def test_validate_processing_params_save_json_bool():
    result = validate_processing_params({"SAVE_JSON": True})
    assert result["SAVE_JSON"] is True


def test_validate_processing_params_save_json_invalid():
    with pytest.raises(ValueError, match="Invalid SAVE_JSON"):
        validate_processing_params({"SAVE_JSON": "yes"})
