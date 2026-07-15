"""Fixture-first tests for qualitative_v2_taxonomy.py (spec section 6.1, REQ-005, REQ-064)."""

from __future__ import annotations

from datetime import date

import pytest

import qualitative_v2_taxonomy as taxonomy
from qualitative_v2_contract import DIMENSION_NAMES


def test_taxonomy_dimensions_are_aligned_with_central_contract() -> None:
    assert taxonomy.DIMENSIONS == frozenset(DIMENSION_NAMES)


def test_claim_category_registry_covers_all_claim_categories() -> None:
    assert set(taxonomy.CLAIM_CATEGORY_REGISTRY.keys()) == taxonomy.CLAIM_CATEGORIES


def test_evidence_type_matrix_covers_all_evidence_types() -> None:
    assert set(taxonomy.EVIDENCE_TYPE_CLAIM_CATEGORY_MATRIX.keys()) == taxonomy.EVIDENCE_TYPES


def test_evidence_type_namespace_registry_covers_all_types_exactly() -> None:
    assert taxonomy.EVIDENCE_TYPE_ID_PREFIXES == {
        "financial_metric": "fundamentals.",
        "valuation_metric": "valuation.",
        "company_disclosure": "disclosure.",
        "regulatory_filing": "regulatory.",
        "ip_record": "ip.",
        "counterparty_disclosure": "counterparty.",
        "news_report": "news.",
        "analyst_consensus": "consensus.",
    }
    assert set(taxonomy.EVIDENCE_TYPE_ID_PREFIXES) == taxonomy.EVIDENCE_TYPES


def test_evidence_id_namespace_requires_matching_nonempty_suffix() -> None:
    assert taxonomy.is_evidence_id_namespace_valid("news_report", "news.major_contract")
    assert not taxonomy.is_evidence_id_namespace_valid("news_report", "disclosure.major_contract")
    assert not taxonomy.is_evidence_id_namespace_valid("news_report", "news.")
    assert not taxonomy.is_evidence_id_namespace_valid("unknown", "news.major_contract")


def test_financial_performance_and_valuation_cannot_be_direct() -> None:
    assert "direct" not in taxonomy.CLAIM_CATEGORY_REGISTRY["financial_performance"].directness_values
    assert "direct" not in taxonomy.CLAIM_CATEGORY_REGISTRY["valuation"].directness_values


def test_context_directness_allowed_for_moat_industry_position_and_sentiment() -> None:
    """codex review: only financial_performance/valuation restrict directness beyond the
    full direct|supporting|context set (spec 6.1); the other three claim_categories must
    still permit context evidence (it just can't alone trigger scored -- validator's job)."""
    for claim_category in ("competitive_moat", "industry_position", "market_sentiment"):
        assert taxonomy.is_directness_allowed(claim_category, "context") is True


def test_valuation_has_no_allowed_dimensions() -> None:
    assert taxonomy.canonical_allowed_dimensions("valuation") == frozenset()


def test_evidence_type_claim_category_compatibility_matrix_matches_spec_6_1_3_exactly() -> None:
    """Asserts the full 8x5 matrix against spec section 6.1.3, not a sample of cells."""
    expected: dict[str, frozenset[str]] = {
        "financial_metric": frozenset({"financial_performance"}),
        "valuation_metric": frozenset({"valuation"}),
        "company_disclosure": frozenset({"competitive_moat", "industry_position", "market_sentiment"}),
        "regulatory_filing": frozenset({"competitive_moat", "industry_position"}),
        "ip_record": frozenset({"competitive_moat"}),
        "counterparty_disclosure": frozenset({"competitive_moat", "industry_position"}),
        "news_report": frozenset({"competitive_moat", "industry_position", "market_sentiment"}),
        "analyst_consensus": frozenset({"industry_position", "market_sentiment"}),
    }
    assert taxonomy.EVIDENCE_TYPE_CLAIM_CATEGORY_MATRIX == expected

    for evidence_type in taxonomy.EVIDENCE_TYPES:
        for claim_category in taxonomy.CLAIM_CATEGORIES:
            actual = taxonomy.is_evidence_type_claim_category_combo_valid(evidence_type, claim_category)
            want = claim_category in expected[evidence_type]
            assert actual is want, f"{evidence_type} x {claim_category}: expected {want}, got {actual}"


def test_unknown_claim_category_raises() -> None:
    with pytest.raises(ValueError):
        taxonomy.canonical_allowed_dimensions("not_a_real_category")


def test_freshness_event_type_boundary_day_is_fresh() -> None:
    as_of = date(2026, 7, 14)
    source_date = as_of.fromordinal(as_of.toordinal() - 30)
    status = taxonomy.compute_freshness_status(
        as_of_date=as_of,
        source_date=source_date,
        freshness_policy="max_age_30d",
    )
    assert status == "fresh"


def test_freshness_event_type_one_day_past_boundary_is_stale() -> None:
    as_of = date(2026, 7, 14)
    source_date = as_of.fromordinal(as_of.toordinal() - 31)
    status = taxonomy.compute_freshness_status(
        as_of_date=as_of,
        source_date=source_date,
        freshness_policy="max_age_30d",
    )
    assert status == "stale"


def test_freshness_future_source_date_rejected() -> None:
    as_of = date(2026, 7, 14)
    future_source = date(2026, 7, 15)
    with pytest.raises(ValueError):
        taxonomy.compute_freshness_status(
            as_of_date=as_of,
            source_date=future_source,
            freshness_policy="max_age_30d",
        )


def test_freshness_status_type_uses_effective_until_not_policy_days(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-064: a status-type evidence published >365d ago but still legally
    effective must be fresh, even though claim_category's freshness_policy is
    max_age_365d."""
    as_of = date(2026, 7, 14)
    old_source_date = date(2020, 1, 1)  # far more than 365 days ago
    still_valid_until = date(2030, 1, 1)
    status = taxonomy.compute_freshness_status(
        as_of_date=as_of,
        source_date=old_source_date,
        freshness_policy="max_age_365d",
        state_type="status",
        effective_until=still_valid_until,
    )
    assert status == "fresh"


def test_freshness_status_type_expired_effective_until_is_stale() -> None:
    as_of = date(2026, 7, 14)
    source_date = date(2025, 1, 1)
    expired = date(2026, 1, 1)
    status = taxonomy.compute_freshness_status(
        as_of_date=as_of,
        source_date=source_date,
        freshness_policy="max_age_365d",
        state_type="status",
        effective_until=expired,
    )
    assert status == "stale"


def test_freshness_status_type_no_effective_until_is_fresh() -> None:
    as_of = date(2026, 7, 14)
    source_date = date(2015, 1, 1)
    status = taxonomy.compute_freshness_status(
        as_of_date=as_of,
        source_date=source_date,
        freshness_policy="max_age_365d",
        state_type="status",
        effective_until=None,
    )
    assert status == "fresh"


def test_requires_state_type_only_for_specific_evidence_types() -> None:
    assert taxonomy.requires_state_type("ip_record") is True
    assert taxonomy.requires_state_type("regulatory_filing") is True
    assert taxonomy.requires_state_type("counterparty_disclosure") is True
    assert taxonomy.requires_state_type("financial_metric") is False
    assert taxonomy.requires_state_type("news_report") is False


def test_requires_persistence_fields_only_for_market_sentiment() -> None:
    assert taxonomy.requires_persistence_fields("market_sentiment") is True
    assert taxonomy.requires_persistence_fields("competitive_moat") is False
    assert taxonomy.requires_persistence_fields("financial_performance") is False
