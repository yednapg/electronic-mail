# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM python:3.14.7-slim-bookworm@sha256:416f0db2a2b561945630cef9877a7ea0581b27449eb9fd9df42f03e1b74b5b63

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=3001

RUN groupadd --system electronicmail \
    && useradd --system --gid electronicmail --home-dir /app electronicmail

WORKDIR /app

COPY requirements.lock ./requirements.lock
RUN python -m pip install \
        --no-deps \
        --require-hashes \
        --only-binary=:all: \
        --requirement requirements.lock \
    && python -m pip check

COPY alembic.ini db_init.py ./
COPY migrations ./migrations
COPY app ./app

RUN python -m compileall -q app \
    && chown -R electronicmail:electronicmail /app

USER electronicmail
EXPOSE 3001

# Process liveness only; scripts/verify_backend_release.py is the production
# API/database/worker readiness and exact-release promotion gate.
HEALTHCHECK --interval=30s --timeout=5s --start-period=150s --retries=3 \
  CMD python -m app.container_health

CMD ["sh", "-c", "python -m app.deploy_check && python -m app.schema_check --wait-seconds 120 && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-access-log"]
