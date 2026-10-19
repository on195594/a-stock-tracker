"""Plain-data presentation rules shared by the server-rendered pages.

Every string here is rendered through Jinja2 autoescaping; nothing is marked safe.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from a_stock_tracker.disclosures import UNFORMED, quality_text, report_summary_status, scope_text
from a_stock_tracker.opportunities import LIMITS, RULE, SECTIONS
from a_stock_tracker.research import SHANGHAI, finite_number

STATUS_LABELS = {
    "running": "执行中",
    "completed": "已结束",
    "partial": "已结束（部分资料缺失）",
    "failed": "失败",
    "interrupted": "中断",
}
MODE_TITLES = {"financial": "研究一家公司", "screen": "从初筛找公司", "swing": "核对价格参考"}
STAGES = {
    "evidence_load": "读取原文证据",
    "announcements": "获取公告",
    "document_selection": "选择定期报告",
    "document_extraction": "提取报告原文",
    "evidence_selection": "选择原文证据",
    "report_generation": "生成研究报告",
    "citation_validation": "校验原文引用",
    "independent_review": "独立复核",
    "review_validation": "校验复核结果",
    "cached_review": "读取既有复核",
}
SECTION_TITLES = (
    ("business", "业务"),
    ("financial", "财务"),
    ("valuation", "估值"),
    ("change_conditions", "什么会改变结论"),
)
UNFORMED_TEXT = "资料不足，本次没有形成判断。"
LEGACY_NOISE = re.compile(r"(?:推论)?[（(]F[0-9]+(?:[、,，]\s*F[0-9]+)*[）)][：:]?")
CODE = re.compile(r"(?:60\d{4}\.SH|00\d{4}\.SZ)")
RUN_ID = re.compile(r"[a-f0-9]{32}")
CURSOR_RE = re.compile(r"^(?:[a-f0-9]{32}|[0-9]+(?:\.[0-9]+)?_[a-f0-9]{32})$")
PRICE_LIMITS = LIMITS
PRICE_SECTIONS = SECTIONS
LEGACY_PRICE_RULE = RULE


def local_time(value: Any) -> str:
    if not value:
        return "未记录"
    try:
        stamp = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return str(value)
    if stamp.tzinfo is None:
        return str(value)
    return stamp.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")


def budget_duration(seconds: int) -> str:
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes} 分钟" + (f" {remainder} 秒" if remainder else "")


def roe_text(value: Any) -> str:
    number = finite_number(value)
    return f"{number:.2f}%" if number is not None else "未记录"


CODE_SPLIT = re.compile(r"[,，\s]+")
SIX_DIGITS = re.compile(r"\d{6}")


def normalize_codes(raw: str) -> list[str]:
    """Accept 600519 / 600519.sh / comma or whitespace lists."""
    codes = [c.strip().upper() for c in CODE_SPLIT.split(raw or "") if c.strip()]
    return [
        c + (".SH" if c.startswith("60") else ".SZ") if SIX_DIGITS.fullmatch(c) else c
        for c in codes
    ]


def source_times_text(value: Any) -> str:
    if not isinstance(value, dict):
        return local_time(value)
    labels = {
        "stock_basic_acquired_at": "证券基本资料",
        "daily_basic_acquired_at": "每日估值资料",
    }
    entries = [f"{label}：{local_time(value.get(key))}" for key, label in labels.items()]
    entries.extend(
        f"{key}：{local_time(stamp)}" for key, stamp in value.items() if key not in labels
    )
    return "\n".join(entries)


def report_data_date(report: dict[str, Any]) -> str:
    docs = report.get("documents", [])
    if not docs:
        return "资料日期：未记录"
    return f"资料截至 {max(d['announced'] for d in docs)}（公告日）"


def report_dates(report: dict[str, Any]) -> str:
    docs = report.get("documents", [])
    if not docs:
        return report_data_date(report)
    periods = "；".join(f"报告期 {d['period']} / 公告 {d['announced']}" for d in docs)
    return f"{report_data_date(report)}\n{periods}"


def readable_report(report: dict[str, Any]) -> bool:
    return report.get("evidence_review") == "passed" or report.get("status") == "synthetic"


def omitted_pages_text(value: Any) -> str:
    return f"{value} 页" if type(value) is int and value >= 0 else "未记录（无法确认页数）"


def usage_text(usage: Any, mode: str | None = None) -> str:
    if not usage:
        return "本轮用量：未记录"

    def recorded(key: str) -> Any:
        return usage.get(key) if usage.get(key) is not None else "未记录"

    reason = {
        "completed": "完成本轮",
        "reused": "复用已有结果，未执行采集",
        "request_budget": "请求预算用尽",
        "time_budget": "时间预算用尽",
        "error": "执行异常",
    }.get(usage.get("stop_reason"), "未记录")
    coverage = (
        "冻结证券范围背景统计（非本次单家公司研究进度）：\n"
        f"本轮新增财报扫描：{recorded('newly_covered')}；已归类或扫描：{recorded('covered')}；尚未扫描：{recorded('remaining')}\n"
        "已归类或扫描包含预先排除和资料缺口；尚未扫描包含本次指定范围以外的证券。"
        if mode == "financial"
        else f"本轮新增覆盖：{recorded('newly_covered')}；累计覆盖：{recorded('covered')}；剩余未覆盖：{recorded('remaining')}"
    )
    return (
        f"本轮请求：{recorded('requests')} / {recorded('request_limit')} 次；耗时：{recorded('seconds_used')} 秒\n"
        f"停止原因：{reason}\n{coverage}"
    )


def price_dates(row: dict[str, Any]) -> str:
    return f"依据截至 {row['as_of']}；有效至 {row['expires']}（后续5个交易日）"


def price_levels_text(row: dict[str, Any]) -> str:
    """Plain reading of the program's own levels; compares them, never derives a new price."""
    levels = row["levels"]
    close, ma20, ma60 = (finite_number(levels.get(k)) for k in ("close", "ma20", "ma60"))
    trend = ""
    if close is not None and ma20 is not None and ma60 is not None:
        if close > ma20 and close > ma60:
            trend = "，在 20 日和 60 日均线上方，走势偏强"
        elif close < ma20 and close < ma60:
            trend = "，在 20 日和 60 日均线下方，走势偏弱"
        else:
            trend = "，夹在 20 日和 60 日均线之间，方向不明"
    trigger = finite_number(levels.get("trigger"))
    watch = f"站上 {trigger:.2f} 元（近 20 日最高）才算转强；" if trigger is not None else ""
    return (
        f"收盘 {levels['close']:.2f} 元{trend}。{watch}"
        f"跌破 {levels['stop']:.2f} 元（近 10 日最低）说明走势转坏，"
        f"离收盘还有 {levels['distance']:.2f}%。"
    )


