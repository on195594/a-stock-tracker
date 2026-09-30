"""Small, source-bound research summaries; no investment scores or price targets."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from a_stock_tracker.research import (
    ScreenError,
    annual_entries,
    finite_number,
    fmt_number,
    iso_date,
    normalize_flag,
)

REPORT_FIELDS = {
    "or_yoy": ("营业收入同比", "%"),
    "netprofit_yoy": ("归母净利润同比", "%"),
    "dt_netprofit_yoy": ("扣非归母净利润同比", "%"),
    "debt_to_assets": ("资产负债率", "%"),
    "ocfps": ("每股经营现金流", "元/股"),
}


def select_latest_report(
    records: list[dict[str, Any]], target: str, checked: str
) -> dict[str, Any]:
    """Select the latest disclosed period, including interim reports, without filling gaps."""
    candidates = []
    for raw in records:
        try:
            period, announced = iso_date(raw.get("end_date")), iso_date(raw.get("ann_date"))
        except ScreenError:
            return {"status": "conflict", "error": "报告日期缺失或无效"}
        if period > target or announced > checked[:10]:
            continue
        if announced < period or period[5:] not in {"03-31", "06-30", "09-30", "12-31"}:
            return {"status": "conflict", "error": "报告期或公告日期冲突"}
        candidates.append({**raw, "period": period, "ann_date": announced})
    if not candidates:
        return {"status": "missing", "error": "本次未取得已披露报告"}
    latest = max(r["period"] for r in candidates)
    versions = [r for r in candidates if r["period"] == latest]
    revisions = [r for r in versions if normalize_flag(r.get("update_flag")) == "1"]
    if len(revisions) == 1:
        chosen, basis = revisions[0], "unique_update_flag_1"
    elif len(versions) == 1 and normalize_flag(versions[0].get("update_flag")) == "0":
        chosen, basis = versions[0], "single_original_update_flag_0"
    else:
        return {"status": "conflict", "period": latest, "error": "最新报告版本冲突，未回退旧报告"}
    metrics = {field: finite_number(chosen.get(field)) for field in REPORT_FIELDS}
    missing = [field for field, value in metrics.items() if value is None]
    return {
        "status": "partial" if missing else "ok",
        "period": latest,
        "ann_date": chosen["ann_date"],
        "source": chosen.get("source"),
        "acquired_at": chosen.get("acquired_at"),
        "selection_basis": basis,
        "metrics": metrics,
        "missing": missing,
    }


def report_valid(report: Any) -> bool:
    """Validate complete and partial evidence before publishing or displaying it."""
    if not isinstance(report, dict):
        return False
    if not isinstance(report.get("error", ""), str):
        return False
    if report.get("status") in ("failed", "missing", "conflict"):
        return "metrics" not in report and (
            "period" not in report or isinstance(report["period"], str)
        )
    if report.get("status") not in ("ok", "partial"):
        return False
    try:
        period, announced = iso_date(report["period"]), iso_date(report["ann_date"])
        acquired = datetime.fromisoformat(report["acquired_at"])
    except (KeyError, ValueError, TypeError, ScreenError):
        return False
    metrics = report.get("metrics")
    return bool(
        report["period"] == period
        and report["ann_date"] == announced
        and period <= announced
        and period[5:] in {"03-31", "06-30", "09-30", "12-31"}
        and acquired.tzinfo is not None
        and announced <= acquired.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        and report.get("source") in ("tushare.fina_indicator", "fixture")
        and report.get("selection_basis")
        in ("single_original_update_flag_0", "unique_update_flag_1")
        and isinstance(metrics, dict)
        and set(metrics) == set(REPORT_FIELDS)
        and all(
            v is None or (type(v) in (int, float) and finite_number(v) is not None)
            for v in metrics.values()
        )
        and report.get("missing") == [key for key in REPORT_FIELDS if metrics[key] is None]
        and (report["status"] == "partial") == bool(report["missing"])
    )


def report_usable(report: Any) -> bool:
    return report_valid(report) and report["status"] == "ok"


def report_for_display(row: dict[str, Any]) -> dict[str, Any]:
    if "research_report" not in row:
        return {}
    report = row["research_report"]
    if report_valid(report):
        return report
    return {"status": "conflict", "error": "最新财报结构或来源日期异常，请重新核查"}


def report_facts(row: dict[str, Any]) -> dict[str, Any]:
    """Only decision-relevant fields: a later fetch timestamp is not a new financial fact."""
    report = row.get("research_report")
    if not isinstance(report, dict) or not report_usable(report):
        return {}
    return {
        key: report[key] for key in ("period", "ann_date", "source", "selection_basis", "metrics")
    }


def report_regressed(current: dict[str, Any], previous: dict[str, Any]) -> bool:
    current_report, previous_report = report_facts(current), report_facts(previous)
    return bool(
        current_report
        and previous_report
        and (current_report["period"], current_report["ann_date"])
        < (previous_report["period"], previous_report["ann_date"])
    )


def research_prompt(row: dict[str, Any]) -> dict[str, str]:
    """Explain a concrete next research action without inferring unobserved risks."""
    report = report_for_display(row)
    metrics = report.get("metrics") or {}
    if row.get("risk_status") == "known_warning" or row.get("list_status") in {"D", "P"}:
        return {
            "action": "先核查公司风险状态",
            "why": "本次基础信息存在名称风险警示或非正常上市状态。",
            "next": "打开公司公告，核对风险警示原因、审计意见和最新处置进展。",
        }
    if report.get("status") in {"failed", "missing", "partial", "conflict"}:
        return {
            "action": "先补齐最新财报证据",
            "why": report.get("error") or "本次最新报告有字段缺失，尚不足以复核盈利质量。",
            "next": "核对公告中的最新报告期及缺失字段；资料补齐前保留原判断。",
        }
    profit, cash = (
        finite_number(metrics.get("dt_netprofit_yoy")),
        finite_number(metrics.get("ocfps")),
    )
    if profit is not None and profit < 0:
        return {
            "action": "复核盈利下滑是否影响原判断",
            "why": f"本次报告扣非归母净利润同比 {fmt_number(profit)}%；尚未核实原因。",
            "next": "查管理层经营讨论和非经常性损益说明，核对需求、毛利与费用变化。",
        }
    if cash is not None and cash < 0:
        return {
            "action": "先核查经营现金流",
            "why": f"本次报告每股经营现金流 {fmt_number(cash)} 元/股；不能仅据此认定经营恶化。",
            "next": "查现金流量表及应收、存货附注，核对季节性和营运资金占用。",
        }
    annual = sorted(
        annual_entries(row),
        key=lambda r: str(r.get("period") or r.get("end_date") or r.get("year") or "").replace(
            "-", ""
        ),
    )
    values = [
        finite_number(r.get("roe_waa") if r.get("roe_waa") is not None else r.get("roe"))
        for r in annual
    ]
    if (
        len(values) == 3
        and values[-1] is not None
        and values[-2] is not None
        and values[-1] < values[-2]
    ):
        return {
            "action": "复核历史盈利能力下降",
            "why": f"最新年度 ROE 从 {fmt_number(values[-2])}% 降至 {fmt_number(values[-1])}%。",
            "next": "核对利润、净资产与一次性损益，判断原先的盈利稳定假设是否仍成立。",
        }
    return {
        "action": "先核查业务可比性与盈利质量",
        "why": f"接口报告期 {report['period']}，扣非归母净利润同比 {fmt_number(profit)}%；业务、资产质量和估值假设仍需核实。"
        if report_usable(report)
        else "PB 与历史 ROE 提供研究线索；业务、资产质量和估值假设仍需证据。",
        "next": "查年报主营业务、现金流量表及审计意见，记录一条支持理由和一条反对证据。",
    }
