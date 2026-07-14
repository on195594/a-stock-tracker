"""
Evidence taxonomy registry for the source-grounded structured qualitative
scoring contract (see docs/specs/2026-07-14-source-grounded-structured-
qualitative-scoring-spec.md).

Two orthogonal axes per REQ-003~005 and section 6.1:
  - evidence_type: where the evidence came from (carrier/source).
  - claim_category: what investment claim the evidence is being used to
    support. allowed_dimensions and freshness_policy are derived from
    claim_category only, never from evidence_type directly (REQ-003~005).

This module is pure data + freshness arithmetic. It does not perform
evidence-level contract validation (schema shape, evidence_id citation
gates, all-or-nothing semantics) -- that lives in qualitative_v2_validator.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

EvidenceType = str
ClaimCategory = str
Dimension = str
Directness = str
FreshnessPolicy = str
StateType = str
PersistenceHorizon = str
Materiality = str

EVIDENCE_TYPES: frozenset[EvidenceType] = frozenset(
    {
        "financial_metric",
        "valuation_metric",
        "company_disclosure",
        "regulatory_filing",
        "ip_record",
        "counterparty_disclosure",
        "news_report",
        "analyst_consensus",
    }
)

CLAIM_CATEGORIES: frozenset[ClaimCategory] = frozenset(
    {
        "financial_performance",
        "valuation",
        "competitive_moat",
        "industry_position",
        "market_sentiment",
    }
)

DIMENSIONS: frozenset[Dimension] = frozenset({"moat", "market_pos", "sentiment"})

DIRECTNESS_VALUES: frozenset[Directness] = frozenset({"direct", "supporting", "context"})

FRESHNESS_POLICIES: frozenset[FreshnessPolicy] = frozenset(
    {"max_age_30d", "max_age_90d", "max_age_365d", "max_age_550d"}
)

# evidence_type values whose evidence may represent an ongoing right/contract
# rather than a one-time event (REQ-064). Only these may declare state_type.
STATE_TYPE_ELIGIBLE_EVIDENCE_TYPES: frozenset[EvidenceType] = frozenset(
    {"regulatory_filing", "ip_record", "counterparty_disclosure"}
)

STATE_TYPES: frozenset[StateType] = frozenset({"event", "status"})

PERSISTENCE_HORIZONS: frozenset[PersistenceHorizon] = frozenset({"one_time", "multi_quarter", "structural"})

MATERIALITY_LEVELS: frozenset[Materiality] = frozenset({"minor", "moderate", "major"})


@dataclass(frozen=True)
class ClaimCategorySpec:
    """Section 6.1.2: canonical allowed_dimensions + freshness_policy per claim_category."""

    allowed_dimensions: frozenset[Dimension]
    freshness_policy: FreshnessPolicy
    directness_values: frozenset[Directness]


# Section 6.1.2. financial_performance and valuation may never use "direct"
# (financial/valuation metrics cannot directly prove moat or anything beyond
# valuation itself -- see the directness rule in 6.1).
CLAIM_CATEGORY_REGISTRY: dict[ClaimCategory, ClaimCategorySpec] = {
    "financial_performance": ClaimCategorySpec(
        allowed_dimensions=frozenset({"moat"}),
        freshness_policy="max_age_550d",
        directness_values=frozenset({"supporting", "context"}),
    ),
    "valuation": ClaimCategorySpec(
        allowed_dimensions=frozenset(),
        freshness_policy="max_age_30d",
        directness_values=frozenset({"context"}),
    ),
    "competitive_moat": ClaimCategorySpec(
        allowed_dimensions=frozenset({"moat"}),
        freshness_policy="max_age_365d",
        directness_values=frozenset({"direct", "supporting", "context"}),
    ),
    "industry_position": ClaimCategorySpec(
        allowed_dimensions=frozenset({"market_pos"}),
        freshness_policy="max_age_365d",
        directness_values=frozenset({"direct", "supporting", "context"}),
    ),
    "market_sentiment": ClaimCategorySpec(
        allowed_dimensions=frozenset({"sentiment"}),
        freshness_policy="max_age_30d",
        directness_values=frozenset({"direct", "supporting", "context"}),
    ),
}

# Section 6.1.3: which evidence_type (carrier) may declare which claim_category.
EVIDENCE_TYPE_CLAIM_CATEGORY_MATRIX: dict[EvidenceType, frozenset[ClaimCategory]] = {
    "financial_metric": frozenset({"financial_performance"}),
    "valuation_metric": frozenset({"valuation"}),
    "company_disclosure": frozenset({"competitive_moat", "industry_position", "market_sentiment"}),
    "regulatory_filing": frozenset({"competitive_moat", "industry_position"}),
    "ip_record": frozenset({"competitive_moat"}),
    "counterparty_disclosure": frozenset({"competitive_moat", "industry_position"}),
    "news_report": frozenset({"competitive_moat", "industry_position", "market_sentiment"}),
    "analyst_consensus": frozenset({"industry_position", "market_sentiment"}),
}

_FRESHNESS_POLICY_DAYS: dict[FreshnessPolicy, int] = {
    "max_age_30d": 30,
    "max_age_90d": 90,
    "max_age_365d": 365,
    "max_age_550d": 550,
}


def is_evidence_type_claim_category_combo_valid(evidence_type: EvidenceType, claim_category: ClaimCategory) -> bool:
    """REQ-005: reject evidence_type x claim_category combinations outside 6.1.3."""
    allowed = EVIDENCE_TYPE_CLAIM_CATEGORY_MATRIX.get(evidence_type)
    return allowed is not None and claim_category in allowed


def canonical_allowed_dimensions(claim_category: ClaimCategory) -> frozenset[Dimension]:
    """Canonical allowed_dimensions for a claim_category (section 6.1.2)."""
    spec = CLAIM_CATEGORY_REGISTRY.get(claim_category)
    if spec is None:
        raise ValueError(f"unknown claim_category: {claim_category!r}")
    return spec.allowed_dimensions


def canonical_freshness_policy(claim_category: ClaimCategory) -> FreshnessPolicy:
    """Canonical freshness_policy for a claim_category (section 6.1.2).

    Only applies to state_type="event" evidence (or evidence_type not eligible
    for state_type at all). state_type="status" evidence uses effective_until
    instead -- see compute_freshness_status().
    """
    spec = CLAIM_CATEGORY_REGISTRY.get(claim_category)
    if spec is None:
        raise ValueError(f"unknown claim_category: {claim_category!r}")
    return spec.freshness_policy


def is_directness_allowed(claim_category: ClaimCategory, directness: Directness) -> bool:
    spec = CLAIM_CATEGORY_REGISTRY.get(claim_category)
    if spec is None:
        raise ValueError(f"unknown claim_category: {claim_category!r}")
    return directness in spec.directness_values


def compute_freshness_status(
    *,
    as_of_date: date,
    source_date: date,
    freshness_policy: FreshnessPolicy,
    state_type: StateType | None = None,
    effective_until: date | None = None,
) -> str:
    """Return "fresh" or "stale" per section 6.1 / REQ-064.

    Event-type evidence (state_type is None or "event") decays by
    (as_of_date - source_date) against freshness_policy's day count,
    inclusive of the boundary day.

    Status-type evidence (state_type == "status") represents an ongoing
    right/contract: freshness is determined by whether effective_until is
    still current (>= as_of_date), or unbounded if effective_until is None.
    The claim_category's freshness_policy day count does not apply.

    Future source_date (source_date > as_of_date) always raises ValueError;
    callers must reject the input before computing freshness (REQ-005).
    """
    if source_date > as_of_date:
        raise ValueError("source_date is in the future relative to as_of_date")

    if state_type == "status":
        if effective_until is None:
            return "fresh"
        if effective_until < as_of_date:
            return "stale"
        return "fresh"

    max_age_days = _FRESHNESS_POLICY_DAYS.get(freshness_policy)
    if max_age_days is None:
        raise ValueError(f"unknown freshness_policy: {freshness_policy!r}")
    age_days = (as_of_date - source_date).days
    return "fresh" if age_days <= max_age_days else "stale"


def requires_state_type(evidence_type: EvidenceType) -> bool:
    """REQ-064: does this evidence_type support (and thus require declaring) state_type?"""
    return evidence_type in STATE_TYPE_ELIGIBLE_EVIDENCE_TYPES


def requires_persistence_fields(claim_category: ClaimCategory) -> bool:
    """REQ-061: does this claim_category require persistence_horizon/materiality?"""
    return claim_category == "market_sentiment"


__all__ = [
    "EVIDENCE_TYPES",
    "CLAIM_CATEGORIES",
    "DIMENSIONS",
    "DIRECTNESS_VALUES",
    "FRESHNESS_POLICIES",
    "STATE_TYPE_ELIGIBLE_EVIDENCE_TYPES",
    "STATE_TYPES",
    "PERSISTENCE_HORIZONS",
    "MATERIALITY_LEVELS",
    "ClaimCategorySpec",
    "CLAIM_CATEGORY_REGISTRY",
    "EVIDENCE_TYPE_CLAIM_CATEGORY_MATRIX",
    "is_evidence_type_claim_category_combo_valid",
    "canonical_allowed_dimensions",
    "canonical_freshness_policy",
    "is_directness_allowed",
    "compute_freshness_status",
    "requires_state_type",
    "requires_persistence_fields",
]
