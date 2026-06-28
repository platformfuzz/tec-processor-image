from __future__ import annotations

import json
import random
import string
from datetime import date
from urllib.parse import quote_plus

import pytest
from hypothesis import given, settings
from hypothesis import HealthCheck
from hypothesis import strategies as st

from processor.logic import (
    compute_nav_doy,
    derive_output_key,
    extract_message_payload,
    merge_parameters,
    parse_raw_key,
    validate_processing_params,
)

DEFAULTS = {
    "NAV_DAY_OFFSET": 1,
    "SAVE_PARQUET": True,
    "SAVE_CSV": False,
    "SAVE_STATIC_PLOTS": False,
    "SAVE_INTERACTIVE_PLOTS": False,
}
DESTINATION_PREFIX = "processed/tec"


# --- Hypothesis strategies for Property 4: Raw Key Parse Determinism ---

_alnum_station = st.text(alphabet=string.ascii_lowercase + string.digits, min_size=4, max_size=4)
_alphanumeric_suffix = st.text(
    alphabet=string.ascii_lowercase + string.digits, min_size=0, max_size=12
)
_extension = st.text(alphabet=string.ascii_lowercase + string.digits, min_size=1, max_size=4)
_year = st.integers(min_value=1900, max_value=2099)
_doy = st.integers(min_value=1, max_value=366)


@st.composite
def valid_raw_keys(draw):
    """Generate valid raw keys matching raw/rinexhourly/{year}/{doy}/{filename}."""
    year = draw(_year)
    doy = draw(_doy)
    station = draw(_alnum_station)
    suffix = draw(_alphanumeric_suffix)
    ext = draw(_extension)
    filename = f"{station}{suffix}.{ext}"
    key = f"raw/rinexhourly/{year}/{doy:03d}/{filename}"
    source_stem = f"{station}{suffix}"
    return key, year, doy, station, source_stem


@st.composite
def invalid_raw_keys(draw):
    """Generate keys that should fail parse_raw_key validation."""
    case = draw(st.integers(min_value=0, max_value=4))

    if case == 0:
        # Missing components (no rinexhourly prefix)
        year = draw(_year)
        doy = draw(_doy)
        filename = draw(_alnum_station) + "1500.24o"
        return f"raw/other/{year}/{doy:03d}/{filename}"
    elif case == 1:
        # Invalid doy > 366
        year = draw(_year)
        doy = draw(st.integers(min_value=367, max_value=999))
        station = draw(_alnum_station)
        return f"raw/rinexhourly/{year}/{doy:03d}/{station}1500.24o"
    elif case == 2:
        # Invalid station prefix (contains symbols)
        year = draw(_year)
        doy = draw(_doy)
        symbol_prefix = draw(st.text(alphabet="!@#$%^&*()", min_size=1, max_size=1))
        return f"raw/rinexhourly/{year}/{doy:03d}/12A{symbol_prefix}1500.24o"
    elif case == 3:
        # Station prefix invalid (symbol injected within first four chars)
        year = draw(_year)
        doy = draw(_doy)
        short_station = draw(st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=3))
        symbol = draw(st.text(alphabet="!@#$%^&*()", min_size=1, max_size=1))
        padding = draw(st.text(alphabet=string.ascii_lowercase + string.digits, min_size=0, max_size=4))
        return f"raw/rinexhourly/{year}/{doy:03d}/{short_station}{symbol}{padding}.24o"
    else:
        # Completely wrong format
        return draw(st.text(min_size=1, max_size=50))


# Feature: tec-processor-image, Property 4: Raw Key Parse Determinism
class TestRawKeyParseDeterminism:
    """Property 4: Raw Key Parse Determinism.

    Validates: Requirements 4.1, 4.2
    """

    @settings(max_examples=100)
    @given(data=valid_raw_keys())
    def test_valid_key_deterministic_extraction(self, data):
        """For any valid raw key, parse_raw_key always returns the same
        (year, doy, station, source_stem) tuple deterministically."""
        key, expected_year, expected_doy, expected_station, expected_stem = data

        # Parse twice to confirm determinism
        result1 = parse_raw_key(key)
        result2 = parse_raw_key(key)

        # Same result each time (determinism)
        assert result1 == result2

        # Correct extraction
        year, doy, station, source_stem = result1
        assert year == expected_year
        assert doy == expected_doy
        assert station == expected_station
        assert source_stem == expected_stem

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(key=invalid_raw_keys())
    def test_invalid_key_raises_error(self, key):
        """For any key not matching the valid pattern, parse_raw_key raises ValueError."""
        with pytest.raises(ValueError):
            parse_raw_key(key)


