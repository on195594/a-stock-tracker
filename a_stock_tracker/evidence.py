"""Financial report selection shared by the independent market-research batch."""

from __future__ import annotations

from typing import Any

from a_stock_tracker.research import (
    ScreenError,
    finite_number,
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
