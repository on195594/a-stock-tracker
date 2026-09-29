#!/bin/sh
set -eu
if [ $# -gt 0 ]; then
    exec "$@"
fi
# Production startup never creates an empty workspace over a forgotten migration.
python -m a_stock_tracker.manage list --mode production --state-dir "${STATE_DIR:-/app/data/research}" >/dev/null
exec python -m a_stock_tracker.app
