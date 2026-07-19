"""Contract tests for the static qualitative-v2 response schema."""

from __future__ import annotations

from typing import cast

from a_stock_tracker.qualitative.contract import (
    CONFIDENCE_VALUES,
    DIMENSION_NAMES,
    DIMENSION_STATUS_VALUES,
    EVIDENCE_IDS_MAX_ITEMS,
    OVERALL_STATUS_VALUES,
    RATIONALE_MAX_CHARS as CONTRACT_RATIONALE_MAX_CHARS,
    SCHEMA_VERSION,
    SCORE_RANGES,
)
from a_stock_tracker.qualitative.schema import RATIONALE_MAX_CHARS, build_generation_config, build_response_schema


def _properties(schema: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], schema["properties"])


def _dimension_schema(schema: dict[str, object], dimension: str) -> dict[str, object]:
    dimensions = cast(dict[str, object], _properties(schema)["dimensions"])
    return cast(dict[str, object], _properties(dimensions)[dimension])


def test_response_schema_is_deterministic_and_returns_independent_dicts() -> None:
    first = build_response_schema()
    second = build_response_schema()
    assert first == second
    assert first is not second

    _properties(first)["schema_version"] = {"type": "number"}
    assert first != second
    assert _properties(second)["schema_version"] == {
        "type": "string",
        "enum": ["qualitative-score-v2"],
    }


def test_response_schema_has_exact_closed_object_shapes() -> None:
    schema = build_response_schema()
    assert set(schema) == {"type", "properties", "required", "additionalProperties"}
    assert schema["required"] == ["schema_version", "overall_status", "as_of_date", "dimensions"]
    assert schema["additionalProperties"] is False

    dimensions = cast(dict[str, object], _properties(schema)["dimensions"])
    assert dimensions["required"] == ["moat", "market_pos", "sentiment"]
    assert dimensions["additionalProperties"] is False
    assert set(_properties(dimensions)) == {"moat", "market_pos", "sentiment"}

    for dimension in ("moat", "market_pos", "sentiment"):
        dim_schema = _dimension_schema(schema, dimension)
        assert dim_schema["required"] == ["status", "score", "confidence", "evidence_ids", "rationale"]
        assert dim_schema["additionalProperties"] is False
        assert set(_properties(dim_schema)) == {"status", "score", "confidence", "evidence_ids", "rationale"}


def test_response_schema_encodes_nullable_ranges_enums_and_array_limits() -> None:
    schema = build_response_schema()
    assert cast(dict[str, object], _properties(schema)["overall_status"])["enum"] == [
        "scored",
        "insufficient_data",
    ]

    for dimension, maximum in (("moat", 10), ("market_pos", 5), ("sentiment", 5)):
        dim_properties = _properties(_dimension_schema(schema, dimension))
        assert dim_properties["score"] == {
            "type": ["integer", "null"],
            "minimum": 1,
            "maximum": maximum,
        }
        assert cast(dict[str, object], dim_properties["status"])["enum"] == ["scored", "insufficient_data"]
        assert cast(dict[str, object], dim_properties["confidence"])["enum"] == ["low", "medium", "high"]
        evidence_ids = cast(dict[str, object], dim_properties["evidence_ids"])
        assert evidence_ids == {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 0,
            "maxItems": 64,
        }
        assert dim_properties["rationale"] == {"type": "string"}


def test_response_schema_is_aligned_with_central_contract_constants() -> None:
    schema = build_response_schema()
    properties = _properties(schema)
    assert cast(dict[str, object], properties["schema_version"])["enum"] == [SCHEMA_VERSION]
    assert cast(dict[str, object], properties["overall_status"])["enum"] == list(OVERALL_STATUS_VALUES)

    dimensions = cast(dict[str, object], properties["dimensions"])
    assert dimensions["required"] == list(DIMENSION_NAMES)
    for dimension in DIMENSION_NAMES:
        dim_properties = _properties(_dimension_schema(schema, dimension))
        minimum, maximum = SCORE_RANGES[dimension]
        assert dim_properties["score"] == {
            "type": ["integer", "null"],
            "minimum": minimum,
            "maximum": maximum,
        }
        assert cast(dict[str, object], dim_properties["status"])["enum"] == list(DIMENSION_STATUS_VALUES)
        assert cast(dict[str, object], dim_properties["confidence"])["enum"] == list(CONFIDENCE_VALUES)
        assert cast(dict[str, object], dim_properties["evidence_ids"])["maxItems"] == EVIDENCE_IDS_MAX_ITEMS

    assert RATIONALE_MAX_CHARS == CONTRACT_RATIONALE_MAX_CHARS


def test_generation_config_uses_only_response_json_schema_and_mime_type() -> None:
    config = build_generation_config()
    assert set(config) == {"responseMimeType", "responseJsonSchema"}
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == build_response_schema()
    assert "responseSchema" not in config
