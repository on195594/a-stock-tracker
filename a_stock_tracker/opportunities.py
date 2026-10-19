"""Bounded end-of-day price references; no trading or model calls."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from a_stock_tracker.automation import (
    Budget,
    MarketClient,
    digest,
    locked_root,
    read_state,
    save_state,
)
from a_stock_tracker.research import SHANGHAI, BudgetStop, ScreenError, call_api, frame_records

RULE = "swing-observation-v1"
REFERENCE_RULE = "price-reference-v2"
# Unadjusted-only references stay readable as published; never rewritten.
REFERENCE_RULE_V1 = "price-reference-v1"
LIMITS = (
    "量价仅作风险纪律参考。重放未发现稳定超额，不代表投资价值或交易建议。"
    "未覆盖公告、财报与资金变化；A股T+1、跳空、停牌和涨跌停可能使退出条件无法执行。"
)
SECTIONS = {"reason": "历史解释", "counter": "历史反证", "wait": "历史待核查事项"}


def today() -> date:
    return datetime.now(SHANGHAI).date()


def number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScreenError("Invalid numeric evidence")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ScreenError("Non-positive/non-finite evidence")
    return result


def validate_dates(values: Any) -> list[str]:
    if not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values):
        raise ScreenError("Invalid sessions")
    if values != sorted(set(values)) or any(date.fromisoformat(v).isoformat() != v for v in values):
        raise ScreenError("Invalid session ordering")
    return values


# Exchange ex-rights reference prices only ever lower pre_close (dividend, bonus or rights
# issue); a rise, an extreme ratio or repeated breaks are treated as a source gap.
MIN_ADJUST_FACTOR = 0.3
MAX_ADJUSTMENTS = 2
PRICE_FIELDS = ("open", "high", "low", "close", "pre_close")


def bars(
    rows: Any, sessions: list[str], *, adjust: bool = False
) -> tuple[list[dict[str, float]], list[dict[str, Any]]]:
    """Validated bars; with adjust, forward-adjust history to the latest price basis."""
    if not isinstance(rows, list) or len(rows) != len(sessions):
        raise ScreenError("Missing trading sessions")
    if [r["date"] for r in rows] != sessions:
        raise ScreenError("Duplicate, stale or missing bars")
    result: list[dict[str, float]] = []
    breaks: list[tuple[int, float]] = []
    for row in rows:
        item = {
            key: number(row[key]) for key in ("open", "high", "low", "close", "vol", "pre_close")
        }
        if (
            not item["low"]
            <= min(item["open"], item["close"])
            <= max(item["open"], item["close"])
            <= item["high"]
        ):
            raise ScreenError("Invalid OHLC")
        if result and abs(item["pre_close"] - result[-1]["close"]) > 0.011:
            factor = item["pre_close"] / result[-1]["close"]
            if not adjust or not MIN_ADJUST_FACTOR <= factor < 1:
                raise ScreenError("Discontinuous price basis; corporate action or source gap")
            breaks.append((len(result), factor))
        result.append(item)
    if len(breaks) > MAX_ADJUSTMENTS:
        raise ScreenError("Discontinuous price basis; corporate action or source gap")
    for index, factor in breaks:
        for item in result[:index]:
            for key in PRICE_FIELDS:
                item[key] *= factor
    return result, [{"date": sessions[i], "factor": round(f, 4)} for i, f in breaks]


def price_conditions(
    stock: list[dict[str, float]], benchmark: list[dict[str, float]]
) -> dict[str, Any]:
    """Compute price-reference levels from two validated 61-session windows."""
    if len(stock) != 61 or len(benchmark) != 61:
        raise ScreenError("Require 61 validated bars per series")
    closes = [r["close"] for r in stock]
    close = closes[-1]
    ma20, ma60 = sum(closes[-20:]) / 20, sum(closes[-60:]) / 60
    relative = (close / closes[-21] - benchmark[-1]["close"] / benchmark[-21]["close"]) * 100
    trigger = round(max(r["high"] for r in stock[-20:]) + 0.01, 2)
    stop = round(min(r["low"] for r in stock[-10:]), 2)
    return {
        "close": close,
        "ma20": ma20,
        "ma60": ma60,
        "relative": relative,
        "trigger": trigger,
        "stop": stop,
    }


def evaluate(packet: dict[str, Any]) -> dict[str, Any]:
    """Deterministic conditions. Reject gaps instead of letting a model invent levels."""
    sessions = validate_dates(packet["sessions"])
    future = validate_dates(packet["future_sessions"])
    if len(sessions) != 61 or len(future) != 5 or future[0] <= sessions[-1]:
        raise ScreenError("Require 61 historical and 5 future sessions")
    code, name = packet["code"], packet["name"]
    if not isinstance(code, str) or not re.fullmatch(r"(?:60\d{4}\.SH|00\d{4}\.SZ)", code):
        raise ScreenError("Only Shanghai/Shenzhen main-board identifiers")
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 30
        or "ST" in name.upper()
        or "退" in name
    ):
        raise ScreenError("Invalid issuer or known ST risk")
    stock, adjustments = bars(packet["bars"], sessions, adjust=True)
    benchmark, _ = bars(packet["benchmark"], sessions)
    c = price_conditions(stock, benchmark)
    distance = (c["close"] - c["stop"]) / c["close"] * 100
    facts = {
        "trend": f"收盘 {c['close']:.2f} 元；MA20 {c['ma20']:.2f} 元；MA60 {c['ma60']:.2f} 元。"
        + ("向上排列。" if c["close"] > c["ma20"] > c["ma60"] else "未向上排列。"),
        "relative": f"近20个交易日较沪深300价格涨幅差 {c['relative']:.2f} 个百分点（非超额收益预测）。",
        "condition": f"观察线 {c['trigger']:.2f} 元（近20日最高价 + 0.01）。",
        "invalidation": f"失效线 {c['stop']:.2f} 元（近10日最低价）。",
        "distance": f"收盘至失效线距离 {distance:.2f}%；不是最大损失承诺。",
        "limits": LIMITS,
    }
    if adjustments:
        events = "、".join(f"{a['date']}（系数 {a['factor']:.4f}）" for a in adjustments)
        facts["adjustment"] = (
            f"已按交易所除权参考价前复权：{events}；历史价格换算到最新价格口径，"
            "均线、观察线与失效线据此计算，可能不是历史实际成交价。"
        )
    return {
        "code": code,
        "name": name,
        "as_of": sessions[-1],
        "expires": future[-1],
        "status": "reference",
        "facts": facts,
        "adjustments": adjustments,
        "levels": {k: c[k] for k in ("close", "ma20", "ma60", "relative", "trigger", "stop")}
        | {"distance": distance, "uptrend": c["close"] > c["ma20"] > c["ma60"]},
    }


def validate_explanation(value: Any, facts: dict[str, str]) -> None:
    if not isinstance(value, dict) or set(value) != set(SECTIONS):
        raise ScreenError("Invalid explanation sections")
    for section in value.values():
        if not isinstance(section, dict) or set(section) != {"text", "refs"}:
            raise ScreenError("Invalid explanation shape")
        text, refs = section["text"], section["refs"]
        if not isinstance(text, str) or not 20 <= len(text) <= 240 or re.search(r"\d", text):
            raise ScreenError("Explanation must leave numeric levels to the program")
        if (
            not isinstance(refs, list)
            or not refs
            or any(not isinstance(r, str) or r not in facts for r in refs)
        ):
            raise ScreenError("Unknown evidence reference")


PRICE_FAILURE_REASONS = {
    "Missing trading sessions": (
        "missing_sessions",
        "日线数量不足，须核查停牌、缺失交易日或数据覆盖。",
    ),
    "Require 61 validated bars per series": (
        "missing_sessions",
        "日线有效K线不足61根，须核查近期停牌、新股上市或数据覆盖。",
    ),
    "Require 61 historical and 5 future sessions": (
        "calendar_gap",
        "交易日历区间不足，须核查交易日历数据覆盖。",
    ),
    "Invalid sessions": (
        "calendar_mismatch",
        "交易日历记录无效，须核查交易日历数据。",
    ),
    "Invalid session ordering": (
        "calendar_mismatch",
        "交易日历顺序异常，须核查交易日历数据。",
    ),
    "Duplicate, stale or missing bars": (
        "session_mismatch",
        "日线日期重复、过旧或缺失，须核查交易日覆盖。",
    ),
    "Discontinuous price basis; corporate action or source gap": (
        "price_discontinuity",
        "未复权价格不连续，可能涉及除权除息或来源缺口；须核查价格依据。",
    ),
    "Invalid numeric evidence": ("invalid_number", "价格或成交量不是有效数值；须核查来源字段。"),
    "Non-positive/non-finite evidence": (
        "invalid_number",
        "价格或成交量非正或非有限值；须核查停牌与来源字段。",
    ),
    "Invalid OHLC": ("invalid_ohlc", "开高低收价格关系不一致；须核查来源日线。"),
    "Issuer identity missing or duplicated": (
        "issuer_identity",
        "证券身份缺失或重复；须核查证券资料。",
    ),
    "Issuer outside supported scope": (
        "issuer_scope",
        "证券身份不符合沪深主板上市范围；须核查证券资料。",
    ),
    "Only Shanghai/Shenzhen main-board identifiers": (
        "issuer_scope",
        "证券代码不属于沪深主板支持范围，仅支持沪深主板个股。",
    ),
    "Invalid issuer or known ST risk": (
        "issuer_scope",
        "证券名称无效或存在 ST/退市标记；须核查证券资料。",
    ),
    "Mismatched price security": ("price_identity", "日线证券代码与目标不一致；须核查来源。"),
}

PRICE_FAILURE_MESSAGES = dict(PRICE_FAILURE_REASONS.values()) | {
    "missing_sessions": "日线数量不足，须核查停牌、缺失交易日或数据覆盖。",
    "session_mismatch": "日线日期重复、过旧或缺失，须核查交易日覆盖。",
    "invalid_number": "价格或成交量数值无效、非正或非有限值；须核查停牌与来源字段。",
    "issuer_scope": "证券身份不符合支持范围或存在 ST/退市标记；须核查证券资料。",
    "validation_failed": "资料或结构核验未通过；须核查本阶段来源与字段完整性。",
}


def price_diagnostic(stage: str, exc: Exception) -> dict[str, str]:
    if match := PRICE_FAILURE_REASONS.get(str(exc)):
        return {"stage": stage, "category": match[0], "message": match[1]}
    return {
        "stage": stage,
        "category": "validation_failed",
        "message": PRICE_FAILURE_MESSAGES["validation_failed"],
    }


def collected_diagnostic(value: Any) -> dict[str, str]:
    # Only accept codes we wrote; stored messages are never echoed back.
    if (
        isinstance(value, dict)
        and isinstance(value.get("stage"), str)
        and value["stage"] in {"issuer_collection", "price_collection"}
        and isinstance(value.get("category"), str)
        and value["category"] in PRICE_FAILURE_MESSAGES
    ):
        return {
            "stage": value["stage"],
            "category": value["category"],
            "message": PRICE_FAILURE_MESSAGES[value["category"]],
        }
    return price_diagnostic("issuer_collection", ScreenError())


def collect(
    client: Any, token: str, codes: list[str], as_of: date, *, latest: bool = False
) -> list[dict[str, Any]]:
    """Explicit bounded pool, no implicit all-market fetch or personal watchlist."""
    start, end = as_of - timedelta(days=240), as_of + timedelta(days=30)

    def request(endpoint: str, fields: str, **kwargs: Any) -> list[dict[str, Any]]:
        return frame_records(
            call_api(client, endpoint, token, fields=fields, **kwargs),
            set(fields.split(",")),
            endpoint,
        )

    calendar = request(
        "trade_cal",
        "exchange,cal_date,is_open",
        exchange="SSE",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    dates = [datetime.strptime(str(r["cal_date"]), "%Y%m%d").date() for r in calendar]
    if len(dates) != len(set(dates)) or set(dates) != {
        start + timedelta(days=i) for i in range((end - start).days + 1)
    }:
        raise ScreenError("Incomplete calendar")
    if any(r["exchange"] != "SSE" or str(r["is_open"]) not in {"0", "1"} for r in calendar):
        raise ScreenError("Invalid calendar evidence")
    opened = sorted(d for d, r in zip(dates, calendar, strict=True) if str(r["is_open"]) == "1")
    if latest:
        completed = [d for d in opened if d <= as_of]
        if not completed:
            raise ScreenError("No proven completed trading day")
        as_of = completed[-1]
    history = [d.isoformat() for d in opened if d <= as_of][-61:]
    future = [d.isoformat() for d in opened if d > as_of][:5]
    if len(history) != 61 or len(future) != 5 or history[-1] != as_of.isoformat():
        raise ScreenError("As-of must be a proven trading day with sufficient calendar")

    def prices(endpoint: str, code: str) -> list[dict[str, Any]]:
        rows = request(
            endpoint,
            "ts_code,trade_date,open,high,low,close,vol,pre_close",
            ts_code=code,
            start_date=history[0].replace("-", ""),
            end_date=history[-1].replace("-", ""),
        )
        if any(r["ts_code"] != code for r in rows):
            raise ScreenError("Mismatched price security")
        return sorted(
            (
                {
                    "date": datetime.strptime(str(r["trade_date"]), "%Y%m%d").date().isoformat(),
                    **{k: r[k] for k in ("open", "high", "low", "close", "vol", "pre_close")},
                }
                for r in rows
            ),
            key=lambda r: r["date"],
        )

    benchmark = prices("index_daily", "000300.SH")

    stage = "issuer_collection"

    def one_packet(code: str) -> dict[str, Any]:
        nonlocal stage
        stage = "issuer_collection"
        identities = request(
            "stock_basic", "ts_code,name,market,exchange,list_status", ts_code=code
        )
        if len(identities) != 1:
            raise ScreenError("Issuer identity missing or duplicated")
        identity = identities[0]
        if (
            identity["ts_code"] != code
            or identity["market"] != "主板"
            or identity["list_status"] != "L"
            or identity["exchange"] != ("SSE" if code.endswith(".SH") else "SZSE")
        ):
            raise ScreenError("Issuer outside supported scope")
        stage = "price_collection"
        return {
            "code": code,
            "name": identity["name"],
            "identity": identity,
            "calendar": calendar,
            "checked_at": datetime.now(SHANGHAI).isoformat(),
            "benchmark_code": "000300.SH",
            "sessions": history,
            "future_sessions": future,
            "bars": prices("daily", code),
            "benchmark": benchmark,
        }

    packets: list[dict[str, Any]] = []
    for code in codes:
        try:
            packets.append(one_packet(code))
        except ScreenError as exc:  # One unsupported issuer must not discard the others.
            packets.append(
                {"code": code, "name": "", "collect_error": price_diagnostic(stage, exc)}
            )
    return packets


def publish(directory: Path, packets: list[dict[str, Any]], budget: Budget) -> dict[str, Any]:
    results = []
    for packet in packets:
        try:
            if "collect_error" in packet:
                raise ScreenError(packet["collect_error"])
            row = evaluate(packet)
        except (
            BudgetStop,
            ScreenError,
            ValueError,
            KeyError,
            TypeError,
            OSError,
        ) as exc:
            diagnostic = (
                collected_diagnostic(packet["collect_error"])
                if "collect_error" in packet
                else price_diagnostic("price_validation", exc)
            )
            row = {
                "code": packet.get("code", "unknown"),
                "name": packet.get("name", ""),
                "status": "gap",
                "reasons": [diagnostic["message"].rstrip("。") + "；不发布价格参考。"],
                "diagnostic": diagnostic,
            }
        results.append(row)
    result = {
        "rule": REFERENCE_RULE,
        "created_at": datetime.now(SHANGHAI).isoformat(),
        "source": "TuShare daily/index_daily/trade_cal/stock_basic",
        "scope": [p["code"] for p in packets],
        "rows": results,
        "model_calls": 0,
        "requests": budget.used,
        "usage": {
            "requests": budget.used,
            "request_limit": budget.limit,
            "seconds_used": round(time.monotonic() - budget.started, 2),
            "stop_reason": budget.stop_reason,
        },
    }
    save_state(directory / "result.json", result)
    return result


def load_board(root: Path | None, *, on: date | None = None) -> dict[str, Any] | None:
    if root is None:
        return None
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ScreenError("Invalid opportunity root")
    latest = root / "latest.json"
    if not latest.exists():
        return None
    if latest.is_symlink() or latest.stat().st_size > 1_000_000:
        raise ScreenError("Invalid opportunity pointer")
    pointer = read_state(latest)
    run = pointer["run"]
    if not isinstance(run, str) or not re.fullmatch(r"swing-[0-9a-f]{32}", run):
        raise ScreenError("Invalid opportunity run")
    directory, output = root / run, root / run / "result.json"
    if directory.is_symlink() or output.is_symlink() or output.stat().st_size > 1_000_000:
        raise ScreenError("Invalid opportunity artifact")
    result = read_state(output)
    if digest(result) != pointer["digest"] or result.get("rule") not in {
        RULE,
        REFERENCE_RULE_V1,
        REFERENCE_RULE,
    }:
        raise ScreenError("Opportunity integrity mismatch")
    if not isinstance(result.get("rows"), list) or len(result["rows"]) > 5:
        raise ScreenError("Invalid bounded board")
    current = on or today()
    statuses = (
        {"excluded", "gap", "observation"} if result["rule"] == RULE else {"reference", "gap"}
    )
    for row in result["rows"]:
        if not isinstance(row, dict) or row.get("status") not in statuses:
            raise ScreenError("Invalid opportunity row")
        if row["status"] == "observation":
            validate_explanation(row["explanation"], row["facts"])
            if row.get("review", {}).get("supported") is not True:
                raise ScreenError("Unreviewed observation")
        if row["status"] in {"observation", "reference"}:
            if date.fromisoformat(row["as_of"]) > current:
                raise ScreenError("Future observation")
            row["expired"] = current > date.fromisoformat(row["expires"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument(
        "--codes", required=True, help="1–5 exact main-board codes; explicit pool, not all-market"
    )
    dating = parser.add_mutually_exclusive_group(required=True)
    dating.add_argument(
        "--as-of", type=date.fromisoformat, help="Completed trading day, YYYY-MM-DD"
    )
    dating.add_argument(
        "--latest", action="store_true", help="Resolve latest completed day from calendar"
    )
    parser.add_argument("--request-budget", type=int, default=20)
    parser.add_argument("--seconds", type=int, default=3000)
    args = parser.parse_args()
    codes = args.codes.split(",")
    now = datetime.now(SHANGHAI)
    if (
        not 1 <= len(codes) <= 5
        or len(set(codes)) != len(codes)
        or any(not re.fullmatch(r"(?:60\d{4}\.SH|00\d{4}\.SZ)", c) for c in codes)
    ):
        parser.error("Require 1–5 unique main-board codes")
    if args.latest:
        args.as_of = now.date() if now.hour >= 18 else now.date() - timedelta(days=1)
    if (
        args.as_of > now.date()
        or (args.as_of == now.date() and now.hour < 18)
        or (now.date() - args.as_of).days > 7
    ):
        parser.error("Use a completed recent trading day; same-day runs require 18:00 Shanghai")
    if not 4 <= args.request_budget <= 50 or not 300 <= args.seconds <= 3600:
        parser.error("Budget out of bounds")
    token = os.getenv("TUSHARE_TOKEN", "")
    if not token:
        parser.error("Explicit TUSHARE_TOKEN required")
    os.umask(0o077)
    import tushare as ts  # type: ignore[import-untyped]

    budget = Budget(args.request_budget, args.seconds)
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(BudgetStop("HARD_TIMEOUT")))
    signal.alarm(args.seconds)
    with locked_root(args.root) as root:
        packets = collect(
            MarketClient(ts.pro_api(token, timeout=30), budget),
            token,
            codes,
            args.as_of,
            latest=args.latest,
        )
        directory = root / ("swing-" + uuid.uuid4().hex)
        directory.mkdir(mode=0o700)
        save_state(directory / "evidence.json", {"packets": packets})
        result = publish(directory, packets, budget)
        save_state(root / "latest.json", {"run": directory.name, "digest": digest(result)})
        print(
            json.dumps(
                {
                    "run": directory.name,
                    "statuses": [r["status"] for r in result["rows"]],
                    "requests": budget.used,
                    "model_calls": result["model_calls"],
                }
            )
        )
        return 2 if any(r["status"] == "gap" for r in result["rows"]) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BudgetStop, ScreenError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
