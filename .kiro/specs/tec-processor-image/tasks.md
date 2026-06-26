# Implementation Plan: tec-processor-image

## Overview

This plan implements the standalone OCI container image repository for the TEC processor Lambda. Work proceeds in waves: scaffold the repository structure, port processor code from the monorepo, add unit and property tests, configure CI/CD workflows, and document GHCR publishing plus deploy-time GHCR-to-ECR promotion steps. Multi-format output (Parquet, CSV, JSON, static PNG, interactive HTML) is implemented in Phase 12.

## Tasks

- [x] 1. Scaffold repository structure and package metadata
  - [x] 1.1 Create Dockerfile and .dockerignore
    - Create `Dockerfile` at repository root using `python:3.13-slim` base image with Lambda compatibility via `awslambdaric`
    - COPY `pyproject.toml` and `requirements.lock` first for layer caching, then COPY `src/`
    - Set CMD to `["processor.handler.handler"]`
    - Create `.dockerignore` excluding `tests/`, `.github/`, `.kiro/`, `*.md`, `.git/`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 15.1, 15.9_

  - [x] 1.2 Create pyproject.toml with pinned dependencies
    - Define `[project]` with name `tec-processor-image`, `requires-python = ">=3.11,<3.14"`
    - Declare direct dependencies: `pytecgg >= 1.3.0` with `python_version < "3.14"` marker, `pyarrow >= 18.0.0`, `polars >= 1.5.0`
    - Do NOT include `boto3` as a dependency (provided by Lambda runtime)
    - Add `[project.optional-dependencies] dev` with `pytest >= 8.0`, `hypothesis >= 6.100`, `ruff >= 0.5`, `moto[s3,dynamodb] >= 5.0`
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.8, 14.3, 15.8_

  - [x] 1.3 Create src/processor package with __init__.py
    - Create `src/processor/__init__.py` with package version and exception hierarchy
    - Define `ProcessingError`, `PayloadError`, `KeyParseError`, `NavFetchError`, `CalibrationError`, `OutputError`, `ParameterError`
    - _Requirements: 15.2_

  - [x] 1.4 Create requirements.lock placeholder
    - Create `requirements.lock` with a comment explaining it records exact resolved versions
    - Populate with pinned versions of all direct and transitive dependencies
    - _Requirements: 2.7, 1.5_

  - [x] 1.5 Create .gitignore for Python container image project
    - Include standard Python ignores: `__pycache__/`, `*.pyc`, `.venv/`, `dist/`, `*.egg-info/`
    - Include Docker ignores: `.docker/`
    - _Requirements: 15.10_

  - [x] 1.6 Update README.md with build instructions and architecture
    - Add docker build command, GHCR pull reference, ECR promotion note
    - Add Lambda invocation example with sample SQS payload
    - Document that PyTECGg transitively pulls scipy, numba, numpy
    - Reference monorepo `docs/DATA_CONTRACT.md` for message schema
    - Include smoke test procedure with `aws lambda invoke` command
    - _Requirements: 2.4, 11.1, 11.2, 11.4, 15.5_

