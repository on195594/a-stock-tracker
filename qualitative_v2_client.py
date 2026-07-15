"""Isolated Gemini transport for qualitative-v2 shadow evaluation.

This module has no database, pipeline, cache, or notification dependencies.
It turns one validated context into one bounded Gemini request and classifies
all outcomes using the shadow status contract.
"""

from __future__ import annotations

import json
import random
import re
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from qualitative_v2_prompt import build_scoring_prompt
from qualitative_v2_schema import build_generation_config
from qualitative_v2_types import QualitativeContext, ScoringResult
from qualitative_v2_validator import validate_context, validate_model_output

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0)
MAX_RESPONSE_BYTES = 262_144
FAILURE_REASON_MAX_CHARS = 256
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward the API-key header to a redirected destination."""

    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())


class ValidationStatus(StrEnum):
    """Bounded REQ-038 outcome values for one external call."""

    VALID_SCORED = "VALID_SCORED"
    VALID_INSUFFICIENT_DATA = "VALID_INSUFFICIENT_DATA"
    API_TIMEOUT = "API_TIMEOUT"
    API_AUTH_ERROR = "API_AUTH_ERROR"
    API_RATE_LIMIT = "API_RATE_LIMIT"
    API_SERVER_ERROR = "API_SERVER_ERROR"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    EVIDENCE_INVALID = "EVIDENCE_INVALID"
    SEMANTIC_INVALID = "SEMANTIC_INVALID"


@dataclass(frozen=True)
class GeminiCallResult:
    """Validated call result or a bounded failure suitable for persistence."""

    status: ValidationStatus
    result: ScoringResult | None
    raw_output: dict[str, object] | None
    failure_reason: str | None
    attempts: int


class _ReadableResponse(Protocol):
    def read(self, amount: int = -1) -> bytes: ...


Transport = Callable[[urllib.request.Request, float], AbstractContextManager[_ReadableResponse]]


def _default_transport(
    request: urllib.request.Request,
    timeout: float,
) -> AbstractContextManager[_ReadableResponse]:
    return cast(AbstractContextManager[_ReadableResponse], _NO_REDIRECT_OPENER.open(request, timeout=timeout))


def _bounded_reason(code: str, summary: str | None = None, *, secret: str = "") -> str:
    """Build a one-line, secret-scrubbed reason capped by REQ-039."""
    clean_summary = ""
    if summary:
        clean_summary = " ".join(summary.split())
        if secret:
            clean_summary = clean_summary.replace(secret, "[REDACTED]")
    reason = code if not clean_summary else f"{code}: {clean_summary}"
    return reason[:FAILURE_REASON_MAX_CHARS]


def _failure(
    status: ValidationStatus,
    code: str,
    *,
    attempts: int,
    summary: str | None = None,
    raw_output: dict[str, object] | None = None,
    secret: str = "",
) -> GeminiCallResult:
    return GeminiCallResult(
        status=status,
        result=None,
        raw_output=raw_output,
        failure_reason=_bounded_reason(code, summary, secret=secret),
        attempts=attempts,
    )


def _extract_model_output(response: _ReadableResponse) -> dict[str, object]:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise OverflowError("response exceeds bounded size")
    envelope = json.loads(body.decode("utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("response envelope must be an object")
    candidates = envelope.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("response has no candidate")
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be an object")
    content = candidate.get("content")
    if not isinstance(content, dict):
        raise ValueError("candidate content must be an object")
    parts = content.get("parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], dict):
        raise ValueError("candidate has no text part")
    text = parts[0].get("text")
    if not isinstance(text, str):
        raise ValueError("candidate text must be a string")
    output = json.loads(text)
    if not isinstance(output, dict):
        raise ValueError("model output must be an object")
    return cast(dict[str, object], output)


def _status_for_rejection(kind: str | None) -> ValidationStatus:
    if kind == "schema":
        return ValidationStatus.SCHEMA_INVALID
    if kind == "evidence":
        return ValidationStatus.EVIDENCE_INVALID
    return ValidationStatus.SEMANTIC_INVALID


def call_gemini(
    context: QualitativeContext,
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_delays: Sequence[float] = DEFAULT_RETRY_DELAYS,
    transport: Transport = _default_transport,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> GeminiCallResult:
    """Call Gemini once logically, with bounded retries for transient failures."""
    if not api_key.strip():
        return _failure(ValidationStatus.API_AUTH_ERROR, "API_KEY_MISSING", attempts=0)
    if _MODEL_ID_RE.fullmatch(model) is None:
        raise ValueError("model must be a bounded Gemini model identifier")
    if not 1 <= max_attempts <= 5:
        raise ValueError("max_attempts must be between 1 and 5")
    if len(retry_delays) < max_attempts - 1 or any(delay < 0 or delay > 60 for delay in retry_delays):
        raise ValueError("retry_delays must cover every retry and stay within 0..60 seconds")
    if not 0 < timeout_seconds <= 120:
        raise ValueError("timeout_seconds must be within 0..120 seconds")

    context_validation = validate_context(context)
    if not context_validation.valid or context_validation.context is None:
        return _failure(
            ValidationStatus.EVIDENCE_INVALID,
            "INVALID_CONTEXT",
            attempts=0,
            summary=context_validation.rejection_reason,
            secret=api_key,
        )
    context = context_validation.context

    try:
        prompt = build_scoring_prompt(context)
        payload = json.dumps(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": build_generation_config(),
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return _failure(
            ValidationStatus.EVIDENCE_INVALID,
            "PROMPT_BUILD_FAILED",
            attempts=0,
            summary=str(exc),
            secret=api_key,
        )

    request = urllib.request.Request(
        GEMINI_API_URL.format(model=model),
        data=payload,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )

    for attempt in range(1, max_attempts + 1):
        try:
            with transport(request, timeout_seconds) as response:
                raw_output = _extract_model_output(response)
        except OverflowError:
            return _failure(
                ValidationStatus.MALFORMED_RESPONSE,
                "RESPONSE_TOO_LARGE",
                attempts=attempt,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return _failure(
                ValidationStatus.MALFORMED_RESPONSE,
                "MALFORMED_RESPONSE",
                attempts=attempt,
            )
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return _failure(
                    ValidationStatus.API_AUTH_ERROR,
                    f"HTTP_{exc.code}",
                    attempts=attempt,
                )
            if exc.code == 408:
                status = ValidationStatus.API_TIMEOUT
                code = "HTTP_408_EXHAUSTED"
                retryable = True
            elif exc.code == 429:
                status = ValidationStatus.API_RATE_LIMIT
                code = "RATE_LIMIT_EXHAUSTED"
                retryable = True
            elif exc.code >= 500:
                status = ValidationStatus.API_SERVER_ERROR
                code = f"HTTP_{exc.code}_EXHAUSTED"
                retryable = True
            else:
                return _failure(
                    ValidationStatus.SCHEMA_INVALID,
                    f"HTTP_{exc.code}_REQUEST_REJECTED",
                    attempts=attempt,
                )
            if retryable and attempt < max_attempts:
                delay = retry_delays[attempt - 1] + min(max(jitter(), 0.0), 1.0) * 0.5
                sleep(delay)
                continue
            return _failure(status, code, attempts=attempt)
        except (TimeoutError, socket.timeout):
            if attempt < max_attempts:
                delay = retry_delays[attempt - 1] + min(max(jitter(), 0.0), 1.0) * 0.5
                sleep(delay)
                continue
            return _failure(ValidationStatus.API_TIMEOUT, "TIMEOUT_EXHAUSTED", attempts=attempt)
        except urllib.error.URLError as exc:
            status = ValidationStatus.API_SERVER_ERROR
            code = "NETWORK_ERROR_EXHAUSTED"
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                status = ValidationStatus.API_TIMEOUT
                code = "TIMEOUT_EXHAUSTED"
            if attempt < max_attempts:
                delay = retry_delays[attempt - 1] + min(max(jitter(), 0.0), 1.0) * 0.5
                sleep(delay)
                continue
            return _failure(status, code, attempts=attempt)

        validation = validate_model_output(raw_output, context=context)
        if not validation.valid or validation.result is None:
            return _failure(
                _status_for_rejection(validation.rejection_kind),
                "LOCAL_VALIDATION_FAILED",
                attempts=attempt,
                summary=validation.rejection_reason,
                raw_output=raw_output,
                secret=api_key,
            )
        status = (
            ValidationStatus.VALID_SCORED
            if validation.result.overall_status == "scored"
            else ValidationStatus.VALID_INSUFFICIENT_DATA
        )
        return GeminiCallResult(
            status=status,
            result=validation.result,
            raw_output=raw_output,
            failure_reason=None,
            attempts=attempt,
        )

    raise AssertionError("bounded attempt loop exited unexpectedly")


__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "FAILURE_REASON_MAX_CHARS",
    "GeminiCallResult",
    "ValidationStatus",
    "call_gemini",
]
