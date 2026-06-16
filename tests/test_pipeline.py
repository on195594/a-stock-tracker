"""tests/test_pipeline.py — pipeline.py 的 15 个单元测试。

所有测试用 tmp_path fixture 隔离 SQLite 数据库，禁止真实 AKShare 网络请求。
通过 monkeypatch 替换 lib.market_data.ak、sys.modules["fetcher"] 以及 config.DB_PATH。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import types
from datetime import date, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config  # noqa: E402
from lib import cache as cache_mod  # noqa: E402
from lib import market_data  # noqa: E402
import pipeline  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """把 config.DB_PATH / lib.cache.DB_PATH / LOG_DIR 都指向 tmp_path。"""
    db_path = str(tmp_path / "tracker.db")
    log_dir = str(tmp_path / "logs")
    os.makedirs(log_dir, exist_ok=True)

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
    monkeypatch.setattr(config, "LOG_DIR", log_dir)
    monkeypatch.setattr(config, "ACCURACY_REPORT_PATH", str(tmp_path / "accuracy_report.txt"))

    # 触发建表
    cache_mod.get_db().close()
    return db_path


class _AkLikeTestProvider:
    def fetch_score_price(self, code: str, score_date: str):
        prefix = "sh" if code.startswith("6") else "sz"
        start = (date.fromisoformat(score_date) - timedelta(days=5)).strftime("%Y%m%d")
        end = score_date.replace("-", "")
        try:
            df = market_data.ak.stock_zh_a_hist_tx(symbol=f"{prefix}{code}", start_date=start, end_date=end)
        except Exception as exc:
            return market_data._exception_result("test.stock_zh_a_hist_tx", exc)
        result = market_data._normalize_bars_result(df, "test.stock_zh_a_hist_tx", "score_price")
        if result.value is None:
            return market_data.MarketDataResult(
                None,
                "failed",
                result.source,
                result.fetched_at,
                error_code=result.error_code,
                error_message=result.error_message,
            )
        row = result.value.iloc[-1]
        return market_data.MarketDataResult(float(row["close"]), "ok", result.source, result.fetched_at)

    def fetch_l3_bars(self, code: str, end_date: str, window: int):
        start = (date.fromisoformat(end_date) - timedelta(days=max(220, window * 2))).strftime("%Y%m%d")
        end = end_date.replace("-", "")
        prefix = "sh" if code.startswith("6") else "sz"
        try:
            tx_df = market_data.ak.stock_zh_a_hist_tx(symbol=f"{prefix}{code}", start_date=start, end_date=end)
            primary = market_data._normalize_bars_result(tx_df, "test.stock_zh_a_hist_tx", "l3_bars")
        except Exception as exc:
            primary = market_data._exception_result("test.stock_zh_a_hist_tx", exc)
        if primary.status != "failed" and primary.value is not None and len(primary.value) >= window:
            return primary
        try:
            em_df = market_data.ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="")
            fallback = market_data._normalize_bars_result(em_df, "test.stock_zh_a_hist", "l3_bars")
        except Exception as exc:
            return market_data._exception_result("test.stock_zh_a_hist", exc)
        if fallback.status != "failed":
            return market_data.MarketDataResult(
                fallback.value,
                "degraded",
                fallback.source,
                fallback.fetched_at,
                fallback_source=primary.source,
                fallback_reason=primary.error_code or market_data.INSUFFICIENT_WINDOW,
                adjusted=fallback.adjusted,
                volume_unit=fallback.volume_unit,
            )
        return fallback

    def fetch_outcome_price(self, code: str, target_date: str):
        for delta in range(11):
            d = date.fromisoformat(target_date) - timedelta(days=delta)
            compact = d.strftime("%Y%m%d")
            try:
                df = market_data.ak.stock_zh_a_hist(symbol=code, period="daily", start_date=compact, end_date=compact, adjust="")
            except Exception as exc:
                result = market_data._exception_result("test.stock_zh_a_hist", exc)
                continue
            result = market_data._normalize_bars_result(df, "test.stock_zh_a_hist", "outcome_price")
            if result.value is not None and not result.value.empty:
                return market_data.MarketDataResult(
                    float(result.value.iloc[-1]["close"]),
                    "ok" if delta == 0 else "degraded",
                    result.source,
                    result.fetched_at,
                    fallback_reason=None if delta == 0 else "NEAREST_AVAILABLE_PRICE",
                    freshness_days=delta,
                )
        return market_data.MarketDataResult(None, "failed", "test.stock_zh_a_hist", date.today().isoformat())

    def fetch_index_bars(self, symbol: str):
        try:
            df = market_data.ak.stock_zh_index_daily_tx(symbol=symbol)
        except Exception as exc:
            return market_data._exception_result("test.stock_zh_index_daily_tx", exc)
        result = market_data._normalize_bars_result(df, "test.stock_zh_index_daily_tx", "benchmark_price")
        if result.value is None:
            return result
        return market_data.MarketDataResult(result.value[["date", "close"]], "ok", result.source, result.fetched_at)


@pytest.fixture(autouse=True)
def test_market_data_provider(monkeypatch):
    monkeypatch.setattr(pipeline, "get_default_market_data_provider", _AkLikeTestProvider)


@pytest.fixture
def small_watchlist(monkeypatch):
    """缩小 watchlist，避免遍历所有默认股票。"""
    wl = [
        {"code": "600036", "name": "招商银行"},
        {"code": "000858", "name": "五粮液"},
    ]
    monkeypatch.setattr(config, "WATCHLIST", wl)
    return wl


@pytest.fixture
def fake_fetcher(monkeypatch):
    """把 lib/fetcher 与默认 L3 日线替换成 mock，避免真实 AKShare 调用。"""
    fake = types.ModuleType("fetcher")
    fake.cmd_batch = MagicMock()
    fake.cmd_fetch = MagicMock()
    monkeypatch.setitem(sys.modules, "fetcher", fake)
    monkeypatch.setattr(
        market_data.ak,
        "stock_zh_a_hist",
        lambda **kw: pd.DataFrame(columns=["日期", "收盘", "成交量"]),
    )
    return fake


@pytest.fixture
def fake_weights(tmp_path, monkeypatch):
    """把 weights.json 指向 tmp，内容与 test_scorer.py 保持一致的最小集。"""
    weights = {
        "version": 1,
        "updated_at": "2026-04-15",
        "frameworks": {
            "A": {
                "fundamental": {
                    "roe_3y_avg": {
                        "max_score": 15,
                        "breakpoints": [[0, 0], [8, 5], [12, 9], [15, 15], [25, 15]],
                        "interpolate": True,
                    },
                    "net_profit_growth": {
                        "max_score": 10,
                        "breakpoints": [[-20, 0], [0, 2], [8, 6], [15, 10], [30, 10]],
                        "interpolate": True,
                    },
                    "debt_ratio": {
                        "max_score": 10,
                        "breakpoints": [[30, 10], [50, 7], [65, 3], [80, 0]],
                        "interpolate": True,
                        "invert": True,
                    },
                    "gross_margin": {
                        "max_score": 10,
                        "breakpoints": [[0, 0], [15, 4], [25, 7], [35, 10], [60, 10]],
                        "interpolate": True,
                    },
                    "moat_fixed": {"max_score": 10, "phase1_fixed": 5},
                    "market_pos_fixed": {"max_score": 5, "phase1_fixed": 2},
                },
                "valuation": {
                    "pb_percentile_10y": {
                        "max_score": 15,
                        "breakpoints": [[5, 15], [20, 12], [40, 8], [60, 4], [80, 0]],
                        "interpolate": True,
                        "invert": True,
                    },
                    "sentiment_fixed": {"max_score": 5, "phase1_fixed": 3},
                },
            }
        },
        "thresholds": {"buy_strong": 65, "buy_moderate": 55, "buy_light": 45},
    }
    weights["frameworks"]["B"] = {
        "fundamental": {
            "roe_3y_avg": {
                "max_score": 20,
                "breakpoints": [[0, 0], [8, 7], [12, 12], [15, 20], [25, 20]],
                "interpolate": True,
            },
            "net_profit_growth": {
                "max_score": 10,
                "breakpoints": [[-20, 0], [0, 2], [8, 6], [15, 10], [30, 10]],
                "interpolate": True,
            },
            "debt_ratio": {
                "max_score": 10,
                "breakpoints": [[30, 10], [50, 7], [65, 3], [80, 0]],
                "interpolate": True,
                "invert": True,
            },
            "gross_margin": {
                "max_score": 15,
                "breakpoints": [[0, 0], [15, 6], [25, 11], [35, 15], [60, 15]],
                "interpolate": True,
            },
            "moat_fixed": {"max_score": 10, "phase1_fixed": 5},
            "market_pos_fixed": {"max_score": 5, "phase1_fixed": 2},
        },
        "valuation": {
            "pb_percentile_10y": {
                "max_score": 5,
                "breakpoints": [[5, 5], [20, 4], [40, 3], [60, 1], [80, 0]],
                "interpolate": True,
                "invert": True,
            },
            "sentiment_fixed": {"max_score": 5, "phase1_fixed": 3},
        },
    }
    p = tmp_path / "weights.json"
    p.write_text(json.dumps(weights, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "WEIGHTS_PATH", str(p))
    return weights


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _insert_fundamentals(code: str, name: str, industry: str, data: dict) -> None:
    """往 stock_fundamentals 写入一条缓存，供 daily 消费。"""
    from datetime import datetime

    db = cache_mod.get_db()
    db.execute(
        """INSERT OR REPLACE INTO stock_fundamentals
           (code, name, industry, data, updated_at, ttl_hours)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (code, name, industry, json.dumps(data, ensure_ascii=False),
         datetime.now().isoformat(), 24),
    )
    db.commit()
    db.close()


def _full_data(report_period: str = "2024-09-30") -> dict:
    """scorer 可打分的完整数据集。"""
    return {
        "roe_3y_avg": 16.5,
        "net_profit_growth": 5.5,
        "debt_ratio": 45.0,
        "gross_margin": 56.8,
        "pb_percentile_10y": 18.0,
        "report_period": report_period,
    }


def _tencent_hist_side_effect(code_price: dict[str, float]):
    """返回 stock_zh_a_hist_tx 的 side_effect：symbol="sh600036" → DataFrame(close=...)。"""
    def _side(symbol: str, **kw):
        code = symbol[2:]  # strip sh/sz prefix
        p = code_price.get(code)
        if p is None:
            return pd.DataFrame(columns=["close", "date"])
        return pd.DataFrame([{"close": p, "date": date.today().isoformat()}])
    return _side


