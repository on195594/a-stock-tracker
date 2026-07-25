from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
import time
from typing import Any
import urllib.error
import urllib.request


logger = logging.getLogger(__name__)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_TIMEOUT_S = 30
MAX_RETRIES = 3
RETRY_DELAYS: tuple[int, ...] = (2, 4)


FORBIDDEN_OUTPUT_KEYS = {
    "score",
    "total_score",
    "quant_score",
    "threshold",
    "thresholds",
    "weights",
    "weights_hash",
    "db_write",
    "trade_action",
    "position",
    "data_fetch_instruction",
}


@dataclass(frozen=True)
class ReviewInput:
    code: str
    name: str
    score_result: dict[str, Any]
    data_quality_result: dict[str, Any]
    policy_version: str
    weights_hash: str
    missing_fields: tuple[str, ...] = field(default_factory=tuple)
    report_period: str | None = None
    risk_flags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReviewOutput:
    explanation: str
    objections: tuple[str, ...] = field(default_factory=tuple)
    missing_data_comment: str = ""
    human_questions: tuple[str, ...] = field(default_factory=tuple)
    confidence_note: str = ""
    is_fallback: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "explanation": self.explanation,
            "objections": list(self.objections),
            "missing_data_comment": self.missing_data_comment,
            "human_questions": list(self.human_questions),
            "confidence_note": self.confidence_note,
            "is_fallback": self.is_fallback,
        }


def validate_review_output(output: dict[str, Any]) -> None:
    forbidden = FORBIDDEN_OUTPUT_KEYS & set(output)
    if forbidden:
        raise ValueError(f"reviewer output contains forbidden keys: {sorted(forbidden)}")


def _fake_review_fallback(input_data: ReviewInput) -> ReviewOutput:
    missing = tuple(input_data.missing_fields)
    missing_comment = "无缺失字段" if not missing else "缺失字段：" + ", ".join(missing)
    objections = tuple(f"缺失 {field}" for field in missing)
    return ReviewOutput(
        explanation=f"{input_data.code} {input_data.name} 已完成确定性评分；reviewer 仅提供只读说明。",
        objections=objections,
        missing_data_comment=missing_comment,
        human_questions=(),
        confidence_note="fake reviewer；未调用真实 LLM；不得覆盖 deterministic score/decision。",
        is_fallback=True,
    )


def _is_retryable_timeout(error: BaseException) -> bool:
    if isinstance(error, TimeoutError):
        return True
    return isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError)


def _retry_timeout(input_data: ReviewInput, attempt: int) -> bool:
    if attempt >= MAX_RETRIES - 1:
        logger.warning(
            "%s gemini_review timeout exhausted after %d attempts, using fallback",
            input_data.code,
            MAX_RETRIES,
        )
        return False
    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
    logger.warning(
        "%s gemini_review timeout, retry in %ss (%d/%d)",
        input_data.code,
        delay,
        attempt + 1,
        MAX_RETRIES,
    )
    time.sleep(delay)
    return True


def gemini_review(input_data: ReviewInput) -> ReviewOutput:
    """Call Gemini to review a stock scoring result. Falls back to _fake_review_fallback on any error."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set, using fallback reviewer")
        return _fake_review_fallback(input_data)

    prompt = (
        f"你是A股量化评分系统的独立审查员。对以下评分结果提供简短的结构化审查意见。\n\n"
        f"股票：{input_data.code} {input_data.name}\n"
        f"评分结果：{json.dumps(input_data.score_result, ensure_ascii=False)}\n"
        f"数据质量：{json.dumps(input_data.data_quality_result, ensure_ascii=False)}\n"
        f"缺失字段：{list(input_data.missing_fields)}\n"
        f"风险标记：{list(input_data.risk_flags)}\n\n"
        "请以JSON格式返回审查意见，包含以下字段（全部使用中文）：\n"
        "- explanation: 对评分结果的简短说明（1-2句）\n"
        "- objections: 对评分结果的异议列表（字符串数组，可为空）\n"
        "- missing_data_comment: 对缺失字段的评论（字符串）\n"
        "- human_questions: 建议人工核查的问题列表（字符串数组，可为空）\n"
        "- confidence_note: 对评分可信度的说明（1句）\n\n"
        "仅返回JSON，不要其他内容。不要在JSON中包含分数、权重、建仓操作等决策字段。"
    )

    payload = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            # gemini-3.5-flash-lite: thinkingConfig is unsupported here (400 INVALID_ARGUMENT if set)
            # and this model does not consume hidden thinking tokens by default, so it's omitted.
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 512},
        }
    ).encode("utf-8")
    url = GEMINI_API_URL.format(model=GEMINI_MODEL, key=api_key)
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT_S) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            text = body["candidates"][0]["content"]["parts"][0]["text"].strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            raw = json.loads(text)
            validate_review_output(raw)
            return ReviewOutput(
                explanation=str(raw.get("explanation", "")),
                objections=tuple(raw.get("objections", [])),
                missing_data_comment=str(raw.get("missing_data_comment", "")),
                human_questions=tuple(raw.get("human_questions", [])),
                confidence_note=str(raw.get("confidence_note", "")),
            )
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                logger.error("%s gemini_review auth error (%s), using fallback", input_data.code, e.code)
                return _fake_review_fallback(input_data)
            retryable = e.code == 429 or e.code >= 500
            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
            if retryable and attempt < MAX_RETRIES - 1:
                logger.warning(
                    "%s gemini_review HTTP %s, retry in %ss (%d/%d)",
                    input_data.code,
                    e.code,
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                )
                time.sleep(delay)
            else:
                logger.warning("%s gemini_review HTTP error %s, using fallback", input_data.code, e.code)
                return _fake_review_fallback(input_data)
        except (TimeoutError, urllib.error.URLError) as e:
            if not _is_retryable_timeout(e):
                logger.warning("%s gemini_review transport error: %s, using fallback", input_data.code, e)
                return _fake_review_fallback(input_data)
            if _retry_timeout(input_data, attempt):
                continue
            return _fake_review_fallback(input_data)
        except (json.JSONDecodeError, KeyError, IndexError, ValueError) as e:
            logger.warning("%s gemini_review parse/validation error: %s, using fallback", input_data.code, e)
            return _fake_review_fallback(input_data)
        except Exception as e:
            logger.warning("%s gemini_review unexpected error: %s, using fallback", input_data.code, e)
            return _fake_review_fallback(input_data)
    return _fake_review_fallback(input_data)
