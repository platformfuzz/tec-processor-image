"""Pure logic for Processor Lambda."""

from __future__ import annotations

import json
import re
import tempfile
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

try:
    import boto3
except Exception:  # pragma: no cover - local test compatibility
    boto3 = None  # type: ignore[assignment]

from . import CalibrationError, OutputError, ProcessingError
from .calibration import run_calibration
from .csv_io import rows_to_csv_bytes
from .json_io import rows_to_json_bytes
from .nav import fetch_nav_file
from .parquet_io import rows_to_parquet_bytes
from .plot_io import rows_to_interactive_plot_bytes, rows_to_static_plot_bytes

RAW_KEY_RE = re.compile(
    r"^(?:raw|gnss)/rinexhourly/(?P<year>\d{4})/(?P<doy>\d{3})/(?P<filename>[^/]+)$"
)
ALLOWED_PARAMS = {
    "NAV_DAY_OFFSET",
    "SAVE_PARQUET",
    "SAVE_CSV",
    "SAVE_JSON",
    "SAVE_STATIC_PLOTS",
    "SAVE_INTERACTIVE_PLOTS",
}

# Priority order for primary output key selection when multiple formats enabled
_FORMAT_PRIORITY = ["SAVE_PARQUET", "SAVE_CSV", "SAVE_JSON", "SAVE_STATIC_PLOTS", "SAVE_INTERACTIVE_PLOTS"]
_FORMAT_EXTENSION = {
    "SAVE_PARQUET": "parquet",
    "SAVE_CSV": "csv",
    "SAVE_JSON": "json",
    "SAVE_STATIC_PLOTS": "png",
    "SAVE_INTERACTIVE_PLOTS": "html",
}
_FORMAT_CONTENT_TYPE = {
    "SAVE_PARQUET": "application/vnd.apache.parquet",
    "SAVE_CSV": "text/csv",
    "SAVE_JSON": "application/json",
    "SAVE_STATIC_PLOTS": "image/png",
    "SAVE_INTERACTIVE_PLOTS": "text/html",
}


def parse_raw_key(key: str) -> tuple[int, int, str, str]:
    """Parse raw key and return (year, doy, station, source_stem)."""
    match = RAW_KEY_RE.match(key)
    if not match:
        raise ValueError(f"Malformed raw key: {key}")

    year = int(match.group("year"))
    doy = int(match.group("doy"))
    if doy < 1 or doy > 366:
        raise ValueError(f"Invalid DOY in key: {doy}")

    filename = match.group("filename")
    source_stem = filename.rsplit(".", 1)[0]
    station = source_stem[:4]
    if len(station) != 4 or not station.isalpha():
        raise ValueError(f"Invalid station extracted from filename: {filename}")

    return year, doy, station.lower(), source_stem


def _normalize_prefix(prefix: str) -> str:
    """Return slash-normalized S3 key prefix without leading/trailing slash."""
    cleaned = prefix.strip().strip("/")
    if not cleaned:
        raise ValueError("Prefix must not be empty")
    return cleaned


def _matches_prefix(key: str, prefix: str) -> bool:
    normalized = _normalize_prefix(prefix)
    return key == normalized or key.startswith(normalized + "/")


def derive_output_key(
    station: str,
    year: int,
    doy: int,
    source_stem: str,
    destination_prefix: str,
    extension: str = "parquet",
) -> str:
    """Return deterministic output key under destination prefix.

    The ``extension`` parameter controls the file suffix (default ``parquet``
    for backward compatibility). Pass ``csv``, ``json``, ``png``, or ``html``
    for other formats.
    """
    normalized_prefix = _normalize_prefix(destination_prefix)
    return (
        f"{normalized_prefix}/station={station.lower()}/year={year}/"
        f"doy={doy:03d}/{source_stem}.{extension}"
    )


def _days_in_year(year: int) -> int:
    jan_1 = date(year, 1, 1)
    next_jan_1 = date(year + 1, 1, 1)
    return (next_jan_1 - jan_1).days


def compute_nav_doy(observation_doy: int, observation_year: int, offset: int) -> tuple[int, int]:
    """Compute navigation day-of-year with year rollback."""
    if offset <= 0:
        raise ValueError("NAV_DAY_OFFSET must be positive")
    if observation_doy < 1 or observation_doy > 366:
        raise ValueError("observation_doy must be between 1 and 366")

    nav_year = observation_year
    nav_doy = observation_doy - offset
    while nav_doy < 1:
        nav_year -= 1
        nav_doy += _days_in_year(nav_year)
    return nav_year, nav_doy


