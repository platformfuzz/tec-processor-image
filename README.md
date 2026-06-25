# tec-processor-image

Lambda container image that calibrates GNSS RINEX observations into TEC Parquet output.

## Architecture

```plaintext
tec-processor-image/
├── Dockerfile                 # OCI image build (public.ecr.aws/lambda/python:3.13)
├── pyproject.toml             # Package metadata + pinned dependencies
├── requirements.lock          # Exact resolved versions for reproducible installs
├── src/
│   └── processor/
│       ├── __init__.py        # Package + exception hierarchy
│       ├── handler.py         # Lambda entry point (CMD: processor.handler.handler)
│       ├── logic.py           # Per-record orchestration
│       ├── nav.py             # BKG navigation file fetch
│       ├── calibration.py     # PyTECGg wrapper
│       └── parquet_io.py      # Parquet encoding + S3 write
├── tests/
├── .github/workflows/
│   ├── ci.yml                 # PR checks (reusable workflow)
│   └── release.yml            # GHCR publish on main/tags
└── README.md
```

## Build Image

```bash
docker build -t tec-processor-image .
```

The Lambda CMD is set to `processor.handler.handler`.

## Publish and Pull from GHCR

The release workflow (`.github/workflows/release.yml`) publishes to GHCR on pushes to `main` and semver tags:

- `ghcr.io/platformfuzz/tec-processor-image:latest` (main branch)
- `ghcr.io/platformfuzz/tec-processor-image:<commit-sha>` (main branch)
- `ghcr.io/platformfuzz/tec-processor-image:<semver>` (version tags, e.g. `1.2.3`)

Pull the latest image:

```bash
docker pull ghcr.io/platformfuzz/tec-processor-image:latest
```

## Deploy-Time Promotion to ECR (for Lambda)

AWS Lambda `package_type = Image` requires an ECR image URI. Promote GHCR images to ECR at deploy time using manual `docker pull/tag/push` or a deploy script.

### ECR Prerequisites

Create the ECR repository (one-time setup per AWS account/region):

```bash
aws ecr create-repository --repository-name tec-processor-image --region <region>
```

### Promote GHCR → ECR

```bash
docker pull ghcr.io/platformfuzz/tec-processor-image:<tag>
docker tag ghcr.io/platformfuzz/tec-processor-image:<tag> <account>.dkr.ecr.<region>.amazonaws.com/tec-processor-image:<tag>
aws ecr get-login-password --region <region> \
  | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker push <account>.dkr.ecr.<region>.amazonaws.com/tec-processor-image:<tag>
```

### Update Terraform

After promoting the image, update the `processor_image_uri` Terraform variable in the monorepo (`event-driven-serverless-platform-demo`) to reference the promoted ECR URI:

```hcl
variable "processor_image_uri" {
  # Example: 123456789012.dkr.ecr.us-east-1.amazonaws.com/tec-processor-image:1.2.3
}
```

Set this variable to the full ECR image URI (including tag) when applying Terraform, e.g.:

```bash
terraform apply -var="processor_image_uri=123456789012.dkr.ecr.us-east-1.amazonaws.com/tec-processor-image:1.2.3"
```

## Dependencies

Direct dependencies are managed in `pyproject.toml`:

- `pytecgg >= 1.3.0` (Python < 3.14 only)
- `pyarrow >= 23.0.1`
- `polars >= 1.5.0`

PyTECGg transitively pulls `scipy`, `numba`, and `numpy`. These are not declared as direct dependencies but are present in the installed image.

`boto3` is provided by the AWS Lambda runtime and is NOT bundled as an application dependency.

## Lambda Invocation Example

Create a sample SQS event file (`event.json`):

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

Invoke the Lambda:

```bash
aws lambda invoke \
  --function-name tec-processor \
  --payload file://event.json \
  --cli-binary-format raw-in-base64-out \
  response.json
```

## Smoke Test

To verify the processor Lambda is working after deployment:

1. Ensure a valid RINEX file exists in the data lake bucket at the key referenced in the payload.
2. Create the `event.json` payload as shown above.
3. Invoke the Lambda:

    ```bash
    aws lambda invoke \
      --function-name tec-processor \
      --payload file://event.json \
      --cli-binary-format raw-in-base64-out \
      response.json
    ```

4. Check the response:

    ```bash
    cat response.json
    ```

5. Expected success condition — the response should contain an empty batch failures array:

    ```json
    {"batchItemFailures": []}
    ```

If `batchItemFailures` contains entries, the referenced RINEX file could not be processed. Check CloudWatch logs for structured JSON error entries with `outcome: "error"`.

## Data Contract

The SQS message schema (payload formats, required fields, optional overrides) is documented in the monorepo:

- `docs/DATA_CONTRACT.md` in `event-driven-serverless-platform-demo`

The handler accepts three payload formats: direct processor messages, S3 event notifications, and SNS-wrapped S3 events. See the data contract for full details.