def _index_tx_df(pairs: list[tuple[str, float]]) -> pd.DataFrame:
    """构造 stock_zh_index_daily_tx 的返回 DataFrame（腾讯列名）。"""
    return pd.DataFrame([{"date": d, "close": c} for d, c in pairs])


def _entry_hist_df(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    """构造 AKShare stock_zh_a_hist 的中文列名日线返回。"""
    if volumes is None:
        volumes = [100.0] * len(closes)
    start = date.today() - timedelta(days=len(closes) - 1)
    return pd.DataFrame(
        {
            "日期": [(start + timedelta(days=i)).isoformat() for i in range(len(closes))],
            "收盘": closes,
            "成交量": volumes,
        }
    )


def _entry_hist_side_effect(code_bars: dict[str, pd.DataFrame]):
    def _side(symbol: str, **kw):
        return code_bars.get(symbol, pd.DataFrame(columns=["日期", "收盘", "成交量"]))

    return _side


def _daily_ready_stock(code: str, name: str, industry: str = "银行") -> None:
    _insert_fundamentals(code, name, industry, _full_data())


def _prediction_l3_rows() -> dict[str, tuple[int | None, str | None]]:
    db = cache_mod.get_db()
    rows = db.execute(
        "SELECT code, entry_signal, entry_signal_version FROM predictions WHERE framework='A'"
    ).fetchall()
    db.close()
    return {code: (entry_signal, entry_signal_version) for code, entry_signal, entry_signal_version in rows}


def _insert_prediction(
    code: str, score_date: str, price_at_score: float,
    weights_hash: str = "abc12345", total_score: float = 60.0,
    entry_signal: int | None = None,
    entry_signal_version: str | None = None,
) -> int:
    db = cache_mod.get_db()
    cur = db.execute(
        """INSERT INTO predictions
           (code, name, framework, score_date, price_at_score,
            quant_score, total_score, weights_hash, report_period,
            entry_signal, entry_signal_version, created_at)
           VALUES (?, ?, 'A', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (code, f"N{code}", score_date, price_at_score,
         total_score - 10, total_score, weights_hash, "2024-09-30",
         entry_signal, entry_signal_version, score_date + "T15:00:00"),
    )
    db.commit()
    row_id = cur.lastrowid
    db.close()
    return row_id


def _framework_a_closed_30d_count() -> int:
    db = cache_mod.get_db()
    count = db.execute(
        """SELECT COUNT(*) FROM predictions
           WHERE outcome_30d IS NOT NULL AND framework = 'A'"""
    ).fetchone()[0]
    db.close()
    return int(count)


def assert_report_matches_db(output: str) -> None:
    expected = _framework_a_closed_30d_count()
    assert f"Framework A {expected} 条已结案记录" in output


FRAMEWORK_B_FINANCIAL_SECTION = "Framework B 金融候选 dry-run"
FRAMEWORK_B_QUALITY_SECTION = "Framework B 非金融质量候选与 B label 研究"
FRAMEWORK_B_THRESHOLDS_SECTION = "B provisional thresholds"
FRAMEWORK_B_LABEL_TRACKING_SECTION = "B provisional label outcome tracking"


def _framework_b_prediction_count() -> int:
    db = cache_mod.get_db()
    count = db.execute("SELECT COUNT(*) FROM predictions WHERE framework='B'").fetchone()[0]
    db.close()
    return int(count)


def _run_accuracy_report(capsys) -> str:
    pipeline.cmd_accuracy_report()
    return capsys.readouterr().out


def _run_accuracy_report_without_b_writes(capsys) -> str:
    before = _framework_b_prediction_count()
    out = _run_accuracy_report(capsys)
    assert _framework_b_prediction_count() == before
    return out


def _assert_contains_all(output: str, needles: list[str]) -> None:
    for needle in needles:
        assert needle in output


def _insert_index_price(symbol: str, d: str, close: float) -> None:
    db = cache_mod.get_db()
    db.execute(
        "INSERT OR REPLACE INTO index_prices (symbol, date, close) VALUES (?, ?, ?)",
        (symbol, d, close),
    )
    db.commit()
    db.close()


def _prediction_columns() -> set[str]:
    db = cache_mod.get_db()
    columns = {row[1] for row in db.execute("PRAGMA table_info(predictions)").fetchall()}
    db.close()
    return columns


# ---------------------------------------------------------------------------
# 0. predictions L3 schema
# ---------------------------------------------------------------------------
L3_AUDIT_COLUMNS = {
    "entry_signal",
    "entry_signal_version",
    "entry_signal_status",
    "entry_signal_reason",
    "entry_signal_source",
    "entry_signal_fetched_at",
}


def test_predictions_schema_includes_l3_entry_signal_columns(tmp_db):
    """新库 predictions 建表时包含 L3 entry_signal 字段。"""
    assert L3_AUDIT_COLUMNS.issubset(_prediction_columns())


def test_get_db_adds_l3_entry_signal_columns_to_legacy_predictions(tmp_path, monkeypatch):
    """旧库 predictions 缺少 L3 字段时，get_db() 只能 additive 补列。"""
    db_path = str(tmp_path / "legacy.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)

    legacy = sqlite3.connect(db_path)
    legacy.execute(
        """CREATE TABLE predictions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            code            TEXT    NOT NULL,
            name            TEXT,
            framework       TEXT    NOT NULL,
            score_date      TEXT    NOT NULL,
            price_at_score  REAL,
            quant_score     REAL,
            total_score     REAL,
            weights_hash    TEXT,
            report_period   TEXT,
            outcome_30d     REAL,
            outcome_60d     REAL,
            outcome_90d     REAL,
            benchmark_30d   REAL,
            benchmark_60d   REAL,
            benchmark_90d   REAL,
            estimate_flag   INTEGER DEFAULT 0,
            threshold_adjusted INTEGER DEFAULT 0,
            created_at      TEXT,
            UNIQUE(code, framework, score_date)
        )"""
    )
    legacy.execute(
        """INSERT INTO predictions
           (code, name, framework, score_date, total_score, created_at)
           VALUES ('600036', '招商银行', 'A', '2026-05-29', 66.0, '2026-05-29T15:00:00')"""
    )
    legacy.commit()
    legacy.close()

    db = cache_mod.get_db()
    columns = {row[1] for row in db.execute("PRAGMA table_info(predictions)").fetchall()}
    row = db.execute(
        """SELECT code, total_score, entry_signal, entry_signal_version,
                  entry_signal_status, entry_signal_reason, entry_signal_source,
                  entry_signal_fetched_at
           FROM predictions"""
    ).fetchone()
    db.close()

    assert L3_AUDIT_COLUMNS.issubset(columns)
    assert row == ("600036", 66.0, None, None, None, None, None, None)


# ---------------------------------------------------------------------------
# 1. daily 写入 L3 entry signal
# ---------------------------------------------------------------------------
def test_daily_writes_l3_entry_signal_when_history_passes(tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch):
    """日线历史足够且三项 AND 通过时，daily 写入 entry_signal=1/v1。"""
    for item in small_watchlist:
        _daily_ready_stock(item["code"], item["name"])

    monkeypatch.setattr(
        market_data.ak,
        "stock_zh_a_hist_tx",
        _tencent_hist_side_effect({"600036": 35.20, "000858": 128.40}),
    )
    monkeypatch.setattr(
        market_data.ak,
        "stock_zh_a_hist",
        _entry_hist_side_effect({
            "600036": _entry_hist_df([100.0] * 119 + [130.0], [100.0] * 115 + [300.0] * 5),
            "000858": _entry_hist_df([100.0] * 119 + [130.0], [100.0] * 115 + [300.0] * 5),
        }),
    )

    pipeline.cmd_daily()

    assert _prediction_l3_rows() == {
        "600036": (1, "v1"),
        "000858": (1, "v1"),
    }
    db = cache_mod.get_db()
    rows = db.execute(
        """SELECT entry_signal_status, entry_signal_reason, entry_signal_source,
                  entry_signal_fetched_at
           FROM predictions
           WHERE framework='A'
           ORDER BY code"""
    ).fetchall()
    db.close()
    assert [(status, reason, source) for status, reason, source, _ in rows] == [
        ("pass", "PASS", "test.stock_zh_a_hist"),
        ("pass", "PASS", "test.stock_zh_a_hist"),
    ]
    assert all(fetched_at for *_, fetched_at in rows)


def test_daily_writes_l3_null_v1_when_history_is_insufficient(tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch):
    """日线不足时仍写入基础评分，同时 L3 为 NULL/v1。"""
    for item in small_watchlist:
        _daily_ready_stock(item["code"], item["name"])

    monkeypatch.setattr(
        market_data.ak,
        "stock_zh_a_hist_tx",
        _tencent_hist_side_effect({"600036": 35.20, "000858": 128.40}),
    )
    monkeypatch.setattr(
        market_data.ak,
        "stock_zh_a_hist",
        _entry_hist_side_effect({
            "600036": _entry_hist_df([100.0] * 119, [100.0] * 119),
            "000858": _entry_hist_df([100.0] * 119, [100.0] * 119),
        }),
    )

    pipeline.cmd_daily()

    assert _prediction_l3_rows() == {
        "600036": (None, "v1"),
        "000858": (None, "v1"),
    }


def test_compute_stock_entry_signal_rejects_stale_cached_bars(tmp_db):
    """本地 daily_bars 足够但最新交易日超过 freshness SLA 时，不得产出 pass/reject。"""
    db = cache_mod.get_db()
    start = date.today() - timedelta(days=129)
    stale_bars = pd.DataFrame(
        {
            "date": [(start + timedelta(days=i)).isoformat() for i in range(120)],
            "close": [100.0] * 119 + [130.0],
            "volume": [100.0] * 115 + [300.0] * 5,
        }
    )
    cache_mod.upsert_daily_bars(
        db,
        "600036",
        stale_bars,
        "akshare.stock_zh_a_hist",
        fetched_at=(date.today() - timedelta(days=10)).isoformat(),
    )

    result = pipeline._compute_stock_entry_signal(db, "600036", date.today().isoformat())
    db.close()

    assert result.signal is None
    assert result.version == "v1"
    assert result.status == "unavailable"
    assert result.reason == "SOURCE_STALE"


def test_compute_stock_entry_signal_uses_l3_audit_failure_reason_when_no_cached_bars(tmp_db):
    """L3 refresh 失败且没有本地窗口时，prediction 原因应保留真实行情失败原因。"""
    db = cache_mod.get_db()
    cache_mod.insert_market_data_audit(
        db,
        market_data.MarketDataResult(
            None,
            "failed",
            "akshare.stock_zh_a_hist",
            date.today().isoformat(),
            error_code=market_data.REMOTE_DISCONNECTED,
        ),
        "l3_bars",
        "600036",
        date.today().isoformat(),
    )
    db.commit()

    result = pipeline._compute_stock_entry_signal(db, "600036", date.today().isoformat())
    db.close()

    assert result.signal is None
    assert result.version == "v1"
    assert result.status == "unavailable"
    assert result.reason == "REMOTE_DISCONNECTED"
    assert result.source == "akshare.stock_zh_a_hist"


def test_compute_stock_entry_signal_rejects_unknown_or_mixed_volume_unit(tmp_db):
    db = cache_mod.get_db()
    start = date.today() - timedelta(days=119)
    first_half = pd.DataFrame(
        {
            "date": [(start + timedelta(days=i)).isoformat() for i in range(60)],
            "close": [100.0] * 60,
            "volume": [100.0] * 60,
        }
    )
    second_half = pd.DataFrame(
        {
            "date": [(start + timedelta(days=60 + i)).isoformat() for i in range(60)],
            "close": [100.0] * 59 + [130.0],
            "volume": [300.0] * 60,
        }
    )
    cache_mod.upsert_daily_bars(db, "600036", first_half, "tushare.daily", volume_unit="share")
    cache_mod.upsert_daily_bars(db, "600036", second_half, "tushare.daily", volume_unit="hand")

    result = pipeline._compute_stock_entry_signal(db, "600036", date.today().isoformat())
    db.close()

    assert result.signal is None
    assert result.version == "v1"
    assert result.status == "insufficient"
    assert result.reason == "MIXED_SOURCE_VOLUME_UNSAFE"


def test_daily_writes_score_even_when_l3_compute_raises(tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch):
    """L3 计算异常时仍写入基础评分，并把 L3 标记为 NULL/v1。"""
    for item in small_watchlist:
        _daily_ready_stock(item["code"], item["name"])

    monkeypatch.setattr(
        market_data.ak,
        "stock_zh_a_hist_tx",
        _tencent_hist_side_effect({"600036": 35.20, "000858": 128.40}),
    )

    monkeypatch.setattr(
        market_data.ak,
        "stock_zh_a_hist",
        _entry_hist_side_effect({
            "600036": _entry_hist_df([100.0] * 119 + [130.0], [100.0] * 115 + [300.0] * 5),
            "000858": _entry_hist_df([100.0] * 119 + [130.0], [100.0] * 115 + [300.0] * 5),
        }),
    )

    def fail_entry_signal(*args, **kw):
        raise RuntimeError("entry signal compute failed")

    monkeypatch.setattr(pipeline, "compute_entry_signal", fail_entry_signal)

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    rows = db.execute(
        """SELECT code, total_score, entry_signal, entry_signal_version
           FROM predictions WHERE framework='A' ORDER BY code"""
    ).fetchall()
    db.close()

    assert [(code, entry_signal, entry_signal_version) for code, _, entry_signal, entry_signal_version in rows] == [
        ("000858", None, "v1"),
        ("600036", None, "v1"),
    ]
    assert all(isinstance(total_score, float) for _, total_score, _, _ in rows)


def test_daily_does_not_update_legacy_l3_null_null_rows(tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch):
    """daily 只写今日新记录，不回写历史 NULL/NULL 记录。"""
    legacy_date = (date.today() - timedelta(days=1)).isoformat()
    _insert_prediction("600036", legacy_date, 30.0, total_score=66.0)
    for item in small_watchlist:
        _daily_ready_stock(item["code"], item["name"])

    monkeypatch.setattr(
        market_data.ak,
        "stock_zh_a_hist_tx",
        _tencent_hist_side_effect({"600036": 35.20, "000858": 128.40}),
    )
    monkeypatch.setattr(
        market_data.ak,
        "stock_zh_a_hist",
        _entry_hist_side_effect({
            "600036": _entry_hist_df([100.0] * 119 + [130.0], [100.0] * 115 + [300.0] * 5),
            "000858": _entry_hist_df([100.0] * 119 + [130.0], [100.0] * 115 + [300.0] * 5),
        }),
    )

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    legacy_row = db.execute(
        """SELECT entry_signal, entry_signal_version FROM predictions
           WHERE code='600036' AND score_date=?""",
        (legacy_date,),
    ).fetchone()
    today_rows = db.execute(
        """SELECT COUNT(*) FROM predictions
           WHERE score_date=? AND entry_signal=1 AND entry_signal_version='v1'""",
        (date.today().isoformat(),),
    ).fetchone()[0]
    db.close()

    assert legacy_row == (None, None)
    assert today_rows == 2


# ---------------------------------------------------------------------------
# 2. daily 快乐路径
# ---------------------------------------------------------------------------
def test_daily_happy_path(tmp_db, small_watchlist, fake_fetcher, fake_weights,
                           monkeypatch):
    """每只股票有基本面 + 腾讯日线返回价格 → predictions 表写入正确行数。"""
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist_tx",
                        _tencent_hist_side_effect({"600036": 35.20, "000858": 128.40}))

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    rows = db.execute(
        "SELECT code, price_at_score, total_score, weights_hash, report_period "
        "FROM predictions"
    ).fetchall()
    db.close()
    assert len(rows) == 2  # 2 stocks × 1 framework (A only, B paused)
    codes = {r[0]: r for r in rows}
    assert codes["600036"][1] == pytest.approx(35.20)
    assert codes["000858"][1] == pytest.approx(128.40)
    # total_score 非 NULL 且 weights_hash 一致
    assert all(r[2] is not None and r[3] and r[4] == "2024-09-30" for r in rows)


def test_daily_persists_report_period_from_cache(
    tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch
):
    """daily 新写入 prediction 时应把缓存 report_period 持久化。"""
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data("2025-12-31"))

    monkeypatch.setattr(
        market_data.ak,
        "stock_zh_a_hist_tx",
        _tencent_hist_side_effect({"600036": 35.20, "000858": 128.40}),
    )

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    rows = db.execute(
        "SELECT DISTINCT report_period FROM predictions WHERE framework='A'"
    ).fetchall()
    db.close()
    assert rows == [("2025-12-31",)]


# ---------------------------------------------------------------------------
# 2. daily 幂等：同日重跑不新增
# ---------------------------------------------------------------------------
def test_daily_idempotent(tmp_db, small_watchlist, fake_fetcher, fake_weights,
                          monkeypatch):
    """UNIQUE(code, framework, score_date) 约束 → 第二次 daily 不新增行。"""
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist_tx",
                        _tencent_hist_side_effect({"600036": 35.20, "000858": 128.40}))

    pipeline.cmd_daily()
    pipeline.cmd_daily()  # 再跑一次

    db = cache_mod.get_db()
    cnt = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    db.close()
    assert cnt == 2  # 2 stocks × 1 framework（A only，幂等不新增）


# ---------------------------------------------------------------------------
# 3. daily 单只股票失败不中断
# ---------------------------------------------------------------------------
def test_daily_one_stock_fails(tmp_db, small_watchlist, fake_fetcher, fake_weights,
                                monkeypatch):
    """一只股票无 fundamentals 缓存 → 跳过但不中断其他股票写入。"""
    _insert_fundamentals("600036", "招商银行", "银行", _full_data())
    # 000858 缺失缓存

    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist_tx",
                        _tencent_hist_side_effect({"600036": 35.20, "000858": 128.40}))

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    rows = db.execute("SELECT code FROM predictions").fetchall()
    db.close()
    assert {r[0] for r in rows} == {"600036"}  # 只有 600036，Framework A 1 条
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 4. daily weights_hash 冲突退出
# ---------------------------------------------------------------------------
def test_daily_weights_hash_conflict(tmp_db, small_watchlist, fake_fetcher,
                                      fake_weights, monkeypatch):
    """今日已有不同 hash 的记录 → sys.exit(1)（hash 检查在价格抓取前完成）。"""
    today = date.today().isoformat()
    _insert_prediction("600036", today, 30.0, weights_hash="OLDHASH1")

    with pytest.raises(SystemExit) as exc:
        pipeline.cmd_daily()
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# 5. daily 价格来自 spot_em 快照
# ---------------------------------------------------------------------------
def test_daily_price_from_tencent_hist(tmp_db, small_watchlist, fake_fetcher,
                                        fake_weights, monkeypatch):
    """price_at_score 等于腾讯日线返回的 close；腾讯无数据则为 NULL。"""
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    # 只返回 600036 的价格，000858 腾讯接口返回空
    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist_tx",
                        _tencent_hist_side_effect({"600036": 40.55}))

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    rows = dict(db.execute(
        "SELECT code, price_at_score FROM predictions"
    ).fetchall())
    db.close()
    assert rows["600036"] == pytest.approx(40.55)
    assert rows["000858"] is None


# ---------------------------------------------------------------------------
# 6. outcome-update 30d 正常
# ---------------------------------------------------------------------------
def test_outcome_update_30d_normal(tmp_db, monkeypatch):
    """到期日能精确查到价格 → outcome = (新价/旧价-1)*100，estimate_flag=0。"""
    today = date.today()
    score_date = (today - timedelta(days=30)).isoformat()
    _insert_prediction("600036", score_date, 100.0)

    # index_prices 提供 benchmark 所需两个端点
    _insert_index_price("000300", score_date, 4000.0)
    _insert_index_price("000300", today.isoformat(), 4200.0)

    # target_date == today → 走 provider outcome price 路径
    def fake_hist_today(**kw):
        if kw.get("symbol") == "600036" and kw.get("start_date") == date.today().strftime("%Y%m%d"):
            return pd.DataFrame([{"收盘": 110.0, "日期": date.today().isoformat()}])
        return pd.DataFrame(columns=["收盘", "日期"])
    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist", fake_hist_today)

    # _ensure_index_prices 用腾讯接口 — 返回空 df 让它无操作（已有 index_prices）
    monkeypatch.setattr(
        market_data.ak, "stock_zh_index_daily_tx",
        lambda **kw: pd.DataFrame(columns=["date", "close"]),
    )

    pipeline.cmd_outcome_update()

    db = cache_mod.get_db()
    row = db.execute(
        "SELECT outcome_30d, benchmark_30d, alpha_30d, estimate_flag "
        "FROM predictions WHERE code='600036'"
    ).fetchone()
    db.close()
    assert row[0] == pytest.approx(10.0)
    assert row[1] == pytest.approx(5.0)
    assert row[2] == pytest.approx(5.0)
    assert row[3] == 0


# ---------------------------------------------------------------------------
# 7. outcome-update estimate_flag 标记
# ---------------------------------------------------------------------------
def test_outcome_update_estimate_flag(tmp_db, monkeypatch):
    """到期日当天停牌 → 向前回溯找到 N<5 日前的价，estimate_flag=1。"""
    today = date.today()
    # score_date + 31 天 ≤ today → 今天就是到期日的后 1 天（target_date = today-1）
    score_date = (today - timedelta(days=31)).isoformat()
    target_date = (today - timedelta(days=1)).isoformat()  # 到期日
    replacement_date = (today - timedelta(days=2)).isoformat()  # 替代日（1 天前）

    _insert_prediction("600036", score_date, 100.0)
    _insert_index_price("000300", score_date, 4000.0)
    _insert_index_price("000300", target_date, 4100.0)

    # 到期日 hist 为空；回溯 1 天得到 105.0
    def fake_hist(**kw):
        d = kw["start_date"]  # YYYYMMDD
        if d == target_date.replace("-", ""):
            return pd.DataFrame(columns=["日期", "收盘"])
        if d == replacement_date.replace("-", ""):
            return pd.DataFrame([{"日期": replacement_date, "收盘": 105.0}])
        return pd.DataFrame(columns=["日期", "收盘"])

    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist", fake_hist)
    monkeypatch.setattr(
        market_data.ak, "stock_zh_index_daily_tx",
        lambda **kw: pd.DataFrame(columns=["date", "close"]),
    )

    pipeline.cmd_outcome_update()

    db = cache_mod.get_db()
    row = db.execute(
        "SELECT outcome_30d, estimate_flag FROM predictions WHERE code='600036'"
    ).fetchone()
    db.close()
    assert row[0] == pytest.approx(5.0)  # 105/100-1 = 5%
    assert row[1] == 1


# ---------------------------------------------------------------------------
# 8. outcome-update 超出 10 日 → NULL
# ---------------------------------------------------------------------------
def test_outcome_update_null_beyond_10_days(tmp_db, monkeypatch):
    """10 日内都无可用价 → outcome 保持 NULL，不写异常值。"""
    today = date.today()
    score_date = (today - timedelta(days=35)).isoformat()
    _insert_prediction("600036", score_date, 100.0)

    # hist 永远返回空；spot_em 也不匹配
    monkeypatch.setattr(
        market_data.ak, "stock_zh_a_hist",
        lambda **kw: pd.DataFrame(columns=["日期", "收盘"]),
    )
    monkeypatch.setattr(
        market_data.ak, "stock_zh_index_daily_tx",
        lambda **kw: pd.DataFrame(columns=["date", "close"]),
    )

    pipeline.cmd_outcome_update()

    db = cache_mod.get_db()
    row = db.execute(
        "SELECT outcome_30d, benchmark_30d FROM predictions WHERE code='600036'"
    ).fetchone()
    db.close()
    assert row[0] is None
    assert row[1] is None


# ---------------------------------------------------------------------------
# 9. outcome-update benchmark 失败，outcome 正常
# ---------------------------------------------------------------------------
def test_outcome_update_benchmark_failure(tmp_db, monkeypatch):
    """index_prices 无数据 → outcome 正常写入，benchmark_*d 留 NULL。"""
    today = date.today()
    score_date = (today - timedelta(days=30)).isoformat()
    _insert_prediction("600036", score_date, 100.0)
    # 故意不插入 index_prices

    # target_date == today → 走 provider outcome price 路径
    def fake_hist_today(**kw):
        if kw.get("symbol") == "600036":
            return pd.DataFrame([{"收盘": 120.0, "日期": date.today().isoformat()}])
        return pd.DataFrame(columns=["收盘", "日期"])
    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist", fake_hist_today)
    monkeypatch.setattr(
        market_data.ak, "stock_zh_index_daily_tx",
        lambda **kw: pd.DataFrame(columns=["date", "close"]),
    )

    pipeline.cmd_outcome_update()

    db = cache_mod.get_db()
    row = db.execute(
        "SELECT outcome_30d, benchmark_30d, alpha_30d FROM predictions "
        "WHERE code='600036'"
    ).fetchone()
    db.close()
    assert row[0] == pytest.approx(20.0)
    assert row[1] is None
    assert row[2] is None  # VIRTUAL column 任一 NULL → NULL


# ---------------------------------------------------------------------------
# 10. outcome-update 60d 未到期不写
# ---------------------------------------------------------------------------
def test_outcome_update_60d_not_yet_due(tmp_db, monkeypatch):
    """score_date + 60 > today → 60d 未到期，不覆盖。"""
    today = date.today()
    score_date = (today - timedelta(days=30)).isoformat()  # 仅 30d 到期
    _insert_prediction("600036", score_date, 100.0)
    _insert_index_price("000300", score_date, 4000.0)
    _insert_index_price("000300", today.isoformat(), 4100.0)

    # target_date == today → 走 provider outcome price 路径
    def fake_hist_today(**kw):
        if kw.get("symbol") == "600036":
            return pd.DataFrame([{"收盘": 110.0, "日期": date.today().isoformat()}])
        return pd.DataFrame(columns=["收盘", "日期"])
    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist", fake_hist_today)
    monkeypatch.setattr(
        market_data.ak, "stock_zh_index_daily_tx",
        lambda **kw: pd.DataFrame(columns=["date", "close"]),
    )

    pipeline.cmd_outcome_update()

    db = cache_mod.get_db()
    row = db.execute(
        "SELECT outcome_30d, outcome_60d, outcome_90d FROM predictions "
        "WHERE code='600036'"
    ).fetchone()
    db.close()
    assert row[0] == pytest.approx(10.0)
    assert row[1] is None
    assert row[2] is None


# ---------------------------------------------------------------------------
# 11. accuracy-report 空表
# ---------------------------------------------------------------------------
def test_accuracy_report_empty(tmp_db, capsys):
    """无记录 → 显示样本不足警告 + 暂无已结案记录。"""
    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out
    assert "样本不足" in out
    assert "Framework A 0 条已结案记录" in out
    assert "暂无已结案记录" in out
    assert "选择性偏差" in out
    assert "L3 买点层" in out


def test_accuracy_report_l3_counts_null_semantics_and_strong_candidates(
    tmp_db, capsys, fake_weights, monkeypatch
):
    """L3 section 区分 NULL/NULL、NULL/v1、0/v1，并统计 strong 候选通过/拒绝。"""
    monkeypatch.setattr(pipeline, "_load_weights", lambda: fake_weights)
    score_date = (date.today() - timedelta(days=30)).isoformat()
    pass_id = _insert_prediction("600001", score_date, 100.0, total_score=70.0, entry_signal=1, entry_signal_version="v1")
    reject_id = _insert_prediction("600002", score_date, 100.0, total_score=68.0, entry_signal=0, entry_signal_version="v1")
    null_v1_id = _insert_prediction("600003", score_date, 100.0, total_score=66.0, entry_signal=None, entry_signal_version="v1")
    _insert_prediction("600004", score_date, 100.0, total_score=67.0, entry_signal=None, entry_signal_version=None)
    db = cache_mod.get_db()
    db.execute(
        "UPDATE predictions SET entry_signal_status='unavailable', entry_signal_reason='FETCH_FAILED' WHERE id=?",
        (null_v1_id,),
    )
    db.execute("UPDATE predictions SET outcome_30d=8.0, benchmark_30d=2.0 WHERE id=?", (pass_id,))
    db.execute("UPDATE predictions SET outcome_30d=3.0, benchmark_30d=5.0 WHERE id=?", (reject_id,))
    db.execute("UPDATE predictions SET outcome_30d=4.0, benchmark_30d=1.0 WHERE id=?", (null_v1_id,))
    db.execute(
        """INSERT INTO predictions
           (code, name, framework, score_date, price_at_score, quant_score,
            total_score, weights_hash, report_period, outcome_30d, benchmark_30d,
            entry_signal, entry_signal_version, created_at)
           VALUES ('B00001', 'B1', 'B', ?, 100, 50, 80, 'hashB', '2024-09-30', -5, -10, 1, 'v1', ?)""",
        (score_date, score_date + "T15:00:00"),
    )
    db.commit()
    db.close()

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out

    assert "L3 买点层" in out
    assert "v1 记录数：4" in out
    assert "entry_signal=1：2" in out
    assert "entry_signal=0：1" in out
    assert "NULL/NULL pre-L3：1" in out
    assert "NULL/v1 不可计算：1" in out
    assert "L3 覆盖率：3/4 = 75.0%" in out
    assert "不可计算原因：FETCH_FAILED=1" in out
    assert "strong 候选 L3 通过：1" in out
    assert "strong 候选 L3 拒绝：1" in out
    assert "strong 候选中 L3 不可计算：1" in out
    assert "高分但未推送：1（L3 unavailable）" in out
    assert "L3 30d 已结案：1" in out
    assert "L3 30d 命中率：1.000" in out
    assert "L3 30d 样本不足" in out


def test_accuracy_report_does_not_modify_project_tracked_report(tmp_db, capsys):
    """测试隔离数据库时，accuracy-report 不应改写项目根目录的 tracked 报告文件。"""
    report_path = os.path.join(PROJECT_ROOT, "accuracy_report.txt")
    before = open(report_path, "rb").read()
    try:
        pipeline.cmd_accuracy_report()
        capsys.readouterr()
        after = open(report_path, "rb").read()
    finally:
        with open(report_path, "wb") as f:
            f.write(before)
    assert after == before


# ---------------------------------------------------------------------------
# 12. accuracy-report 层级排序
# ---------------------------------------------------------------------------
def test_accuracy_report_ordering(tmp_db, capsys):
    """分层输出顺序：strong → moderate → light → no-action。"""
    today = date.today()
    score_date = (today - timedelta(days=30)).isoformat()

    def _closed(code: str, total: float, outcome: float) -> None:
        row_id = _insert_prediction(code, score_date, 100.0, total_score=total)
        db = cache_mod.get_db()
        db.execute(
            "UPDATE predictions SET outcome_30d=?, benchmark_30d=? WHERE id=?",
            (outcome, 0.0, row_id),
        )
        db.commit()
        db.close()

    _closed("000001", 60.0, 5.0)   # strong   (>=44)
    _closed("000002", 40.0, 3.0)   # moderate (35-44)
    _closed("000003", 30.0, 1.0)   # light    (26-35)
    _closed("000004", 20.0, -1.0)  # no-action (<26)

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out

    idx_strong = out.find("strong")
    idx_mod = out.find("moderate")
    idx_light = out.find("light")
    idx_none = out.find("no-action")
    assert 0 < idx_strong < idx_mod < idx_light < idx_none


# ---------------------------------------------------------------------------
# 13. accuracy-report 统计警告阈值
# ---------------------------------------------------------------------------
def test_accuracy_report_stat_warning(tmp_db, capsys):
    """已结案记录 < 100 → 头部带样本不足警告。"""
    today = date.today()
    score_date = (today - timedelta(days=30)).isoformat()
    row_id = _insert_prediction("600036", score_date, 100.0, total_score=40.0)  # moderate (35-44)
    db = cache_mod.get_db()
    db.execute(
        "UPDATE predictions SET outcome_30d=?, benchmark_30d=? WHERE id=?",
        (5.0, 2.0, row_id),
    )
    db.commit()
    db.close()

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out
    assert "样本不足（Framework A 1 条已结案记录）" in out
    # 且确实展示了分层数据
    assert "moderate" in out


def test_accuracy_report_warning_uses_framework_a_not_all_frameworks(tmp_db, capsys):
    """总结案≥100 但当前 A 框<100 时，仍应提示 A 框样本不足。"""
    score_date = (date.today() - timedelta(days=30)).isoformat()
    db = cache_mod.get_db()
    for i in range(75):
        db.execute(
            """INSERT INTO predictions
               (code, name, framework, score_date, price_at_score,
                quant_score, total_score, weights_hash, report_period,
                outcome_30d, benchmark_30d, created_at)
               VALUES (?, ?, 'A', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"A{i:05d}", f"A{i}", score_date, 100.0, 30.0,
             40.0, "hash-a", "2024-09-30", 5.0, 2.0,
             score_date + "T15:00:00"),
        )
    for i in range(35):
        db.execute(
            """INSERT INTO predictions
               (code, name, framework, score_date, price_at_score,
                quant_score, total_score, weights_hash, report_period,
                outcome_30d, benchmark_30d, created_at)
               VALUES (?, ?, 'B', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"B{i:05d}", f"B{i}", score_date, 100.0, 30.0,
             40.0, "hash-b", "2024-09-30", 5.0, 2.0,
             score_date + "T15:00:00"),
        )
    db.commit()
    db.close()

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out

    assert "样本不足（Framework A 75 条已结案记录）" in out
    assert "A" in out and "75        75" in out
    assert "B" in out and "35        35" in out


def test_accuracy_report_contract_matches_framework_a_sql_anchor(
    tmp_db, capsys, fake_weights, monkeypatch
):
    monkeypatch.setattr(pipeline, "_load_weights", lambda: fake_weights)
    score_date = (date.today() - timedelta(days=30)).isoformat()
    db = cache_mod.get_db()
    # A 框 2 条已结案
    for idx in range(2):
        db.execute(
            """INSERT INTO predictions
               (code, name, framework, score_date, price_at_score,
                quant_score, total_score, weights_hash, report_period,
                outcome_30d, benchmark_30d, created_at)
               VALUES (?, ?, 'A', ?, 10, 40, 50, 'hashA', '2024-09-30', 0.10, 0.02, ?)""",
            (f"60003{idx}", f"A{idx}", score_date, score_date + "T15:00:00"),
        )
    # B 框 3 条已结案：不得污染 A 框样本数
    for idx in range(3):
        db.execute(
            """INSERT INTO predictions
               (code, name, framework, score_date, price_at_score,
                quant_score, total_score, weights_hash, report_period,
                outcome_30d, benchmark_30d, created_at)
               VALUES (?, ?, 'B', ?, 10, 40, 50, 'hashB', '2024-09-30', 0.10, 0.02, ?)""",
            (f"00000{idx}", f"B{idx}", score_date, score_date + "T15:00:00"),
        )
    db.commit()
    db.close()

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out

    assert _framework_a_closed_30d_count() == 2
    assert_report_matches_db(out)
    assert "Framework A 2 条已结案记录" in out


def test_accuracy_report_post_fix_section_splits_samples(tmp_db, capsys):
    """accuracy-report 单独展示 2026-05-15 后 post-fix 样本，避免跨口径混合解释。"""
    pre_id = _insert_prediction("600001", "2026-05-14", 100.0, total_score=50.0)
    post_id = _insert_prediction("600002", "2026-05-15", 100.0, total_score=50.0)
    db = cache_mod.get_db()
    db.execute("UPDATE predictions SET outcome_30d=1.0, benchmark_30d=0.0 WHERE id=?", (pre_id,))
    db.execute("UPDATE predictions SET outcome_30d=2.0, benchmark_30d=0.0 WHERE id=?", (post_id,))
    db.commit()
    db.close()

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out

    assert "Post-fix 样本专区" in out
    assert "pre-fix 30d 结案：1" in out
    assert "post-fix 30d 结案：1" in out
    assert "post-fix 样本不足（1/100）" in out


def test_accuracy_report_data_quality_audit(tmp_db, capsys, small_watchlist):
    """数据质量审计报告 watchlist 缓存、PB 日度可计算和 Gemini cache 状态。"""
    data = _full_data()
    data["bps"] = 10.0
    data["pb_hist_monthly"] = [1.0] * 12
    _insert_fundamentals("600036", "招商银行", "银行", data)
    _insert_prediction("600036", "2026-05-30", 35.0, total_score=50.0)
    db = cache_mod.get_db()
    db.execute(
        """INSERT INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date)
           VALUES ('600036', 7, 3, 4, '2026-05-30')"""
    )
    db.commit()
    db.close()

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out

    assert "数据质量审计（当前 watchlist）" in out
    assert "基本面缓存可用：1/2" in out
    assert "基本面缓存缺失或过期：1" in out
    assert "金融行业 gross_margin 不适用：0" in out
    assert "PB 日度可计算：1/1" in out
    assert "cache report_period 缺失：0/1" in out
    assert "prediction report_period 缺失：0/1" in out
    assert "Gemini 缓存存在：1/2" in out
    assert "000858 五粮液: cache_missing_or_expired" in out


def test_accuracy_report_report_period_missing_split_history_locked(
    tmp_db, capsys, small_watchlist
):
    """缓存已有 report_period 但 prediction 为空时，应标成历史记录不可回填。"""
    _insert_fundamentals("600036", "招商银行", "银行", _full_data("2025-12-31"))
    row_id = _insert_prediction("600036", "2026-05-30", 35.0, total_score=50.0)
    db = cache_mod.get_db()
    db.execute("UPDATE predictions SET report_period=NULL WHERE id=?", (row_id,))
    db.commit()
    db.close()

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out

    assert "prediction report_period 缺失：1/1" in out
    assert "prediction report_period 缺失拆分：历史记录不可回填=1, 新记录待补齐=0, 无A记录=0" in out
    assert "missing:prediction_report_period(history_locked_cache_ready)" in out


def test_accuracy_report_framework_b_dry_run_does_not_write_predictions(
    tmp_db, capsys, fake_weights, small_watchlist, monkeypatch
):
    """Framework B dry-run 只在报告里对比，不启用生产写入。"""
    monkeypatch.setattr(pipeline, "_load_weights", lambda: fake_weights)
    _insert_fundamentals("600036", "招商银行", "银行", _full_data())
    _insert_fundamentals("000858", "五粮液", "白酒", _full_data())
    _insert_prediction("600036", "2026-05-30", 35.0, total_score=50.0)

    out = _run_accuracy_report_without_b_writes(capsys)

    _assert_contains_all(out, [
        FRAMEWORK_B_FINANCIAL_SECTION,
        "report-only，不写 predictions",
        "口径：银行/证券/保险/金融关键词候选",
        "不参与 B label 阈值",
        "候选样本：1",
        "可评分：1",
        "招商银行(600036)",
        "B 候选行业分布：",
        "银行: 1",
        "B-A 分项得分差异均值：",
        "B-A delta Top：",
        "B-A delta Bottom：",
        "金融行业专用口径",
    ])


def test_accuracy_report_framework_b_quality_expansion_report_only(
    tmp_db, capsys, fake_weights, small_watchlist, monkeypatch
):
    """B 框扩展候选只做非金融质量股 dry-run，不写 predictions。"""
    monkeypatch.setattr(pipeline, "_load_weights", lambda: fake_weights)
    _insert_fundamentals("600036", "招商银行", "银行", _full_data())
    _insert_fundamentals("000858", "五粮液", "白酒", _full_data())
    _insert_prediction("000858", "2026-05-30", 35.0, total_score=50.0)

    out = _run_accuracy_report_without_b_writes(capsys)

    _assert_contains_all(out, [
        FRAMEWORK_B_QUALITY_SECTION,
        "report-only",
        "口径：非金融质量规则",
        "provisional thresholds 与 label outcome",
        "conservative 规则：非金融",
        "base 规则：非金融",
        "loose 规则：非金融",
        "候选样本：1",
        "五粮液(000858)",
        "[白酒]",
        "当前候选已覆盖非金融行业",
        "后续积累建议：",
        FRAMEWORK_B_THRESHOLDS_SECTION,
        "基于非金融质量候选",
        FRAMEWORK_B_LABEL_TRACKING_SECTION,
        "禁止解释命中率/胜率",
        "不能作为交易或生产门槛",
    ])


def test_accuracy_report_framework_b_label_outcome_tracking_report_only(
    tmp_db, capsys, fake_weights, monkeypatch
):
    """B 框 provisional label 只能按最新 A 记录做只读 outcome 追踪。"""
    watchlist = [
        {"code": "000858", "name": "五粮液"},
        {"code": "600519", "name": "贵州茅台"},
        {"code": "000333", "name": "美的集团"},
        {"code": "600309", "name": "万华化学"},
        {"code": "300750", "name": "宁德时代"},
    ]
    monkeypatch.setattr(config, "WATCHLIST", watchlist)
    monkeypatch.setattr(pipeline, "_load_weights", lambda: fake_weights)
    for item in watchlist:
        _insert_fundamentals(item["code"], item["name"], "制造", _full_data())
        row_id = _insert_prediction(item["code"], "2026-05-01", 35.0, total_score=50.0)
        if item["code"] in {"000858", "600519"}:
            db = cache_mod.get_db()
            db.execute(
                "UPDATE predictions SET outcome_30d=?, benchmark_30d=? WHERE id=?",
                (3.0, 1.0, row_id),
            )
            db.commit()
            db.close()

    out = _run_accuracy_report_without_b_writes(capsys)

    _assert_contains_all(out, [
        FRAMEWORK_B_THRESHOLDS_SECTION,
        FRAMEWORK_B_LABEL_TRACKING_SECTION,
        "基于非金融质量候选",
        "只读 A 框 outcome 代理",
        "不写 predictions",
        "已结案30d=",
        "未来可结案30d=",
        "30d可结案日期：最早=",
        "下一批预计=",
        "到期但 outcome 仍为空风险清单（最多 8 条）：",
        "due=2026-05-31",
        "A均分=",
        "B均分=",
        "行业=制造:",
        "B label outcome 自然结案（非金融质量候选）：WAIT",
        "最早可评估=2026-05-31",
        "overdue风险=3",
        "B label 阈值/命中率解释：禁止",
        "禁止解释命中率/胜率",
    ])


def test_accuracy_report_financial_gross_margin_not_required(tmp_db, capsys, small_watchlist):
    """金融行业 gross_margin=None 应计为不适用，而不是 required 缺失。"""
    data = _full_data()
    data["gross_margin"] = None
    data["bps"] = 10.0
    data["pb_hist_monthly"] = [1.0] * 12
    _insert_fundamentals("600036", "招商银行", "银行", data)
    _insert_prediction("600036", "2026-05-30", 35.0, total_score=50.0)

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out

    assert "金融行业 gross_margin 不适用：1" in out
    assert "required 字段可接受：1/1" in out
    assert "600036 招商银行: gross_margin" not in out


def test_accuracy_report_phase6_readiness_waits_for_data_quality(tmp_db, capsys, small_watchlist):
    """Phase 6 readiness 应明确在数据质量或 post-fix outcome 不满足时等待。"""
    _insert_fundamentals("600036", "招商银行", "银行", _full_data())

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out

    assert "Phase 6 readiness" in out
    assert "post-fix A框 30d 结案 ≥ 100：0/100 WAIT" in out
    assert "数据质量门槛：WAIT" in out
    assert "B label outcome 自然结案（非金融质量候选）：WAIT" in out
    assert "B label 阈值/命中率解释：禁止" in out
    assert "Phase 6 生产化阻塞项：" in out
    assert "post-fix A框 30d 结案不足（0/100）" in out
    assert "数据质量门槛未满足" in out
    assert "B label 已结案样本不足（0/20）" in out
    assert "Phase 6 下一步：" in out
    assert "继续 daily/outcome-update，等待 post-fix A 框自然结案" in out
    assert "结论：暂不进入 Phase 6 生产化" in out


def test_pipeline_pb_percentile_wrapper_uses_scorer() -> None:
    hist = [1.0] * 12
    assert pipeline._compute_daily_pb_percentile(20.0, {"bps": 10.0, "pb_hist_monthly": hist}) == 100.0


# ---------------------------------------------------------------------------
# 14. _ensure_index_prices 首次拉取
# ---------------------------------------------------------------------------
def test_index_prices_init(tmp_db, monkeypatch):
    """index_prices 初始为空 → 用内部 canonical symbol 调 provider，过滤到 earliest 起。"""
    today = date.today().isoformat()
    earliest = (date.today() - timedelta(days=90)).isoformat()
    older = (date.today() - timedelta(days=120)).isoformat()  # 应被过滤掉

    class _IndexProvider:
        called = 0
        symbol = None

        def fetch_index_bars(self, symbol: str):
            self.called += 1
            self.symbol = symbol
            return market_data.MarketDataResult(
                _index_tx_df([
                    (older, 3800.0),
                    (earliest, 4000.0),
                    (today, 4200.0),
                ]),
                "ok",
                "test.index",
                today,
            )

    provider = _IndexProvider()

    db = cache_mod.get_db()
    pipeline._ensure_index_prices(db, earliest, today, provider)

    rows = db.execute(
        "SELECT symbol, date, close FROM index_prices ORDER BY date"
    ).fetchall()
    db.close()

    assert provider.called == 1
    assert provider.symbol == "000300"
    assert len(rows) == 2  # older 被 start_date 过滤掉
    assert rows[0] == ("000300", earliest, 4000.0)
    assert rows[1] == ("000300", today, 4200.0)


# ---------------------------------------------------------------------------
# 15. _ensure_index_prices 已有数据则跳过
# ---------------------------------------------------------------------------
def test_index_prices_skip_existing(tmp_db, monkeypatch):
    """latest_cached >= today → 完全不调用 AKShare（增量起点已过 today）。"""
    today_dt = date.today()
    today = today_dt.isoformat()
    earliest = (today_dt - timedelta(days=90)).isoformat()
    # 预填到未来一天，使 start_date > today
    future = (today_dt + timedelta(days=1)).isoformat()
    _insert_index_price("000300", future, 4500.0)

    called = {"n": 0}
    def fake_index_tx(**kw):
        called["n"] += 1
        return pd.DataFrame(columns=["date", "close"])

    monkeypatch.setattr(market_data.ak, "stock_zh_index_daily_tx", fake_index_tx)

    db = cache_mod.get_db()
    pipeline._ensure_index_prices(db, earliest, today)

    rows = db.execute("SELECT COUNT(*) FROM index_prices").fetchone()[0]
    db.close()

    assert called["n"] == 0     # start_date > today → 早返回，不触发网络调用
    assert rows == 1            # 原有数据保持


# ---------------------------------------------------------------------------
# 16. outcome-update 幂等性 — 已有 outcome 不被覆盖
# ---------------------------------------------------------------------------
def test_outcome_update_idempotent(tmp_db, monkeypatch):
    """outcome_30d 已写入 → 重跑不覆盖（WHERE outcome_30d IS NULL 过滤）。"""
    today = date.today()
    score_date = (today - timedelta(days=30)).isoformat()
    row_id = _insert_prediction("600036", score_date, 100.0)

    # 手动写入 outcome，模拟已结案状态
    db = cache_mod.get_db()
    db.execute(
        "UPDATE predictions SET outcome_30d=5.0, benchmark_30d=2.0 WHERE id=?",
        (row_id,),
    )
    db.commit()
    db.close()

    monkeypatch.setattr(
        market_data.ak, "stock_zh_index_daily_tx",
        lambda **kw: pd.DataFrame(columns=["date", "close"]),
    )

    pipeline.cmd_outcome_update()

    db = cache_mod.get_db()
    row = db.execute(
        "SELECT outcome_30d, benchmark_30d FROM predictions WHERE id=?", (row_id,)
    ).fetchone()
    db.close()
    assert row[0] == pytest.approx(5.0)   # 原值不被覆盖
    assert row[1] == pytest.approx(2.0)   # 原值不被覆盖


# ---------------------------------------------------------------------------
# 17. estimate_flag delta=5 边界（最大回溯）
# ---------------------------------------------------------------------------
def test_outcome_update_estimate_flag_delta5(tmp_db, monkeypatch):
    """delta=0~4 全空，delta=5（最大回溯）找到价格 → outcome 正常，estimate_flag=1。"""
    today = date.today()
    # target_date = score_date + 30 = today - 5（delta=5 → today-10）
    score_date = (today - timedelta(days=35)).isoformat()
    target_date = (today - timedelta(days=5)).isoformat()
    replacement_date = (today - timedelta(days=10)).isoformat()  # target_date - 5

    _insert_prediction("600036", score_date, 100.0)
    _insert_index_price("000300", score_date, 4000.0)
    _insert_index_price("000300", target_date, 4100.0)

    def fake_hist(**kw):
        if kw["start_date"] == replacement_date.replace("-", ""):
            return pd.DataFrame([{"日期": replacement_date, "收盘": 112.0}])
        return pd.DataFrame(columns=["日期", "收盘"])  # delta 0-4 全部空

    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist", fake_hist)
    monkeypatch.setattr(
        market_data.ak, "stock_zh_index_daily_tx",
        lambda **kw: pd.DataFrame(columns=["date", "close"]),
    )

    pipeline.cmd_outcome_update()

    db = cache_mod.get_db()
    row = db.execute(
        "SELECT outcome_30d, estimate_flag FROM predictions WHERE code='600036'"
    ).fetchone()
    db.close()
    assert row[0] == pytest.approx(12.0)  # (112/100-1)*100
    assert row[1] == 1


# ---------------------------------------------------------------------------
# 18. benchmark 单端缺失（只有 score_date 指数价）→ benchmark NULL，outcome 正常
# ---------------------------------------------------------------------------
def test_outcome_update_benchmark_one_side_missing(tmp_db, monkeypatch):
    """score_date 有指数价，target_date 无 → benchmark_30d NULL，outcome_30d 正常写入。"""
    today = date.today()
    score_date = (today - timedelta(days=30)).isoformat()
    _insert_prediction("600036", score_date, 100.0)

    # 只有 score_date 端有指数价，target_date (today) 无数据
    _insert_index_price("000300", score_date, 4000.0)

    # target_date == today → 走 provider outcome price 路径
    def fake_hist_today(**kw):
        if kw.get("symbol") == "600036":
            return pd.DataFrame([{"收盘": 115.0, "日期": date.today().isoformat()}])
        return pd.DataFrame(columns=["收盘", "日期"])
    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist", fake_hist_today)
    monkeypatch.setattr(
        market_data.ak, "stock_zh_index_daily_tx",
        lambda **kw: pd.DataFrame(columns=["date", "close"]),
    )

    pipeline.cmd_outcome_update()

    db = cache_mod.get_db()
    row = db.execute(
        "SELECT outcome_30d, benchmark_30d, alpha_30d FROM predictions WHERE code='600036'"
    ).fetchone()
    db.close()
    assert row[0] == pytest.approx(15.0)   # (115/100-1)*100
    assert row[1] is None                   # 单端缺失 → benchmark NULL
    assert row[2] is None                   # VIRTUAL: 任一 NULL → NULL


# ---------------------------------------------------------------------------
# 19. outcome-update：spot_em 失败时不提前退出（P0-B 回归）
# ---------------------------------------------------------------------------
def test_outcome_update_non_today_expiry_via_hist(tmp_db, monkeypatch):
    """target_date < today（非今日到期）→ 走 provider outcome price 历史查询路径。"""
    today = date.today()
    score_date = (today - timedelta(days=31)).isoformat()
    target_date = (today - timedelta(days=1)).isoformat()

    _insert_prediction("600036", score_date, 100.0)
    _insert_index_price("000300", score_date, 4000.0)
    _insert_index_price("000300", target_date, 4100.0)

    def fake_hist(**kw):
        if kw.get("start_date") == target_date.replace("-", ""):
            return pd.DataFrame([{"日期": target_date, "收盘": 110.0}])
        return pd.DataFrame(columns=["日期", "收盘"])

    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist", fake_hist)
    monkeypatch.setattr(
        market_data.ak, "stock_zh_index_daily_tx",
        lambda **kw: pd.DataFrame(columns=["date", "close"]),
    )

    pipeline.cmd_outcome_update()

    db = cache_mod.get_db()
    row = db.execute(
        "SELECT outcome_30d FROM predictions WHERE code='600036'"
    ).fetchone()
    db.close()
    assert row[0] == pytest.approx(10.0)  # (110/100-1)*100


# ---------------------------------------------------------------------------
# 20. daily：spot_em 失败时仍写入 predictions（price_at_score=NULL）
# ---------------------------------------------------------------------------
def test_daily_writes_predictions_when_tencent_fails(
    tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch
):
    """腾讯日线全部失败 → daily 阻断写入，predictions 保持空（不写 NULL price 记录）。"""
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    monkeypatch.setattr(
        market_data.ak, "stock_zh_a_hist_tx",
        lambda **_: (_ for _ in ()).throw(ConnectionError("RemoteDisconnected")),
    )

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    rows = db.execute(
        "SELECT code, price_at_score FROM predictions ORDER BY code"
    ).fetchall()
    db.close()
    assert len(rows) == 0  # 价格全部失败时阻断写入，不写 NULL


# ---------------------------------------------------------------------------
# 21. outcome-update：target_date==today → 走 provider outcome price（D3）
# ---------------------------------------------------------------------------
def test_outcome_update_today_expiry_via_hist(tmp_db, monkeypatch):
    """到期日恰好是今天，snapshot_data 恒为空 → 走 provider outcome price delta=0 路径。"""
    today = date.today()
    score_date = (today - timedelta(days=30)).isoformat()
    _insert_prediction("600036", score_date, 100.0)
    _insert_index_price("000300", score_date, 4000.0)
    _insert_index_price("000300", today.isoformat(), 4200.0)

    def fake_hist(**kw):
        # delta=0：start_date == end_date == today
        if kw.get("symbol") == "600036" and kw.get("start_date") == today.strftime("%Y%m%d"):
            return pd.DataFrame([{"收盘": 108.0, "日期": today.isoformat()}])
        return pd.DataFrame(columns=["收盘", "日期"])

    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist", fake_hist)
    monkeypatch.setattr(
        market_data.ak, "stock_zh_index_daily_tx",
        lambda **kw: pd.DataFrame(columns=["date", "close"]),
    )

    pipeline.cmd_outcome_update()

    db = cache_mod.get_db()
    row = db.execute(
        "SELECT outcome_30d, benchmark_30d, estimate_flag FROM predictions WHERE code='600036'"
    ).fetchone()
    db.close()
    assert row[0] == pytest.approx(8.0)   # (108/100-1)*100
    assert row[1] == pytest.approx(5.0)   # (4200/4000-1)*100
    assert row[2] == 0                    # 精确到期日，非估算


# ---------------------------------------------------------------------------
# 41. daily 价格全部失败 → 阻断写入，predictions 保持空
# ---------------------------------------------------------------------------
def test_daily_aborts_when_no_prices(tmp_db, small_watchlist, fake_fetcher,
                                      fake_weights, monkeypatch):
    """腾讯日线全部失败（返回空 DataFrame）→ cmd_daily 提前退出，不写 predictions。"""
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist_tx",
                        lambda **kw: pd.DataFrame(columns=["close", "date"]))

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    cnt = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    db.close()
    assert cnt == 0


def test_daily_with_disabled_default_provider_writes_audit_but_no_predictions(
    tmp_db,
    small_watchlist,
    fake_weights,
    monkeypatch,
):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(pipeline, "get_default_market_data_provider", market_data.get_default_market_data_provider)

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    prediction_count = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    audit_rows = db.execute(
        """SELECT purpose, error_code, COUNT(*)
           FROM market_data_audit
           GROUP BY purpose, error_code
           ORDER BY purpose"""
    ).fetchall()
    db.close()

    assert prediction_count == 0
    assert audit_rows == [
        ("l3_bars", market_data.SOURCE_DISABLED, len(small_watchlist)),
        ("score_price", market_data.SOURCE_DISABLED, len(small_watchlist)),
    ]


def test_daily_with_baostock_only_env_still_does_not_write_predictions(
    tmp_db,
    small_watchlist,
    fake_weights,
    monkeypatch,
):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setenv("MARKET_DATA_ALLOW_BAOSTOCK_ONLY", "1")
    monkeypatch.setattr(pipeline, "get_default_market_data_provider", market_data.get_default_market_data_provider)

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    prediction_count = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    audit_error_codes = {
        row[0]
        for row in db.execute("SELECT DISTINCT error_code FROM market_data_audit").fetchall()
    }
    db.close()

    assert prediction_count == 0
    assert audit_error_codes == {market_data.SOURCE_DISABLED}


class _BackfillProvider:
    def __init__(self):
        self.range_calls = []

    def fetch_l3_bars(self, code: str, end_date: str, window: int):
        raise AssertionError("backfill should fetch a date range, not only end/window")

    def fetch_daily_bars_range(self, code: str, start_date: str, end_date: str):
        self.range_calls.append((code, start_date, end_date))
        bars = market_data._normalize_bars_result(
            _entry_hist_df([100.0] * 119 + [130.0], [100.0] * 115 + [300.0] * 5),
            "test.backfill",
            "l3_bars",
        )
        return market_data.MarketDataResult(
            bars.value,
            "ok",
            "test.backfill",
            date.today().isoformat(),
            adjusted="none",
            volume_unit="share",
        )

    def fetch_score_price(self, code: str, score_date: str):
        raise AssertionError("not used")

    def fetch_outcome_price(self, code: str, target_date: str):
        raise AssertionError("not used")

    def fetch_index_bars(self, symbol: str):
        raise AssertionError("not used")


def test_market_data_backfill_recomputes_existing_l3_metadata_without_rescoring(
    tmp_db,
    small_watchlist,
    fake_weights,
):
    today = date.today().isoformat()
    row_id = _insert_prediction(
        "600036",
        today,
        30.0,
        total_score=66.0,
        entry_signal=None,
        entry_signal_version="v1",
    )

    provider = _BackfillProvider()
    pipeline.cmd_market_data_backfill(today, today, provider)

    db = cache_mod.get_db()
    row = db.execute(
        """SELECT total_score, entry_signal, entry_signal_status, entry_signal_reason,
                  entry_signal_source
           FROM predictions
           WHERE id=?""",
        (row_id,),
    ).fetchone()
    bars_count = db.execute("SELECT COUNT(*) FROM daily_bars WHERE code='600036'").fetchone()[0]
    db.close()

    assert row == (66.0, 1, "pass", "PASS", "test.backfill")
    assert bars_count == 120
    assert provider.range_calls == [("600036", (date.today() - timedelta(days=240)).isoformat(), today), ("000858", (date.today() - timedelta(days=240)).isoformat(), today)]


class _RangeAwareBackfillProvider:
    def __init__(self, score_date: str):
        self.score_date = date.fromisoformat(score_date)
        self.calls = []

    def fetch_l3_bars(self, code: str, end_date: str, window: int):
        raise AssertionError("backfill should fetch the complete date range")

    def fetch_daily_bars_range(self, code: str, start_date: str, end_date: str):
        self.calls.append((code, start_date, end_date))
        start_dt = date.fromisoformat(start_date)
        end_dt = date.fromisoformat(end_date)
        days = (end_dt - start_dt).days + 1
        rows = []
        for i in range(days):
            d = start_dt + timedelta(days=i)
            close = 130.0 if d == self.score_date else 100.0
            volume = 300.0 if self.score_date - timedelta(days=4) <= d <= self.score_date else 100.0
            rows.append({"date": d.isoformat(), "close": close, "volume": volume})
        return market_data.MarketDataResult(
            pd.DataFrame(rows),
            "ok",
            "test.range_backfill",
            date.today().isoformat(),
            adjusted="none",
            volume_unit="share",
        )

    def fetch_score_price(self, code: str, score_date: str):
        raise AssertionError("not used")

    def fetch_outcome_price(self, code: str, target_date: str):
        raise AssertionError("not used")

    def fetch_index_bars(self, symbol: str):
        raise AssertionError("not used")


def test_market_data_backfill_fetches_full_range_before_recomputing_old_l3(
    tmp_db,
    small_watchlist,
    fake_weights,
):
    end_dt = date.today()
    score_dt = end_dt - timedelta(days=300)
    score_date = score_dt.isoformat()
    end = end_dt.isoformat()
    row_id = _insert_prediction(
        "600036",
        score_date,
        30.0,
        total_score=66.0,
        entry_signal=1,
        entry_signal_version="v1",
    )
    provider = _RangeAwareBackfillProvider(score_date)

    pipeline.cmd_market_data_backfill(score_date, end, provider)

    db = cache_mod.get_db()
    row = db.execute(
        "SELECT entry_signal, entry_signal_status, entry_signal_reason, entry_signal_source FROM predictions WHERE id=?",
        (row_id,),
    ).fetchone()
    db.close()

    assert row == (1, "pass", "PASS", "test.range_backfill")
    assert ("600036", (score_dt - timedelta(days=240)).isoformat(), end) in provider.calls


def test_market_data_backfill_disabled_provider_does_not_rewrite_l3_metadata(
    tmp_db,
    small_watchlist,
    fake_weights,
    monkeypatch,
):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(pipeline, "get_default_market_data_provider", market_data.get_default_market_data_provider)
    today = date.today().isoformat()
    row_id = _insert_prediction(
        "600036",
        today,
        30.0,
        total_score=66.0,
        entry_signal=1,
        entry_signal_version="v1",
    )

    pipeline.cmd_market_data_backfill(today, today)

    db = cache_mod.get_db()
    row = db.execute(
        "SELECT entry_signal, entry_signal_version, entry_signal_reason FROM predictions WHERE id=?",
        (row_id,),
    ).fetchone()
    audit_count = db.execute(
        "SELECT COUNT(*) FROM market_data_audit WHERE error_code=?",
        (market_data.SOURCE_DISABLED,),
    ).fetchone()[0]
    db.close()

    assert row == (1, "v1", None)
    assert audit_count == len(small_watchlist)


def test_validate_l3_bar_windows_ignores_legacy_adjusted_empty_rows(tmp_db) -> None:
    db = cache_mod.get_db()
    bars = _entry_hist_df([100.0, 101.0], [100.0, 110.0]).rename(
        columns={"日期": "date", "收盘": "close", "成交量": "volume"}
    )
    cache_mod.upsert_daily_bars(db, "600036", bars, "legacy", adjusted="", volume_unit="share")
    cache_mod.upsert_daily_bars(db, "600036", bars, "tushare.daily", adjusted="none", volume_unit="hand")

    rows = pipeline._validate_l3_bar_windows(db, (date.today() - timedelta(days=10)).isoformat())
    db.close()

    assert rows == [("600036", 2, bars["date"].min(), bars["date"].max(), 1, 1, 1)]


# ---------------------------------------------------------------------------
# 42. daily 回填近期 NULL price_at_score
# ---------------------------------------------------------------------------
def test_daily_backfills_null_prices(tmp_db, small_watchlist, fake_fetcher,
                                      fake_weights, monkeypatch):
    """DB 中存在近期 price_at_score=NULL 记录 → 今日 daily 成功时自动回填。"""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    # 插入昨日 NULL 价格记录
    db = cache_mod.get_db()
    db.execute(
        """INSERT INTO predictions
           (code, name, framework, score_date, price_at_score,
            quant_score, total_score, weights_hash, report_period, created_at)
           VALUES ('600036','招商银行','A',?,NULL,45.0,55.0,'abc12345','2024-09-30',?)""",
        (yesterday, yesterday + "T16:30:00"),
    )
    db.commit()
    db.close()

    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    # 今日价格正常返回；昨日回填也由同一 mock 返回
    def hist_effect(symbol: str, start_date: str, end_date: str, **kw):
        code = symbol[2:]
        prices = {"600036": 36.50, "000858": 130.00}
        price = prices.get(code, 35.0)
        # 返回含 start_date 当日的价格
        from datetime import datetime as _dt
        d = _dt.strptime(start_date, "%Y%m%d").strftime("%Y-%m-%d")
        return pd.DataFrame([{"close": price, "date": d}])

    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist_tx", hist_effect)

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    row = db.execute(
        "SELECT price_at_score FROM predictions WHERE code='600036' AND score_date=?",
        (yesterday,),
    ).fetchone()
    db.close()
    assert row is not None
    assert row[0] == pytest.approx(36.50)  # 回填成功


# ---------------------------------------------------------------------------
# 43. TTL 回归：get_fundamentals 在缓存过期时返回 None
# ---------------------------------------------------------------------------
def test_ttl_expiry_regression(tmp_db):
    """回归测试：ttl_hours=24 的缓存在超过 24h 后应过期返回 None；改为 168h 后不过期。
    这是 2026-05-11 修复的 bug：行业识别失败时 TTL=24h，weekly cron 周六刷新后
    周一 daily（54.5h后）运行时缓存全部过期，34/35只股票被跳过。
    """
    import json as _json
    from datetime import datetime, timedelta

    db = cache_mod.get_db()
    old_updated = (datetime.now() - timedelta(hours=25)).isoformat()
    db.execute(
        """INSERT OR REPLACE INTO stock_fundamentals
           (code, name, industry, data, updated_at, ttl_hours)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("601939", "建设银行", "未知", _json.dumps({"roe_3y_avg": 12.0}), old_updated, 24),
    )
    db.commit()
    db.close()

    # TTL=24h，25h 前写入 → 应过期
    assert cache_mod.get_fundamentals("601939") is None

    # 改为 168h → 不过期
    db = cache_mod.get_db()
    db.execute("UPDATE stock_fundamentals SET ttl_hours=168 WHERE code='601939'")
    db.commit()
    db.close()
    assert cache_mod.get_fundamentals("601939") is not None