def test_property_raw_key_round_trip():
    stations = ["auck", "wark", "chch", "dned"]
    for _ in range(200):
        year = random.randint(2000, 2099)
        doy = random.randint(1, 366)
        station = random.choice(stations)
        suffix = "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(random.randint(0, 8)))
        filename = f"{station}{doy:03d}{suffix}.24o"
        key = f"raw/rinexhourly/{year}/{doy:03d}/{filename}"
        parsed_year, parsed_doy, parsed_station, parsed_stem = parse_raw_key(key)
        assert parsed_year == year
        assert parsed_doy == doy
        assert parsed_station == station
        assert parsed_stem == filename.rsplit(".", 1)[0]


def test_property_navigation_doy_rollback():
    for _ in range(200):
        observation_year = random.randint(2001, 2100)
        observation_doy = random.randint(1, 366)
        offset = random.randint(1, 30)
        nav_year, nav_doy = compute_nav_doy(observation_doy, observation_year, offset)
        assert nav_doy >= 1
        max_doy = (date(nav_year + 1, 1, 1) - date(nav_year, 1, 1)).days
        assert nav_doy <= max_doy


def test_property_deterministic_output_key():
    stations = ["auck", "wark", "chch", "dned"]
    for _ in range(200):
        year = random.randint(2000, 2099)
        doy = random.randint(1, 366)
        station = random.choice(stations)
        stem = "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(random.randint(4, 20)))
        first = derive_output_key(station, year, doy, stem, DESTINATION_PREFIX)
        second = derive_output_key(station, year, doy, stem, DESTINATION_PREFIX)
        assert first == second


def test_property_parameter_merge_correctness():
    for _ in range(200):
        overrides = {
            "NAV_DAY_OFFSET": random.randint(1, 7),
            "SAVE_PARQUET": random.choice([True, False]),
            "SAVE_CSV": random.choice([True, False]),
            "SAVE_STATIC_PLOTS": random.choice([True, False]),
            "SAVE_INTERACTIVE_PLOTS": random.choice([True, False]),
        }
        merged = merge_parameters(DEFAULTS, overrides)
        assert set(merged.keys()) == set(DEFAULTS.keys())
        assert merged == overrides


def test_property_parameter_type_validation():
    invalid_nav_values = ["abc", 0, -1, 3.14]
    invalid_bool_values = ["true", 1, 0, "no"]
    for nav_value in invalid_nav_values:
        params = dict(DEFAULTS)
        params["NAV_DAY_OFFSET"] = nav_value
        try:
            validate_processing_params(params)
        except ValueError:
            pass
        else:
            raise AssertionError("Expected ValueError for invalid NAV_DAY_OFFSET type/value")

    for bool_value in invalid_bool_values:
        params = dict(DEFAULTS)
        params["SAVE_CSV"] = bool_value
        try:
            validate_processing_params(params)
        except ValueError:
            pass
        else:
            raise AssertionError("Expected ValueError for invalid SAVE_CSV type")


# --- Hypothesis-based property tests ---

from datetime import date as _date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


