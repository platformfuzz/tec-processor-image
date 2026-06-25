# tec-processor-image

Lambda container image that calibrates GNSS RINEX observations into TEC Parquet output.

The processor dependency stack (PyTECGg, polars, scipy, numba, numpy, pyarrow) exceeds AWS Lambda's 250 MB zip/layer limit. Packaging as a container image (up to 10 GB) is required.

## Processing flow

For each SQS record the handler:

1. Normalizes the message body (direct JSON, S3 event, or SNS-wrapped S3 event).
2. Parses the raw object key (`raw/rinexhourly/{year}/{doy}/{station}{doy}{hour}.{yy}o`).
3. Fetches the matching BKG BRDC navigation file for `observation_doy - NAV_DAY_OFFSET`.
4. Runs PyTECGg calibration on the observation and navigation RINEX files.
5. Writes Snappy-compressed Parquet to the data lake when `SAVE_PARQUET` is enabled.
6. Optionally updates DynamoDB job status (`processing` → `completed` or `failed`).
7. Returns partial batch failures for records that raised an exception.

Structured JSON logs are written to stdout (one JSON object per line) with fields such as `trace_id`, `outcome`, `station`, `output_key`, and `duration_ms`.

## Repository layout

```plaintext
tec-processor-image/
├── Dockerfile                 # OCI image (public.ecr.aws/lambda/python:3.13)
├── pyproject.toml             # Package metadata and dependency constraints
├── requirements.lock          # Pinned versions for reproducible container installs
├── src/processor/
│   ├── handler.py             # Lambda entry point (CMD: processor.handler.handler)
│   ├── logic.py               # Key parsing, payload normalization, orchestration
│   ├── nav.py                 # BKG BRDC navigation file download
│   ├── calibration.py         # PyTECGg wrapper
│   ├── parquet_io.py          # Parquet encoding and S3 write helpers
│   └── logging.py             # Structured logging utilities
├── tests/                     # Unit and property tests (pytest + hypothesis)
└── .github/workflows/
    ├── ci.yml                 # PR checks via actionsforge reusables
    └── release.yml            # GHCR publish on main and semver tags
```

## Runtime

