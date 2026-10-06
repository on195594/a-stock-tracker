#!/usr/bin/env python3
"""Small, read-only peer screen for personal A-share research."""

from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

RULE = "peer-screen-v1"
SCHEMA_VERSION = 1
CAP = 50
TOP_N = 3
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


def base_code(value: str) -> str:
    return normalize_code(value).split(".", 1)[0]


def frame_records(frame: Any, required: set[str], endpoint: str) -> list[dict[str, Any]]:
    columns = set(getattr(frame, "columns", []))
    missing = required - columns
    if missing:
        raise ScreenError(f"{endpoint} 缺少字段: {', '.join(sorted(missing))}")
    return json.loads(frame.to_json(orient="records", force_ascii=False, date_format="iso"))


def call_api(client: Any, endpoint: str, token: str, **params: Any) -> Any:
    try:
        return getattr(client, endpoint)(**params)
    except BudgetStop:
        raise
    except Exception as exc:
        message = str(exc).replace(token, "[REDACTED]")
        raise ScreenError(f"{endpoint} 请求失败: {message}") from exc


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
        raise ScreenError("stock_basic 为空或疑似达到 6000 行截断上限")
    if len({str(row["ts_code"]) for row in basic}) != len(basic):
        raise ScreenError("stock_basic 含重复证券代码")

    valuation_frame = call_api(
        client,
        "daily_basic",
        token,
        trade_date=data_date.replace("-", ""),
        fields="ts_code,trade_date,pb,total_mv",
    )
    valuation = frame_records(
        valuation_frame, {"ts_code", "trade_date", "pb", "total_mv"}, "daily_basic"
    )
    daily_basic_acquired_at = now_iso()
    if not valuation or len(valuation) >= 6000:
        raise ScreenError("daily_basic 为空或疑似达到 6000 行截断上限")
    if len({str(row["ts_code"]) for row in valuation}) != len(valuation):
        raise ScreenError("daily_basic 含重复证券代码")
    dates = {iso_date(row["trade_date"]) for row in valuation}
    if dates != {data_date}:
        raise ScreenError(f"daily_basic 返回了非目标日期: {sorted(dates)}")
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


def load_peers(
    stock_rows: list[dict[str, Any]],
    valuation_rows: list[dict[str, Any]],
    anchor: str,
    watchlist_codes: list[str],
    cap: int = CAP,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]]]:
    stocks = {normalize_code(str(row["ts_code"])): dict(row) for row in stock_rows}
    anchor = normalize_code(anchor)
    reference = stocks.get(anchor)
    if reference is None:
        raise ScreenError(f"stock_basic 中没有参照公司 {anchor}")
    if (
        reference.get("list_status") != "L"
        or reference.get("exchange") not in {"SSE", "SZSE"}
        or reference.get("market") != "主板"
    ):
        raise ScreenError("参照公司不是当前沪深主板上市公司")
    industry = str(reference.get("industry") or "").strip()
    if not industry:
        raise ScreenError("参照公司行业不明")
    if is_financial_industry(industry):
        raise ScreenError(f"金融行业不适用 peer-screen-v1: {industry}")

    valuations = {normalize_code(str(row["ts_code"])): dict(row) for row in valuation_rows}
    peers = [
        row
        for code, row in stocks.items()
        if str(row.get("industry") or "").strip() == industry
        and row.get("list_status") == "L"
        and row.get("exchange") in {"SSE", "SZSE"}
        and row.get("market") == "主板"
    ]
    if reference not in peers:
        raise ScreenError("参照公司未进入同业枚举")

    eligible_others: list[tuple[float, str, dict[str, Any]]] = []
    missing_valuation: list[str] = []
    for row in peers:
        code = normalize_code(str(row["ts_code"]))
        if code == anchor:
            continue
        total_mv = finite_number(valuations.get(code, {}).get("total_mv"))
        if total_mv is None or total_mv <= 0:
            missing_valuation.append(code)
        else:
            eligible_others.append((total_mv, code, row))
    eligible_others.sort(key=lambda item: (-item[0], item[1]))
    selected = [reference, *(item[2] for item in eligible_others[: max(0, cap - 1)])]
    selected_codes = [normalize_code(str(row["ts_code"])) for row in selected]
    scope = {
        "industry": industry,
        "industry_source": "tushare.stock_basic",
        "enumerated_count": len(peers),
        "enumerated_codes": sorted(normalize_code(str(row["ts_code"])) for row in peers),
        "selected_codes": selected_codes,
        "excluded_missing_valuation": sorted(missing_valuation),
        "excluded_by_cap": [item[1] for item in eligible_others[max(0, cap - 1) :]],
        "cap": cap,
        "market_bias": "同日总市值降序，偏向较大公司",
        "watchlist_codes": sorted(watchlist_codes),
    }
    return selected, scope, valuations


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


