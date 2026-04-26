"""tests/test_gemini_scorer.py — gemini_scorer 的 5 个单元测试。

所有测试 mock _call_gemini 或 _check_cache/_write_cache，不发起真实网络请求。
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# 1. 正常路径：Gemini 返回合法 JSON，写入缓存并返回
# ---------------------------------------------------------------------------
def test_normal_path(tmp_path, monkeypatch):
    """Gemini 正常返回，应写缓存并返回解析后的 dict。"""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("gemini_scorer.DB_PATH" if hasattr(__import__("gemini_scorer"), "DB_PATH") else "lib.cache.DB_PATH",
                        str(tmp_path / "test.db"), raising=False)

    import gemini_scorer
    gemini_scorer_module = gemini_scorer

    with patch.object(gemini_scorer_module, "_check_cache", return_value=None), \
         patch.object(gemini_scorer_module, "_call_gemini",
                      return_value={"moat": 7, "market_pos": 4, "sentiment": 3}), \
         patch.object(gemini_scorer_module, "_write_cache") as mock_write:
        result = gemini_scorer_module.get_qualitative_score("600036", "招商银行")

    assert result == {"moat": 7, "market_pos": 4, "sentiment": 3}
    mock_write.assert_called_once_with("600036", {"moat": 7, "market_pos": 4, "sentiment": 3})


# ---------------------------------------------------------------------------
# 2. 超时：_call_gemini 返回 None，触发 all-or-nothing fallback
# ---------------------------------------------------------------------------
def test_timeout_fallback():
    """_call_gemini 返回 None（超时场景），应返回完整 FALLBACK 值。"""
    import gemini_scorer

    with patch.object(gemini_scorer, "_check_cache", return_value=None), \
         patch.object(gemini_scorer, "_call_gemini", return_value=None), \
         patch.object(gemini_scorer, "_write_cache") as mock_write:
        result = gemini_scorer.get_qualitative_score("600519", "贵州茅台")

    assert result == {"moat": 5, "market_pos": 2, "sentiment": 3}
    mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# 3. 非 JSON 响应：_validate 返回 None，触发 fallback
# ---------------------------------------------------------------------------
def test_invalid_json_fallback():
    """Gemini 响应非 JSON（_validate 返回 None），应返回 FALLBACK。"""
    import gemini_scorer

    with patch.object(gemini_scorer, "_check_cache", return_value=None), \
         patch.object(gemini_scorer, "_call_gemini", return_value=None), \
         patch.object(gemini_scorer, "_write_cache") as mock_write:
        result = gemini_scorer.get_qualitative_score("000858", "五粮液")

    assert result == gemini_scorer.FALLBACK
    mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# 4. 值越界：_validate 应拒绝越界值
# ---------------------------------------------------------------------------
def test_validate_rejects_out_of_range():
    """_validate 对越界值应返回 None（触发 all-or-nothing fallback）。"""
    import gemini_scorer

    # moat 超过 10
    assert gemini_scorer._validate({"moat": 11, "market_pos": 4, "sentiment": 3}) is None
    # market_pos 超过 5
    assert gemini_scorer._validate({"moat": 7, "market_pos": 8, "sentiment": 3}) is None
    # sentiment 为 0（低于下限 1）
    assert gemini_scorer._validate({"moat": 7, "market_pos": 4, "sentiment": 0}) is None
    # 非整数类型
    assert gemini_scorer._validate({"moat": 7.5, "market_pos": 4, "sentiment": 3}) is None
    # 合法值应通过
    assert gemini_scorer._validate({"moat": 7, "market_pos": 4, "sentiment": 3}) == \
           {"moat": 7, "market_pos": 4, "sentiment": 3}


# ---------------------------------------------------------------------------
# 5. 缓存命中：30 天内有记录时，不调用 Gemini API
# ---------------------------------------------------------------------------
def test_cache_hit_skips_api():
    """qualitative_scores 有 30 天内记录时，_call_gemini 不应被调用。"""
    import gemini_scorer

    cached = {"moat": 6, "market_pos": 3, "sentiment": 4}
    with patch.object(gemini_scorer, "_check_cache", return_value=cached), \
         patch.object(gemini_scorer, "_call_gemini") as mock_api:
        result = gemini_scorer.get_qualitative_score("601318", "中国平安")

    assert result == cached
    mock_api.assert_not_called()
