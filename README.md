# tec-processor-image

Dual-mode Python 3.13 container image for TEC processing from GNSS RINEX observations.

This repository builds one OCI image that runs in:

- AWS Lambda image mode (SQS/event-driven handler path), and
- generic container mode (CLI/env-driven single-message processing).

The project uses `pytecgg` as an external dependency and keeps PyTECGg integration behind thin adapters in `src/processor/`.

## Runtime baseline

- Python runtime baseline: `3.13`
- Base image: `python:3.13-slim`
- Lambda compatibility: `awslambdaric`
- Platform target: `linux/amd64`
- Plot output dependencies: `matplotlib`, `plotly` (static PNG and interactive HTML formats)

This repository is intentionally aligned to Python 3.13 runtime behavior and CI checks.

## Quick start

### 1) Install for local development

```bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

### 2) Run local tests

```bash
# Default offline suite (same marker policy as CI)
pytest tests/ -v -m "not integration_geonet"

# Optional live networked integration tests
RUN_GEONET_INTEGRATION=1 pytest tests/ -v -m integration_geonet
```

### 3) Build the image

```bash
docker build -t tec-processor-image .
```

## What the processor does

For each logical message, the processor:

1. Normalizes the payload body (direct JSON, S3 event, or SNS-wrapped S3 event).
2. Validates that the input key is under `SOURCE_PREFIX`, then parses year/doy/station from the key.
3. Resolves nav date via `NAV_DAY_OFFSET` and downloads BRDC nav through PyTECGg downloader APIs.
4. Runs PyTECGg calibration and serializes rows into all enabled output formats.
5. Writes each enabled output to `DESTINATION_BUCKET` under `DESTINATION_PREFIX` and returns per-record failures where applicable.
6. Optionally updates job status in DynamoDB when `JOBS_TABLE_NAME` and `job_id` are provided.

Multiple output format flags may be enabled simultaneously. All outputs share the same destination partition path and differ only by file extension.

Lambda remains input-driven. Runtime code does not perform local sample discovery.

## Runtime modes

Entrypoint is `python -m processor.main`.

- `PROCESSOR_MODE=lambda` (default)  
  Starts Lambda Runtime Interface Client with handler `processor.handler.handler`.
- `PROCESSOR_MODE=container`  
  Processes one payload from `--event-json`, `--event-file`, or stdin.
- `PROCESSOR_MODE=shell`  
  Opens a shell only when `ENABLE_DEBUG_SHELL=true`.

### Container mode: end-to-end with S3 write

Container mode needs AWS credentials when reading from private buckets or writing outputs. Mount your local AWS config and pass the profile into the container:

```bash
export AWS_PROFILE=your-profile
export AWS_REGION=ap-southeast-2

docker run --rm \
  -e PROCESSOR_MODE=container \
  -e AWS_REGION="$AWS_REGION" \
  -e AWS_PROFILE="$AWS_PROFILE" \
  -e AWS_SDK_LOAD_CONFIG=1 \
  -e SOURCE_BUCKET=geonet-open-data \
  -e SOURCE_PREFIX=gnss/rinexhourly \
  -e DESTINATION_BUCKET=your-destination-bucket \
  -e DESTINATION_PREFIX=processed/tec \
  -e SAVE_PARQUET=true \
  -e SAVE_CSV=true \
  -e SAVE_JSON=true \
  -e SAVE_STATIC_PLOTS=true \
  -e SAVE_INTERACTIVE_PLOTS=true \
  -v "$HOME/.aws:/root/.aws" \
  tec-processor-image \
  --event-json '{"key":"gnss/rinexhourly/2026/175/AUKT00NZL_R_20261750000_01H_30S_MO.rnx.gz"}'