def validate_processing_params(params: dict) -> dict:
    """Validate processor parameter payload types."""
    unknown_keys = set(params) - ALLOWED_PARAMS
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise ValueError(f"Unsupported processing parameters: {unknown}")

    validated = dict(params)
    nav_offset = validated.get("NAV_DAY_OFFSET")
    if nav_offset is not None:
        if isinstance(nav_offset, bool) or not isinstance(nav_offset, int):
            raise ValueError(f"Invalid NAV_DAY_OFFSET: {nav_offset}")
        if nav_offset <= 0:
            raise ValueError(f"Invalid NAV_DAY_OFFSET: {nav_offset}")

    for key in ("SAVE_PARQUET", "SAVE_CSV", "SAVE_JSON", "SAVE_STATIC_PLOTS", "SAVE_INTERACTIVE_PLOTS"):
        value = validated.get(key)
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"Invalid {key}: {value}")
    return validated


def merge_parameters(env_defaults: dict, message_overrides: dict | None) -> dict:
    """Merge defaults with message overrides and validate allowed keys/types."""
    base = validate_processing_params(env_defaults)
    if not message_overrides:
        return dict(base)

    overrides = validate_processing_params(message_overrides)
    merged = dict(base)
    merged.update(overrides)
    return merged


def require_output_format(params: dict) -> None:
    """Ensure at least one output format flag is enabled."""
    for flag in _FORMAT_PRIORITY:
        if params.get(flag):
            return
    raise ValueError(
        "No output format enabled: set at least one of "
        + ", ".join(_FORMAT_PRIORITY)
    )


def extract_message_payload(record_body: str) -> dict[str, Any]:
    """Normalize an SQS body into the processor queue message shape."""
    body: Any = json.loads(record_body) if record_body else {}

    for _ in range(3):
        if isinstance(body, str):
            body = json.loads(body)
        else:
            break

    if isinstance(body, dict) and "Message" in body and isinstance(body["Message"], str):
        body = json.loads(body["Message"])

    if not isinstance(body, dict):
        raise ValueError(f"Unsupported queue message type: {type(body).__name__}")

    if body.get("Event") == "s3:TestEvent" and body.get("Service") == "Amazon S3":
        return {"_s3_test_event": True}

    records = body.get("Records")
    if isinstance(records, list) and records:
        s3_record = records[0]
        if s3_record.get("eventSource") != "aws:s3":
            raise ValueError(f"Unsupported event source: {s3_record.get('eventSource')}")

        s3 = s3_record.get("s3") or {}
        bucket = (s3.get("bucket") or {}).get("name")
        key = (s3.get("object") or {}).get("key")
        if not bucket or not key:
            raise ValueError("S3 event record missing bucket name or object key")

        return {
            "bucket": bucket,
            "key": unquote_plus(key),
            "event_time": s3_record.get("eventTime"),
            "parameters": None,
            "job_id": None,
            "trace_id": None,
        }

    if "key" in body:
        return body

    raise ValueError("Queue message missing required 'key' field and is not a recognized S3 event")


def _log(payload: dict[str, Any]) -> None:
    """Emit structured JSON log to stdout."""
    print(json.dumps(payload, default=str))


