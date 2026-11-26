#!/usr/bin/env bash
# Build and serve the integrated dashboard and API from one local origin.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -d frontend/node_modules ]]; then
  npm --prefix frontend ci
fi
npm --prefix frontend run build
nijsetu_python=python3
if [[ -x .venv/bin/python ]]; then
  nijsetu_python=.venv/bin/python
fi
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$nijsetu_python" -m nijsetu.web "$@"
