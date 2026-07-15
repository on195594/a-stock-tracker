"""Fixture-first tests for qualitative_v2_validator.py, covering spec section 8
(Fixture-first test matrix) for REQ-005/007~009/019/023~030/062/064."""

from __future__ import annotations

import copy
from datetime import date

import pytest

from qualitative_v2_contract import (
    CONTEXT_TEXT_MAX_CHARS,
    EVIDENCE_ID_MAX_CHARS,
    EVIDENCE_IDS_MAX_ITEMS,
    EVIDENCE_PACKET_MAX_ITEMS,
    EVIDENCE_VALUE_MAX_JSON_BYTES,
    SOURCE_MAX_CHARS,
    UNIT_MAX_CHARS,
)
from qualitative_v2_types import Evidence, QualitativeContext
from qualitative_v2_validator import (
    validate_context,
    validate_context_dict,
    validate_evidence_dict,
    validate_model_output,
)
from qualitative_v2_taxonomy import CLAIM_CATEGORY_REGISTRY

AS_OF_DATE = "2026-07-14"
AS_OF_DATE_OBJ = date(2026, 7, 14)


def _financial_evidence(evidence_id: str = "fundamentals.roe_3y_avg") -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "evidence_type": "financial_metric",
        "claim_category": "financial_performance",
        "allowed_dimensions": ["moat"],
        "directness": "supporting",
        "freshness_policy": "max_age_550d",
        "value": 14.2,
        "unit": "percent",
        "source": "local_fundamentals_cache",
        "source_date": "2026-03-31",
        "freshness_status": "fresh",
    }


def _moat_evidence(evidence_id: str = "ip.patent_grant") -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "evidence_type": "ip_record",
        "claim_category": "competitive_moat",
        "allowed_dimensions": ["moat"],
        "directness": "direct",
        "freshness_policy": "max_age_365d",
        "value": "granted",
        "unit": None,
        "source": "cninfo_filing",
        "source_date": "2026-01-01",
        "freshness_status": "fresh",
        "state_type": "status",
        "effective_until": "2035-01-01",
    }


def _industry_position_evidence(evidence_id: str = "disclosure.market_share") -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "evidence_type": "company_disclosure",
        "claim_category": "industry_position",
        "allowed_dimensions": ["market_pos"],
        "directness": "direct",
        "freshness_policy": "max_age_365d",
        "value": "top3",
        "unit": None,
        "source": "annual_report",
        "source_date": "2026-01-01",
        "freshness_status": "fresh",
    }


def _sentiment_evidence(
    evidence_id: str = "news.major_contract",
    persistence_horizon: str = "multi_quarter",
    materiality: str = "major",
    directness: str = "direct",
) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "evidence_type": "news_report",
        "claim_category": "market_sentiment",
        "allowed_dimensions": ["sentiment"],
        "directness": directness,
        "freshness_policy": "max_age_30d",
        "value": "positive",
        "unit": None,
        "source": "reuters",
        "source_date": "2026-07-01",
        "freshness_status": "fresh",
        "persistence_horizon": persistence_horizon,
        "materiality": materiality,
    }


def _full_context_dict(evidence: list[dict[str, object]]) -> dict[str, object]:
    return {
        "code": "601899",
        "name": "紫金矿业",
        "industry": "铜",
        "as_of_date": AS_OF_DATE,
        "schema_version": "qualitative-score-v2",
        "rubric_version": "rubric-v1",
        "taxonomy_version": "taxonomy-v1",
        "evidence": evidence,
    }


def _insufficient_dim() -> dict[str, object]:
    return {
        "status": "insufficient_data",
        "score": None,
        "confidence": "low",
        "evidence_ids": [],
        "rationale": "no evidence",
    }


# ---------------------------------------------------------------------------
# Evidence-level validation (REQ-003~005, REQ-061, REQ-064)
# ---------------------------------------------------------------------------


def test_valid_financial_evidence_accepted() -> None:
    result = validate_evidence_dict(_financial_evidence(), as_of_date_value=AS_OF_DATE_OBJ)
    assert result.valid
    assert result.evidence is not None
    assert result.evidence.claim_category == "financial_performance"


def test_unknown_evidence_type_rejected() -> None:
    raw = _financial_evidence()
    raw["evidence_type"] = "not_a_real_type"
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


