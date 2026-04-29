"""tests/test_scorer.py — scorer.py 的 11 个单元测试。

所有测试直接构造最小 weights dict 传入 score_stock()，
不读取真实 weights.json，不发起任何网络请求。
"""
import pytest
from scorer import (
    InsufficientDataError,
    UnsupportedFrameworkError,
    score_stock,
)

# ---------------------------------------------------------------------------
# 共用的最小 weights dict（与 weights.json 保持一致）
# ---------------------------------------------------------------------------
WEIGHTS = {
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
    }
}

# 设计文档 §评分示例 中给出的完整数据集
FULL_DATA = {
    "roe_3y_avg": 16.5,
    "net_profit_growth": 5.5,
    "debt_ratio": 91.2,
    "gross_margin": 56.8,
    "pe_percentile_10y": 18.0,
}


# ---------------------------------------------------------------------------
# 1. 全字段正常路径
# ---------------------------------------------------------------------------
def test_happy_path_all_fields():
    """5 个非固定字段全有，验证 total_score ≈ 52.2（设计文档示例）。"""
    result = score_stock("600036", "A", FULL_DATA, weights=WEIGHTS)

    # 设计文档标注 total_score ≈ 52.2
    assert abs(result["total_score"] - 52.15) < 0.1
    assert result["data_quality"] == 1.0
    assert result["missing_fields"] == []
    assert "quant_score" in result
    assert "component_scores" in result


# ---------------------------------------------------------------------------
# 2. 不支持的 framework
# ---------------------------------------------------------------------------
def test_unsupported_framework():
    """未实现的 framework 应抛出 UnsupportedFrameworkError。"""
    with pytest.raises(UnsupportedFrameworkError):
        score_stock("600036", "Z", FULL_DATA, weights=WEIGHTS)


# ---------------------------------------------------------------------------
# 3. 数据不足（< 0.5）
# ---------------------------------------------------------------------------
def test_insufficient_data():
    """5 个非固定字段中仅 2 个有值 → data_quality=0.4 < 0.5 → InsufficientDataError。"""
    sparse = {
        "roe_3y_avg": 15.0,
        "net_profit_growth": 10.0,
        "debt_ratio": None,
        "gross_margin": None,
        "pe_percentile_10y": None,
    }
    with pytest.raises(InsufficientDataError):
        score_stock("600036", "A", sparse, weights=WEIGHTS)


# ---------------------------------------------------------------------------
# 4. 边界 data_quality（恰好 >= 0.5）
# ---------------------------------------------------------------------------
def test_boundary_data_quality():
    """3/5 字段有值 → data_quality=0.6 ≥ 0.5，应正常返回（不抛异常）。"""
    data = {
        "roe_3y_avg": 15.0,
        "net_profit_growth": 10.0,
        "debt_ratio": 40.0,
        "gross_margin": None,
        "pe_percentile_10y": None,
    }
    result = score_stock("600036", "A", data, weights=WEIGHTS)
    assert result["data_quality"] == pytest.approx(0.6)
    assert set(result["missing_fields"]) == {"gross_margin", "pe_percentile_10y"}


# ---------------------------------------------------------------------------
# 5. breakpoints 插值
# ---------------------------------------------------------------------------
def test_breakpoint_interpolation():
    """ROE=10% 在 [8,5]→[12,9] 之间，线性插值应得 7.0 分。"""
    data = {**FULL_DATA, "roe_3y_avg": 10.0}
    result = score_stock("600036", "A", data, weights=WEIGHTS)
    # 5 + (10-8)/(12-8) * (9-5) = 5 + 0.5*4 = 7.0
    assert result["component_scores"]["roe_3y_avg"] == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# 6. 低于最小 breakpoint
# ---------------------------------------------------------------------------
def test_breakpoint_below_min():
    """ROE=-5% 低于最小 breakpoint[0]=0，应取最小 score=0。"""
    data = {**FULL_DATA, "roe_3y_avg": -5.0}
    result = score_stock("600036", "A", data, weights=WEIGHTS)
    assert result["component_scores"]["roe_3y_avg"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 7. 高于最大 breakpoint
# ---------------------------------------------------------------------------
def test_breakpoint_above_max():
    """ROE=30% 超过最大 breakpoint[-1]=25，应取最大 score=15。"""
    data = {**FULL_DATA, "roe_3y_avg": 30.0}
    result = score_stock("600036", "A", data, weights=WEIGHTS)
    assert result["component_scores"]["roe_3y_avg"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# 8. invert 字段低值端（低分位 → 高分）
# ---------------------------------------------------------------------------
def test_invert_field_low_value():
    """pe_percentile_10y=5（历史最低分位），breakpoints 第一点 [5,15]，应得满分 15。"""
    data = {**FULL_DATA, "pe_percentile_10y": 5.0}
    result = score_stock("600036", "A", data, weights=WEIGHTS)
    assert result["component_scores"]["pe_percentile_10y"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# 9. invert 字段高值端（高分位 → 低分）
# ---------------------------------------------------------------------------
def test_invert_field_high_value():
    """pe_percentile_10y=80（历史最高分位），breakpoints 最后点 [80,0]，应得 0 分。"""
    data = {**FULL_DATA, "pe_percentile_10y": 80.0}
    result = score_stock("600036", "A", data, weights=WEIGHTS)
    assert result["component_scores"]["pe_percentile_10y"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 10. None 字段计分为 0，且出现在 missing_fields
# ---------------------------------------------------------------------------
def test_none_field_scores_zero():
    """gross_margin=None → component_scores['gross_margin']=0，且在 missing_fields 中。"""
    data = {**FULL_DATA, "gross_margin": None}
    result = score_stock("600036", "A", data, weights=WEIGHTS)
    assert result["component_scores"]["gross_margin"] == pytest.approx(0.0)
    assert "gross_margin" in result["missing_fields"]


# ---------------------------------------------------------------------------
# 11. Phase 3：data 字典可覆盖固定字段；无值时回落 phase1_fixed
# ---------------------------------------------------------------------------
def test_phase3_fixed_field_override():
    """Phase 3 后，data 里提供的值会覆盖 phase1_fixed；未提供时回落默认值。"""
    # 有值时使用 data 里的值（Gemini 注入场景）
    data_with_gemini = {**FULL_DATA, "moat_fixed": 8, "market_pos_fixed": 4, "sentiment_fixed": 4}
    result = score_stock("600036", "A", data_with_gemini, weights=WEIGHTS)
    cs = result["component_scores"]
    assert cs["moat_fixed"] == pytest.approx(8.0)
    assert cs["market_pos_fixed"] == pytest.approx(4.0)
    assert cs["sentiment_fixed"] == pytest.approx(4.0)

    # 无值时回落到 phase1_fixed（fallback 场景）
    result2 = score_stock("600036", "A", FULL_DATA, weights=WEIGHTS)
    cs2 = result2["component_scores"]
    assert cs2["moat_fixed"] == pytest.approx(5.0)
    assert cs2["market_pos_fixed"] == pytest.approx(2.0)
    assert cs2["sentiment_fixed"] == pytest.approx(3.0)