- [x] 2. Port processor source modules from monorepo
  - [x] 2.1 Implement handler.py — Lambda entry point
    - Create `src/processor/handler.py` with `handler(event, context)` function
    - Validate split S3 config (`SOURCE_BUCKET`, `SOURCE_PREFIX`, `DESTINATION_BUCKET`, `DESTINATION_PREFIX`) is set at entry (fail-fast RuntimeError)
    - Iterate `event["Records"]`, normalize each payload via body parsing
    - Delegate to `logic.process_record()` per record, catch `ProcessingError`
    - Collect `batchItemFailures` with `itemIdentifier` for failed records
    - Skip S3 test events (`Event == "s3:TestEvent"`) without failure
    - Emit structured JSON logs for each record outcome
    - _Requirements: 3.1, 3.5, 3.7, 3.8, 3.9, 3.10, 6.6, 13.1_

  - [x] 2.2 Implement logic.py — per-record orchestration and payload normalization
    - Create `src/processor/logic.py` with `process_record(payload, source_bucket, source_prefix, destination_bucket, destination_prefix, env_params)` function
    - Implement payload normalization for three formats: direct message, S3 event, SNS-wrapped S3 event
    - Parse raw key → `(year, doy, station, source_stem)` with validation
    - Merge message parameters over environment defaults with validation
    - Orchestrate: nav fetch → calibration → parquet write → DynamoDB update
    - Generate UUID v4 trace_id when not provided; propagate through all log entries
    - Handle source bucket mismatch check for optional `bucket` field and enforce source prefix match
    - _Requirements: 3.2, 3.3, 3.4, 3.6, 3.11, 4.1, 4.2, 7.1, 7.2, 7.3, 7.4, 13.5, 13.6_

  - [x] 2.3 Implement nav.py — BKG navigation file fetch
    - Create `src/processor/nav.py` with `fetch_nav_file(year, doy, nav_day_offset, timeout_list, timeout_download)` and `compute_nav_doy(year, doy, offset)`
    - Compute nav year/doy with year rollback when result < 1
    - HTTP GET BKG directory listing at `https://igs.bkg.bund.de/root_ftp/IGS/BRDC/{nav_year}/{nav_doy}/`
    - Parse directory for compatible nav filename (BRDC IGS RINEX 3 or legacy `brdc{doy}0.{yy}p.gz`)
    - Download nav file to `/tmp` with timeout enforcement (30s listing, 120s download)
    - Raise `NavFetchError` on timeout, HTTP error, or no compatible file
    - _Requirements: 4.3, 4.4, 4.5, 4.6, 4.7, 4.8_

  - [x] 2.4 Implement calibration.py — PyTECGg wrapper
    - Create `src/processor/calibration.py` with `calibrate(obs_path, nav_path)` function
    - Invoke PyTECGg pipeline: `read_rinex_obs`, `read_rinex_nav`, linear combinations, satellite coords, IPP, arc extraction, TEC, vertical equivalent
    - Filter output to rows with non-null `id_arc_valid`, `stec`, `vtec`; map `id_arc_valid` → integer `id_arc`
    - Return DataFrame with 11 columns or None if no valid rows
    - Raise `CalibrationError` if PyTECGg not importable or calibration crashes
    - No synthetic/demo/hardcoded TEC data in any code path
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 9.1, 9.2, 9.3, 9.4_

  - [x] 2.5 Implement parquet_io.py — Parquet encoding and S3 write
    - Create `src/processor/parquet_io.py` with `write_parquet(df, bucket, station, year, doy, source_stem)` and `build_output_key(station, year, doy, source_stem)`
    - Define `OUTPUT_COLUMNS` list with exactly 11 columns
    - Write Snappy-compressed Parquet to deterministic S3 key: `{DESTINATION_PREFIX}/station={station}/year={year}/doy={doy:03d}/{source_stem}.parquet`
    - Validate DataFrame schema matches expected columns before write
    - Use `s3.put_object` with Parquet binary (PAR1 magic bytes)
    - Overwrite existing key for idempotent reprocessing
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.7, 6.8_

  - [x] 2.6 Implement DynamoDB job status updates in logic.py
    - Add job status lifecycle: `processing` → `completed` (with `output_key`) or `failed` (with `error_type`, `error_message`)
    - Skip DynamoDB updates when `JOBS_TABLE_NAME` is unset or no `job_id` in message
    - Log warning on DynamoDB errors without failing the record
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 2.7 Implement structured logging utility
    - Create JSON logging helper emitting single-line JSON to stdout
    - Include `trace_id`, `message_id`, `station`, `year`, `doy` in success entries
    - Include `outcome`, `duration_ms`, `row_count`, `output_key` in success entries
    - Include `error_type`, `error_message`, `stack_trace` in error entries
    - Include `outcome=skipped`, `reason=s3_test_event` for test event entries
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.7_

- [x] 3. Checkpoint — Verify source modules
  - Ensure all source modules are importable and exception hierarchy is consistent, ask the user if questions arise.

