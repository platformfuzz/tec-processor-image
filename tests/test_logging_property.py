# Feature: tec-processor-image, Property 13: Structured JSON Log Format
"""Property 13: Structured JSON Log Format.

Generate log data payloads via Hypothesis and assert every emitted line is
valid single-line JSON parseable by ``json.loads()``.

**Validates: Requirements 13.1**
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout

from hypothesis import given, settings
from hypothesis import strategies as st

from processor.logging import log_entry, log_error, log_skipped, log_success

# Strategy for arbitrary log field values (strings, ints, floats, booleans, None)
_field_values = st.one_of(
    st.text(min_size=0, max_size=64),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.none(),
)

# Strategy for field names (identifier-like, lightweight generation)
_field_names = st.from_regex(r"[a-z_][a-z_0-9]{0,20}", fullmatch=True)


@st.composite
def arbitrary_log_fields(draw: st.DrawFn) -> dict:
    """Generate a dict of arbitrary keyword fields for log_entry."""
    # Use dictionaries directly to avoid slow unique-list + per-key draw patterns.
    return draw(st.dictionaries(keys=_field_names, values=_field_values, min_size=0, max_size=8))


class TestStructuredJsonLogFormat:
    """Property 13: Structured JSON Log Format.

    Validates: Requirements 13.1
    """

    @settings(max_examples=100)
    @given(fields=arbitrary_log_fields())
    def test_log_entry_emits_valid_single_line_json(self, fields: dict) -> None:
        """log_entry with arbitrary fields emits exactly one line of valid JSON."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            log_entry(**fields)

        output = buf.getvalue()
        lines = output.splitlines()

        # Exactly one line of output per log_entry call
        assert len(lines) == 1, f"Expected 1 line, got {len(lines)}: {output!r}"

        # The line must be valid JSON
        parsed = json.loads(lines[0])
        assert isinstance(parsed, dict)

    @settings(max_examples=100)
    @given(
        trace_id=st.text(min_size=1, max_size=50),
        message_id=st.text(min_size=1, max_size=50),
        station=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=4, max_size=4),
        year=st.integers(min_value=1990, max_value=2099),
        doy=st.integers(min_value=1, max_value=366),
        duration_ms=st.integers(min_value=0, max_value=600_000),
        row_count=st.integers(min_value=0, max_value=1_000_000),
        output_key=st.text(min_size=1, max_size=200),
    )
    def test_log_success_emits_valid_single_line_json(
        self,
        trace_id: str,
        message_id: str,
        station: str,
        year: int,
        doy: int,
        duration_ms: int,
        row_count: int,
        output_key: str,
    ) -> None:
        """log_success with generated valid arguments emits exactly one valid JSON line."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            log_success(
                trace_id=trace_id,
                message_id=message_id,
                station=station,
                year=year,
                doy=doy,
                duration_ms=duration_ms,
                row_count=row_count,
                output_key=output_key,
            )

        output = buf.getvalue()
        lines = output.splitlines()

        assert len(lines) == 1, f"Expected 1 line, got {len(lines)}: {output!r}"

        parsed = json.loads(lines[0])
        assert isinstance(parsed, dict)
        assert parsed["outcome"] == "success"

    @settings(max_examples=100)
    @given(
        trace_id=st.text(min_size=1, max_size=50),
        message_id=st.text(min_size=1, max_size=50),
        duration_ms=st.integers(min_value=0, max_value=600_000),
        error_type=st.text(min_size=1, max_size=100),
        error_message=st.text(min_size=0, max_size=500),
        stack_trace=st.text(min_size=0, max_size=1000),
    )
    def test_log_error_emits_valid_single_line_json(
        self,
        trace_id: str,
        message_id: str,
        duration_ms: int,
        error_type: str,
        error_message: str,
        stack_trace: str,
    ) -> None:
        """log_error with generated valid arguments emits exactly one valid JSON line."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            log_error(
                trace_id=trace_id,
                message_id=message_id,
                duration_ms=duration_ms,
                error_type=error_type,
                error_message=error_message,
                stack_trace=stack_trace,
            )

        output = buf.getvalue()
        lines = output.splitlines()

        assert len(lines) == 1, f"Expected 1 line, got {len(lines)}: {output!r}"

        parsed = json.loads(lines[0])
        assert isinstance(parsed, dict)
        assert parsed["outcome"] == "error"

    @settings(max_examples=100)
    @given(
        message_id=st.text(min_size=1, max_size=50),
        reason=st.text(min_size=1, max_size=50),
    )
    def test_log_skipped_emits_valid_single_line_json(
        self,
        message_id: str,
        reason: str,
    ) -> None:
        """log_skipped with generated valid arguments emits exactly one valid JSON line."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            log_skipped(message_id=message_id, reason=reason)

        output = buf.getvalue()
        lines = output.splitlines()

        assert len(lines) == 1, f"Expected 1 line, got {len(lines)}: {output!r}"

        parsed = json.loads(lines[0])
        assert isinstance(parsed, dict)
        assert parsed["outcome"] == "skipped"

    @settings(max_examples=100)
    @given(fields=arbitrary_log_fields())
    def test_log_entry_no_multiline_output(self, fields: dict) -> None:
        """Each log_entry call produces exactly one line (no embedded newlines in output)."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            log_entry(**fields)

        output = buf.getvalue()
        # Output should end with exactly one newline (from print)
        assert output.endswith("\n")
        # Stripping the trailing newline, there should be no other newlines
        assert "\n" not in output.rstrip("\n")
