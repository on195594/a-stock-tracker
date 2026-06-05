"""Framework B report-only accuracy report helpers."""

import math
import sqlite3
from datetime import date, timedelta
from typing import Any

import config
from lib.cache import get_fundamentals
from scorer import (
    InsufficientDataError,
    UnsupportedFrameworkError,
    compute_daily_pb_percentile,
    score_stock,
)

POST_FIX_DATE = "2026-05-15"


def _is_framework_b_candidate(item: dict, fundamentals: dict | None) -> bool:
    text_parts = [item.get("name", "")]
    if fundamentals:
        meta = fundamentals.get("_cache_meta", {})
        text_parts.extend([meta.get("industry", ""), fundamentals.get("industry", "")])
    text = " ".join(str(part) for part in text_parts)
    return any(keyword in text for keyword in ("银行", "证券", "券商", "金融", "保险"))


FRAMEWORK_B_QUALITY_RULES = {
    "conservative": {
        "roe_3y_avg_min": 15.0,
        "gross_margin_min": 25.0,
        "debt_ratio_max": 50.0,
        "pb_percentile_10y_max": 40.0,
    },
    "base": {
        "roe_3y_avg_min": 12.0,
        "gross_margin_min": 20.0,
        "debt_ratio_max": 60.0,
        "pb_percentile_10y_max": 60.0,
    },
    "loose": {
        "roe_3y_avg_min": 10.0,
        "gross_margin_min": 15.0,
        "debt_ratio_max": 70.0,
        "pb_percentile_10y_max": 70.0,
    },
}


def _framework_b_candidate_industry(item: dict, fundamentals: dict | None) -> str:
    if fundamentals:
        meta = fundamentals.get("_cache_meta", {})
        industry = str(meta.get("industry") or fundamentals.get("industry") or "").strip()
        if industry and industry != "未知":
            return industry
    name = item.get("name", "")
    if "银行" in name:
        return "银行"
    if "证券" in name or "券商" in name:
        return "证券"
    if "保险" in name:
        return "保险"
    if "金融" in name:
        return "金融"
    if "医药" in name:
        return "医药"
    if "化学" in name or "化工" in name:
        return "化工"
    if "建材" in name:
        return "建材"
    if "电缆" in name or "电工" in name or "电气" in name:
        return "电力设备"
    if "智控" in name or "精密" in name or "富联" in name:
        return "制造"
    if "电力" in name or "发电" in name:
        return "公用事业"
    if "石油" in name or "海油" in name or "煤业" in name or "矿业" in name:
        return "资源能源"
    if "百货" in name or "超市" in name:
        return "零售"
    if "酒" in name:
        return "白酒"
    if "移动" in name or "电信" in name:
        return "通信"
    return "未识别"


def _is_framework_b_quality_candidate(data: dict, industry: str, rule: dict[str, float]) -> bool:
    if industry in {"银行", "证券", "保险", "金融"}:
        return False
    gross_margin = data.get("gross_margin")
    if gross_margin is None:
        return False
    return (
        data.get("roe_3y_avg") is not None
        and data["roe_3y_avg"] >= rule["roe_3y_avg_min"]
        and gross_margin >= rule["gross_margin_min"]
        and data.get("debt_ratio") is not None
        and data["debt_ratio"] <= rule["debt_ratio_max"]
        and data.get("pb_percentile_10y") is not None
        and data["pb_percentile_10y"] <= rule["pb_percentile_10y_max"]
    )


def _framework_b_quality_reason(data: dict) -> str:
    return (
        f"ROE={data.get('roe_3y_avg')}, "
        f"GM={data.get('gross_margin')}, "
        f"Debt={data.get('debt_ratio')}, "
        f"PBpct={data.get('pb_percentile_10y')}"
    )


def _score_framework_b_candidate(
    code: str,
    name: str,
    industry: str,
    data: dict,
    weights: dict,
) -> dict:
    score_a = score_stock(code, "A", data, weights=weights)
    score_b = score_stock(code, "B", data, weights=weights, enforce_supported=False)
    return {
        "code": code,
        "name": name,
        "industry": industry,
        "score_a": score_a,
        "score_b": score_b,
        "delta": score_b["total_score"] - score_a["total_score"],
    }


