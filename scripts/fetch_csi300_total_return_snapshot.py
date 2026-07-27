"""Fetch and validate a frozen CSI 300 total-return benchmark snapshot."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import tushare as ts

SCHEMA_VERSION = 1
SOURCE = "tushare.index_daily"
TOTAL_RETURN_SYMBOL = "H00300.CSI"
PRICE_SYMBOL = "000300.SH"
RATIO_DECREASE_TOLERANCE = 5e-6


class SnapshotContractError(RuntimeError):
    """Raised when fetched benchmark data fails the snapshot contract."""


@dataclass(frozen=True)
class Args:
    """Validated command-line arguments."""

    start_date: str
    end_date: str
    output: Path


def _parse_cli_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected date in YYYY-MM-DD format") from exc


def parse_args(argv: Sequence[str] | None = None) -> Args:
    """Parse and validate command-line arguments."""
    parser = argparse.ArgumentParser(description="Fetch a CSI 300 total-return index snapshot.")
    parser.add_argument("--start-date", required=True, type=_parse_cli_date)
    parser.add_argument("--end-date", required=True, type=_parse_cli_date)
    parser.add_argument("--output", required=True, type=Path)
    ns = parser.parse_args(argv)
    if ns.start_date > ns.end_date:
        parser.error("--start-date must not be after --end-date")
    if ns.output.suffix.lower() != ".json":
        parser.error("--output must have a .json suffix")
    return Args(ns.start_date, ns.end_date, ns.output)


def _create_api() -> Any:
    token = os.environ.get("TUSHARE_TOKEN")
    return ts.pro_api(token) if token else ts.pro_api()


def _normalize_rows(frame: Any, symbol: str) -> list[dict[str, float | str]]:
    if not isinstance(frame, pd.DataFrame):
        raise SnapshotContractError(f"{symbol}: expected pandas.DataFrame")
    if frame.empty:
        raise SnapshotContractError(f"{symbol}: no rows returned")
    missing = {"trade_date", "close"} - set(frame.columns)
    if missing:
        raise SnapshotContractError(f"{symbol}: missing columns: {', '.join(sorted(missing))}")

    normalized: list[dict[str, float | str]] = []
    seen_dates: set[str] = set()
    for raw_date, raw_close in frame[["trade_date", "close"]].itertuples(index=False, name=None):
        try:
            trade_date = datetime.strptime(str(raw_date), "%Y%m%d").date().isoformat()
        except ValueError as exc:
            raise SnapshotContractError(f"{symbol}: invalid trade_date: {raw_date}") from exc
        if trade_date in seen_dates:
            raise SnapshotContractError(f"{symbol}: duplicate trade_date: {trade_date}")
        if isinstance(raw_close, bool):
            raise SnapshotContractError(f"{symbol}: invalid close on {trade_date}")
        try:
            close = float(raw_close)
        except (TypeError, ValueError) as exc:
            raise SnapshotContractError(f"{symbol}: invalid close on {trade_date}") from exc
        if not math.isfinite(close) or close <= 0:
            raise SnapshotContractError(f"{symbol}: invalid close on {trade_date}")
        seen_dates.add(trade_date)
        normalized.append({"trade_date": trade_date, "close": close})
    return sorted(normalized, key=lambda row: str(row["trade_date"]))


def _validate_total_return(
    total_return_rows: Sequence[dict[str, float | str]],
    price_rows: Sequence[dict[str, float | str]],
) -> None:
    total_by_date = {str(row["trade_date"]): float(row["close"]) for row in total_return_rows}
    price_by_date = {str(row["trade_date"]): float(row["close"]) for row in price_rows}
    common_dates = sorted(total_by_date.keys() & price_by_date.keys())
    if len(common_dates) < 2:
        raise SnapshotContractError("fewer than two common trading dates")

    previous_date = common_dates[0]
    previous_ratio = total_by_date[previous_date] / price_by_date[previous_date]
    for trade_date in common_dates[1:]:
        ratio = total_by_date[trade_date] / price_by_date[trade_date]
        relative_change = ratio / previous_ratio - 1.0
        if relative_change < -RATIO_DECREASE_TOLERANCE:
            raise SnapshotContractError(
                "total-return/price ratio decreased beyond tolerance: "
                f"{previous_date}->{trade_date} ({relative_change:.12g})"
            )
        previous_date = trade_date
        previous_ratio = ratio

    first_date, last_date = common_dates[0], common_dates[-1]
    total_return = total_by_date[last_date] / total_by_date[first_date] - 1.0
    price_return = price_by_date[last_date] / price_by_date[first_date] - 1.0
    if total_return < price_return:
        raise SnapshotContractError(
            f"total-return index underperformed price index: {total_return:.12g} < {price_return:.12g}"
        )


def _write_snapshot(output: Path, rows: Sequence[dict[str, float | str]]) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "symbol": TOTAL_RETURN_SYMBOL,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "rows": list(rows),
    }
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        temporary_path.replace(output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def run(args: Args, api: Any | None = None) -> int:
    """Fetch, validate, and write one snapshot."""
    try:
        client = api if api is not None else _create_api()
        query = {
            "start_date": args.start_date.replace("-", ""),
            "end_date": args.end_date.replace("-", ""),
        }
        total_rows = _normalize_rows(
            client.index_daily(ts_code=TOTAL_RETURN_SYMBOL, **query),
            TOTAL_RETURN_SYMBOL,
        )
        price_rows = _normalize_rows(
            client.index_daily(ts_code=PRICE_SYMBOL, **query),
            PRICE_SYMBOL,
        )
        _validate_total_return(total_rows, price_rows)
        _write_snapshot(args.output, total_rows)
    except Exception as exc:
        print(f"CSI300_TOTAL_RETURN_SNAPSHOT_FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
