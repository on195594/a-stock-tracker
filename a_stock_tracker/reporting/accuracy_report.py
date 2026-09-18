"""Manifest-driven QFQ factor research report.

This remains a research report, not an executable or cost-adjusted trading backtest.
"""

from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from a_stock_tracker import paths
from a_stock_tracker.reporting.evaluation import (
    EVALUATION_VERSION,
    ExperimentManifest,
    CalendarEvidence,
    EvaluationInputError,
    ManifestError,
    evaluate_manifest,
    load_experiment_manifest,
    load_calendar_evidence,
    pending_manifest_summary,
    strip_internal_evaluations,
)


def _fmt_percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}%"


def _fmt_number(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def _parse_as_of(value: date | datetime | str | None) -> date | None:
    try:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("naive datetime is not allowed")
            return value.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if isinstance(value, date):
            return value
        if not isinstance(value, str):
            raise ValueError("unsupported as-of type")
        if len(value) == 10:
            return date.fromisoformat(value)
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("naive datetime is not allowed")
        return parsed.astimezone(ZoneInfo("Asia/Shanghai")).date()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("evaluation_as_of must be an ISO date or datetime") from exc


def _current_shanghai_date() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _empty_summary(reason: str) -> dict[str, Any]:
    return {
        "experiment_id": None,
        "manifest_hash": None,
        "evaluation_version": None,
        "protocol_status": "MANIFEST_UNAVAILABLE",
        "scoring_hashes": [],
        "as_of": None,
        "evidence_status": "INSUFFICIENT_EVIDENCE",
        "verdict": None,
        "gaps": [reason],
        "price_diagnostics": [],
        "windows": {},
    }


def build_accuracy_summary(
    db: sqlite3.Connection,
    *,
    manifest: str | Path | Mapping[str, Any] | ExperimentManifest | None = None,
    evaluation_as_of: date | datetime | str | None = None,
    calendar: CalendarEvidence | Sequence[str | date] | None = None,
) -> dict[str, Any]:
    """Build the structured read-only summary.

    With no explicit manifest, load the tracked project configuration from the
    central paths owner. A pending manifest remains fail-closed.
    """
    if manifest is None:
        manifest = paths.experiment_manifest_path()
    try:
        loaded = load_experiment_manifest(manifest)
    except ManifestError as exc:
        return _empty_summary(str(exc))
    try:
        parsed_as_of = _parse_as_of(evaluation_as_of)
    except ValueError as exc:
        return {
            "experiment_id": loaded.experiment_id,
            "manifest_hash": loaded.manifest_hash,
            "evaluation_version": None,
            "protocol_status": "MANIFEST_INVALID",
            "scoring_hashes": list(loaded.scoring_hashes),
            "as_of": None,
            "evidence_status": "INSUFFICIENT_EVIDENCE",
            "verdict": None,
            "gaps": [f"evaluation_as_of_invalid:{exc}"],
            "price_diagnostics": [],
            "windows": {},
        }
    if loaded.registration_status != "verified":
        return pending_manifest_summary(loaded, parsed_as_of)
    as_of = parsed_as_of or _current_shanghai_date()
    try:
        if calendar is None:
            try:
                calendar = load_calendar_evidence(paths.trading_calendar_path())
            except EvaluationInputError as exc:
                if not str(exc).startswith("calendar_evidence_missing:"):
                    raise
        return strip_internal_evaluations(evaluate_manifest(db, loaded, evaluation_as_of=as_of, calendar=calendar))
    except (EvaluationInputError, sqlite3.Error) as exc:
        return {
            "experiment_id": loaded.experiment_id,
            "manifest_hash": loaded.manifest_hash,
            "evaluation_version": EVALUATION_VERSION,
            "protocol_status": "EVALUATION_INPUT_INVALID",
            "scoring_hashes": list(loaded.scoring_hashes),
            "as_of": as_of.isoformat(),
            "evidence_status": "INSUFFICIENT_EVIDENCE",
            "verdict": None,
            "gaps": [str(exc)],
            "price_diagnostics": [],
            "windows": {},
        }


def _render_window(window_days: int, window: Mapping[str, Any]) -> list[str]:
    metrics = window["metrics"]
    fmt_count = lambda value: "N/A" if value is None else str(value)
    lines = [
        "",
        f"── {window_days}d ──",
        (
            f"评分时资格：expected={fmt_count(window['expected'])}；实际唯一预测={fmt_count(window['actual_unique_predictions'])}；"
            f"有效快照={fmt_count(window['qualified_snapshots'])}；覆盖门槛={fmt_count(window['required_coverage'])}；"
            f"选中快照={fmt_count(window['selected_snapshot_count'])}；收益可计算={fmt_count(window['outcome_computable'])}"
        ),
        f"固定非重叠批次：{fmt_count(window['selected_sections'])}；成熟批次：{fmt_count(window['mature_sections'])}；未成熟：{fmt_count(window['immature_sections'])}",
        "指标范围：只用已到观察期限的固定批次；未成熟批次仅诊断，到期但缺价的批次不得跳过。",
        "已到期截面：" + (", ".join(window["due_dates"]) if window["due_dates"] else "无"),
        "采用截面：" + (", ".join(window["selected_dates"]) if window["selected_dates"] else "无"),
        f"窗口证据状态：{window['readiness_status']}",
        f"截面 Spearman IC 均值：{_fmt_number(metrics.get('ic'))}",
        f"Q5−Q1 平均 alpha spread：{_fmt_percent(metrics.get('spread'))}",
        f"Q5 研究累计收益：{_fmt_percent(metrics.get('q5_return'))}",
        f"观察池等权累计总收益：{_fmt_percent(metrics.get('equal_return'))}",
        f"Q5−观察池等权累计收益差：{_fmt_percent(metrics.get('q5_minus_equal'))}",
        f"沪深300全收益：{_fmt_percent(metrics.get('benchmark_return'))}",
        f"Q5 日收盘最大回撤：{_fmt_percent(metrics.get('q5_mdd'))}",
        (
            "L3 v2 极端风险提示（仅记录信号）："
            f"正常 n={window['l3_recorded']['normal']}；触发 n={window['l3_recorded']['triggered']}；"
            f"未知 n={window['l3_recorded']['unknown']}"
        ),
    ]
    if window["all_scores_tied"]:
        lines.append("信号状态：ALL_SCORES_TIED；IC、spread、Q5排序优势=N/A；人工方向复核")
    disclosures = window.get("tie_disclosure", [])
    if disclosures:
        lines.append(
            "并列边界披露：" + json.dumps(disclosures, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    if window.get("endpoint_diagnostics"):
        lines.append(
            "端点批次诊断（不作为全链指标）："
            + json.dumps(window["endpoint_diagnostics"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    if window.get("price_diagnostics"):
        lines.append("行情行诊断（不单独阻塞窗口）：" + ", ".join(window["price_diagnostics"]))
    if window.get("readiness_gaps"):
        lines.append("指标诊断：" + ", ".join(window["readiness_gaps"]))
    if window["gaps"]:
        lines.append("缺口：" + ", ".join(window["gaps"]))
    lines.append("回撤口径：持有期间逐日盯市；必要日线缺失时返回N/A，不以端点回撤代替。")
    return lines


def build_accuracy_report(
    db: sqlite3.Connection,
    strong_threshold: float = 44.0,
    *,
    manifest: str | Path | Mapping[str, Any] | ExperimentManifest | None = None,
    evaluation_as_of: date | datetime | str | None = None,
    calendar: CalendarEvidence | Sequence[str | date] | None = None,
) -> str:
    """Render the existing text report from one structured, read-only summary."""
    summary = build_accuracy_summary(
        db,
        manifest=manifest,
        evaluation_as_of=evaluation_as_of,
        calendar=calendar,
    )
    public_summary = strip_internal_evaluations(summary)
    lines = [
        "=" * 60,
        "a-stock-tracker QFQ 策略评估报告",
        f"生成时间：{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')}",
        "=" * 60,
        "口径：个股前复权收益 vs 沪深300全收益指数；仅统计评分时合格、日历可证明的样本。",
        "仅为选股研究：30/60/90为自然日；收益从评分日收盘起算，盘后信号无法按该价格成交。",
        "不含费用、滑点、涨跌停/停牌成交约束；仅在批次逐日路径连续可证明时链式计算研究NAV/MDD，不是可执行策略或账户净值。",
        f"验证对象为Q5（评分最高约20%等权），不等于Telegram总分>={strong_threshold:.0f}的观察名单；阈值不是买入线。",
        "投资边界：固定股票池、跨行业分数不可直接解释为质量优劣；无已验证买点、仓位或退出规则。",
        "定性分为冻结研究输入，不代表当日重新核实的护城河、行业地位或市场情绪。",
        "不同期限复用同一批股票，不能视为独立重复验证；方向为正也不代表统计显著或已证明有效。",
        f"experiment_id：{public_summary['experiment_id'] or 'N/A'}",
        f"manifest_hash：{public_summary['manifest_hash'] or 'N/A'}",
        f"evaluation_version：{public_summary['evaluation_version'] or 'N/A'}",
        f"scoring_hashes：{','.join(public_summary['scoring_hashes']) or 'N/A'}",
        f"evaluation_as_of：{public_summary['as_of'] or 'N/A'}",
        f"总体证据状态：{public_summary['evidence_status']}",
        f"协议状态：{public_summary.get('protocol_status', 'N/A')}",
        f"verdict：{public_summary['verdict'] or 'N/A'}",
        "结构化摘要：" + json.dumps(public_summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    ]
    if public_summary["gaps"]:
        lines.append("总体缺口：" + ", ".join(public_summary["gaps"]))
    for raw_window, window in public_summary["windows"].items():
        lines.extend(_render_window(int(raw_window), window))
    lines.extend(
        [
            "",
            "限制：watchlist 为人工维护标的，结果不代表样本外泛化能力。",
            "报告生产启用仍需单独授权；本实现不修改评分、预测、股票池、schema、cron或通知。",
        ]
    )
    return "\n".join(lines)


__all__ = ["build_accuracy_report", "build_accuracy_summary"]