def _append_framework_b_deep_dive(
    lines: list[str],
    scored: list[dict],
    industry_counts: dict[str, int],
    watchlist_total: int,
) -> None:
    lines.append("B 候选行业分布：")
    for industry, count in sorted(industry_counts.items(), key=lambda row: (-row[1], row[0])):
        lines.append(f"  - {industry}: {count}")

    candidate_count = len(scored)
    financial_only = candidate_count < watchlist_total and set(industry_counts) <= {"银行", "证券", "保险", "金融"}
    if financial_only:
        lines.append(
            "B 适用性判断：当前 report-only 候选筛选是金融行业专用口径；"
            "B 权重本身是“质量优先/估值降权”，尚不能证明适用于全行业。"
        )
    else:
        lines.append("B 适用性判断：当前候选已覆盖非金融行业，可继续观察跨行业稳定性。")

    if not scored:
        return

    field_names = sorted({
        field
        for row in scored
        for field in set(row["score_a"]["component_scores"]) | set(row["score_b"]["component_scores"])
    })
    lines.append("B-A 分项得分差异均值：")
    for field in field_names:
        avg_delta = sum(
            row["score_b"]["component_scores"].get(field, 0.0)
            - row["score_a"]["component_scores"].get(field, 0.0)
            for row in scored
        ) / len(scored)
        lines.append(f"  - {field}: {avg_delta:+.2f}")

    lines.append("B-A delta Top：")
    for row in sorted(scored, key=lambda item: item["delta"], reverse=True)[:5]:
        lines.append(
            f"  - {row['name']}({row['code']}) "
            f"B={row['score_b']['total_score']:.1f} A={row['score_a']['total_score']:.1f} Δ={row['delta']:+.1f}"
        )

    lines.append("B-A delta Bottom：")
    for row in sorted(scored, key=lambda item: item["delta"])[:5]:
        lines.append(
            f"  - {row['name']}({row['code']}) "
            f"B={row['score_b']['total_score']:.1f} A={row['score_a']['total_score']:.1f} Δ={row['delta']:+.1f}"
        )


def _latest_prediction_audit_row(db: sqlite3.Connection, code: str) -> tuple | None:
    return db.execute(
        """SELECT price_at_score, report_period
           FROM predictions
           WHERE code=? AND framework='A'
           ORDER BY score_date DESC, id DESC
           LIMIT 1""",
        (code,),
    ).fetchone()


def append_framework_b_dry_run(lines: list[str], db: sqlite3.Connection, weights: dict) -> dict[str, int]:
    lines.append("")
    lines.append("── Framework B 金融候选 dry-run（report-only，不写 predictions）──")
    lines.append("口径：银行/证券/保险/金融关键词候选；仅检查原始金融口径覆盖，不参与 B label 阈值。")
    if "B" not in weights.get("frameworks", {}):
        lines.append("weights.json 未配置 Framework B，跳过 dry-run。")
        return {"candidate_count": 0, "scored_count": 0, "skipped_count": 0}

    candidate_count = 0
    industry_counts: dict[str, int] = {}
    scored: list[dict] = []
    skipped: list[str] = []

    for item in config.WATCHLIST:
        code = item["code"]
        fundamentals = get_fundamentals(code)
        if not _is_framework_b_candidate(item, fundamentals):
            continue
        candidate_count += 1
        industry = _framework_b_candidate_industry(item, fundamentals)
        industry_counts[industry] = industry_counts.get(industry, 0) + 1
        if not fundamentals:
            skipped.append(f"{code}: cache_missing_or_expired")
            continue
        data = dict(fundamentals.get("data", fundamentals))
        latest = _latest_prediction_audit_row(db, code)
        if latest and latest[0]:
            daily_pct = compute_daily_pb_percentile(latest[0], data)
            if daily_pct is not None:
                data["pb_percentile_10y"] = daily_pct
        try:
            scored_row = _score_framework_b_candidate(code, item["name"], industry, data, weights)
        except (InsufficientDataError, UnsupportedFrameworkError) as e:
            skipped.append(f"{code}: {e}")
            continue
        scored.append(scored_row)

    lines.append(f"候选样本：{candidate_count}")
    lines.append(f"可评分：{len(scored)}")
    lines.append(f"跳过：{len(skipped)}")
    if not scored:
        if skipped:
            lines.append("跳过原因（最多 8 条）：")
            lines.extend(f"  - {row}" for row in skipped[:8])
        return {
            "candidate_count": candidate_count,
            "scored_count": 0,
            "skipped_count": len(skipped),
        }

    avg_a = sum(row["score_a"]["total_score"] for row in scored) / len(scored)
    avg_b = sum(row["score_b"]["total_score"] for row in scored) / len(scored)
    avg_delta = sum(row["delta"] for row in scored) / len(scored)
    lines.append(f"A 均分：{avg_a:.2f}")
    lines.append(f"B dry-run 均分：{avg_b:.2f}")
    lines.append(f"B-A 平均差：{avg_delta:+.2f}")
    lines.append("B dry-run Top 8：")
    for row in sorted(scored, key=lambda item: item["score_b"]["total_score"], reverse=True)[:8]:
        lines.append(
            f"  - {row['name']}({row['code']}) "
            f"B={row['score_b']['total_score']:.1f} A={row['score_a']['total_score']:.1f} Δ={row['delta']:+.1f}"
        )
    _append_framework_b_deep_dive(lines, scored, industry_counts, len(config.WATCHLIST))
    if skipped:
        lines.append("跳过原因（最多 8 条）：")
        lines.extend(f"  - {row}" for row in skipped[:8])
    return {
        "candidate_count": candidate_count,
        "scored_count": len(scored),
        "skipped_count": len(skipped),
    }


