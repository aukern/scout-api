.PHONY: test test-infra test-integration coverage lint format migrate migrate-status rollback \
        lock lock-upgrade audit pre-commit ci \
        docker-build docker-up docker-dev docker-down docker-logs docker-shell docker-reset clean

test:
	APP_ENV=dev pytest tests/ -q --ignore=tests/test_infra

test-infra:
	pytest tests/test_infra/ -q

test-integration:
	APP_ENV=dev pytest tests/ -q -m integration

coverage:
	APP_ENV=dev pytest tests/ --cov=src --cov-report=term-missing --cov-fail-under=90 -q -m "not integration"

lint:
	python -m ruff check src/ tests/
	python -m ruff format --check src/ tests/
	python -m mypy src/ --ignore-missing-imports

format:
	python -m ruff format src/ tests/

# Dependency lockfile management
lock:
	pip-compile pyproject.toml --extra dev --extra llm --output-file requirements-lock.txt --quiet --generate-hashes
	@echo "requirements-lock.txt updated (with hashes). Commit the result."

lock-upgrade:
	pip-compile pyproject.toml --extra dev --extra llm --output-file requirements-lock.txt --upgrade --quiet --generate-hashes
	@echo "All dependencies upgraded (with hashes). Review and commit."

# Security audit
audit:
	python -m bandit -r src/ -ll -q
	pip-audit --requirement requirements-lock.txt --strict

# Run all pre-commit hooks manually
pre-commit:
	pre-commit run --all-files

# Local CI gate — mirrors .github/workflows/ci.yml exactly
ci:
	bash scripts/ci_check.sh

migrate:
	python scripts/migrate.py

migrate-status:
	python scripts/migrate.py --status

rollback:
	@echo "Usage: make rollback N=1  (N = number of migrations to roll back, default 1)"
	python scripts/migrate.py --rollback $${N:-1}

docker-build:
	docker build -f Dockerfile -t scout-api:latest .

docker-up:
	APP_ENV=prod docker compose up -d

docker-dev:
	APP_ENV=dev docker compose -f docker-compose.yml -f docker-compose.dev.yml up

docker-down:
	docker compose down

docker-logs:
	docker compose logs app --tail=100 -f

docker-shell:
	docker compose exec app /bin/sh

docker-reset:
	docker compose down -v

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