- [x] 4. Unit and property tests
  - [x] 4.1 Create tests/conftest.py with shared fixtures
    - Set up pytest fixtures for mocked S3, DynamoDB (using moto), and sample SQS events
    - Create fixture for environment variables (`SOURCE_BUCKET`, `SOURCE_PREFIX`, `DESTINATION_BUCKET`, `DESTINATION_PREFIX`, `JOBS_TABLE_NAME`, parameter defaults)
    - _Requirements: 14.5_

  - [x] 4.2 Write property test for raw key parsing
    - __Property 4: Raw Key Parse Determinism__
    - Generate valid and invalid raw keys via Hypothesis strategies
    - Assert deterministic `(year, doy, station, source_stem)` extraction
    - Assert errors on invalid patterns
    - __Validates: Requirements 4.1, 4.2__

  - [x] 4.3 Write property test for nav DOY computation
    - __Property 5: Nav DOY Computation with Year Rollback__
    - Generate `(year, doy, offset)` triples via Hypothesis
    - Assert rollback to previous year when `doy - offset < 1`
    - Assert result always satisfies `1 <= nav_doy <= 366`
    - __Validates: Requirements 4.3__

  - [x] 4.4 Write property test for payload normalization
    - __Property 2: Payload Normalization__
    - Generate all three message formats (direct, S3 event, SNS-wrapped) via Hypothesis
    - Assert same bucket and key extracted regardless of envelope format
    - __Validates: Requirements 3.2, 3.3, 3.4, 3.6__

  - [x] 4.5 Write property test for output key derivation
    - __Property 9: Output Path Determinism__
    - Generate `(station, year, doy, source_stem)` tuples via Hypothesis
    - Assert output key always matches `{DESTINATION_PREFIX}/station={station}/year={year}/doy={doy:03d}/{source_stem}.parquet`
    - __Validates: Requirements 6.1, 6.3__

  - [x] 4.6 Write property test for parameter merging
    - __Property 12: Parameter Override Precedence__
    - Generate env defaults and message parameter combinations via Hypothesis
    - Assert message values override env defaults for matching keys
    - Assert env defaults used when message key absent
    - __Validates: Requirements 7.1, 7.2__

  - [x] 4.7 Write property test for batch failure isolation
    - __Property 1: Partial Batch Failure Completeness__
    - Generate batches of N records with K successes and N-K failures
    - Assert `batchItemFailures` contains exactly N-K entries
    - Assert no successful record's messageId appears in failures
    - __Validates: Requirements 3.1, 3.7, 3.8, 3.9__

  - [x] 4.8 Write property test for Parquet schema invariance
    - __Property 10: Parquet Schema Invariance__
    - Generate DataFrames with valid calibration output via Hypothesis
    - Assert written Parquet contains exactly 11 specified columns
    - __Validates: Requirements 6.2__

  - [x] 4.9 Write property test for structured log format
    - __Property 13: Structured JSON Log Format__
    - Generate log data payloads via Hypothesis
    - Assert every emitted line is valid single-line JSON parseable by `json.loads()`
    - __Validates: Requirements 13.1__

  - [x] 4.10 Write property test for invalid parameter rejection
    - __Property 6: Invalid Parameter Rejection__
    - Generate invalid parameter values (non-integer offsets, non-positive offsets, non-boolean flags, unsupported keys)
    - Assert handler treats record as processing failure
    - __Validates: Requirements 4.8, 7.3__

  - [x] 4.11 Write property test for calibration row filtering
    - __Property 7: Calibration Row Filtering__
    - Generate DataFrames with various null patterns in `id_arc_valid`, `stec`, `vtec`
    - Assert filtered output contains only rows where all three are non-null
    - __Validates: Requirements 5.2__

  - [x] 4.12 Write unit tests for BKG nav filename selection
    - Test URL construction for various year/doy combinations
    - Test parsing of directory listing HTML for BRDC filenames
    - Test selection of compatible nav file from multiple candidates
    - Mock HTTP responses for listing and download
    - _Requirements: 4.4, 4.5, 4.6, 4.7_

  - [x] 4.13 Write unit tests for handler with mocked dependencies
    - Test full handler invocation with mocked S3, DynamoDB, nav fetch, and calibration
    - Test S3 test event skipping
    - Test invalid JSON body handling
    - Test bucket mismatch rejection
    - Test DynamoDB update failure isolation (warning only)
    - Test split S3 env unset fail-fast (`SOURCE_BUCKET`, `SOURCE_PREFIX`, `DESTINATION_BUCKET`, `DESTINATION_PREFIX`)
    - _Requirements: 3.1, 3.5, 3.7, 3.8, 3.9, 3.10, 3.11, 6.6, 8.4, 8.5_

  - [x] 4.14 Write unit tests for Parquet output encoding
    - Test Parquet binary validity (PAR1 magic bytes, Snappy compression)
    - Test output readable by pyarrow without exceptions
    - Test schema contains exactly 11 columns
    - Test idempotent overwrite of existing key
    - _Requirements: 6.2, 6.7, 6.8_