# Feature: tec-processor-image, Property 5: Nav DOY Computation with Year Rollback
class TestNavDoyComputationProperty:
    """Property 5: Nav DOY Computation with Year Rollback.

    Validates: Requirements 4.3
    """

    @settings(max_examples=100)
    @given(
        year=st.integers(min_value=1990, max_value=2030),
        doy=st.integers(min_value=1, max_value=366),
        offset=st.integers(min_value=1, max_value=365),
    )
    def test_nav_doy_always_in_valid_range(self, year: int, doy: int, offset: int) -> None:
        """Result (nav_year, nav_doy) always satisfies 1 <= nav_doy <= 366."""
        nav_year, nav_doy = compute_nav_doy(doy, year, offset)
        assert 1 <= nav_doy <= 366, f"nav_doy={nav_doy} out of range for nav_year={nav_year}"

    @settings(max_examples=100)
    @given(
        year=st.integers(min_value=1990, max_value=2030),
        doy=st.integers(min_value=1, max_value=366),
        offset=st.integers(min_value=1, max_value=365),
    )
    def test_nav_doy_no_rollback_when_positive(self, year: int, doy: int, offset: int) -> None:
        """When doy - offset >= 1: nav_year == year and nav_doy == doy - offset."""
        from hypothesis import assume

        assume(doy - offset >= 1)
        nav_year, nav_doy = compute_nav_doy(doy, year, offset)
        assert nav_year == year
        assert nav_doy == doy - offset

    @settings(max_examples=100)
    @given(
        year=st.integers(min_value=1990, max_value=2030),
        doy=st.integers(min_value=1, max_value=366),
        offset=st.integers(min_value=1, max_value=365),
    )
    def test_nav_doy_rollback_when_negative(self, year: int, doy: int, offset: int) -> None:
        """When doy - offset < 1: nav_year < year (rolled back to previous year)."""
        from hypothesis import assume

        assume(doy - offset < 1)
        nav_year, nav_doy = compute_nav_doy(doy, year, offset)
        assert nav_year < year, f"Expected rollback but nav_year={nav_year} >= year={year}"
        # Also verify the result is still valid
        assert 1 <= nav_doy <= 366

    @settings(max_examples=100)
    @given(
        year=st.integers(min_value=1990, max_value=2030),
        doy=st.integers(min_value=1, max_value=366),
        offset=st.integers(max_value=0),
    )
    def test_nav_doy_raises_on_non_positive_offset(self, year: int, doy: int, offset: int) -> None:
        """Offset <= 0 raises ValueError."""
        with pytest.raises(ValueError):
            compute_nav_doy(doy, year, offset)


# Feature: tec-processor-image, Property 12: Parameter Override Precedence
import hypothesis.strategies as st
from hypothesis import given, settings


@st.composite
def env_defaults_strategy(draw: st.DrawFn) -> dict:
    """Generate valid env_defaults with all 5 allowed keys."""
    return {
        "NAV_DAY_OFFSET": draw(st.integers(min_value=1, max_value=365)),
        "SAVE_PARQUET": draw(st.booleans()),
        "SAVE_CSV": draw(st.booleans()),
        "SAVE_STATIC_PLOTS": draw(st.booleans()),
        "SAVE_INTERACTIVE_PLOTS": draw(st.booleans()),
    }


@st.composite
def message_overrides_strategy(draw: st.DrawFn) -> dict | None:
    """Generate message_overrides as a subset of allowed keys with valid values, or None."""
    include_overrides = draw(st.booleans())
    if not include_overrides:
        return None

    keys_to_include = draw(
        st.lists(
            st.sampled_from(["NAV_DAY_OFFSET", "SAVE_PARQUET", "SAVE_CSV", "SAVE_STATIC_PLOTS", "SAVE_INTERACTIVE_PLOTS"]),
            min_size=1,
            max_size=5,
            unique=True,
        )
    )

    overrides: dict = {}
    for key in keys_to_include:
        if key == "NAV_DAY_OFFSET":
            overrides[key] = draw(st.integers(min_value=1, max_value=365))
        else:
            overrides[key] = draw(st.booleans())

    return overrides


@settings(max_examples=100)
@given(env_defaults=env_defaults_strategy(), message_overrides=message_overrides_strategy())
def test_property_12_parameter_override_precedence(env_defaults: dict, message_overrides: dict | None):
    """
    Property 12: Parameter Override Precedence

    For any allowed parameter key present in both env_defaults and message_overrides,
    the merged result SHALL equal the message value. For any key absent from
    message_overrides, the merged result SHALL equal the env default.

    Validates: Requirements 7.1, 7.2
    """
    merged = merge_parameters(env_defaults, message_overrides)

    if message_overrides is None or len(message_overrides) == 0:
        # When no overrides, merged result should equal env_defaults
        assert merged == env_defaults
    else:
        # Keys in message_overrides take the message value
        for key, value in message_overrides.items():
            assert merged[key] == value, f"Expected message override for {key}={value}, got {merged[key]}"

        # Keys NOT in message_overrides retain env_defaults value
        for key, value in env_defaults.items():
            if key not in message_overrides:
                assert merged[key] == value, f"Expected env default for {key}={value}, got {merged[key]}"


