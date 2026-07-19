"""Accuracy report construction helpers."""

import sqlite3
from datetime import date, datetime, timedelta

import a_stock_tracker.config as config
from a_stock_tracker.data.cache import get_fundamentals
from a_stock_tracker.data.data_quality import FieldStatus, evaluate_data_quality
from a_stock_tracker.reporting.framework_b_report import (
    POST_FIX_DATE,
    append_framework_b_dry_run,
    append_framework_b_quality_expansion,
    append_phase6_readiness,
)


def _fmt(v) -> str:
    if v is None:
        return "N/A"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def _tier_rows_for_period(
    db: sqlite3.Connection,
    start_date: str | None,
    strong: float,
    moderate: float,
    light: float,
) -> list[tuple]:
    date_filter = "AND score_date >= ?" if start_date else ""
    query = f"""
        SELECT COUNT(*),
               ROUND(COUNT(CASE WHEN outcome_30d > 0 THEN 1 END) * 1.0 / NULLIF(COUNT(outcome_30d), 0), 3),
               ROUND(COUNT(CASE WHEN alpha_30d  > 0 THEN 1 END) * 1.0 / NULLIF(COUNT(alpha_30d),  0), 3),
               ROUND(COUNT(CASE WHEN alpha_60d  > 0 THEN 1 END) * 1.0 / NULLIF(COUNT(alpha_60d),  0), 3),
               ROUND(COUNT(CASE WHEN alpha_90d  > 0 THEN 1 END) * 1.0 / NULLIF(COUNT(alpha_90d),  0), 3),
               ROUND(AVG(alpha_30d), 2),
               ROUND(AVG(alpha_60d), 2),
               ROUND(AVG(alpha_90d), 2)
        FROM predictions
        WHERE outcome_30d IS NOT NULL
          AND framework = 'A'
          {date_filter}
          AND total_score >= ? AND total_score < ?
    """
    rows = []
    params_prefix: tuple = (start_date,) if start_date else ()
    for tier, lo, hi in [
        ("strong", strong, 9999),
        ("moderate", moderate, strong),
        ("light", light, moderate),
        ("no-action", 0, light),
    ]:
        r = db.execute(query, params_prefix + (lo, hi)).fetchone()
        if r and r[0] > 0:
            rows.append((tier,) + r)
    return rows


def _append_tier_rows(lines: list[str], rows: list[tuple]) -> None:
    header = f"{'信号层级':<10} {'条数':>5}  {'绝对30d':>8}  {'超额30d':>8}  {'超额60d':>8}  {'超额90d':>8}  {'α30d':>7}  {'α60d':>7}  {'α90d':>7}"
    lines.append(header)
    lines.append("-" * len(header))
    for row in rows:
        tier, cnt, h30, hb30, hb60, hb90, a30, a60, a90 = row
        lines.append(
            f"{tier:<10} {cnt:>5}  "
            f"{_fmt(h30):>8}  {_fmt(hb30):>8}  {_fmt(hb60):>8}  {_fmt(hb90):>8}  "
            f"{_fmt(a30):>7}  {_fmt(a60):>7}  {_fmt(a90):>7}"
        )


def _append_post_fix_section(
    lines: list[str],
    db: sqlite3.Connection,
    strong: float,
    moderate: float,
    light: float,
) -> None:
    post_fix_closed = db.execute(
        """SELECT COUNT(*) FROM predictions
           WHERE framework='A' AND score_date >= ? AND outcome_30d IS NOT NULL""",
        (POST_FIX_DATE,),
    ).fetchone()[0]
    pre_fix_closed = db.execute(
        """SELECT COUNT(*) FROM predictions
           WHERE framework='A' AND score_date < ? AND outcome_30d IS NOT NULL""",
        (POST_FIX_DATE,),
    ).fetchone()[0]

    lines.append("")
    lines.append(f"── Post-fix 样本专区（Framework A，score_date >= {POST_FIX_DATE}）──")
    lines.append(f"pre-fix 30d 结案：{pre_fix_closed}")
    lines.append(f"post-fix 30d 结案：{post_fix_closed}")
    if post_fix_closed < 100:
        lines.append(f"post-fix 样本不足（{post_fix_closed}/100），不得与 pre-fix 混合下结论")

    rows = _tier_rows_for_period(db, POST_FIX_DATE, strong, moderate, light)
    if rows:
        _append_tier_rows(lines, rows)
    else:
        lines.append("post-fix 暂无已结案分层样本。")


