"""Fixture-first tests for qualitative_v2_types.py (spec REQ-001~003, REQ-002 input_hash)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from qualitative_v2_types import DimensionResult, Dimensions, Evidence, QualitativeContext, ScoringResult


def _make_evidence(evidence_id: str = "fundamentals.roe_3y_avg") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
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


def _make_context(evidence: tuple[Evidence, ...] = ()) -> QualitativeContext:
    return QualitativeContext(
        code="601899",
        name="紫金矿业",
        industry="铜",
        as_of_date="2026-07-14",
        schema_version="qualitative-score-v2",
        rubric_version="rubric-v1",
        taxonomy_version="taxonomy-v1",
        evidence=evidence,
    )


def test_input_hash_stable_for_identical_content() -> None:
    ctx_a = _make_context((_make_evidence(),))
    ctx_b = _make_context((_make_evidence(),))
    assert ctx_a.compute_input_hash() == ctx_b.compute_input_hash()


def test_input_hash_stable_regardless_of_evidence_order() -> None:
    e1 = _make_evidence("fundamentals.roe_3y_avg")
    e2 = _make_evidence("fundamentals.gross_margin")
    ctx_forward = _make_context((e1, e2))
    ctx_reversed = _make_context((e2, e1))
    assert ctx_forward.compute_input_hash() == ctx_reversed.compute_input_hash()


def test_input_hash_stable_for_duplicate_ids_with_different_content_regardless_of_order() -> None:
    first = _make_evidence("fundamentals.duplicate")
    second = replace(first, value=99.9, source="alternate_source")

    ctx_forward = _make_context((first, second))
    ctx_reversed = _make_context((second, first))

    assert ctx_forward.compute_input_hash() == ctx_reversed.compute_input_hash()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_input_hash_rejects_non_finite_numeric_evidence(value: float) -> None:
    context = _make_context((replace(_make_evidence(), value=value),))

    with pytest.raises(ValueError, match="Out of range float values are not JSON compliant"):
        context.compute_input_hash()


def test_input_hash_changes_when_evidence_content_changes() -> None:
    ctx_a = _make_context((_make_evidence(),))
    changed_evidence = Evidence(
        evidence_id="fundamentals.roe_3y_avg",
        evidence_type="financial_metric",
        claim_category="financial_performance",
        allowed_dimensions=("moat",),
        directness="supporting",
        freshness_policy="max_age_550d",
        value=99.9,  # changed value
        unit="percent",
        source="local_fundamentals_cache",
        source_date="2026-03-31",
        freshness_status="fresh",
    )
    ctx_b = _make_context((changed_evidence,))
    assert ctx_a.compute_input_hash() != ctx_b.compute_input_hash()


def test_input_hash_changes_when_as_of_date_changes() -> None:
    ctx_a = _make_context((_make_evidence(),))
    ctx_b = QualitativeContext(
        code=ctx_a.code,
        name=ctx_a.name,
        industry=ctx_a.industry,
        as_of_date="2026-07-15",
        schema_version=ctx_a.schema_version,
        rubric_version=ctx_a.rubric_version,
        taxonomy_version=ctx_a.taxonomy_version,
        evidence=ctx_a.evidence,
    )
    assert ctx_a.compute_input_hash() != ctx_b.compute_input_hash()


def test_input_hash_changes_when_name_changes() -> None:
    ctx_a = _make_context((_make_evidence(),))
    ctx_b = QualitativeContext(
        code=ctx_a.code,
        name="紫金矿业股份有限公司",
        industry=ctx_a.industry,
        as_of_date=ctx_a.as_of_date,
        schema_version=ctx_a.schema_version,
        rubric_version=ctx_a.rubric_version,
        taxonomy_version=ctx_a.taxonomy_version,
        evidence=ctx_a.evidence,
    )
    assert ctx_a.compute_input_hash() != ctx_b.compute_input_hash()


def test_input_hash_changes_when_industry_changes() -> None:
    ctx_a = _make_context((_make_evidence(),))
    ctx_b = QualitativeContext(
        code=ctx_a.code,
        name=ctx_a.name,
        industry="有色金属",
        as_of_date=ctx_a.as_of_date,
        schema_version=ctx_a.schema_version,
        rubric_version=ctx_a.rubric_version,
        taxonomy_version=ctx_a.taxonomy_version,
        evidence=ctx_a.evidence,
    )
    assert ctx_a.compute_input_hash() != ctx_b.compute_input_hash()


def test_input_hash_changes_when_rubric_version_changes() -> None:
    ctx_a = _make_context((_make_evidence(),))
    ctx_b = QualitativeContext(
        code=ctx_a.code,
        name=ctx_a.name,
        industry=ctx_a.industry,
        as_of_date=ctx_a.as_of_date,
        schema_version=ctx_a.schema_version,
        rubric_version="rubric-v2",
        taxonomy_version=ctx_a.taxonomy_version,
        evidence=ctx_a.evidence,
    )
    assert ctx_a.compute_input_hash() != ctx_b.compute_input_hash()


def test_input_hash_changes_when_schema_version_changes() -> None:
    ctx_a = _make_context((_make_evidence(),))
    ctx_b = QualitativeContext(
        code=ctx_a.code,
        name=ctx_a.name,
        industry=ctx_a.industry,
        as_of_date=ctx_a.as_of_date,
        schema_version="qualitative-score-v3",
        rubric_version=ctx_a.rubric_version,
        taxonomy_version=ctx_a.taxonomy_version,
        evidence=ctx_a.evidence,
    )
    assert ctx_a.compute_input_hash() != ctx_b.compute_input_hash()


def test_input_hash_changes_when_taxonomy_version_changes() -> None:
    ctx_a = _make_context((_make_evidence(),))
    ctx_b = QualitativeContext(
        code=ctx_a.code,
        name=ctx_a.name,
        industry=ctx_a.industry,
        as_of_date=ctx_a.as_of_date,
        schema_version=ctx_a.schema_version,
        rubric_version=ctx_a.rubric_version,
        taxonomy_version="taxonomy-v2",
        evidence=ctx_a.evidence,
    )
    assert ctx_a.compute_input_hash() != ctx_b.compute_input_hash()


def test_evidence_canonical_dict_sorts_allowed_dimensions() -> None:
    evidence = Evidence(
        evidence_id="disclosure.market_share",
        evidence_type="company_disclosure",
        claim_category="industry_position",
        allowed_dimensions=("market_pos",),
        directness="direct",
        freshness_policy="max_age_365d",
        value="top3",
        unit=None,
        source="annual_report",
        source_date="2026-01-01",
        freshness_status="fresh",
    )
    canonical = evidence.canonical_dict()
    assert canonical["allowed_dimensions"] == ["market_pos"]


def test_scoring_result_wraps_three_dimensions_under_dimensions_field() -> None:
    """REQ-015/016: top-level must be schema_version/overall_status/as_of_date/dimensions,
    with moat/market_pos/sentiment nested under dimensions -- not flattened at top level
    (codex review caught the original flattened draft)."""
    dim = DimensionResult(
        status="insufficient_data", score=None, confidence="low", evidence_ids=(), rationale="no evidence"
    )
    result = ScoringResult(
        schema_version="qualitative-score-v2",
        overall_status="insufficient_data",
        as_of_date="2026-07-14",
        dimensions=Dimensions(moat=dim, market_pos=dim, sentiment=dim),
    )
    assert not hasattr(result, "moat")
    assert result.dimensions.moat is dim
    assert result.dimension("moat") is dim
    assert result.dimension("market_pos") is dim
    assert result.dimension("sentiment") is dim


def test_scoring_result_dimension_accessor_rejects_unknown_name() -> None:
    dim = DimensionResult(
        status="insufficient_data", score=None, confidence="low", evidence_ids=(), rationale="no evidence"
    )
    result = ScoringResult(
        schema_version="qualitative-score-v2",
        overall_status="insufficient_data",
        as_of_date="2026-07-14",
        dimensions=Dimensions(moat=dim, market_pos=dim, sentiment=dim),
    )
    try:
        result.dimension("not_a_dimension")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
