"""tests/test_gemini_scorer.py — gemini_scorer 的 5 个单元测试。

所有测试 mock _call_gemini 或 _check_cache/_write_cache，不发起真实网络请求。
"""
import io
import json
from unittest.mock import patch


# ---------------------------------------------------------------------------
# 1. 正常路径：Gemini 返回合法 JSON，写入缓存并返回
# ---------------------------------------------------------------------------
def test_normal_path():
    """Gemini 正常返回，应写缓存并返回解析后的 dict。"""
    import gemini_scorer

    with patch.object(gemini_scorer, "_check_cache", return_value=None), \
         patch.object(gemini_scorer, "_call_gemini",
                      return_value={"moat": 7, "market_pos": 4, "sentiment": 3}), \
         patch.object(gemini_scorer, "_write_cache") as mock_write:
        result = gemini_scorer.get_qualitative_score("600036", "招商银行")

    assert result == {"moat": 7, "market_pos": 4, "sentiment": 3}
    mock_write.assert_called_once_with("600036", {"moat": 7, "market_pos": 4, "sentiment": 3})


# ---------------------------------------------------------------------------
# 2. 超时：_call_gemini 返回 None，触发 all-or-nothing fallback
# ---------------------------------------------------------------------------
def test_timeout_fallback():
    """_call_gemini 返回 None（超时场景），应返回完整 FALLBACK 值且不写缓存。"""
    import gemini_scorer

    with patch.object(gemini_scorer, "_check_cache", return_value=None), \
         patch.object(gemini_scorer, "_call_gemini", return_value=None), \
         patch.object(gemini_scorer, "_write_cache") as mock_write:
        result = gemini_scorer.get_qualitative_score("600519", "贵州茅台")

    assert result == {"moat": 5, "market_pos": 2, "sentiment": 3}
    mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# 3. 非 JSON 响应：urlopen 返回非 JSON 文本，_call_gemini 应捕获并返回 None
# ---------------------------------------------------------------------------
def test_invalid_json_response(monkeypatch):
    """Gemini API 返回非 JSON（如纯文本），_call_gemini 应捕获 JSONDecodeError 并 fallback。"""
    import gemini_scorer

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    fake_body = json.dumps({
        "candidates": [{"content": {"parts": [{"text": "对不起，我无法完成该请求。"}]}}]
    }).encode()

    with patch("urllib.request.urlopen") as mock_urlopen, \
         patch.object(gemini_scorer, "_check_cache", return_value=None), \
         patch.object(gemini_scorer, "_write_cache") as mock_write:
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = lambda s, *a: False
        mock_urlopen.return_value.read.return_value = fake_body

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
