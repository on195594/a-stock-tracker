"""Tests for the packaged Gemini qualitative-scoring integration.

所有测试 mock _call_gemini 或 _check_cache/_write_cache，不发起真实网络请求。
"""

import json
from unittest.mock import patch


# ---------------------------------------------------------------------------
# 1. 正常路径：Gemini 返回合法 JSON，写入缓存并返回
# ---------------------------------------------------------------------------
def test_normal_path():
    """Gemini 正常返回，应写缓存并返回解析后的 dict。"""
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer

    with (
        patch.object(gemini_scorer, "_check_cache", return_value=None),
        patch.object(gemini_scorer, "_call_gemini", return_value={"moat": 7, "market_pos": 4, "sentiment": 3}),
        patch.object(gemini_scorer, "_write_cache") as mock_write,
    ):
        result = gemini_scorer.get_qualitative_score("600036", "招商银行")

    assert result == {"moat": 7, "market_pos": 4, "sentiment": 3}
    mock_write.assert_called_once_with("600036", {"moat": 7, "market_pos": 4, "sentiment": 3})


# ---------------------------------------------------------------------------
# 2. 超时：_call_gemini 返回 None，触发 all-or-nothing fallback
# ---------------------------------------------------------------------------
def test_timeout_fallback():
    """_call_gemini 返回 None（超时场景），应返回完整 FALLBACK 值且不写缓存。"""
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer

    with (
        patch.object(gemini_scorer, "_check_cache", return_value=None),
        patch.object(gemini_scorer, "_call_gemini", return_value=None),
        patch.object(gemini_scorer, "_write_cache") as mock_write,
    ):
        result = gemini_scorer.get_qualitative_score("600519", "贵州茅台")

    assert result == {"moat": 5, "market_pos": 2, "sentiment": 3}
    mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# 3. 非 JSON 响应：urlopen 返回非 JSON 文本，_call_gemini 应捕获并返回 None
# ---------------------------------------------------------------------------
def test_invalid_json_response(monkeypatch):
    """Gemini API 返回非 JSON（如纯文本），_call_gemini 应捕获 JSONDecodeError 并 fallback。"""
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    fake_body = json.dumps({"candidates": [{"content": {"parts": [{"text": "对不起，我无法完成该请求。"}]}}]}).encode()

    with (
        patch("urllib.request.urlopen") as mock_urlopen,
        patch.object(gemini_scorer, "_check_cache", return_value=None),
        patch.object(gemini_scorer, "_write_cache") as mock_write,
    ):
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
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer

    # moat 超过 10
    assert gemini_scorer._validate({"moat": 11, "market_pos": 4, "sentiment": 3}) is None
    # market_pos 超过 5
    assert gemini_scorer._validate({"moat": 7, "market_pos": 8, "sentiment": 3}) is None
    # sentiment 为 0（低于下限 1）
    assert gemini_scorer._validate({"moat": 7, "market_pos": 4, "sentiment": 0}) is None
    # 非整数类型
    assert gemini_scorer._validate({"moat": 7.5, "market_pos": 4, "sentiment": 3}) is None
    # 合法值应通过
    assert gemini_scorer._validate({"moat": 7, "market_pos": 4, "sentiment": 3}) == {
        "moat": 7,
        "market_pos": 4,
        "sentiment": 3,
    }


# ---------------------------------------------------------------------------
# 5. 缓存命中：30 天内有记录时，不调用 Gemini API
# ---------------------------------------------------------------------------
def test_cache_hit_skips_api():
    """qualitative_scores 有 30 天内记录时，_call_gemini 不应被调用。"""
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer

    cached = {"moat": 6, "market_pos": 3, "sentiment": 4}
    with (
        patch.object(gemini_scorer, "_check_cache", return_value=cached),
        patch.object(gemini_scorer, "_call_gemini") as mock_api,
    ):
        result = gemini_scorer.get_qualitative_score("601318", "中国平安")

    assert result == cached
    mock_api.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Gemini 返回 markdown 包装的 JSON（如 ```json{...}```）应被正确剥离解析
