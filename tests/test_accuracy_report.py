from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from a_stock_tracker.reporting.accuracy_report import build_accuracy_report


def _strategy_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE predictions (
            code TEXT NOT NULL,
            framework TEXT NOT NULL,
            score_date TEXT NOT NULL,
            total_score REAL,
            l3_v2_signal INTEGER,
            weights_hash TEXT,
            qualitative_mode TEXT
        );
        CREATE TABLE daily_bars (
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            close REAL,
            source TEXT NOT NULL,
            adjusted TEXT NOT NULL,
            volume REAL DEFAULT 1000000,
            volume_unit TEXT DEFAULT 'hand',
            fetched_at TEXT DEFAULT '2026-01-01T00:00:00',
            quality_status TEXT DEFAULT 'ok'
        );
        CREATE TABLE index_prices (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            close REAL NOT NULL
        );
        """
    )
    dates = ("2026-01-05", "2026-02-04", "2026-03-06", "2026-04-03")
    benchmark_closes = (100.0, 105.0, 110.0, 115.0)
    db.executemany(
        "INSERT INTO index_prices(symbol,date,close) VALUES ('H00300',?,?)",
        zip(dates, benchmark_closes, strict=True),
    )
    for index in range(5):
        code = f"60000{index}"
        db.execute(
            "INSERT INTO predictions VALUES (?, 'A', '2026-01-05', ?, ?, 'current', 'v1')",
            (code, float(index + 1), 1 if index >= 3 else 0),
        )
        returns = (0.0, float(index * 10 - 10), float(index * 12 - 10), float(index * 14 - 10))
        closes = tuple(100.0 * (1.0 + value / 100.0) for value in returns)
        db.executemany(
            """INSERT INTO daily_bars(code,trade_date,close,source,adjusted)
               VALUES (?,?,?,'tushare.pro_bar.qfq','qfq')""",
            ((code, trade_date, close) for trade_date, close in zip(dates, closes, strict=True)),
        )
    return db


def _insert_history(
    db: sqlite3.Connection,
    code: str,
    closes: list[float],
    *,
    adjusted: str,
    end_date: date = date(2026, 1, 4),
) -> None:
    start_date = end_date - timedelta(days=len(closes) - 1)
    source = "tushare.pro_bar.qfq" if adjusted == "qfq" else "tushare.daily"
    db.executemany(
        """INSERT INTO daily_bars(code,trade_date,close,source,adjusted)
           VALUES (?,?,?,?,?)""",
        (
            (code, (start_date + timedelta(days=offset)).isoformat(), close, source, adjusted)
            for offset, close in enumerate(closes)
        ),
    )


def test_qfq_report_computes_core_strategy_metrics() -> None:
    db = _strategy_db()
    report = build_accuracy_report(db, strong_threshold=4.0)

    assert "当前评分版本：weights_hash=current；" in report
    assert "对齐样本：5；非重叠样本：5；非重叠截面：1；IC有效截面：1" in report
    assert "截面 Spearman IC 均值：1.000" in report
    assert "Q5−Q1 平均 alpha spread：40.00%" in report
    assert "Q5 研究累计收益：30.00%" in report
    assert "观察池等权累计总收益：10.00%" in report
    assert "Q5−观察池等权累计收益差：20.00%" in report
    assert "沪深300全收益：5.00%" in report
    assert "Q5 批次端点回撤（旧口径，非最大回撤）：0.00%" in report
    assert "Q5 日收盘最大回撤：0.00%" in report
    assert "盘后信号无法按该价格成交" in report
    assert "不是可执行策略或账户净值" in report
    assert "样本门槛：INSUFFICIENT_EVIDENCE" in report
    assert "Q5 策略最大回撤" not in report
    assert "L3 v2 极端风险提示（高分候选>=4）：正常 n=2；触发 n=0，暂无选择能力样本" in report


def test_daily_drawdown_captures_intra_window_loss_and_rejects_missing_marks() -> None:
    db = _strategy_db()
    for day, close in (("2026-01-12", 150.0), ("2026-01-19", 75.0)):
        db.execute("INSERT INTO index_prices VALUES ('H00300',?,100)", (day,))
        for index in range(5):
            db.execute(
                "INSERT INTO daily_bars(code,trade_date,close,source,adjusted) VALUES (?,?,?,'tushare.pro_bar.qfq','qfq')",
                (f"60000{index}", day, close),
            )
    report = build_accuracy_report(db)
    assert "Q5 批次端点回撤（旧口径，非最大回撤）：0.00%" in report
    assert "Q5 日收盘最大回撤：-50.00%" in report
    db.execute("DELETE FROM daily_bars WHERE code='600004' AND trade_date='2026-01-19'")
    assert "Q5 日收盘最大回撤：N/A" in build_accuracy_report(db)


def test_daily_drawdown_preserves_peak_across_batches_and_holds_cash_in_gaps() -> None:
    from a_stock_tracker.reporting.accuracy_report import StrategyObservation, _daily_max_drawdown

    dates = ["2026-01-01", "2026-01-15", "2026-01-31", "2026-02-10", "2026-03-01", "2026-03-15", "2026-03-31"]
    stocks = {"600001": (dates, [100.0, 150.0, 120.0, 1.0, 60.0, 90.0, 45.0])}
    sections = [
        [StrategyObservation(day, "600001", 80.0, 1, ret, 0.0, ret)]
        for day, ret in (("2026-01-01", 20.0), ("2026-03-01", -25.0))
    ]
    assert _daily_max_drawdown(sections, 30, stocks, (dates, [100.0] * len(dates))) == -50.0


def test_new_input_hash_does_not_inherit_mature_legacy_evidence() -> None:
    from a_stock_tracker.reporting.accuracy_report import _current_scoring_version

    db = _strategy_db()
    db.execute("UPDATE predictions SET weights_hash='8aea81ed'")
    for index in range(5):
        db.execute(
            "INSERT INTO predictions VALUES (?, 'A', '2026-08-01', ?, 1, 'new-input-hash', 'v1')",
            (f"60000{index}", float(index)),
        )
    assert _current_scoring_version(db) == ("new-input-hash", ("new-input-hash",), "2026-08-01", "2026-08-01")
    report = build_accuracy_report(db)
    assert report.count("暂无可用的 QFQ/沪深300全收益对齐样本") == 3
    assert "记录区间：2026-08-01 至 2026-08-01" in report
    assert "不与历史权重hash合并" in report


def test_qfq_report_excludes_wrong_source_and_framework() -> None:
    db = _strategy_db()
    db.execute("UPDATE daily_bars SET source='other' WHERE code='600004'")
    db.execute("UPDATE predictions SET framework='B' WHERE code='600003'")

    report = build_accuracy_report(db)

    assert report.count("对齐样本：3；非重叠样本：0；非重叠截面：0；IC有效截面：0") == 2
    assert report.count("暂无可用的 QFQ/沪深300全收益对齐样本") == 1


def test_qfq_report_uses_latest_weights_hash_only() -> None:
    db = _strategy_db()
    db.execute("INSERT INTO predictions VALUES ('600099', 'A', '2026-01-05', 99, 1, 'retired', 'v1')")
    db.executemany(
        """INSERT INTO daily_bars(code,trade_date,close,source,adjusted)
           VALUES ('600099',?,?,'tushare.pro_bar.qfq','qfq')""",
        (
            ("2026-01-05", 100.0),
            ("2026-02-04", 200.0),
            ("2026-03-06", 300.0),
            ("2026-04-03", 400.0),
        ),
    )

    report = build_accuracy_report(db)

    assert "当前评分版本：weights_hash=current；" in report
    assert "对齐样本：5；" in report
    assert "100.00%" not in report


def test_qfq_report_combines_known_note_only_hashes() -> None:
    db = _strategy_db()
    db.execute("UPDATE predictions SET weights_hash='8aea81ed'")
    for index in range(5):
        db.execute(
            "INSERT INTO predictions VALUES (?, 'A', '2026-01-06', ?, 1, '832893a3', 'v1')",
            (f"60000{index}", float(index + 1)),
        )

    report = build_accuracy_report(db)

    assert "当前评分版本：weights_hash=8181a13c" in report
    assert "兼容历史hash=8aea81ed,832893a3" in report
    assert "记录区间：2026-01-05 至 2026-01-06" in report


def test_qfq_report_deduplicates_overlapping_score_dates() -> None:
    db = _strategy_db()
    extra_dates = ("2026-01-06", "2026-02-05", "2026-03-07", "2026-04-04")
    db.executemany(
        "INSERT INTO index_prices(symbol,date,close) VALUES ('H00300',?,?)",
        zip(extra_dates, (100.0, 105.0, 110.0, 115.0), strict=True),
    )
    for index in range(5):
        code = f"60000{index}"
        db.execute(
            "INSERT INTO predictions VALUES (?, 'A', '2026-01-06', ?, ?, 'current', 'v1')",
            (code, float(5 - index), 1 if index >= 3 else 0),
        )
        returns = (0.0, float(30 - index * 10), float(40 - index * 10), float(50 - index * 10))
        closes = tuple(100.0 * (1.0 + value / 100.0) for value in returns)
        db.executemany(
            """INSERT INTO daily_bars(code,trade_date,close,source,adjusted)
               VALUES (?,?,?,'tushare.pro_bar.qfq','qfq')""",
            ((code, trade_date, close) for trade_date, close in zip(extra_dates, closes, strict=True)),
        )

    report = build_accuracy_report(db)

    assert "对齐样本：10；非重叠样本：5；非重叠截面：1；IC有效截面：1" in report
    assert "采用截面：2026-01-05" in report
    assert "截面 Spearman IC 均值：1.000" in report


def test_qfq_report_excludes_rows_without_qualitative_provenance() -> None:
    """After QUALITATIVE_V1_ONLY_CUTOFF, a NULL qualitative_mode is ambiguous (the
    prediction may have used qualitative_scores_v2) and must stay excluded."""
    db = _strategy_db()
    for index in range(5):
        db.execute(
            "INSERT INTO predictions VALUES (?, 'A', '2026-07-20', ?, 1, 'current', NULL)",
            (f"60000{index}", float(index + 1)),
        )

    report = build_accuracy_report(db)

    assert "记录区间：2026-01-05 至 2026-01-05" in report


def test_qfq_report_includes_rows_before_qualitative_v1_only_cutoff_without_provenance() -> None:
    """Rows written by the pre-v2 production path are usable without the later snapshot."""
    db = _strategy_db()
    db.execute("UPDATE predictions SET qualitative_mode=NULL")

    report = build_accuracy_report(db)

    assert "记录区间：2026-01-05 至 2026-01-05" in report
    assert "对齐样本：5；非重叠样本：5；非重叠截面：1；IC有效截面：1" in report


def test_qfq_report_excludes_cutoff_date_without_provenance_from_version_selection() -> None:
    """The cutoff date itself is v2-capable, so a newer ambiguous hash cannot become current."""
    db = _strategy_db()
    for index in range(5):
        db.execute(
            "INSERT INTO predictions VALUES (?, 'A', '2026-07-19', ?, 1, 'ambiguous', NULL)",
            (f"60000{index}", float(index + 1)),
        )

    report = build_accuracy_report(db)

    assert "当前评分版本：weights_hash=current；" in report
    assert "记录区间：2026-01-05 至 2026-01-05" in report
    assert "weights_hash=ambiguous" not in report


def test_qfq_report_rejects_incomplete_cross_section() -> None:
    db = _strategy_db()
    db.execute("UPDATE daily_bars SET source='other' WHERE code='600004'")

    report = build_accuracy_report(db)

    assert "对齐样本：4；非重叠样本：0；非重叠截面：0；IC有效截面：0" in report
    assert "截面完整性门槛：至少 5/5 只" in report


def test_qfq_report_compares_l3_only_within_strong_candidates() -> None:
    db = _strategy_db()
    db.execute("UPDATE predictions SET l3_v2_signal=0 WHERE total_score=4")

    report = build_accuracy_report(db, strong_threshold=4.0)

    assert "L3 v2 极端风险提示（高分候选>=4）：正常 n=1" in report
    assert "触发 n=1" in report


def test_qfq_report_distinguishes_missing_l3_results_from_zero_rejects() -> None:
    """With <120 daily_bars rows, reconstruction itself is INSUFFICIENT_WINDOW, so the
    report must say so instead of implying zero rejects."""
    db = _strategy_db()
    db.execute("UPDATE predictions SET l3_v2_signal=NULL")

    report = build_accuracy_report(db, strong_threshold=4.0)

    assert (
        "L3 v2 极端风险提示（高分候选>=4）：暂无提示结果已记录或可回溯计算的对齐样本"
        "（含回溯计算 QFQ强通过0/未复权弱通过0/拒绝0/不可计算2）"
    ) in report
    assert "拒绝 n=0" not in report


def test_qfq_report_reconstructs_l3_v2_signal_when_not_recorded() -> None:
    """A resolvable NULL and an unresolvable NULL can coexist in one report section."""
    db = _strategy_db()
    db.execute("UPDATE predictions SET l3_v2_signal=NULL WHERE total_score>=4")
    _insert_history(db, "600004", [50.0] * 120, adjusted="qfq")

    report = build_accuracy_report(db, strong_threshold=4.0)

    assert "正常 n=1；触发 n=0" in report
    assert "含回溯计算 QFQ强通过1/未复权弱通过0/拒绝0/不可计算1" in report


def test_qfq_report_reconstructs_l3_v2_reject_when_not_recorded() -> None:
    db = _strategy_db()
    db.execute("UPDATE predictions SET l3_v2_signal=NULL WHERE code='600004'")
    _insert_history(db, "600004", [100.0] * 119 + [50.0], adjusted="qfq")

    report = build_accuracy_report(db, strong_threshold=4.0)

    assert "正常 n=1 平均alpha=" in report
    assert "；触发 n=1 平均alpha=" in report
    assert "含回溯计算 QFQ强通过0/未复权弱通过0/拒绝1/不可计算0" in report


def test_qfq_report_discloses_unadjusted_reconstruction_fallback() -> None:
    db = _strategy_db()
    db.execute("UPDATE predictions SET l3_v2_signal=NULL WHERE code='600004'")
    _insert_history(db, "600004", [50.0] * 120, adjusted="none")

    report = build_accuracy_report(db, strong_threshold=4.0)

    assert "正常 n=2；触发 n=0" in report
    assert "含回溯计算 QFQ强通过0/未复权弱通过1/拒绝0/不可计算0" in report


def test_qfq_report_reconstruction_excludes_score_date_close() -> None:
    """Predictions may be generated intraday, before that date's close is known."""
    db = _strategy_db()
    db.execute("UPDATE predictions SET l3_v2_signal=NULL WHERE code='600004'")
    db.execute("UPDATE daily_bars SET close=1.0 WHERE code='600004' AND trade_date='2026-01-05' AND adjusted='qfq'")
    _insert_history(db, "600004", [100.0] * 120, adjusted="qfq")

    report = build_accuracy_report(db, strong_threshold=4.0)

    assert "正常 n=2；触发 n=0" in report
    assert "含回溯计算 QFQ强通过1/未复权弱通过0/拒绝0/不可计算0" in report