```

On success the container prints JSON to stdout, for example:

```json
{"outcome":"success","output_key":"processed/tec/station=aukt/year=2026/doy=175/AUKT00NZL_R_20261750000_01H_30S_MO.rnx.parquet","s3_write_performed":true}
```

`output_key` is the primary output (first enabled format by priority: Parquet, then CSV, JSON, static plot, interactive plot). When multiple flags are enabled, all formats are written under the same partition path with different extensions.

Verify all five objects:

```bash
aws s3 ls "s3://your-destination-bucket/processed/tec/station=aukt/year=2026/doy=175/"
```

Expected files for the example key above:

- `AUKT00NZL_R_20261750000_01H_30S_MO.rnx.parquet`
- `AUKT00NZL_R_20261750000_01H_30S_MO.rnx.csv`
- `AUKT00NZL_R_20261750000_01H_30S_MO.rnx.json`
- `AUKT00NZL_R_20261750000_01H_30S_MO.rnx.png`
- `AUKT00NZL_R_20261750000_01H_30S_MO.rnx.html`

Use a key that already exists in your configured `SOURCE_BUCKET` and matches `SOURCE_PREFIX`.

#### Credentials in container mode

- Mount `~/.aws` read-write (`-v "$HOME/.aws:/root/.aws"`) so SSO cache refresh works.
- Pass `AWS_PROFILE` and `AWS_SDK_LOAD_CONFIG=1` into the container — the host `AWS_PROFILE` is not inherited automatically.
- Do **not** set `CONTAINER_PUBLIC_S3_READ=true` when writing to a private destination bucket. That flag forces an unsigned S3 client for reads, and the same client is used for `put_object`, which causes `AccessDenied` on writes.

Optional: create a temporary writable destination bucket for testing, then force-delete it when done (including contents):

```bash
# Set your region and a globally unique bucket name
export AWS_REGION=ap-southeast-2
export TEST_BUCKET="tec-processor-test-$(date +%s)"

# Create bucket (non-versioned test bucket)
aws s3api create-bucket \
  --bucket "$TEST_BUCKET" \
  --region "$AWS_REGION" \
  --create-bucket-configuration LocationConstraint="$AWS_REGION"

# ...run tests using DESTINATION_BUCKET=$TEST_BUCKET...

# Force delete bucket and all objects inside it
aws s3 rb "s3://$TEST_BUCKET" --force
```

### Container mode: process-only test (no output write)

For public GeoNet testing where you only want to verify processing:

```bash
docker run --rm \
  -e PROCESSOR_MODE=container \
  -e SOURCE_BUCKET=geonet-open-data \
  -e SOURCE_PREFIX=gnss/rinexhourly \
  -e DESTINATION_BUCKET=my-processed-bucket \
  -e DESTINATION_PREFIX=processed/tec \
  -e CONTAINER_PUBLIC_S3_READ=true \
  -e CONTAINER_SKIP_S3_WRITE=true \
  tec-processor-image \
  --event-json '{"key":"gnss/rinexhourly/2026/175/AUKT00NZL_R_20261750000_01H_30S_MO.rnx.gz"}'
```

`CONTAINER_PUBLIC_S3_READ=true` uses unsigned S3 reads for public buckets (GeoNet open data). Use only with `CONTAINER_SKIP_S3_WRITE=true` — unsigned clients cannot write to private buckets.

`CONTAINER_SKIP_S3_WRITE=true` skips all S3 output writes while still running parse, nav fetch, calibration, and serialization for every enabled format.

### Debug shell gate example

```bash
docker run --rm -it \
  -e PROCESSOR_MODE=shell \
  -e ENABLE_DEBUG_SHELL=true \
  tec-processor-image