# ---------------------------------------------------------------------------
def test_markdown_code_block_stripped(monkeypatch):
    """Gemini 偶尔以 ```json\n{...}\n``` 包装返回值，_call_gemini 应剥离 markdown 后正常解析。"""
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    inner = json.dumps({"moat": 8, "market_pos": 4, "sentiment": 4})
    wrapped = f"```json\n{inner}\n```"
    fake_body = json.dumps({"candidates": [{"content": {"parts": [{"text": wrapped}]}}]}).encode()

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = lambda s, *a: False
        mock_urlopen.return_value.read.return_value = fake_body

        result = gemini_scorer._call_gemini("600036", "招商银行")

    assert result == {"moat": 8, "market_pos": 4, "sentiment": 4}


# ---------------------------------------------------------------------------
# 7. _check_cache 多行时返回最新记录
# ---------------------------------------------------------------------------
def test_check_cache_multiple_rows_returns_latest(tmp_path, monkeypatch):
    """qualitative_scores 同一 code 有多行时，应返回 scored_date 最新的记录。"""
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer
    from a_stock_tracker.data import cache as cache_mod
    from datetime import date, timedelta

    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)

    db = cache_mod.get_db()
    old_date = (date.today() - timedelta(days=25)).isoformat()
    new_date = (date.today() - timedelta(days=1)).isoformat()
    db.execute(
        "INSERT INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date) VALUES ('600036', 5, 2, 3, ?)",
        (old_date,),
    )
    db.execute(
        "INSERT INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date) VALUES ('600036', 8, 4, 4, ?)",
        (new_date,),
    )
    db.commit()
    db.close()

    result = gemini_scorer._check_cache("600036")
    assert result is not None
    assert result["moat"] == 8  # 最新行（new_date）


# ---------------------------------------------------------------------------
# 8. _write_cache INSERT OR IGNORE：同日重复写入不覆盖原有记录
# ---------------------------------------------------------------------------
def test_write_cache_insert_ignore_same_date(tmp_path, monkeypatch):
    """同 code + scored_date 写入两次，INSERT OR IGNORE 应保留第一次的值。"""
    from a_stock_tracker.data import cache as cache_mod

    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
    cache_mod.get_db().close()  # 建表

    # 直接操作 DB 写入两条相同 (code, scored_date) 记录，验证第二条被 IGNORE
    db = cache_mod.get_db()
    db.execute(
        "INSERT OR IGNORE INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date)"
        " VALUES ('600036', 7, 3, 4, '2026-05-12')"
    )
    db.execute(
        "INSERT OR IGNORE INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date)"
        " VALUES ('600036', 9, 5, 5, '2026-05-12')"  # 同日，应被 IGNORE
    )
    db.commit()

    rows = db.execute("SELECT moat FROM qualitative_scores WHERE code='600036'").fetchall()
    db.close()
    assert len(rows) == 1
    assert rows[0][0] == 7  # 保留第一次写入的值


# ---------------------------------------------------------------------------
# 9. _check_cache 过期测试
# ---------------------------------------------------------------------------
def test_check_cache_expired(tmp_path, monkeypatch):
    """超过 30 天的缓存应返回 None。"""
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer
    from a_stock_tracker.data import cache as cache_mod
    from datetime import date, timedelta

    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)

    db = cache_mod.get_db()
    old_date = (date.today() - timedelta(days=31)).isoformat()
    db.execute(
        "INSERT INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date) VALUES ('600036', 5, 2, 3, ?)",
        (old_date,),
    )
    db.commit()
    db.close()

    assert gemini_scorer._check_cache("600036") is None


