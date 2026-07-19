"""HTTP-boundary tests for the isolated qualitative-v2 Gemini client."""

from __future__ import annotations

import json
import urllib.error
from collections.abc import Callable
from email.message import Message

import pytest

from a_stock_tracker.qualitative.client import GeminiCallResult, ValidationStatus, call_gemini
from a_stock_tracker.qualitative.types import QualitativeContext


def _context() -> QualitativeContext:
    return QualitativeContext(
        code="000000",
        name="合同测试",
        industry="synthetic",
        as_of_date="2026-07-15",
        schema_version="qualitative-score-v2",
        rubric_version="rubric-v1",
        taxonomy_version="taxonomy-v1",
        evidence=(),
    )


def _insufficient_output() -> dict[str, object]:
    dimension: dict[str, object] = {
        "status": "insufficient_data",
        "score": None,
        "confidence": "low",
        "evidence_ids": [],
        "rationale": "No qualifying evidence was supplied.",
    }
    return {
        "schema_version": "qualitative-score-v2",
        "overall_status": "insufficient_data",
        "as_of_date": "2026-07-15",
        "dimensions": {
            "moat": dict(dimension),
            "market_pos": dict(dimension),
            "sentiment": dict(dimension),
        },
    }


def _api_body(output: object) -> bytes:
    return json.dumps(
        {"candidates": [{"content": {"parts": [{"text": json.dumps(output)}]}}]},
        ensure_ascii=False,
    ).encode("utf-8")


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self.body if amount < 0 else self.body[:amount]


class _Transport:
    def __init__(self, outcomes: list[bytes | Exception]) -> None:
        self.outcomes = outcomes
        self.calls = 0
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.payloads: list[dict[str, object]] = []

    def __call__(self, request: object, timeout: float) -> _Response:
        assert timeout > 0
        self.calls += 1
        request_url = getattr(request, "full_url")
        self.urls.append(request_url)
        self.headers.append(dict(getattr(request, "headers")))
        self.payloads.append(json.loads(getattr(request, "data").decode("utf-8")))
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(outcome)


def _call(
    transport: _Transport,
    *,
    sleep: Callable[[float], None] = lambda _delay: None,
) -> GeminiCallResult:
    return call_gemini(
        _context(),
        api_key="AIza-test-secret",
        transport=transport,
        sleep=sleep,
        jitter=lambda: 0.0,
        retry_delays=(0.0, 0.0),
    )


def test_success_uses_header_schema_and_local_validation() -> None:
    transport = _Transport([_api_body(_insufficient_output())])

    result = _call(transport)

    assert result.status is ValidationStatus.VALID_INSUFFICIENT_DATA
    assert result.result is not None
    assert result.raw_output == _insufficient_output()
    assert result.failure_reason is None
    assert result.attempts == 1
    assert "AIza-test-secret" not in transport.urls[0]
    assert transport.headers[0]["X-goog-api-key"] == "AIza-test-secret"
    generation_config = transport.payloads[0]["generationConfig"]
    assert isinstance(generation_config, dict)
    assert set(generation_config) == {"responseMimeType", "responseJsonSchema"}
    assert "responseSchema" not in generation_config


@pytest.mark.parametrize("http_code", [401, 403])
def test_auth_errors_do_not_retry(http_code: int) -> None:
    error = urllib.error.HTTPError("https://safe.invalid", http_code, "auth", Message(), None)
    transport = _Transport([error])

    result = _call(transport)

    assert result.status is ValidationStatus.API_AUTH_ERROR
    assert result.attempts == 1
    assert transport.calls == 1
    assert "AIza-test-secret" not in (result.failure_reason or "")


@pytest.mark.parametrize(
    ("http_code", "expected_status"),
    [
        (408, ValidationStatus.API_TIMEOUT),
        (429, ValidationStatus.API_RATE_LIMIT),
        (500, ValidationStatus.API_SERVER_ERROR),
    ],
)
def test_retryable_http_errors_are_bounded(http_code: int, expected_status: ValidationStatus) -> None:
    error = urllib.error.HTTPError("https://safe.invalid", http_code, "retry", Message(), None)
    transport = _Transport([error])
    sleeps: list[float] = []

    result = _call(transport, sleep=sleeps.append)

    assert result.status is expected_status
    assert result.attempts == 3
    assert transport.calls == 3
    assert sleeps == [0.0, 0.0]


def test_timeout_is_retried_and_bounded() -> None:
    transport = _Transport([TimeoutError("contains local details")])

    result = _call(transport)

    assert result.status is ValidationStatus.API_TIMEOUT
    assert result.attempts == 3
    assert transport.calls == 3
    assert "local details" not in (result.failure_reason or "")


def test_urlerror_wrapped_timeout_is_classified_as_timeout() -> None:
    transport = _Transport([urllib.error.URLError(TimeoutError("socket detail"))])

    result = _call(transport)

    assert result.status is ValidationStatus.API_TIMEOUT
    assert result.attempts == 3
    assert "socket detail" not in (result.failure_reason or "")


def test_malformed_response_does_not_retry() -> None:
    transport = _Transport([b"not-json"])

    result = _call(transport)

    assert result.status is ValidationStatus.MALFORMED_RESPONSE
    assert result.attempts == 1
    assert transport.calls == 1


def test_oversized_response_is_rejected_without_unbounded_read() -> None:
    transport = _Transport([b"x" * 300_000])

    result = _call(transport)

    assert result.status is ValidationStatus.MALFORMED_RESPONSE
    assert result.failure_reason == "RESPONSE_TOO_LARGE"


def test_structural_output_failure_maps_to_schema_invalid() -> None:
    output = _insufficient_output()
    output["unexpected"] = True
    transport = _Transport([_api_body(output)])

    result = _call(transport)

    assert result.status is ValidationStatus.SCHEMA_INVALID
    assert result.result is None
    assert result.raw_output == output


def test_unknown_citation_maps_to_evidence_invalid() -> None:
    output = _insufficient_output()
    dimensions = output["dimensions"]
    assert isinstance(dimensions, dict)
    dimensions["market_pos"] = {
        "status": "scored",
        "score": 4,
        "confidence": "medium",
        "evidence_ids": ["disclosure.unknown"],
        "rationale": "Unsupported citation.",
    }
    transport = _Transport([_api_body(output)])

    result = _call(transport)

    assert result.status is ValidationStatus.EVIDENCE_INVALID


def test_cross_field_failure_maps_to_semantic_invalid() -> None:
    output = _insufficient_output()
    output["overall_status"] = "scored"
    transport = _Transport([_api_body(output)])

    result = _call(transport)

    assert result.status is ValidationStatus.SEMANTIC_INVALID


@pytest.mark.parametrize("model", ["", "../other", "gemini?key=leak", "gemini%2Fother", "x" * 129])
def test_model_identifier_is_restricted_before_transport(model: str) -> None:
    transport = _Transport([_api_body(_insufficient_output())])

    with pytest.raises(ValueError, match="model identifier"):
        call_gemini(_context(), api_key="secret", model=model, transport=transport)

    assert transport.calls == 0
