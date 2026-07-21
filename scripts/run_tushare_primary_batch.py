#!/usr/bin/env python3
"""Thin entrypoint for bounded TuShare primary batch ingestion."""

from __future__ import annotations

from a_stock_tracker.data.tushare_primary_batch import main


if __name__ == "__main__":
    raise SystemExit(main())
