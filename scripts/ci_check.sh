#!/usr/bin/env bash
# ci_check.sh — Local CI gate. Must exit 0 before any commit is accepted.
# Mirrors the checks run in .github/workflows/ci.yml so local == remote.
#
# Usage: bash scripts/ci_check.sh
# From Makefile: make ci
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/venv/bin"
PASS=0
FAIL=0

_ok()  { echo "  ✅ $1"; PASS=$((PASS + 1)); }
_fail(){ echo "  ❌ $1"; FAIL=$((FAIL + 1)); }

echo ""
echo "=== Aukern CI Gate ==="
echo ""

# ── Format ──────────────────────────────────────────────────────────────────
echo "── Format (ruff) ──"
if "$VENV/python" -m ruff format --check src/ tests/ 2>&1; then
  _ok "ruff format"
else
  _fail "ruff format — run: venv/bin/python -m ruff format src/ tests/"
fi

# ── Lint ────────────────────────────────────────────────────────────────────
echo ""
echo "── Lint (ruff) ──"
if "$VENV/python" -m ruff check src/ tests/ 2>&1; then
  _ok "ruff lint"
else
  _fail "ruff lint — run: venv/bin/python -m ruff check src/ tests/ --fix"
fi

# ── Type Check ──────────────────────────────────────────────────────────────
echo ""
echo "── Type Check (mypy) ──"
if "$VENV/python" -m mypy src/ --ignore-missing-imports 2>&1; then
  _ok "mypy"
else
  _fail "mypy — fix type errors above"
fi

# ── Unit Tests ───────────────────────────────────────────────────────────────
echo ""
echo "── Unit Tests (pytest) ──"
if APP_ENV=dev "$VENV/pytest" tests/ -q --tb=short --ignore=tests/test_infra -m "not integration" 2>&1; then
  _ok "unit tests"
else
  _fail "unit tests — see output above"
fi

# ── Infra Tests ──────────────────────────────────────────────────────────────
echo ""
echo "── Infra Tests ──"
if [ -d "$ROOT/tests/test_infra" ]; then
  if APP_ENV=dev "$VENV/pytest" tests/test_infra/ -q --tb=short 2>&1; then
    _ok "infra tests"
  else
    _fail "infra tests — see output above"
  fi
else
  _fail "tests/test_infra/ missing — run: pipeline gen-infra-tests --project ."
fi

# ── Coverage ─────────────────────────────────────────────────────────────────
echo ""
echo "── Coverage Gate (≥90%) ──"
if APP_ENV=dev "$VENV/pytest" tests/ tests/test_infra/ \
    --cov=src --cov-report=term-missing --cov-fail-under=90 \
    -q -m "not integration" 2>&1; then
  _ok "coverage ≥90%"
else
  _fail "coverage <90% — add tests for uncovered lines above"
fi

# ── Lockfile Freshness ────────────────────────────────────────────────────────
echo ""
echo "── Lockfile freshness (pip-compile) ──"
if [ -f "$ROOT/requirements-lock.txt" ]; then
  # Regenerate into a temp file and compare — fails if pyproject.toml has changed but lockfile wasn't updated
  TMPLOCK=$(mktemp)
  "$VENV/pip-compile" "$ROOT/pyproject.toml" \
      --extra dev --extra llm \
      --output-file "$TMPLOCK" --quiet 2>/dev/null || true
  if diff -q "$ROOT/requirements-lock.txt" "$TMPLOCK" >/dev/null 2>&1; then
    _ok "lockfile up to date"
  else
    _fail "lockfile stale — run: venv/bin/pip-compile pyproject.toml --extra dev --extra llm --output-file requirements-lock.txt"
  fi
  rm -f "$TMPLOCK"
else
  _fail "requirements-lock.txt missing — run: venv/bin/pip-compile pyproject.toml --extra dev --extra llm --output-file requirements-lock.txt"
fi

# ── Security ─────────────────────────────────────────────────────────────────
echo ""
echo "── Security (bandit) ──"
if "$VENV/python" -m bandit -r src/ -ll -q 2>&1; then
  _ok "bandit"
else
  _fail "bandit — fix security issues above"
fi

# ── Integration Tests ────────────────────────────────────────────────────────
echo ""
echo "── Integration Tests (requires running DB + Redis) ──"
if [ -n "${DATABASE_URL:-}" ] && [ -n "${REDIS_URL:-}" ]; then
  if APP_ENV=dev "$VENV/pytest" tests/ -q --tb=short -m "integration" --timeout=60 2>&1; then
    _ok "integration tests"
  else
    _fail "integration tests — see output above"
  fi
else
  echo "  ⚠️  SKIPPED — set DATABASE_URL and REDIS_URL to run integration tests locally"
  echo "      Quickstart: docker compose -f docker/docker-compose.dev.yml up -d"
  echo "      Then: DATABASE_URL=postgresql://... REDIS_URL=redis://... bash scripts/ci_check.sh"
fi

# ── Performance Benchmarks ────────────────────────────────────────────────────
echo ""
echo "── Performance Benchmarks ──"
if "$VENV/python" -m pytest --co -q -m "benchmark" tests/ 2>/dev/null | grep -q "::"; then
  if APP_ENV=dev "$VENV/pytest" tests/ -m "benchmark" \
      --benchmark-only \
      --benchmark-compare \
      --benchmark-compare-fail=mean:10% \
      -q --tb=short 2>&1; then
    _ok "performance benchmarks (no regression > 10%)"
  else
    _fail "performance benchmarks — mean regression > 10% detected vs baseline"
  fi
else
  echo "  ⚠️  SKIPPED — no tests marked @pytest.mark.benchmark found"
  echo "      Add: @pytest.mark.benchmark to critical-path tests"
fi

# ── Note: Mutation tests are CI-only (too slow for local) ─────────────────────
echo ""
echo "── Mutation Tests ──"
echo "  ⚠️  SKIPPED locally — runs in CI on push to main and weekly on schedule"
echo "      To run manually: mutmut run --paths-to-mutate src/ --tests-dir tests/"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo "BLOCKED — fix the $FAIL failure(s) above before committing."
  exit 1
fi

echo "GREEN — all checks passed."
exit 0
