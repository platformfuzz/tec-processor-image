import pytest

from processor.logic import compute_nav_doy, derive_output_key, parse_raw_key, require_output_format


def test_parse_raw_key_valid():
    year, doy, station, stem = parse_raw_key("raw/rinexhourly/2024/150/auck1500.24o")
    assert (year, doy, station, stem) == (2024, 150, "auck", "auck1500")


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
    key = derive_output_key("auck", 2024, 150, "auck1500")
    assert key == "processed/station=auck/year=2024/doy=150/auck1500.parquet"


def test_compute_nav_doy_rolls_back_year():
    nav_year, nav_doy = compute_nav_doy(1, 2024, 1)
    assert nav_year == 2023
    assert nav_doy == 365


def test_require_output_format_requires_parquet():
    with pytest.raises(ValueError, match="SAVE_PARQUET is false"):
        require_output_format({"SAVE_PARQUET": False, "SAVE_CSV": False})
