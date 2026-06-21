# Secrets Guide

Scout API uses environment variables for all secrets. No secrets are committed to the repository.

---

## Required secrets

| Secret | Example | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://user:pass@host:5432/db` | Full Postgres DSN including password |
| `POSTGRES_PASSWORD` | `changeme` | Postgres user password (Docker Compose only) |
| `AWS_ACCESS_KEY_ID` | `AKIAIOSFODNN7EXAMPLE` | AWS credential for S3 uploads. Use IRSA role in production. |
| `AWS_SECRET_ACCESS_KEY` | `wJalrXUtnFEMI/K7MDENGbPxRfiCY` | AWS secret key. Use IRSA role in production. |
| `REDIS_URL` | `redis://localhost:6379` | Redis DSN for arq background job queue |

---

## Setting secrets

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set real values for all secrets.

3. `.env` is gitignored — it will never be committed.

---

## Secrets in production

For production deployments:
- Use a secrets manager (AWS Secrets Manager, HashiCorp Vault, or your platform's secret store)
- Inject secrets as environment variables at runtime
- Never commit `.env` or any file containing real credentials

---

## What is NOT a secret

- `APP_ENV`, `APP_PORT`, `LOG_LEVEL` — these are configuration, not secrets
- `COMPOSE_PROJECT_NAME` — deployment metadata, not a secret
