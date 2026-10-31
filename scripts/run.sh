#!/usr/bin/env bash
set -euo pipefail
unset PYTHONPATH
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then uv sync --frozen --group dev; fi
if [ ! -d frontend/dist ]; then (cd frontend && npm ci && npm run build); fi
exec .venv/bin/python -m dse_tool.api "$@"