# ---------------------------------------------------------------------------
# 44. _refresh_fundamentals：成功和失败计数正确，返回 (success, failed)
# ---------------------------------------------------------------------------
def test_refresh_fundamentals_counts(tmp_db, small_watchlist, monkeypatch):
    """_refresh_fundamentals 正确统计成功/失败次数并返回元组。"""
    import types
    call_count = [0]

    def fake_cmd_fetch(args):
        call_count[0] += 1
        if args[0] == "000858":  # 五粮液失败
            raise RuntimeError("fetch failed")

    fake_fetcher_mod = types.ModuleType("fetcher")
    fake_fetcher_mod.cmd_fetch = fake_cmd_fetch
    monkeypatch.setitem(sys.modules, "fetcher", fake_fetcher_mod)

    success, failed = pipeline._refresh_fundamentals("test")
    assert success == 1  # 600036 成功
    assert failed == 1   # 000858 失败
    assert call_count[0] == 2


# ---------------------------------------------------------------------------
# 45. cmd_remove：删除已有股票；对未知股票发 WARNING 但不报错
# ---------------------------------------------------------------------------
def test_cmd_remove(tmp_db, monkeypatch):
    """cmd_remove 删除 predictions + stock_fundamentals；股票不存在时无异常。"""
    from datetime import datetime

    db = cache_mod.get_db()
    # 插入基本面缓存
    db.execute(
        """INSERT INTO stock_fundamentals (code, name, industry, data, updated_at, ttl_hours)
           VALUES ('600036','招商银行','银行Ⅱ','{}',?,168)""",
        (datetime.now().isoformat(),),
    )
    # 插入 predictions 记录
    db.execute(
        """INSERT INTO predictions
           (code, name, framework, score_date, quant_score, total_score,
            weights_hash, created_at)
           VALUES ('600036','招商银行','A','2026-04-21',40.0,55.0,'abc12345',?)""",
        (datetime.now().isoformat(),),
    )
    db.commit()
    db.close()

    pipeline.cmd_remove("600036")

    db = cache_mod.get_db()
    assert db.execute("SELECT COUNT(*) FROM predictions WHERE code='600036'").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM stock_fundamentals WHERE code='600036'").fetchone()[0] == 0
    db.close()

    # 对不存在的股票不应抛出异常
    pipeline.cmd_remove("999999")


