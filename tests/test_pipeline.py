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
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import a_stock_tracker.config as config  # noqa: E402
from a_stock_tracker.data import cache as cache_mod  # noqa: E402
from a_stock_tracker.data import market_data  # noqa: E402
import a_stock_tracker.cli as pipeline  # noqa: E402


def test_weights_hash_ignores_descriptive_notes():
    weights = {
        "frameworks": {
            "A": {
                "metric": {
                    "max_score": 10,
                    "breakpoints": [[0, 0], [1, 10]],
                    "note": "old provider wording",
                }
            }
        }
    }
    changed_note = json.loads(json.dumps(weights))
    changed_note["frameworks"]["A"]["metric"]["note"] = "new provider wording"

    assert pipeline._compute_weights_hash(weights) == pipeline._compute_weights_hash(changed_note)


def test_input_implementation_changes_start_a_new_cohort(monkeypatch):
    weights = {"frameworks": {"A": {"valuation": {}}}}
    first = pipeline._compute_weights_hash(weights)
    monkeypatch.setattr(pipeline, "_scoring_implementation", lambda: {"input_policy_version": "changed"})
    assert pipeline._compute_weights_hash(weights) != first
    assert len(first) == 16
    assert first not in {"8181a13c", "8aea81ed", "832893a3"}


