from __future__ import annotations

import json

import pytest

from processor import main as runtime_main


def test_container_mode_processes_event_json(monkeypatch, capsys):
    monkeypatch.setenv("PROCESSOR_MODE", "container")
    monkeypatch.setenv("SOURCE_BUCKET", "lake-source")
    monkeypatch.setenv("SOURCE_PREFIX", "raw/rinexhourly")
    monkeypatch.setenv("DESTINATION_BUCKET", "lake-dest")
    monkeypatch.setenv("DESTINATION_PREFIX", "processed/tec")
    monkeypatch.delenv("CONTAINER_SKIP_S3_WRITE", raising=False)
    monkeypatch.delenv("CONTAINER_PUBLIC_S3_READ", raising=False)
    monkeypatch.setattr(
        runtime_main,
        "process_record",
        lambda payload, source_bucket, _source_prefix, destination_bucket, _destination_prefix, _defaults, **_kwargs: (
            f"processed/{source_bucket}/{destination_bucket}/{payload['key']}.parquet"
        ),
    )
    monkeypatch.setattr(
        runtime_main,
        "extract_message_payload",
        lambda body: json.loads(body),
    )
    monkeypatch.setattr("sys.argv", ["processor.main", "--event-json", '{"key":"raw/rinexhourly/2024/150/auck1500.24o"}'])

    rc = runtime_main.main()
    assert rc == 0
    output = capsys.readouterr().out.strip()
    payload = json.loads(output)
    assert payload["outcome"] == "success"
    assert payload["output_key"].endswith(".parquet")
    assert payload["s3_write_performed"] is True


def test_container_mode_skip_s3_write(monkeypatch, capsys):
    monkeypatch.setenv("PROCESSOR_MODE", "container")
    monkeypatch.setenv("SOURCE_BUCKET", "geonet-open-data")
    monkeypatch.setenv("SOURCE_PREFIX", "gnss/rinexhourly")
    monkeypatch.setenv("DESTINATION_BUCKET", "my-processed-bucket")
    monkeypatch.setenv("DESTINATION_PREFIX", "processed/tec")
    monkeypatch.setenv("CONTAINER_SKIP_S3_WRITE", "true")
    monkeypatch.setenv("CONTAINER_PUBLIC_S3_READ", "true")

    captured: dict = {}

    def _mock_process_record(
        payload, source_bucket, source_prefix, destination_bucket, destination_prefix, _defaults, **kwargs
    ):
        captured["source_bucket"] = source_bucket
        captured["source_prefix"] = source_prefix
        captured["destination_bucket"] = destination_bucket
        captured["destination_prefix"] = destination_prefix
        captured["kwargs"] = kwargs
        return "processed/station=aukt/year=2026/doy=175/sample.parquet"

    monkeypatch.setattr(runtime_main, "process_record", _mock_process_record)
    monkeypatch.setattr(runtime_main, "_container_s3_client", lambda: object())
    monkeypatch.setattr(runtime_main, "extract_message_payload", lambda body: json.loads(body))
    monkeypatch.setattr(
        "sys.argv",
        [
            "processor.main",
            "--event-json",
            '{"key":"gnss/rinexhourly/2026/175/AUKT00NZL_R_20261750000_01H_30S_MO.rnx.gz"}',
        ],
    )

    rc = runtime_main.main()
    assert rc == 0
    output = json.loads(capsys.readouterr().out.strip())
    assert output["outcome"] == "success"
    assert output["s3_write_performed"] is False
    assert captured["source_bucket"] == "geonet-open-data"
    assert captured["source_prefix"] == "gnss/rinexhourly"
    assert captured["destination_bucket"] == "my-processed-bucket"
    assert captured["destination_prefix"] == "processed/tec"
    assert captured["kwargs"]["write_output"] is False
    assert captured["kwargs"]["s3_client"] is not None


def test_shell_mode_disabled_by_default(monkeypatch):
    monkeypatch.setenv("PROCESSOR_MODE", "shell")
    monkeypatch.delenv("ENABLE_DEBUG_SHELL", raising=False)
    monkeypatch.setattr("sys.argv", ["processor.main"])

    with pytest.raises(PermissionError, match="disabled"):
        runtime_main.main()


def test_shell_mode_enabled_execs_shell(monkeypatch):
    calls: list[tuple[str, list[str]]] = []

    def _fake_exec(program: str, args: list[str]):
        calls.append((program, args))
        raise SystemExit(0)

    monkeypatch.setenv("PROCESSOR_MODE", "shell")
    monkeypatch.setenv("ENABLE_DEBUG_SHELL", "true")
    monkeypatch.setenv("DEBUG_SHELL_PATH", "/bin/bash")
    monkeypatch.setattr("sys.argv", ["processor.main"])
    monkeypatch.setattr(runtime_main.os, "execvp", _fake_exec)

    with pytest.raises(SystemExit):
        runtime_main.main()
    assert calls == [("/bin/bash", ["/bin/bash"])]


def test_lambda_mode_execs_awslambdaric(monkeypatch):
    calls: list[tuple[str, list[str]]] = []

    def _fake_exec(program: str, args: list[str]):
        calls.append((program, args))
        raise SystemExit(0)

    monkeypatch.setenv("PROCESSOR_MODE", "lambda")
    monkeypatch.setattr("sys.argv", ["processor.main"])
    monkeypatch.setattr(runtime_main.os, "execvp", _fake_exec)

    with pytest.raises(SystemExit):
        runtime_main.main()
    assert calls
    assert calls[0][1][1:3] == ["-m", "awslambdaric"]