@pytest.mark.parametrize(
    ("evidence_type", "evidence_id", "claim_category", "allowed_dimensions", "directness", "extras"),
    [
        ("financial_metric", "fundamentals.roe", "financial_performance", ["moat"], "supporting", {}),
        ("valuation_metric", "valuation.pb", "valuation", [], "context", {}),
        ("company_disclosure", "disclosure.moat", "competitive_moat", ["moat"], "direct", {}),
        ("regulatory_filing", "regulatory.license", "competitive_moat", ["moat"], "direct", {"state_type": "event"}),
        ("ip_record", "ip.patent", "competitive_moat", ["moat"], "direct", {"state_type": "event"}),
        (
            "counterparty_disclosure",
            "counterparty.market_position",
            "industry_position",
            ["market_pos"],
            "direct",
            {"state_type": "event"},
        ),
        (
            "news_report",
            "news.sentiment",
            "market_sentiment",
            ["sentiment"],
            "direct",
            {"persistence_horizon": "structural", "materiality": "major"},
        ),
        ("analyst_consensus", "consensus.rank", "industry_position", ["market_pos"], "direct", {}),
    ],
)
def test_all_evidence_type_namespaces_accepted(
    evidence_type: str,
    evidence_id: str,
    claim_category: str,
    allowed_dimensions: list[str],
    directness: str,
    extras: dict[str, object],
) -> None:
    freshness_policy_by_category = {
        "financial_performance": "max_age_550d",
        "valuation": "max_age_30d",
        "competitive_moat": "max_age_365d",
        "industry_position": "max_age_365d",
        "market_sentiment": "max_age_30d",
    }
    raw: dict[str, object] = {
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "claim_category": claim_category,
        "allowed_dimensions": allowed_dimensions,
        "directness": directness,
        "freshness_policy": freshness_policy_by_category[claim_category],
        "value": "fixture",
        "unit": None,
        "source": "fixture",
        "source_date": "2026-07-01",
        "freshness_status": "fresh",
        **extras,
    }
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert result.valid, result.rejection_reason


def test_evidence_type_namespace_mismatch_rejected() -> None:
    raw = _financial_evidence("news.roe_3y_avg")
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid
    assert "namespace" in (result.rejection_reason or "")


def test_evidence_type_claim_category_mismatch_rejected() -> None:
    raw = _financial_evidence()
    raw["claim_category"] = "market_sentiment"  # financial_metric cannot carry this
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid
    assert "may not carry claim_category" in (result.rejection_reason or "")


def test_allowed_dimensions_mismatch_rejected() -> None:
    raw = _financial_evidence()
    raw["allowed_dimensions"] = ["sentiment"]  # financial_performance canonical is [moat]
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


def test_state_type_status_missing_effective_until_rejected() -> None:
    raw = _moat_evidence()
    del raw["effective_until"]
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid
    assert "effective_until" in (result.rejection_reason or "")


def test_state_type_status_with_explicit_null_effective_until_accepted() -> None:
    raw = _moat_evidence()
    raw["effective_until"] = None
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert result.valid


def test_evidence_type_not_eligible_for_state_type_rejected() -> None:
    raw = _financial_evidence()
    raw["state_type"] = "event"
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


def test_market_sentiment_missing_persistence_fields_rejected() -> None:
    raw = _sentiment_evidence()
    del raw["persistence_horizon"]
    del raw["materiality"]
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


def test_non_sentiment_with_persistence_fields_rejected() -> None:
    raw = _financial_evidence()
    raw["persistence_horizon"] = "structural"
    raw["materiality"] = "major"
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


def test_declared_freshness_mismatch_rejected() -> None:
    raw = _financial_evidence()
    raw["freshness_status"] = "stale"  # actually fresh given source_date/as_of_date
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


def test_future_source_date_rejected() -> None:
    raw = _financial_evidence()
    raw["source_date"] = "2026-12-31"
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numeric_evidence_rejected(value: float) -> None:
    raw = _financial_evidence()
    raw["value"] = value
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid
    assert "finite JSON" in (result.rejection_reason or "")


@pytest.mark.parametrize("value", ["20260714", "2026-W29-2"])
def test_noncanonical_source_dates_rejected(value: str) -> None:
    raw = _financial_evidence()
    raw["source_date"] = value
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid
    assert "YYYY-MM-DD" in (result.rejection_reason or "")