def _latest_prediction_audit_row(db: sqlite3.Connection, code: str) -> tuple | None:
    return db.execute(
        """SELECT price_at_score, report_period
           FROM predictions
           WHERE code=? AND framework='A'
           ORDER BY score_date DESC, id DESC
           LIMIT 1""",
        (code,),
    ).fetchone()


def _latest_qualitative_row(db: sqlite3.Connection, code: str) -> tuple | None:
    return db.execute(
        """SELECT scored_date FROM qualitative_scores
           WHERE code=? ORDER BY scored_date DESC LIMIT 1""",
        (code,),
    ).fetchone()


def _append_report_header(lines: list[str], db: sqlite3.Connection) -> None:
    null_count = db.execute("SELECT COUNT(*) FROM predictions WHERE outcome_30d IS NULL").fetchone()[0]
    framework_a_closed = db.execute(
        "SELECT COUNT(*) FROM predictions WHERE outcome_30d IS NOT NULL AND framework = 'A'"
    ).fetchone()[0]
    lines.extend(
        [
            "=" * 60,
            "a-stock-tracker 准确率报告",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "=" * 60,
        ]
    )
    if framework_a_closed < 100:
        lines.append(
            f"\n⚠️  样本不足（Framework A {framework_a_closed} 条已结案记录）\n"
            "    结论仅供参考，请勿据此做交易决策。\n"
            "    建议 Framework A 积累至 100 条以上再解读命中率。"
        )
    lines.append(f"\n已排除 {null_count} 条 NULL outcome 记录（停牌/退市/数据缺失），详见 daily_log.txt")
    lines.append("\n⚠️  选择性偏差声明：watchlist 为手动维护的已知标的，命中率不代表框架泛化能力。")
    lines.append("")


def _append_framework_stats(lines: list[str], db: sqlite3.Connection) -> None:
    fw_rows = db.execute(
        """SELECT framework, COUNT(*), COUNT(outcome_30d),
                  ROUND(COUNT(CASE WHEN alpha_30d > 0 THEN 1 END) * 1.0
                        / NULLIF(COUNT(alpha_30d), 0), 3)
           FROM predictions
           GROUP BY framework ORDER BY framework"""
    ).fetchall()
    if not fw_rows:
        return
    lines.append("── 分 Framework 统计 ──")
    lines.append(f"{'Framework':<12} {'总记录':>6}  {'30d结案':>8}  {'超额命中30d':>12}")
    for fw, total, closed30, hr30 in fw_rows:
        lines.append(f"{fw:<12} {total:>6}  {closed30:>8}  {_fmt(hr30):>12}")
    lines.append("")


def _append_quintile_monotonicity(lines: list[str], db: sqlite3.Connection) -> None:
    q_rows = db.execute(
        """SELECT quintile, COUNT(*) as cnt, ROUND(MIN(total_score), 1) as lo,
                  ROUND(MAX(total_score), 1) as hi, ROUND(AVG(alpha_30d), 2) as avg_a30
           FROM (
               SELECT total_score, alpha_30d, NTILE(5) OVER (ORDER BY total_score) as quintile
               FROM predictions WHERE outcome_30d IS NOT NULL AND framework = 'A'
           ) GROUP BY quintile ORDER BY quintile DESC"""
    ).fetchall()
    if q_rows and len(q_rows) >= 3:
        lines.append("")
        lines.append("── 五分位单调性检验（Framework A，分数最高→最低）──")
        lines.append(f"{'分位':>4}  {'样本':>5}  {'分数范围':>12}  {'α30d均值':>10}")
        for qnum, cnt, lo, hi, a30 in q_rows:
            label = {5: "Q5(高)", 4: "Q4", 3: "Q3", 2: "Q2", 1: "Q1(低)"}.get(qnum, f"Q{qnum}")
            lines.append(f"{label:>6}  {cnt:>5}  [{lo:>5} ~{hi:>5}]  {_fmt(a30):>10}")
        lines.append("  理想：Q5 alpha > Q4 > Q3 > ... > Q1（单调递减 = 框架有序预测力）")
    lines.append("")
    lines.append("解读：hit_vs300 > 55% 才开始有意义；avg_alpha > 2% 且样本≥20 可认为有初步信号")
    lines.append("      不同 weights_hash 的记录代表不同实验，请分开解读")


