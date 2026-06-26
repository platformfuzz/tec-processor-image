# Requirements Document

## Introduction

This document specifies the requirements for the `tec-processor-image` repository — a standalone OCI container image that runs the TEC (Total Electron Content) processor in dual-mode: AWS Lambda-compatible execution and generic container execution. The image encapsulates PyTECGg-based RINEX calibration, PyTECGg-native BKG navigation retrieval, and multi-format output to S3 (Parquet, CSV, JSON, static PNG plot, interactive HTML plot).

The image exists because the processor's dependency stack (PyTECGg, polars, scipy, numba, numpy, pyarrow) exceeds AWS Lambda's 250 MB zip/layer limit (~881 MB unzipped). A container image (up to 10 GB) is required.

### Current State

The repository now contains implemented processor code, Docker packaging, tests, and workflow orchestration. It has been overhauled as a greenfield standalone project with:

- Runtime modules under `src/processor/` (Lambda and container entry support)
- Local-only tooling under `tools/` (not imported by runtime handlers)
- Dependency lock file and CI checks in place
- Kiro requirements/tasks aligned to current implemented architecture

### Source of Truth

Application logic is extracted from the monorepo `event-driven-serverless-platform-demo`:

| Artifact | Monorepo path |
| --- | --- |
| Handler | `services/processor/src/processor/handler.py` |
| Modules | `logic.py`, `nav.py`, `calibration.py`, `parquet_io.py` |
| Package metadata | `services/processor/pyproject.toml` |
| Tests | `services/processor/tests/` |
| Dockerfile (adapt COPY paths) | `services/processor/Dockerfile` |
| Build script reference | `scripts/build-push-processor-image.sh` |
| Data contract | `docs/DATA_CONTRACT.md` |

