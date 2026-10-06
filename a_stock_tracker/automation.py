"""Bounded market discovery. Own files only; never opens the personal workspace."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from a_stock_tracker.calendar import _atomic_write_bytes
from a_stock_tracker.evidence import REPORT_FIELDS, select_latest_report
from a_stock_tracker.research import (
    BudgetStop,
    ScreenError,
    average_ranks,
    call_api,
    fetch_financials,
    fetch_universe,
    finite_number,
    frame_records,
    is_financial_industry,
    iso_date,
    now_iso,
    parse_date,
    risk_status,
    select_annual_roes,
)

RULE = "market-research-v1"
MARKER = {"owner": "a-stock-tracker.automation", "schema": 1}


def encoded(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def save(path: Path, value: Any, *, immutable: bool = False) -> str:
    payload = encoded(value)
    sha = hashlib.sha256(payload).hexdigest()
    if immutable and path.exists():
        if path.read_bytes() != payload:
            raise ScreenError(f"Immutable result conflict: {path.name}")
        return sha
    _atomic_write_bytes(path, payload)
    return sha


def read_result(path: Path, sha: str | None = None) -> dict[str, Any]:
    payload = path.read_bytes()
    if sha is not None and hashlib.sha256(payload).hexdigest() != sha:
        raise ScreenError(f"Result hash mismatch: {path.name}")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ScreenError("Invalid saved object")
    return value


def save_state(path: Path, value: dict[str, Any]) -> None:
    save(path, {"sha256": digest(value), "state": value})


def read_state(path: Path) -> dict[str, Any]:
    envelope = read_result(path)
    if digest(envelope.get("state")) != envelope.get("sha256"):
        raise ScreenError("State checksum mismatch")
    return envelope["state"]


@contextmanager
def locked_root(root: Path):
    if not root.is_absolute():
        raise ScreenError("Automation root must be explicit and absolute")
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ScreenError("Symlink automation root refused")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = root / "automation.json"
    # Never initialize inside an existing workspace, cache, backup, or unrelated directory.
    if not marker.exists() and any(root.iterdir()):
        raise ScreenError("Automation root is not empty and has no owner marker")
    if marker.exists() and read_result(marker) != MARKER:
        raise ScreenError("Automation root owner mismatch")
    if root.stat().st_mode & 0o077:
        raise ScreenError("Automation root must be private (0700)")
    with (root / ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ScreenError("Automation already running") from exc
        if not marker.exists():
            save(marker, MARKER, immutable=True)
        yield root


class Budget:
    """One invocation, counted before dispatch. No hidden retries."""

    def __init__(self, requests: int, seconds: int):
        self.limit = requests
        self.used = 0
        self.deadline = time.monotonic() + seconds

    def check(self, reserve: float = 0) -> None:
        if time.monotonic() + reserve >= self.deadline:
            raise BudgetStop("TIME_BUDGET")

    def take(self) -> None:
        self.check()
        if self.used >= self.limit:
            raise BudgetStop("REQUEST_BUDGET")
        self.used += 1


class MarketClient:
    def __init__(self, client: Any, budget: Budget):
        self.client, self.budget = client, budget

    def __getattr__(self, name: str):
        def request(**kwargs):
            self.budget.take()
            time.sleep(0.35)
            return getattr(self.client, name)(**kwargs)

        return request


def target_date(client: Any, token: str, today: str) -> tuple[str, list[dict[str, Any]]]:
    end = parse_date(today) - timedelta(days=1)
    start = end - timedelta(days=35)
    rows = frame_records(
        call_api(
            client,
            "trade_cal",
            token,
            exchange="SSE",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            fields="exchange,cal_date,is_open",
        ),
        {"exchange", "cal_date", "is_open"},
        "trade_cal",
    )
    days = [iso_date(r["cal_date"]) for r in rows]
    expected = {(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)}
    if (
        len(days) != len(set(days))
        or set(days) != expected
        or any(
            r["exchange"] != "SSE"
            or type(r["is_open"]) not in (int, float)
            or r["is_open"] not in (0, 1)
            for r in rows
        )
    ):
        raise ScreenError("Incomplete or conflicting calendar")
    opened = [iso_date(r["cal_date"]) for r in rows if r["is_open"] == 1]
    if not opened:
        raise ScreenError("No proved valuation date")
    return max(opened), rows


def initial_row(basic: dict[str, Any], valuation: dict[str, Any], target: str) -> dict[str, Any]:
    code = basic.get("ts_code")
    row = {
        **basic,
        "code": code,
        "valuation_date": target,
        "pb": finite_number(valuation.get("pb")),
        "status": "pending",
        "reason": "FINANCIAL_PENDING",
    }
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", code):
        raise ScreenError("Malformed stock_basic identifier")
    if (
        not basic.get("industry")
        or not basic.get("name")
        or basic.get("list_status") != "L"
        or basic.get("exchange") not in {"SSE", "SZSE", "BSE"}
        or not basic.get("market")
    ):
        row.update(status="gap", reason="SCOPE_IDENTITY_UNKNOWN")
    elif (code.endswith(".SH") != (basic["exchange"] == "SSE")) or (
        code.endswith(".SZ") != (basic["exchange"] == "SZSE")
    ):
        row.update(status="gap", reason="IDENTITY_CONFLICT")
    elif (
        basic["exchange"] not in {"SSE", "SZSE"}
        or basic["market"] != "主板"
        or is_financial_industry(basic["industry"])
    ):
        row.update(status="excluded", reason="OUTSIDE_SCOPE")
    elif risk_status(basic["name"]) == "known_warning":
        row.update(status="excluded", reason="KNOWN_ST_WARNING")
    elif row["pb"] is None:
        row.update(status="gap", reason="MISSING_OR_INVALID_PB")
    elif row["pb"] <= 0:
        row.update(status="excluded", reason="NON_POSITIVE_PB")
    return row


def freeze(client: Any, token: str, today: str, sample: list[str]) -> dict[str, Any]:
    target, calendar = target_date(client, token, today)
    basics, valuations, times = fetch_universe(client, token, target)
    by_code = {r["ts_code"]: r for r in valuations}
    rows = [initial_row(r, by_code.get(r["ts_code"], {}), target) for r in basics]
    if len({r["code"] for r in rows}) != len(rows):
        raise ScreenError("Duplicate scope identifiers")
    if not set(sample) <= {r["code"] for r in rows}:
        raise ScreenError("Sample identifier absent from universe")
    return {
        "rule": RULE,
        "data_date": target,
        "cutoff": today,
        "created_at": now_iso(),
        "mode": "sample" if sample else "market",
        "sample": sample,
        "calendar": calendar,
        "source_times": times,
        "raw_basic": basics,
        "raw_valuation": valuations,
        "rows": rows,
    }


def evaluate(
    row: dict[str, Any], records: list[dict[str, Any]], frozen: dict[str, Any]
) -> dict[str, Any]:
    selection = select_annual_roes(records, frozen["data_date"], frozen["cutoff"])
    result = {
        **row,
        **selection,
        "records": records,
        "checked_at": now_iso(),
        "research_report": select_latest_report(records, frozen["data_date"], frozen["cutoff"]),
    }
    if selection["error"]:
        result.update(status="gap", reason=selection["error"])
    elif finite_number(selection["roe_mean"]) is None:
        result.update(status="gap", reason="INVALID_ROE_MEAN")
    elif selection["roe_mean"] <= 0:
        result.update(status="excluded", reason="NON_POSITIVE_ROE_MEAN")
    else:
        result.update(
            status="qualified",
            reason="ELIGIBLE",
            warnings=["业务可比性、净资产分母、审计及交易状态未由筛选验证"]
            + (
                ["历史年度有负ROE"]
                if any(r["roe_waa"] < 0 for r in selection["annual_roes"])
                else []
            ),
        )
    return result


def scan(
    directory: Path,
    frozen: dict[str, Any],
    state: dict[str, Any],
    client: Any,
    token: str,
    budget: Budget,
) -> list[dict[str, Any]]:
    result_rows = []
    for original in frozen["rows"]:
        code = original["code"]
        path = directory / f"scan-{code}.json"
        stored = state["scan"].get(code)
        if stored:
            result = read_result(path, stored)
        elif path.exists():
            result = read_state(path)["result"]  # recover a published result before cursor commit
        elif original["status"] != "pending" or (frozen["sample"] and code not in frozen["sample"]):
            result_rows.append(original)
            continue
        else:
            # Reserve both attempts so budget interruption cannot become a terminal data gap.
            if budget.limit - budget.used < 2:
                break
            budget.check(65)
            result = None
            for attempt in range(2):
                try:
                    records = fetch_financials(
                        client,
                        token,
                        code,
                        frozen["data_date"],
                        frozen["cutoff"],
                        extra_fields=tuple(REPORT_FIELDS),
                    )
                    result = evaluate(original, records, frozen)
                    break
                except ScreenError:
                    if attempt == 0:
                        time.sleep(1)
            if result is None:
                result = {
                    **original,
                    "status": "gap",
                    "reason": "FINANCIAL_FAILED_AFTER_TWO_ATTEMPTS",
                }
            save_state(path, {"freeze": state["freeze"], "result": result})
        envelope = read_state(path)
        result = envelope["result"]
        if envelope["freeze"] != state["freeze"] or result.get("code") != code:
            raise ScreenError("Scan result identity conflict")
        state["scan"][code] = hashlib.sha256(path.read_bytes()).hexdigest()
        save_state(directory / "state.json", state)
        result_rows.append(result)
    # Always account for the frozen universe, including the tail left by a budget stop.
    resolved = {r["code"]: r for r in result_rows}
    return [resolved.get(r["code"], r) for r in frozen["rows"]]


def coverage(rows: list[dict[str, Any]], frozen: dict[str, Any]) -> dict[str, Any]:
    counts = dict(Counter(r["status"] for r in rows))
    if len({r["code"] for r in rows}) != len(frozen["rows"]) or sum(counts.values()) != len(
        frozen["rows"]
    ):
        raise ScreenError("Coverage does not reconcile")
    industry_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["reason"] != "OUTSIDE_SCOPE":
            industry_rows[row.get("industry") or "UNKNOWN"].append(row)
    groups = []
    for industry, members in sorted(industry_rows.items()):
        if industry == "UNKNOWN" or any(r["status"] in {"gap", "pending"} for r in members):
            continue
        periods: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        for row in members:
            if row["status"] == "qualified":
                periods[tuple(r["period"] for r in row["annual_roes"])].append(row)
        for years, qualified in sorted(periods.items()):
            pb, roe = (
                average_ranks(qualified, "pb", False),
                average_ranks(qualified, "roe_mean", True),
            )
            ranks = sorted(
                (
                    {
                        "code": r["code"],
                        "pb_rank": pb[r["code"]],
                        "roe_rank": roe[r["code"]],
                        "order": (pb[r["code"]] + roe[r["code"]]) / 2,
                    }
                    for r in qualified
                ),
                key=lambda r: (r["order"], r["code"]),
            )
            groups.append(
                {
                    "industry": industry,
                    "periods": list(years),
                    "ranking": ranks,
                    "top": [r["code"] for r in ranks[:3]],
                }
            )
    if "UNKNOWN" in industry_rows:
        groups = []  # Unclassified companies could belong to any group.
    return {
        "rule": RULE,
        "mode": frozen["mode"],
        "data_date": frozen["data_date"],
        "status": "partial"
        if frozen["sample"] or counts.get("gap") or counts.get("pending")
        else "complete",
        "total": len(rows),
        "counts": counts,
        "groups": groups,
        "rows": rows,
    }


def candidates(
    summary: dict[str, Any], frozen: dict[str, Any], last_industry: str = ""
) -> list[str]:
    if frozen["sample"]:
        eligible = {r["code"] for r in summary["rows"] if r["status"] == "qualified"}
        return [c for c in frozen["sample"] if c in eligible][:5]
    groups = summary["groups"]
    groups = [g for g in groups if g["industry"] > last_industry] + [
        g for g in groups if g["industry"] <= last_industry
    ]
    return [g["top"][i] for i in range(3) for g in groups if len(g["top"]) > i][:5]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--resume", help="Frozen run id; cannot change scope or date")
    parser.add_argument(
        "--sample",
        default="",
        help="Comma-separated exact TS identifiers; validation only, never a full-market result",
    )
    parser.add_argument("--request-budget", type=int, default=4000)
    parser.add_argument("--seconds", type=int, default=14400)
    parser.add_argument("--max-research", type=int, default=5)
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--provider", default="openai-codex")
    args = parser.parse_args()
    if (
        not 3 <= args.request_budget <= 10000
        or not 60 <= args.seconds <= 21600
        or not 0 <= args.max_research <= 5
    ):
        parser.error("Budget outside supported bounds")
    if args.resume and args.sample:
        parser.error("Resume uses frozen sample/scope")
    sample = args.sample.split(",") if args.sample else []
    if len(sample) != len(set(sample)) or any(
        not re.fullmatch(r"[0-9]{6}\.(SH|SZ)", c) for c in sample
    ):
        parser.error("Invalid/duplicate literal sample identifier")
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        parser.error("TUSHARE_TOKEN is required; no production config fallback")
    os.umask(0o077)
    budget = Budget(args.request_budget, args.seconds)
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(BudgetStop("HARD_TIMEOUT")))
    signal.alarm(args.seconds)
    import tushare as ts  # type: ignore[import-untyped]

    client = MarketClient(ts.pro_api(token, timeout=30), budget)
    with locked_root(args.root) as root:
        previous = []
        for path in sorted(root.glob("run-*/state.json")):
            previous.append((path.parent, read_state(path)))
        if args.resume:
            if not re.fullmatch(r"run-[0-9T-]+-[a-f0-9]{12}", args.resume):
                raise ScreenError("Invalid run id")
            directory = root / args.resume
            state = read_state(directory / "state.json")
            frozen = read_result(directory / "universe.json", state["freeze"])
            if (parse_date(now_iso()[:10]) - parse_date(frozen["cutoff"])).days > 7:
                raise ScreenError("Frozen run expired; create a new run explicitly")
            if state["model"] != {"provider": args.provider, "model": args.model}:
                raise ScreenError("Resume model mismatch")
        else:
            frozen = freeze(client, token, now_iso()[:10], sample)
            run_id = (
                "run-" + frozen["created_at"][:19].replace(":", "-") + "-" + digest(frozen)[:12]
            )
            directory = root / run_id
            directory.mkdir(mode=0o700)
            sha = save(directory / "universe.json", frozen, immutable=True)
            state = {
                "freeze": sha,
                "scan": {},
                "research": {},
                "queue": [],
                "cursor": 0,
                "model_calls": 0,
                "model": {"provider": args.provider, "model": args.model},
                "last_industry": previous[-1][1].get("last_industry", "") if previous else "",
                "notification": "not_enabled",
            }
            save_state(directory / "state.json", state)
        try:
            rows = scan(directory, frozen, state, client, token, budget)
        except BudgetStop:
            rows = [
                read_result(directory / f"scan-{r['code']}.json", state["scan"][r["code"]])[
                    "state"
                ]["result"]
                if r["code"] in state["scan"]
                else r
                for r in frozen["rows"]
            ]
        summary = coverage(rows, frozen)
        save(directory / "coverage.json", summary)
        # Newly completed industries can fill unused slots after a partial scan resumes.
        state["queue"] = (
            state["queue"]
            + [
                c
                for c in candidates(summary, frozen, state["last_industry"])
                if c not in state["queue"]
            ]
        )[:5]
        save_state(directory / "state.json", state)
        if args.max_research:
            from a_stock_tracker.disclosures import research_batch

            research_batch(directory, frozen, summary, state, previous, budget, args.max_research)
        save(
            directory / "invocation.json",
            {
                "finished_at": now_iso(),
                "requests": budget.used,
                "request_limit": budget.limit,
                "coverage": summary["status"],
                "cursor": state["cursor"],
                "candidates": len(state["queue"]),
                "notification": "not_enabled",
            },
        )
        print(
            json.dumps(
                {
                    "run": str(directory),
                    "coverage": summary["status"],
                    "counts": summary["counts"],
                    "reports": len(state["research"]),
                    "pending_candidates": len(state["queue"]) - state["cursor"],
                    "requests": budget.used,
                },
                ensure_ascii=False,
            )
        )
        return (
            0 if summary["status"] == "complete" and state["cursor"] == len(state["queue"]) else 2
        )


if __name__ == "__main__":
    # Keep one module identity for BudgetStop when disclosures imports this module.
    from a_stock_tracker import automation as owner

    try:
        raise SystemExit(owner.main())
    except (ScreenError, owner.BudgetStop, OSError, ValueError) as exc:
        print(
            json.dumps({"status": "failed", "error_type": type(exc).__name__}, ensure_ascii=False)
        )
        raise SystemExit(1) from None