def _compute_l3_coverage_stats(db: sqlite3.Connection) -> tuple:
    l3_counts = db.execute(
        """SELECT
               COUNT(CASE WHEN entry_signal_version='v1' THEN 1 END) AS v1_count,
               COUNT(CASE WHEN entry_signal=1 THEN 1 END) AS pass_count,
               COUNT(CASE WHEN entry_signal=0 THEN 1 END) AS reject_count,
               COUNT(CASE WHEN entry_signal IS NULL THEN 1 END) AS null_count,
               COUNT(CASE WHEN entry_signal IS NULL AND entry_signal_version IS NULL THEN 1 END) AS pre_l3_count,
               COUNT(CASE WHEN entry_signal IS NULL AND entry_signal_version='v1' THEN 1 END) AS null_v1_count
           FROM predictions"""
    ).fetchone()
    reason_rows = db.execute(
        """SELECT COALESCE(entry_signal_reason, 'UNKNOWN'), COUNT(*)
           FROM predictions
           WHERE entry_signal IS NULL AND entry_signal_version='v1'
           GROUP BY COALESCE(entry_signal_reason, 'UNKNOWN')
           ORDER BY COUNT(*) DESC, 1"""
    ).fetchall()
    v1_count, pass_count, reject_count, l3_null_count, pre_l3_count, null_v1_count = l3_counts
    computable_v1 = pass_count + reject_count
    l3_coverage = (computable_v1 / v1_count * 100) if v1_count else 0.0
    return (*l3_counts, computable_v1, l3_coverage, reason_rows)


def _compute_l3_strong_and_closed_stats(db: sqlite3.Connection, strong: float) -> tuple:
    strong_l3 = db.execute(
        """SELECT
               COUNT(CASE WHEN entry_signal=1 THEN 1 END) AS strong_pass,
               COUNT(CASE WHEN entry_signal=0 THEN 1 END) AS strong_reject,
               COUNT(CASE WHEN entry_signal IS NULL AND entry_signal_version='v1' THEN 1 END) AS strong_unavailable
           FROM predictions
           WHERE framework='A' AND total_score >= ?""",
        (strong,),
    ).fetchone()
    l3_closed = db.execute(
        """SELECT COUNT(*) AS closed_count,
                  ROUND(COUNT(CASE WHEN alpha_30d > 0 THEN 1 END) * 1.0
                        / NULLIF(COUNT(alpha_30d), 0), 3) AS hit_rate
           FROM predictions
           WHERE framework='A' AND entry_signal=1 AND entry_signal_version='v1'
             AND outcome_30d IS NOT NULL"""
    ).fetchone()
    return (*strong_l3, *l3_closed)


