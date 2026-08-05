FROM python:3.11-slim

WORKDIR /app

# System deps for asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install uv for fast dep management
RUN pip install --no-cache-dir uv

# Copy project
COPY pyproject.toml .
COPY README.md .
COPY packages/ packages/
COPY apps/ apps/
COPY migrations/ migrations/
COPY alembic.ini .

# Install dependencies
RUN uv pip install --system ".[dev]"

# Workspace volume
RUN mkdir -p /app/workspace

ENV PYTHONUNBUFFERED=1