The standalone Dockerfile SHALL install the package from the repository root `pyproject.toml` (not the monorepo's nested `services/processor/` path).

### Integration Reference

The monorepo Terraform processing module references this image:

```hcl
package_type = "Image"
image_uri    = var.processor_image_uri  # account.dkr.ecr.region.amazonaws.com/tec-processor-image:tag
```

Terraform does not build the image. This repository builds and publishes to **GHCR** (`ghcr.io/<org>/tec-processor-image`). Lambda deploy still consumes an **ECR** `image_uri` — promote from GHCR to ECR at deploy time.

## Glossary

- **Processor_Image**: The OCI-compliant container image built from this repository's Dockerfile; published to GHCR by CI and promoted to ECR for Lambda deployment.
- **GHCR**: GitHub Container Registry — where this repository publishes built images (`ghcr.io/<org>/tec-processor-image`).
- **ECR**: Amazon Elastic Container Registry — where the Lambda function pulls the image at runtime (`image_uri` in Terraform).
- **PyTECGg**: A Python library (`pytecgg >= 1.3.0`, Python 3.11–3.13 only) that performs GPS Total Electron Content calibration from RINEX observation and navigation data.
- **BKG**: Bundesamt für Kartographie und Geodäsie — the German federal agency hosting BRDC navigation files at `https://igs.bkg.bund.de/root_ftp/IGS/BRDC`.
- **Parquet**: Apache Parquet columnar storage format used for calibrated TEC output.
- **SQS_Partial_Batch_Failure**: AWS SQS partial batch failure reporting where the Lambda returns `batchItemFailures` containing only the `itemIdentifier` values of failed messages, allowing successful messages to be deleted from the queue.
- **RINEX**: Receiver Independent Exchange Format — the standard format for raw GNSS observation data ingested by the processor.
- **Raw_Key**: The S3 object key for input RINEX data, matching `{SOURCE_PREFIX}/{year}/{doy}/{filename}` where `year` is a four-digit integer, `doy` is a three-digit day-of-year (001–366), and `filename` yields a four-character alphabetic station prefix.
- **source_stem**: The filename portion of the raw key without its final dot-separated extension, used to derive the output Parquet key as `{source_stem}.parquet`.
- **Handler**: The Lambda entry point function at `processor.handler.handler` invoked by the Lambda runtime.
- **NAV_DAY_OFFSET**: A positive integer (default 1) specifying how many days before the observation DOY to fetch BKG navigation files. May be overridden per message via the `parameters` object.
- **Process_Queue**: The SQS queue that triggers the processor Lambda with RINEX processing messages.
- **Jobs_Table**: The DynamoDB table tracking reprocessing job status (`queued` → `processing` → `completed` or `failed`).
- **JOBS_TABLE_NAME**: Optional Lambda environment variable naming the DynamoDB Jobs_Table; when unset, job status updates are skipped.

## Requirements

### Requirement 1: Container Image Build

**User Story:** As a platform engineer, I want a reproducible Docker image build from a Dockerfile at the repository root, so that the processor can be deployed as a Lambda container image.

#### Acceptance Criteria (Requirement 1)

1. THE Processor_Image SHALL be buildable from a single `Dockerfile` located at the repository root using `docker build .`
2. THE Processor_Image SHALL use a neutral Python base image (`python:3.13-slim`) and support Lambda via `awslambdaric`, while remaining runnable on generic container platforms
3. THE Processor_Image SHALL install the application package via `pip install` from the repository root `pyproject.toml`
4. THE Processor_Image SHALL set the CMD to `["processor.handler.handler"]`
5. THE Processor_Image SHALL reference only pinned dependency versions and a pinned base image tag so that identical source produces identical installed packages
6. THE Processor_Image SHALL NOT contain secrets, credentials, or AWS access keys in any image layer
7. THE Processor_Image SHALL include application source code under `src/processor/` importable as `processor.*`
8. THE Processor_Image SHALL NOT exceed 10 GB uncompressed size (the AWS Lambda container image limit)

#### Correctness Properties (Requirement 1)

- **Reproducibility**: Given identical source code, Dockerfile, and pinned dependency versions, two consecutive builds SHALL produce images with the same installed package set.
- **No Secrets**: No image layer SHALL contain strings matching AWS access key or secret key patterns.

### Requirement 2: Dependency Management

**User Story:** As a developer, I want dependencies pinned in `pyproject.toml` with documented transitive dependencies, so that builds are reproducible and the dependency tree is understood.

#### Acceptance Criteria (Requirement 2)

1. THE Processor_Image SHALL install dependencies from a `pyproject.toml` file at the repository root
2. THE Processor_Image SHALL declare `pytecgg >= 1.3.0` as a direct dependency with a Python version marker restricting install to Python `< 3.14`
3. THE Processor_Image SHALL declare `pyarrow`, `polars`, `matplotlib`, and `plotly` as direct dependencies with version pins
4. THE Processor_Image SHALL document in the README that PyTECGg transitively pulls scipy, numba, and numpy
5. THE Processor_Image SHALL pin all direct dependency versions in `pyproject.toml` using lower-and-upper-bound specifiers (e.g., `>= x.y, < z.0`) where practical
6. THE Processor_Image SHALL declare `requires-python = ">=3.11,<3.14"` to reflect PyTECGg runtime compatibility
7. THE Repository SHALL include a lock file (e.g., `requirements.lock` or `uv.lock`) to record exact resolved versions for fully reproducible installs
8. THE Processor_Image SHALL include `boto3` as an application dependency so the same image works in both Lambda and non-Lambda container runtimes

#### Correctness Properties (Requirement 2)

- **Version Constraint Satisfaction**: All installed packages SHALL satisfy the constraints declared in `pyproject.toml`.
- **Transitive Completeness**: The installed dependency set SHALL include scipy, numba, and numpy (pulled transitively by pytecgg).

### Requirement 3: Handler Entry Point and SQS Integration

**User Story:** As a platform operator, I want the handler to process SQS messages containing RINEX S3 keys and report partial batch failures, so that successful messages are acknowledged independently from failed ones.

#### Acceptance Criteria (Requirement 3)

1. WHEN an SQS event is received, THE Handler SHALL iterate over each record in the `Records` array
2. WHEN an SQS record body is a direct processor message containing a `key` field, THE Handler SHALL use that value as the S3 object key for the raw RINEX file
3. WHEN an SQS record body is an S3 event notification (`Records[0].eventSource == "aws:s3"`), THE Handler SHALL extract `bucket` and `key` from the nested S3 event structure, URL-decoding the key
4. WHEN an SQS record body is an SNS notification wrapping an S3 event (`Message` field containing JSON), THE Handler SHALL unwrap the SNS envelope and extract the S3 bucket and key
5. WHEN an SQS record body is an S3 connectivity test event (`Event == "s3:TestEvent"`), THE Handler SHALL skip the record without adding it to `batchItemFailures`
6. WHEN an SQS record body contains optional `job_id`, `trace_id`, `bucket`, or `parameters` fields, THE Handler SHALL propagate those values through the processing pipeline
7. WHEN processing of a record fails, THE Handler SHALL add the record's `messageId` to the `batchItemFailures` response list as an `itemIdentifier` value
8. WHEN processing of a record succeeds, THE Handler SHALL NOT include that record in `batchItemFailures`
9. THE Handler SHALL return a response object with a `batchItemFailures` array containing objects with `itemIdentifier` keys
10. IF an SQS record body contains invalid JSON or cannot be normalized to a payload with a `key` field (and is not an S3 test event), THEN THE Handler SHALL treat the record as a processing failure and include its `messageId` in `batchItemFailures`
11. IF the optional `bucket` field is present and does not match `SOURCE_BUCKET`, THEN THE Handler SHALL treat the record as a processing failure

#### Correctness Properties (Requirement 3)

- **Partial Failure Isolation**: For a batch of N records where K succeed (or are skipped as test events) and N-K fail, the response SHALL contain exactly N-K entries in `batchItemFailures`.
- **No Silent Drops**: Every non-test record in the input `Records` array SHALL either produce a successful output or appear in `batchItemFailures`.

### Requirement 4: Raw Key Parsing and Navigation Fetch

**User Story:** As a platform operator, I want the handler to parse observation metadata from the S3 object key and fetch matching BKG navigation data, so that calibration uses the correct ionospheric context.

#### Acceptance Criteria (Requirement 4)

1. THE Handler SHALL parse `year`, `doy`, `station`, and `source_stem` from the raw key using the pattern `{SOURCE_PREFIX}/{year}/{doy}/{filename}` where `station` is the first four alphabetic characters of the filename stem (lowercased) and `source_stem` is the filename without its final extension
2. IF the raw key does not match the expected pattern, or `doy` is outside 001–366, or the station prefix is not exactly four alphabetic characters, THEN THE Handler SHALL treat the record as a processing failure
3. WHEN year and doy are parsed successfully, THE Handler SHALL compute navigation `(nav_year, nav_doy)` as observation DOY minus `NAV_DAY_OFFSET`, rolling back to the previous calendar year when the result is less than 1
4. THE Handler SHALL fetch the BKG BRDC navigation file from `https://igs.bkg.bund.de/root_ftp/IGS/BRDC/{nav_year}/{nav_doy}/` by listing the directory and selecting a compatible filename (BRDC IGS RINEX 3 or legacy `brdc{doy}0.{yy}p.gz` patterns)
5. IF the BKG directory listing HTTP request returns a non-2xx status code or does not complete within 30 seconds, THEN THE Handler SHALL treat the record as a processing failure
6. IF the BKG navigation file download returns a non-2xx status code or does not complete within 120 seconds, THEN THE Handler SHALL treat the record as a processing failure
7. IF no compatible navigation file exists in the BKG directory, THEN THE Handler SHALL treat the record as a processing failure
8. IF `NAV_DAY_OFFSET` is set to a non-integer value or an integer less than or equal to zero, THEN THE Handler SHALL treat the record as a processing failure

#### Correctness Properties (Requirement 4)

- **Nav Fetch Determinism**: Given a fixed observation year, observation DOY, and `NAV_DAY_OFFSET`, the Handler SHALL always request the same BKG directory URL.
- **Key Parse Determinism**: Given a fixed raw key, `parse_raw_key` SHALL always return the same `(year, doy, station, source_stem)` tuple.

### Requirement 5: RINEX Calibration Processing

**User Story:** As a scientist, I want raw RINEX observations calibrated to TEC using PyTECGg with real BKG navigation data, so that the output represents physically meaningful ionospheric measurements.

#### Acceptance Criteria (Requirement 5)

1. WHEN a valid RINEX observation file is retrieved from S3 and a BKG navigation file is available locally, THE Handler SHALL invoke the PyTECGg calibration pipeline (`read_rinex_obs`, `read_rinex_nav`, linear combinations, satellite coordinates, IPP, arc extraction, TEC, vertical equivalent) to produce calibrated TEC rows
2. THE Handler SHALL filter calibration output to rows with non-null `id_arc_valid`, `stec`, and `vtec`, mapping `id_arc_valid` to integer `id_arc` values
3. IF calibration fails for any reason (empty observations, missing navigation ephemeris, alignment failure, no valid TEC rows), THEN THE Handler SHALL NOT write any output under `DESTINATION_PREFIX` in S3
4. THE Handler SHALL NOT use synthetic, demo, or hardcoded TEC data in any code path
5. IF PyTECGg is not importable (unsupported Python version or missing wheel), THEN THE Handler SHALL treat the record as a processing failure with an explicit calibration error

#### Correctness Properties (Requirement 5)

- **No Output Without Calibration**: If PyTECGg calibration was not successfully invoked, no file SHALL exist under `DESTINATION_PREFIX` for that input.
- **Real Data Only**: Every row in every output Parquet file SHALL trace back to PyTECGg processing of real RINEX observation data.

### Requirement 6: Multi-Format Output

**User Story:** As a data consumer, I want calibrated TEC data written in one or more selectable formats (Parquet, CSV, JSON, static PNG plot, interactive HTML plot) with Hive-style partitioning, so that downstream consumers can choose the most appropriate representation.

#### Acceptance Criteria (Requirement 6)

1. WHEN calibration succeeds, THE Handler SHALL write all enabled output formats to S3 in the same processing run. Enabled formats are determined independently by each flag — multiple flags may be true simultaneously.
2. Each enabled output SHALL be written at `{DESTINATION_PREFIX}/station={station}/year={year}/doy={doy:03d}/{source_stem}.{ext}` where `station` is lowercase and `ext` is the format-specific extension (`.parquet`, `.csv`, `.json`, `.png`, `.html`)
3. THE Handler SHALL derive `source_stem` by removing the final dot-separated extension from the input RINEX filename (e.g., `auck1500.24o` yields `auck1500`)
4. THE Handler SHALL read raw input from `SOURCE_BUCKET` and write all output objects to `DESTINATION_BUCKET`
5. IF no output format flag is enabled, THEN THE Handler SHALL reject the record as a processing failure
6. IF any required split S3 variable (`SOURCE_BUCKET`, `SOURCE_PREFIX`, `DESTINATION_BUCKET`, `DESTINATION_PREFIX`) is not set or is empty, THEN THE Handler SHALL fail the invocation before processing any records
7. THE Handler SHALL write Parquet output as valid Apache Parquet (Snappy, `PAR1` magic bytes), CSV as UTF-8 delimited text, JSON as a UTF-8 JSON array of TEC rows, PNG as a matplotlib-generated static plot, and HTML as a Plotly interactive figure with CDN-linked plotly.js
8. Parquet, CSV, and JSON SHALL contain exactly these 11 fields: `epoch`, `sv`, `id_arc`, `lat_ipp`, `lon_ipp`, `azi`, `ele`, `bias`, `stec`, `vtec`, `veq`
9. WHEN writing output, THE Handler SHALL overwrite any existing object at the deterministic output key (idempotent reprocessing)

#### Correctness Properties (Requirement 6)

- **Format Independence**: Each output format flag is an independent if-check; enabling one format SHALL NOT suppress another enabled format.
- **Shared Partition**: All output formats for a given record SHALL share the same `{DESTINATION_PREFIX}/station=.../year=.../doy=.../` partition path.
- **Path Determinism**: Given a fixed destination prefix, station, year, doy, source_stem, and extension, the output S3 key SHALL always equal the deterministic template.
- **Stem Derivation**: The source_stem SHALL equal the input filename with its final extension removed.

### Requirement 7: Processing Environment Defaults

**User Story:** As a platform operator, I want consistent default processing parameters via environment variables with per-message overrides, so that reprocessing jobs can customize behavior without redeploying the Lambda.

#### Acceptance Criteria (Requirement 7)

1. IF no override parameters are present in the incoming SQS message body, THE Handler SHALL use these environment variable defaults: `NAV_DAY_OFFSET=1`, `SAVE_PARQUET=true`, `SAVE_CSV=false`, `SAVE_JSON=false`, `SAVE_STATIC_PLOTS=false`, `SAVE_INTERACTIVE_PLOTS=false`
2. WHEN a message includes a `parameters` object, THE Handler SHALL merge message-level overrides on top of environment defaults for the allowed keys: `NAV_DAY_OFFSET`, `SAVE_PARQUET`, `SAVE_CSV`, `SAVE_JSON`, `SAVE_STATIC_PLOTS`, `SAVE_INTERACTIVE_PLOTS`
3. IF a message or environment variable contains an unsupported parameter key, a non-integer `NAV_DAY_OFFSET`, a non-positive `NAV_DAY_OFFSET`, or a non-boolean save flag, THEN THE Handler SHALL treat the record as a processing failure
4. IF all output format flags are `false`, THEN THE Handler SHALL treat the record as a processing failure with message "No output format enabled"

#### Correctness Properties (Requirement 7)

- **Override Precedence**: For any allowed parameter present in both environment defaults and message `parameters`, the message value SHALL take precedence.

### Requirement 8: DynamoDB Job Status Updates

**User Story:** As a portal user, I want reprocessing job status reflected in the Jobs_Table, so that I can track job progress and outcomes.

#### Acceptance Criteria (Requirement 8)

1. WHEN `JOBS_TABLE_NAME` is set and a message contains a `job_id`, THE Handler SHALL update the Jobs_Table record to `processing` before calibration begins
2. WHEN processing completes successfully, THE Handler SHALL update the Jobs_Table record to `completed` with the `output_key`
3. WHEN processing fails, THE Handler SHALL update the Jobs_Table record to `failed` with `error_type` and `error_message`
4. IF `JOBS_TABLE_NAME` is unset or the message has no `job_id`, THE Handler SHALL skip DynamoDB updates without failing the record
5. IF a DynamoDB update fails, THE Handler SHALL log a warning (`outcome: ddb_update_warning`) and SHALL NOT fail the calibration or SQS acknowledgment for that reason alone

#### Correctness Properties (Requirement 8)

- **Status Lifecycle**: A job-linked record SHALL transition `processing` → `completed` on success or `processing` → `failed` on calibration failure.

### Requirement 9: No Demo or Synthetic Data

**User Story:** As a platform owner, I want an explicit guarantee that no synthetic TEC generation, JSON-in-Parquet workarounds, or hardcoded station coordinates exist in the image, so that the processor produces only real calibration results.

#### Acceptance Criteria (Requirement 9)

1. THE Processor_Image SHALL NOT include `demo_rows.py` or any module that produces TEC column values (epoch, stec, vtec) without invoking PyTECGg calibration on real RINEX and navigation file inputs
2. IF calibration has not been successfully performed by PyTECGg for a given record, THEN THE Handler SHALL NOT write any file under `DESTINATION_PREFIX` for that record
3. THE Processor_Image SHALL NOT contain hardcoded latitude or longitude literals referenced as a fallback when station metadata is unavailable
4. THE Processor_Image SHALL NOT contain any code path that constructs a TEC DataFrame or record set from static values, random generators, or interpolation without PyTECGg processing of observation data

#### Correctness Properties (Requirement 9)

- **Binary Format Validity**: Every `.parquet` file SHALL be parseable by pyarrow without raising exceptions.

### Requirement 10: CI and GHCR Publishing

**User Story:** As a release engineer, I want thin GitHub Actions workflows that delegate to shared `actionsforge/actions` reusables and publish images to GHCR, so that PR validation and releases follow the same patterns as other platformfuzz `*-image` repositories.

#### Acceptance Criteria (Requirement 10)

1. THE Repository SHALL define `.github/workflows/ci.yml` that calls `actionsforge/actions/.github/workflows/python-image-pr-checks.yml@main` on pull requests
2. THE `ci.yml` workflow SHALL run, via the reusable workflow: markdown lint, commit message conform, Python lint/test (Python 3.13), Docker image build validation (`linux/amd64`), and Trivy scan over HIGH and CRITICAL severities with the failure threshold managed by reusable-workflow inputs
3. THE Repository SHALL define `.github/workflows/release.yml` that calls `actionsforge/actions/.github/workflows/docker-image-publish.yml@main` on pushes to `main` and semver tags matching `v*`
4. THE release workflow SHALL publish to GHCR at `ghcr.io/<org>/tec-processor-image` using `GITHUB_TOKEN` (no AWS OIDC or `AWS_ROLE_ARN` required in this repository)
5. WHEN a git tag matching `v[0-9]+.[0-9]+.[0-9]+` (with optional pre-release suffix) is pushed, THE release workflow SHALL tag the image with the version string without the `v` prefix
6. WHEN a commit is pushed to the `main` branch, THE release workflow SHALL tag the image with the full 40-character git commit SHA and additionally tag the image as `latest`
7. THE release workflow SHALL NOT push the `latest` tag for non-main-branch builds
8. IF the image build, Trivy scan, or push to GHCR fails, THEN THE release workflow SHALL fail

#### Deployment Note

Lambda `package_type = Image` requires an **ECR** URI (`image_uri` in the monorepo Terraform). GHCR is the build artifact registry for this repository; promoting a GHCR image to ECR for Lambda deploy is **out of scope** here and handled at deploy time (manual `docker pull/tag/push`, monorepo script, or a separate infra workflow).

#### Correctness Properties (Requirement 10)

- **Tag Immutability**: A semver-tagged image in GHCR SHALL NOT be overwritten by a subsequent push.
- **Latest Only on Main**: The `latest` tag SHALL only refer to an image built from the `main` branch HEAD.
- **Reusable Delegation**: Consumer workflow files SHALL contain orchestration only; build, test, scan, and push logic SHALL live in `actionsforge/actions` reusable workflows.

### Requirement 11: Lambda Smoke Test

**User Story:** As a developer, I want an optional CI job or documented manual procedure to invoke the Lambda with a sample SQS payload, so that basic end-to-end behavior can be validated after deployment.

#### Acceptance Criteria (Requirement 11)

1. THE Repository SHALL include in the README a documented procedure for invoking the processor Lambda with a sample SQS event payload, including the `aws lambda invoke` command with the function name parameterized
2. THE smoke test documentation SHALL include a sample JSON payload structured as a complete SQS event envelope (with a `Records` array containing at least one record with `messageId` and a JSON `body` containing at minimum a `key` field referencing a valid `{SOURCE_PREFIX}/{year}/{doy}/{filename}` key)
3. WHERE a CI smoke test job is configured, THE CI_Pipeline SHALL invoke the Lambda using the sample payload and verify that the response contains a `batchItemFailures` array with zero entries within 60 seconds
4. THE smoke test documentation SHALL specify the expected success condition: the Lambda returns a JSON response with an empty `batchItemFailures` array

#### Correctness Properties (Requirement 11)

- **Smoke Test Validity**: A successful smoke test invocation SHALL return `{"batchItemFailures": []}` given a valid RINEX input already present in the data lake bucket.

### Requirement 12: Security

**User Story:** As a security engineer, I want the container image to run as non-root where possible, be scanned for vulnerabilities, and contain no baked-in credentials, so that the deployment meets organizational security baselines.

#### Acceptance Criteria (Requirement 12)

1. THE Processor_Image SHALL NOT contain AWS credentials, API keys, or secrets in any image layer; this SHALL be verified by scanning image layers for patterns matching `AKIA[0-9A-Z]{16}` and `aws_secret_access_key`
2. THE Processor_Image SHALL NOT add a `USER root` directive after the application code is installed; where the AWS Lambda base image runs as a non-root user by default, that configuration SHALL be preserved
3. THE CI_Pipeline SHALL perform a container image vulnerability scan using Trivy on PR validation and release builds; PR validation SHALL evaluate HIGH and CRITICAL severities, and release SHALL fail on CRITICAL before push
4. IF the Trivy vulnerability scan reports any CRITICAL severity vulnerabilities, THEN THE CI_Pipeline SHALL fail the build

#### Correctness Properties (Requirement 12)

- **No Baked Credentials**: No image layer SHALL contain strings matching AWS credential patterns.
- **Scan Gate**: No image with CRITICAL vulnerabilities SHALL be pushed to GHCR.

### Requirement 13: Observability and Structured Logging

**User Story:** As an operations engineer, I want structured JSON logs emitted by the handler with trace context and processing metrics, so that I can correlate events and monitor performance in CloudWatch.

#### Acceptance Criteria (Requirement 13)

1. THE Handler SHALL emit all log entries as single-line structured JSON objects to stdout parseable by `json.loads()`
2. THE Handler SHALL include `trace_id`, `station`, `year`, and `doy` fields in success log entries; IF `station`, `year`, or `doy` are not yet determined at the time of logging (e.g., early failures), THEN THE Handler MAY omit those fields
3. WHEN processing of a record completes successfully, THE Handler SHALL log an entry with `outcome` set to `"success"`, `duration_ms` (wall-clock milliseconds), `row_count` (integer number of calibrated rows), `output_key` (the S3 key written), and `message_id`
4. WHEN processing of a record fails, THE Handler SHALL log an entry with `outcome` set to `"error"`, `error_type` (exception class name), `error_message`, `stack_trace` (full Python traceback as a string), `duration_ms`, and `message_id`
5. WHEN a `trace_id` is provided in the SQS message body, THE Handler SHALL propagate that value in all log entries for the corresponding record
6. WHEN no `trace_id` is provided in the SQS message body, THE Handler SHALL generate a UUID v4 string and use it as the `trace_id` for that record
7. WHEN an S3 test event is skipped, THE Handler SHALL log an entry with `outcome` set to `"skipped"` and `reason` set to `"s3_test_event"`

#### Correctness Properties (Requirement 13)

- **Structured Format**: Every line emitted to stdout by the Handler SHALL be valid JSON.
- **Trace Correlation**: All log entries for a single record SHALL share the same `trace_id` value.

### Requirement 14: Unit and Property Tests

**User Story:** As a developer, I want the monorepo test suite migrated to this repository, so that handler behavior is validated without deploying to AWS.

#### Acceptance Criteria (Requirement 14)

1. THE Repository SHALL place tests under `tests/` migrated from `services/processor/tests/` in the monorepo
2. THE test suite SHALL cover: raw key parsing, output key derivation, nav DOY computation with year rollback, message payload normalization (direct, S3 event, SNS envelope, test event), Parquet encoding, BKG nav filename selection, and handler partial-batch-failure behavior
3. THE Repository SHALL include `hypothesis` and `pytest` as dev dependencies for property-based and unit tests
4. THE CI_Pipeline SHALL run the test suite on pull requests via the `python-image-pr-checks` reusable workflow
5. WHEN tests run in CI, THE test suite SHALL NOT require AWS credentials, network access to BKG, or a live PyTECGg installation (calibration and nav download SHALL be mocked or isolated in unit tests)

#### Correctness Properties (Requirement 14)

- **Test Parity**: Every test file in the monorepo `services/processor/tests/` directory SHALL have a corresponding test file in this repository with equivalent coverage.

### Requirement 15: Repository Layout

**User Story:** As a contributor, I want the repository to follow platformfuzz conventions with a predictable layout, so that I can navigate and contribute without additional onboarding.

#### Acceptance Criteria (Requirement 15)

1. THE Repository SHALL place the Dockerfile at the repository root
2. THE Repository SHALL place application source under `src/processor/` with modules: `__init__.py`, `handler.py`, `logic.py`, `nav.py`, `calibration.py`, `parquet_io.py`, `csv_io.py`, `json_io.py`, `plot_io.py`
3. THE Repository SHALL place tests under `tests/`
4. THE Repository SHALL place CI workflows under `.github/workflows/` with at minimum `ci.yml` (PR checks) and `release.yml` (GHCR publish on `main` and semver tags)
5. THE Repository SHALL include a `README.md` containing at minimum: a docker build command, a GHCR pull reference, a note on promoting to ECR for Lambda deploy, a Lambda invocation example, and a reference to the monorepo `docs/DATA_CONTRACT.md` message schema
6. THE Repository SHALL use the MIT license in a `LICENSE` file at the repository root
7. THE Repository SHALL follow the platformfuzz `*-image` naming suffix convention for the repository name
8. THE Repository SHALL place a `pyproject.toml` at the repository root
9. THE Repository SHALL include a `.dockerignore` file at the repository root to exclude tests, CI workflows, and development artifacts from the Docker build context
10. THE Repository SHALL include a `.gitignore` file appropriate for a Python container image project

#### Correctness Properties (Requirement 15)

- **Layout Compliance**: Every file specified in the acceptance criteria SHALL exist at the stated path in the repository.

### Requirement 16: Python Runtime Policy

**User Story:** As a maintainer of this repository, I want an explicit and testable runtime policy, so that runtime, dependency, and CI behavior stay consistent with the accepted Python 3.13 baseline.

#### Acceptance Criteria (Requirement 16)

1. THE Repository SHALL run runtime and CI on Python 3.13
2. THE Repository SHALL keep `requires-python = ">=3.11,<3.14"` and `pytecgg` marker alignment with the accepted Python 3.13 baseline
3. THE Repository SHALL NOT mix runtime and CI Python versions without an explicit approved policy update
4. THE Repository SHALL keep calibration/runtime compatibility messaging consistent with the Python 3.13 baseline

#### Correctness Properties (Requirement 16)

- **Version Cohesion**: Runtime image, dependency constraints, and CI Python version SHALL remain consistent with each other.
- **Baseline Stability**: Runtime behavior and calibration execution SHALL remain validated on the Python 3.13 baseline.

### Requirement 17: Local GeoNet Integration and Runtime Boundary

**User Story:** As a developer, I want local-first validation against real GeoNet hourly RINEX samples while preserving deployed Lambda input-driven behavior, so that we can verify real-world processing without changing production execution semantics.

#### Acceptance Criteria (Requirement 17)

1. THE Repository SHALL provide a local-only helper module for fetching a small fixed sample set of AUCK hourly RINEX files from `s3://geonet-open-data/gnss/rinexhourly/`
2. THE Handler runtime path (`processor.handler.handler`) SHALL remain input-driven from SQS/S3 payload data; it SHALL NOT call the local GeoNet sample fetch helper
3. THE Repository SHALL provide a local command to run download + calibration + parquet generation end-to-end for one AUCK sample
4. THE test suite SHALL include opt-in live integration tests (networked) for GeoNet sample processing, separated from default CI tests
5. THE Repository SHALL document Kiro usage and local tooling workflow for this local-first workflow without introducing runtime dependency on developer-only tooling in Lambda code paths

#### Correctness Properties (Requirement 17)

- **Runtime Boundary Integrity**: Deployable Lambda processing behavior SHALL depend only on event payload and configured environment variables, not on internal sample discovery logic.
- **Local Reproducibility**: Given reachable GeoNet and BKG endpoints, the local AUCK sample workflow SHALL produce a readable Parquet file with the standard output schema.

## Out of Scope

- Ingest service, Query API, Reprocess API, Portal, or Amplify frontend
- Terraform for ECR repository creation (handled manually or in a separate infra repo)
- Query API Parquet reading logic (remains in the monorepo)
- Multi-architecture image builds (amd64 only unless specified otherwise)
- Promoting GHCR images to ECR for Lambda deploy (handled at deploy time or in the monorepo/infra repo)
- Dead-letter queue alarm handling and DLQ-driven job status updates (owned by the monorepo processing module)
