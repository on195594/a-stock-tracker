"""Fixture-first tests for qualitative_v2_types.py (spec REQ-001~003, REQ-002 input_hash)."""

from __future__ import annotations

from qualitative_v2_types import DimensionResult, Evidence, QualitativeContext, ScoringResult


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
        evidence_id="industry.market_share",
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


def test_scoring_result_dimension_accessor() -> None:
    dim = DimensionResult(
        status="insufficient_data", score=None, confidence="low", evidence_ids=(), rationale="no evidence"
    )
    result = ScoringResult(
        schema_version="qualitative-score-v2",
        overall_status="insufficient_data",
        as_of_date="2026-07-14",
        moat=dim,
        market_pos=dim,
        sentiment=dim,
    )
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
        moat=dim,
        market_pos=dim,
        sentiment=dim,
    )
    try:
        result.dimension("not_a_dimension")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
