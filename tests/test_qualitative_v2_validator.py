"""Fixture-first tests for qualitative_v2_validator.py, covering spec section 8
(Fixture-first test matrix) for REQ-005/007~009/019/023~030/062/064."""

from __future__ import annotations

import copy
from datetime import date

from qualitative_v2_validator import (
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


def _moat_evidence(evidence_id: str = "competitive_advantage.patent_grant") -> dict[str, object]:
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


def _industry_position_evidence(evidence_id: str = "industry.market_share") -> dict[str, object]:
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
    evidence_id: str = "news.major_contract", persistence_horizon: str = "multi_quarter"
) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "evidence_type": "news_report",
        "claim_category": "market_sentiment",
        "allowed_dimensions": ["sentiment"],
        "directness": "direct",
        "freshness_policy": "max_age_30d",
        "value": "positive",
        "unit": None,
        "source": "reuters",
        "source_date": "2026-07-01",
        "freshness_status": "fresh",
        "persistence_horizon": persistence_horizon,
        "materiality": "major",
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


# ---------------------------------------------------------------------------
# Context-level validation (REQ-001~005)
# ---------------------------------------------------------------------------


def test_valid_context_accepted() -> None:
    result = validate_context_dict(_full_context_dict([_financial_evidence(), _moat_evidence()]))
    assert result.valid
    assert result.context is not None
    assert len(result.context.evidence) == 2


def test_duplicate_evidence_id_rejected() -> None:
    result = validate_context_dict(_full_context_dict([_financial_evidence("dup.id"), _moat_evidence("dup.id")]))
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
                "evidence_ids": ["fundamentals.roe_3y_avg", "competitive_advantage.patent_grant"],
                "rationale": "patent + supporting financials",
            },
            "market_pos": {
                "status": "scored",
                "score": 4,
                "confidence": "medium",
                "evidence_ids": ["industry.market_share"],
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
                "evidence_ids": ["fundamentals.roe_3y_avg", "competitive_advantage.patent_grant"],
                "rationale": "x",
            },
            "market_pos": _insufficient_dim(),
            "sentiment": _insufficient_dim(),
        },
    }
    assert not validate_model_output(output, context=context).valid

    # insufficient_data + non-null score
    output2 = copy.deepcopy(output)
    output2["dimensions"]["moat"]["status"] = "insufficient_data"
    output2["dimensions"]["moat"]["score"] = 5
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
                "evidence_ids": ["competitive_advantage.patent_grant"],  # missing financial support
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
                "evidence_ids": ["fundamentals.roe_3y_avg", "competitive_advantage.patent_grant"],
                "rationale": "x",
            },
            "market_pos": {
                "status": "scored",
                "score": 4,
                "confidence": "medium",
                "evidence_ids": ["industry.market_share"],
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
                "evidence_ids": ["fundamentals.roe_3y_avg", "competitive_advantage.patent_grant"],
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
