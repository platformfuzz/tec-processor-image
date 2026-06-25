"""Unit tests for processor.logging structured logging utility."""

from __future__ import annotations

import json

from processor.logging import log_entry, log_error, log_skipped, log_success


def test_log_entry_emits_single_line_json(capsys: object) -> None:
    """log_entry emits valid single-line JSON to stdout."""
    log_entry(foo="bar", count=42)
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    lines = captured.out.strip().split("\n")
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed == {"foo": "bar", "count": 42}


def test_log_entry_handles_non_serializable_types(capsys: object) -> None:
    """log_entry uses default=str for non-JSON-serializable values."""
    from pathlib import Path

    log_entry(path=Path("/tmp/test"))
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    parsed = json.loads(captured.out.strip())
    assert parsed["path"] == "/tmp/test"


def test_log_success_includes_required_fields(capsys: object) -> None:
    """log_success emits all fields required by Req 13.2, 13.3."""
    log_success(
        trace_id="trace-123",
        message_id="msg-456",
        station="auck",
        year=2024,
        doy=150,
        duration_ms=1234,
        row_count=100,
        output_key="processed/station=auck/year=2024/doy=150/auck1500.parquet",
    )
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    parsed = json.loads(captured.out.strip())

    assert parsed["trace_id"] == "trace-123"
    assert parsed["message_id"] == "msg-456"
    assert parsed["station"] == "auck"
    assert parsed["year"] == 2024
    assert parsed["doy"] == 150
    assert parsed["outcome"] == "success"
    assert parsed["duration_ms"] == 1234
    assert parsed["row_count"] == 100
    assert parsed["output_key"] == "processed/station=auck/year=2024/doy=150/auck1500.parquet"


def test_log_success_accepts_extra_fields(capsys: object) -> None:
    """log_success passes through additional keyword arguments."""
    log_success(
        trace_id="t",
        message_id="m",
        station="test",
        year=2024,
        doy=1,
        duration_ms=0,
        row_count=0,
        output_key="k",
        custom_field="extra",
    )
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    parsed = json.loads(captured.out.strip())
    assert parsed["custom_field"] == "extra"


def test_log_error_includes_required_fields(capsys: object) -> None:
    """log_error emits all fields required by Req 13.4."""
    log_error(
        trace_id="trace-err",
        message_id="msg-err",
        duration_ms=500,
        error_type="CalibrationError",
        error_message="No valid TEC rows",
        stack_trace="Traceback (most recent call last):\n  ...",
    )
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    parsed = json.loads(captured.out.strip())

    assert parsed["trace_id"] == "trace-err"
    assert parsed["message_id"] == "msg-err"
    assert parsed["outcome"] == "error"
    assert parsed["duration_ms"] == 500
    assert parsed["error_type"] == "CalibrationError"
    assert parsed["error_message"] == "No valid TEC rows"
    assert parsed["stack_trace"] == "Traceback (most recent call last):\n  ..."


def test_log_error_accepts_extra_fields(capsys: object) -> None:
    """log_error passes through additional keyword arguments."""
    log_error(
        trace_id="t",
        message_id="m",
        duration_ms=0,
        error_type="E",
        error_message="msg",
        stack_trace="tb",
        station="test",
    )
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    parsed = json.loads(captured.out.strip())
    assert parsed["station"] == "test"


def test_log_skipped_default_reason(capsys: object) -> None:
    """log_skipped emits outcome=skipped with reason=s3_test_event by default."""
    log_skipped(message_id="msg-skip")
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    parsed = json.loads(captured.out.strip())

    assert parsed["message_id"] == "msg-skip"
    assert parsed["outcome"] == "skipped"
    assert parsed["reason"] == "s3_test_event"


def test_log_skipped_custom_reason(capsys: object) -> None:
    """log_skipped can use a custom reason."""
    log_skipped(message_id="msg-x", reason="maintenance_window")
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    parsed = json.loads(captured.out.strip())

    assert parsed["reason"] == "maintenance_window"


def test_log_skipped_accepts_extra_fields(capsys: object) -> None:
    """log_skipped passes through additional keyword arguments."""
    log_skipped(message_id="m", trace_id="t-123", duration_ms=10)
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    parsed = json.loads(captured.out.strip())
    assert parsed["trace_id"] == "t-123"
    assert parsed["duration_ms"] == 10


def test_all_outputs_are_valid_json(capsys: object) -> None:
    """All log helpers produce output parseable by json.loads()."""
    log_entry(a=1)
    log_success(
        trace_id="t",
        message_id="m",
        station="s",
        year=2024,
        doy=1,
        duration_ms=0,
        row_count=0,
        output_key="k",
    )
    log_error(
        trace_id="t",
        message_id="m",
        duration_ms=0,
        error_type="E",
        error_message="e",
        stack_trace="s",
    )
    log_skipped(message_id="m")

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    lines = [line for line in captured.out.strip().split("\n") if line]
    assert len(lines) == 4
    for line in lines:
        parsed = json.loads(line)
        assert isinstance(parsed, dict)
