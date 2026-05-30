"""tests/test_fetcher.py — lib/fetcher.py 和 pipeline._compute_daily_pb_percentile 的单元测试。

禁止真实 AKShare 网络请求。所有外部调用通过 monkeypatch 替换。
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pandas as pd
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import lib.fetcher as fetcher_mod
import lib.cache as cache_mod
import pipeline


# ---------------------------------------------------------------------------
# _compute_gross_margin 测试
# ---------------------------------------------------------------------------

def test_gross_margin_financial_industry_skip():
    """银行/保险等金融行业直接返回 None，不调 AKShare。"""
    for industry in ["银行", "保险", "证券", "非银金融", "多元金融"]:
        result = fetcher_mod._compute_gross_margin("600036", industry)
        assert result is None, f"行业={industry} 应返回 None"


def test_gross_margin_missing_required_columns(monkeypatch):
    """利润表缺少必要列时返回 None。"""
    df = pd.DataFrame({"报告日": ["20251231"], "营业收入": [1000.0]})
    with patch("akshare.stock_financial_report_sina", return_value=df):
        result = fetcher_mod._compute_gross_margin("603606", "制造业")
    assert result is None


def test_gross_margin_insufficient_annual_data(monkeypatch):
    """年报数据不足2年时返回 None（只有1条有效年报行）。"""
    df = pd.DataFrame({
        "报告日": ["20251231"],
        "营业收入": [1000.0],
        "营业成本": [800.0],
    })
    with patch("akshare.stock_financial_report_sina", return_value=df):
        result = fetcher_mod._compute_gross_margin("603606", "制造业")
    assert result is None


def test_gross_margin_zero_revenue_rows_skipped(monkeypatch):
    """零营业收入的行应被跳过，剩余有效行≥2时正常计算。"""
    df = pd.DataFrame({
        "报告日": ["20251231", "20241231", "20231231"],
        "营业收入": [0.0, 1000.0, 1200.0],
        "营业成本": [0.0, 800.0, 900.0],
    })
    with patch("akshare.stock_financial_report_sina", return_value=df):
        result = fetcher_mod._compute_gross_margin("603606", "制造业")
    # 有效行：2024(毛利率20%) 和 2023(毛利率25%)，均值22.5%
    assert result is not None
    assert abs(result - 22.5) < 0.1


def test_gross_margin_normal_calculation(monkeypatch):
    """正常情况：3年年报数据，计算均值毛利率。"""
    df = pd.DataFrame({
        "报告日": ["20251231", "20241231", "20231231", "20250630"],  # 含半年报，应被过滤
        "营业收入": [1000.0, 800.0, 600.0, 500.0],
        "营业成本": [700.0, 560.0, 420.0, 350.0],
    })
    with patch("akshare.stock_financial_report_sina", return_value=df):
        result = fetcher_mod._compute_gross_margin("603606", "制造业")
    # 年报：2025(30%), 2024(30%), 2023(30%)，均值30%
    assert result is not None
    assert abs(result - 30.0) < 0.1


def test_gross_margin_out_of_range_returns_none(monkeypatch):
    """毛利率超出 -20%~95% 合理范围时返回 None。"""
    df = pd.DataFrame({
        "报告日": ["20251231", "20241231"],
        "营业收入": [100.0, 100.0],
        "营业成本": [200.0, 200.0],  # 成本>收入 → 毛利率 -100%（超出范围）
    })
    with patch("akshare.stock_financial_report_sina", return_value=df):
        result = fetcher_mod._compute_gross_margin("603606", "制造业")
    assert result is None


def test_gross_margin_api_error_returns_none(monkeypatch):
    """AKShare 调用返回错误时，返回 None。"""
    with patch("akshare.stock_financial_report_sina", side_effect=Exception("网络超时")):
        result = fetcher_mod._compute_gross_margin("603606", "制造业")
    assert result is None


# ---------------------------------------------------------------------------
# _compute_daily_pb_percentile 测试（pipeline.py 中的函数）
# ---------------------------------------------------------------------------

def test_daily_pb_percentile_normal():
    """正常情况：价格变化导致分位变化。"""
    hist = [1.0] * 50 + [2.0] * 50   # 100个点：一半1.0，一半2.0
    data = {"bps": 10.0, "pb_hist_monthly": hist}
    # price=25 → pb=2.5 → 高于全部100个点 → 100%
    assert pipeline._compute_daily_pb_percentile(25.0, data) == 100.0
    # price=5 → pb=0.5 → 低于全部 → 0%
    assert pipeline._compute_daily_pb_percentile(5.0, data) == 0.0
    # price=15 → pb=1.5 → 50个点在其下（那50个1.0）→ 50%
    assert pipeline._compute_daily_pb_percentile(15.0, data) == 50.0


def test_daily_pb_percentile_missing_bps():
    """缺少 bps 时返回 None。"""
    data = {"pb_hist_monthly": [1.0] * 50}
    assert pipeline._compute_daily_pb_percentile(25.0, data) is None


def test_daily_pb_percentile_bps_zero():
    """bps <= 0 时返回 None（避免除零错误）。"""
    data = {"bps": 0.0, "pb_hist_monthly": [1.0] * 50}
    assert pipeline._compute_daily_pb_percentile(25.0, data) is None
    data["bps"] = -5.0
    assert pipeline._compute_daily_pb_percentile(25.0, data) is None


def test_daily_pb_percentile_insufficient_hist():
    """历史序列不足 12 个点时返回 None。"""
    data = {"bps": 10.0, "pb_hist_monthly": [1.0] * 5}
    assert pipeline._compute_daily_pb_percentile(25.0, data) is None


def test_daily_pb_percentile_missing_hist():
    """缺少 pb_hist_monthly 时返回 None。"""
    data = {"bps": 10.0}
    assert pipeline._compute_daily_pb_percentile(25.0, data) is None


def test_daily_pb_percentile_rounding():
    """分位值四舍五入到1位小数。"""
    hist = list(range(1, 101))   # [1,2,...,100]，每个值不同
    data = {"bps": 1.0, "pb_hist_monthly": [float(x) for x in hist]}
    # price=50.5 → pb=50.5 → 50个点(1..50)在其下 → 50.0%
    result = pipeline._compute_daily_pb_percentile(50.5, data)
    assert result == 50.0


# ---------------------------------------------------------------------------
# set_fundamentals merge=True 测试
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_cache_db(tmp_path, monkeypatch):
    """把 lib.cache.DB_PATH 指向 tmp_path 下的临时数据库。"""
    import config
    db_path = str(tmp_path / "test_cache.db")
    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    cache_mod.get_db().close()
    return db_path


def test_set_fundamentals_merge_preserves_old_valid_value(tmp_cache_db):
    """merge=True：新值为 None 时保留旧缓存中的有效值。"""
    cache_mod.set_fundamentals("603606", "东方电缆", "制造业",
                               {"gross_margin": 32.5, "pb_hist_monthly": [1.0, 2.0, 3.0] * 50})
    # 模拟本次接口失败：gross_margin=None, pb_hist_monthly=None
    cache_mod.set_fundamentals("603606", "东方电缆", "制造业",
                               {"gross_margin": None, "pb_hist_monthly": None}, merge=True)
    result = cache_mod.get_fundamentals("603606")
    assert result is not None
    assert result["gross_margin"] == 32.5, "旧有效值应被保留"
    assert result["pb_hist_monthly"] is not None, "旧序列应被保留"


def test_set_fundamentals_merge_new_value_overrides(tmp_cache_db):
    """merge=True：新值不为 None 时正常覆盖旧值。"""
    cache_mod.set_fundamentals("603606", "东方电缆", "制造业",
                               {"gross_margin": 30.0})
    cache_mod.set_fundamentals("603606", "东方电缆", "制造业",
                               {"gross_margin": 35.0}, merge=True)
    result = cache_mod.get_fundamentals("603606")
    assert result["gross_margin"] == 35.0, "新有效值应覆盖旧值"


def test_set_fundamentals_no_merge_overwrites(tmp_cache_db):
    """merge=False（默认）：整行替换，不保留旧值。"""
    cache_mod.set_fundamentals("603606", "东方电缆", "制造业",
                               {"gross_margin": 32.5})
    cache_mod.set_fundamentals("603606", "东方电缆", "制造业",
                               {"gross_margin": None})
    result = cache_mod.get_fundamentals("603606")
    assert result["gross_margin"] is None, "默认行为应整行覆盖"


def test_gross_margin_sort_order_latest_first(monkeypatch):
    """sort_values('报告日', ascending=False).head(3) 应取最新3年年报（非最旧）。"""
    df = pd.DataFrame({
        "报告日": ["20211231", "20221231", "20231231", "20241231"],
        "营业收入": [500.0, 600.0, 700.0, 800.0],
        "营业成本": [400.0, 450.0, 490.0, 560.0],  # 毛利率：20%,25%,30%,30%
    })
    with patch("akshare.stock_financial_report_sina", return_value=df):
        result = fetcher_mod._compute_gross_margin("603606", "制造业")
    # 最新3年：2024(30%), 2023(30%), 2022(25%) → 均值≈28.33%
    # 如果排序错误取最旧3年：2021(20%), 2022(25%), 2023(30%) → 均值25%
    assert result is not None
    assert abs(result - 28.33) < 0.1, f"应取最新3年，期望≈28.33%，实际={result}"