def test_evidence_value_encoded_size_boundary() -> None:
    accepted = _financial_evidence()
    accepted["value"] = "x" * (EVIDENCE_VALUE_MAX_JSON_BYTES - 2)
    rejected = _financial_evidence()
    rejected["value"] = "x" * (EVIDENCE_VALUE_MAX_JSON_BYTES - 1)

    assert validate_evidence_dict(accepted, as_of_date_value=AS_OF_DATE_OBJ).valid
    result = validate_evidence_dict(rejected, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid
    assert "encoded size" in (result.rejection_reason or "")


def test_missing_required_field_rejected() -> None:
    raw = _financial_evidence()
    del raw["source"]
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


def test_extra_unknown_field_rejected() -> None:
    raw = _financial_evidence()
    raw["extra_field"] = "not allowed"
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


def test_directness_not_allowed_for_claim_category_rejected() -> None:
    raw = _financial_evidence()
    raw["directness"] = "direct"  # financial_performance forbids direct
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


def test_non_canonical_freshness_policy_rejected_even_though_known_value() -> None:
    """codex review Critical: a market_sentiment item declaring the
    financial_performance-length policy (550d instead of canonical 30d)
    must be rejected outright, not merely accepted and evaluated against
    the wrong (longer) window."""
    raw = _sentiment_evidence()
    raw["freshness_policy"] = "max_age_550d"  # canonical for market_sentiment is max_age_30d
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid
    assert "canonical" in (result.rejection_reason or "")


def test_non_string_evidence_id_rejected() -> None:
    raw = _financial_evidence()
    raw["evidence_id"] = 123
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


def test_empty_string_evidence_id_rejected() -> None:
    raw = _financial_evidence()
    raw["evidence_id"] = ""
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


@pytest.mark.parametrize("evidence_id", ["fundamentals.roe bad", "fundamentals.roe\n"])
def test_evidence_id_whitespace_or_control_rejected(evidence_id: str) -> None:
    raw = _financial_evidence(evidence_id)
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid
    assert "whitespace or control" in (result.rejection_reason or "")


def test_evidence_id_length_limit_enforced() -> None:
    prefix = "fundamentals."
    raw = _financial_evidence(prefix + "x" * (EVIDENCE_ID_MAX_CHARS - len(prefix) + 1))
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid
    assert "maximum length" in (result.rejection_reason or "")


def test_empty_unit_string_rejected() -> None:
    raw = _financial_evidence()
    raw["unit"] = ""
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


@pytest.mark.parametrize("unit", ["   ", "x" * (UNIT_MAX_CHARS + 1)])
def test_unit_must_be_nonblank_and_bounded(unit: str) -> None:
    raw = _financial_evidence()
    raw["unit"] = unit
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid
    assert "bounded" in (result.rejection_reason or "")


@pytest.mark.parametrize("source", ["   ", "x" * (SOURCE_MAX_CHARS + 1)])
def test_source_must_be_nonblank_and_bounded(source: str) -> None:
    raw = _financial_evidence()
    raw["source"] = source
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid
    assert "bounded" in (result.rejection_reason or "")


def test_unhashable_evidence_type_fails_closed_not_typeerror() -> None:
    """codex review Important #2: a list/dict value in a field checked
    against a frozenset must be rejected cleanly, not raise TypeError."""
    raw = _financial_evidence()
    raw["evidence_type"] = ["not", "a", "string"]
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


def test_unhashable_allowed_dimensions_element_fails_closed() -> None:
    raw = _financial_evidence()
    raw["allowed_dimensions"] = [{"nested": "dict"}]
    result = validate_evidence_dict(raw, as_of_date_value=AS_OF_DATE_OBJ)
    assert not result.valid


# ---------------------------------------------------------------------------
# Context-level validation (REQ-001~005)
# ---------------------------------------------------------------------------


def test_valid_context_accepted() -> None:
    result = validate_context_dict(_full_context_dict([_financial_evidence(), _moat_evidence()]))
    assert result.valid
    assert result.context is not None
    assert len(result.context.evidence) == 2


def test_duplicate_evidence_id_rejected() -> None:
    duplicate_id = "fundamentals.duplicate"
    result = validate_context_dict(
        _full_context_dict([_financial_evidence(duplicate_id), _financial_evidence(duplicate_id)])
    )
    assert not result.valid
    assert "duplicate" in (result.rejection_reason or "")


def test_context_missing_field_rejected() -> None:
    raw = _full_context_dict([_financial_evidence()])
    del raw["taxonomy_version"]
    result = validate_context_dict(raw)
    assert not result.valid


def test_one_invalid_evidence_rejects_whole_context() -> None:
    bad_evidence = _financial_evidence("bad.one")
    bad_evidence["evidence_type"] = "not_real"
    result = validate_context_dict(_full_context_dict([_financial_evidence(), bad_evidence]))
    assert not result.valid


@pytest.mark.parametrize("value", ["20260714", "2026-W29-2"])
def test_noncanonical_context_dates_rejected(value: str) -> None:
    raw = _full_context_dict([])
    raw["as_of_date"] = value
    result = validate_context_dict(raw)
    assert not result.valid
    assert "YYYY-MM-DD" in (result.rejection_reason or "")


def test_context_evidence_item_limit_enforced() -> None:
    evidence = [_financial_evidence(f"fundamentals.metric_{index}") for index in range(EVIDENCE_PACKET_MAX_ITEMS + 1)]
    result = validate_context_dict(_full_context_dict(evidence))
    assert not result.valid
    assert f"maximum item count {EVIDENCE_PACKET_MAX_ITEMS}" in (result.rejection_reason or "")


@pytest.mark.parametrize(
    "field_name", ["code", "name", "industry", "schema_version", "rubric_version", "taxonomy_version"]
)
@pytest.mark.parametrize("value", ["   ", "x" * (CONTEXT_TEXT_MAX_CHARS + 1)])
def test_context_text_fields_must_be_nonblank_and_bounded(field_name: str, value: str) -> None:
    raw = _full_context_dict([])
    raw[field_name] = value
    result = validate_context_dict(raw)
    assert not result.valid


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("schema_version", "qualitative-score-v3"),
        ("rubric_version", "rubric-v2"),
        ("taxonomy_version", "taxonomy-v2"),
    ],
)
def test_unknown_context_versions_rejected(field_name: str, value: str) -> None:
    raw = _full_context_dict([])
    raw[field_name] = value
    result = validate_context_dict(raw)
    assert not result.valid
    assert f"unsupported {field_name}" in (result.rejection_reason or "")


