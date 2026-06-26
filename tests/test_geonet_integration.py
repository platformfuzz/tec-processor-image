from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools.geonet_samples import download_auck_hourly_sample
from tools.local_geonet_runner import run_local_auck_sample

pytestmark = pytest.mark.integration_geonet


def _require_live_geonet() -> None:
    if os.getenv("RUN_GEONET_INTEGRATION") != "1":
        pytest.skip("Set RUN_GEONET_INTEGRATION=1 to run live GeoNet integration tests")


def test_download_auck_sample_from_geonet_bucket(tmp_path: Path):
    _require_live_geonet()
    sample = download_auck_hourly_sample(tmp_path)
    assert sample.local_path.exists()
    assert sample.local_path.stat().st_size > 0
    assert sample.local_path.name.lower().startswith(("auck", "aukt"))


def test_local_auck_runner_writes_parquet_output(tmp_path: Path):
    _require_live_geonet()
    pytest.importorskip("pytecgg")
    pytest.importorskip("pyarrow")

    output_path = run_local_auck_sample(tmp_path)
    assert output_path.exists()
    assert output_path.suffix == ".parquet"
    assert output_path.stat().st_size > 0
