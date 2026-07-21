#!/usr/bin/env python3
"""Thin entrypoint for atomic TuShare primary materialization."""

from __future__ import annotations

from a_stock_tracker.data.tushare_primary_materialization import main


if __name__ == "__main__":
    raise SystemExit(main())