def test_validate_context_preserves_status_effective_until_null() -> None:
    raw = _full_context_dict([_moat_evidence()])
    evidence_raw = raw["evidence"]
    assert isinstance(evidence_raw, list)
    evidence_raw[0]["effective_until"] = None
    validated = validate_context_dict(raw)
    assert validated.valid and validated.context is not None

    revalidated = validate_context(validated.context)
    assert revalidated.valid, revalidated.rejection_reason


# ---------------------------------------------------------------------------
# Model output validation (REQ-007~009/015~019/024~029/062)
# ---------------------------------------------------------------------------


def _build_context(evidence: list[dict[str, object]]):
    result = validate_context_dict(_full_context_dict(evidence))
    assert result.valid, result.rejection_reason
    return result.context


def test_full_scorable_output_accepted() -> None:
    context = _build_context(
        [_financial_evidence(), _moat_evidence(), _industry_position_evidence(), _sentiment_evidence()]
    )
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "scored",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": {
                "status": "scored",
                "score": 8,
                "confidence": "medium",
                "evidence_ids": ["fundamentals.roe_3y_avg", "ip.patent_grant"],
                "rationale": "patent + supporting financials",
            },
            "market_pos": {
                "status": "scored",
                "score": 4,
                "confidence": "medium",
                "evidence_ids": ["disclosure.market_share"],
                "rationale": "top3 market share",
            },
            "sentiment": {
                "status": "scored",
                "score": 4,
                "confidence": "medium",
                "evidence_ids": ["news.major_contract"],
                "rationale": "multi-quarter positive contract",
            },
        },
    }
    result = validate_model_output(output, context=context)
    assert result.valid, result.rejection_reason
    assert result.result is not None
    assert result.result.overall_status == "scored"


def test_missing_evidence_per_dimension_yields_insufficient_data() -> None:
    context = _build_context([_financial_evidence()])  # no moat/industry/sentiment direct evidence at all
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "insufficient_data",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": _insufficient_dim(),
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert result.valid
    assert result.result is not None
    assert result.result.overall_status == "insufficient_data"


