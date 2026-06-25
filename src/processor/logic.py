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
from .nav import fetch_nav_file
from .parquet_io import rows_to_parquet_bytes

RAW_KEY_RE = re.compile(r"^raw/rinexhourly/(?P<year>\d{4})/(?P<doy>\d{3})/(?P<filename>[^/]+)$")
ALLOWED_PARAMS = {
    "NAV_DAY_OFFSET",
    "SAVE_PARQUET",
    "SAVE_CSV",
    "SAVE_STATIC_PLOTS",
    "SAVE_INTERACTIVE_PLOTS",
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


def derive_output_key(station: str, year: int, doy: int, source_stem: str) -> str:
    """Return deterministic processed output key."""
    return f"processed/station={station.lower()}/year={year}/doy={doy:03d}/{source_stem}.parquet"


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

    for key in ("SAVE_PARQUET", "SAVE_CSV", "SAVE_STATIC_PLOTS", "SAVE_INTERACTIVE_PLOTS"):
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
    """Ensure at least one supported output format is enabled."""
    if params.get("SAVE_PARQUET"):
        return
    unsupported = [key for key in ("SAVE_CSV", "SAVE_STATIC_PLOTS", "SAVE_INTERACTIVE_PLOTS") if params.get(key)]
    if unsupported:
        raise ValueError(f"Unsupported output formats requested: {', '.join(unsupported)}")
    raise ValueError("No output format enabled: SAVE_PARQUET is false")


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
    bucket: str,
    env_params: dict,
) -> str:
    """
    Process a single normalized SQS record payload.

    Orchestrates the full processing pipeline: key parsing, parameter merging,
    navigation fetch, S3 raw download, calibration, parquet write, and returns
    the output S3 key.

    Args:
        payload: Normalized dict with key, bucket, job_id, trace_id, parameters
        bucket: DATA_LAKE_BUCKET value
        env_params: Environment-derived defaults for processing parameters

    Returns:
        output_key: S3 key of written Parquet file

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
    source_bucket = payload.get("bucket")
    if source_bucket and source_bucket != bucket:
        raise ValueError(f"Unexpected source bucket: {source_bucket}")

    # 3. Parse raw key → year, doy, station, source_stem
    year, doy, station, source_stem = parse_raw_key(raw_key)

    # 4. Merge message parameters over environment defaults
    params = merge_parameters(env_params, payload.get("parameters"))

    # 5. Validate output format
    require_output_format(params)

    # 6. Compute nav year/doy
    nav_day_offset = params["NAV_DAY_OFFSET"]
    nav_year, nav_doy = compute_nav_doy(doy, year, nav_day_offset)

    # 7. Derive deterministic output key
    output_key = derive_output_key(station, year, doy, source_stem)

    # 8. Fetch navigation file
    nav_path = fetch_nav_file(year, doy, nav_day_offset)

    # 9. Download raw RINEX from S3
    if boto3 is None:
        raise ProcessingError("boto3 is required for S3 operations")

    s3 = boto3.client("s3")

    with tempfile.TemporaryDirectory(prefix="processor-") as tmp_dir:
        work_dir = Path(tmp_dir)

        response = s3.get_object(Bucket=bucket, Key=raw_key)
        raw_bytes = response["Body"].read()
        if not raw_bytes:
            raise ProcessingError(f"Raw object is empty: {raw_key}")

        # Write observation file locally
        filename = raw_key.rsplit("/", 1)[-1]
        observation_path = work_dir / filename
        observation_path.write_bytes(raw_bytes)

        # 10. Run calibration
        rows = run_calibration(observation_path, nav_path, station, params)

        if not rows:
            raise CalibrationError("Calibration produced no valid TEC rows")

        # 11. Write parquet to S3
        if params.get("SAVE_PARQUET"):
            parquet_body = rows_to_parquet_bytes(rows)
            try:
                s3.put_object(
                    Bucket=bucket,
                    Key=output_key,
                    Body=parquet_body,
                    ContentType="application/vnd.apache.parquet",
                )
            except Exception as exc:
                raise OutputError(f"S3 put_object failed for key '{output_key}': {exc}") from exc

    return output_key
