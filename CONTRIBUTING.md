# Contributing

Thank you for your interest in contributing. This document covers everything you need to get started.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [First-Time Contributors](#first-time-contributors)
- [How to Report a Bug](#how-to-report-a-bug)
- [How to Suggest a Feature](#how-to-suggest-a-feature)
- [Development Setup](#development-setup)
- [Running Tests](#running-tests)
- [Making Changes](#making-changes)
- [Pull Request Process](#pull-request-process)
- [Architecture Decisions (ADRs)](#architecture-decisions-adrs)
- [Tech Debt](#tech-debt)
- [Security](#security)

---

## Code of Conduct

This project is a professional environment. We expect all contributors to:

- Be respectful and constructive in all communications
- Accept feedback gracefully — critique is about the code, not the person
- Ask questions when unclear rather than guessing

Violations can be reported by opening a confidential issue tagged `conduct`.

---

## First-Time Contributors

New to this project? Start here:

1. Read [DEVELOPMENT.md](DEVELOPMENT.md) to understand the architecture
2. Look for issues tagged `good first issue` — these are small, well-scoped, and mentored
3. Comment on the issue before starting work so we can avoid duplication
4. When in doubt, open a draft PR early — feedback early is better than feedback late

---

## How to Report a Bug

Open an issue and include:

- **What you expected** — what should have happened
- **What actually happened** — exact error message or observed behavior
- **Steps to reproduce** — minimal example (the shorter, the faster the fix)
- **Environment** — OS, Python version, Docker version if relevant

Search existing issues before opening a new one.

---

## How to Suggest a Feature

Open a GitHub Discussion (not an issue) with:

- **Problem statement** — what pain point does this solve?
- **Proposed solution** — how you imagine it working
- **Alternatives considered** — other approaches you thought about

Features are accepted when they align with the project's architecture decisions (see `docs/adr/`).

---

## Development Setup

```bash
git clone <repo>
cd <repo>
python3.12 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install               # sets up git hooks
cp .env.example .env
# fill in .env with real values (never commit this file)
```

See [DEVELOPMENT.md](DEVELOPMENT.md) for the full local development guide.

---

## Running Tests

```bash
make test              # unit tests — fast, no external dependencies
make test-integration  # requires .env fully configured
make coverage          # coverage report — must stay ≥ 90%
make lint              # ruff + type checks
```

All tests must pass before submitting a PR. Coverage must not drop below 90%.

---

## Making Changes

### Branch naming

```
feat/short-description
fix/short-description
docs/short-description
refactor/short-description
chore/short-description
```

### Commit messages — Conventional Commits

Every commit must follow this format (enforced by pre-commit):

```
<type>(<scope>): <short description>

[optional body — explain WHY, not what]

[optional footer: BREAKING CHANGE or issue reference]
```

**Types:** `feat` | `fix` | `docs` | `style` | `refactor` | `test` | `chore` | `ci`

**Examples:**
```
feat(nodes): add node creation with type validation
fix(auth): handle expired JWT gracefully
docs: update health check ops guide
refactor(edges): extract predicate filtering to EdgePredicateFilter
test(facts): add property-based tests for TemporalBounds
```

Breaking changes: add `BREAKING CHANGE:` in the footer, or `!` after the type:
```
feat!: rename NodeType enum values to snake_case
```

---

## Pull Request Process

### Before submitting

- [ ] `make test` passes
- [ ] `make coverage` — coverage ≥ 90%
- [ ] `make lint` — no errors
- [ ] New public APIs have docstrings
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] If secrets added: `.env.example` updated with the new variable (empty value, comment explaining what it is)
- [ ] If config added: `docs/config/CONFIGURATION.md` updated
- [ ] If architecture decision made: ADR written in `docs/adr/`

### Review SLA

PRs are reviewed within 2 business days. If you haven't heard back after 3 days, ping in the issue comments.

### Merge policy

- All CI checks must pass
- At least one approval required
- No force-pushes to `main`
- Squash merge for feature branches, merge commit for release branches

---

## Architecture Decisions (ADRs)

Significant, hard-to-reverse decisions get an ADR in `docs/adr/`.
Use the template at `docs/adr/0000-template.md`.

An ADR is needed when:
1. The decision is hard to reverse later
2. A future reader would ask "why did they do it this way?"
3. There were real alternatives and a specific reason drove the choice

---

## Tech Debt

Known tech debt is tracked in `docs/tech-debt.md`.
Every entry has: description, impact, owner, and target resolution milestone.
Never leave a `TODO` in code without a corresponding entry in `docs/tech-debt.md`.

---

## Security

### Secret scanning

GitHub Secret Scanning is enabled on this repo. If you accidentally commit a secret (API key, token, password), GitHub will alert you within minutes.

**Enable push protection** (blocks commits before they reach the remote):
Repository Settings → Security → Secret scanning → Push protection → Enable

This is a one-time manual step per repo.

### Dependency vulnerability alerts

Dependabot is configured in `.github/dependabot.yml` and opens weekly PRs for Python and GitHub Actions updates.

`pip-audit` and Trivy run in CI and block merges on HIGH or CRITICAL CVEs.

### Reporting a vulnerability

Do not open a public issue for security vulnerabilities.
Use GitHub's private vulnerability reporting: Security tab → Report a vulnerability.