def test_nullable_score_reverse_combinations_rejected() -> None:
    context = _build_context([_financial_evidence(), _moat_evidence()])

    # scored + null score
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "scored",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": {
                "status": "scored",
                "score": None,
                "confidence": "medium",
                "evidence_ids": ["fundamentals.roe_3y_avg", "ip.patent_grant"],
                "rationale": "x",
            },
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    assert not validate_model_output(output, context=context).valid

    # insufficient_data + non-null score
    output2 = copy.deepcopy(output)
    dimensions2 = output2["dimensions"]
    assert isinstance(dimensions2, dict)
    moat2 = dimensions2["moat"]
    assert isinstance(moat2, dict)
    moat2["status"] = "insufficient_data"
    moat2["score"] = 5
    assert not validate_model_output(output2, context=context).valid


def test_scored_with_empty_evidence_ids_rejected() -> None:
    context = _build_context([_financial_evidence(), _moat_evidence()])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "scored",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": {"status": "scored", "score": 8, "confidence": "medium", "evidence_ids": [], "rationale": "x"},
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid


def test_model_output_evidence_id_limit_enforced_locally() -> None:
    context = _build_context([_financial_evidence()])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "insufficient_data",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": {
                **_insufficient_dim(),
                "evidence_ids": ["fundamentals.roe_3y_avg"] * (EVIDENCE_IDS_MAX_ITEMS + 1),
            },
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid
    assert f"maximum item count {EVIDENCE_IDS_MAX_ITEMS}" in (result.rejection_reason or "")


def test_unknown_evidence_id_reference_rejected() -> None:
    context = _build_context([_financial_evidence(), _moat_evidence()])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "scored",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": {
                "status": "scored",
                "score": 8,
                "confidence": "medium",
                "evidence_ids": ["does.not.exist"],
                "rationale": "x",
            },
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid


def test_moat_scored_requires_both_direct_moat_and_financial_support() -> None:
    """REQ-007: citing only the moat evidence (no financial support) must not
    satisfy the gate, even though the moat evidence itself is fresh+direct."""
    context = _build_context([_financial_evidence(), _moat_evidence()])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "scored",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": {
                "status": "scored",
                "score": 8,
                "confidence": "medium",
                "evidence_ids": ["ip.patent_grant"],  # missing financial support
                "rationale": "x",
            },
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid
    assert "REQ-007~009/062" in (result.rejection_reason or "")


def test_evidence_exists_in_packet_but_not_cited_does_not_satisfy_gate() -> None:
    """The gate checks the model's actual citation set, not packet availability."""
    context = _build_context([_financial_evidence(), _moat_evidence(), _industry_position_evidence()])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "scored",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            # cites only the financial evidence, not the moat evidence, yet claims scored
            "moat": {
                "status": "scored",
                "score": 8,
                "confidence": "medium",
                "evidence_ids": ["fundamentals.roe_3y_avg"],
                "rationale": "x",
            },
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid


def test_sentiment_one_time_only_cannot_satisfy_scored_gate() -> None:
    """REQ-062/034: one_time-only citation must not support scored."""
    context = _build_context([_financial_evidence(), _sentiment_evidence(persistence_horizon="one_time")])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "scored",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": _insufficient_dim(),
            "market_pos": _insufficient_dim(),
            "sentiment": {
                "status": "scored",
                "score": 4,
                "confidence": "medium",
                "evidence_ids": ["news.major_contract"],
                "rationale": "one-time event",
            },
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid


def test_sentiment_multi_quarter_satisfies_scored_gate() -> None:
    """Context has no moat/industry_position evidence at all, so those two
    dimensions must legitimately be insufficient_data; only sentiment can be
    scored here. overall_status must reflect that mix (insufficient_data),
    not "scored" -- this also exercises REQ-026 overall/dimension consistency
    alongside the REQ-062 persistence gate."""
    context = _build_context([_financial_evidence(), _sentiment_evidence(persistence_horizon="multi_quarter")])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "insufficient_data",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": _insufficient_dim(),
            "market_pos": _insufficient_dim(),
            "sentiment": {
                "status": "scored",
                "score": 4,
                "confidence": "medium",
                "evidence_ids": ["news.major_contract"],
                "rationale": "durable positive development",
            },
        },
    }
    result = validate_model_output(output, context=context)
    assert result.valid, result.rejection_reason
    assert result.result is not None
    assert result.result.dimensions.sentiment.status == "scored"


