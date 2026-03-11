# Stage 1: Builder
FROM python:3.13-slim AS builder
WORKDIR /build
RUN pip install uv
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev --compile-bytecode

# Stage 2: Runtime
FROM python:3.13-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
COPY --from=builder /build/src /app/src
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Alembic migration files
COPY alembic.ini ./
COPY migrations/ ./migrations/
# Config file (optional — env vars override)
COPY config.yaml* ./
CMD ["sh", "-c", "alembic upgrade head && python -m pmtb.main"]