def load_inputs(
    selected: list[dict[str, Any]],
    valuations: dict[str, dict[str, Any]],
    reference: dict[str, Any],
    data_date: str,
    client: Any,
    token: str,
    source_times: dict[str, str],
) -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    screened_at = now_iso()
    watchlist_codes = set(reference["watchlist_codes"])
    for index, basic in enumerate(selected):
        code = normalize_code(str(basic["ts_code"]))
        valuation = valuations.get(code, {})
        pb = finite_number(valuation.get("pb"))
        total_mv = finite_number(valuation.get("total_mv"))
        status = risk_status(basic.get("name"))
        exclusions: list[str] = []
        if status == "known_warning":
            exclusions.append("KNOWN_ST_WARNING")
        if pb is None or pb <= 0:
            exclusions.append("INVALID_PB")

        financial: dict[str, Any] = {
            "annual_roes": [],
            "roe_mean": None,
            "error": "NOT_FETCHED",
        }
        financial_source = "none"
        financial_checked_at: str | None = None
        if not exclusions or (pb is not None and pb <= 0):
            if index:
                time.sleep(0.35)
            try:
                records = fetch_financials(client, token, code, data_date, screened_at)
                financial = select_annual_roes(records, data_date, screened_at)
                financial_source = "tushare.fina_indicator"
                if financial["error"] is None:
                    financial_checked_at = now_iso()
            except ScreenError:
                financial = {
                    "annual_roes": [],
                    "roe_mean": None,
                    "error": "FINANCIAL_REQUEST_FAILED",
                }
                financial_source = "tushare.fina_indicator:error"
        if financial["error"]:
            exclusions.append(str(financial["error"]))
        roe_mean = finite_number(financial.get("roe_mean"))
        if roe_mean is not None and roe_mean <= 0:
            exclusions.append("NON_POSITIVE_ROE_MEAN")
        rows.append(
            {
                "code": code,
                "name": str(basic.get("name") or ""),
                "industry": str(basic.get("industry") or ""),
                "market": basic.get("market"),
                "exchange": basic.get("exchange"),
                "list_status": basic.get("list_status"),
                "in_watchlist": base_code(code) in watchlist_codes,
                "pb": pb,
                "total_mv": total_mv,
                "valuation_date": data_date,
                "basic_source": "tushare.stock_basic",
                "basic_acquired_at": source_times["stock_basic_acquired_at"],
                "valuation_source": "tushare.daily_basic",
                "valuation_acquired_at": source_times["daily_basic_acquired_at"],
                "annual_roes": financial["annual_roes"],
                "roe_mean": roe_mean,
                "financial_source": financial_source,
                "financial_checked_at": financial_checked_at,
                "risk_status": status,
                "risk_source": "tushare.stock_basic.name",
                "exclusions": sorted(set(exclusions)),
            }
        )
    return rows, screened_at


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


def rank_peers(
    rows: list[dict[str, Any]], anchor: str, watchlist_codes: list[str]
) -> dict[str, Any]:
    qualified = [
        row
        for row in rows
        if not row.get("exclusions")
        and finite_number(row.get("pb")) is not None
        and finite_number(row.get("roe_mean")) is not None
        and float(row["pb"]) > 0
        and float(row["roe_mean"]) > 0
        and len(row.get("annual_roes", [])) == 3
    ]
    roe_ranks = average_ranks(qualified, "roe_mean", reverse=True)
    pb_ranks = average_ranks(qualified, "pb", reverse=False)
    ranking = [
        {
            "code": row["code"],
            "roe_rank": roe_ranks[row["code"]],
            "pb_rank": pb_ranks[row["code"]],
            "research_order": (roe_ranks[row["code"]] + pb_ranks[row["code"]]) / 2,
        }
        for row in qualified
    ]
    ranking.sort(key=lambda item: (item["research_order"], item["code"]))
    for position, item in enumerate(ranking, 1):
        item["position"] = position
    anchor = normalize_code(anchor)
    anchor_item = next((item for item in ranking if item["code"] == anchor), None)
    watchlist = set(watchlist_codes)
    outside_qualified = sum(base_code(item["code"]) not in watchlist for item in ranking)
    return {
        "qualified_codes": [item["code"] for item in ranking],
        "ranking": ranking,
        "top": [item["code"] for item in ranking[:TOP_N]],
        "anchor_position": anchor_item["position"] if anchor_item else None,
        "outside_watchlist_qualified_count": outside_qualified,
        "discovery_complete": outside_qualified > 0,
    }


def build_live_snapshot(
    anchor: str, data_date: str, references: list[dict[str, str]]
) -> dict[str, Any]:
    """Fetch one frozen research request; no filesystem, old DB, or hidden fallback."""
    anchor = normalize_code(anchor)
    codes = [base_code(item["code"]) for item in references]
    if base_code(anchor) not in codes:
        raise ScreenError("参照公司不在本次冻结范围内")
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        raise ScreenError("更新资料需要配置 TuShare Token")
    import tushare as ts  # type: ignore[import-untyped]

    client = ts.pro_api(token, timeout=30)
    stock_rows, valuation_rows, source_times = fetch_universe(client, token, data_date)
    selected, scope, valuations = load_peers(stock_rows, valuation_rows, anchor, codes)
    rows, _ = load_inputs(
        selected, valuations, {"watchlist_codes": codes}, data_date, client, token, source_times
    )
    captured_at = now_iso()
    scope.update(source_times, valuation_date=data_date, source="tracker")
    return {
        "schema_version": SCHEMA_VERSION,
        "rule": RULE,
        "source": "tracker",
        "limits": {"cap": CAP, "top_n": TOP_N, "max_report_age_days": MAX_REPORT_AGE_DAYS},
        "formula": "research_order=(roe_rank_desc+pb_rank_asc)/2; average ties",
        "screened_at": captured_at,
        "generated_at": captured_at,
        "data_date": data_date,
        "anchor": anchor,
        "watchlist_codes": codes,
        "scope": scope,
        "rows": rows,
        "results": rank_peers(rows, anchor, codes),
    }