# ---------------------------------------------------------------------------
# 46. accuracy-report：Gemini 仅首次评分（无后续重打分数据）→ 显示预计重打分日期
# ---------------------------------------------------------------------------
def test_accuracy_report_gemini_no_rescore(tmp_db, capsys):
    """qualitative_scores 每只股只有1条记录 → 显示尚无重评数据 + 预计首批重评。"""
    db = cache_mod.get_db()
    db.execute(
        """INSERT INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date)
           VALUES ('600036', 7, 3, 4, '2026-04-12')"""
    )
    db.commit()
    db.close()

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out
    assert "尚无重评数据" in out
    assert "2026-05-12" in out  # 2026-04-12 + 30天


# ---------------------------------------------------------------------------
# 47. accuracy-report：Gemini 漂移检测（2条记录，moat差值>2）→ 标记 low_confidence
# ---------------------------------------------------------------------------
def test_accuracy_report_gemini_drift_detected(tmp_db, capsys):
    """同一股票有首次和重打分两条记录，且 moat 差值 > 2 → ⚠️ low_confidence 标记。"""
    db = cache_mod.get_db()
    db.execute(
        """INSERT INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date)
           VALUES ('600036', 5, 3, 3, '2026-03-01')"""
    )
    db.execute(
        """INSERT INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date)
           VALUES ('600036', 9, 3, 3, '2026-04-01')"""
    )
    db.commit()
    db.close()

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out
    assert "low_confidence" in out
    assert "600036" in out


