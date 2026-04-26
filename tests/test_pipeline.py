"""tests/test_pipeline.py — pipeline.py 的 15 个单元测试。

所有测试用 tmp_path fixture 隔离 SQLite 数据库，禁止真实 AKShare 网络请求。
通过 monkeypatch 替换 pipeline.ak、sys.modules["fetcher"] 以及 config.DB_PATH。
"""
from __future__ import annotations

import json
import os
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

    # 触发建表
    cache_mod.get_db().close()
    return db_path


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
    """把 lib/fetcher 替换成 mock，避免真实 AKShare 调用。"""
    fake = types.ModuleType("fetcher")
    fake.cmd_batch = MagicMock()
    fake.cmd_fetch = MagicMock()
    monkeypatch.setitem(sys.modules, "fetcher", fake)
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
                    "pe_percentile_10y": {
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
        "pe_percentile_10y": 18.0,
        "report_period": report_period,
    }


def _spot_df(prices: dict[str, float]) -> pd.DataFrame:
    """构造 stock_zh_a_spot_em 的返回 DataFrame。"""
    return pd.DataFrame([
        {"代码": code, "名称": f"NAME{code}", "最新价": price}
        for code, price in prices.items()
    ])


def _index_df(pairs: list[tuple[str, float]]) -> pd.DataFrame:
    """构造 index_zh_a_hist 的返回 DataFrame。"""
    return pd.DataFrame([{"日期": d, "收盘": c} for d, c in pairs])


def _insert_prediction(
    code: str, score_date: str, price_at_score: float,
    weights_hash: str = "abc12345", total_score: float = 60.0,
) -> int:
    db = cache_mod.get_db()
    cur = db.execute(
        """INSERT INTO predictions
           (code, name, framework, score_date, price_at_score,
            quant_score, total_score, weights_hash, report_period, created_at)
           VALUES (?, ?, 'A', ?, ?, ?, ?, ?, ?, ?)""",
        (code, f"N{code}", score_date, price_at_score,
         total_score - 10, total_score, weights_hash, "2024-09-30",
         score_date + "T15:00:00"),
    )
    db.commit()
    row_id = cur.lastrowid
    db.close()
    return row_id


def _insert_index_price(symbol: str, d: str, close: float) -> None:
    db = cache_mod.get_db()
    db.execute(
        "INSERT OR REPLACE INTO index_prices (symbol, date, close) VALUES (?, ?, ?)",
        (symbol, d, close),
    )
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# 1. daily 快乐路径
# ---------------------------------------------------------------------------
def test_daily_happy_path(tmp_db, small_watchlist, fake_fetcher, fake_weights,
                           monkeypatch):
    """每只股票有基本面 + spot_em 返回价格 → predictions 表写入正确行数。"""
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    spot = _spot_df({"600036": 35.20, "000858": 128.40})
    monkeypatch.setattr(pipeline.ak, "stock_zh_a_spot_em", lambda: spot)

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    rows = db.execute(
        "SELECT code, price_at_score, total_score, weights_hash, report_period "
        "FROM predictions"
    ).fetchall()
    db.close()
    assert len(rows) == 2
    codes = {r[0]: r for r in rows}
    assert codes["600036"][1] == pytest.approx(35.20)
    assert codes["000858"][1] == pytest.approx(128.40)
    # total_score 非 NULL 且 weights_hash 一致
    assert all(r[2] is not None and r[3] and r[4] == "2024-09-30" for r in rows)


# ---------------------------------------------------------------------------
# 2. daily 幂等：同日重跑不新增
# ---------------------------------------------------------------------------
def test_daily_idempotent(tmp_db, small_watchlist, fake_fetcher, fake_weights,
                          monkeypatch):
    """UNIQUE(code, framework, score_date) 约束 → 第二次 daily 不新增行。"""
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    spot = _spot_df({"600036": 35.20, "000858": 128.40})
    monkeypatch.setattr(pipeline.ak, "stock_zh_a_spot_em", lambda: spot)

    pipeline.cmd_daily()
    pipeline.cmd_daily()  # 再跑一次

    db = cache_mod.get_db()
    cnt = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    db.close()
    assert cnt == 2


# ---------------------------------------------------------------------------
# 3. daily 单只股票失败不中断
# ---------------------------------------------------------------------------
def test_daily_one_stock_fails(tmp_db, small_watchlist, fake_fetcher, fake_weights,
                                monkeypatch):
    """一只股票无 fundamentals 缓存 → 跳过但不中断其他股票写入。"""
    _insert_fundamentals("600036", "招商银行", "银行", _full_data())
    # 000858 缺失缓存

    spot = _spot_df({"600036": 35.20, "000858": 128.40})
    monkeypatch.setattr(pipeline.ak, "stock_zh_a_spot_em", lambda: spot)

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    rows = db.execute("SELECT code FROM predictions").fetchall()
    db.close()
    assert [r[0] for r in rows] == ["600036"]


