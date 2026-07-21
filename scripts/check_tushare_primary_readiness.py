#!/usr/bin/env python3
"""Thin entrypoint for TuShare primary readiness checks."""

from __future__ import annotations

from a_stock_tracker.data.tushare_primary_readiness import readiness_main


if __name__ == "__main__":
    raise SystemExit(readiness_main())