# ---------------------------------------------------------------------------
# 48. accuracy-report：Framework B 门槛 1 未满足
# ---------------------------------------------------------------------------
def test_accuracy_report_b_progress_not_met(tmp_db, capsys, fake_weights, monkeypatch):
    """A框 30d 结案 < 100 → 门槛 1 显示 ❌。"""
    today = date.today()
    score_date = (today - timedelta(days=30)).isoformat()
    row_id = _insert_prediction("600036", score_date, 100.0, total_score=50.0)
    db = cache_mod.get_db()
    db.execute(
        "UPDATE predictions SET outcome_30d=5.0, benchmark_30d=2.0 WHERE id=?", (row_id,)
    )
    db.commit()
    db.close()

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out
    assert "门槛 1" in out
    assert "❌" in out


# ---------------------------------------------------------------------------
# 49. accuracy-report：Framework B 旧门槛进度显示存在
# ---------------------------------------------------------------------------
def test_accuracy_report_b_progress_section_present(tmp_db, capsys):
    """accuracy-report 始终包含 Framework B 旧重启门槛进度节。"""
    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out
    assert "Framework B 旧重启门槛进度（A框生产化前置，不等同 report-only）" in out
    assert "门槛 1" in out
    assert "门槛 2" in out


def test_backfill_null_prices_uses_db_cache(tmp_db) -> None:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    db = cache_mod.get_db()

    # Insert a daily bar for yesterday
    bars = pd.DataFrame([{"date": yesterday, "close": 38.50, "volume": 1000.0}])
    cache_mod.upsert_daily_bars(db, "600036", bars, "test_source", volume_unit="share")

    # Insert a prediction record for yesterday with NULL price_at_score
    db.execute(
        """INSERT INTO predictions
           (code, name, framework, score_date, price_at_score,
            quant_score, total_score, weights_hash, report_period, created_at)
           VALUES ('600036','招商银行','A',?,NULL,45.0,55.0,'abc12345','2024-09-30',?)""",
        (yesterday, yesterday + "T16:30:00"),
    )
    db.commit()

    class _StrictMockProvider:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_score_price(self, code, score_date):
            self.calls += 1
            raise AssertionError("Should not fetch price over network when cached locally")

    provider = _StrictMockProvider()

    # Call the backfill function
    updated = pipeline._backfill_null_prices(db, date.today().isoformat(), provider)

    # Verify that the price was successfully backfilled from daily_bars
    row = db.execute(
        "SELECT price_at_score FROM predictions WHERE code='600036' AND score_date=?",
        (yesterday,),
    ).fetchone()
    db.close()

    assert updated == 1
    assert provider.calls == 0
    assert row is not None
    assert row[0] == pytest.approx(38.50)
