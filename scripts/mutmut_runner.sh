#!/usr/bin/env bash
# Runner script for mutmut mutation testing.
# Invoked by mutmut as: bash scripts/mutmut_runner.sh
# Must exit 0 when tests pass, non-zero when they fail.
set -euo pipefail
APP_ENV=dev python3 -m pytest tests/ -x -q --tb=no -m "not integration"
