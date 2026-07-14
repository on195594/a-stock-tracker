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
    """One item of the evidence packet (section 6.1)."""

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

        Same version + same evidence content -> same hash. Any change to
        as_of_date, schema_version, rubric_version, taxonomy_version, or any
        evidence field must change the hash.
        """
        normalized_evidence = sorted(
            (item.canonical_dict() for item in self.evidence),
            key=lambda item: str(item["evidence_id"]),
        )
        payload = {
            "code": self.code,
            "as_of_date": self.as_of_date,
            "schema_version": self.schema_version,
            "rubric_version": self.rubric_version,
            "taxonomy_version": self.taxonomy_version,
            "evidence": normalized_evidence,
        }
        canonical_json = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
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
class ScoringResult:
    """The full structured output envelope (REQ-015~016)."""

    schema_version: str
    overall_status: str  # "scored" | "insufficient_data" | "invalid"
    as_of_date: str
    moat: DimensionResult
    market_pos: DimensionResult
    sentiment: DimensionResult

    def dimension(self, name: str) -> DimensionResult:
        if name == "moat":
            return self.moat
        if name == "market_pos":
            return self.market_pos
        if name == "sentiment":
            return self.sentiment
        raise ValueError(f"unknown dimension: {name!r}")


__all__ = ["Evidence", "QualitativeContext", "DimensionResult", "ScoringResult"]
