"""Fixture-first tests for qualitative_v2_taxonomy.py (spec section 6.1, REQ-005, REQ-064)."""

from __future__ import annotations

from datetime import date

import pytest

import qualitative_v2_taxonomy as taxonomy


def test_claim_category_registry_covers_all_claim_categories() -> None:
    assert set(taxonomy.CLAIM_CATEGORY_REGISTRY.keys()) == taxonomy.CLAIM_CATEGORIES


def test_evidence_type_matrix_covers_all_evidence_types() -> None:
    assert set(taxonomy.EVIDENCE_TYPE_CLAIM_CATEGORY_MATRIX.keys()) == taxonomy.EVIDENCE_TYPES


def test_financial_performance_and_valuation_cannot_be_direct() -> None:
    assert "direct" not in taxonomy.CLAIM_CATEGORY_REGISTRY["financial_performance"].directness_values
    assert "direct" not in taxonomy.CLAIM_CATEGORY_REGISTRY["valuation"].directness_values


def test_valuation_has_no_allowed_dimensions() -> None:
    assert taxonomy.canonical_allowed_dimensions("valuation") == frozenset()


@pytest.mark.parametrize(
    ("evidence_type", "claim_category", "expected"),
    [
        ("financial_metric", "financial_performance", True),
        ("financial_metric", "market_sentiment", False),
        ("ip_record", "competitive_moat", True),
        ("ip_record", "industry_position", False),
        ("news_report", "competitive_moat", True),
        ("news_report", "market_sentiment", True),
        ("analyst_consensus", "financial_performance", False),
        ("regulatory_filing", "market_sentiment", False),
    ],
)
def test_evidence_type_claim_category_compatibility_matrix(
    evidence_type: str, claim_category: str, expected: bool
) -> None:
    assert taxonomy.is_evidence_type_claim_category_combo_valid(evidence_type, claim_category) is expected


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