```

## Environment variables

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `SOURCE_BUCKET` | yes | — | S3 bucket for raw input objects |
| `SOURCE_PREFIX` | yes | — | Required key prefix for raw inputs (e.g. `raw/rinexhourly`) |
| `DESTINATION_BUCKET` | yes | — | S3 bucket for output objects |
| `DESTINATION_PREFIX` | yes | — | Prefix root for output objects (e.g. `processed/tec`) |
| `JOBS_TABLE_NAME` | no | — | DynamoDB table for job status |
| `NAV_DAY_OFFSET` | no | `1` | Navigation day offset |
| `SAVE_PARQUET` | no | `true` | Write Snappy-compressed `.parquet` output |
| `SAVE_CSV` | no | `false` | Write UTF-8 `.csv` output |
| `SAVE_JSON` | no | `false` | Write `.json` output (JSON array of TEC rows) |
| `SAVE_STATIC_PLOTS` | no | `false` | Write `.png` static TEC plot |
| `SAVE_INTERACTIVE_PLOTS` | no | `false` | Write `.html` interactive TEC plot (Plotly CDN) |
| `PROCESSOR_MODE` | no | `lambda` | Runtime mode selector |
| `CONTAINER_PUBLIC_S3_READ` | no | `false` | Unsigned S3 reads for public buckets (container mode only; incompatible with writes) |
| `CONTAINER_SKIP_S3_WRITE` | no | `false` | Skip S3 output writes after processing (container mode only) |
| `ENABLE_DEBUG_SHELL` | no | `false` | Enables shell mode access |
| `DEBUG_SHELL_PATH` | no | `/bin/bash` | Shell binary path in shell mode |

At least one output flag must be `true`. Multiple flags may be enabled simultaneously — all enabled formats are written in the same processing run to the same destination partition.

Per-message `parameters` can override processing flags such as `NAV_DAY_OFFSET` and `SAVE_PARQUET`.

## Data contract

### Input key format

Input `key` must start with `SOURCE_PREFIX/` and include:

```text
{source_prefix}/{year}/{doy}/{filename}
```

Example:

`raw/rinexhourly/2024/150/auck1500.24o` (when `SOURCE_PREFIX=raw/rinexhourly`)

### Output key format

All enabled output formats share the same partition path and differ only by extension:

```text
{destination_prefix}/station={station}/year={year}/doy={doy}/{source_stem}.{ext}
```

| Format | Extension | Content type |
| --- | --- | --- |
| Parquet | `.parquet` | `application/vnd.apache.parquet` |
| CSV | `.csv` | `text/csv` |
| JSON | `.json` | `application/json` |
| Static plot | `.png` | `image/png` |
| Interactive plot | `.html` | `text/html` |

Example (Parquet, `DESTINATION_PREFIX=processed/tec`):

`processed/tec/station=auck/year=2024/doy=150/auck1500.parquet`

### Output schema

All data formats (Parquet, CSV, JSON) contain 11 columns/fields:

`epoch`, `sv`, `id_arc`, `lat_ipp`, `lon_ipp`, `azi`, `ele`, `bias`, `stec`, `vtec`, `veq`

### Accepted SQS body shapes

- Direct processor payload (`key`, optional `job_id`, `trace_id`, `parameters`)
- Standard S3 event notification (`Records[0].s3`)
- SNS-wrapped S3 event (`Message` containing S3 event JSON)

S3 `TestEvent` bodies are acknowledged and skipped.

## Local GeoNet tooling (non-runtime)

Local sample helpers live under `tools/` and are not imported by Lambda runtime paths.

```bash
# Download one fixed AUCK hourly sample candidate
python -m tools.geonet_samples --output-dir .tmp/geonet-samples

# Run local sample -> nav download -> calibration -> parquet output
python -m tools.local_geonet_runner --output-dir .tmp/local-geonet-run --nav-day-offset 1
```

## Repository layout

```text
tec-processor-image/
├── Dockerfile
├── pyproject.toml
├── requirements.lock
├── src/processor/
│   ├── handler.py
│   ├── logic.py
│   ├── nav.py
│   ├── calibration.py
│   ├── parquet_io.py
│   ├── csv_io.py
│   ├── json_io.py
│   ├── plot_io.py
│   ├── logging.py
│   └── main.py
├── tools/
│   ├── geonet_samples.py
│   └── local_geonet_runner.py
├── tests/
└── .github/workflows/
```

## CI/CD

### Pull request checks

`/.github/workflows/ci.yml` runs reusable checks with:

- Python `3.13`
- `pytest tests/ -v -m "not integration_geonet"`
- Trivy library dependency scanning

### Release

`/.github/workflows/release.yml` publishes images to GHCR on `main`, semver tags, and manual dispatch.

Image reference format:

`ghcr.io/<owner>/tec-processor-image:<tag>`

## Deploy-time GHCR -> ECR promotion

Lambda `package_type = Image` requires ECR image URIs.

```bash
aws ecr create-repository --repository-name tec-processor-image --region <region>

docker pull ghcr.io/platformfuzz/tec-processor-image:<tag>
docker tag ghcr.io/platformfuzz/tec-processor-image:<tag> \
  <account>.dkr.ecr.<region>.amazonaws.com/tec-processor-image:<tag>
aws ecr get-login-password --region <region> \
  | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker push <account>.dkr.ecr.<region>.amazonaws.com/tec-processor-image:<tag>
```

## Lambda smoke test

Create `event.json`:

```json
{
  "Records": [
    {
      "messageId": "smoke-test-001",
      "body": "{\"key\":\"raw/rinexhourly/2024/150/auck1500.24o\"}"
    }
  ]
}
```

Invoke:

```bash
aws lambda invoke \
  --function-name tec-processor \
  --payload file://event.json \
  --cli-binary-format raw-in-base64-out \
  response.json
cat response.json
```

Expected success:

```json
{"batchItemFailures":[]}
```

## Specification

Kiro specification artifacts are under `.kiro/specs/tec-processor-image/`.

Requirements-first mode is configured via `.kiro/specs/tec-processor-image/.config.kiro`.