def _framework_b_quality_rule_text(rule: dict[str, float]) -> str:
    return (
        "非金融；"
        f"ROE≥{rule['roe_3y_avg_min']:.0f}；"
        f"gross_margin≥{rule['gross_margin_min']:.0f}；"
        f"debt_ratio≤{rule['debt_ratio_max']:.0f}；"
        f"PB分位≤{rule['pb_percentile_10y_max']:.0f}"
    )


def _collect_framework_b_quality_candidates(
    db: sqlite3.Connection,
    weights: dict,
    rule: dict[str, float],
) -> dict:
    industry_counts: dict[str, int] = {}
    scored: list[dict] = []
    skipped: list[str] = []

    for item in config.WATCHLIST:
        code = item["code"]
        fundamentals = get_fundamentals(code)
        if not fundamentals:
            continue
        industry = _framework_b_candidate_industry(item, fundamentals)
        data = dict(fundamentals.get("data", fundamentals))
        latest = _latest_prediction_audit_row(db, code)
        if latest and latest[0]:
            daily_pct = compute_daily_pb_percentile(latest[0], data)
            if daily_pct is not None:
                data["pb_percentile_10y"] = daily_pct
        if not _is_framework_b_quality_candidate(data, industry, rule):
            continue
        industry_counts[industry] = industry_counts.get(industry, 0) + 1
        try:
            scored_row = _score_framework_b_candidate(code, item["name"], industry, data, weights)
            scored_row["quality_reason"] = _framework_b_quality_reason(data)
            scored.append(scored_row)
        except (InsufficientDataError, UnsupportedFrameworkError) as e:
            skipped.append(f"{code}: {e}")

    candidate_count = sum(industry_counts.values())
    summary = {
        "candidate_count": candidate_count,
        "scored_count": len(scored),
        "skipped_count": len(skipped),
        "industry_counts": industry_counts,
        "scored": scored,
        "skipped": skipped,
        "avg_a": None,
        "avg_b": None,
        "avg_delta": None,
    }
    if scored:
        summary["avg_a"] = sum(row["score_a"]["total_score"] for row in scored) / len(scored)
        summary["avg_b"] = sum(row["score_b"]["total_score"] for row in scored) / len(scored)
        summary["avg_delta"] = sum(row["delta"] for row in scored) / len(scored)
    return summary