- [x] 5. Checkpoint — Verify tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. GitHub Actions CI/CD workflows
  - [x] 6.1 Configure ci.yml for PR checks
    - Update `.github/workflows/ci.yml` to call `actionsforge/actions/.github/workflows/python-image-pr-checks.yml@main`
    - Pass inputs for Python 3.13 (CPython 3.13), `linux/amd64` platform, and Trivy scan settings aligned with the reusable workflow
    - Workflow triggers on pull requests
    - _Requirements: 10.1, 10.2, 14.4_

  - [x] 6.2 Configure release.yml for GHCR publish
    - Update `.github/workflows/release.yml` to call `actionsforge/actions/.github/workflows/docker-image-publish.yml@main`
    - Trigger on pushes to `main` and tags matching `v*`
    - Publish to `ghcr.io/<org>/tec-processor-image` using `GITHUB_TOKEN`
    - Tag with commit SHA + `latest` on main pushes; semver (without `v` prefix) on version tags
    - Fail on CRITICAL Trivy findings before push
    - _Requirements: 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 12.3, 12.4_

- [x] 7. Checkpoint — Verify CI configuration
  - Ensure workflow YAML is valid and references correct reusable workflow paths, ask the user if questions arise.

- [x] 8. Documentation and deploy-time promotion follow-up
  - [x] 8.1 Document deploy-time ECR preparation and image promotion procedure
    - Add section to README explaining ECR prerequisites for Lambda deploy (`aws ecr create-repository`) when needed
    - Document the monorepo Terraform variable `processor_image_uri` and how to update it
    - Document GHCR → ECR promotion flow (docker pull/tag/push)
    - _Requirements: 10.4, 11.1, 15.5_

  - [x] 8.2 Document monorepo follow-up: remove embedded Dockerfile and build script
    - Add a `MONOREPO_FOLLOWUP.md` or README section documenting the steps to perform in the monorepo after this repo is operational:
      - Remove `services/processor/Dockerfile`
      - Remove `scripts/build-push-processor-image.sh`
      - Update Terraform `processor_image_uri` to reference the GHCR-published image (promoted to ECR)
      - Remove any Lambda image build logic from monorepo CI
    - _Requirements: 15.5_

- [x] 9. Python runtime compatibility gate (greenfield)
  - [x] 9.1 Set and document Python 3.13 as accepted baseline
    - Keep Docker runtime base, docs, and CI runtime statements aligned to Python 3.13
    - Keep `requires-python` and dependency markers aligned to runtime policy
    - _Requirements: 16.1, 16.2_

  - [x] 9.2 Validate Python 3.13 runtime and CI cohesion
    - Confirm test/lint/build commands run successfully against the Python 3.13 baseline
    - Confirm compatibility messaging in docs/code matches accepted baseline
    - _Requirements: 16.3, 16.4_

  - [x] 9.3 Prevent drift from Python 3.13 baseline
    - Ensure no runtime/CI docs imply a required move to another Python version
    - Keep dependency and runtime constraints consistent across repo files
    - _Requirements: 16.3, 16.4_

- [x] 10. Local GeoNet AUCK integration workflow
  - [x] 10.1 Add local-only fixed-sample GeoNet fetch helper
    - Add `tools/geonet_samples.py` with AUCK fixed sample key candidates from `s3://geonet-open-data/gnss/rinexhourly/`
    - Keep helper local-only; no Lambda handler dependency
    - _Requirements: 3.2, 3.11, 14.5, 17.1, 17.2_

  - [x] 10.2 Add local AUCK end-to-end runner
    - Add `tools/local_geonet_runner.py` for local sample download + BKG nav fetch + PyTECGg calibration + local parquet write
    - _Requirements: 5.1, 6.2, 11.1, 17.3_

  - [x] 10.3 Add test split for live GeoNet integration
    - Add marker `integration_geonet` and opt-in tests under `tests/test_geonet_integration.py`
    - Keep PR CI deterministic by excluding live marker in CI test command
    - _Requirements: 10.2, 14.5, 17.4_

  - [x] 10.4 Enforce input-driven Lambda behavior
    - Add unit test asserting handler remains payload-driven and does not call local GeoNet pull helpers
    - _Requirements: 3.2, 3.11, 17.2_