@pytest.mark.parametrize("score", [1, 5])
def test_sentiment_extreme_score_accepts_major_persistent_direct_evidence(score: int) -> None:
    context = _build_context([_sentiment_evidence()])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "insufficient_data",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": _insufficient_dim(),
            "market_pos": _insufficient_dim(),
            "sentiment": {
                "status": "scored",
                "score": score,
                "confidence": "medium",
                "evidence_ids": ["news.major_contract"],
                "rationale": "major persistent direct evidence",
            },
        },
    }
    result = validate_model_output(output, context=context)
    assert result.valid, result.rejection_reason


@pytest.mark.parametrize(
    ("evidence", "reason_fragment"),
    [
        (_sentiment_evidence(materiality="moderate"), "major persistent direct"),
        (_sentiment_evidence(persistence_horizon="one_time"), "REQ-007~009/062"),
        (_sentiment_evidence(directness="supporting"), "REQ-007~009/062"),
    ],
)
def test_sentiment_extreme_score_rejects_missing_major_persistent_direct_requirement(
    evidence: dict[str, object], reason_fragment: str
) -> None:
    context = _build_context([evidence])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "insufficient_data",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": _insufficient_dim(),
            "market_pos": _insufficient_dim(),
            "sentiment": {
                "status": "scored",
                "score": 5,
                "confidence": "medium",
                "evidence_ids": ["news.major_contract"],
                "rationale": "unsupported extreme",
            },
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid
    assert reason_fragment in (result.rejection_reason or "")


def test_all_or_nothing_one_invalid_dimension_invalidates_whole_result() -> None:
    """REQ-029: even though market_pos/sentiment are fine, an invalid moat
    dimension must invalidate the entire result -- not be adopted partially."""
    context = _build_context([_financial_evidence(), _moat_evidence(), _industry_position_evidence()])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "scored",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": {
                "status": "scored",
                "score": 99,  # out of range 1-10
                "confidence": "medium",
                "evidence_ids": ["fundamentals.roe_3y_avg", "ip.patent_grant"],
                "rationale": "x",
            },
            "market_pos": {
                "status": "scored",
                "score": 4,
                "confidence": "medium",
                "evidence_ids": ["disclosure.market_share"],
                "rationale": "valid",
            },
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid


def test_schema_version_mismatch_rejected() -> None:
    context = _build_context([_financial_evidence()])
    output = {
        "schema_version": "qualitative-score-v3",  # mismatched
        "overall_status": "insufficient_data",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": _insufficient_dim(),
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid


def test_model_declaring_invalid_status_rejected() -> None:
    """Section 6.3: 'invalid' is local-code-only; the model must not return it."""
    context = _build_context([_financial_evidence()])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "invalid",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": _insufficient_dim(),
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid


def test_overall_status_inconsistent_with_dimensions_rejected() -> None:
    context = _build_context([_financial_evidence(), _moat_evidence()])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "scored",  # but a dimension is insufficient_data
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": {
                "status": "scored",
                "score": 8,
                "confidence": "medium",
                "evidence_ids": ["fundamentals.roe_3y_avg", "ip.patent_grant"],
                "rationale": "x",
            },
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid


def test_extra_top_level_field_rejected() -> None:
    context = _build_context([_financial_evidence()])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "insufficient_data",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": _insufficient_dim(),
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
        "extra": "field",
    }
    result = validate_model_output(output, context=context)
    assert not result.valid


def test_claim_category_registry_still_internally_consistent() -> None:
    """Sanity check tying this test module to the taxonomy registry so a
    future accidental registry edit is caught here too, not only in
    test_qualitative_v2_taxonomy.py."""
    assert "competitive_moat" in CLAIM_CATEGORY_REGISTRY
    assert "context" in CLAIM_CATEGORY_REGISTRY["competitive_moat"].directness_values


def test_empty_rationale_rejected() -> None:
    context = _build_context([_financial_evidence()])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "insufficient_data",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": {
                "status": "insufficient_data",
                "score": None,
                "confidence": "low",
                "evidence_ids": [],
                "rationale": "",
            },
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid


def test_whitespace_only_rationale_rejected() -> None:
    context = _build_context([_financial_evidence()])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "insufficient_data",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": {
                "status": "insufficient_data",
                "score": None,
                "confidence": "low",
                "evidence_ids": [],
                "rationale": "   ",
            },
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid


