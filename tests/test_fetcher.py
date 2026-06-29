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

import lib.fetcher as fetcher_mod  # noqa: E402
import lib.cache as cache_mod  # noqa: E402
import pipeline  # noqa: E402


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


def test_report_period_is_cached_field() -> None:
    """cmd_fetch 计算出的 report_period 必须进入 cache_data，不应被 FIELDS 过滤掉。"""
    assert "report_period" in fetcher_mod.FIELDS


def test_cmd_fetch_skips_cache_write_when_no_valid_fields(monkeypatch) -> None:
    """所有外部源失败时不应写入 0 字段缓存。"""
    set_cache = patch("lib.fetcher.set_fundamentals").start()
    monkeypatch.setattr(fetcher_mod, "timed_call", lambda *a, **k: ("ERROR", "dns failed"))
    monkeypatch.setattr(fetcher_mod, "timed_call_with_retry", lambda *a, **k: ("ERROR", "dns failed"))
    monkeypatch.setattr(fetcher_mod, "get_spot_em_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(fetcher_mod, "_fetch_spot_em_safe", lambda *a, **k: ("ERROR", "dns failed"))
    monkeypatch.setattr(fetcher_mod, "_compute_gross_margin", lambda *a, **k: None)
    monkeypatch.setattr(fetcher_mod, "_fetch_pb_hist_and_percentile", lambda *a, **k: (None, []))

    try:
        with pytest.raises(RuntimeError, match="未获取到任何有效字段"):
            fetcher_mod.cmd_fetch(["603606"])
    finally:
        patch.stopall()
    set_cache.assert_not_called()


def test_fetch_spot_em_safe_returns_error_after_same_day_failure(monkeypatch) -> None:
    """同日失败记忆命中时返回明确错误，不返回 None 触发 to_dict 解析异常。"""
    monkeypatch.setattr(fetcher_mod, "_spot_em_failed_today", "2026-06-04")
    result = fetcher_mod._fetch_spot_em_safe("2026-06-04")
    assert result == ("ERROR", "spot_em 今日已失败，跳过重复拉取")


def test_cmd_fetch_uses_recent_spot_snapshot_when_today_fetch_fails(monkeypatch) -> None:
    """spot_em 今日拉取失败时，可使用最近缓存快照恢复 PE/PB/股息率价格。"""
    captured: dict = {}
    fin_df = pd.DataFrame({
        "报告期": ["2023", "2024", "2025"],
        "净资产收益率": [10.0, 11.0, 12.0],
        "净利润同比增长率": [5.0, 6.0, 7.0],
        "资产负债率": [40.0, 41.0, 42.0],
        "基本每股收益": [1.0, 1.1, 1.2],
        "每股净资产": [8.0, 8.5, 9.0],
    })
    div_df = pd.DataFrame({
        "进度": ["实施"],
        "除权除息日": ["2026-01-15"],
        "派息": [2.0],
    })
    recent_snapshot = [{
        "代码": "603606",
        "市盈率-动态": "18.5",
        "市净率": "2.1",
        "最新价": "21.0",
        "总市值": "100",
        "流通市值": "80",
    }]

    def fake_timed_call(fn, *args, **kwargs):
        if fn is fetcher_mod._fetch_info:
            return {"股票简称": "东方电缆", "行业": "电力设备", "最新": "20.0"}
        if fn is fetcher_mod._fetch_dividends:
            return div_df
        if fn is fetcher_mod._fetch_price_history:
            raise AssertionError("recent snapshot has price, should not fetch latest close")
        return ("ERROR", "unexpected")

    def fake_set_fundamentals(code, name, industry, data, ttl=None, merge=False):
        captured.update({"code": code, "name": name, "industry": industry, "data": data, "merge": merge})
        return "ok"

    monkeypatch.setattr(fetcher_mod, "timed_call", fake_timed_call)
    monkeypatch.setattr(fetcher_mod, "timed_call_with_retry", lambda *a, **k: fin_df)
    monkeypatch.setattr(fetcher_mod, "get_spot_em_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(fetcher_mod, "_fetch_spot_em_safe", lambda *a, **k: ("ERROR", "remote closed"))
    monkeypatch.setattr(fetcher_mod, "get_recent_spot_em_snapshot", lambda *a, **k: ("2026-06-03", recent_snapshot))
    monkeypatch.setattr(fetcher_mod, "_compute_gross_margin", lambda *a, **k: 25.0)
    monkeypatch.setattr(fetcher_mod, "_fetch_pb_hist_and_percentile", lambda *a, **k: (42.0, [1.0] * 24))
    monkeypatch.setattr(fetcher_mod, "set_fundamentals", fake_set_fundamentals)

    fetcher_mod.cmd_fetch(["603606"])

    data = captured["data"]
    assert captured["merge"] is True
    assert data["pe_ttm"] == 18.5
    assert data["pb"] == 2.1
    assert data["float_to_total_ratio"] == 80.0
    assert data["dividend_yield"] == 0.95


def test_cmd_fetch_falls_back_to_latest_close_for_pb_and_dividend(monkeypatch) -> None:
    """无 spot_em 快照时，用个股日线最新收盘价计算 PB，并支撑股息率。"""
    captured: dict = {}
    fin_df = pd.DataFrame({
        "报告期": ["2023", "2024", "2025"],
        "净资产收益率": [10.0, 11.0, 12.0],
        "净利润同比增长率": [5.0, 6.0, 7.0],
        "资产负债率": [40.0, 41.0, 42.0],
        "基本每股收益": [1.0, 1.1, 1.2],
        "每股净资产": [8.0, 8.0, 8.0],
    })
    div_df = pd.DataFrame({
        "进度": ["实施"],
        "除权除息日": ["2026-01-15"],
        "派息": [1.6],
    })

    def fake_timed_call(fn, *args, **kwargs):
        if fn is fetcher_mod._fetch_info:
            return ("ERROR", "info failed")
        if fn is fetcher_mod._fetch_price_history:
            return pd.DataFrame({"收盘": [11.5, 12.0]})
        if fn is fetcher_mod._fetch_dividends:
            return div_df
        return ("ERROR", "unexpected")

    def fake_set_fundamentals(code, name, industry, data, ttl=None, merge=False):
        captured.update({"code": code, "name": name, "industry": industry, "data": data, "merge": merge})
        return "ok"

    monkeypatch.setattr(fetcher_mod, "timed_call", fake_timed_call)
    monkeypatch.setattr(fetcher_mod, "timed_call_with_retry", lambda *a, **k: fin_df)
    monkeypatch.setattr(fetcher_mod, "get_spot_em_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(fetcher_mod, "_fetch_spot_em_safe", lambda *a, **k: ("ERROR", "remote closed"))
    monkeypatch.setattr(fetcher_mod, "get_recent_spot_em_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(fetcher_mod, "_compute_gross_margin", lambda *a, **k: 25.0)
    monkeypatch.setattr(fetcher_mod, "_fetch_pb_hist_and_percentile", lambda *a, **k: (42.0, [1.0] * 24))
    monkeypatch.setattr(fetcher_mod, "set_fundamentals", fake_set_fundamentals)

    fetcher_mod.cmd_fetch(["603606"])

    data = captured["data"]
    assert data["pb"] == 1.5
    assert data["dividend_yield"] == 1.33
    assert "pe_ttm" in data
    assert data["pe_ttm"] is None


def test_fetch_latest_close_returns_reason_when_close_series_has_no_valid_values(monkeypatch) -> None:
    """日线 fallback 有行但收盘列全无效时，应返回失败原因而不是抛 IndexError。"""
    def fake_timed_call(fn, *args, **kwargs):
        return pd.DataFrame({"收盘": [None, "", "bad"]})

    monkeypatch.setattr(fetcher_mod, "timed_call", fake_timed_call)

    close, reason = fetcher_mod._fetch_latest_close("603606", "2026-06-05")

    assert close is None
    assert reason == "日线收盘价无有效数据"


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
# _detect_split_ratio 测试
# ---------------------------------------------------------------------------

def _make_fhps_df(ratio: float, ex_date: str, progress: str = "实施分配") -> pd.DataFrame:
    """构造 stock_fhps_detail_em 风格的 DataFrame。"""
    return pd.DataFrame({
        "送转股份-送转总比例": [ratio],
        "除权除息日":          [ex_date],
        "方案进度":            [progress],
    })


def test_detect_split_ratio_no_data():
    """None/empty 输入返回 (0.0, None)。"""
    assert fetcher_mod._detect_split_ratio(None, 2025) == (0.0, None)
    assert fetcher_mod._detect_split_ratio(pd.DataFrame(), 2025) == (0.0, None)
    assert fetcher_mod._detect_split_ratio(_make_fhps_df(2.0, "2026-05-26"), None) == (0.0, None)


def test_detect_split_ratio_normal():
    """10转2（比例=2）在报告年后实施 → 返回 (0.2, 除权日)。"""
    fhps_df = _make_fhps_df(ratio=2.0, ex_date="2026-05-26")
    ratio, ex_date = fetcher_mod._detect_split_ratio(fhps_df, latest_report_year=2025)
    assert abs(ratio - 0.2) < 1e-5
    assert ex_date == "2026-05-26"


def test_detect_split_ratio_future_ex_date_ignored():
    """除权日在今天之后的送转不计入。"""
    fhps_df = _make_fhps_df(ratio=2.0, ex_date="2099-01-01")
    ratio, ex_date = fetcher_mod._detect_split_ratio(fhps_df, latest_report_year=2025)
    assert ratio == 0.0
    assert ex_date is None


def test_detect_split_ratio_before_report_year_ignored():
    """除权日在年报截止日（2025-12-31）之前的送转不计入。"""
    fhps_df = _make_fhps_df(ratio=2.0, ex_date="2025-06-01")
    ratio, _ = fetcher_mod._detect_split_ratio(fhps_df, latest_report_year=2025)
    assert ratio == 0.0


def test_detect_split_ratio_not_implemented_ignored():
    """方案进度非"实施分配"的记录不计入。"""
    fhps_df = _make_fhps_df(ratio=2.0, ex_date="2026-05-26", progress="董事会预案")
    ratio, _ = fetcher_mod._detect_split_ratio(fhps_df, latest_report_year=2025)
    assert ratio == 0.0


def test_bps_adjusted_in_cmd_fetch(monkeypatch) -> None:
    """cmd_fetch 检测到送转后，results['bps'] 应按比例缩减（10转2 → bps/1.2）。"""
    captured: dict = {}
    fin_df = pd.DataFrame({
        "报告期": ["2023", "2024", "2025"],
        "净资产收益率": [10.0, 11.0, 12.0],
        "净利润同比增长率": [5.0, 6.0, 7.0],
        "资产负债率": [40.0, 41.0, 42.0],
        "基本每股收益": [1.0, 1.1, 1.2],
        "每股净资产": [8.0, 9.0, 12.04],
    })
    fhps_df = _make_fhps_df(ratio=2.0, ex_date="2026-05-26")
    div_df = pd.DataFrame({
        "进度": ["实施"],
        "除权除息日": ["2026-01-15"],
        "派息": [2.0],
    })
    snapshot = [{
        "代码": "603606",
        "市盈率-动态": "27.0",
        "市净率": "4.1",
        "最新价": "42.0",
        "总市值": "100",
        "流通市值": "80",
    }]

    def fake_timed_call(fn, *args, **kwargs):
        if fn is fetcher_mod._fetch_info:
            return {"股票简称": "东方电缆", "行业": "电力设备", "最新": "42.0"}
        if fn is fetcher_mod._fetch_fhps_detail:
            return fhps_df
        if fn is fetcher_mod._fetch_dividends:
            return div_df
        return ("ERROR", "unexpected")

    monkeypatch.setattr(fetcher_mod, "timed_call", fake_timed_call)
    monkeypatch.setattr(fetcher_mod, "timed_call_with_retry", lambda *a, **k: fin_df)
    monkeypatch.setattr(fetcher_mod, "get_spot_em_snapshot", lambda *a, **k: snapshot)
    monkeypatch.setattr(fetcher_mod, "_compute_gross_margin", lambda *a, **k: 30.0)
    monkeypatch.setattr(fetcher_mod, "_fetch_pb_hist_and_percentile", lambda *a, **k: (20.0, [1.0] * 24))
    monkeypatch.setattr(
        fetcher_mod, "set_fundamentals",
        lambda code, name, industry, data, ttl=None, merge=False: captured.update({"data": data}) or "ok",
    )

    fetcher_mod.cmd_fetch(["603606"])

    bps_stored = captured["data"]["bps"]
    expected_bps = round(12.04 / 1.2, 4)
    assert abs(bps_stored - expected_bps) < 1e-4, (
        f"BPS 未正确复权：期望 {expected_bps}，实际 {bps_stored}"
    )


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
                               {
                                   "gross_margin": 32.5,
                                   "pb_hist_monthly": [1.0, 2.0, 3.0] * 50,
                                   "report_period": "2025-12-31",
                               })
    # 模拟本次接口失败：gross_margin=None, pb_hist_monthly=None
    cache_mod.set_fundamentals("603606", "东方电缆", "制造业",
                               {"gross_margin": None, "pb_hist_monthly": None}, merge=True)
    result = cache_mod.get_fundamentals("603606")
    assert result is not None
    assert result["gross_margin"] == 32.5, "旧有效值应被保留"
    assert result["pb_hist_monthly"] is not None, "旧序列应被保留"
    assert result["report_period"] == "2025-12-31", "本次未返回的旧字段应被保留"


def test_set_fundamentals_merge_preserves_existing_metadata(tmp_cache_db):
    """merge=True：基本信息接口失败时不应用代码/未知覆盖旧名称和行业。"""
    cache_mod.set_fundamentals("603606", "东方电缆", "电力设备", {"pb": 2.1})
    cache_mod.set_fundamentals("603606", "603606", "未知", {"pb": 2.2}, merge=True)
    result = cache_mod.get_fundamentals("603606")
    assert result is not None
    assert result["_cache_meta"]["name"] == "东方电缆"
    assert result["_cache_meta"]["industry"] == "电力设备"
    assert result["pb"] == 2.2


def test_set_fundamentals_merge_recomputes_ttl_after_restoring_industry(tmp_cache_db):
    """merge=True 恢复旧行业后，TTL 应按恢复后的行业重新计算。"""
    cache_mod.set_fundamentals("000858", "五粮液", "白酒", {"pb": 2.1})
    cache_mod.set_fundamentals("000858", "000858", "未知", {"pb": 2.2}, merge=True)

    result = cache_mod.get_fundamentals("000858")

    assert result is not None
    assert result["_cache_meta"]["industry"] == "白酒"
    assert result["_cache_meta"]["ttl_hours"] == 48


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


# ---------------------------------------------------------------------------
# timed_call_with_retry / avg_of 回归测试（2026-06-29 修复）
# ---------------------------------------------------------------------------

def test_timed_call_with_retry_max_retries_zero_returns_not_raises():
    """max_retries=0 时不应抛 UnboundLocalError，应返回失败哨兵而非崩溃。"""
    def always_fail() -> str:
        return "TIMEOUT"

    result = fetcher_mod.timed_call_with_retry(always_fail, max_retries=0, timeout=1)
    assert isinstance(result, (str, tuple)), (
        f"max_retries=0 应返回失败哨兵（str/tuple），实际得到 {type(result)}"
    )


def test_avg_of_none_series_returns_none():
    """series=None 时应直接返回 None，不抛 AttributeError。"""
    result = fetcher_mod.avg_of(None, 3)
    assert result is None
