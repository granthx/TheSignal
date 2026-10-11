#!/usr/bin/env bash
# One-command local start: creates a virtualenv, installs dependencies, runs the site at http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r backend/requirements.txt
cd backend
exec python -m app.cli serve --host 127.0.0.1 --port "${PORT:-8000}"
