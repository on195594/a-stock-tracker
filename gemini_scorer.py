"""
Gemini 定性评分模块。
对 moat / market_pos / sentiment 三个字段调用 Gemini 2.0 Flash，结果缓存 30 天。
任何字段缺失/越界/超时/非JSON → all-or-nothing fallback 到 phase1_fixed 值。
"""
import json
import logging
import os
import urllib.request
import urllib.error
from datetime import date, timedelta

from lib.cache import get_db

logger = logging.getLogger(__name__)

# phase1_fixed 兜底值（all-or-nothing fallback 时使用）
FALLBACK = {"moat": 5, "market_pos": 2, "sentiment": 3}

# 每个字段的合法整数范围（与 weights.json max_score 保持一致）
VALID_RANGES = {"moat": (1, 10), "market_pos": (1, 5), "sentiment": (1, 5)}

CACHE_TTL_DAYS = 30
GEMINI_TIMEOUT_S = 10
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


def _check_cache(code: str) -> dict | None:
    """查询 qualitative_scores 缓存，30天内有效则返回，否则 None。"""
    db = get_db()
    row = db.execute(
        "SELECT moat, market_pos, sentiment, scored_date FROM qualitative_scores WHERE code=?",
        (code,),
    ).fetchone()
    db.close()
    if row is None:
        return None
    moat, market_pos, sentiment, scored_date = row
    if date.today() - date.fromisoformat(scored_date) > timedelta(days=CACHE_TTL_DAYS):
        return None
    return {"moat": moat, "market_pos": market_pos, "sentiment": sentiment}


def _write_cache(code: str, scores: dict) -> None:
    db = get_db()
    db.execute(
        """INSERT OR REPLACE INTO qualitative_scores
           (code, moat, market_pos, sentiment, scored_date) VALUES (?,?,?,?,?)""",
        (code, scores["moat"], scores["market_pos"], scores["sentiment"], date.today().isoformat()),
    )
    db.commit()
    db.close()


def _validate(raw: dict) -> dict | None:
    """验证 Gemini 返回的三个字段。任一不合法返回 None（触发 all-or-nothing fallback）。"""
    for field, (lo, hi) in VALID_RANGES.items():
        val = raw.get(field)
        if not isinstance(val, int) or not (lo <= val <= hi):
            logger.warning(f"Gemini 字段 {field}={val!r} 不合法（期望 {lo}-{hi} 整数），触发 fallback")
            return None
    return {k: raw[k] for k in VALID_RANGES}


def _call_gemini(code: str, name: str) -> dict | None:
    """调用 Gemini API，返回验证通过的 dict 或 None（失败时）。"""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.error("GEMINI_API_KEY 未设置，跳过 Gemini 评分")
        return None

    prompt = (
        f"你是一位 A 股研究员。请对以下股票的三个维度给出整数评分，返回纯 JSON，不要其他内容：\n"
        f"股票：{name}（{code}）\n"
        f"- moat（护城河，1-10整数）\n"
        f"- market_pos（市场地位，1-5整数）\n"
        f"- sentiment（市场情绪/近期消息面，1-5整数）\n"
        f"只返回 JSON，格式：{{\"moat\": 7, \"market_pos\": 4, \"sentiment\": 3}}"
    )

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 64},
    }).encode("utf-8")

    url = GEMINI_API_URL.format(model=GEMINI_MODEL, key=api_key)
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = body["candidates"][0]["content"]["parts"][0]["text"].strip()
        # 去掉可能的 markdown 代码块包装
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        raw = json.loads(text)
        return _validate(raw)
    except TimeoutError:
        logger.warning(f"{code} Gemini 超时（>{GEMINI_TIMEOUT_S}s），使用 fallback")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            logger.error(f"{code} Gemini 鉴权失败（{e.code}），请更新 .env GEMINI_API_KEY")
        elif e.code == 429:
            logger.warning(f"{code} Gemini quota 超限（429），将在下次 weekly 重试")
        else:
            logger.warning(f"{code} Gemini HTTP 错误 {e.code}")
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning(f"{code} Gemini 响应解析失败：{e}，触发 fallback")
    except Exception as e:
        logger.warning(f"{code} Gemini 调用异常：{e}，触发 fallback")
    return None


def get_qualitative_score(code: str, name: str) -> dict:
    """
    获取定性评分。优先读 30 天缓存，缓存过期则调 Gemini API。
    任何失败均 fallback 到 phase1_fixed 值，保证始终返回合法 dict。
    返回格式：{"moat": int, "market_pos": int, "sentiment": int}
    """
    cached = _check_cache(code)
    if cached is not None:
        logger.debug(f"{code} 定性评分缓存命中：{cached}")
        return cached

    result = _call_gemini(code, name)
    if result is None:
        logger.info(f"{code} 定性评分使用 fallback：{FALLBACK}")
        return dict(FALLBACK)

    _write_cache(code, result)
    logger.info(f"{code} 定性评分 Gemini：{result}")
    return result