def _append_l3_signal_lines(lines: list[str], coverage_stats: tuple, strong_closed_stats: tuple) -> None:
    v1_count, pass_count, reject_count, l3_null_count, pre_l3_count, null_v1_count, computable, coverage, reasons = (
        coverage_stats
    )
    strong_pass, strong_reject, strong_unavailable, closed_count, hit_rate = strong_closed_stats
    lines.extend(
        [
            "",
            "── L3 买点层 ──",
            f"v1 记录数：{v1_count}",
            f"entry_signal=1：{pass_count}",
            f"entry_signal=0：{reject_count}",
            f"entry_signal=NULL：{l3_null_count}",
            f"NULL/NULL pre-L3：{pre_l3_count}",
            f"NULL/v1 不可计算：{null_v1_count}",
            f"L3 覆盖率：{computable}/{v1_count} = {coverage:.1f}%",
        ]
    )
    if reasons:
        reason_summary = ", ".join(f"{reason}={count}" for reason, count in reasons)
        lines.append(f"不可计算原因：{reason_summary}")
    lines.extend(
        [
            f"strong 候选 L3 通过：{strong_pass}",
            f"strong 候选 L3 拒绝：{strong_reject}",
            f"strong 候选中 L3 不可计算：{strong_unavailable}",
            f"高分但未推送：{strong_unavailable}（L3 unavailable）",
            f"L3 30d 已结案：{closed_count}",
            f"L3 30d 命中率：{_fmt(hit_rate)}",
        ]
    )
    if closed_count < 30:
        lines.append(f"L3 30d 样本不足（{closed_count}/30），不得输出确定性结论")


def _append_gemini_drift_section(lines: list[str], db: sqlite3.Connection) -> None:
    lines.extend(["", "── Gemini 评分稳定性 ──"])
    drift_rows = db.execute(
        """SELECT q1.code, q1.moat AS first_moat, q2.moat AS latest_moat,
                  q1.sentiment AS first_sent, q2.sentiment AS latest_sent,
                  q1.scored_date AS first_date, q2.scored_date AS latest_date
           FROM qualitative_scores q1 JOIN qualitative_scores q2 ON q1.code = q2.code
           WHERE q1.scored_date = (SELECT MIN(scored_date) FROM qualitative_scores WHERE code=q1.code)
             AND q2.scored_date = (SELECT MAX(scored_date) FROM qualitative_scores WHERE code=q1.code)
             AND q1.scored_date != q2.scored_date"""
    ).fetchall()
    stock_count = db.execute("SELECT COUNT(DISTINCT code) FROM qualitative_scores").fetchone()[0]
    first_date_row = db.execute("SELECT MIN(scored_date) FROM qualitative_scores").fetchone()[0]
    if not drift_rows:
        if first_date_row:
            next_reeval = (date.fromisoformat(first_date_row) + timedelta(days=30)).isoformat()
            lines.append(f"{stock_count} 只股已评，尚无重评数据")
            lines.append(f"预计首批重评：{next_reeval}（30天缓存到期）")
        else:
            lines.append("尚无 Gemini 评分记录")
        return
    lines.append(f"{'股票':<8} {'首次日期':<12} {'最新日期':<12} {'moat变化':>8} {'sent变化':>8} 状态")
    lines.append("-" * 56)
    for code, fm, lm, fs, ls, fd, ld in drift_rows:
        moat_delta = lm - fm
        sent_delta = ls - fs
        flag = " ⚠️ low_confidence" if abs(moat_delta) > 2 or abs(sent_delta) > 2 else ""
        lines.append(f"{code:<8} {fd:<12} {ld:<12} {moat_delta:>+8} {sent_delta:>+8}{flag}")


def _classify_report_period_gap(
    latest, cache_report_period, prediction_report_period
) -> tuple[bool, bool, bool, str | None]:
    if prediction_report_period:
        return False, False, False, None
    if latest and cache_report_period:
        return True, False, False, "missing:prediction_report_period(history_locked_cache_ready)"
    if latest:
        return False, True, False, "missing:prediction_report_period(new_record_pending_cache_missing)"
    return False, False, True, "missing:prediction_report_period(no_a_record)"


