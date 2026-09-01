# ETL worker — runs on the owner's GPU box via docker-compose (ADR v1.1.0).
# Outbound-only: polls Supabase etl_jobs, no inbound ports.
FROM python:3.11-slim

WORKDIR /worker

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY worker/ ./worker/
COPY app/ ./app/

CMD ["uv", "run", "python", "-m", "worker.main"]
