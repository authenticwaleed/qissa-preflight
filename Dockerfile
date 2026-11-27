FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    STORAGE_DIR=/data/audio

# The app may write to the storage folder only. Docker gives a new named volume the owner of the
# folder it is mounted on, so the volume in docker-compose.yml is writable as well.
RUN useradd --system --user-group --no-create-home qissa \
    && mkdir -p /data/audio \
    && chown qissa:qissa /data/audio

WORKDIR /app

# Dependencies get their own layer, rebuilt only when pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache

COPY alembic.ini ./
COPY migrations/ migrations/
COPY app/ app/

USER qissa
EXPOSE 8000

# exec makes uvicorn PID 1, so it receives the SIGTERM from `docker stop` and shuts down cleanly.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000"]