def update_job_status(
    table_name: str | None,
    job_id: str | None,
    status: str,
    output_key: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    """Update DynamoDB job record with status and optional result fields.

    Skips the update when table_name or job_id is not provided.
    """
    if not table_name or not job_id:
        return

    if boto3 is None:  # pragma: no cover
        return

    ddb = boto3.client("dynamodb")

    update_expression = "SET #status = :status, updated_at = :updated_at"
    expression_names = {"#status": "status"}
    expression_values: dict[str, Any] = {
        ":status": {"S": status},
        ":updated_at": {"S": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
    }

    if output_key:
        update_expression += ", output_key = :output_key"
        expression_values[":output_key"] = {"S": output_key}
    if error_type:
        update_expression += ", error_type = :error_type"
        expression_values[":error_type"] = {"S": error_type}
    if error_message:
        update_expression += ", error_message = :error_message"
        expression_values[":error_message"] = {"S": error_message}

    ddb.update_item(
        TableName=table_name,
        Key={"job_id": {"S": job_id}},
        UpdateExpression=update_expression,
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values,
    )


def safe_update_job_status(
    table_name: str | None,
    job_id: str | None,
    status: str,
    trace_id: str | None = None,
    **kwargs: Any,
) -> None:
    """Update job status without raising on DynamoDB errors.

    Wraps update_job_status in try/except. On error, logs a warning with
    outcome="ddb_update_warning" but never raises — isolating DDB failures
    from record processing.
    """
    if not table_name or not job_id:
        return

    try:
        update_job_status(table_name, job_id, status, **kwargs)
    except Exception as exc:
        _log(
            {
                "trace_id": trace_id or "unknown",
                "outcome": "ddb_update_warning",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "job_id": job_id,
            }
        )


def process_record(
    payload: dict,
    source_bucket: str,
    source_prefix: str,
    destination_bucket: str,
    destination_prefix: str,
    env_params: dict,
    *,
    s3_client: Any | None = None,
    write_output: bool = True,
) -> str:
    """
    Process a single normalized SQS record payload.

    Orchestrates key parsing, parameter merging, nav fetch, source S3 read,
    calibration, optional destination S3 write, and returns output key.

    Args:
        payload: Normalized dict with key, bucket, job_id, trace_id, parameters
        source_bucket: S3 bucket used for input object reads
        source_prefix: Required source key prefix (validation contract)
        destination_bucket: S3 bucket used for output object writes
        destination_prefix: Required destination key prefix for outputs
        env_params: Environment-derived defaults for processing parameters
        s3_client: Optional S3 client override (for testing/public-read modes)
        write_output: When False, skip S3 output write after successful processing

    Returns:
        output_key: Primary output key (first enabled format by priority order).

    Raises:
        ProcessingError (or subclass) on any failure
    """
    # Generate or propagate trace_id
    trace_id = payload.get("trace_id") or str(uuid.uuid4())  # noqa: F841

    # 1. Extract raw key
    raw_key = payload.get("key")
    if not raw_key:
        raise ValueError("Payload missing required 'key' field")

    # 2. Bucket mismatch check
    payload_bucket = payload.get("bucket")
    if payload_bucket and payload_bucket != source_bucket:
        raise ValueError(f"Unexpected source bucket: {payload_bucket}")
    if not _matches_prefix(raw_key, source_prefix):
        raise ValueError(f"Raw key does not match SOURCE_PREFIX '{source_prefix}': {raw_key}")

    # 3. Parse raw key → year, doy, station, source_stem
    year, doy, station, source_stem = parse_raw_key(raw_key)

    # 4. Merge message parameters over environment defaults
    params = merge_parameters(env_params, payload.get("parameters"))

    # 5. Validate output format
    require_output_format(params)

    # 6. Compute nav year/doy
    nav_day_offset = params["NAV_DAY_OFFSET"]
    nav_year, nav_doy = compute_nav_doy(doy, year, nav_day_offset)

    # 7. Fetch navigation file
    nav_path = fetch_nav_file(year, doy, nav_day_offset)

    # 8. Download raw RINEX from S3
    if boto3 is None:
        raise ProcessingError("boto3 is required for S3 operations")

    s3 = s3_client or boto3.client("s3")

    primary_key: str | None = None

    with tempfile.TemporaryDirectory(prefix="processor-") as tmp_dir:
        work_dir = Path(tmp_dir)

        response = s3.get_object(Bucket=source_bucket, Key=raw_key)
        raw_bytes = response["Body"].read()
        if not raw_bytes:
            raise ProcessingError(f"Raw object is empty: {raw_key}")

        filename = raw_key.rsplit("/", 1)[-1]
        observation_path = work_dir / filename
        observation_path.write_bytes(raw_bytes)

        # 9. Run calibration
        rows = run_calibration(observation_path, nav_path, station, params)

        if not rows:
            raise CalibrationError("Calibration produced no valid TEC rows")

        # 10. Write all enabled output formats (independent if-checks, same as PyTECGg Batch Calibrator)
        _serializers = {
            "SAVE_PARQUET": lambda: rows_to_parquet_bytes(rows),
            "SAVE_CSV": lambda: rows_to_csv_bytes(rows),
            "SAVE_JSON": lambda: rows_to_json_bytes(rows),
            "SAVE_STATIC_PLOTS": lambda: rows_to_static_plot_bytes(rows, station, year, doy),
            "SAVE_INTERACTIVE_PLOTS": lambda: rows_to_interactive_plot_bytes(rows, station, year, doy),
        }
        for fmt in _FORMAT_PRIORITY:
            if not params.get(fmt):
                continue
            ext = _FORMAT_EXTENSION[fmt]
            output_key = derive_output_key(station, year, doy, source_stem, destination_prefix, extension=ext)
            if primary_key is None:
                primary_key = output_key
            if write_output:
                body = _serializers[fmt]()
                try:
                    s3.put_object(
                        Bucket=destination_bucket,
                        Key=output_key,
                        Body=body,
                        ContentType=_FORMAT_CONTENT_TYPE[fmt],
                    )
                except Exception as exc:
                    raise OutputError(f"S3 put_object failed for key '{output_key}': {exc}") from exc

    if primary_key is None:
        raise OutputError("No output format written")
    return primary_key
