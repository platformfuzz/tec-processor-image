"""Lambda handler for processor."""

from __future__ import annotations

import json
import os
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

try:
    import boto3
except Exception:  # pragma: no cover - local test compatibility
    boto3 = None  # type: ignore[assignment]

from .calibration import run_calibration
from .csv_io import rows_to_csv_bytes
from .json_io import rows_to_json_bytes
from .logic import (
    _FORMAT_CONTENT_TYPE,
    _FORMAT_EXTENSION,
    _FORMAT_PRIORITY,
    _matches_prefix,
    compute_nav_doy,
    derive_output_key,
    extract_message_payload,
    merge_parameters,
    parse_raw_key,
    require_output_format,
)
from .nav import download_nav_file
from .parquet_io import rows_to_parquet_bytes
from .plot_io import rows_to_interactive_plot_bytes, rows_to_static_plot_bytes


def _log(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, default=str))


def _parse_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _default_params_from_env() -> dict[str, Any]:
    return {
        "NAV_DAY_OFFSET": int(os.getenv("NAV_DAY_OFFSET", "1")),
        "SAVE_PARQUET": _parse_bool(os.getenv("SAVE_PARQUET", "true"), True),
        "SAVE_CSV": _parse_bool(os.getenv("SAVE_CSV", "false"), False),
        "SAVE_JSON": _parse_bool(os.getenv("SAVE_JSON", "false"), False),
        "SAVE_STATIC_PLOTS": _parse_bool(os.getenv("SAVE_STATIC_PLOTS", "false"), False),
        "SAVE_INTERACTIVE_PLOTS": _parse_bool(os.getenv("SAVE_INTERACTIVE_PLOTS", "false"), False),
    }