# ---------------------------------------------------------------------------
# 10. _check_cache_stale 测试
# ---------------------------------------------------------------------------
def test_check_cache_stale(tmp_path, monkeypatch):
    """无论是否过期，_check_cache_stale 都应返回最新值，不存在时返回 None。"""
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer
    from a_stock_tracker.data import cache as cache_mod
    from datetime import date, timedelta

    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)

    assert gemini_scorer._check_cache_stale("000001") is None

    db = cache_mod.get_db()
    old_date = (date.today() - timedelta(days=50)).isoformat()
    db.execute(
        "INSERT INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date) VALUES ('000001', 9, 4, 4, ?)",
        (old_date,),
    )
    db.commit()
    db.close()

    result = gemini_scorer._check_cache_stale("000001")
    assert result == {"moat": 9, "market_pos": 4, "sentiment": 4}


# ---------------------------------------------------------------------------
# 11. _call_gemini API Key 缺失
# ---------------------------------------------------------------------------
def test_call_gemini_empty_api_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer

    assert gemini_scorer._call_gemini("000001", "平安银行") is None


# ---------------------------------------------------------------------------
# 12. _call_gemini HTTP Errors (Retries & Auth)
# ---------------------------------------------------------------------------
def test_call_gemini_http_errors(monkeypatch):
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer
    import urllib.error

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(gemini_scorer, "GEMINI_RETRY_DELAYS", (0, 0))  # Speed up

    # 401 Auth error -> returns None immediately
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 401, "Auth", {}, None)) as mock_auth:
        assert gemini_scorer._call_gemini("000001", "Bank") is None
        assert mock_auth.call_count == 1

    # 500 Server error -> retries then returns None
    with patch(
        "urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 500, "Error", {}, None)
    ) as mock_server_err:
        assert gemini_scorer._call_gemini("000001", "Bank") is None
        assert mock_server_err.call_count == gemini_scorer.MAX_GEMINI_RETRIES

    # 400 Bad Request -> No retry, returns None
    with patch(
        "urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 400, "Bad Req", {}, None)
    ) as mock_bad_req:
        assert gemini_scorer._call_gemini("000001", "Bank") is None
        assert mock_bad_req.call_count == 1


# ---------------------------------------------------------------------------
# 13. _call_gemini Generic Exception
# ---------------------------------------------------------------------------
def test_call_gemini_generic_exception(monkeypatch):
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    with patch("urllib.request.urlopen", side_effect=Exception("Unknown Error")):
        assert gemini_scorer._call_gemini("000001", "Bank") is None


# ---------------------------------------------------------------------------
# 14. get_qualitative_score 兜底旧缓存
# ---------------------------------------------------------------------------
def test_get_qualitative_score_stale_fallback():
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer

    stale_data = {"moat": 8, "market_pos": 3, "sentiment": 2}
    with (
        patch.object(gemini_scorer, "_check_cache", return_value=None),
        patch.object(gemini_scorer, "_call_gemini", return_value=None),
        patch.object(gemini_scorer, "_check_cache_stale", return_value=stale_data),
    ):
        result = gemini_scorer.get_qualitative_score("000001", "Bank")
        assert result == stale_data


# ---------------------------------------------------------------------------
# 15. _check_cache 空结果
# ---------------------------------------------------------------------------
def test_check_cache_empty(tmp_path, monkeypatch):
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer
    from a_stock_tracker.data import cache as cache_mod

    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
    assert gemini_scorer._check_cache("999999") is None


# ---------------------------------------------------------------------------
# 16. _write_cache 执行写入
# ---------------------------------------------------------------------------
def test_write_cache_execution(tmp_path, monkeypatch):
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer
    from a_stock_tracker.data import cache as cache_mod

    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)

    gemini_scorer._write_cache("000001", {"moat": 8, "market_pos": 4, "sentiment": 3})

    db = cache_mod.get_db()
    row = db.execute("SELECT moat, market_pos, sentiment FROM qualitative_scores WHERE code='000001'").fetchone()
    db.close()
    assert row == (8, 4, 3)


# ---------------------------------------------------------------------------
# 17. _call_gemini TimeoutError
# ---------------------------------------------------------------------------
def test_call_gemini_timeout(monkeypatch):
    import a_stock_tracker.integrations.gemini_scorer as gemini_scorer

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    with patch("urllib.request.urlopen", side_effect=TimeoutError):
        assert gemini_scorer._call_gemini("000001", "Bank") is None
