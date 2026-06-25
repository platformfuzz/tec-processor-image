"""Structured JSON logging utility for the TEC processor.

All log entries are emitted as single-line JSON to stdout, parseable by
``json.loads()``.  This module provides typed helpers for the three outcome
categories (success, error, skipped) as well as a low-level ``log_entry``
function for arbitrary structured payloads.

Requirements: 13.1, 13.2, 13.3, 13.4, 13.7
"""

from __future__ import annotations

import json
from typing import Any


def log_entry(**fields: Any) -> None:
    """Emit a single-line JSON log entry to stdout.

    This is the low-level logging primitive.  All values are serialized via
    ``json.dumps(default=str)`` to handle datetime objects, UUIDs, Paths, etc.
    """
    print(json.dumps(fields, default=str))


def log_success(
    *,
    trace_id: str,
    message_id: str,
    station: str,
    year: int,
    doy: int,
    duration_ms: int,
    row_count: int,
    output_key: str,
    **extra: Any,
) -> None:
    """Log a successful record processing outcome.

    Includes all fields required by Requirements 13.2 and 13.3:
    trace_id, message_id, station, year, doy, outcome, duration_ms,
    row_count, output_key.
    """
    log_entry(
        trace_id=trace_id,
        message_id=message_id,
        station=station,
        year=year,
        doy=doy,
        outcome="success",
        duration_ms=duration_ms,
        row_count=row_count,
        output_key=output_key,
        **extra,
    )


def log_error(
    *,
    trace_id: str,
    message_id: str,
    duration_ms: int,
    error_type: str,
    error_message: str,
    stack_trace: str,
    **extra: Any,
) -> None:
    """Log a failed record processing outcome.

    Includes all fields required by Requirement 13.4:
    outcome, error_type, error_message, stack_trace, duration_ms, message_id.
    """
    log_entry(
        trace_id=trace_id,
        message_id=message_id,
        outcome="error",
        duration_ms=duration_ms,
        error_type=error_type,
        error_message=error_message,
        stack_trace=stack_trace,
        **extra,
    )


def log_skipped(
    *,
    message_id: str,
    reason: str = "s3_test_event",
    **extra: Any,
) -> None:
    """Log a skipped record (e.g. S3 test event).

    Includes fields required by Requirement 13.7:
    outcome="skipped", reason="s3_test_event".
    """
    log_entry(
        message_id=message_id,
        outcome="skipped",
        reason=reason,
        **extra,
    )
