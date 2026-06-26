from __future__ import annotations

from pathlib import Path

import pytest

from tools.geonet_samples import (
    AUCK_HOURLY_SAMPLE_KEYS,
    GeoNetSampleUnavailableError,
    download_auck_hourly_sample,
)


class _Body:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _FakeS3:
    def __init__(self, responses: dict[str, bytes]):
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def get_object(self, *, Bucket: str, Key: str):  # noqa: N803 - boto3 API shape
        self.calls.append((Bucket, Key))
        if Key not in self.responses:
            raise RuntimeError("NoSuchKey")
        return {"Body": _Body(self.responses[Key])}


def test_download_auck_hourly_sample_uses_first_available_key(tmp_path: Path):
    chosen_key = AUCK_HOURLY_SAMPLE_KEYS[1]
    fake_s3 = _FakeS3({chosen_key: b"rinex-bytes"})

    sample = download_auck_hourly_sample(
        tmp_path,
        s3_client=fake_s3,
    )

    assert sample.key == chosen_key
    assert sample.local_path.exists()
    assert sample.local_path.read_bytes() == b"rinex-bytes"
    assert sample.local_path.name == Path(chosen_key).name


def test_download_auck_hourly_sample_raises_when_none_available(tmp_path: Path):
    fake_s3 = _FakeS3({})

    with pytest.raises(GeoNetSampleUnavailableError):
        download_auck_hourly_sample(tmp_path, s3_client=fake_s3)