def recent_result_label(run: dict[str, Any]) -> str:
    """What the user got, not the batch's coverage flag (a single-company run is always partial)."""
    status = run["status"]
    if status in {"running", "failed", "interrupted"}:
        return STATUS_LABELS[status]
    if status not in {"completed", "partial"}:
        return "状态待核查"
    result = run.get("summary") or {}
    rows = result.get("board", {}).get("rows", [])
    reports = result.get("reports", [])
    if rows:
        available = [r for r in rows if r["status"] in {"reference", "observation"}]
        return (
            "价格参考已过期"
            if available and all(r.get("expired") for r in available)
            else "价格参考"
            if available
            else "价格资料不足"
        )
    if reports:
        return "研究完成" if any(readable_report(r) for r in reports) else "未能生成结论"
    if run.get("request", {}).get("mode") == "screen":
        return "初筛完成" if status == "completed" else "初筛未全部完成"
    return "结果待核查"


def conclusion_text(report: dict[str, Any]) -> str:
    """One readable line: the v3 summary, else the first sentence of the main risk."""
    if not readable_report(report):
        return ""
    analysis = report.get("analysis") or {}
    summary = (analysis.get("summary") or {}).get("text", "")
    if summary:
        return summary
    counter = (analysis.get("counterevidence") or {}).get("text", "")
    # Older reports carry inline fact ids and engine prefixes; the card shows plain words.
    first = LEGACY_NOISE.sub("", counter.split("。", 1)[0]).removeprefix("最强反证是")
    return "主要风险：" + first + "。" if first else ""


def disclosure_text(report: dict[str, Any]) -> str:
    docs = report.get("documents") or []
    if not docs:
        return "披露期：未记录"
    return "披露期：" + "；".join(
        f"{d.get('period') or '未记录'}（公告 {d.get('announced') or '未记录'}）" for d in docs
    )


