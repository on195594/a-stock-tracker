"""Frozen Framework B cohort storage and read-only report helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import sqlite3
from typing import Any

from a_stock_tracker.data.cache import ensure_framework_b_cohort_schema, get_fundamentals


@dataclass(frozen=True)
class FreezePayload:
    code: str
    name: str
    industry: str
    source_a_prediction_id: int
    source_a_score_date: str
    source_a_total_score: float | None
    score_a: float
    score_b: float
    delta: float
    label: str
    rule_name: str
    rule_snapshot: dict[str, float]
    threshold_snapshot: dict[str, float]
    candidate_input_snapshot: dict[str, Any]
    score_snapshot: dict[str, Any]


@dataclass(frozen=True)
class FreezeResult:
    cohort_week: str
    label_date: str
    candidates: int
    inserted: int
    skipped_existing: int
    dry_run: bool


@dataclass(frozen=True)
class CohortOutcomeSummary:
    sample_count: int
    closed_count: int
    closed_weeks: int
    future_count: int
    overdue_count: int
    missing_source_count: int
    earliest_due: str | None
    ready_for_manual_review: bool


def cohort_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='framework_b_label_cohorts'").fetchone()
    return row is not None


def _weights_hash(weights: dict[str, Any]) -> str:
    payload = json.dumps(weights["frameworks"], sort_keys=True).encode()
    return hashlib.md5(payload).hexdigest()[:8]


def _cohort_week(label_date: str) -> str:
    iso = date.fromisoformat(label_date).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _candidate_input_snapshot(code: str) -> dict[str, Any]:
    fundamentals = get_fundamentals(code)
    data = dict(fundamentals.get("data", fundamentals)) if fundamentals else {}
    fields = (
        "roe_3y_avg",
        "roe_latest",
        "gross_margin",
        "debt_ratio",
        "pb_percentile_10y",
        "net_profit_growth",
        "report_period",
    )
    return {field: data.get(field) for field in fields}


def _latest_a_source(conn: sqlite3.Connection, code: str, label_date: str) -> tuple[Any, ...] | None:
    return conn.execute(
        """SELECT id, score_date, total_score
           FROM predictions
           WHERE code=? AND framework='A' AND score_date<=?
           ORDER BY score_date DESC, id DESC
           LIMIT 1""",
        (code, label_date),
    ).fetchone()


def _build_freeze_payloads(
    conn: sqlite3.Connection,
    weights: dict[str, Any],
    label_date: str,
) -> list[FreezePayload]:
    from a_stock_tracker.reporting.framework_b_report import (
        FRAMEWORK_B_QUALITY_RULES,
        _collect_framework_b_quality_candidates,
        _framework_b_provisional_label,
        _framework_b_provisional_threshold_data,
    )

    summaries = {
        name: _collect_framework_b_quality_candidates(conn, weights, rule)
        for name, rule in FRAMEWORK_B_QUALITY_RULES.items()
    }
    threshold_data = _framework_b_provisional_threshold_data(summaries)
    if threshold_data is None:
        return []

    rule_name = str(threshold_data["rule_name"])
    rule_snapshot = dict(FRAMEWORK_B_QUALITY_RULES[rule_name])
    threshold_snapshot = {key: float(threshold_data[key]) for key in ("strong", "moderate", "light")}
    payloads: list[FreezePayload] = []
    for row in threshold_data["summary"]["scored"]:
        source = _latest_a_source(conn, row["code"], label_date)
        if source is None:
            continue
        score_a = float(row["score_a"]["total_score"])
        score_b = float(row["score_b"]["total_score"])
        payloads.append(
            FreezePayload(
                code=row["code"],
                name=row["name"],
                industry=row["industry"],
                source_a_prediction_id=int(source[0]),
                source_a_score_date=str(source[1]),
                source_a_total_score=float(source[2]) if source[2] is not None else None,
                score_a=score_a,
                score_b=score_b,
                delta=float(row["delta"]),
                label=_framework_b_provisional_label(score_b, threshold_data),
                rule_name=rule_name,
                rule_snapshot=rule_snapshot,
                threshold_snapshot=threshold_snapshot,
                candidate_input_snapshot=_candidate_input_snapshot(row["code"]),
                score_snapshot={"score_a": row["score_a"], "score_b": row["score_b"]},
            )
        )
    return payloads


def insert_freeze_payloads(
    conn: sqlite3.Connection,
    payloads: list[FreezePayload],
    *,
    cohort_week: str,
    label_date: str,
    weights_hash: str,
) -> tuple[int, int]:
    ensure_framework_b_cohort_schema(conn)
    inserted = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        for payload in payloads:
            cursor = conn.execute(
                """INSERT INTO framework_b_label_cohorts
               (cohort_week, label_date, code, name, industry,
                source_a_prediction_id, source_a_score_date, source_a_total_score,
                score_a, score_b, delta, label, rule_name, rule_snapshot_json,
                threshold_snapshot_json, candidate_input_json, score_snapshot_json,
                weights_hash, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT DO NOTHING""",
                (
                    cohort_week,
                    label_date,
                    payload.code,
                    payload.name,
                    payload.industry,
                    payload.source_a_prediction_id,
                    payload.source_a_score_date,
                    payload.source_a_total_score,
                    payload.score_a,
                    payload.score_b,
                    payload.delta,
                    payload.label,
                    payload.rule_name,
                    json.dumps(payload.rule_snapshot, sort_keys=True, ensure_ascii=False),
                    json.dumps(payload.threshold_snapshot, sort_keys=True, ensure_ascii=False),
                    json.dumps(payload.candidate_input_snapshot, sort_keys=True, ensure_ascii=False),
                    json.dumps(payload.score_snapshot, sort_keys=True, ensure_ascii=False),
                    weights_hash,
                    "active",
                    datetime.now().isoformat(),
                ),
            )
            inserted += max(cursor.rowcount, 0)
        conn.commit()
    except (sqlite3.Error, TypeError, ValueError, OverflowError):
        conn.rollback()
        raise
    return inserted, len(payloads) - inserted


def freeze_weekly_cohort(
    conn: sqlite3.Connection,
    weights: dict[str, Any],
    *,
    label_date: str,
    dry_run: bool = False,
) -> FreezeResult:
    date.fromisoformat(label_date)
    week = _cohort_week(label_date)
    payloads = _build_freeze_payloads(conn, weights, label_date)
    if not payloads:
        return FreezeResult(week, label_date, 0, 0, 0, dry_run)
    if dry_run:
        return FreezeResult(week, label_date, len(payloads), 0, 0, True)
    inserted, skipped = insert_freeze_payloads(
        conn,
        payloads,
        cohort_week=week,
        label_date=label_date,
        weights_hash=_weights_hash(weights),
    )
    return FreezeResult(week, label_date, len(payloads), inserted, skipped, False)


def _empty_summary() -> CohortOutcomeSummary:
    return CohortOutcomeSummary(0, 0, 0, 0, 0, 0, None, False)


def summarize_cohort_outcomes(
    conn: sqlite3.Connection,
    *,
    as_of_date: str | None = None,
) -> CohortOutcomeSummary:
    if not cohort_table_exists(conn):
        return _empty_summary()
    today = date.fromisoformat(as_of_date) if as_of_date else date.today()
    rows = conn.execute(
        """SELECT c.source_a_prediction_id, MIN(c.cohort_week),
                  MIN(c.source_a_score_date), MAX(p.outcome_30d), COUNT(p.id)
           FROM framework_b_label_cohorts c
           LEFT JOIN predictions p ON p.id=c.source_a_prediction_id
           WHERE c.status='active'
           GROUP BY c.source_a_prediction_id"""
    ).fetchall()
    closed = 0
    future = 0
    overdue = 0
    missing = 0
    closed_weeks: set[str] = set()
    due_dates: list[date] = []
    for _, week, score_date, outcome, source_count in rows:
        if source_count == 0:
            missing += 1
            continue
        due = date.fromisoformat(score_date) + timedelta(days=30)
        due_dates.append(due)
        if outcome is not None:
            closed += 1
            closed_weeks.add(str(week))
        elif due > today:
            future += 1
        else:
            overdue += 1
    return CohortOutcomeSummary(
        sample_count=len(rows),
        closed_count=closed,
        closed_weeks=len(closed_weeks),
        future_count=future,
        overdue_count=overdue,
        missing_source_count=missing,
        earliest_due=min(due_dates).isoformat() if due_dates else None,
        ready_for_manual_review=closed >= 20 and len(closed_weeks) >= 3 and overdue == 0 and missing == 0,
    )


def append_framework_b_cohort_report(lines: list[str], conn: sqlite3.Connection) -> dict[str, Any]:
    summary = summarize_cohort_outcomes(conn)
    lines.extend(["", "── Framework B prospective frozen cohort（只读）──"])
    if not cohort_table_exists(conn):
        lines.append("尚未创建 cohort 表；先运行 framework-b-cohort-freeze --dry-run。")
    lines.append(
        f"冻结样本={summary.sample_count} 已结案30d={summary.closed_count}/20 "
        f"已结案周={summary.closed_weeks}/3 未来可结案={summary.future_count} "
        f"overdue={summary.overdue_count} source缺失={summary.missing_source_count}"
    )
    lines.append(f"最早可评估={summary.earliest_due or 'N/A'}")
    lines.append("本节按冻结 source_a_prediction_id 追踪；legacy B 记录不计入 prospective 门禁。")
    return {
        "b_label_sample_count": summary.sample_count,
        "b_label_closed_count": summary.closed_count,
        "b_label_closed_weeks": summary.closed_weeks,
        "b_label_earliest_due": summary.earliest_due,
        "b_label_overdue_count": summary.overdue_count + summary.missing_source_count,
        "b_label_ready": summary.ready_for_manual_review,
    }


def append_framework_b_legacy_report(
    lines: list[str],
    conn: sqlite3.Connection,
    weights_hash: str,
) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT score_date, code, total_score, alpha_30d, weights_hash
           FROM predictions
           WHERE framework='B' AND outcome_30d IS NOT NULL
           ORDER BY score_date, id"""
    ).fetchall()
    lines.extend(["", "── Framework B legacy retrospective validation（legacy-only）──"])
    if not rows:
        lines.append("暂无已结案 B 历史记录。")
        return {"sample_count": 0, "date_count": 0, "code_count": 0}
    dated = {str(row[0]) for row in rows}
    codes = {str(row[1]) for row in rows}
    historical_hashes = sorted({str(row[4]) for row in rows})
    alphas = [float(row[3]) for row in rows if row[3] is not None]
    hit_rate = sum(alpha_value > 0 for alpha_value in alphas) / len(alphas) if alphas else 0.0
    avg_alpha = sum(alphas) / len(alphas) if alphas else 0.0
    lines.append(
        f"样本={len(rows)} 股票={len(codes)} 评分日期={len(dated)} "
        f"30d超额命中率={hit_rate:.1%} 平均alpha={avg_alpha:+.2f}%"
    )
    lines.append(
        f"历史weights_hash={','.join(historical_hashes)}；当前全框架weights_hash={weights_hash}。"
        "hash 不同或缺少当时输入快照时，只能做 legacy 描述统计，不能声称与当前规则等价。"
    )
    for score_date in sorted(dated):
        date_alphas = [float(row[3]) for row in rows if str(row[0]) == score_date and row[3] is not None]
        date_hit = sum(value > 0 for value in date_alphas) / len(date_alphas) if date_alphas else 0.0
        date_avg = sum(date_alphas) / len(date_alphas) if date_alphas else 0.0
        lines.append(f"  - {score_date}: n={len(date_alphas)} hit={date_hit:.1%} avg_alpha={date_avg:+.2f}%")
    score_sorted = sorted(rows, key=lambda row: float(row[2] or 0.0), reverse=True)
    lines.append("B分数五分位（高→低，仅描述）：")
    for index in range(5):
        start = len(score_sorted) * index // 5
        end = len(score_sorted) * (index + 1) // 5
        bucket = score_sorted[start:end]
        if not bucket:
            continue
        bucket_alphas = [float(row[3]) for row in bucket if row[3] is not None]
        bucket_avg = sum(bucket_alphas) / len(bucket_alphas) if bucket_alphas else 0.0
        lines.append(f"  - Q{5 - index}: n={len(bucket)} avg_alpha={bucket_avg:+.2f}%")
    lines.append(
        "免责声明：同一评分日样本共享市场风险，不能视为独立样本；"
        "该结果仅作 legacy 回溯，不计入 prospective 门禁，也不解除生产写入限制。"
    )
    return {"sample_count": len(rows), "date_count": len(dated), "code_count": len(codes)}
