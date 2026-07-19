#!/usr/bin/env python3
"""Execute the directly authorized five-stock official-source collection."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.parse
from collections.abc import Sequence
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import a_stock_tracker.config as config  # noqa: E402
from a_stock_tracker.qualitative.production_contexts import ContextCollectionError  # noqa: E402
from a_stock_tracker.qualitative.selected_five import (  # noqa: E402
    collect_selected_five,
    create_run_root,
    load_active_authorization,
    load_prior_responses,
    load_prior_usage,
)


def _read_only_db() -> sqlite3.Connection:
    path = Path(config.DB_PATH).resolve(strict=True)
    return sqlite3.connect(f"file:{urllib.parse.quote(str(path), safe='/')}?mode=ro", uri=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect the authorized five-stock official-document batch.")
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--as-of-date", required=True, type=date.fromisoformat)
    parser.add_argument("--execute-sources", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.execute_sources:
            raise ContextCollectionError("--execute-sources is required; no artifact, database, or request was used")
        authorization = load_active_authorization(PROJECT_ROOT, args.authorization_id)
        if authorization.as_of_date != args.as_of_date.isoformat():
            raise ContextCollectionError("as-of date differs from the selected-five grant")
        prior_http_attempts, prior_pdf_downloads = load_prior_usage(PROJECT_ROOT)
        prior_responses = load_prior_responses(PROJECT_ROOT)
        root = create_run_root(PROJECT_ROOT, args.run_id)
        conn = _read_only_db()
        try:
            result = collect_selected_five(
                conn,
                authorization=authorization,
                run_root=root,
                as_of_date=args.as_of_date,
                prior_http_attempts=prior_http_attempts,
                prior_pdf_downloads=prior_pdf_downloads,
                prior_responses=prior_responses,
            )
        finally:
            conn.close()
    except (OSError, sqlite3.Error, ContextCollectionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