def price_badge(row: dict[str, Any]) -> dict[str, str]:
    """Validity tag only; tones are neutral or caution, never a success colour."""
    if row["status"] == "gap":
        return {"text": "资料缺口", "tone": "caution"}
    if row["status"] == "excluded":
        return {"text": "旧规则未入选", "tone": "plain"}
    if row.get("expired"):
        return {"text": "已过期", "tone": "caution"}
    return {"text": "有效期内" if row["status"] == "reference" else "旧规则 · 有效期内", "tone": ""}


def validity_text(row: dict[str, Any]) -> str:
    if not row.get("expires"):
        return "有效期：未记录"
    if row.get("expired"):
        return f"依据截至 {row.get('as_of') or '未记录'}；原有效至 {row['expires']}，已过期"
    return price_dates(row)


def report_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": report["code"],
        "badge": None,
        "text": conclusion_text(report) or report_summary_status(report),
        "helper": disclosure_text(report),
        "levels": None,
        "expires": None,
    }


def board_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Recent/dossier price line; only a live reference carries levels."""
    live = row["status"] == "reference" and not row.get("expired")
    if row["status"] in {"gap", "excluded"}:
        text = "；".join(row.get("reasons") or [])
    elif row.get("expired"):
        text = "已过期，价位已隐藏；需要时重新核对。"
    elif row["status"] == "observation":
        text = "旧规则批次；请进入详情核查历史条件。"
    else:
        text = ""
    return {
        "code": row["code"],
        "badge": price_badge(row),
        "text": text,
        "helper": validity_text(row) if row["status"] not in {"gap", "excluded"} else "",
        # Live levels are a separate element so the page script can hide them at expiry.
        "levels": price_levels_text(row) if live else None,
        "expires": row.get("expires") if live else None,
    }


def run_mode(run: dict[str, Any]) -> str | None:
    return (run.get("request") or {}).get("mode")


def run_codes(run: dict[str, Any]) -> list[str]:
    return (run.get("request") or {}).get("codes") or []


def recent_item(run: dict[str, Any]) -> dict[str, Any]:
    chain = f" · 续跑 {run['continuations']} 次" if run.get("continuations") else ""
    if run.get("reused") or run.get("reused_from"):
        chain += " · 复用"
    result = run.get("summary") or {}
    codes = run_codes(run)
    scope = ",".join(codes) or "全市场初筛"
    # A single-company run is named after the company; its summaries then omit the code.
    entries = result.get("reports", []) + result.get("board", {}).get("rows", [])
    name = next((e["name"] for e in entries if e.get("name")), "") if len(codes) == 1 else ""
    return {
        "id": run["id"],
        "active": run["status"] == "running",
        "title": (run.get("limits") or {}).get("title", "研究") + " · " + scope,
        "scope": f"{name} · {scope}" if name else scope,
        "single": len(codes) == 1,
        "meta": f"{recent_result_label(run)}{chain} · {local_time(run.get('created_at'))}",
        "summaries": [report_summary(r) for r in result.get("reports", [])]
        + [board_summary(row) for row in result.get("board", {}).get("rows", [])],
    }


VIEWS = {"overview": "公司", "financial": "财报记录", "screen": "初筛记录", "swing": "价格记录"}
MODE_ORDER = ("financial", "screen", "swing")
EMPTY = {
    "financial": "暂无财报研究记录。",
    "screen": "暂无初筛记录。",
    "swing": "暂无价格参考记录。",
}


def tab_groups(runs: dict[str, list[dict[str, Any]]], view: str, limit: int) -> dict[str, Any]:
    """Home tabs: running work pinned on top, then one group per mode in the view.

    `runs` holds the global recent list under "all" and per-mode lists under each mode.
    Lists are re-filtered by mode, so a receiver that ignores the filter cannot mix tabs.
    """
    active, pinned = [], set()
    for run in (r for key in ("all", *MODE_ORDER) for r in runs.get(key, [])):
        if run["status"] == "running" and run["id"] not in pinned:
            pinned.add(run["id"])
            active.append(recent_item(run))
    groups = []
    for mode in MODE_ORDER if view == "overview" else (view,):
        listed = runs.get(mode, [])
        items = [recent_item(r) for r in listed if run_mode(r) == mode and r["id"] not in pinned]
        groups.append(
            {
                "mode": mode,
                "title": VIEWS[mode],
                "items": items,
                "empty": EMPTY[mode],
                "more": f"/?view={mode}" if view == "overview" and items else None,
                "next": f"/?view={mode}&cursor={listed[-1].get('cursor') or listed[-1]['id']}"
                if view != "overview" and len(listed) == limit
                else None,
            }
        )
    return {"active": active, "groups": groups}


def company_cards(runs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """One card per company from the newest runs; superseded, failed and expired results
    fold away automatically and stay reachable in the per-type record tabs."""
    cards: dict[str, dict[str, Any]] = {}

    def card(code: str, name: str | None, when: str | None) -> dict[str, Any]:
        entry = cards.setdefault(
            code,
            {"code": code, "name": "", "when": "", "report": None, "failed": None, "price": None},
        )
        entry["name"] = entry["name"] or name or ""
        entry["when"] = max(entry["when"], when or "")
        return entry

    for run in runs.get("financial", []):
        if run_mode(run) != "financial" or run["status"] not in {"completed", "partial"}:
            continue
        for report in (run.get("summary") or {}).get("reports", []):
            entry = card(report["code"], report.get("name"), run.get("created_at"))
            if entry["report"]:
                continue  # newest readable report already shown; older ones are history
            if readable_report(report):
                entry["report"] = {
                    "run_id": run["id"],
                    "text": conclusion_text(report),
                    "time": local_time(run.get("created_at")),
                    "disclosure": disclosure_text(report),
                }
            elif entry["failed"] is None:
                entry["failed"] = run["id"]
    for run in runs.get("swing", []):
        if run_mode(run) != "swing" or run["status"] not in {"completed", "partial"}:
            continue
        for row in (run.get("summary") or {}).get("board", {}).get("rows", []):
            if row["status"] != "reference":
                continue  # gaps and legacy rows stay in the price records
            entry = card(row["code"], row.get("name"), run.get("created_at"))
            if entry["price"] is None:
                entry["price"] = {"run_id": run["id"], **board_summary(row)}
    for entry in cards.values():
        if entry["report"]:
            entry["failed"] = None  # a later failed retry does not hide a readable report
    return sorted(cards.values(), key=lambda c: c["when"], reverse=True)


def latest_screen(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    finished = [r for r in runs if run_mode(r) == "screen" and r["status"] != "running"]
    return recent_item(finished[0]) if finished else None


def archived_report(report: dict[str, Any]) -> dict[str, Any]:
    # The receiver lists only reviewed reports; synthetic ones say so themselves.
    report = {"evidence_review": "passed", **report}
    return {
        "run_id": report["run_id"],
        "time": local_time(report.get("finished_at")),
        "status": report_summary_status(report),
        "conclusion": report.get("conclusion") or "",
        "disclosure": disclosure_text(report),
        "quality": quality_text(report.get("quality")),
        "gaps": f"待补资料 {report.get('gap_count', 0)} 条",
    }


def company_view(
    code: str,
    reports: dict[str, Any],
    swing: list[dict[str, Any]],
    financial: list[dict[str, Any]],
) -> dict[str, Any]:
    """Company dossier: latest reviewed report, latest price reference, report archive."""
    listed = [r for r in reports.get("reports", []) if r.get("code") == code]
    archive = [archived_report(r) for r in listed]
    attempt_run = next(
        (r for r in financial if run_mode(r) == "financial" and code in run_codes(r)), None
    )
    attempt = None
    # Surface the newest financial run only when it is running or left nothing readable.
    if attempt_run and not (
        attempt_run["status"] in {"completed", "partial"}
        and any(readable_report(r) for r in (attempt_run.get("summary") or {}).get("reports", []))
    ):
        attempt = recent_item(attempt_run)
    price_run = next((r for r in swing if run_mode(r) == "swing" and code in run_codes(r)), None)
    price = None
    if price_run:
        item = recent_item(price_run)
        price = {
            "id": item["id"],
            "active": item["active"],
            "meta": item["meta"],
            "summaries": [s for s in item["summaries"] if s["code"] == code],
        }
    return {
        "code": code,
        "name": next((r["name"] for r in listed if r.get("name")), ""),
        "latest": archive[0] if archive else None,
        "archive": archive[1:],
        "attempt": attempt,
        "price": price,
    }


def quote_lines(info: dict[str, Any], mode: str, offer: dict[str, Any]) -> list[str]:
    """Cost boundary for the selected mode shown before confirmation."""
    lines = [
        "合成演示：不调用真实接口，不产生费用。"
        if info.get("demo")
        else "确认后后台请求行情" + ("并调用模型。" if mode == "financial" else "，不调用模型。"),
        f"上限：{offer['requests']} 次数据请求 · {budget_duration(offer['seconds'])}"
        + (f" · {offer['reports']} 份财报报告" if offer.get("reports") else ""),
    ]
    if mode == "screen":
        screening = offer.get("prefilter")
        if screening:
            lines.append(
                f"初筛门槛：0＜PB≤{screening['pb_max']}；"
                f"总市值≥{screening['total_mv_min_yi']}亿元，不设上限。"
            )
        lines.append(
            "结果是筛后同业排名（不是完整行业前三），只代表财报资格、不代表投资价值；"
            "门槛未经收益验证，可能漏选。"
        )
    elif mode == "financial":
        model, budget = offer.get("model") or {}, offer.get("model_budget") or {}
        lines.append(
            f"模型：{model.get('provider')} / {model.get('model')}，"
            f"最多 {budget.get('calls')} 次调用，每次本地等待 ≤"
            f"{budget.get('local_timeout_seconds')} 秒。"
        )
        lines.append(
            ("真实运行时：" if info.get("demo") else "")
            + "输出 token 与费用无硬上限；本地超时不保证提供方停止计费，失败调用也可能计费。"
        )
    else:
        lines.append("最多五家，仅计算价格纪律参考，不调用模型；不因趋势或距离排除资料完整的公司。")
    return lines


def confirmation_label(info: dict[str, Any], mode: str) -> str:
    if mode != "financial":
        return "已核对范围与预算，确认启动（不调用模型）"
    if info.get("demo"):
        return "已核对范围、预算与模型，确认启动"
    return "已核对范围、预算与模型，接受模型费用无硬上限"


def frozen_cost_lines(payload: dict[str, Any]) -> list[str]:
    """The already-confirmed offer shown before a same-id reconciliation."""
    offer = payload["quote"]
    lines = [
        f"范围：{','.join(payload['codes']) or '全市场初筛'}",
        f"已确认预算：最多 {offer['requests']} 次数据请求 / {budget_duration(offer['seconds'])}",
    ]
    model = offer.get("model")
    if model:
        budget = offer.get("model_budget") or {}
        lines.append(f"已确认模型：{model['provider']} / {model['model']}")
        lines.append(
            f"最多 {budget.get('calls')} 次调用，每次本地等待 ≤{budget.get('local_timeout_seconds')} 秒。"
            "输出 token 与费用无硬上限；本地超时不保证提供方停止计费，失败调用也可能计费。"
        )
    else:
        lines.append("不调用模型。")
    return lines


def report_view(report: dict[str, Any]) -> dict[str, Any]:
    """Structured report projection for the page; a failed review exposes no draft."""
    readable = readable_report(report)
    analysis = report.get("analysis", {}) if readable else {}
    diagnostic = report.get("diagnostic")
    stage = message = None
    if isinstance(diagnostic, dict):
        key = diagnostic.get("stage")
        stage = STAGES.get(key if isinstance(key, str) else "", "未记录")
        message = diagnostic.get("message")
    error = report.get("error") if report.get("error") != message else None
    clues = report.get("clues") or {}
    return {
        "code": report["code"],
        "name": report.get("name") or "",
        "readable": readable,
        "status": report_summary_status(report),
        "quality": quality_text(report.get("quality")),
        "dates": report_dates(report),
        "data_date": report_data_date(report),
        "summary": analysis.get("summary"),
        "snapshot": snapshot_text(report.get("snapshot")) if readable else None,
        "counter": analysis.get("counterevidence"),
        "gaps": report.get("gaps", []),
        "scope": report.get("scope", []),
        "scope_text": scope_text(report),
        "stage": stage,
        "stage_message": message,
        "error": error,
        "clues": clues if any(v is not None for v in clues.values()) else {},
        "roe": roe_text(clues.get("roe_mean")),
        "sections": [
            {"title": title, **analysis[key]}
            | (
                {"text": UNFORMED_TEXT}
                if analysis[key]["text"].strip().rstrip("。") == UNFORMED
                else {}
            )
            for key, title in SECTION_TITLES
            if key in analysis
        ],
        "facts": report.get("facts", []) if readable else [],
        "omitted": omitted_pages_text(report.get("omitted_pages")),
    }


def snapshot_text(value: Any) -> str | None:
    """Valuation snapshot line; values are shown as the receiver gave them, never recomputed."""
    if not isinstance(value, dict):
        return None
    parts = [
        f"{label} {round(value[key], 2):g}{unit}"
        for key, label, unit in (
            ("close", "收盘", " 元"),
            ("pe_ttm", "市盈率(TTM)", " 倍"),
            ("pb", "市净率", " 倍"),
            ("total_mv_yi", "总市值", " 亿元"),
            ("dv_ttm", "股息率", "%"),
        )
        if finite_number(value.get(key)) is not None
    ]
    if finite_number(value.get("pe_ttm")) is None:
        parts.insert(1, "市盈率(TTM) 无（亏损或无法计算）")
    return f"{' · '.join(parts)}（{value.get('date') or '日期未记录'}）" if parts else None


def screen_view(run: dict[str, Any]) -> dict[str, Any]:
    result = run["result"]
    summary = result["coverage"]
    counts = summary["counts"]
    pending = counts.get("pending", 0)
    groups = result.get("groups", [])
    candidate_count = sum(len(g["ranking"]) for g in groups)
    covered = (
        ("未完成可继续" if run.get("can_continue") else "未完成不可继续")
        if pending
        else "已尽但有资料缺口"
        if counts.get("gap")
        else "完整"
    )
    screening = summary.get("prefilter")
    initial = summary.get("prefilter_counts") or {}
    return {
        "candidate_count": candidate_count,
        "data_date": summary["data_date"],
        "pending": pending,
        "gap": counts.get("gap", 0),
        "caution": bool(pending or counts.get("gap")),
        "truncated": result.get("truncation_note") if result.get("truncated") else None,
        "groups": groups,
        "covered": f"覆盖：{covered}\n财报资格通过 {counts.get('qualified', 0)}；排除 {counts.get('excluded', 0)}；资料缺口 {counts.get('gap', 0)}；尚未覆盖 {pending}",
        "outcome": f"结果：{candidate_count} 家同业排名候选，{len(result.get('reports', []))} 份报告。",
        "usage": usage_text(result.get("usage"), "screen"),
        "basis": f"依据日期：{summary['data_date']}\n来源核查时间（上海）：\n{source_times_text(summary.get('source_times'))}\n证据截止：{summary.get('cutoff') or '未记录'}",
        "prefilter": (
            f"冻结门槛：0＜PB≤{screening['pb_max']}；总市值≥{screening['total_mv_min_yi']}亿元，不设上限。\n"
            f"财报采集前：排除 {initial.get('excluded', 0)} 家；资料缺口 {initial.get('gap', 0)} 家；进入财报采集池 {initial.get('pending', 0)} 家。\n"
            "门槛用于控制采集量，未经收益验证，可能漏选。"
        )
        if screening
        else None,
    }


def ranking_row(row: dict[str, Any]) -> dict[str, Any]:
    total_mv = finite_number(row.get("total_mv"))
    order = finite_number(row.get("order"))
    return {
        **row,
        "facts": f"市净率 {row.get('pb', '未记录')} 倍 · 近年平均 ROE {roe_text(row.get('roe_mean'))}"
        + (f" · 总市值 {total_mv / 10000:.1f} 亿元" if total_mv is not None else "")
        + (f"\n本组按市净率与 ROE 综合排第 {order:g}" if order is not None else ""),
    }


def board_rows(board: dict[str, Any], code: str | None = None) -> list[dict[str, Any]]:
    rows = board["rows"]
    if code:
        rows = [row for row in rows if row["code"] == code]
    return rows


def available_count(rows: list[dict[str, Any]]) -> int:
    return sum(
        1 for r in rows if r["status"] in {"observation", "reference"} and not r.get("expired")
    )
