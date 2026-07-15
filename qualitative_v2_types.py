"""
Dataclasses for the source-grounded structured qualitative scoring contract
(see docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md).

These types are pure data containers (REQ-001~003). They intentionally do not
enforce taxonomy/business rules in __post_init__ -- qualitative_v2_validator.py
is the single source of truth for contract validation (REQ-023), so that there
is exactly one place evidence/schema rules can diverge from, not two.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Evidence:
    """One item of the evidence packet (section 6.1).

    ⚠️ codex review note on state_type/effective_until (REQ-064): this
    dataclass defaults `effective_until` to None, which makes "field omitted
    entirely" and "field explicitly set to null" indistinguishable once an
    Evidence object has been constructed. REQ-064 requires the validator to
    reject `state_type=status` evidence that is *missing* `effective_until`,
    while still accepting an *explicit* null (meaning no fixed term). The
    validator (qualitative_v2_validator.py) MUST therefore check field
    presence on the raw input (dict/JSON) BEFORE constructing an Evidence
    object -- constructing Evidence first and inspecting `.effective_until`
    afterwards cannot recover this distinction and must not be used for that
    check.
    """

    evidence_id: str
    evidence_type: str
    claim_category: str
    allowed_dimensions: tuple[str, ...]
    directness: str
    freshness_policy: str
    value: str | float | bool
    unit: str | None
    source: str
    source_date: str  # ISO YYYY-MM-DD
    freshness_status: str  # "fresh" | "stale", as declared by the input builder
    state_type: str | None = None  # "event" | "status"; only for REQ-064 evidence_types
    effective_until: str | None = None  # ISO YYYY-MM-DD; required when state_type == "status"
    persistence_horizon: str | None = None  # "one_time" | "multi_quarter" | "structural"
    materiality: str | None = None  # "minor" | "moderate" | "major"

    def canonical_dict(self) -> dict[str, object]:
        """Stable, sorted representation used for input_hash computation (REQ-002)."""
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type,
            "claim_category": self.claim_category,
            "allowed_dimensions": sorted(self.allowed_dimensions),
            "directness": self.directness,
            "freshness_policy": self.freshness_policy,
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            "source_date": self.source_date,
            "freshness_status": self.freshness_status,
            "state_type": self.state_type,
            "effective_until": self.effective_until,
            "persistence_horizon": self.persistence_horizon,
            "materiality": self.materiality,
        }


@dataclass(frozen=True)
class QualitativeContext:
    """The full input to a scoring request (REQ-001)."""

    code: str
    name: str
    industry: str
    as_of_date: str  # ISO YYYY-MM-DD
    schema_version: str
    rubric_version: str
    taxonomy_version: str
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)

    def compute_input_hash(self) -> str:
        """REQ-002: normalize evidence order, then hash version + evidence content.

        Same company identity, version, and evidence content -> same hash.
        Any change to code, name, industry, as_of_date, a version, or any
        evidence field must change the hash.
        """
        normalized_evidence = sorted(
            (item.canonical_dict() for item in self.evidence),
            key=lambda item: json.dumps(
                item,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        payload = {
            "code": self.code,
            "name": self.name,
            "industry": self.industry,
            "as_of_date": self.as_of_date,
            "schema_version": self.schema_version,
            "rubric_version": self.rubric_version,
            "taxonomy_version": self.taxonomy_version,
            "evidence": normalized_evidence,
        }
        canonical_json = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DimensionResult:
    """One dimension's entry in the model's structured output (REQ-017)."""

    status: str  # "scored" | "insufficient_data" | "invalid" (invalid is local-only, section 6.3)
    score: int | None
    confidence: str  # "low" | "medium" | "high"
    evidence_ids: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class Dimensions:
    """REQ-016: the `dimensions` object must contain exactly moat/market_pos/sentiment."""

    moat: DimensionResult
    market_pos: DimensionResult
    sentiment: DimensionResult

    def get(self, name: str) -> DimensionResult:
        if name == "moat":
            return self.moat
        if name == "market_pos":
            return self.market_pos
        if name == "sentiment":
            return self.sentiment
        raise ValueError(f"unknown dimension: {name!r}")


@dataclass(frozen=True)
class ScoringResult:
    """The full structured output envelope (REQ-015): top level contains exactly
    schema_version, overall_status, as_of_date, dimensions -- not the three
    dimension results flattened at the top level (codex review caught this:
    the original draft flattened moat/market_pos/sentiment onto ScoringResult
    itself, which does not match the REQ-015/016 wire shape)."""

    schema_version: str
    overall_status: str  # "scored" | "insufficient_data" | "invalid"
    as_of_date: str
    dimensions: Dimensions

    def dimension(self, name: str) -> DimensionResult:
        return self.dimensions.get(name)


__all__ = ["Evidence", "QualitativeContext", "DimensionResult", "Dimensions", "ScoringResult"]
