# ── Base image ────────────────────────────────────────────────────────────────
# Digest-pinned for supply chain security — tag alone is mutable, digest is not.
# Update digest when a new patch is released (Dependabot handles this automatically
# when .github/dependabot.yml is present with pip ecosystem enabled).
#
# To get the current digest:
#   docker pull python:3.12-slim && docker inspect python:3.12-slim --format='{{index .RepoDigests 0}}'
#
# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim@sha256:c2d8472b831337ab296a8ce652e1ba786e9e3034fc445dc58b50a7f5251f0003 AS builder

WORKDIR /app

# Install build dependencies (not carried into runtime image)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency specs first — layer cache: only reinstalls when deps change
COPY pyproject.toml requirements-lock.txt ./

# Install from pinned lockfile — reproducible builds
RUN python -m venv /app/venv && \
    /app/venv/bin/pip install --upgrade pip --quiet && \
    /app/venv/bin/pip install -r requirements-lock.txt --quiet

# Copy source after deps — layer cache: code changes don't bust dep layer
COPY src/ ./src/

# Install the project itself (no deps — already installed above)
RUN /app/venv/bin/pip install -e . --no-deps --quiet

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim@sha256:c2d8472b831337ab296a8ce652e1ba786e9e3034fc445dc58b50a7f5251f0003 AS runner

# OCI standard image labels — populated at build time via --build-arg or CI
# docker build --build-arg VCS_REF=$(git rev-parse HEAD) --build-arg VERSION=1.2.3 .
ARG VCS_REF=unknown
ARG VERSION=0.1.0
ARG BUILD_DATE
LABEL org.opencontainers.image.title="{project_name}" \
      org.opencontainers.image.description="{project_description}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/{org}/{repo}" \
      org.opencontainers.image.vendor="Aukern" \
      org.opencontainers.image.licenses="MIT"

# curl for health check probe — minimal addition
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user — never run as root in production
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Copy only the venv and application code — no build tools in runtime image
COPY --from=builder /app/venv /app/venv
COPY --from=builder /app/src /app/src
COPY config/ ./config/

# Create writable directories and hand ownership to non-root user
RUN mkdir -p /app/logs /app/data && \
    chown -R appuser:appuser /app

USER appuser

ENV APP_ENV=prod \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/venv/bin:$PATH" \
    APP_PORT=8000 \
    APP_WORKERS=1 \
    APP_SHUTDOWN_TIMEOUT=30

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${APP_PORT}/health/live || exit 1

EXPOSE ${APP_PORT}

# Entrypoint uses exec so uvicorn is PID 1 inside the venv process tree.
# Combined with init: true (tini as PID 1), SIGTERM flows:
#   Docker → tini → uvicorn → graceful shutdown → drain in-flight requests
COPY --chown=appuser:appuser docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["entrypoint.sh"]
