#!/usr/bin/env bash
# Runner script for mutmut mutation testing.
# Finds pytest via the same Python that's running mutmut (works in venv + CI).
PYTHON=$(python3 -c "import sys; print(sys.executable)" 2>/dev/null || python -c "import sys; print(sys.executable)")
APP_ENV=dev "$PYTHON" -m pytest tests/ -x -q --tb=no -m "not integration"
