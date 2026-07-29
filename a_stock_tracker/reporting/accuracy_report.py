"""Compact QFQ strategy evaluation for the default report."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
import sqlite3
from statistics import fmean

WINDOWS = (30, 60, 90)
STOCK_SOURCE = "tushare.pro_bar.qfq"
STOCK_ADJUSTED = "qfq"
BENCHMARK_SYMBOL = "H00300"
MAX_LAG_DAYS = 10
MIN_CROSS_SECTION = 5
MIN_CROSS_SECTION_COVERAGE = 0.9

# The v2 production consumer and the first qualitative_scores_v2 rows were introduced on this
# date. Earlier predictions were written by the legacy v1-only path, before the per-prediction
# qualitative_mode snapshot existed; rows from this date onward require that snapshot.
QUALITATIVE_V1_ONLY_CUTOFF = "2026-07-19"
PROVENANCE_CONFIRMED_CLAUSE = "(score_date < ? OR qualitative_mode IS NOT NULL)"


@dataclass(frozen=True)
class StrategyObservation:
    score_date: str
    code: str
    total_score: float
    l3_v2_signal: int | None
    stock_return: float
    benchmark_return: float
    alpha: float


@dataclass(frozen=True)
class StrategyPath:
    batches: int
    q5_return: float | None
    equal_weight_return: float | None
    benchmark_return: float | None
    q5_minus_equal_weight: float | None
    max_drawdown: float | None


def _fmt_percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}%"


def _fmt_number(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def _load_price_series(
    db: sqlite3.Connection,
) -> tuple[dict[str, tuple[list[str], list[float]]], tuple[list[str], list[float]]]:
    stock_rows = db.execute(
        """SELECT code, trade_date, close
           FROM daily_bars
           WHERE adjusted=? AND source=? AND close IS NOT NULL
           ORDER BY code, trade_date""",
        (STOCK_ADJUSTED, STOCK_SOURCE),
    ).fetchall()
    grouped: dict[str, tuple[list[str], list[float]]] = {}
    dates_by_code: dict[str, list[str]] = defaultdict(list)
    closes_by_code: dict[str, list[float]] = defaultdict(list)
    for code, trade_date, close in stock_rows:
        dates_by_code[str(code)].append(str(trade_date))
        closes_by_code[str(code)].append(float(close))
    for code in dates_by_code:
        grouped[code] = (dates_by_code[code], closes_by_code[code])

    benchmark_rows = db.execute(
        "SELECT date, close FROM index_prices WHERE symbol=? ORDER BY date",
        (BENCHMARK_SYMBOL,),
    ).fetchall()
    benchmark = (
        [str(row[0]) for row in benchmark_rows],
        [float(row[1]) for row in benchmark_rows],
    )
    return grouped, benchmark


def _aligned_close(
    series: tuple[list[str], list[float]],
    anchor: date,
) -> tuple[str, float] | None:
    dates, closes = series
    index = bisect_right(dates, anchor.isoformat()) - 1
    if index < 0:
        return None
    actual = date.fromisoformat(dates[index])
    if (anchor - actual).days > MAX_LAG_DAYS:
        return None
    return dates[index], closes[index]


def _current_scoring_version(db: sqlite3.Connection) -> tuple[str | None, str | None, str | None]:
    row = db.execute(
        f"""SELECT weights_hash
           FROM predictions
           WHERE framework='A'
             AND total_score IS NOT NULL
             AND weights_hash IS NOT NULL
             AND {PROVENANCE_CONFIRMED_CLAUSE}
             AND score_date = (
                 SELECT MAX(score_date)
                 FROM predictions
                 WHERE framework='A'
                   AND total_score IS NOT NULL
                   AND weights_hash IS NOT NULL
                   AND {PROVENANCE_CONFIRMED_CLAUSE}
             )
           GROUP BY weights_hash
           ORDER BY COUNT(*) DESC, weights_hash
           LIMIT 1""",
        (QUALITATIVE_V1_ONLY_CUTOFF, QUALITATIVE_V1_ONLY_CUTOFF),
    ).fetchone()
    if row is None:
        return None, None, None
    weights_hash = str(row[0])
    first_date, last_date = db.execute(
        f"""SELECT MIN(score_date), MAX(score_date)
           FROM predictions
           WHERE framework='A'
             AND total_score IS NOT NULL
             AND weights_hash=?
             AND {PROVENANCE_CONFIRMED_CLAUSE}""",
        (weights_hash, QUALITATIVE_V1_ONLY_CUTOFF),
    ).fetchone()
    return weights_hash, str(first_date), str(last_date)


def _load_observations(
    db: sqlite3.Connection,
    window_days: int,
    weights_hash: str | None,
) -> tuple[list[StrategyObservation], int]:
    if weights_hash is None:
        return [], 0
    expected_size = db.execute(
        f"""SELECT COALESCE(MAX(prediction_count), 0)
           FROM (
               SELECT COUNT(*) AS prediction_count
               FROM predictions
               WHERE framework='A'
                 AND total_score IS NOT NULL
                 AND weights_hash=?
                 AND {PROVENANCE_CONFIRMED_CLAUSE}
               GROUP BY score_date
           )""",
        (weights_hash, QUALITATIVE_V1_ONLY_CUTOFF),
    ).fetchone()[0]
    stocks, benchmark = _load_price_series(db)
    if not benchmark[0]:
        return [], int(expected_size)
    as_of_date = date.fromisoformat(benchmark[0][-1])
    predictions = db.execute(
        f"""SELECT code, score_date, total_score, l3_v2_signal
           FROM predictions
           WHERE framework='A'
             AND total_score IS NOT NULL
             AND weights_hash=?
             AND {PROVENANCE_CONFIRMED_CLAUSE}
           ORDER BY score_date, code""",
        (weights_hash, QUALITATIVE_V1_ONLY_CUTOFF),
    ).fetchall()
    observations: list[StrategyObservation] = []
    for raw_code, raw_score_date, raw_score, raw_signal in predictions:
        code = str(raw_code)
        if code not in stocks:
            continue
        score_date = date.fromisoformat(str(raw_score_date))
        target_date = score_date + timedelta(days=window_days)
        if target_date > as_of_date:
            continue
        stock_entry = _aligned_close(stocks[code], score_date)
        stock_target = _aligned_close(stocks[code], target_date)
        benchmark_entry = _aligned_close(benchmark, score_date)
        benchmark_target = _aligned_close(benchmark, target_date)
        if None in {stock_entry, stock_target, benchmark_entry, benchmark_target}:
            continue
        assert stock_entry is not None
        assert stock_target is not None
        assert benchmark_entry is not None
        assert benchmark_target is not None
        if stock_entry[0] != benchmark_entry[0] or stock_target[0] != benchmark_target[0]:
            continue
        stock_return = (stock_target[1] / stock_entry[1] - 1.0) * 100.0
        benchmark_return = (benchmark_target[1] / benchmark_entry[1] - 1.0) * 100.0
        observations.append(
            StrategyObservation(
                score_date=score_date.isoformat(),
                code=code,
                total_score=float(raw_score),
                l3_v2_signal=int(raw_signal) if raw_signal in (0, 1) else None,
                stock_return=stock_return,
                benchmark_return=benchmark_return,
                alpha=stock_return - benchmark_return,
            )
        )
    return observations, int(expected_size)


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        rank = (start + 1 + end) / 2.0
        for index in ordered[start:end]:
            ranks[index] = rank
        start = end
    return ranks


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = fmean(left)
    right_mean = fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True))
    left_scale = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_scale = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / (left_scale * right_scale)


def _non_overlapping_sections(
    observations: list[StrategyObservation],
    window_days: int,
    expected_size: int,
) -> list[list[StrategyObservation]]:
    grouped: dict[str, list[StrategyObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.score_date].append(observation)
    sections: list[list[StrategyObservation]] = []
    required_size = max(MIN_CROSS_SECTION, math.ceil(expected_size * MIN_CROSS_SECTION_COVERAGE))
    next_eligible = date.min
    for raw_score_date in sorted(grouped):
        score_date = date.fromisoformat(raw_score_date)
        rows = grouped[raw_score_date]
        if score_date < next_eligible or len(rows) < required_size:
            continue
        sections.append(rows)
        next_eligible = score_date + timedelta(days=window_days)
    return sections


def _cross_section_metrics(
    sections: list[list[StrategyObservation]],
) -> tuple[int, float | None, float | None]:
    information_coefficients: list[float] = []
    spreads: list[float] = []
    for rows in sections:
        information_coefficient = _correlation(
            _average_ranks([row.total_score for row in rows]),
            _average_ranks([row.alpha for row in rows]),
        )
        if information_coefficient is not None:
            information_coefficients.append(information_coefficient)
        ordered = sorted(rows, key=lambda row: row.total_score)
        bucket_size = max(1, len(ordered) // 5)
        spreads.append(
            fmean(row.alpha for row in ordered[-bucket_size:]) - fmean(row.alpha for row in ordered[:bucket_size])
        )
    return (
        len(information_coefficients),
        fmean(information_coefficients) if information_coefficients else None,
        fmean(spreads) if spreads else None,
    )


def _strategy_path(
    sections: list[list[StrategyObservation]],
) -> StrategyPath:
    q5_equity = 1.0
    equal_weight_equity = 1.0
    benchmark_equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for rows in sections:
        ordered = sorted(rows, key=lambda row: row.total_score)
        bucket_size = max(1, len(ordered) // 5)
        top = ordered[-bucket_size:]
        q5_return = fmean(row.stock_return for row in top)
        equal_weight_return = fmean(row.stock_return for row in rows)
        benchmark_return = fmean(row.benchmark_return for row in top)
        q5_equity *= 1.0 + q5_return / 100.0
        equal_weight_equity *= 1.0 + equal_weight_return / 100.0
        benchmark_equity *= 1.0 + benchmark_return / 100.0
        peak = max(peak, q5_equity)
        max_drawdown = min(max_drawdown, q5_equity / peak - 1.0)
    if not sections:
        return StrategyPath(0, None, None, None, None, None)
    q5_total_return = (q5_equity - 1.0) * 100.0
    equal_weight_total_return = (equal_weight_equity - 1.0) * 100.0
    return StrategyPath(
        batches=len(sections),
        q5_return=q5_total_return,
        equal_weight_return=equal_weight_total_return,
        benchmark_return=(benchmark_equity - 1.0) * 100.0,
        q5_minus_equal_weight=q5_total_return - equal_weight_total_return,
        max_drawdown=max_drawdown * 100.0,
    )


def _l3_summary(sections: list[list[StrategyObservation]], strong_threshold: float) -> str:
    rows = [row for section in sections for row in section if row.total_score >= strong_threshold]
    passed = [row.alpha for row in rows if row.l3_v2_signal == 1]
    rejected = [row.alpha for row in rows if row.l3_v2_signal == 0]
    if not passed and not rejected:
        return f"L3 v2（高分候选>={strong_threshold:.0f}）：暂无门禁结果已记录的对齐样本"
    if not rejected:
        return f"L3 v2（高分候选>={strong_threshold:.0f}）：通过 n={len(passed)}；拒绝 n=0，暂无选择能力样本"
    return (
        f"L3 v2（高分候选>={strong_threshold:.0f}）："
        f"通过 n={len(passed)} 平均alpha={_fmt_percent(fmean(passed) if passed else None)}；"
        f"拒绝 n={len(rejected)} 平均alpha={_fmt_percent(fmean(rejected) if rejected else None)}"
    )


def build_accuracy_report(db: sqlite3.Connection, strong_threshold: float = 44.0) -> str:
    weights_hash, first_date, last_date = _current_scoring_version(db)
    lines = [
        "=" * 60,
        "a-stock-tracker QFQ 策略评估报告",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 60,
        "口径：个股前复权总收益 vs 沪深300全收益指数；仅统计交易日对齐样本。",
        (
            f"当前评分版本：weights_hash={weights_hash}；"
            f"定性 provenance 口径：{QUALITATIVE_V1_ONLY_CUTOFF} 前按 v2 上线前历史确认为 v1，"
            f"自该日起要求预测快照已记录；"
            f"记录区间：{first_date} 至 {last_date}。"
            if weights_hash is not None
            else "当前评分版本：暂无 Framework A 记录。"
        ),
        "去重：各窗口按 score_date 选取非重叠截面；IC、spread、组合收益和 L3 使用相同批次。",
        "限制：watchlist 为人工维护标的，结果不代表样本外泛化能力。",
    ]
    for window_days in WINDOWS:
        observations, expected_size = _load_observations(db, window_days, weights_hash)
        lines.extend(["", f"── {window_days}d ──"])
        if not observations:
            lines.append("暂无可用的 QFQ/沪深300全收益对齐样本。")
            continue
        required_size = max(MIN_CROSS_SECTION, math.ceil(expected_size * MIN_CROSS_SECTION_COVERAGE))
        sections = _non_overlapping_sections(observations, window_days, expected_size)
        cross_sections, information_coefficient, spread = _cross_section_metrics(sections)
        path = _strategy_path(sections)
        lines.extend(
            [
                (
                    f"对齐样本：{len(observations)}；非重叠样本：{sum(len(rows) for rows in sections)}；"
                    f"非重叠截面：{path.batches}；IC有效截面：{cross_sections}"
                ),
                f"截面完整性门槛：至少 {required_size}/{expected_size} 只",
                f"截面 Spearman IC 均值：{_fmt_number(information_coefficient)}",
                f"Q5−Q1 平均 alpha spread：{_fmt_percent(spread)}",
                f"Q5 策略累计总收益：{_fmt_percent(path.q5_return)}",
                f"观察池等权累计总收益：{_fmt_percent(path.equal_weight_return)}",
                f"Q5−观察池等权累计收益差：{_fmt_percent(path.q5_minus_equal_weight)}",
                f"沪深300全收益：{_fmt_percent(path.benchmark_return)}",
                f"Q5 策略最大回撤：{_fmt_percent(path.max_drawdown)}",
                _l3_summary(sections, strong_threshold),
            ]
        )
    return "\n".join(lines)