def _framework_b_quality_recommendation(summaries: dict[str, dict]) -> str:
    eligible = [
        (name, summary)
        for name, summary in summaries.items()
        if summary["scored_count"] >= 5 and summary["avg_delta"] is not None
    ]
    if not eligible:
        return "后续积累建议：暂无规则达到最小样本（≥5），先扩大 watchlist 或放宽 loose 规则。"
    positive = [(name, summary) for name, summary in eligible if summary["avg_delta"] > 0]
    if positive:
        name, summary = max(positive, key=lambda item: (item[1]["scored_count"], item[1]["avg_delta"]))
        return (
            f"后续积累建议：优先采用 {name} 规则，"
            f"样本={summary['scored_count']}，B-A={summary['avg_delta']:+.2f}，"
            "兼顾覆盖和正向差异。"
        )
    name, summary = max(eligible, key=lambda item: item[1]["avg_delta"])
    return (
        f"后续积累建议：三组规则均未转正，暂以 {name} 作为观察上限，"
        f"B-A={summary['avg_delta']:+.2f}，不设计独立阈值。"
    )


def _pick_framework_b_quality_rule(summaries: dict[str, dict]) -> tuple[str, dict] | None:
    eligible = [
        (name, summary)
        for name, summary in summaries.items()
        if summary["scored_count"] >= 5 and summary["avg_delta"] is not None
    ]
    positive = [(name, summary) for name, summary in eligible if summary["avg_delta"] > 0]
    if positive:
        return max(positive, key=lambda item: (item[1]["scored_count"], item[1]["avg_delta"]))
    if eligible:
        return max(eligible, key=lambda item: item[1]["avg_delta"])
    return None


def _threshold_at_rank(scores_desc: list[float], fraction: float) -> float:
    idx = max(0, min(len(scores_desc) - 1, math.ceil(len(scores_desc) * fraction) - 1))
    return scores_desc[idx]


