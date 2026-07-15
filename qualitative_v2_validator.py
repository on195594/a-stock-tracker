"""
Local, HTTP-independent validator for the source-grounded structured
qualitative scoring contract (see docs/specs/2026-07-14-source-grounded-
structured-qualitative-scoring-spec.md, REQ-005/007~009/019/023~030/062/064).

Design note: evidence and model-output validation both operate on plain
dicts, not on already-constructed qualitative_v2_types dataclasses. Field
*presence* (e.g. "was effective_until included at all, even as null?") can
only be observed on the raw dict -- a dataclass with a default value cannot
distinguish "field omitted" from "field explicitly null" once constructed
(see qualitative_v2_types.Evidence docstring). Typed dataclass instances are
constructed here only after validation succeeds.

Defense in depth (codex review, round 2): citation-time freshness is always
*recomputed* from evidence.source_date/state_type/effective_until using the
*canonical* freshness_policy for the evidence's claim_category -- never
trusted from evidence.freshness_status or evidence.freshness_policy as
stored. This closes two related gaps: (1) a caller declaring a mismatched,
overly-permissive freshness_policy at ingestion, and (2) a caller
hand-constructing a QualitativeContext/Evidence directly (bypassing
validate_context_dict) with a fabricated freshness_status="fresh".
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import qualitative_v2_taxonomy as taxonomy
from qualitative_v2_contract import (
    CONFIDENCE_VALUES,
    CONTEXT_TEXT_MAX_CHARS,
    DIMENSION_NAMES,
    DIMENSION_STATUS_VALUES,
    EVIDENCE_ID_MAX_CHARS,
    EVIDENCE_IDS_MAX_ITEMS,
    EVIDENCE_PACKET_MAX_ITEMS,
    EVIDENCE_VALUE_MAX_JSON_BYTES,
    OVERALL_STATUS_VALUES,
    RATIONALE_MAX_CHARS,
    RUBRIC_VERSION,
    SCHEMA_VERSION,
    SCORE_RANGES,
    SOURCE_MAX_CHARS,
    TAXONOMY_VERSION,
    UNIT_MAX_CHARS,
)
from qualitative_v2_types import DimensionResult, Dimensions, Evidence, QualitativeContext, ScoringResult

_REQUIRED_EVIDENCE_KEYS: frozenset[str] = frozenset(
    {
        "evidence_id",
        "evidence_type",
        "claim_category",
        "allowed_dimensions",
        "directness",
        "freshness_policy",
        "value",
        "unit",
        "source",
        "source_date",
        "freshness_status",
    }
)
_OPTIONAL_EVIDENCE_KEYS: frozenset[str] = frozenset(
    {"state_type", "effective_until", "persistence_horizon", "materiality"}
)
_ALL_EVIDENCE_KEYS: frozenset[str] = _REQUIRED_EVIDENCE_KEYS | _OPTIONAL_EVIDENCE_KEYS

_REQUIRED_CONTEXT_KEYS: frozenset[str] = frozenset(
    {"code", "name", "industry", "as_of_date", "schema_version", "rubric_version", "taxonomy_version", "evidence"}
)

_REQUIRED_DIMENSION_RESULT_KEYS: frozenset[str] = frozenset(
    {"status", "score", "confidence", "evidence_ids", "rationale"}
)
_REQUIRED_SCORING_RESULT_KEYS: frozenset[str] = frozenset(
    {"schema_version", "overall_status", "as_of_date", "dimensions"}
)

_CONFIDENCE_VALUE_SET = frozenset(CONFIDENCE_VALUES)
_DIMENSION_STATUS_VALUE_SET = frozenset(DIMENSION_STATUS_VALUES)


def _parse_iso_date(value: object, *, field_name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO date string, got {type(value).__name__}")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid ISO YYYY-MM-DD date: {value!r}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} must use canonical YYYY-MM-DD form: {value!r}")
    return parsed


def _is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _str_in(value: object, allowed: frozenset[str]) -> bool:
    """Membership test that is safe against unhashable/non-str inputs (codex
    review: bare `value in some_frozenset` raises TypeError on unhashable
    values like list/dict instead of failing closed with a rejection)."""
    return isinstance(value, str) and value in allowed


@dataclass(frozen=True)
class EvidenceValidationResult:
    valid: bool
    evidence: Evidence | None
    rejection_reason: str | None


def validate_evidence_dict(raw: Mapping[str, object], *, as_of_date_value: date) -> EvidenceValidationResult:
    """REQ-003~005/061/064: validate one raw evidence dict, fail closed."""
    keys = set(raw.keys())

    missing = _REQUIRED_EVIDENCE_KEYS - keys
    if missing:
        return EvidenceValidationResult(False, None, f"missing required evidence fields: {sorted(missing)}")

    unknown = keys - _ALL_EVIDENCE_KEYS
    if unknown:
        return EvidenceValidationResult(False, None, f"unknown evidence fields: {sorted(unknown)}")

    evidence_id = raw["evidence_id"]
    evidence_type = raw["evidence_type"]
    claim_category = raw["claim_category"]
    directness = raw["directness"]
    freshness_policy = raw["freshness_policy"]
    allowed_dimensions_raw = raw["allowed_dimensions"]
    value = raw["value"]
    unit = raw["unit"]
    source = raw["source"]
    declared_freshness_status = raw["freshness_status"]

    if not isinstance(evidence_id, str) or not evidence_id.strip():
        return EvidenceValidationResult(False, None, "evidence_id must be a non-empty string")
    if len(evidence_id) > EVIDENCE_ID_MAX_CHARS:
        return EvidenceValidationResult(False, None, "evidence_id exceeds maximum length")
    if any(character.isspace() or ord(character) < 32 for character in evidence_id):
        return EvidenceValidationResult(False, None, "evidence_id may not contain whitespace or control characters")
    if not _str_in(evidence_type, taxonomy.EVIDENCE_TYPES):
        return EvidenceValidationResult(False, None, f"unknown evidence_type: {evidence_type!r}")
    if not taxonomy.is_evidence_id_namespace_valid(str(evidence_type), str(evidence_id)):
        expected_prefix = taxonomy.EVIDENCE_TYPE_ID_PREFIXES[str(evidence_type)]
        return EvidenceValidationResult(
            False,
            None,
            f"evidence_id {evidence_id!r} must use namespace {expected_prefix!r} for evidence_type {evidence_type!r}",
        )
    if not _str_in(claim_category, taxonomy.CLAIM_CATEGORIES):
        return EvidenceValidationResult(False, None, f"unknown claim_category: {claim_category!r}")
    if not taxonomy.is_evidence_type_claim_category_combo_valid(str(evidence_type), str(claim_category)):
        return EvidenceValidationResult(
            False, None, f"evidence_type {evidence_type!r} may not carry claim_category {claim_category!r}"
        )
    if not _str_in(directness, taxonomy.DIRECTNESS_VALUES):
        return EvidenceValidationResult(False, None, f"unknown directness: {directness!r}")
    if not taxonomy.is_directness_allowed(str(claim_category), str(directness)):
        return EvidenceValidationResult(
            False, None, f"claim_category {claim_category!r} may not use directness {directness!r}"
        )
    if not _str_in(freshness_policy, taxonomy.FRESHNESS_POLICIES):
        return EvidenceValidationResult(False, None, f"unknown freshness_policy: {freshness_policy!r}")

    # Critical fix (codex review round 2): freshness_policy must match the
    # *canonical* policy for this claim_category, not merely be some known
    # policy value. Without this, e.g. a market_sentiment item (canonical
    # max_age_30d) could declare max_age_550d and have a 100-day-old item
    # pass as "fresh", laundering stale evidence through the sentiment gate.
    canonical_policy = taxonomy.canonical_freshness_policy(str(claim_category))
    if freshness_policy != canonical_policy:
        return EvidenceValidationResult(
            False,
            None,
            f"freshness_policy {freshness_policy!r} does not match canonical {canonical_policy!r} "
            f"for claim_category {claim_category!r}",
        )

    if not isinstance(allowed_dimensions_raw, (list, tuple)):
        return EvidenceValidationResult(False, None, "allowed_dimensions must be a list")
    if not all(isinstance(dim, str) for dim in allowed_dimensions_raw):
        return EvidenceValidationResult(False, None, "allowed_dimensions must contain only strings")
    allowed_dimensions = tuple(allowed_dimensions_raw)
    if len(set(allowed_dimensions)) != len(allowed_dimensions):
        return EvidenceValidationResult(False, None, "allowed_dimensions must not contain duplicates")
    for dim in allowed_dimensions:
        if dim not in taxonomy.DIMENSIONS:
            return EvidenceValidationResult(False, None, f"unknown dimension in allowed_dimensions: {dim!r}")
    canonical_dims = taxonomy.canonical_allowed_dimensions(str(claim_category))
    if set(allowed_dimensions) != canonical_dims:
        return EvidenceValidationResult(
            False,
            None,
            f"allowed_dimensions {sorted(allowed_dimensions)} does not match canonical "
            f"{sorted(canonical_dims)} for claim_category {claim_category!r}",
        )

    # state_type / effective_until (REQ-064) -- must check key presence, not a
    # constructed-dataclass default, per the module docstring.
    evidence_type_requires_state_type = taxonomy.requires_state_type(str(evidence_type))
    has_state_type_key = "state_type" in raw
    if evidence_type_requires_state_type and not has_state_type_key:
        return EvidenceValidationResult(False, None, f"evidence_type {evidence_type!r} requires a state_type field")
    if not evidence_type_requires_state_type and has_state_type_key:
        return EvidenceValidationResult(False, None, f"evidence_type {evidence_type!r} may not declare state_type")

    state_type: str | None = None
    effective_until_raw: object = None
    has_effective_until_key = "effective_until" in raw
    if has_state_type_key:
        state_type_candidate = raw["state_type"]
        if not _str_in(state_type_candidate, taxonomy.STATE_TYPES):
            return EvidenceValidationResult(False, None, f"unknown state_type: {state_type_candidate!r}")
        state_type = str(state_type_candidate)
        if state_type == "status" and not has_effective_until_key:
            return EvidenceValidationResult(
                False, None, "state_type='status' requires an effective_until field (may be null)"
            )
        if state_type == "event" and has_effective_until_key:
            return EvidenceValidationResult(False, None, "state_type='event' may not declare effective_until")
        if has_effective_until_key:
            effective_until_raw = raw["effective_until"]
    elif has_effective_until_key:
        return EvidenceValidationResult(False, None, "effective_until requires evidence-eligible state_type")

    effective_until_date: date | None = None
    if effective_until_raw is not None:
        try:
            effective_until_date = _parse_iso_date(effective_until_raw, field_name="effective_until")
        except ValueError as exc:
            return EvidenceValidationResult(False, None, str(exc))

    # persistence_horizon / materiality (REQ-061) -- required iff market_sentiment.
    requires_persistence = taxonomy.requires_persistence_fields(str(claim_category))
    has_persistence_key = "persistence_horizon" in raw
    has_materiality_key = "materiality" in raw
    if requires_persistence and not (has_persistence_key and has_materiality_key):
        return EvidenceValidationResult(
            False, None, "claim_category='market_sentiment' requires persistence_horizon and materiality"
        )
    if not requires_persistence and (has_persistence_key or has_materiality_key):
        return EvidenceValidationResult(
            False, None, "persistence_horizon/materiality only allowed for claim_category='market_sentiment'"
        )
    persistence_horizon: str | None = None
    materiality: str | None = None
    if requires_persistence:
        persistence_horizon_candidate = raw["persistence_horizon"]
        materiality_candidate = raw["materiality"]
        if not _str_in(persistence_horizon_candidate, taxonomy.PERSISTENCE_HORIZONS):
            return EvidenceValidationResult(
                False, None, f"unknown persistence_horizon: {persistence_horizon_candidate!r}"
            )
        if not _str_in(materiality_candidate, taxonomy.MATERIALITY_LEVELS):
            return EvidenceValidationResult(False, None, f"unknown materiality: {materiality_candidate!r}")
        persistence_horizon = str(persistence_horizon_candidate)
        materiality = str(materiality_candidate)

    if not isinstance(value, (str, float, bool, int)):
        return EvidenceValidationResult(False, None, f"value must be a JSON scalar, got {type(value).__name__}")
    try:
        encoded_value = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (ValueError, OverflowError) as exc:
        return EvidenceValidationResult(False, None, f"value must be finite JSON data: {exc}")
    if len(encoded_value) > EVIDENCE_VALUE_MAX_JSON_BYTES:
        return EvidenceValidationResult(False, None, "value exceeds maximum encoded size")
    if unit is not None and (not isinstance(unit, str) or not unit.strip() or len(unit) > UNIT_MAX_CHARS):
        return EvidenceValidationResult(False, None, "unit must be a non-empty bounded string or null")
    if not isinstance(source, str) or not source.strip() or len(source) > SOURCE_MAX_CHARS:
        return EvidenceValidationResult(False, None, "source must be a non-empty bounded string")

    try:
        source_date = _parse_iso_date(raw["source_date"], field_name="source_date")
    except ValueError as exc:
        return EvidenceValidationResult(False, None, str(exc))

    if not _str_in(declared_freshness_status, frozenset({"fresh", "stale"})):
        return EvidenceValidationResult(False, None, f"unknown freshness_status: {declared_freshness_status!r}")

    try:
        recomputed_status = taxonomy.compute_freshness_status(
            as_of_date=as_of_date_value,
            source_date=source_date,
            freshness_policy=str(freshness_policy),
            state_type=state_type,
            effective_until=effective_until_date,
        )
    except ValueError as exc:
        return EvidenceValidationResult(False, None, str(exc))

    if recomputed_status != declared_freshness_status:
        return EvidenceValidationResult(
            False,
            None,
            f"declared freshness_status {declared_freshness_status!r} does not match recomputed {recomputed_status!r}",
        )

    effective_until_str: str | None = None
    if has_effective_until_key and effective_until_raw is not None:
        effective_until_str = str(effective_until_raw)

    evidence = Evidence(
        evidence_id=str(evidence_id),
        evidence_type=str(evidence_type),
        claim_category=str(claim_category),
        allowed_dimensions=allowed_dimensions,
        directness=str(directness),
        freshness_policy=str(freshness_policy),
        value=value,
        unit=unit,
        source=source,
        source_date=str(raw["source_date"]),
        freshness_status=str(declared_freshness_status),
        state_type=state_type,
        effective_until=effective_until_str,
        persistence_horizon=persistence_horizon,
        materiality=materiality,
    )
    return EvidenceValidationResult(True, evidence, None)


@dataclass(frozen=True)
class ContextValidationResult:
    valid: bool
    context: QualitativeContext | None
    rejection_reason: str | None


def validate_context_dict(raw: Mapping[str, object]) -> ContextValidationResult:
    """REQ-001~005: validate the full evidence packet, fail closed."""
    keys = set(raw.keys())
    missing = _REQUIRED_CONTEXT_KEYS - keys
    if missing:
        return ContextValidationResult(False, None, f"missing required context fields: {sorted(missing)}")
    unknown = keys - _REQUIRED_CONTEXT_KEYS
    if unknown:
        return ContextValidationResult(False, None, f"unknown context fields: {sorted(unknown)}")

    try:
        as_of_date_value = _parse_iso_date(raw["as_of_date"], field_name="as_of_date")
    except ValueError as exc:
        return ContextValidationResult(False, None, str(exc))

    expected_versions = {
        "schema_version": SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
    }
    for field_name in ("code", "name", "industry", *expected_versions):
        value = raw[field_name]
        if not isinstance(value, str) or not value.strip():
            return ContextValidationResult(False, None, f"{field_name} must be a non-empty string")
        if len(value) > CONTEXT_TEXT_MAX_CHARS:
            return ContextValidationResult(False, None, f"{field_name} exceeds maximum length")
    for field_name, expected in expected_versions.items():
        if raw[field_name] != expected:
            return ContextValidationResult(False, None, f"unsupported {field_name}: {raw[field_name]!r}")

    evidence_raw = raw["evidence"]
    if not isinstance(evidence_raw, (list, tuple)):
        return ContextValidationResult(False, None, "evidence must be a list")
    if len(evidence_raw) > EVIDENCE_PACKET_MAX_ITEMS:
        return ContextValidationResult(
            False,
            None,
            f"evidence exceeds maximum item count {EVIDENCE_PACKET_MAX_ITEMS}",
        )

    validated_evidence: list[Evidence] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(evidence_raw):
        if not isinstance(item, dict):
            return ContextValidationResult(False, None, f"evidence[{index}] must be an object")
        evidence_id = item.get("evidence_id")
        if not _is_nonempty_str(evidence_id):
            return ContextValidationResult(False, None, f"evidence[{index}] evidence_id must be a non-empty string")
        if evidence_id in seen_ids:
            return ContextValidationResult(False, None, f"duplicate evidence_id: {evidence_id!r}")
        seen_ids.add(str(evidence_id))
        result = validate_evidence_dict(item, as_of_date_value=as_of_date_value)
        if not result.valid or result.evidence is None:
            return ContextValidationResult(
                False, None, f"evidence[{index}] ({evidence_id!r}): {result.rejection_reason}"
            )
        validated_evidence.append(result.evidence)

    context = QualitativeContext(
        code=str(raw["code"]),
        name=str(raw["name"]),
        industry=str(raw["industry"]),
        as_of_date=str(raw["as_of_date"]),
        schema_version=str(raw["schema_version"]),
        rubric_version=str(raw["rubric_version"]),
        taxonomy_version=str(raw["taxonomy_version"]),
        evidence=tuple(validated_evidence),
    )
    return ContextValidationResult(True, context, None)


def _evidence_to_raw(evidence: Evidence) -> dict[str, object]:
    """Recover the raw shape needed to revalidate a typed trust-boundary input."""
    raw: dict[str, object] = {
        "evidence_id": evidence.evidence_id,
        "evidence_type": evidence.evidence_type,
        "claim_category": evidence.claim_category,
        "allowed_dimensions": evidence.allowed_dimensions,
        "directness": evidence.directness,
        "freshness_policy": evidence.freshness_policy,
        "value": evidence.value,
        "unit": evidence.unit,
        "source": evidence.source,
        "source_date": evidence.source_date,
        "freshness_status": evidence.freshness_status,
    }
    if evidence.state_type is not None:
        raw["state_type"] = evidence.state_type
    if evidence.effective_until is not None or evidence.state_type == "status":
        raw["effective_until"] = evidence.effective_until
    if evidence.persistence_horizon is not None:
        raw["persistence_horizon"] = evidence.persistence_horizon
    if evidence.materiality is not None:
        raw["materiality"] = evidence.materiality
    return raw


def validate_context(context: QualitativeContext) -> ContextValidationResult:
    """Revalidate a typed context instead of trusting dataclass construction."""
    try:
        raw: dict[str, object] = {
            "code": context.code,
            "name": context.name,
            "industry": context.industry,
            "as_of_date": context.as_of_date,
            "schema_version": context.schema_version,
            "rubric_version": context.rubric_version,
            "taxonomy_version": context.taxonomy_version,
            "evidence": [_evidence_to_raw(item) for item in context.evidence],
        }
    except (AttributeError, TypeError, ValueError) as exc:
        return ContextValidationResult(False, None, f"malformed typed context: {exc}")
    return validate_context_dict(raw)


def _lookup_evidence_by_id(context: QualitativeContext) -> dict[str, Evidence]:
    return {item.evidence_id: item for item in context.evidence}


def _recompute_is_fresh(evidence: Evidence, *, as_of_date_value: date) -> bool:
    """Defense in depth (codex review round 2): never trust
    evidence.freshness_status directly at citation-check time. Recompute
    using the *canonical* freshness_policy for the evidence's claim_category
    (not evidence.freshness_policy as stored), so that even a hand-built
    QualitativeContext bypassing validate_context_dict, or a stored policy
    mismatch, cannot launder stale evidence as fresh."""
    try:
        canonical_policy = taxonomy.canonical_freshness_policy(evidence.claim_category)
        source_date = _parse_iso_date(evidence.source_date, field_name="source_date")
        effective_until_date = (
            _parse_iso_date(evidence.effective_until, field_name="effective_until")
            if evidence.effective_until is not None
            else None
        )
        status = taxonomy.compute_freshness_status(
            as_of_date=as_of_date_value,
            source_date=source_date,
            freshness_policy=canonical_policy,
            state_type=evidence.state_type,
            effective_until=effective_until_date,
        )
    except ValueError:
        return False
    return status == "fresh"


def _dimension_gate_satisfied(dimension: str, cited: list[Evidence], *, as_of_date_value: date) -> bool:
    """REQ-007~009/062: machine threshold checked against the model's actually
    cited evidence_ids (not merely what exists in the packet), with freshness
    recomputed rather than trusted (see _recompute_is_fresh)."""
    if dimension == "moat":
        has_direct_moat = any(
            e.claim_category == "competitive_moat"
            and e.directness == "direct"
            and _recompute_is_fresh(e, as_of_date_value=as_of_date_value)
            for e in cited
        )
        has_financial_support = any(
            e.claim_category == "financial_performance"
            and e.directness == "supporting"
            and _recompute_is_fresh(e, as_of_date_value=as_of_date_value)
            for e in cited
        )
        return has_direct_moat and has_financial_support
    if dimension == "market_pos":
        return any(
            e.claim_category == "industry_position"
            and e.directness == "direct"
            and _recompute_is_fresh(e, as_of_date_value=as_of_date_value)
            for e in cited
        )
    if dimension == "sentiment":
        fresh_direct_sentiment = [
            e
            for e in cited
            if e.claim_category == "market_sentiment"
            and e.directness == "direct"
            and _recompute_is_fresh(e, as_of_date_value=as_of_date_value)
        ]
        if not fresh_direct_sentiment:
            return False
        # REQ-062: at least one cited sentiment evidence must be persistent;
        # one_time-only citations cannot satisfy this gate on their own.
        return any(e.persistence_horizon in ("multi_quarter", "structural") for e in fresh_direct_sentiment)
    raise ValueError(f"unknown dimension: {dimension!r}")


@dataclass(frozen=True)
class ScoringValidationResult:
    valid: bool
    result: ScoringResult | None
    rejection_reason: str | None


def _validate_dimension_result(
    dimension: str,
    raw_dim: object,
    *,
    evidence_by_id: dict[str, Evidence],
    as_of_date_value: date,
) -> tuple[DimensionResult | None, str | None]:
    if not isinstance(raw_dim, dict):
        return None, f"dimension {dimension!r} must be an object"
    keys = set(raw_dim.keys())
    if keys != _REQUIRED_DIMENSION_RESULT_KEYS:
        return None, f"dimension {dimension!r} has wrong field set: {sorted(keys)}"

    status = raw_dim["status"]
    score = raw_dim["score"]
    confidence = raw_dim["confidence"]
    evidence_ids_raw = raw_dim["evidence_ids"]
    rationale = raw_dim["rationale"]

    if not _str_in(status, _DIMENSION_STATUS_VALUE_SET):
        return None, f"dimension {dimension!r} has unknown/disallowed status: {status!r}"
    if not _str_in(confidence, _CONFIDENCE_VALUE_SET):
        return None, f"dimension {dimension!r} has unknown confidence: {confidence!r}"
    if not isinstance(evidence_ids_raw, (list, tuple)):
        return None, f"dimension {dimension!r} evidence_ids must be a list"
    if not all(_is_nonempty_str(item) for item in evidence_ids_raw):
        return None, f"dimension {dimension!r} evidence_ids must contain only non-empty strings"
    if len(evidence_ids_raw) > EVIDENCE_IDS_MAX_ITEMS:
        return None, (f"dimension {dimension!r} evidence_ids exceeds maximum item count {EVIDENCE_IDS_MAX_ITEMS}")
    evidence_ids = tuple(evidence_ids_raw)
    if len(set(evidence_ids)) != len(evidence_ids):
        return None, f"dimension {dimension!r} evidence_ids contains duplicates"
    if not isinstance(rationale, str) or not rationale.strip():
        return None, f"dimension {dimension!r} rationale must be a non-empty, non-whitespace string"
    if len(rationale) > RATIONALE_MAX_CHARS:
        return None, f"dimension {dimension!r} rationale exceeds {RATIONALE_MAX_CHARS} characters"

    # REQ-026: scored<->null / insufficient_data<->non-null mismatches.
    if status == "scored":
        min_score, max_score = SCORE_RANGES[dimension]
        if not isinstance(score, int) or isinstance(score, bool) or not (min_score <= score <= max_score):
            return None, f"dimension {dimension!r} status=scored requires integer score in [{min_score},{max_score}]"
        if not evidence_ids:
            return None, f"dimension {dimension!r} status=scored requires non-empty evidence_ids"
    else:  # insufficient_data
        if score is not None:
            return None, f"dimension {dimension!r} status=insufficient_data requires score=null"

    # REQ-025: reject unknown evidence_id references.
    cited: list[Evidence] = []
    for evidence_id in evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            return None, f"dimension {dimension!r} cites unknown evidence_id: {evidence_id!r}"
        if dimension not in evidence.allowed_dimensions:
            return None, f"dimension {dimension!r} cites evidence not allowed for this dimension: {evidence_id!r}"
        if not _recompute_is_fresh(evidence, as_of_date_value=as_of_date_value):
            return None, f"dimension {dimension!r} cites stale evidence: {evidence_id!r}"
        cited.append(evidence)

    # REQ-028: scored is only valid if the cited set actually satisfies the
    # REQ-007~009/062 machine threshold -- not merely because the packet
    # contained qualifying evidence somewhere.
    if status == "scored" and not _dimension_gate_satisfied(dimension, cited, as_of_date_value=as_of_date_value):
        return None, f"dimension {dimension!r} claims scored but cited evidence does not satisfy REQ-007~009/062"
    if dimension == "sentiment" and status == "scored" and score in (1, 5):
        has_major_persistent_direct = any(
            evidence.claim_category == "market_sentiment"
            and evidence.directness == "direct"
            and evidence.persistence_horizon in ("multi_quarter", "structural")
            and evidence.materiality == "major"
            and _recompute_is_fresh(evidence, as_of_date_value=as_of_date_value)
            for evidence in cited
        )
        if not has_major_persistent_direct:
            return None, f"dimension {dimension!r} score={score} requires major persistent direct evidence"

    return (
        DimensionResult(
            status=str(status),
            score=score,
            confidence=str(confidence),
            evidence_ids=evidence_ids,
            rationale=rationale,
        ),
        None,
    )


def validate_model_output(raw_output: Mapping[str, object], *, context: QualitativeContext) -> ScoringValidationResult:
    """REQ-015~019/024~029: validate the model's structured output against
    context. All-or-nothing: any per-dimension violation invalidates the
    entire result (REQ-029) -- callers must not adopt individual dimensions
    from a rejected result.

    Typed contexts are revalidated here because dataclass construction alone
    is not a trust boundary and does not enforce taxonomy or size constraints.
    """
    context_validation = validate_context(context)
    if not context_validation.valid or context_validation.context is None:
        return ScoringValidationResult(
            False,
            None,
            f"invalid context: {context_validation.rejection_reason}",
        )
    context = context_validation.context

    keys = set(raw_output.keys())
    if keys != _REQUIRED_SCORING_RESULT_KEYS:
        return ScoringValidationResult(False, None, f"top-level output has wrong field set: {sorted(keys)}")

    schema_version = raw_output["schema_version"]
    overall_status_claimed = raw_output["overall_status"]
    as_of_date_raw = raw_output["as_of_date"]
    dimensions_raw = raw_output["dimensions"]

    if schema_version != context.schema_version:
        return ScoringValidationResult(
            False, None, f"schema_version mismatch: output={schema_version!r} context={context.schema_version!r}"
        )
    if as_of_date_raw != context.as_of_date:
        return ScoringValidationResult(
            False, None, f"as_of_date mismatch: output={as_of_date_raw!r} context={context.as_of_date!r}"
        )
    if overall_status_claimed not in OVERALL_STATUS_VALUES:
        return ScoringValidationResult(
            False,
            None,
            f"overall_status must be scored or insufficient_data, model may not claim: {overall_status_claimed!r}",
        )
    if not isinstance(dimensions_raw, dict) or set(dimensions_raw.keys()) != set(DIMENSION_NAMES):
        return ScoringValidationResult(False, None, "dimensions must contain exactly moat/market_pos/sentiment")

    try:
        as_of_date_value = _parse_iso_date(context.as_of_date, field_name="context.as_of_date")
    except ValueError as exc:
        return ScoringValidationResult(False, None, str(exc))

    evidence_by_id = _lookup_evidence_by_id(context)
    validated: dict[str, DimensionResult] = {}
    for dimension in DIMENSION_NAMES:
        dim_result, reason = _validate_dimension_result(
            dimension,
            dimensions_raw[dimension],
            evidence_by_id=evidence_by_id,
            as_of_date_value=as_of_date_value,
        )
        if dim_result is None:
            return ScoringValidationResult(False, None, reason)
        validated[dimension] = dim_result

    # REQ-029/6.3: overall status is derived from validated per-dimension
    # statuses, and must match what the model claimed.
    any_insufficient = any(d.status == "insufficient_data" for d in validated.values())
    computed_overall = "insufficient_data" if any_insufficient else "scored"
    if computed_overall != overall_status_claimed:
        return ScoringValidationResult(
            False,
            None,
            f"overall_status {overall_status_claimed!r} inconsistent with dimension statuses "
            f"(computed {computed_overall!r})",
        )

    result = ScoringResult(
        schema_version=str(schema_version),
        overall_status=str(computed_overall),
        as_of_date=str(as_of_date_raw),
        dimensions=Dimensions(
            moat=validated["moat"], market_pos=validated["market_pos"], sentiment=validated["sentiment"]
        ),
    )
    return ScoringValidationResult(True, result, None)


__all__ = [
    "EvidenceValidationResult",
    "validate_evidence_dict",
    "ContextValidationResult",
    "validate_context_dict",
    "validate_context",
    "ScoringValidationResult",
    "validate_model_output",
]
