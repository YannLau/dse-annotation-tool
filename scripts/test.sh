#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
unset PYTHONPATH
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
exec .venv/bin/pytest "$@"
