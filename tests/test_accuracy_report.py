from __future__ import annotations

import sqlite3

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
            l3_v2_signal INTEGER
        );
        CREATE TABLE daily_bars (
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            close REAL,
            source TEXT NOT NULL,
            adjusted TEXT NOT NULL
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
            "INSERT INTO predictions VALUES (?, 'A', '2026-01-05', ?, ?)",
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


def test_qfq_report_computes_core_strategy_metrics() -> None:
    db = _strategy_db()
    report = build_accuracy_report(db)

    assert "对齐样本：5；有效截面：1；非重叠批次：1" in report
    assert "截面 Spearman IC 均值：1.000" in report
    assert "Q5−Q1 平均 alpha spread：40.00%" in report
    assert "Q5 策略累计总收益：30.00%" in report
    assert "沪深300全收益：5.00%" in report
    assert "Q5 策略最大回撤：0.00%" in report
    assert "L3 v2：通过 n=2" in report


def test_qfq_report_excludes_wrong_source_and_framework() -> None:
    db = _strategy_db()
    db.execute("UPDATE daily_bars SET source='other' WHERE code='600004'")
    db.execute("UPDATE predictions SET framework='B' WHERE code='600003'")

    report = build_accuracy_report(db)

    assert report.count("对齐样本：3；有效截面：0；非重叠批次：0") == 2
    assert report.count("暂无可用的 QFQ/沪深300全收益对齐样本") == 1