def _framework_b_provisional_threshold_data(summaries: dict[str, dict]) -> dict | None:
    picked = _pick_framework_b_quality_rule(summaries)
    if picked is None:
        return None

    rule_name, summary = picked
    scores_desc = sorted((row["score_b"]["total_score"] for row in summary["scored"]), reverse=True)
    if not scores_desc:
        return None

    strong = _threshold_at_rank(scores_desc, 0.25)
    moderate = _threshold_at_rank(scores_desc, 0.50)
    light = _threshold_at_rank(scores_desc, 0.80)
    label_counts = {
        "strong": sum(1 for score in scores_desc if score >= strong),
        "moderate": sum(1 for score in scores_desc if moderate <= score < strong),
        "light": sum(1 for score in scores_desc if light <= score < moderate),
        "below": sum(1 for score in scores_desc if score < light),
    }
    median = scores_desc[len(scores_desc) // 2] if len(scores_desc) % 2 else (
        scores_desc[len(scores_desc) // 2 - 1] + scores_desc[len(scores_desc) // 2]
    ) / 2
    return {
        "rule_name": rule_name,
        "summary": summary,
        "scores_desc": scores_desc,
        "strong": strong,
        "moderate": moderate,
        "light": light,
        "label_counts": label_counts,
        "median": median,
    }


def _framework_b_provisional_label(score: float, threshold_data: dict) -> str:
    if score >= threshold_data["strong"]:
        return "strong"
    if score >= threshold_data["moderate"]:
        return "moderate"
    if score >= threshold_data["light"]:
        return "light"
    return "below"


def _latest_framework_a_outcome_row(db: sqlite3.Connection, code: str) -> tuple | None:
    return db.execute(
        """SELECT score_date, outcome_30d, total_score
           FROM predictions
           WHERE code=? AND framework='A'
           ORDER BY score_date DESC, id DESC
           LIMIT 1""",
        (code,),
    ).fetchone()


def _outcome_status(score_date: str | None, outcome_30d: float | None) -> str:
    if outcome_30d is not None:
        return "closed"
    if not score_date:
        return "no_a_record"
    try:
        due_date = date.fromisoformat(score_date) + timedelta(days=30)
    except ValueError:
        return "pending_unknown_date"
    if due_date > date.today():
        return "future_closeable"
    return "missing_after_due"


def _outcome_due_date(score_date: str | None) -> date | None:
    if not score_date:
        return None
    try:
        return date.fromisoformat(score_date) + timedelta(days=30)
    except ValueError:
        return None


def _fmt_due_delta(due_date: date) -> str:
    days = (due_date - date.today()).days
    if days > 0:
        return f"{due_date.isoformat()}（还需{days}天）"
    if days == 0:
        return f"{due_date.isoformat()}（今日到期）"
    return f"{due_date.isoformat()}（已逾期{abs(days)}天）"


def _append_framework_b_provisional_thresholds(lines: list[str], summaries: dict[str, dict]) -> dict | None:
    lines.append("B provisional thresholds（基于非金融质量候选，仅观察标签，不写入 weights）:")
    threshold_data = _framework_b_provisional_threshold_data(summaries)
    if threshold_data is None:
        lines.append("  暂无可用样本：不能提出 strong/moderate/light 草案。")
        lines.append("  结论：只能继续 report-only 积累，不能作为交易或生产门槛。")
        return None

    rule_name = threshold_data["rule_name"]
    summary = threshold_data["summary"]
    scores_desc = threshold_data["scores_desc"]
    strong = threshold_data["strong"]
    moderate = threshold_data["moderate"]
    light = threshold_data["light"]
    label_counts = threshold_data["label_counts"]
    median = threshold_data["median"]
    lines.append(
        f"  来源规则：{rule_name}（样本={summary['scored_count']}，B-A={summary['avg_delta']:+.2f}）"
    )
    lines.append(
        f"  B分数分布：min={min(scores_desc):.1f}, median={median:.1f}, max={max(scores_desc):.1f}"
    )
    lines.append(
        f"  草案阈值：strong≥{strong:.1f}, moderate≥{moderate:.1f}, light≥{light:.1f}"
    )
    lines.append(
        "  标签分布："
        f"strong={label_counts['strong']}, moderate={label_counts['moderate']}, "
        f"light={label_counts['light']}, below={label_counts['below']}"
    )
    if summary["scored_count"] < 10:
        lines.append("  样本不足：少于 10 个，只能作为观察标签，不能作为交易或生产门槛。")
    else:
        lines.append("  样本仍需 outcome 验证；当前阈值仅用于 report-only 观察。")
    return threshold_data


def _append_framework_b_label_outcome_tracking(
    lines: list[str],
    db: sqlite3.Connection,
    threshold_data: dict | None,
) -> dict:
    lines.append("B provisional label outcome tracking（基于非金融质量候选，只读 A 框 outcome 代理）:")
    if threshold_data is None:
        lines.append("  暂无 provisional label：不能建立分标签 outcome 追踪。")
        lines.append("  样本不足：禁止解释命中率/胜率，只允许检查数据流是否可追踪。")
        return {
            "sample_count": 0,
            "closed_count": 0,
            "earliest_due": None,
            "overdue_count": 0,
        }

    groups: dict[str, dict[str, Any]] = {
        label: {
            "rows": [],
            "industries": {},
            "closed": 0,
            "future_closeable": 0,
            "missing_after_due": 0,
            "no_a_record": 0,
            "pending_unknown_date": 0,
        }
        for label in ("strong", "moderate", "light", "below")
    }
    for row in threshold_data["summary"]["scored"]:
        label = _framework_b_provisional_label(row["score_b"]["total_score"], threshold_data)
        latest = _latest_framework_a_outcome_row(db, row["code"])
        score_date = latest[0] if latest else None
        outcome_30d = latest[1] if latest else None
        status = _outcome_status(score_date, outcome_30d)
        due_date = _outcome_due_date(score_date)
        item: dict[str, Any] = {
            "code": row["code"],
            "name": row["name"],
            "industry": row["industry"],
            "score_date": score_date,
            "due_date": due_date,
            "score_a": row["score_a"]["total_score"],
            "score_b": row["score_b"]["total_score"],
            "delta": row["delta"],
            "outcome_30d": outcome_30d,
            "status": status,
        }
        group = groups[label]
        group["rows"].append(item)
        group["industries"][row["industry"]] = group["industries"].get(row["industry"], 0) + 1
        if status in group:
            group[status] += 1

    total_closed = sum(group["closed"] for group in groups.values())
    overdue_risks: list[tuple[str, dict]] = []
    all_due_dates: list[date] = []
    lines.append("  说明：B 标签来自非金融质量候选 dry-run；结案状态只读取同代码最新 A 框记录，不写 predictions。")
    for label in ("strong", "moderate", "light", "below"):
        group = groups[label]
        rows = group["rows"]
        if not rows:
            lines.append(f"  {label}: 样本=0")
            continue
        due_dates = sorted(item["due_date"] for item in rows if item["due_date"] is not None)
        pending_due_dates = sorted(
            item["due_date"]
            for item in rows
            if item["due_date"] is not None and item["status"] in {"future_closeable", "missing_after_due"}
        )
        all_due_dates.extend(due_dates)
        earliest_due = _fmt_due_delta(due_dates[0]) if due_dates else "N/A"
        latest_due = _fmt_due_delta(due_dates[-1]) if due_dates else "N/A"
        next_due = _fmt_due_delta(pending_due_dates[0]) if pending_due_dates else "N/A"
        avg_a = sum(item["score_a"] for item in rows) / len(rows)
        avg_b = sum(item["score_b"] for item in rows) / len(rows)
        avg_delta = sum(item["delta"] for item in rows) / len(rows)
        industries = ", ".join(
            f"{industry}:{count}"
            for industry, count in sorted(group["industries"].items(), key=lambda item: (-item[1], item[0]))
        )
        lines.append(
            f"  {label}: 样本={len(rows)} 已结案30d={group['closed']} "
            f"未来可结案30d={group['future_closeable']} 到期缺失30d={group['missing_after_due']} "
            f"无A记录={group['no_a_record']} A均分={avg_a:.2f} B均分={avg_b:.2f} "
            f"B-A={avg_delta:+.2f} 行业={industries}"
        )
        lines.append(
            f"    30d可结案日期：最早={earliest_due} 最近={latest_due} 下一批预计={next_due}"
        )
        overdue_risks.extend((label, item) for item in rows if item["status"] == "missing_after_due")
    if overdue_risks:
        lines.append("  到期但 outcome 仍为空风险清单（最多 8 条）：")
        for label, item in sorted(overdue_risks, key=lambda row: (row[1]["due_date"] or date.max, row[1]["code"]))[:8]:
            due_text = _fmt_due_delta(item["due_date"]) if item["due_date"] else "N/A"
            lines.append(
                f"    - {label}: {item['name']}({item['code']}) "
                f"score_date={item['score_date']} due={due_text}"
            )
    else:
        lines.append("  到期但 outcome 仍为空风险清单：无")
    if total_closed < 20:
        lines.append(
            f"  样本不足：分标签已结案30d仅 {total_closed}/20，"
            "禁止解释命中率/胜率，只允许观察覆盖、分数分布和 outcome 数据流。"
        )
    else:
        lines.append("  分标签 outcome 可开始观察，但仍不得作为交易或生产门槛。")
    return {
        "sample_count": sum(len(group["rows"]) for group in groups.values()),
        "closed_count": total_closed,
        "earliest_due": min(all_due_dates).isoformat() if all_due_dates else None,
        "overdue_count": len(overdue_risks),
    }


def append_framework_b_quality_expansion(lines: list[str], db: sqlite3.Connection, weights: dict) -> dict[str, int]:
    lines.append("")
    lines.append("── Framework B 非金融质量候选与 B label 研究（report-only）──")
    lines.append("口径：非金融质量规则；本节的 provisional thresholds 与 label outcome 均基于选中的质量候选规则。")
    if "B" not in weights.get("frameworks", {}):
        lines.append("weights.json 未配置 Framework B，跳过扩展候选。")
        return {"candidate_count": 0, "scored_count": 0, "skipped_count": 0}

    summaries: dict[str, dict] = {}
    for rule_name, rule in FRAMEWORK_B_QUALITY_RULES.items():
        summary = _collect_framework_b_quality_candidates(db, weights, rule)
        summaries[rule_name] = summary
        lines.append(f"{rule_name} 规则：{_framework_b_quality_rule_text(rule)}")
        lines.append(
            f"  候选={summary['candidate_count']} "
            f"可评分={summary['scored_count']} "
            f"跳过={summary['skipped_count']}"
        )
        if summary["scored_count"] == 0:
            lines.append("  扩展候选为空：当前 watchlist 没有同时满足该组约束的非金融标的。")
            continue
        lines.append(
            f"  A均分={summary['avg_a']:.2f} "
            f"B均分={summary['avg_b']:.2f} "
            f"B-A={summary['avg_delta']:+.2f}"
        )
        lines.append(
            "  行业覆盖："
            + ", ".join(
                f"{industry}:{count}"
                for industry, count in sorted(summary["industry_counts"].items(), key=lambda row: (-row[1], row[0]))
            )
        )
        lines.append("  Top delta：")
        for row in sorted(summary["scored"], key=lambda item: item["delta"], reverse=True)[:3]:
            lines.append(
                f"    - {row['name']}({row['code']}) [{row['industry']}] "
                f"B={row['score_b']['total_score']:.1f} A={row['score_a']['total_score']:.1f} Δ={row['delta']:+.1f}"
            )
        lines.append("  Bottom delta：")
        for row in sorted(summary["scored"], key=lambda item: item["delta"])[:3]:
            lines.append(
                f"    - {row['name']}({row['code']}) [{row['industry']}] "
                f"B={row['score_b']['total_score']:.1f} A={row['score_a']['total_score']:.1f} Δ={row['delta']:+.1f}"
            )

    base_summary = summaries.get("base", {"scored": [], "industry_counts": {}, "candidate_count": 0, "scored_count": 0, "skipped_count": 0})
    if base_summary["scored"]:
        lines.append("base 扩展候选明细：")
        for row in sorted(base_summary["scored"], key=lambda item: item["score_b"]["total_score"], reverse=True)[:10]:
            lines.append(
                f"  - {row['name']}({row['code']}) [{row['industry']}] "
                f"B={row['score_b']['total_score']:.1f} A={row['score_a']['total_score']:.1f} "
                f"Δ={row['delta']:+.1f} | {row['quality_reason']}"
            )
        _append_framework_b_deep_dive(
            lines,
            base_summary["scored"],
            base_summary["industry_counts"],
            len(config.WATCHLIST),
        )

    lines.append(_framework_b_quality_recommendation(summaries))
    threshold_data = _append_framework_b_provisional_thresholds(lines, summaries)
    b_label_summary = _append_framework_b_label_outcome_tracking(lines, db, threshold_data)
    if any(summary["skipped"] for summary in summaries.values()):
        lines.append("跳过原因（最多 8 条）：")
        emitted = 0
        for rule_name, summary in summaries.items():
            for row in summary["skipped"]:
                lines.append(f"  - {rule_name}: {row}")
                emitted += 1
                if emitted >= 8:
                    break
            if emitted >= 8:
                break
    return {
        "candidate_count": max((summary["candidate_count"] for summary in summaries.values()), default=0),
        "scored_count": max((summary["scored_count"] for summary in summaries.values()), default=0),
        "skipped_count": sum(summary["skipped_count"] for summary in summaries.values()),
        "b_label_sample_count": b_label_summary["sample_count"],
        "b_label_closed_count": b_label_summary["closed_count"],
        "b_label_earliest_due": b_label_summary["earliest_due"],
        "b_label_overdue_count": b_label_summary["overdue_count"],
    }


def append_phase6_readiness(
    lines: list[str],
    db: sqlite3.Connection,
    data_quality_summary: dict[str, int],
    framework_b_summary: dict[str, int],
) -> None:
    post_fix_closed = db.execute(
        """SELECT COUNT(*) FROM predictions
           WHERE framework='A' AND score_date >= ? AND outcome_30d IS NOT NULL""",
        (POST_FIX_DATE,),
    ).fetchone()[0]
    data_ready = (
        data_quality_summary["missing_cache"] == 0
        and data_quality_summary["acceptable"] == data_quality_summary["audited"]
        and data_quality_summary["cache_report_period_missing"] == 0
    )
    b_ready = (
        framework_b_summary["candidate_count"] > 0
        and framework_b_summary["candidate_count"] == framework_b_summary["scored_count"]
    )
    b_label_sample_count = framework_b_summary.get("b_label_sample_count", 0)
    b_label_closed_count = framework_b_summary.get("b_label_closed_count", 0)
    b_label_earliest_due = framework_b_summary.get("b_label_earliest_due")
    b_label_overdue_count = framework_b_summary.get("b_label_overdue_count", 0)
    b_label_ready = b_label_closed_count >= 20 and b_label_overdue_count == 0
    outcome_ready = post_fix_closed >= 100

    lines.append("")
    lines.append("── Phase 6 readiness ──")
    lines.append(f"post-fix A框 30d 结案 ≥ 100：{post_fix_closed}/100 {'OK' if outcome_ready else 'WAIT'}")
    lines.append(
        "数据质量门槛："
        f"{'OK' if data_ready else 'WAIT'}"
        f"（cache缺失={data_quality_summary['missing_cache']}, "
        f"required可接受={data_quality_summary['acceptable']}/{data_quality_summary['audited']}, "
        f"cache_report_period缺失={data_quality_summary['cache_report_period_missing']}）"
    )
    lines.append(
        "Framework B 金融候选 dry-run 覆盖："
        f"{'OK' if b_ready else 'WAIT'}"
        f"（可评分={framework_b_summary['scored_count']}/{framework_b_summary['candidate_count']}）"
    )
    earliest_due_text = b_label_earliest_due or "N/A"
    lines.append(
        "B label outcome 自然结案（非金融质量候选）："
        f"{'OK' if b_label_ready else 'WAIT'}"
        f"（已结案={b_label_closed_count}/20, 候选={b_label_sample_count}, "
        f"最早可评估={earliest_due_text}, overdue风险={b_label_overdue_count}）"
    )
    if b_label_closed_count < 20:
        lines.append(
            "B label 阈值/命中率解释：禁止"
            f"（样本未结案，需等待自然结案；最早可评估={earliest_due_text}）"
        )
    elif b_label_overdue_count:
        lines.append(
            "B label 阈值/命中率解释：禁止"
            f"（存在 {b_label_overdue_count} 条到期但 outcome 为空风险）"
        )
    else:
        lines.append("B label 阈值/命中率解释：可开始审阅，但仍不得作为交易或生产门槛。")
    report_only_ready = data_ready and b_ready
    lines.append(
        "Framework B report-only 深化："
        f"{'OK（可继续报告研究，不写 predictions）' if report_only_ready else 'WAIT（先补齐数据质量或 dry-run 覆盖）'}"
    )

    blockers: list[str] = []
    if not outcome_ready:
        blockers.append(f"post-fix A框 30d 结案不足（{post_fix_closed}/100）")
    if not data_ready:
        blockers.append("数据质量门槛未满足")
    if not b_ready:
        blockers.append(
            f"Framework B 金融候选 dry-run 未全覆盖（{framework_b_summary['scored_count']}/"
            f"{framework_b_summary['candidate_count']}）"
        )
    if not b_label_ready:
        if b_label_closed_count < 20:
            blockers.append(f"B label 已结案样本不足（{b_label_closed_count}/20）")
        if b_label_overdue_count:
            blockers.append(f"B label 存在到期缺失 outcome 风险（{b_label_overdue_count}）")
    lines.append("Phase 6 生产化阻塞项：" + ("无" if not blockers else "；".join(blockers)))

    next_actions: list[str] = []
    if not outcome_ready:
        next_actions.append("继续 daily/outcome-update，等待 post-fix A 框自然结案")
    if not data_ready:
        next_actions.append("先补齐 watchlist 基本面缓存与 required 字段")
    if not b_ready:
        next_actions.append("修复 B 金融候选 dry-run 跳过原因")
    if b_label_closed_count < 20:
        next_actions.append(f"等待 B label 自然结案至 20 条（最早可评估={earliest_due_text}）")
    if b_label_overdue_count:
        next_actions.append("优先处理到期但 outcome 为空的 B label 样本")
    if not next_actions:
        next_actions.append("保持 report-only 审阅，不启用生产写入")
    lines.append("Phase 6 下一步：" + "；".join(next_actions))

    if outcome_ready and data_ready and b_ready:
        lines.append("结论：可以进入 Phase 6 report-only 深化；仍不要启用生产写入。")
    elif report_only_ready:
        lines.append(
            "结论：可以继续 Framework B report-only 深化；暂不进入 Phase 6 生产化，"
            "等待 post-fix outcome 与 B label 自然结案。"
        )
    else:
        lines.append("结论：暂不进入 Phase 6 生产化；优先修复数据质量并等待 post-fix outcome。")
