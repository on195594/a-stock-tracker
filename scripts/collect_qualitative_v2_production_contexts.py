#!/usr/bin/env python3
"""Collect an approved real-watchlist qualitative-v2 context batch."""

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

import config  # noqa: E402
from qualitative_v2_production_contexts import (  # noqa: E402
    ContextCollectionError,
    collect_production_contexts,
    create_context_run_root,
    load_active_authorization,
    load_prior_http_attempts,
)


def _companies(scope: str) -> dict[str, tuple[str, str]]:
    watchlist = {str(item["code"]): str(item["name"]) for item in config.WATCHLIST}
    if len(watchlist) != len(config.WATCHLIST):
        raise ContextCollectionError("production watchlist contains duplicate codes")
    codes = sorted(watchlist if scope == "all" else config.QUALITATIVE_V2_LEGACY_CANARY_CODES)
    expected = 35 if scope == "all" else 5
    if len(codes) != expected or any(code not in watchlist for code in codes):
        raise ContextCollectionError(f"{scope} company scope drift")
    return {code: (watchlist[code], config.QUALITATIVE_V2_INDUSTRIES.get(code, "未知")) for code in codes}


def _read_only_db() -> sqlite3.Connection:
    path = Path(config.DB_PATH).resolve(strict=True)
    uri = f"file:{urllib.parse.quote(str(path), safe='/')}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect bounded CNINFO snippets into sealed v2 context files.")
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--scope", required=True, choices=("canary", "all"))
    parser.add_argument("--as-of-date", required=True, type=date.fromisoformat)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--execute-cninfo", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.execute_cninfo:
            raise ContextCollectionError("--execute-cninfo is required; no artifact, database, or request was used")
        authorization = load_active_authorization(
            PROJECT_ROOT,
            authorization_id=args.authorization_id,
            scope=args.scope,
        )
        companies = _companies(args.scope)
        prior_http_attempts = load_prior_http_attempts(
            PROJECT_ROOT,
            authorization_id=args.authorization_id,
            scope=args.scope,
        )
        http_limit = authorization.http_attempt_limit
        if prior_http_attempts >= http_limit:
            raise ContextCollectionError("no authorized CNINFO HTTP attempts remain")
        run_root = create_context_run_root(PROJECT_ROOT, args.run_id)
        connection = _read_only_db()
        try:
            result = collect_production_contexts(
                connection,
                companies,
                authorization=authorization,
                scope=args.scope,
                run_root=run_root,
                as_of_date=args.as_of_date,
                prior_http_attempts=prior_http_attempts,
            )
        finally:
            connection.close()
    except (OSError, sqlite3.Error, ContextCollectionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
