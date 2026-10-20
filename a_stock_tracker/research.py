"""Shared A-share financial evidence validation, eligibility and ranking formulas."""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

MAX_REPORT_AGE_DAYS = 550
SHANGHAI = ZoneInfo("Asia/Shanghai")
FINANCIAL_INDUSTRY_WORDS = (
    "银行",
    "证券",
    "保险",
    "多元金融",
    "金融服务",
    "信托",
    "期货",
)


class ScreenError(RuntimeError):
    pass


class DataError(ScreenError):
    """Host-owned diagnostics: never serialize provider messages or request parameters."""

    def __init__(self, endpoint: str, category: str, message: str):
        super().__init__(message)
        self.diagnostic = {
            "stage": "data_preparation",
            "endpoint": endpoint
            if endpoint in {"trade_cal", "stock_basic", "daily_basic", "fina_indicator"}
            else "other",
            "category": category,
        }


def public_data_diagnostic(value: Any) -> dict[str, str] | None:
    messages = {
        "timeout": "数据准备超时",
        "request_failed": "数据来源请求失败",
        "missing_fields": "数据来源缺少必需字段",
        "empty_data": "数据来源返回空结果",
        "truncated_data": "数据来源返回的证券范围不完整",
        "duplicate_security": "数据来源返回重复证券",
        "date_mismatch": "数据日期与本次请求不符",
    }
    if not isinstance(value, dict) or value.get("stage") != "data_preparation":
        return None
    category = value.get("category")
    if not isinstance(category, str) or category not in messages:
        return None
    return {
        "stage": "data_preparation",
        "category": category,
        "message": messages[category] + "；本次未生成报告，不会自动重试。",
    }


class BudgetStop(RuntimeError):
    """Acquisition was interrupted, not a failed observation."""


def now_iso() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def parse_date(value: Any) -> date:
    text = str(value or "").strip()
    if not re.fullmatch(r"(?:[0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2})", text):
        raise ScreenError(f"无效日期: {value!r}")
    try:
        return datetime.strptime(text.replace("-", ""), "%Y%m%d").date()
    except ValueError as exc:
        raise ScreenError(f"无效日期: {value!r}") from exc


def iso_date(value: Any) -> str:
    return parse_date(value).isoformat()


def finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_flag(value: Any) -> str:
    number = finite_number(value)
    if number is not None and number.is_integer():
        return str(int(number))
    return str(value or "").strip()


def normalize_code(value: str) -> str:
    text = value.strip().upper()
    match = re.fullmatch(r"(\d{6})(?:\.(SH|SZ|BJ))?", text)
    if not match:
        raise ScreenError(f"无效股票代码: {value!r}")
    base, suffix = match.groups()
    suffix = suffix or ("SH" if base.startswith(("5", "6", "9")) else "SZ")
    return f"{base}.{suffix}"


def frame_records(frame: Any, required: set[str], endpoint: str) -> list[dict[str, Any]]:
    columns = set(getattr(frame, "columns", []))
    missing = required - columns
    if missing:
        raise DataError(
            endpoint, "missing_fields", f"{endpoint} 缺少字段: {', '.join(sorted(missing))}"
        )
    return json.loads(frame.to_json(orient="records", force_ascii=False, date_format="iso"))


def call_api(client: Any, endpoint: str, token: str, **params: Any) -> Any:
    try:
        return getattr(client, endpoint)(**params)
    except BudgetStop:
        raise
    except Exception as exc:
        # Even a token-redacted provider message may contain headers or private URLs.
        category = (
            "timeout"
            if isinstance(exc, TimeoutError)
            or type(exc).__name__ in {"Timeout", "ReadTimeout", "ConnectTimeout"}
            else "request_failed"
        )
        raise DataError(endpoint, category, f"{endpoint} 请求失败 ({category})") from exc


