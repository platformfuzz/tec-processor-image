"""Processor package — TEC processor Lambda container image."""

__version__ = "0.1.0"


# --- Exception hierarchy ---


class ProcessingError(Exception):
    """Base for all per-record processing failures."""


class PayloadError(ProcessingError):
    """Invalid or un-normalizable SQS record body."""


class KeyParseError(ProcessingError):
    """Raw key does not match expected pattern."""


class NavFetchError(ProcessingError):
    """BKG navigation file fetch failed."""


class CalibrationError(ProcessingError):
    """PyTECGg calibration failed or produced no valid rows."""


class OutputError(ProcessingError):
    """S3 Parquet write failed."""


class ParameterError(ProcessingError):
    """Invalid processing parameter value."""


# --- Public API re-exports ---

from .logic import (  # noqa: E402
    compute_nav_doy,
    derive_output_key,
    merge_parameters,
    parse_raw_key,
    process_record,
    require_output_format,
    validate_processing_params,
)

__all__ = [
    "__version__",
    "CalibrationError",
    "KeyParseError",
    "NavFetchError",
    "OutputError",
    "ParameterError",
    "PayloadError",
    "ProcessingError",
    "compute_nav_doy",
    "derive_output_key",
    "merge_parameters",
    "parse_raw_key",
    "process_record",
    "require_output_format",
    "validate_processing_params",
]
