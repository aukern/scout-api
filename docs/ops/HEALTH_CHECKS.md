# Health Checks

Scout API exposes two health endpoints for container orchestration probes.

---

## GET /health/live

**Liveness probe.** Returns 200 if the process is running.

This endpoint never fails — if it returns anything other than 200, the process has crashed. Kubernetes and Docker use this to decide whether to restart the container.

```bash
curl http://localhost:8000/health/live
# {"status": "ok"}
```

---

## GET /health/ready

**Readiness probe.** Returns 200 if the service can handle traffic.

Checks database connectivity by running `SELECT 1` through the connection pool. Returns 503 if the database is unreachable.

```bash
curl http://localhost:8000/health/ready
# 200: {"status": "ready", "database": "ok"}
# 503: {"status": "not_ready", "database": "connection refused"}
```

---

## Docker Compose

The `app` service in `docker-compose.yml` is configured with:

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health/live"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 15s
```

---

## Kubernetes

```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8000
  initialDelaySeconds: 15
  periodSeconds: 30

readinessProbe:
  httpGet:
    path: /health/ready
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 10
```
