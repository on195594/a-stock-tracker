#!/usr/bin/env python3
"""Execute the approved one-stock official-document production pilot."""

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
from qualitative_v2_orient_cable_pilot import (  # noqa: E402
    collect_orient_cable_context,
    create_pilot_run_root,
    load_prior_pdf_attempts,
    load_prior_source_attempts,
)
from qualitative_v2_production_contexts import (  # noqa: E402
    ContextCollectionError,
    load_active_authorization,
)


def _read_only_db() -> sqlite3.Connection:
    path = Path(config.DB_PATH).resolve(strict=True)
    uri = f"file:{urllib.parse.quote(str(path), safe='/')}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect the approved 603606 official-document context.")
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--as-of-date", required=True, type=date.fromisoformat)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--execute-sources", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.execute_sources:
            raise ContextCollectionError("--execute-sources is required; no artifact, database, or request was used")
        authorization = load_active_authorization(
            PROJECT_ROOT,
            authorization_id=args.authorization_id,
            scope="orient-cable",
        )
        if authorization.as_of_date != args.as_of_date.isoformat():
            raise ContextCollectionError("as-of-date does not match the active production grant")
        prior_http_attempts = load_prior_source_attempts(PROJECT_ROOT)
        prior_pdf_attempts = load_prior_pdf_attempts(PROJECT_ROOT)
        if prior_http_attempts >= authorization.http_attempt_limit:
            raise ContextCollectionError("no authorized official source attempts remain")
        run_root = create_pilot_run_root(PROJECT_ROOT, args.run_id)
        connection = _read_only_db()
        try:
            result = collect_orient_cable_context(
                connection,
                authorization=authorization,
                run_root=run_root,
                as_of_date=args.as_of_date,
                prior_http_attempts=prior_http_attempts,
                prior_pdf_attempts=prior_pdf_attempts,
            )
        finally:
            connection.close()
    except (OSError, sqlite3.Error, ContextCollectionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, ensure_ascii=False, sort_keys=True))
    return 0 if result["score_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
