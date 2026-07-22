import json

import pytest

import a_stock_tracker.integrations.agent_reviewer as agent_reviewer

from a_stock_tracker.integrations.agent_reviewer import (
    ReviewInput,
    _fake_review_fallback as fake_review,
    validate_review_output,
)


def _input() -> ReviewInput:
    return ReviewInput(
        code="600036",
        name="招商银行",
        score_result={"total_score": 50.0, "decision": "buy_moderate"},
        data_quality_result={"is_acceptable": True},
        policy_version="phase-f-schema-only",
        weights_hash="abc12345",
        missing_fields=("gross_margin",),
        report_period="2024-09-30",
        risk_flags=("sample_size_low",),
    )


def test_fake_review_is_read_only_commentary() -> None:
    review = fake_review(_input())
    output = review.as_dict()
    assert review.is_fallback is True
    assert output["is_fallback"] is True
    assert "explanation" in output
    assert "gross_margin" in output["missing_data_comment"]
    assert "total_score" not in output
    assert "thresholds" not in output
    assert "trade_action" not in output


def test_successful_gemini_review_is_not_fallback(monkeypatch) -> None:
    response_body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "explanation": "真实审查说明",
                                    "objections": [],
                                    "missing_data_comment": "无",
                                    "human_questions": [],
                                    "confidence_note": "可信",
                                },
                                ensure_ascii=False,
                            )
                        }
                    ]
                }
            }
        ]
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return json.dumps(response_body, ensure_ascii=False).encode("utf-8")

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(agent_reviewer.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())

    review = agent_reviewer.gemini_review(_input())

    assert review.explanation == "真实审查说明"
    assert review.is_fallback is False


def test_validate_review_output_rejects_score_override() -> None:
    with pytest.raises(ValueError):
        validate_review_output({"explanation": "x", "total_score": 99})


def test_validate_review_output_rejects_trade_or_db_instruction() -> None:
    with pytest.raises(ValueError):
        validate_review_output({"explanation": "x", "db_write": "UPDATE predictions"})
    with pytest.raises(ValueError):
        validate_review_output({"explanation": "x", "trade_action": "buy"})
