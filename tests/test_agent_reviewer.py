import pytest

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
    output = fake_review(_input()).as_dict()
    assert "explanation" in output
    assert "gross_margin" in output["missing_data_comment"]
    assert "total_score" not in output
    assert "thresholds" not in output
    assert "trade_action" not in output


def test_validate_review_output_rejects_score_override() -> None:
    with pytest.raises(ValueError):
        validate_review_output({"explanation": "x", "total_score": 99})


def test_validate_review_output_rejects_trade_or_db_instruction() -> None:
    with pytest.raises(ValueError):
        validate_review_output({"explanation": "x", "db_write": "UPDATE predictions"})
    with pytest.raises(ValueError):
        validate_review_output({"explanation": "x", "trade_action": "buy"})