@settings(max_examples=100)
@given(env_defaults=env_defaults_strategy())
def test_property_12_none_overrides_returns_defaults(env_defaults: dict):
    """
    Property 12 sub-property: When message_overrides is None, merged result
    equals env_defaults exactly.

    Validates: Requirements 7.1, 7.2
    """
    merged = merge_parameters(env_defaults, None)
    assert merged == env_defaults


@settings(max_examples=100)
@given(env_defaults=env_defaults_strategy())
def test_property_12_empty_overrides_returns_defaults(env_defaults: dict):
    """
    Property 12 sub-property: When message_overrides is empty dict, merged result
    equals env_defaults exactly.

    Validates: Requirements 7.1, 7.2
    """
    merged = merge_parameters(env_defaults, {})
    assert merged == env_defaults


# Feature: tec-processor-image, Property 9: Output Path Determinism
class TestOutputPathDeterminism:
    """Property 9: Output Path Determinism.

    **Validates: Requirements 6.1, 6.3**
    """

    @settings(max_examples=100)
    @given(
        station=st.text(alphabet=string.ascii_lowercase, min_size=4, max_size=4),
        year=st.integers(min_value=1990, max_value=2099),
        doy=st.integers(min_value=1, max_value=366),
        source_stem=st.text(
            alphabet=string.ascii_lowercase + string.digits, min_size=1, max_size=20
        ),
    )
    def test_output_key_matches_expected_format(
        self, station: str, year: int, doy: int, source_stem: str
    ) -> None:
        """Output key always equals the deterministic path template."""
        result = derive_output_key(station, year, doy, source_stem, DESTINATION_PREFIX)
        expected = (
            f"{DESTINATION_PREFIX}/station={station}/year={year}/"
            f"doy={doy:03d}/{source_stem}.parquet"
        )
        assert result == expected

    @settings(max_examples=100)
    @given(
        station=st.text(alphabet=string.ascii_lowercase, min_size=4, max_size=4),
        year=st.integers(min_value=1990, max_value=2099),
        doy=st.integers(min_value=1, max_value=366),
        source_stem=st.text(
            alphabet=string.ascii_lowercase + string.digits, min_size=1, max_size=20
        ),
    )
    def test_output_key_determinism(
        self, station: str, year: int, doy: int, source_stem: str
    ) -> None:
        """Calling derive_output_key twice with same input gives same result."""
        first = derive_output_key(station, year, doy, source_stem, DESTINATION_PREFIX)
        second = derive_output_key(station, year, doy, source_stem, DESTINATION_PREFIX)
        assert first == second


# Feature: tec-processor-image, Property 2: Payload Normalization
# Validates: Requirements 3.2, 3.3, 3.4, 3.6

# Strategies for generating valid S3 bucket names and object keys
_bucket_names = st.from_regex(r"[a-z][a-z0-9\-]{2,62}", fullmatch=True)
_station_names = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz"), min_size=4, max_size=4
)
_years = st.integers(min_value=2000, max_value=2099)
_doys = st.integers(min_value=1, max_value=366)
_suffixes = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789"), min_size=0, max_size=8
)


@st.composite
def s3_bucket_and_key(draw):
    """Generate a bucket name and a valid raw RINEX key."""
    bucket = draw(_bucket_names)
    station = draw(_station_names)
    year = draw(_years)
    doy = draw(_doys)
    suffix = draw(_suffixes)
    filename = f"{station}{doy:03d}{suffix}.{year % 100:02d}o"
    key = f"raw/rinexhourly/{year}/{doy:03d}/{filename}"
    return bucket, key


