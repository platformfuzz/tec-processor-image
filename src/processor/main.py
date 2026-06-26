"""Dual-mode runtime entrypoint for Lambda and generic containers."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .logic import extract_message_payload, process_record

TRUE_VALUES = {"1", "true", "yes", "on"}


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def _default_params_from_env() -> dict[str, Any]:
    return {
        "NAV_DAY_OFFSET": int(os.getenv("NAV_DAY_OFFSET", "1")),
        "SAVE_PARQUET": _as_bool(os.getenv("SAVE_PARQUET", "true"), True),
        "SAVE_CSV": _as_bool(os.getenv("SAVE_CSV", "false"), False),
        "SAVE_JSON": _as_bool(os.getenv("SAVE_JSON", "false"), False),
        "SAVE_STATIC_PLOTS": _as_bool(os.getenv("SAVE_STATIC_PLOTS", "false"), False),
        "SAVE_INTERACTIVE_PLOTS": _as_bool(os.getenv("SAVE_INTERACTIVE_PLOTS", "false"), False),
    }


def _run_lambda_mode() -> int:
    handler_path = os.getenv("LAMBDA_HANDLER", "processor.handler.handler")
    os.execvp(sys.executable, [sys.executable, "-m", "awslambdaric", handler_path])
    return 0


def _container_s3_client() -> Any | None:
    if not _as_bool(os.getenv("CONTAINER_PUBLIC_S3_READ", "false"), False):
        return None
    try:
        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config
    except Exception as exc:
        raise RuntimeError(
            "CONTAINER_PUBLIC_S3_READ=true requires boto3 and botocore support"
        ) from exc
    return boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "ap-southeast-2"),
        config=Config(signature_version=UNSIGNED),
    )


def _read_payload_text(event_json: str | None, event_file: str | None) -> str:
    if event_json:
        return event_json
    if event_file:
        with open(event_file, encoding="utf-8") as f:
            return f.read()
    if not sys.stdin.isatty():
        text = sys.stdin.read().strip()
        if text:
            return text
    raise ValueError("Provide --event-json, --event-file, or stdin payload for container mode")


def _run_container_mode(event_json: str | None, event_file: str | None) -> int:
    source_bucket = os.getenv("SOURCE_BUCKET")
    source_prefix = os.getenv("SOURCE_PREFIX")
    destination_bucket = os.getenv("DESTINATION_BUCKET")
    destination_prefix = os.getenv("DESTINATION_PREFIX")
    if not source_bucket:
        raise RuntimeError("SOURCE_BUCKET is required for container mode")
    if not source_prefix:
        raise RuntimeError("SOURCE_PREFIX is required for container mode")
    if not destination_bucket:
        raise RuntimeError("DESTINATION_BUCKET is required for container mode")
    if not destination_prefix:
        raise RuntimeError("DESTINATION_PREFIX is required for container mode")

    payload_text = _read_payload_text(event_json, event_file)
    payload = extract_message_payload(payload_text)
    if payload.get("_s3_test_event"):
        print(json.dumps({"outcome": "skipped", "reason": "s3_test_event"}))
        return 0

    write_output = not _as_bool(os.getenv("CONTAINER_SKIP_S3_WRITE", "false"), False)
    s3_client = _container_s3_client()
    output_key = process_record(
        payload,
        source_bucket,
        source_prefix,
        destination_bucket,
        destination_prefix,
        _default_params_from_env(),
        s3_client=s3_client,
        write_output=write_output,
    )
    print(
        json.dumps(
            {
                "outcome": "success",
                "output_key": output_key,
                "s3_write_performed": write_output,
            }
        )
    )
    return 0


def _run_shell_mode() -> int:
    if not _as_bool(os.getenv("ENABLE_DEBUG_SHELL", "false"), False):
        raise PermissionError("Debug shell is disabled. Set ENABLE_DEBUG_SHELL=true to enable")
    shell = os.getenv("DEBUG_SHELL_PATH", "/bin/bash")
    os.execvp(shell, [shell])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="TEC processor runtime entrypoint")
    parser.add_argument("--mode", choices=["lambda", "container", "shell"])
    parser.add_argument("--event-json", help="Container mode: raw body payload as JSON text")
    parser.add_argument("--event-file", help="Container mode: file containing payload JSON")
    args = parser.parse_args()

    mode = args.mode or os.getenv("PROCESSOR_MODE", "lambda").strip().lower()
    if mode == "lambda":
        return _run_lambda_mode()
    if mode == "container":
        return _run_container_mode(args.event_json, args.event_file)
    if mode == "shell":
        return _run_shell_mode()
    raise ValueError(f"Unsupported PROCESSOR_MODE: {mode}")


if __name__ == "__main__":  # pragma: no cover - CLI wrapper
    raise SystemExit(main())
