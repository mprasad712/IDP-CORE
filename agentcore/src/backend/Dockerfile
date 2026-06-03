# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg build-essential && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock package-lock.json ./
COPY base/agentcore ./base/agentcore

RUN uv sync --frozen
RUN uv pip install --python .venv/bin/python sqlparse cryptography faker

FROM python:3.12-slim AS runtime

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg build-essential && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY . .

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN mkdir -p /app/data/alembic /app/data/logs

EXPOSE 7860

CMD ["uvicorn", "agentcore.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "7860"]
