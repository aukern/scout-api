#!/bin/sh
# Entrypoint — runs migrations then hands off to uvicorn.
# Uses exec so uvicorn is PID 1 inside the container: SIGTERM from
# `docker compose down` goes straight to uvicorn, not to this shell.

set -e

# ── Migrations ────────────────────────────────────────────────────────────────
# Run before accepting traffic so the schema is always up to date.
# scripts/migrate.py is idempotent — safe to run on every container start.
if [ -n "${DATABASE_URL:-}" ]; then
    echo "[entrypoint] running database migrations..."
    python scripts/migrate.py || { echo '[entrypoint] FATAL: migrations failed'; exit 1; }
    echo "[entrypoint] migrations applied."
fi

# ── Start application ─────────────────────────────────────────────────────────
exec uvicorn src.main:app \
    --host 0.0.0.0 \
    --port "${APP_PORT:-8000}" \
    --workers "${APP_WORKERS:-1}" \
    --timeout-graceful-shutdown "${APP_SHUTDOWN_TIMEOUT:-30}"