- [x] 11. Kiro developer enablement
  - [x] 11.1 Document Kiro workflow in README
    - Add sections covering local GeoNet run commands, integration marker usage, and Kiro guidance
    - _Requirements: 11.1, 15.5, 17.5_

- [x] 12. Multi-format output support
  - [x] 12.1 Add CSV serializer (csv_io.py)
    - Create `src/processor/csv_io.py` with `rows_to_csv_bytes(rows) -> bytes` using Polars `write_csv()`
    - Preserves column order matching PARQUET_COLUMNS
    - _Requirements: 6.1, 6.7_

  - [x] 12.2 Add JSON serializer (json_io.py)
    - Create `src/processor/json_io.py` with `rows_to_json_bytes(rows) -> bytes` using stdlib `json.dumps`
    - Epoch strings preserved as-is in ISO 8601 format
    - _Requirements: 6.1, 6.7_

  - [x] 12.3 Add plot serializers (plot_io.py)
    - Create `src/processor/plot_io.py` with `rows_to_static_plot_bytes` (PNG via matplotlib) and `rows_to_interactive_plot_bytes` (HTML via plotly CDN)
    - Port plot functions from PyTECGg Batch Calibrator, adapted to return bytes instead of writing to disk
    - Add `matplotlib >= 3.10` and `plotly >= 6.2` to pyproject.toml and requirements.lock
    - _Requirements: 6.1, 6.7_

  - [x] 12.4 Update logic.py for multi-format orchestration
    - Add `SAVE_JSON` to `ALLOWED_PARAMS` and boolean validation
    - Update `require_output_format` to accept any of the five flags (not just Parquet)
    - Add `extension` parameter (default `"parquet"`) to `derive_output_key`
    - Update `process_record` to write all enabled formats in independent if-checks, returning primary key
    - _Requirements: 6.1, 6.2, 6.5, 7.1, 7.2, 7.4_

  - [x] 12.5 Update handler.py and main.py for multi-format
    - Add `SAVE_JSON` to `_default_params_from_env()` in handler.py and main.py
    - Update `_process_message` to write all enabled formats
    - _Requirements: 6.1, 7.1_

  - [x] 12.6 Add serializer unit tests and update existing tests
    - Add `tests/test_csv_io_unit.py`, `tests/test_json_io_unit.py`, `tests/test_plot_io_unit.py`
    - Update `tests/test_logic_unit.py` to reflect new `require_output_format` behavior and extension support
    - Add multi-format write tests to `tests/test_handler_unit.py`
    - _Requirements: 6.1, 6.2, 7.1, 14.2_

## Notes

- Tasks marked with `*` are optional property-based test sub-tasks and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation between major phases
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples, integration points, and error conditions with mocks
- All tests run without AWS credentials, network access, or live PyTECGg (mocked)
- Source code is ported from `event-driven-serverless-platform-demo/services/processor/` — adapt import paths from nested monorepo layout to standalone `src/processor/`
- Python runtime target in this repository is __CPython 3.13__ (not "Python 13"), because PyTECGg currently supports Python 3.11-3.13 only

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "1.4", "1.5"] },
    { "id": 1, "tasks": ["1.6", "2.1", "2.3", "2.4", "2.5", "2.7"] },
    { "id": 2, "tasks": ["2.2", "2.6"] },
    { "id": 3, "tasks": ["4.1"] },
    { "id": 4, "tasks": ["4.2", "4.3", "4.4", "4.5", "4.6", "4.8", "4.9", "4.10", "4.11", "4.12", "4.14"] },
    { "id": 5, "tasks": ["4.7", "4.13"] },
    { "id": 6, "tasks": ["6.1", "6.2"] },
    { "id": 7, "tasks": ["8.1", "8.2"] },
    { "id": 8, "tasks": ["9.1"] },
    { "id": 9, "tasks": ["9.2", "9.3"] }
  ]
}
```
