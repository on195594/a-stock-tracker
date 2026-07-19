"""Static Gemini structured-output schema for qualitative scoring v2.

The schema intentionally describes only the wire shape. Cross-field rules,
evidence citation validity, and the rationale length limit are enforced by the
local validator.
"""

from __future__ import annotations

from a_stock_tracker.qualitative.contract import (
    CONFIDENCE_VALUES,
    DIMENSION_NAMES,
    DIMENSION_STATUS_VALUES,
    EVIDENCE_IDS_MAX_ITEMS,
    OVERALL_STATUS_VALUES,
    RATIONALE_MAX_CHARS,
    SCHEMA_VERSION,
    SCORE_RANGES,
)

_DIMENSION_FIELDS = ["status", "score", "confidence", "evidence_ids", "rationale"]


def _dimension_schema(dimension: str) -> dict[str, object]:
    minimum, maximum = SCORE_RANGES[dimension]
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": list(DIMENSION_STATUS_VALUES)},
            "score": {"type": ["integer", "null"], "minimum": minimum, "maximum": maximum},
            "confidence": {"type": "string", "enum": list(CONFIDENCE_VALUES)},
            "evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 0,
                "maxItems": EVIDENCE_IDS_MAX_ITEMS,
            },
            # Gemini's currently documented JSON Schema subset does not list
            # maxLength. The prompt and local validator enforce 500 chars.
            "rationale": {"type": "string"},
        },
        "required": list(_DIMENSION_FIELDS),
        "additionalProperties": False,
    }


def build_response_schema() -> dict[str, object]:
    """Return a fresh deterministic JSON Schema for the v2 model response."""
    return {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string", "enum": [SCHEMA_VERSION]},
            "overall_status": {"type": "string", "enum": list(OVERALL_STATUS_VALUES)},
            "as_of_date": {"type": "string"},
            "dimensions": {
                "type": "object",
                "properties": {dimension: _dimension_schema(dimension) for dimension in DIMENSION_NAMES},
                "required": list(DIMENSION_NAMES),
                "additionalProperties": False,
            },
        },
        "required": ["schema_version", "overall_status", "as_of_date", "dimensions"],
        "additionalProperties": False,
    }


def build_generation_config() -> dict[str, object]:
    """Return the Gemini generationConfig fields for structured JSON output."""
    return {
        "responseMimeType": "application/json",
        "responseJsonSchema": build_response_schema(),
    }


__all__ = ["RATIONALE_MAX_CHARS", "build_generation_config", "build_response_schema"]
