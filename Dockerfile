# syntax=docker/dockerfile:1
# Python 3.12 + uv (docs/architecture.md §2). Цел "dev" се ползва от docker-compose (профил "app").
# Изисква pyproject.toml и uv.lock (задача agro-zaz.1).

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    TZ=Europe/Sofia

# weasyprint (PDF на фактури) и pg_dump (backup.pg_dump) искат системни библиотеки.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi8 shared-mime-info \
        fonts-dejavu-core postgresql-client tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

# Непривилегиран потребител.
RUN useradd --create-home --uid 1000 agro \
    && mkdir -p /app /opt/venv /var/lib/agro/storage \
    && chown -R agro:agro /app /opt/venv /var/lib/agro

USER agro
WORKDIR /app

# Зависимостите първо — слоят се кешира, докато lock файлът не се промени.
COPY --chown=agro:agro pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# ---- dev: кодът идва от bind mount (.:/app), без копиране ----
FROM base AS dev
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---- prod: кодът е в образа, без dev зависимости ----
FROM base AS prod
COPY --chown=agro:agro . .
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