| Item | Value |
| --- | --- |
| Base image | `public.ecr.aws/lambda/python:3.13` |
| Handler | `processor.handler.handler` |
| Python | 3.13 (PyTECGg has no `cp314` wheels yet; see [Python 3.14 gate](#python-314-gate)) |
| Platform | `linux/amd64` |

### Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DATA_LAKE_BUCKET` | yes | — | S3 bucket for raw RINEX input and processed Parquet output |
| `JOBS_TABLE_NAME` | no | — | DynamoDB table for job status updates (`job_id` partition key) |
| `NAV_DAY_OFFSET` | no | `1` | Days before observation DOY to fetch navigation data |
| `SAVE_PARQUET` | no | `true` | Write calibrated rows as Parquet (required output format today) |
| `SAVE_CSV` | no | `false` | Not supported in Lambda image (raises if enabled without Parquet) |
| `SAVE_STATIC_PLOTS` | no | `false` | Not supported in Lambda image |
| `SAVE_INTERACTIVE_PLOTS` | no | `false` | Not supported in Lambda image |

Per-message `parameters` in the SQS body override the env defaults for the keys above.

### IAM permissions

The Lambda execution role needs at minimum:

- `s3:GetObject` on `raw/rinexhourly/*` keys in the data lake bucket
- `s3:PutObject` on `processed/station=*/*` keys in the data lake bucket
- `dynamodb:UpdateItem` on the jobs table (when `JOBS_TABLE_NAME` is set)
- Outbound HTTPS to `igs.bkg.bund.de` for navigation file download

`boto3` is provided by the AWS Lambda runtime and is not bundled as an application dependency.

## Data contract

### Input object key

Raw RINEX files must use this key pattern:

```plaintext
raw/rinexhourly/{year}/{doy}/{station}{doy}{hour}.{yy}o
```

Example: `raw/rinexhourly/2024/150/auck1500.24o` (station `auck`, year 2024, DOY 150).

### Output object key

```plaintext
processed/station={station}/year={year}/doy={doy}/{source_stem}.parquet
```

Example: `processed/station=auck/year=2024/doy=150/auck1500.parquet`

### Parquet schema

Eleven columns, Snappy compression, UTC `epoch` timestamps:

`epoch`, `sv`, `id_arc`, `lat_ipp`, `lon_ipp`, `azi`, `ele`, `bias`, `stec`, `vtec`, `veq`

### SQS message formats

The handler accepts three body shapes:

**Direct processor message** (optional fields shown):

```json
{
  "key": "raw/rinexhourly/2024/150/auck1500.24o",
  "job_id": "optional-uuid",
  "trace_id": "optional-trace",
  "parameters": {
    "NAV_DAY_OFFSET": 1,
    "SAVE_PARQUET": true
  }
}
```

**S3 event notification** — standard `Records[0].s3` structure; `bucket` and URL-decoded `key` are extracted.

**SNS-wrapped S3 event** — SNS envelope with JSON `Message` containing the S3 event.

S3 `TestEvent` messages are acknowledged and skipped (`outcome: skipped`).

The full platform data contract (including upstream ingest schemas) lives in the monorepo at `event-driven-serverless-platform-demo/docs/DATA_CONTRACT.md`.

## Dependencies

Direct dependencies (`pyproject.toml`):

- `pytecgg >= 1.3.0` (Python `< 3.14`)
- `pyarrow >= 23.0.1`
- `polars >= 1.5.0`

PyTECGg transitively installs `numpy`, `scipy`, `numba`, `llvmlite`, `pymap3d`, `ppigrf`, `pandas`, `requests`, and others. Exact container versions are pinned in `requirements.lock`.

### Regenerating `requirements.lock`

Resolve on the Lambda base image so wheels match production:

```bash
docker run --rm --entrypoint bash public.ecr.aws/lambda/python:3.13 -c \
  'pip install --no-cache-dir "pytecgg==1.3.0" "pyarrow==23.0.1" "polars==1.18.0" && pip freeze'
```

Copy the installed packages into `requirements.lock` (exclude `boto3`, `botocore`, and other Lambda-runtime packages).

## Local development

Requires Python 3.11–3.13 (`requires-python = ">=3.11,<3.14"`).

```bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/ tests/
```

On a Python 3.14 host, local installs may require `pip install --ignore-requires-python -e ".[dev]"` for validation only; CI and the container image target 3.13.

## Build image

```bash
docker build -t tec-processor-image .
```

The image installs from `requirements.lock`, copies `src/` into `${LAMBDA_TASK_ROOT}`, and sets `CMD ["processor.handler.handler"]`.

## CI/CD

### Pull requests (`.github/workflows/ci.yml`)

Calls [`actionsforge/actions`](https://github.com/actionsforge/actions) reusable `python-image-pr-checks.yml`:

- Markdown lint and commit message conformance
- Python 3.13 lint and test (`pytest tests/ -v`)
- Docker build validation with Trivy (`trivy-vuln-type: library` — scans application dependencies only; OS packages in the AWS Lambda base image are AWS-managed)

### Release (`.github/workflows/release.yml`)

Publishes to GHCR on pushes to `main`, semver tags (`v*`), and `workflow_dispatch`. Markdown-only changes do not trigger a release (`paths-ignore`).

Tags produced by `docker/metadata-action`:

| Trigger | Example tag |
| --- | --- |
| `main` branch | `latest`, `sha-<full-commit>` |
| Semver tag `v1.2.3` | `1.2.3` |

Image reference: `ghcr.io/<owner>/tec-processor-image:<tag>` (owner/repo lowercased).

```bash
docker pull ghcr.io/platformfuzz/tec-processor-image:latest
```

Trivy runs before push and fails on `CRITICAL` findings.

## Deploy-time promotion to ECR

AWS Lambda `package_type = Image` requires an ECR image URI. Promote from GHCR at deploy time:

### ECR prerequisites

```bash
aws ecr create-repository --repository-name tec-processor-image --region <region>
```

### Promote GHCR → ECR

```bash
docker pull ghcr.io/platformfuzz/tec-processor-image:<tag>
docker tag ghcr.io/platformfuzz/tec-processor-image:<tag> \
  <account>.dkr.ecr.<region>.amazonaws.com/tec-processor-image:<tag>
aws ecr get-login-password --region <region> \
  | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker push <account>.dkr.ecr.<region>.amazonaws.com/tec-processor-image:<tag>
```

### Update Terraform

Point the monorepo `processor_image_uri` variable at the promoted ECR URI:

```bash
terraform apply \
  -var="processor_image_uri=123456789012.dkr.ecr.us-east-1.amazonaws.com/tec-processor-image:1.2.3"
```

## Smoke test

After deployment:

1. Ensure a valid RINEX file exists in the data lake at the key referenced in the payload.
2. Create `event.json`:

```json
{
  "Records": [
    {
      "messageId": "smoke-test-001",
      "body": "{\"key\": \"raw/rinexhourly/2024/150/auck1500.24o\"}"
    }
  ]
}
```

3. Invoke the Lambda:

```bash
aws lambda invoke \
  --function-name tec-processor \
  --payload file://event.json \
  --cli-binary-format raw-in-base64-out \
  response.json
cat response.json
```

4. Expected success — empty partial failure list:

```json
{"batchItemFailures": []}
```

If `batchItemFailures` is non-empty, check CloudWatch logs for structured entries with `"outcome": "error"`.

## Python 3.14 gate

PyTECGg currently publishes wheels for Python 3.11–3.13 only. This repository targets CPython 3.13 until `cp314` wheels are available on PyPI. Tracking details are in `.kiro/specs/tec-processor-image/requirements.md` (Requirement 16) and `pyproject.toml`.

## Specification

Design, requirements, and implementation tasks for this repository are maintained under `.kiro/specs/tec-processor-image/`.
