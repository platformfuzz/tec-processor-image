"""Local-only helpers for fetching fixed GeoNet hourly sample files.

This module is intentionally not used by Lambda runtime code paths.
It supports local integration validation against a small, stable AUCK sample set.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config
except Exception:  # pragma: no cover - import guard for lightweight environments
    boto3 = None  # type: ignore[assignment]
    UNSIGNED = None  # type: ignore[assignment]
    Config = None  # type: ignore[assignment]

GEONET_OPEN_DATA_BUCKET = "geonet-open-data"

# Fixed AUCK/AUKT sample candidates (few keys, local integration only).
AUCK_HOURLY_SAMPLE_KEYS = (
    "gnss/rinexhourly/2026/175/AUKT00NZL_R_20261750000_01H_30S_MO.rnx.gz",
    "gnss/rinexhourly/2026/175/AUCK00NZL_R_20261750000_01H_30S_MO.rnx.gz",
    "gnss/rinexhourly/2026/176/AUKT00NZL_R_20261760000_01H_30S_MO.rnx.gz",
)


class GeoNetSampleUnavailableError(RuntimeError):
    """Raised when no requested GeoNet sample key can be fetched."""


@dataclass(frozen=True)
class GeoNetSample:
    """Metadata for a downloaded sample file."""

    bucket: str
    key: str
    local_path: Path


def _public_s3_client() -> Any:
    if boto3 is None or Config is None or UNSIGNED is None:  # pragma: no cover
        raise RuntimeError("boto3 and botocore are required to fetch GeoNet samples")
    return boto3.client(
        "s3",
        region_name="ap-southeast-2",
        config=Config(signature_version=UNSIGNED),
    )


def _is_missing_key_error(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    code = str((response or {}).get("Error", {}).get("Code", "")).lower()
    return code in {"nosuchkey", "404", "notfound", "keynotfounderror", "nosuchbucket"}


def _discover_recent_auck_keys(
    client: Any,
    bucket: str,
    *,
    max_years: int = 2,
    max_days_per_year: int = 14,
    max_keys: int = 3,
) -> tuple[str, ...]:
    """Discover a small recent AUCK key set from rolling GeoNet hourly data."""

    try:
        years_resp = client.list_objects_v2(
            Bucket=bucket,
            Prefix="gnss/rinexhourly/",
            Delimiter="/",
            MaxKeys=200,
        )
    except Exception:
        return ()

    year_values: list[int] = []
    for prefix in (years_resp.get("CommonPrefixes") or []):
        value = str(prefix.get("Prefix", "")).rstrip("/").split("/")[-1]
        if value.isdigit():
            year_values.append(int(value))

    discovered: list[str] = []
    for year in sorted(year_values, reverse=True)[:max_years]:
        try:
            days_resp = client.list_objects_v2(
                Bucket=bucket,
                Prefix=f"gnss/rinexhourly/{year}/",
                Delimiter="/",
                MaxKeys=500,
            )
        except Exception:
            continue

        day_values: list[int] = []
        for prefix in (days_resp.get("CommonPrefixes") or []):
            value = str(prefix.get("Prefix", "")).rstrip("/").split("/")[-1]
            if value.isdigit():
                day_values.append(int(value))

        for day in sorted(day_values, reverse=True)[:max_days_per_year]:
            prefix = f"gnss/rinexhourly/{year}/{day:03d}/"
            for station_prefix in ("AUKT", "aukt", "AUCK", "auck"):
                try:
                    listing = client.list_objects_v2(
                        Bucket=bucket,
                        Prefix=prefix + station_prefix,
                        MaxKeys=200,
                    )
                except Exception:
                    continue

                for obj in listing.get("Contents") or []:
                    key = str(obj.get("Key", ""))
                    if key.endswith(".gz"):
                        discovered.append(key)
                        if len(discovered) >= max_keys:
                            return tuple(discovered)

    return tuple(discovered)


def download_sample(
    *,
    keys: Iterable[str],
    destination_dir: Path,
    bucket: str = GEONET_OPEN_DATA_BUCKET,
    s3_client: Any | None = None,
) -> GeoNetSample:
    """Download the first available key from a candidate list."""

    destination_dir.mkdir(parents=True, exist_ok=True)
    client = s3_client or _public_s3_client()

    errors: list[str] = []
    for key in keys:
        try:
            response = client.get_object(Bucket=bucket, Key=key)
            payload = response["Body"].read()
            if not payload:
                errors.append(f"{key}: empty object")
                continue
            local_path = destination_dir / Path(key).name
            local_path.write_bytes(payload)
            return GeoNetSample(bucket=bucket, key=key, local_path=local_path)
        except Exception as exc:  # pragma: no cover - depends on network state
            if _is_missing_key_error(exc):
                errors.append(f"{key}: missing")
            else:
                errors.append(f"{key}: {type(exc).__name__}: {exc}")

    details = "; ".join(errors) if errors else "no keys provided"
    raise GeoNetSampleUnavailableError(
        f"No GeoNet sample key could be fetched from s3://{bucket}: {details}"
    )


def download_auck_hourly_sample(
    destination_dir: Path,
    *,
    bucket: str = GEONET_OPEN_DATA_BUCKET,
    s3_client: Any | None = None,
) -> GeoNetSample:
    """Download one AUCK hourly RINEX sample from a fixed key set."""
    client = s3_client or _public_s3_client()
    candidate_keys = AUCK_HOURLY_SAMPLE_KEYS + _discover_recent_auck_keys(client, bucket)
    return download_sample(
        keys=candidate_keys,
        destination_dir=destination_dir,
        bucket=bucket,
        s3_client=client,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download a fixed AUCK GeoNet hourly sample for local integration tests."
    )
    parser.add_argument(
        "--output-dir",
        default=".tmp/geonet-samples",
        help="Directory where sample files are written.",
    )
    parser.add_argument(
        "--bucket",
        default=GEONET_OPEN_DATA_BUCKET,
        help="GeoNet S3 bucket name (default: geonet-open-data).",
    )
    args = parser.parse_args()

    sample = download_auck_hourly_sample(Path(args.output_dir), bucket=args.bucket)
    print(f"downloaded: s3://{sample.bucket}/{sample.key}")
    print(f"path: {sample.local_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wrapper
    raise SystemExit(main())
