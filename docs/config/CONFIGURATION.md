# Configuration Reference

Scout API is configured via environment variables. All settings have defaults that work for local development.

Copy `.env.example` to `.env` and set the required values before running.

---

## Environment variables

| Variable | Default | Required | Description |
|---|---|---|---|
| `DATABASE_URL` | `postgresql://appuser:apppassword@localhost:5432/appdb` | Yes (prod) | PostgreSQL connection string |
| `MAX_CONNECTIONS` | `10` | No | asyncpg pool max size |
| `APP_ENV` | `dev` | No | `dev` / `staging` / `prod` |
| `APP_PORT` | `8000` | No | Port uvicorn listens on |
| `LOG_LEVEL` | `INFO` | No | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `S3_BUCKET_NAME` | `""` | Yes (for file ingest) | S3 bucket for uploaded file storage |
| `S3_REGION` | `us-east-1` | No | AWS region |
| `S3_ENDPOINT_URL` | `""` | No | S3 endpoint override (e.g. `http://localhost:4566` for localstack) |
| `REDIS_URL` | `""` | Yes (for background jobs) | Redis DSN for arq queue |
| `ARQ_CONCURRENCY` | `10` | No | Number of concurrent arq worker slots |

### Docker Compose variables

| Variable | Default | Description |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | `scout-api` | Docker container name prefix |
| `POSTGRES_DB` | `appdb` | Postgres database name |
| `POSTGRES_USER` | `appuser` | Postgres user |
| `POSTGRES_PASSWORD` | (required) | Postgres password — set before deploying |

---

## Configuration files

Config files in `config/` provide base settings and per-environment overrides:

| File | When loaded |
|---|---|
| `config/app_config.yaml` | Always (base defaults) |
| `config/app_config.dev.yaml` | When `APP_ENV=dev` |
| `config/app_config.staging.yaml` | When `APP_ENV=staging` |
| `config/app_config.prod.yaml` | When `APP_ENV=prod` |

Environment variables override config files. Config files override hardcoded defaults.

---

## Quick start

```bash
cp .env.example .env
# Edit .env and set POSTGRES_PASSWORD
docker compose --profile postgres up
```