# ---------------------------------------------------------------------------
# 4. daily weights_hash 冲突退出
# ---------------------------------------------------------------------------
def test_daily_weights_hash_conflict(tmp_db, small_watchlist, fake_fetcher,
                                      fake_weights, monkeypatch):
    """今日已有不同 hash 的记录 → sys.exit(1)。"""
    today = date.today().isoformat()
    _insert_prediction("600036", today, 30.0, weights_hash="OLDHASH1")

    spot = _spot_df({"600036": 35.20, "000858": 128.40})
    monkeypatch.setattr(pipeline.ak, "stock_zh_a_spot_em", lambda: spot)

    with pytest.raises(SystemExit) as exc:
        pipeline.cmd_daily()
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# 5. daily 价格来自 spot_em 快照
# ---------------------------------------------------------------------------
def test_daily_price_from_spot_em(tmp_db, small_watchlist, fake_fetcher,
                                   fake_weights, monkeypatch):
    """price_at_score 必须等于 spot_em 返回的 '最新价'；缺失则为 NULL。"""
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    # 只返回 600036 的价格，000858 在快照中缺失
    spot = _spot_df({"600036": 40.55})
    monkeypatch.setattr(pipeline.ak, "stock_zh_a_spot_em", lambda: spot)

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

    # spot_em 返回今日价（target_date == today → 走 spot 路径）
    spot = _spot_df({"600036": 110.0})
    monkeypatch.setattr(pipeline.ak, "stock_zh_a_spot_em", lambda: spot)

    # _ensure_index_prices 会调 index_zh_a_hist — 返回空 df 让它无操作
    monkeypatch.setattr(
        pipeline.ak, "index_zh_a_hist",
        lambda **kw: pd.DataFrame(columns=["日期", "收盘"]),
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

    monkeypatch.setattr(pipeline.ak, "stock_zh_a_hist", fake_hist)
    monkeypatch.setattr(pipeline.ak, "stock_zh_a_spot_em",
                        lambda: _spot_df({"600036": 999.0}))  # 不该被用到
    monkeypatch.setattr(
        pipeline.ak, "index_zh_a_hist",
        lambda **kw: pd.DataFrame(columns=["日期", "收盘"]),
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
# 8. outcome-update 超出 5 日 → NULL
# ---------------------------------------------------------------------------
def test_outcome_update_null_beyond_5_days(tmp_db, monkeypatch):
    """5 日内都无可用价 → outcome 保持 NULL，不写异常值。"""
    today = date.today()
    score_date = (today - timedelta(days=35)).isoformat()
    _insert_prediction("600036", score_date, 100.0)

    # hist 永远返回空；spot_em 也不匹配
    monkeypatch.setattr(
        pipeline.ak, "stock_zh_a_hist",
        lambda **kw: pd.DataFrame(columns=["日期", "收盘"]),
    )
    monkeypatch.setattr(pipeline.ak, "stock_zh_a_spot_em", lambda: _spot_df({}))
    monkeypatch.setattr(
        pipeline.ak, "index_zh_a_hist",
        lambda **kw: pd.DataFrame(columns=["日期", "收盘"]),
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

    spot = _spot_df({"600036": 120.0})
    monkeypatch.setattr(pipeline.ak, "stock_zh_a_spot_em", lambda: spot)
    monkeypatch.setattr(
        pipeline.ak, "index_zh_a_hist",
        lambda **kw: pd.DataFrame(columns=["日期", "收盘"]),
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

    spot = _spot_df({"600036": 110.0})
    monkeypatch.setattr(pipeline.ak, "stock_zh_a_spot_em", lambda: spot)
    monkeypatch.setattr(
        pipeline.ak, "index_zh_a_hist",
        lambda **kw: pd.DataFrame(columns=["日期", "收盘"]),
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
    assert "0 条已结案记录" in out
    assert "暂无已结案记录" in out
    assert "选择性偏差" in out


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

    _closed("000001", 70.0, 5.0)   # strong
    _closed("000002", 60.0, 3.0)   # moderate
    _closed("000003", 50.0, 1.0)   # light
    _closed("000004", 30.0, -1.0)  # no-action

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
    row_id = _insert_prediction("600036", score_date, 100.0, total_score=60.0)
    db = cache_mod.get_db()
    db.execute(
        "UPDATE predictions SET outcome_30d=?, benchmark_30d=? WHERE id=?",
        (5.0, 2.0, row_id),
    )
    db.commit()
    db.close()

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out
    assert "样本不足（1 条已结案记录）" in out
    # 且确实展示了分层数据
    assert "moderate" in out


# ---------------------------------------------------------------------------
# 14. _ensure_index_prices 首次拉取
# ---------------------------------------------------------------------------
def test_index_prices_init(tmp_db, monkeypatch):
    """index_prices 初始为空 → 调用 index_zh_a_hist 填充全量区间。"""
    today = date.today().isoformat()
    earliest = (date.today() - timedelta(days=90)).isoformat()

    called = {"n": 0}
    def fake_index_hist(**kw):
        called["n"] += 1
        called["args"] = kw
        return _index_df([
            (earliest, 4000.0),
            (today, 4200.0),
        ])

    monkeypatch.setattr(pipeline.ak, "index_zh_a_hist", fake_index_hist)

    db = cache_mod.get_db()
    pipeline._ensure_index_prices(db, earliest, today)

    rows = db.execute(
        "SELECT symbol, date, close FROM index_prices ORDER BY date"
    ).fetchall()
    db.close()

    assert called["n"] == 1
    assert len(rows) == 2
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
    def fake_index_hist(**kw):
        called["n"] += 1
        return pd.DataFrame(columns=["日期", "收盘"])

    monkeypatch.setattr(pipeline.ak, "index_zh_a_hist", fake_index_hist)

    db = cache_mod.get_db()
    pipeline._ensure_index_prices(db, earliest, today)

    rows = db.execute("SELECT COUNT(*) FROM index_prices").fetchone()[0]
    db.close()

    assert called["n"] == 0     # 不触发网络调用
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

    # spot_em 返回差异很大的价格；若被重新计算 → (999/100-1)*100 = 899%
    spot = _spot_df({"600036": 999.0})
    monkeypatch.setattr(pipeline.ak, "stock_zh_a_spot_em", lambda: spot)
    monkeypatch.setattr(
        pipeline.ak, "index_zh_a_hist",
        lambda **kw: pd.DataFrame(columns=["日期", "收盘"]),
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

    monkeypatch.setattr(pipeline.ak, "stock_zh_a_hist", fake_hist)
    monkeypatch.setattr(pipeline.ak, "stock_zh_a_spot_em", lambda: _spot_df({}))
    monkeypatch.setattr(
        pipeline.ak, "index_zh_a_hist",
        lambda **kw: pd.DataFrame(columns=["日期", "收盘"]),
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

    spot = _spot_df({"600036": 115.0})
    monkeypatch.setattr(pipeline.ak, "stock_zh_a_spot_em", lambda: spot)
    monkeypatch.setattr(
        pipeline.ak, "index_zh_a_hist",
        lambda **kw: pd.DataFrame(columns=["日期", "收盘"]),
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
def test_outcome_update_continues_when_spot_em_fails(tmp_db, monkeypatch):
    """spot_em 抛异常 → outcome-update 不提前退出，过期记录仍能用历史价更新。"""
    today = date.today()
    score_date = (today - timedelta(days=31)).isoformat()
    target_date = (today - timedelta(days=1)).isoformat()

    _insert_prediction("600036", score_date, 100.0)
    _insert_index_price("000300", score_date, 4000.0)
    _insert_index_price("000300", target_date, 4100.0)

    # spot_em 抛异常（模拟东方财富断线）
    monkeypatch.setattr(
        pipeline.ak, "stock_zh_a_spot_em",
        lambda: (_ for _ in ()).throw(ConnectionError("RemoteDisconnected")),
    )

    # 历史价：target_date 可查到
    def fake_hist(**kw):
        if kw.get("start_date") == target_date.replace("-", ""):
            return pd.DataFrame([{"日期": target_date, "收盘": 110.0}])
        return pd.DataFrame(columns=["日期", "收盘"])

    monkeypatch.setattr(pipeline.ak, "stock_zh_a_hist", fake_hist)
    monkeypatch.setattr(
        pipeline.ak, "index_zh_a_hist",
        lambda **kw: pd.DataFrame(columns=["日期", "收盘"]),
    )

    pipeline.cmd_outcome_update()

    db = cache_mod.get_db()
    row = db.execute(
        "SELECT outcome_30d FROM predictions WHERE code='600036'"
    ).fetchone()
    db.close()
    # spot_em 失败不应阻止历史价更新
    assert row[0] == pytest.approx(10.0)  # (110/100-1)*100


# ---------------------------------------------------------------------------
# 20. daily：spot_em 失败时仍写入 predictions（price_at_score=NULL）
# ---------------------------------------------------------------------------
def test_daily_writes_predictions_when_spot_em_fails(
    tmp_db, small_watchlist, fake_fetcher, fake_weights, monkeypatch
):
    """spot_em 抛异常 → daily 仍写入评分记录，price_at_score=NULL。"""
    for item in small_watchlist:
        _insert_fundamentals(item["code"], item["name"], "银行", _full_data())

    monkeypatch.setattr(
        pipeline.ak, "stock_zh_a_spot_em",
        lambda: (_ for _ in ()).throw(ConnectionError("RemoteDisconnected")),
    )

    pipeline.cmd_daily()

    db = cache_mod.get_db()
    rows = db.execute(
        "SELECT code, price_at_score FROM predictions ORDER BY code"
    ).fetchall()
    db.close()
    assert len(rows) == len(small_watchlist)
    for _, price in rows:
        assert price is None  # spot_em 失败时为 NULL，但记录必须存在
