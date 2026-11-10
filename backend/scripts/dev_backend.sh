#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Local development: run the application tier (API on :8000) and the
# background worker together. Ctrl-C stops both.
#
#   cd backend && ./scripts/dev_backend.sh
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate

python -m app.cli migrate        # no-op when the schema is current
python -m app.cli seed           # idempotent

python -m app.cli worker &
WORKER_PID=$!
trap 'kill $WORKER_PID 2>/dev/null || true' EXIT

exec uvicorn app.main:app --host 127.0.0.1 --port 8000
