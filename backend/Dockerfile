# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=3001

RUN groupadd --system electronicmail \
    && useradd --system --gid electronicmail --home-dir /app electronicmail

WORKDIR /app

COPY requirements.lock ./requirements.lock
RUN python -m pip install --upgrade pip==26.1.2 \
    && python -m pip install --no-deps --requirement requirements.lock \
    && python -m pip check

COPY alembic.ini db_init.py ./
COPY migrations ./migrations
COPY app ./app

RUN python -m compileall -q app \
    && chown -R electronicmail:electronicmail /app

USER electronicmail
EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '3001') + '/health', timeout=3)" || exit 1

CMD ["sh", "-c", "python -m app.deploy_check && python -m app.schema_check --wait-seconds 120 && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-access-log"]
