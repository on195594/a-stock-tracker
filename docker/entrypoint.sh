#!/bin/sh
set -eu
if [ $# -gt 0 ]; then
    exec "$@"
fi
exec python -m a_stock_tracker.app
