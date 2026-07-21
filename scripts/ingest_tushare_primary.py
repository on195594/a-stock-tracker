#!/usr/bin/env python3
"""Thin entrypoint for isolated TuShare primary-source shadow ingestion."""

from __future__ import annotations

from a_stock_tracker.data.tushare_primary_ingestion import main


if __name__ == "__main__":
    raise SystemExit(main())