@given(data=s3_bucket_and_key())
@settings(max_examples=100)
def test_property_payload_normalization_same_bucket_and_key(data):
    """All three message formats extract the same bucket and key."""
    bucket, key = data

    # Format 1: Direct processor message
    direct_body = json.dumps({"key": key, "bucket": bucket})
    direct_result = extract_message_payload(direct_body)
    assert direct_result["key"] == key
    assert direct_result["bucket"] == bucket

    # Format 2: S3 event notification (key is URL-encoded in S3 events)
    s3_event_body = json.dumps({
        "Records": [{
            "eventSource": "aws:s3",
            "s3": {
                "bucket": {"name": bucket},
                "object": {"key": key},  # No encoding needed here since key has no special chars
            },
        }]
    })
    s3_result = extract_message_payload(s3_event_body)
    assert s3_result["bucket"] == bucket
    assert s3_result["key"] == key

    # Format 3: SNS-wrapped S3 event
    sns_body = json.dumps({
        "Message": json.dumps({
            "Records": [{
                "eventSource": "aws:s3",
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key},
                },
            }]
        })
    })
    sns_result = extract_message_payload(sns_body)
    assert sns_result["bucket"] == bucket
    assert sns_result["key"] == key


@given(data=s3_bucket_and_key())
@settings(max_examples=100)
def test_property_payload_normalization_url_decoding(data):
    """S3 event format URL-decodes the key (spaces encoded as +, special chars as %XX)."""
    bucket, key = data

    # URL-encode the key (spaces become +, other chars become %XX)
    encoded_key = quote_plus(key, safe="/")

    # Format 2: S3 event with URL-encoded key
    s3_event_body = json.dumps({
        "Records": [{
            "eventSource": "aws:s3",
            "s3": {
                "bucket": {"name": bucket},
                "object": {"key": encoded_key},
            },
        }]
    })
    s3_result = extract_message_payload(s3_event_body)
    # After URL-decoding, the key should match the original
    assert s3_result["key"] == key
    assert s3_result["bucket"] == bucket


# Feature: tec-processor-image, Property 6: Invalid Parameter Rejection
class TestInvalidParameterRejection:
    """Property 6: Invalid Parameter Rejection.

    Generate invalid parameter values (non-integer offsets, non-positive offsets,
    non-boolean flags, unsupported keys) and assert validate_processing_params
    raises ValueError.

    **Validates: Requirements 4.8, 7.3**
    """

    @settings(max_examples=100)
    @given(
        value=st.one_of(
            st.text(min_size=1, max_size=20),
            st.floats(allow_nan=False, allow_infinity=False),
            st.booleans(),
        )
    )
    def test_non_integer_nav_day_offset_rejected(self, value) -> None:
        """Non-integer NAV_DAY_OFFSET values (strings, floats, booleans) raise ValueError."""
        params = {"NAV_DAY_OFFSET": value}
        with pytest.raises(ValueError):
            validate_processing_params(params)

    @settings(max_examples=100)
    @given(value=st.integers(max_value=0))
    def test_non_positive_nav_day_offset_rejected(self, value: int) -> None:
        """Non-positive NAV_DAY_OFFSET values (0, negative integers) raise ValueError."""
        params = {"NAV_DAY_OFFSET": value}
        with pytest.raises(ValueError):
            validate_processing_params(params)

    @settings(max_examples=100)
    @given(
        key=st.sampled_from(["SAVE_PARQUET", "SAVE_CSV", "SAVE_STATIC_PLOTS", "SAVE_INTERACTIVE_PLOTS"]),
        value=st.one_of(
            st.text(min_size=1, max_size=10).filter(lambda s: s.lower() in ("true", "false", "yes", "no", "1", "0")),
            st.integers(),
        ),
    )
    def test_non_boolean_save_flags_rejected(self, key: str, value) -> None:
        """Non-boolean save flag values (strings like "true", integers like 1 or 0) raise ValueError."""
        params = {key: value}
        with pytest.raises(ValueError):
            validate_processing_params(params)

    @settings(max_examples=100)
    @given(
        key=st.text(
            alphabet=string.ascii_lowercase + string.digits + "_",
            min_size=1,
            max_size=30,
        ).filter(
            lambda k: k not in {"NAV_DAY_OFFSET", "SAVE_PARQUET", "SAVE_CSV", "SAVE_STATIC_PLOTS", "SAVE_INTERACTIVE_PLOTS"}
        )
    )
    def test_unsupported_parameter_keys_rejected(self, key: str) -> None:
        """Unsupported parameter keys (random strings not in the allowed set) raise ValueError."""
        params = {key: "anything"}
        with pytest.raises(ValueError):
            validate_processing_params(params)
