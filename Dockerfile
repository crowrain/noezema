# Noezema dev image (impl/from-scratch, M0).
# Used by infra/compose.dev.yaml (fake LLM) and CI docker build check.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.7

COPY pyproject.toml README.md LICENSE ./
COPY packages ./packages
COPY apps ./apps
COPY hostctl ./hostctl
COPY tests ./tests
RUN uv pip install --system -e '.[dev]'

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "tests.fakes.fake_openai_server", "--host", "0.0.0.0", "--port", "8089"]