def _update_job_status(
    ddb: Any,
    table_name: str | None,
    job_id: str | None,
    status: str,
    output_key: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    if not table_name or not job_id:
        return

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


def _safe_update_job_status(ddb: Any, **kwargs: Any) -> None:
    trace_id = kwargs.pop("trace_id", None)
    try:
        _update_job_status(ddb, **kwargs)
    except Exception as exc:
        _log(
            {
                "trace_id": trace_id or "unknown",
                "outcome": "ddb_update_warning",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "job_id": kwargs.get("job_id"),
            }
        )


def _write_observation_file(raw_bytes: bytes, raw_key: str, directory: Path) -> Path:
    filename = raw_key.rsplit("/", 1)[-1]
    path = directory / filename
    path.write_bytes(raw_bytes)
    return path


def _process_message(
    *,
    s3: Any,
    ddb: Any,
    source_bucket: str,
    source_prefix: str,
    destination_bucket: str,
    destination_prefix: str,
    jobs_table_name: str | None,
    defaults: dict[str, Any],
    payload: dict[str, Any],
    started_at: float,
    message_id: str,
) -> None:
    trace_id = payload.get("trace_id") or str(uuid.uuid4())
    raw_key = payload["key"]
    payload_bucket = payload.get("bucket")
    if payload_bucket and payload_bucket != source_bucket:
        raise ValueError(f"Unexpected source bucket: {payload_bucket}")
    if not _matches_prefix(raw_key, source_prefix):
        raise ValueError(f"Raw key does not match SOURCE_PREFIX '{source_prefix}': {raw_key}")

    year, doy, station, source_stem = parse_raw_key(raw_key)
    params = merge_parameters(defaults, payload.get("parameters"))
    require_output_format(params)
    nav_year, nav_doy = compute_nav_doy(doy, year, params["NAV_DAY_OFFSET"])
    job_id = payload.get("job_id")

    _safe_update_job_status(
        ddb,
        table_name=jobs_table_name,
        job_id=job_id,
        status="processing",
        trace_id=trace_id,
    )

    _serializers = {
        "SAVE_PARQUET": lambda rows: rows_to_parquet_bytes(rows),
        "SAVE_CSV": lambda rows: rows_to_csv_bytes(rows),
        "SAVE_JSON": lambda rows: rows_to_json_bytes(rows),
        "SAVE_STATIC_PLOTS": lambda rows: rows_to_static_plot_bytes(rows, station, year, doy),
        "SAVE_INTERACTIVE_PLOTS": lambda rows: rows_to_interactive_plot_bytes(rows, station, year, doy),
    }

    with tempfile.TemporaryDirectory(prefix="processor-") as tmp_dir:
        work_dir = Path(tmp_dir)
        response = s3.get_object(Bucket=source_bucket, Key=raw_key)
        raw_bytes = response["Body"].read()
        if not raw_bytes:
            raise ValueError(f"Raw object is empty: {raw_key}")

        observation_path = _write_observation_file(raw_bytes, raw_key, work_dir)
        navigation_path = download_nav_file(nav_year, nav_doy, work_dir / "nav")
        rows = run_calibration(observation_path, navigation_path, station, params)

        primary_key: str | None = None
        for fmt in _FORMAT_PRIORITY:
            if not params.get(fmt):
                continue
            ext = _FORMAT_EXTENSION[fmt]
            output_key = derive_output_key(station, year, doy, source_stem, destination_prefix, extension=ext)
            if primary_key is None:
                primary_key = output_key
            body = _serializers[fmt](rows)
            s3.put_object(
                Bucket=destination_bucket,
                Key=output_key,
                Body=body,
                ContentType=_FORMAT_CONTENT_TYPE[fmt],
            )

    _safe_update_job_status(
        ddb,
        table_name=jobs_table_name,
        job_id=job_id,
        status="completed",
        output_key=primary_key,
        trace_id=trace_id,
    )
    _log(
        {
            "trace_id": trace_id,
            "station": station,
            "year": year,
            "doy": doy,
            "output_key": primary_key,
            "row_count": len(rows),
            "duration_ms": int((time.time() - started_at) * 1000),
            "outcome": "success",
            "message_id": message_id,
        }
    )


def handler(event: dict, context: object) -> dict:
    """Process SQS batch and report partial failures."""
    started_at = time.time()
    source_bucket = os.getenv("SOURCE_BUCKET")
    source_prefix = os.getenv("SOURCE_PREFIX")
    destination_bucket = os.getenv("DESTINATION_BUCKET")
    destination_prefix = os.getenv("DESTINATION_PREFIX")
    jobs_table_name = os.getenv("JOBS_TABLE_NAME")
    if not source_bucket:
        raise RuntimeError("SOURCE_BUCKET is required")
    if not source_prefix:
        raise RuntimeError("SOURCE_PREFIX is required")
    if not destination_bucket:
        raise RuntimeError("DESTINATION_BUCKET is required")
    if not destination_prefix:
        raise RuntimeError("DESTINATION_PREFIX is required")

    if boto3 is None:
        raise RuntimeError("boto3 is required")
    s3 = boto3.client("s3")
    ddb = boto3.client("dynamodb")
    defaults = _default_params_from_env()
    failures: list[dict[str, str]] = []

    for record in event.get("Records", []):
        message_id = record.get("messageId", "unknown")
        trace_id = str(uuid.uuid4())
        try:
            payload = extract_message_payload(record.get("body", "{}"))
            if payload.get("_s3_test_event"):
                _log(
                    {
                        "trace_id": str(uuid.uuid4()),
                        "duration_ms": int((time.time() - started_at) * 1000),
                        "outcome": "skipped",
                        "reason": "s3_test_event",
                        "message_id": message_id,
                    }
                )
                continue

            trace_id = payload.get("trace_id") or trace_id
            _process_message(
                s3=s3,
                ddb=ddb,
                source_bucket=source_bucket,
                source_prefix=source_prefix,
                destination_bucket=destination_bucket,
                destination_prefix=destination_prefix,
                jobs_table_name=jobs_table_name,
                defaults=defaults,
                payload=payload,
                started_at=started_at,
                message_id=message_id,
            )
        except Exception as exc:
            failures.append({"itemIdentifier": message_id})
            try:
                payload = extract_message_payload(record.get("body", "{}"))
                trace_id = payload.get("trace_id") or trace_id
                _safe_update_job_status(
                    ddb,
                    table_name=jobs_table_name,
                    job_id=payload.get("job_id"),
                    status="failed",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    trace_id=trace_id,
                )
            except Exception:
                pass

            _log(
                {
                    "trace_id": trace_id,
                    "duration_ms": int((time.time() - started_at) * 1000),
                    "outcome": "error",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "stack_trace": traceback.format_exc(),
                    "message_id": message_id,
                }
            )

    return {"batchItemFailures": failures}
