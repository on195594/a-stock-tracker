"""Accuracy report construction helpers."""

import sqlite3
from datetime import datetime

POST_FIX_DATE = "2026-05-15"


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


def _append_report_header(lines: list[str], db: sqlite3.Connection) -> None:
    null_count = db.execute("SELECT COUNT(*) FROM predictions WHERE framework='A' AND outcome_30d IS NULL").fetchone()[
        0
    ]
    framework_a_closed = db.execute(
        "SELECT COUNT(*) FROM predictions WHERE outcome_30d IS NOT NULL AND framework = 'A'"
    ).fetchone()[0]
    lines.extend(
        [
            "=" * 60,
            "a-stock-tracker 策略评估报告",
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


def _append_l3_v2_section(lines: list[str], db: sqlite3.Connection, strong: float) -> None:
    total, v2_count, passed, rejected, unavailable, pre_v2 = db.execute(
        """SELECT
               COUNT(*),
               COUNT(CASE WHEN l3_v2_version IS NOT NULL THEN 1 END),
               COUNT(CASE WHEN l3_v2_signal=1 AND l3_v2_version IS NOT NULL THEN 1 END),
               COUNT(CASE WHEN l3_v2_signal=0 AND l3_v2_version IS NOT NULL THEN 1 END),
               COUNT(CASE WHEN l3_v2_signal IS NULL AND l3_v2_version IS NOT NULL THEN 1 END),
               COUNT(CASE WHEN l3_v2_signal IS NULL AND l3_v2_version IS NULL THEN 1 END)
           FROM predictions
           WHERE framework='A'"""
    ).fetchone()
    reason_rows = db.execute(
        """SELECT COALESCE(l3_v2_reason, 'UNKNOWN'), COUNT(*)
           FROM predictions
           WHERE framework='A'
             AND l3_v2_signal IS NULL
             AND l3_v2_version IS NOT NULL
           GROUP BY COALESCE(l3_v2_reason, 'UNKNOWN')
           ORDER BY COUNT(*) DESC, 1"""
    ).fetchall()
    strong_pass, strong_reject, strong_unavailable, strong_pre_v2 = db.execute(
        """SELECT
               COUNT(CASE WHEN l3_v2_signal=1 AND l3_v2_version IS NOT NULL THEN 1 END),
               COUNT(CASE WHEN l3_v2_signal=0 AND l3_v2_version IS NOT NULL THEN 1 END),
               COUNT(CASE WHEN l3_v2_signal IS NULL AND l3_v2_version IS NOT NULL THEN 1 END),
               COUNT(CASE WHEN l3_v2_signal IS NULL AND l3_v2_version IS NULL THEN 1 END)
           FROM predictions
           WHERE framework='A' AND total_score >= ?""",
        (strong,),
    ).fetchone()
    outcome_rows = db.execute(
        """SELECT l3_v2_signal,
                  COUNT(*),
                  ROUND(AVG(alpha_30d), 2),
                  ROUND(COUNT(CASE WHEN alpha_30d > 0 THEN 1 END) * 1.0 / COUNT(*), 3)
           FROM predictions
           WHERE framework='A'
             AND l3_v2_version IS NOT NULL
             AND l3_v2_signal IN (0, 1)
             AND outcome_30d IS NOT NULL
             AND benchmark_30d IS NOT NULL
           GROUP BY l3_v2_signal
           ORDER BY l3_v2_signal DESC"""
    ).fetchall()
    coverage = ((passed + rejected) / v2_count * 100) if v2_count else 0.0
    lines.extend(
        [
            "",
            "── L3 v2 风险门禁 ──",
            "当前规则仅按风险门禁解释，不代表已验证买点。",
            f"Framework A 总记录：{total}",
            f"v2 记录：{v2_count}",
            f"通过/拒绝/不可用：{passed}/{rejected}/{unavailable}",
            f"pre-v2 记录：{pre_v2}",
            f"可判断覆盖率：{passed + rejected}/{v2_count} = {coverage:.1f}%",
            f"strong v2 候选通过/拒绝/不可用：{strong_pass}/{strong_reject}/{strong_unavailable}",
            f"strong pre-v2 候选：{strong_pre_v2}",
        ]
    )
    if reason_rows:
        reason_summary = ", ".join(f"{reason}={count}" for reason, count in reason_rows)
        lines.append(f"不可计算原因：{reason_summary}")
    for signal, count, avg_alpha, hit_rate in outcome_rows:
        label = "通过" if signal == 1 else "拒绝"
        lines.append(f"{label}后 30d：样本={count}，平均alpha={_fmt(avg_alpha)}，超额命中率={_fmt(hit_rate)}")
    if not outcome_rows:
        lines.append("暂无可比较的 v2 30d 已结案样本。")
    else:
        closed_count = sum(int(row[1]) for row in outcome_rows)
        if closed_count < 30:
            lines.append(f"L3 v2 30d 样本不足（{closed_count}/30），不得输出确定性结论。")


def build_accuracy_report(db: sqlite3.Connection, weights: dict) -> str:
    lines: list[str] = []
    _append_report_header(lines, db)
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
    _append_l3_v2_section(lines, db, strong)
    return "\n".join(lines)