def fmt_number(value: Any, digits: int = 2) -> str:
    number = finite_number(value)
    return "—" if number is None else f"{number:.{digits}f}"


def annual_entries(row: dict[str, Any]) -> list[dict[str, Any]]:
    value = row.get("annual_roes")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def indexed_rows(
    snapshot: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    raw_rows = snapshot.get("rows")
    if not isinstance(raw_rows, list):
        return {}, ["rows 不是列表"]
    rows: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for index, row in enumerate(raw_rows):
        if not isinstance(row, dict) or not isinstance(row.get("code"), str):
            warnings.append(f"rows[{index}] 缺少字符串 code")
            continue
        code = row["code"]
        if code in rows:
            warnings.append(f"rows 含重复代码 {code}")
            continue
        rows[code] = row
    return rows, warnings


def candidate_review_sections(snapshot: dict[str, Any]) -> list[str]:
    rows, _ = indexed_rows(snapshot)
    results = snapshot.get("results")
    results = results if isinstance(results, dict) else {}
    ranking = results.get("ranking")
    ranks = (
        {
            item["code"]: item
            for item in ranking
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        }
        if isinstance(ranking, list)
        else {}
    )
    anchor_code = snapshot.get("anchor")
    anchor = rows.get(anchor_code) if isinstance(anchor_code, str) else None
    anchor_rank = ranks.get(anchor_code) if isinstance(anchor_code, str) else None
    top = results.get("top")
    lines = ["", "## 候选审查", ""]
    for code in top if isinstance(top, list) else []:
        row = rows.get(code)
        rank = ranks.get(code)
        if row is None or rank is None:
            lines.append(f"- 快照中的候选 `{code}` 缺少对应明细或排名，无法生成审查说明。")
            continue
        lines.append(f"### {row.get('name') or '—'} `{code}`")
        lines.append("")
        lines.append(
            f"- 排序原因：三年 ROE 均值 {fmt_number(row.get('roe_mean'))}%（第 {fmt_number(rank.get('roe_rank'), 1)} 名），"
            f"PB {fmt_number(row.get('pb'))}（第 {fmt_number(rank.get('pb_rank'), 1)} 名），综合研究次序第 {rank.get('position')}。"
        )
        row_roe = finite_number(row.get("roe_mean"))
        row_pb = finite_number(row.get("pb"))
        anchor_roe = finite_number(anchor.get("roe_mean")) if anchor else None
        anchor_pb = finite_number(anchor.get("pb")) if anchor else None
        if (
            anchor is not None
            and anchor_rank is not None
            and row_roe is not None
            and row_pb is not None
            and anchor_roe is not None
            and anchor_pb is not None
        ):
            row_years = [str(item.get("period") or "")[:4] for item in annual_entries(row)]
            anchor_years = [str(item.get("period") or "")[:4] for item in annual_entries(anchor)]
            comparison = (
                f"相对参照公司，ROE 均值差 {row_roe - anchor_roe:+.2f} 个百分点，"
                f"PB 差 {row_pb - anchor_pb:+.2f} 倍（候选减参照）"
            )
            if row_years != anchor_years:
                comparison += f"；年报覆盖不同（候选 {row_years}，参照 {anchor_years}）"
            lines.append(f"- 与参照比较：{comparison}。")
        else:
            lines.append("- 与参照比较：参照或候选数据不完整，不计算差值。")

        annual = sorted(annual_entries(row), key=lambda item: str(item.get("period") or ""))
        values = [finite_number(item.get("roe_waa")) for item in annual]
        facts: list[str] = []
        if len(annual) != 3 or any(value is None for value in values):
            facts.append("逐年 ROE 数据不足，不能核验负值或最新年度下降")
        else:
            numeric = [float(value) for value in values if value is not None]
            if any(value < 0 for value in numeric):
                facts.append("三年中存在负 ROE")
            if numeric[-1] < numeric[-2]:
                facts.append(
                    f"最新年度 ROE 从 {fmt_number(numeric[-2])}% 降至 {fmt_number(numeric[-1])}%"
                )
        lines.append(
            "- 已知事实："
            + ("；".join(facts) if facts else "本次指定字段未触发负 ROE 或最新年度下降提示")
            + "。"
        )
        lines.append(
            "- 待核查：主营业务可比吗？高 ROE 是否依赖杠杆或一次性收益？低 PB 是否反映资产质量问题？风险警示、停牌与可交易性如何？"
        )
        lines.append("")
    return lines