def _classify_stock_audit(
    db, code, item, data, latest, cache_report_period, prediction_report_period, result
) -> dict[str, object]:
    qualitative = _latest_qualitative_row(db, code)
    status_by_name = {field.name: field.status for field in result.fields}
    issues = list(result.missing_required)
    stale_fields = [field.name for field in result.fields if field.status == FieldStatus.STALE]
    issues.extend(f"stale:{name}" for name in stale_fields)
    if not cache_report_period:
        issues.append("missing:cache_report_period")
    history_locked, new_record_pending, no_a_record, period_issue = _classify_report_period_gap(
        latest, cache_report_period, prediction_report_period
    )
    if period_issue:
        issues.append(period_issue)
    if data.get("price_at_score") is None:
        issues.append("missing:price_at_score")
    if not qualitative:
        issues.append("gemini:no_cache")
    problem = f"{code} {item['name']}: {', '.join(issues)}" if issues else None
    return {
        "missing_cache": False,
        "acceptable": result.is_acceptable,
        "pb_ready": status_by_name.get("pb_percentile_10y") == FieldStatus.OK,
        "financial_gross_margin_na": status_by_name.get("gross_margin") == FieldStatus.NOT_APPLICABLE,
        "cache_report_period_missing": not cache_report_period,
        "prediction_report_period_missing": not prediction_report_period,
        "prediction_report_period_history_locked": history_locked,
        "prediction_report_period_new_record_pending": new_record_pending,
        "prediction_report_period_no_a_record": no_a_record,
        "gemini_cached": bool(qualitative),
        "problem": problem,
    }


def _audit_one_watchlist_stock(db: sqlite3.Connection, item: dict) -> dict[str, object]:
    code = item["code"]
    fundamentals = get_fundamentals(code)
    if not fundamentals:
        return {"missing_cache": True, "problem": f"{code} {item['name']}: cache_missing_or_expired"}
    data = dict(fundamentals.get("data", fundamentals))
    meta = fundamentals.get("_cache_meta", {})
    industry = str(meta.get("industry") or data.get("industry") or "")
    cache_report_period = data.get("report_period")
    latest = _latest_prediction_audit_row(db, code)
    prediction_report_period = latest[1] if latest else None
    if latest:
        data["price_at_score"] = latest[0]
        data["report_period"] = cache_report_period or prediction_report_period
    result = evaluate_data_quality(code, data, industry=f"{industry} {item['name']}")
    return _classify_stock_audit(db, code, item, data, latest, cache_report_period, prediction_report_period, result)


def _append_data_quality_summary_lines(lines: list[str], total: int, counters: dict, problem_rows: list[str]) -> None:
    audited = counters["audited"]
    lines.extend(
        [
            "",
            "── 数据质量审计（当前 watchlist）──",
            f"watchlist 股票数：{total}",
            f"基本面缓存可用：{audited}/{total}",
            f"基本面缓存缺失或过期：{counters['missing_cache']}",
            f"required 字段可接受：{counters['acceptable']}/{audited if audited else 0}",
            f"金融行业 gross_margin 不适用：{counters['financial_gross_margin_na']}",
            f"PB 日度可计算：{counters['pb_ready']}/{audited if audited else 0}",
            f"cache report_period 缺失：{counters['cache_report_period_missing']}/{audited if audited else 0}",
            f"prediction report_period 缺失：{counters['prediction_report_period_missing']}/{audited if audited else 0}",
        ]
    )
    missing = counters["prediction_report_period_missing"]
    if missing:
        history = counters["prediction_report_period_history_locked"]
        pending = counters["prediction_report_period_new_record_pending"]
        no_record = counters["prediction_report_period_no_a_record"]
        lines.append(
            f"prediction report_period 缺失拆分：历史记录不可回填={history}, 新记录待补齐={pending}, 无A记录={no_record}"
        )
        if history:
            lines.append(
                "  - 历史记录不可回填：缓存已有 report_period，但最新 prediction 已写入为空；本报告不改写历史 predictions。"
            )
        if pending:
            lines.append("  - 新记录待补齐：缓存仍缺 report_period，需先运行 weekly/fetch 成功后未来 daily 才能写入。")
        if no_record:
            lines.append("  - 无A记录：尚无可审计的 Framework A prediction。")
    lines.append(f"Gemini 缓存存在：{counters['gemini_cached']}/{total}")
    if problem_rows:
        lines.append("需处理样本（最多 12 条）：")
        lines.extend(f"  - {row}" for row in problem_rows[:12])
        if len(problem_rows) > 12:
            lines.append(f"  - ... 另 {len(problem_rows) - 12} 条")
    else:
        lines.append("未发现数据质量问题。")