def fetch_universe(
    client: Any, token: str, data_date: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    basic_frame = call_api(
        client,
        "stock_basic",
        token,
        exchange="",
        list_status="L",
        fields="ts_code,symbol,name,industry,market,exchange,list_status",
    )
    basic = frame_records(
        basic_frame,
        {"ts_code", "name", "industry", "market", "exchange", "list_status"},
        "stock_basic",
    )
    stock_basic_acquired_at = now_iso()
    if not basic or len(basic) >= 6000:
        raise DataError(
            "stock_basic",
            "empty_data" if not basic else "truncated_data",
            "stock_basic 为空或疑似达到 6000 行截断上限",
        )
    if len({str(row["ts_code"]) for row in basic}) != len(basic):
        raise DataError("stock_basic", "duplicate_security", "stock_basic 含重复证券代码")

    valuation_frame = call_api(
        client,
        "daily_basic",
        token,
        trade_date=data_date.replace("-", ""),
        fields="ts_code,trade_date,close,pe_ttm,pb,total_mv,dv_ttm",
    )
    valuation = frame_records(
        valuation_frame, {"ts_code", "trade_date", "pb", "total_mv"}, "daily_basic"
    )
    daily_basic_acquired_at = now_iso()
    if not valuation or len(valuation) >= 6000:
        raise DataError(
            "daily_basic",
            "empty_data" if not valuation else "truncated_data",
            "daily_basic 为空或疑似达到 6000 行截断上限",
        )
    if len({str(row["ts_code"]) for row in valuation}) != len(valuation):
        raise DataError("daily_basic", "duplicate_security", "daily_basic 含重复证券代码")
    dates = {iso_date(row["trade_date"]) for row in valuation}
    if dates != {data_date}:
        raise DataError("daily_basic", "date_mismatch", "daily_basic 返回了非目标日期")
    return (
        basic,
        valuation,
        {
            "stock_basic_acquired_at": stock_basic_acquired_at,
            "daily_basic_acquired_at": daily_basic_acquired_at,
        },
    )


def risk_status(name: Any) -> str:
    normalized = str(name or "").strip().upper()
    if not normalized:
        return "unknown"
    if normalized.startswith(("ST", "*ST")):
        return "known_warning"
    return "name_check_clear_other_risks_unknown"


def is_financial_industry(industry: str) -> bool:
    return any(word in industry for word in FINANCIAL_INDUSTRY_WORDS)


def select_annual_roes(
    records: list[dict[str, Any]], data_date: str, screened_at: str
) -> dict[str, Any]:
    data_day = parse_date(data_date)
    screened_day = parse_date(screened_at[:10])
    deduped: dict[str, dict[str, Any]] = {}
    for raw in records:
        try:
            period = iso_date(raw.get("end_date"))
            announced = iso_date(raw.get("ann_date"))
        except ScreenError:
            return {"annual_roes": [], "roe_mean": None, "error": "INVALID_REPORT_DATE"}
        if announced < period:
            return {"annual_roes": [], "roe_mean": None, "error": "INVALID_REPORT_DATE"}
        if (
            not period.endswith("-12-31")
            or parse_date(period) > data_day
            or parse_date(announced) > screened_day
        ):
            continue
        row = {
            "period": period,
            "ann_date": announced,
            "roe_waa": finite_number(raw.get("roe_waa")),
            "update_flag": normalize_flag(raw.get("update_flag")),
            "source": str(raw.get("source") or ""),
            "acquired_at": raw.get("acquired_at"),
        }
        key = json.dumps(
            {name: value for name, value in row.items() if name != "acquired_at"},
            ensure_ascii=False,
            sort_keys=True,
        )
        previous = deduped.get(key)
        if previous is None or str(row.get("acquired_at") or "") > str(
            previous.get("acquired_at") or ""
        ):
            deduped[key] = row

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in deduped.values():
        groups.setdefault(row["period"], []).append(row)
    periods = sorted(groups, reverse=True)[:3]
    if len(periods) < 3:
        return {
            "annual_roes": [],
            "roe_mean": None,
            "error": "MISSING_THREE_ANNUAL_REPORTS",
        }
    years = [int(period[:4]) for period in periods]
    if years != [years[0], years[0] - 1, years[0] - 2]:
        return {
            "annual_roes": [],
            "roe_mean": None,
            "error": "NON_CONSECUTIVE_ANNUAL_REPORTS",
        }
    if (data_day - parse_date(periods[0])).days > MAX_REPORT_AGE_DAYS:
        return {
            "annual_roes": [],
            "roe_mean": None,
            "error": "LATEST_ANNUAL_REPORT_TOO_OLD",
        }

    selected: list[dict[str, Any]] = []
    for period in periods:
        rows = groups[period]
        revisions = [row for row in rows if row["update_flag"] == "1"]
        if len(revisions) == 1:
            chosen = revisions[0]
            basis = "unique_update_flag_1"
        elif not revisions and len(rows) == 1 and rows[0]["update_flag"] == "0":
            chosen = rows[0]
            basis = "single_original_update_flag_0"
        else:
            return {
                "annual_roes": [],
                "roe_mean": None,
                "error": f"AMBIGUOUS_VERSION:{period}",
            }
        if chosen["roe_waa"] is None:
            return {
                "annual_roes": [],
                "roe_mean": None,
                "error": f"INVALID_ROE:{period}",
            }
        selected.append({**chosen, "selection_basis": basis})
    selected.sort(key=lambda row: row["period"])
    return {
        "annual_roes": selected,
        "roe_mean": sum(float(row["roe_waa"]) for row in selected) / 3,
        "error": None,
    }


def fetch_financials(
    client: Any,
    token: str,
    code: str,
    data_date: str,
    screened_at: str,
    *,
    extra_fields: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    end = parse_date(screened_at[:10])
    start = date(max(1990, parse_date(data_date).year - 6), 1, 1)
    frame = call_api(
        client,
        "fina_indicator",
        token,
        ts_code=normalize_code(code),
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        fields=",".join(
            ("ts_code", "ann_date", "end_date", "update_flag", "roe_waa", *extra_fields)
        ),
    )
    records = frame_records(
        frame,
        {"ts_code", "ann_date", "end_date", "update_flag", "roe_waa"},
        "fina_indicator",
    )
    if len(records) >= 100:
        raise ScreenError(f"{code} fina_indicator 疑似达到 100 行截断上限")
    if len({json.dumps(row, sort_keys=True) for row in records}) != len(records):
        raise ScreenError(f"{code} fina_indicator 返回重复记录")
    acquired_at = now_iso()
    requested_code = normalize_code(code)
    for row in records:
        if row.get("ts_code") != requested_code:
            raise ScreenError(f"{code} fina_indicator 包含错配代码: {row.get('ts_code')}")
        row["source"] = "tushare.fina_indicator"
        row["acquired_at"] = acquired_at
    return records


def average_ranks(rows: list[dict[str, Any]], field: str, reverse: bool) -> dict[str, float]:
    ordered = sorted(rows, key=lambda row: float(row[field]), reverse=reverse)
    ranks: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][field] == ordered[index][field]:
            end += 1
        rank = ((index + 1) + end) / 2
        for row in ordered[index:end]:
            ranks[row["code"]] = rank
        index = end
    return ranks
