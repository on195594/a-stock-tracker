from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


FORBIDDEN_OUTPUT_KEYS = {
    "score", "total_score", "quant_score", "threshold", "thresholds", "weights",
    "weights_hash", "db_write", "trade_action", "position", "data_fetch_instruction",
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "explanation": self.explanation,
            "objections": list(self.objections),
            "missing_data_comment": self.missing_data_comment,
            "human_questions": list(self.human_questions),
            "confidence_note": self.confidence_note,
        }


def validate_review_output(output: dict[str, Any]) -> None:
    forbidden = FORBIDDEN_OUTPUT_KEYS & set(output)
    if forbidden:
        raise ValueError(f"reviewer output contains forbidden keys: {sorted(forbidden)}")


def fake_review(input_data: ReviewInput) -> ReviewOutput:
    missing = tuple(input_data.missing_fields)
    missing_comment = "无缺失字段" if not missing else "缺失字段：" + ", ".join(missing)
    objections = tuple(f"缺失 {field}" for field in missing)
    return ReviewOutput(
        explanation=f"{input_data.code} {input_data.name} 已完成确定性评分；reviewer 仅提供只读说明。",
        objections=objections,
        missing_data_comment=missing_comment,
        human_questions=(),
        confidence_note="fake reviewer；未调用真实 LLM；不得覆盖 deterministic score/decision。",
    )