def _append_data_quality_audit(lines: list[str], db: sqlite3.Connection) -> dict[str, int]:
    keys = (
        "missing_cache",
        "acceptable",
        "pb_ready",
        "financial_gross_margin_na",
        "cache_report_period_missing",
        "prediction_report_period_missing",
        "prediction_report_period_history_locked",
        "prediction_report_period_new_record_pending",
        "prediction_report_period_no_a_record",
        "gemini_cached",
    )
    counters = {key: 0 for key in keys}
    problem_rows = []
    for item in config.WATCHLIST:
        audit = _audit_one_watchlist_stock(db, item)
        for key in keys:
            counters[key] += int(bool(audit.get(key)))
        if audit.get("problem"):
            problem_rows.append(str(audit["problem"]))
    total = len(config.WATCHLIST)
    counters["audited"] = total - counters["missing_cache"]
    _append_data_quality_summary_lines(lines, total, counters, problem_rows)
    return {"watchlist_total": total, **counters}


def _append_legacy_threshold_progress(
    lines: list[str], db: sqlite3.Connection, weights_hash: str, rows: list[tuple]
) -> None:
    lines.extend(["", "── Framework B 旧重启门槛进度（A框生产化前置，不等同 report-only）──"])
    closed_a = db.execute(
        "SELECT COUNT(*) FROM predictions WHERE framework='A' AND outcome_30d IS NOT NULL AND weights_hash=?",
        (weights_hash,),
    ).fetchone()[0]
    threshold1_met = closed_a >= 100
    lines.append(f"门槛 1：A框 30d 结案 ≥ 100（当前权重）：当前 {closed_a} / 100  {'✅' if threshold1_met else '❌'}")
    threshold2_met = any(row[3] is not None and row[3] > 0.55 and row[1] >= 20 for row in rows)
    lines.append(f"门槛 2：任一层级 hit_rate_vs_300 > 55%（≥20条）：{'✅ 已满足' if threshold2_met else '❌ 尚未满足'}")
    if threshold1_met and threshold2_met:
        lines.append("→ 两个门槛同时满足，才可讨论 Framework B 生产写入；report-only 不受此门槛阻断。")
    else:
        lines.append("→ 生产写入继续等待；report-only 研究可按后续小节推进。")


def _append_framework_b_sections(lines: list[str], db, weights: dict, data_quality_summary: dict) -> None:
    framework_b_summary = append_framework_b_dry_run(lines, db, weights)
    quality_summary = append_framework_b_quality_expansion(lines, db, weights)
    readiness_summary = dict(framework_b_summary)
    for key in ("b_label_sample_count", "b_label_closed_count", "b_label_earliest_due", "b_label_overdue_count"):
        if key in quality_summary:
            readiness_summary[key] = quality_summary[key]
    append_phase6_readiness(lines, db, data_quality_summary, readiness_summary)


def build_accuracy_report(db: sqlite3.Connection, weights: dict, weights_hash: str) -> str:
    lines: list[str] = []
    _append_report_header(lines, db)
    _append_framework_stats(lines, db)
    thresholds = weights.get("thresholds", {})
    strong = thresholds.get("buy_strong", 55)
    moderate = thresholds.get("buy_moderate", 45)
    light = thresholds.get("buy_light", 35)
    rows = _tier_rows_for_period(db, None, strong, moderate, light)
    if rows:
        _append_tier_rows(lines, rows)
    else:
        lines.append("暂无已结案记录（outcome_30d 全部为 NULL）。")
    _append_post_fix_section(lines, db, strong, moderate, light)
    _append_quintile_monotonicity(lines, db)
    coverage_stats = _compute_l3_coverage_stats(db)
    strong_closed_stats = _compute_l3_strong_and_closed_stats(db, strong)
    _append_l3_signal_lines(lines, coverage_stats, strong_closed_stats)
    _append_gemini_drift_section(lines, db)
    data_quality_summary = _append_data_quality_audit(lines, db)
    _append_legacy_threshold_progress(lines, db, weights_hash, rows)
    _append_framework_b_sections(lines, db, weights, data_quality_summary)
    return "\n".join(lines)
