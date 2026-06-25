FROM public.ecr.aws/lambda/python:3.13

# Install application package from repository root pyproject.toml
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY src/ ${LAMBDA_TASK_ROOT}/

CMD ["processor.handler.handler"]