@pytest.mark.parametrize(("length", "expected_valid"), [(500, True), (501, False)])
def test_rationale_character_limit_boundary(length: int, expected_valid: bool) -> None:
    context = _build_context([])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "insufficient_data",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": {**_insufficient_dim(), "rationale": "理" * length},
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert result.valid is expected_valid


def test_non_string_evidence_id_reference_in_output_rejected() -> None:
    """codex review Important #1: a bare int evidence_id reference must be
    rejected, not coerced via str() and treated as equal to its string twin."""
    context = _build_context([_financial_evidence(), _moat_evidence()])
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "scored",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": {
                "status": "scored",
                "score": 8,
                "confidence": "medium",
                "evidence_ids": [123, "ip.patent_grant"],
                "rationale": "x",
            },
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid


def test_hand_built_context_with_fabricated_fresh_status_does_not_bypass_recomputed_freshness() -> None:
    """codex review Important #3 (defense in depth): validate_model_output
    must not trust a QualitativeContext/Evidence's stored freshness_status
    if it bypasses validate_context_dict -- freshness is recomputed from
    source_date/canonical policy at citation-check time."""
    stale_but_claims_fresh = Evidence(
        evidence_id="ip.old_patent",
        evidence_type="ip_record",
        claim_category="competitive_moat",
        allowed_dimensions=("moat",),
        directness="direct",
        freshness_policy="max_age_365d",
        value="granted",
        unit=None,
        source="cninfo_filing",
        source_date="2020-01-01",  # far more than 365 days before AS_OF_DATE
        freshness_status="fresh",  # fabricated -- should not be trusted
        state_type="event",
    )
    financial = Evidence(**_financial_evidence())  # type: ignore[arg-type]
    context = QualitativeContext(
        code="601899",
        name="紫金矿业",
        industry="铜",
        as_of_date=AS_OF_DATE,
        schema_version="qualitative-score-v2",
        rubric_version="rubric-v1",
        taxonomy_version="taxonomy-v1",
        evidence=(financial, stale_but_claims_fresh),
    )
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "scored",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": {
                "status": "scored",
                "score": 8,
                "confidence": "medium",
                "evidence_ids": ["fundamentals.roe_3y_avg", "ip.old_patent"],
                "rationale": "x",
            },
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid


def test_hand_built_context_with_invalid_namespace_is_rejected() -> None:
    invalid = Evidence(
        evidence_id="news.wrong_namespace",
        evidence_type="financial_metric",
        claim_category="financial_performance",
        allowed_dimensions=("moat",),
        directness="supporting",
        freshness_policy="max_age_550d",
        value=14.2,
        unit="percent",
        source="local_fundamentals_cache",
        source_date="2026-03-31",
        freshness_status="fresh",
    )
    context = QualitativeContext(
        code="601899",
        name="紫金矿业",
        industry="铜",
        as_of_date=AS_OF_DATE,
        schema_version="qualitative-score-v2",
        rubric_version="rubric-v1",
        taxonomy_version="taxonomy-v1",
        evidence=(invalid,),
    )
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "insufficient_data",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": _insufficient_dim(),
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid
    assert "invalid context" in (result.rejection_reason or "")
    assert "namespace" in (result.rejection_reason or "")


def test_hand_built_context_with_unknown_taxonomy_fails_closed_without_throwing() -> None:
    invalid = Evidence(
        evidence_id="fundamentals.unknown",
        evidence_type="financial_metric",
        claim_category="unknown_category",
        allowed_dimensions=("moat",),
        directness="supporting",
        freshness_policy="max_age_550d",
        value=1.0,
        unit=None,
        source="fixture",
        source_date="2026-07-01",
        freshness_status="fresh",
    )
    context = QualitativeContext(
        code="601899",
        name="紫金矿业",
        industry="铜",
        as_of_date=AS_OF_DATE,
        schema_version="qualitative-score-v2",
        rubric_version="rubric-v1",
        taxonomy_version="taxonomy-v1",
        evidence=(invalid,),
    )
    output = {
        "schema_version": "qualitative-score-v2",
        "overall_status": "insufficient_data",
        "as_of_date": AS_OF_DATE,
        "dimensions": {
            "moat": _insufficient_dim(),
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    result = validate_model_output(output, context=context)
    assert not result.valid
    assert "unknown claim_category" in (result.rejection_reason or "")