def _with_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Fill minimal OHLC columns in AKShare test fixtures before shared normalization."""
    if df.empty:
        return df
    normalized = df.copy()
    if "close" in normalized.columns:
        for col in ["open", "high", "low"]:
            if col not in normalized.columns:
                normalized[col] = normalized["close"]
        return normalized
    if "收盘" not in normalized.columns:
        return normalized
    for col in ["开盘", "最高", "最低"]:
        if col not in normalized.columns:
            normalized[col] = normalized["收盘"]
    return normalized


def _fixture_bars_result(df: pd.DataFrame, source: str, purpose: str):
    if df.empty:
        return market_data.MarketDataResult(
            None,
            "failed",
            source,
            market_data.now(),
            error_code=market_data.EMPTY_RESPONSE,
        )
    normalized = df.rename(
        columns={"日期": "date", "开盘": "open", "最高": "high", "最低": "low", "收盘": "close", "成交量": "volume"}
    )
    required = {"date", "close"} | ({"volume"} if purpose == "l3_bars" else set())
    if not required.issubset(normalized.columns):
        return market_data.MarketDataResult(
            None,
            "failed",
            source,
            market_data.now(),
            error_code=market_data.MISSING_COLUMNS,
        )
    return market_data.MarketDataResult(normalized, "ok", source, market_data.now())


def _fixture_exception_result(source: str, exc: Exception):
    return market_data.MarketDataResult(
        None,
        "failed",
        source,
        market_data.now(),
        error_code=market_data.UNKNOWN_ERROR,
        error_message=str(exc),
    )


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
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

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
            return _fixture_exception_result("test.stock_zh_a_hist_tx", exc)
        result = _fixture_bars_result(_with_ohlc(df), "test.stock_zh_a_hist_tx", "score_price")
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
            primary = _fixture_bars_result(_with_ohlc(tx_df), "test.stock_zh_a_hist_tx", "l3_bars")
        except Exception as exc:
            primary = _fixture_exception_result("test.stock_zh_a_hist_tx", exc)
        if primary.status != "failed" and primary.value is not None and len(primary.value) >= window:
            return primary
        try:
            em_df = market_data.ak.stock_zh_a_hist(
                symbol=code, period="daily", start_date=start, end_date=end, adjust=""
            )
            fallback = _fixture_bars_result(_with_ohlc(em_df), "test.stock_zh_a_hist", "l3_bars")
        except Exception as exc:
            return _fixture_exception_result("test.stock_zh_a_hist", exc)
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
                df = market_data.ak.stock_zh_a_hist(
                    symbol=code, period="daily", start_date=compact, end_date=compact, adjust=""
                )
            except Exception as exc:
                result = _fixture_exception_result("test.stock_zh_a_hist", exc)
                continue
            result = _fixture_bars_result(_with_ohlc(df), "test.stock_zh_a_hist", "outcome_price")
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
            return _fixture_exception_result("test.stock_zh_index_daily_tx", exc)
        result = _fixture_bars_result(_with_ohlc(df), "test.stock_zh_index_daily_tx", "benchmark_price")
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
        (code, name, industry, json.dumps(data, ensure_ascii=False), datetime.now().isoformat(), 24),
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
            return pd.DataFrame(columns=["open", "high", "low", "close", "date"])
        return pd.DataFrame([{"open": p, "high": p, "low": p, "close": p, "date": date.today().isoformat()}])

    return _side


def _entry_hist_df(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    """构造 AKShare stock_zh_a_hist 的中文列名日线返回。"""
    if volumes is None:
        volumes = [100.0] * len(closes)
    start = date.today() - timedelta(days=len(closes) - 1)
    return pd.DataFrame(
        {
            "日期": [(start + timedelta(days=i)).isoformat() for i in range(len(closes))],
            "开盘": closes,
            "最高": closes,
            "最低": closes,
            "收盘": closes,
            "成交量": volumes,
        }
    )


def _entry_hist_side_effect(code_bars: dict[str, pd.DataFrame]):
    def _side(symbol: str, **kw):
        return code_bars.get(symbol, pd.DataFrame(columns=["日期", "开盘", "最高", "最低", "收盘", "成交量"]))

    return _side


def _daily_ready_stock(code: str, name: str, industry: str = "银行") -> None:
    _insert_fundamentals(code, name, industry, _full_data())


def _prediction_l3_rows() -> dict[str, tuple[int | None, str | None]]:
    db = cache_mod.get_db()
    rows = db.execute("SELECT code, entry_signal, entry_signal_version FROM predictions WHERE framework='A'").fetchall()
    db.close()
    return {code: (entry_signal, entry_signal_version) for code, entry_signal, entry_signal_version in rows}


def _insert_prediction(
    code: str,
    score_date: str,
    price_at_score: float,
    weights_hash: str = "abc12345",
    total_score: float = 60.0,
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
        (
            code,
            f"N{code}",
            score_date,
            price_at_score,
            total_score - 10,
            total_score,
            weights_hash,
            "2024-09-30",
            entry_signal,
            entry_signal_version,
            score_date + "T15:00:00",
        ),
    )
    db.commit()
    row_id = cur.lastrowid
    db.close()
    assert row_id is not None
    return row_id


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

QUALITATIVE_SNAPSHOT_COLUMNS = {
    "qualitative_snapshot_json",
    "qualitative_sources_json",
    "qualitative_mode",
}


def test_predictions_schema_includes_l3_entry_signal_columns(tmp_db):
    """新库 predictions 建表时包含 L3 entry_signal 字段。"""
    assert L3_AUDIT_COLUMNS.issubset(_prediction_columns())


def test_predictions_schema_includes_qualitative_snapshot_columns(tmp_db):
    assert (QUALITATIVE_SNAPSHOT_COLUMNS | {"scoring_snapshot_json"}).issubset(_prediction_columns())


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
    assert (QUALITATIVE_SNAPSHOT_COLUMNS | {"scoring_snapshot_json"}).issubset(columns)
    assert row == ("600036", 66.0, None, None, None, None, None, None)

    migrated = cache_mod.get_db()
    snapshot_row = migrated.execute(
        """SELECT qualitative_snapshot_json, qualitative_sources_json, qualitative_mode
           FROM predictions WHERE code='600036'"""
    ).fetchone()
    migrated.close()
    assert snapshot_row == (None, None, None)


def test_prediction_insert_persists_snapshot_and_conflict_does_not_overwrite(tmp_db):
    signal = types.SimpleNamespace(
        signal=1,
        version="v2.1-no-tech-gate",
        status="pass",
        reason="fixture",
        source="fixture",
        fetched_at="2026-05-30T15:00:00",
    )
    prep = {
        "code": "600036",
        "name": "招商银行",
        "price_at_score": 10.0,
        "report_period": "2026-03-31",
        "threshold_adjusted": 0,
        "l3_v2_result": signal,
        "l3_v2_fetched_at": "2026-05-30T15:00:00",
        "qualitative_snapshot_json": '{"market_pos":4,"moat":7,"sentiment":3}',
        "qualitative_sources_json": '{"market_pos":"v2","moat":"v2","sentiment":"v1"}',
        "qualitative_mode": "hybrid_v2",
        "scoring_snapshot_json": '{"scoring_inputs":{"roe_3y_avg":15}}',
    }
    result = {"quant_score": 40.0, "total_score": 54.0}
    db = cache_mod.get_db()

    assert pipeline._upsert_one_framework_prediction(db, prep, "2026-05-30", "hash", "A", result)
    prep["qualitative_snapshot_json"] = '{"market_pos":1,"moat":1,"sentiment":1}'
    prep["qualitative_sources_json"] = '{"market_pos":"v1","moat":"v1","sentiment":"v1"}'
    prep["qualitative_mode"] = "v1"
    prep["scoring_snapshot_json"] = '{"scoring_inputs":{"roe_3y_avg":0}}'
    assert not pipeline._upsert_one_framework_prediction(db, prep, "2026-05-30", "hash", "A", result)
    row = db.execute(
        """SELECT qualitative_snapshot_json, qualitative_sources_json, qualitative_mode, scoring_snapshot_json
           FROM predictions WHERE code='600036' AND framework='A'"""
    ).fetchone()
    db.close()

    assert row == (
        '{"market_pos":4,"moat":7,"sentiment":3}',
        '{"market_pos":"v2","moat":"v2","sentiment":"v1"}',
        "hybrid_v2",
        '{"scoring_inputs":{"roe_3y_avg":15}}',
    )


def test_local_qualitative_cache_replaces_external_model_fallback(tmp_db):
    db = cache_mod.get_db()
    db.execute(
        """INSERT INTO qualitative_scores
           (code, moat, market_pos, sentiment, scored_date)
           VALUES ('600036', 8, 4, 2, '2026-07-27')"""
    )
    db.commit()

    assert pipeline._load_local_qualitative_scores(db, "600036", "招商银行") == {
        "moat": 8,
        "market_pos": 4,
        "sentiment": 2,
    }
    assert pipeline._load_local_qualitative_scores(db, "000858", "五粮液") == {
        "moat": 5,
        "market_pos": 2,
        "sentiment": 3,
    }
    db.close()


def test_score_price_prefers_local_qfq_close(tmp_db):
    db = cache_mod.get_db()
    today = date.today().isoformat()
    cache_mod.upsert_daily_bars(
        db,
        "600036",
        pd.DataFrame([{"date": today, "close": 35.5, "volume": 100.0}]),
        "tushare.pro_bar.qfq",
        adjusted="qfq",
        volume_unit="hand",
    )

    class _NoNetworkProvider:
        def fetch_score_price(self, code: str, score_date: str):
            raise AssertionError("fresh QFQ cache must avoid a score-price network call")

    assert pipeline._get_score_price(db, _NoNetworkProvider(), "600036", today) == pytest.approx(35.5)
    db.close()


# ---------------------------------------------------------------------------
# 1. retired L3 v1 compatibility
# ---------------------------------------------------------------------------
def test_daily_leaves_retired_l3_v1_fields_null(tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch):
    for item in small_watchlist:
        _daily_ready_stock(item["code"], item["name"])

    monkeypatch.setattr(
        market_data.ak,
        "stock_zh_a_hist_tx",
        _tencent_hist_side_effect({"600036": 35.20, "000858": 128.40}),
    )

    pipeline.cmd_daily()

    assert _prediction_l3_rows() == {
        "600036": (None, None),
        "000858": (None, None),
    }


# ---------------------------------------------------------------------------
# 2. daily 快乐路径
# ---------------------------------------------------------------------------
def test_daily_happy_path(tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch):
    """每只股票有基本面 + 腾讯日线返回价格 → predictions 表写入正确行数。"""
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    monkeypatch.setattr(
        market_data.ak, "stock_zh_a_hist_tx", _tencent_hist_side_effect({"600036": 35.20, "000858": 128.40})
    )

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    rows = db.execute(
        "SELECT code, price_at_score, total_score, weights_hash, report_period FROM predictions"
    ).fetchall()
    db.close()
    assert len(rows) == 2  # 2 stocks × Framework A
    codes = {r[0]: r for r in rows}
    assert codes["600036"][1] == pytest.approx(35.20)
    assert codes["000858"][1] == pytest.approx(128.40)
    # total_score 非 NULL 且 weights_hash 一致
    assert all(r[2] is not None and r[3] and r[4] == "2024-09-30" for r in rows)


def test_daily_snapshot_reproduces_score_without_current_cache(
    tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch
):
    for item in small_watchlist:
        data = {
            **_full_data(),
            "valuation_coverage_status": "FULL_10Y",
            "valuation_valid_months": 120,
            "valuation_source_as_of": date.today().isoformat(),
        }
        _insert_fundamentals(item["code"], item["name"], "银行", data)
    db = cache_mod.get_db()
    db.execute("INSERT INTO qualitative_scores VALUES ('600036',8,4,2,'2026-07-13')")
    db.commit()
    db.close()
    monkeypatch.setattr(
        market_data.ak, "stock_zh_a_hist_tx", _tencent_hist_side_effect({"600036": 35.2, "000858": 128.4})
    )
    pipeline.cmd_daily()
    db = cache_mod.get_db()
    rows = db.execute("SELECT code,total_score,scoring_snapshot_json FROM predictions").fetchall()
    db.execute("DELETE FROM stock_fundamentals")
    db.commit()
    db.close()
    for code, total, raw in rows:
        snapshot = json.loads(raw)
        result = pipeline.score_stock(
            code, "A", snapshot["scoring_inputs"], weights={"frameworks": {"A": snapshot["weights"]}}
        )
        assert result == snapshot["result"]
        assert result["total_score"] == total
        assert snapshot["fundamentals_updated_at"]
        assert snapshot["implementation"]["source_sha256"]
        assert snapshot["industry"] == "银行"
        assert snapshot["qualitative_as_of"]["moat"] == ("2026-07-13" if code == "600036" else None)


def test_daily_persists_report_period_from_cache(tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch):
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
    rows = db.execute("SELECT DISTINCT report_period FROM predictions WHERE framework='A'").fetchall()
    db.close()
    assert rows == [("2025-12-31",)]


# ---------------------------------------------------------------------------
# 2. daily 幂等：同日重跑不新增
# ---------------------------------------------------------------------------
def test_daily_idempotent(tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch):
    """UNIQUE(code, framework, score_date) 约束 → 第二次 daily 不新增行。"""
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    monkeypatch.setattr(
        market_data.ak, "stock_zh_a_hist_tx", _tencent_hist_side_effect({"600036": 35.20, "000858": 128.40})
    )

    pipeline.cmd_daily()
    pipeline.cmd_daily()  # 再跑一次

    db = cache_mod.get_db()
    cnt = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    db.close()
    assert cnt == 2  # 2 stocks × 1 framework（A only，幂等不新增）


# ---------------------------------------------------------------------------
# 3. daily 单只股票失败不中断
# ---------------------------------------------------------------------------
def test_daily_one_stock_fails(tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch):
    """一只股票无 fundamentals 缓存 → 跳过但不中断其他股票写入。"""
    _insert_fundamentals("600036", "招商银行", "银行", _full_data())
    # 000858 缺失缓存

    monkeypatch.setattr(
        market_data.ak, "stock_zh_a_hist_tx", _tencent_hist_side_effect({"600036": 35.20, "000858": 128.40})
    )

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    rows = db.execute("SELECT code FROM predictions").fetchall()
    db.close()
    assert {r[0] for r in rows} == {"600036"}  # 只有 600036，Framework A 1 条
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 4. daily weights_hash 冲突退出
# ---------------------------------------------------------------------------
def test_daily_weights_hash_conflict(tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch):
    """今日已有不同 hash 的记录 → sys.exit(1)（hash 检查在价格抓取前完成）。"""
    today = date.today().isoformat()
    _insert_prediction("600036", today, 30.0, weights_hash="OLDHASH1")

    with pytest.raises(SystemExit) as exc:
        pipeline.cmd_daily()
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# 5. daily 价格来自 spot_em 快照
# ---------------------------------------------------------------------------
def test_daily_price_from_tencent_hist(tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch):
    """price_at_score 等于腾讯日线返回的 close；腾讯无数据则为 NULL。"""
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    # 只返回 600036 的价格，000858 腾讯接口返回空
    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist_tx", _tencent_hist_side_effect({"600036": 40.55}))

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    rows = dict(db.execute("SELECT code, price_at_score FROM predictions").fetchall())
    db.close()
    assert rows["600036"] == pytest.approx(40.55)
    assert rows["000858"] is None


def test_accuracy_report_empty(tmp_db):
    pipeline._write_accuracy_report()
    out = Path(config.ACCURACY_REPORT_PATH).read_text(encoding="utf-8")
    assert "a-stock-tracker QFQ 策略评估报告" in out
    assert out.count("暂无可用的 QFQ/沪深300全收益对齐样本") == 3
    assert "人工维护标的" in out


def test_accuracy_report_writes_only_to_isolated_output_path(tmp_db):
    """测试报告写入 fixture 配置的路径，不在项目根目录创建运行产物。"""
    root_report = Path(PROJECT_ROOT) / "accuracy_report.txt"

    pipeline._write_accuracy_report()

    assert Path(config.ACCURACY_REPORT_PATH).is_file()
    assert "a-stock-tracker QFQ 策略评估报告" in Path(config.ACCURACY_REPORT_PATH).read_text(encoding="utf-8")
    assert not root_report.exists()


def test_daily_post_steps_pushes_telegram(tmp_db, monkeypatch):
    import a_stock_tracker.reporting.telegram_push as telegram_push

    calls: list[str] = []
    monkeypatch.setattr(telegram_push, "push_daily_signals", lambda *_args: calls.append("telegram"))

    pipeline._run_daily_post_steps("2026-07-28", {"thresholds": {"buy_strong": 44}})

    assert calls == ["telegram"]


def test_accuracy_report_excludes_retired_default_sections(tmp_db):
    pipeline._write_accuracy_report()
    out = Path(config.ACCURACY_REPORT_PATH).read_text(encoding="utf-8")

    assert "a-stock-tracker QFQ 策略评估报告" in out
    assert "Framework B" not in out
    assert "Post-fix" not in out
    assert "命中率" not in out


def test_pipeline_clears_invalid_pb_instead_of_retaining_cached_value() -> None:
    data = {"pb_percentile_10y": 0.0, "bps": 10.0, "pb_hist_monthly": [1.0] * 54}
    pipeline._validate_daily_pb_percentile(data, "2026-09-04", "600938")
    assert data["pb_percentile_10y"] is None


# ---------------------------------------------------------------------------
# 20. daily：spot_em 失败时仍写入 predictions（price_at_score=NULL）
# ---------------------------------------------------------------------------
def test_daily_writes_predictions_when_tencent_fails(tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch):
    """腾讯日线全部失败 → daily 阻断写入，predictions 保持空（不写 NULL price 记录）。"""
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    monkeypatch.setattr(
        market_data.ak,
        "stock_zh_a_hist_tx",
        lambda **_: (_ for _ in ()).throw(ConnectionError("RemoteDisconnected")),
    )

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    rows = db.execute("SELECT code, price_at_score FROM predictions ORDER BY code").fetchall()
    db.close()
    assert len(rows) == 0  # 价格全部失败时阻断写入，不写 NULL


# ---------------------------------------------------------------------------
# 41. daily 价格全部失败 → 阻断写入，predictions 保持空
# ---------------------------------------------------------------------------
def test_daily_aborts_when_no_prices(tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch):
    """腾讯日线全部失败（返回空 DataFrame）→ cmd_daily 提前退出，不写 predictions。"""
    import a_stock_tracker.reporting.telegram_push as telegram_push

    calls: list[str] = []
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    monkeypatch.setattr(market_data.ak, "stock_zh_a_hist_tx", lambda **kw: pd.DataFrame(columns=["close", "date"]))
    monkeypatch.setattr(pipeline, "_write_accuracy_report", lambda *_args: calls.append("report"))
    monkeypatch.setattr(telegram_push, "push_daily_signals", lambda *_args: calls.append("telegram"))

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    cnt = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    db.close()
    assert cnt == 0
    assert calls == ["report"]


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
        ("score_price", market_data.SOURCE_DISABLED, len(small_watchlist)),
    ]


# 42. daily 回填近期 NULL price_at_score
# ---------------------------------------------------------------------------
def test_daily_backfills_null_prices(tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch):
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

        def fetch_l3_bars(self, code, end_date, window):
            raise AssertionError("not used")

        def fetch_outcome_price(self, code, target_date):
            raise AssertionError("not used")

        def fetch_daily_bars_range(self, code, start_date, end_date):
            raise AssertionError("not used")

        def fetch_index_bars(self, symbol):
            raise AssertionError("not used")

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
