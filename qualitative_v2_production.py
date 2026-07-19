"""Fail-safe production selector and sealed SQLite cache for qualitative v2.

The production pipeline never calls Gemini through this module. A separate,
explicit batch command writes validated v2 records first; daily scoring only
reads them and falls back to the existing v1 scorer per stock.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from typing import cast

from qualitative_v2_client import DEFAULT_GEMINI_MODEL
from qualitative_v2_contract import DIMENSION_NAMES
from qualitative_v2_validator import validate_context_dict, validate_model_output

logger = logging.getLogger(__name__)

MODE_ENV = "QUALITATIVE_V2_MODE"
VALID_MODES = frozenset({"off", "canary", "on"})
V2_CACHE_TTL_DAYS = 30
_CONTEXT_FIELDS = frozenset(
    {
        "code",
        "name",
        "industry",
        "as_of_date",
        "schema_version",
        "rubric_version",
        "taxonomy_version",
        "evidence",
    }
)

LegacyGetter = Callable[[str, str], dict[str, int]]


class ProductionV2Error(ValueError):
    """A v2 production record or runtime setting failed closed."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def production_mode(environment: Mapping[str, str] | None = None) -> str:
    """Return the exact production mode, rejecting unsafe misspellings."""
    source = os.environ if environment is None else environment
    mode = source.get(MODE_ENV, "off")
    if mode not in VALID_MODES:
        raise ProductionV2Error(f"{MODE_ENV} must be exactly one of off/canary/on")
    return mode


def is_v2_eligible(code: str, *, mode: str, canary_codes: frozenset[str]) -> bool:
    """Decide whether one stock may consume a precomputed v2 score."""
    if mode not in VALID_MODES:
        raise ProductionV2Error("invalid qualitative v2 mode")
    return mode == "on" or (mode == "canary" and code in canary_codes)


def _json_object(raw: object, *, label: str) -> dict[str, object]:
    if not isinstance(raw, str):
        raise ProductionV2Error(f"{label} must be a JSON string")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProductionV2Error(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ProductionV2Error(f"{label} must contain a JSON object")
    return cast(dict[str, object], value)


def _context_from_shadow(raw_json: object, expected_hash: object) -> tuple[dict[str, object], str]:
    payload = _json_object(raw_json, label="shadow context_json")
    embedded_hash = payload.pop("input_hash", None)
    if set(payload) != _CONTEXT_FIELDS:
        raise ProductionV2Error("shadow context_json field set drift")
    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        raise ProductionV2Error("shadow context evidence is invalid")
    # Older shadow artifacts used Evidence.canonical_dict(), which includes
    # optional null fields that the wire contract correctly rejects as
    # explicitly declared. Normalize only those null placeholders before the
    # same validator is run; non-null declarations remain untouched.
    for item in evidence:
        if not isinstance(item, dict):
            raise ProductionV2Error("shadow context evidence item is invalid")
        if item.get("state_type") is None:
            item.pop("state_type", None)
            item.pop("effective_until", None)
        elif item.get("state_type") == "event" and item.get("effective_until") is None:
            item.pop("effective_until", None)
        if item.get("persistence_horizon") is None:
            item.pop("persistence_horizon", None)
        if item.get("materiality") is None:
            item.pop("materiality", None)
    validation = validate_context_dict(payload)
    if not validation.valid or validation.context is None:
        raise ProductionV2Error(f"shadow context is invalid: {validation.rejection_reason}")
    computed_hash = validation.context.compute_input_hash()
    if expected_hash != computed_hash or embedded_hash != computed_hash:
        raise ProductionV2Error("shadow context input hash drift")
    return payload, computed_hash


def _validated_payloads(
    context_json: str,
    result_json: str,
    *,
    code: str,
    name: str,
    as_of_date: str,
    input_hash: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, int] | None, str]:
    context_raw = _json_object(context_json, label="stored context_json")
    if set(context_raw) != _CONTEXT_FIELDS:
        raise ProductionV2Error("stored context_json field set drift")
    context_validation = validate_context_dict(context_raw)
    if not context_validation.valid or context_validation.context is None:
        raise ProductionV2Error(f"stored context is invalid: {context_validation.rejection_reason}")
    context = context_validation.context
    if (
        context.code != code
        or context.name != name
        or context.as_of_date != as_of_date
        or context.compute_input_hash() != input_hash
    ):
        raise ProductionV2Error("stored context identity or hash drift")

    result_raw = _json_object(result_json, label="stored result_json")
    result_validation = validate_model_output(result_raw, context=context)
    if not result_validation.valid or result_validation.result is None:
        raise ProductionV2Error(f"stored v2 result is invalid: {result_validation.rejection_reason}")
    result = result_validation.result
    scores: dict[str, int] | None = None
    if result.overall_status == "scored":
        scores = {}
        for dimension in DIMENSION_NAMES:
            score = result.dimension(dimension).score
            if score is None:
                raise ProductionV2Error("scored v2 result contains a null score")
            scores[dimension] = score
    return context_raw, result_raw, scores, result.overall_status


def promote_shadow_record(
    conn: sqlite3.Connection,
    record: Mapping[str, object],
    *,
    expected_codes: frozenset[str],
) -> bool:
    """Validate and create one independent v2 cache row from a shadow record."""
    status = record.get("validation_status")
    if status not in {"VALID_SCORED", "VALID_INSUFFICIENT_DATA"}:
        raise ProductionV2Error("only locally validated shadow results may be promoted")
    code = record.get("code")
    name_value: object
    as_of_date = record.get("scored_date")
    input_hash = record.get("input_hash")
    model = record.get("model")
    created_at = record.get("created_at")
    if not all(isinstance(value, str) and value for value in (code, as_of_date, input_hash, model, created_at)):
        raise ProductionV2Error("shadow record identity fields are incomplete")
    code = cast(str, code)
    as_of_date = cast(str, as_of_date)
    input_hash = cast(str, input_hash)
    model = cast(str, model)
    created_at = cast(str, created_at)
    if code not in expected_codes:
        raise ProductionV2Error("shadow record code is outside the requested production scope")
    if model != DEFAULT_GEMINI_MODEL:
        raise ProductionV2Error("shadow record model is not the fixed production Gemini model")
    try:
        scored_at = datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise ProductionV2Error("shadow record created_at is invalid") from exc
    if scored_at.tzinfo is None or scored_at.utcoffset() is None:
        raise ProductionV2Error("shadow record created_at must be timezone-aware")

    context_raw, computed_hash = _context_from_shadow(record.get("context_json"), input_hash)
    name_value = context_raw["name"]
    if not isinstance(name_value, str):
        raise ProductionV2Error("shadow context name is invalid")
    canonical_context = _canonical_json(context_raw)
    result_raw = _json_object(record.get("result_json"), label="shadow result_json")
    canonical_result = _canonical_json(result_raw)
    _, _, scores, overall_status = _validated_payloads(
        canonical_context,
        canonical_result,
        code=code,
        name=name_value,
        as_of_date=as_of_date,
        input_hash=computed_hash,
    )
    if record.get("overall_status") != overall_status:
        raise ProductionV2Error("shadow record overall status drift")
    expected_validation_status = "VALID_SCORED" if overall_status == "scored" else "VALID_INSUFFICIENT_DATA"
    if status != expected_validation_status:
        raise ProductionV2Error("shadow validation status is inconsistent with the result")

    score_values: dict[str, int | None] = (
        dict(scores) if scores is not None else {dimension: None for dimension in DIMENSION_NAMES}
    )
    values = (
        code,
        name_value,
        as_of_date,
        computed_hash,
        model,
        overall_status,
        score_values["moat"],
        score_values["market_pos"],
        score_values["sentiment"],
        canonical_context,
        canonical_result,
        created_at,
    )
    existing = conn.execute(
        """SELECT code, name, as_of_date, input_hash, model, overall_status,
                  moat, market_pos, sentiment, context_json, result_json, scored_at
           FROM qualitative_scores_v2
           WHERE code=? AND as_of_date=? AND input_hash=? AND model=?""",
        (code, as_of_date, computed_hash, model),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != values:
            raise ProductionV2Error("refusing to overwrite a different sealed v2 score")
        return False
    conn.execute(
        """INSERT INTO qualitative_scores_v2
           (code, name, as_of_date, input_hash, model, overall_status,
            moat, market_pos, sentiment, context_json, result_json, scored_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        values,
    )
    return True


def load_usable_v2_score(
    conn: sqlite3.Connection,
    code: str,
    name: str,
    *,
    today: date | None = None,
) -> dict[str, int] | None:
    """Load and revalidate the newest fixed-model v2 row for one stock."""
    current_date = today or date.today()
    row = conn.execute(
        """SELECT code, name, as_of_date, input_hash, model, overall_status,
                  moat, market_pos, sentiment, context_json, result_json, scored_at
           FROM qualitative_scores_v2
           WHERE code=? AND model=?
           ORDER BY as_of_date DESC, scored_at DESC
           LIMIT 1""",
        (code, DEFAULT_GEMINI_MODEL),
    ).fetchone()
    if row is None:
        return None
    (
        stored_code,
        stored_name,
        as_of_date,
        input_hash,
        _model,
        stored_status,
        stored_moat,
        stored_market_pos,
        stored_sentiment,
        context_json,
        result_json,
        _scored_at,
    ) = row
    if stored_code != code or stored_name != name:
        raise ProductionV2Error("v2 cache company identity drift")
    try:
        context_date = date.fromisoformat(as_of_date)
    except (TypeError, ValueError) as exc:
        raise ProductionV2Error("v2 cache as_of_date is invalid") from exc
    if context_date > current_date or current_date - context_date > timedelta(days=V2_CACHE_TTL_DAYS):
        return None
    _, _, scores, validated_status = _validated_payloads(
        context_json,
        result_json,
        code=code,
        name=name,
        as_of_date=as_of_date,
        input_hash=input_hash,
    )
    if validated_status != stored_status:
        raise ProductionV2Error("v2 cache status drift")
    denormalized = {"moat": stored_moat, "market_pos": stored_market_pos, "sentiment": stored_sentiment}
    if scores is None:
        if any(value is not None for value in denormalized.values()):
            raise ProductionV2Error("insufficient v2 cache row contains denormalized scores")
        return None
    if denormalized != scores:
        raise ProductionV2Error("v2 cache denormalized score drift")
    return scores


def get_production_qualitative_score(
    conn: sqlite3.Connection,
    code: str,
    name: str,
    *,
    canary_codes: frozenset[str],
    legacy_getter: LegacyGetter,
    mode: str | None = None,
) -> dict[str, int]:
    """Return v2 when eligible and sealed, otherwise preserve the v1 path."""
    try:
        selected_mode = production_mode() if mode is None else mode
        if is_v2_eligible(code, mode=selected_mode, canary_codes=canary_codes):
            score = load_usable_v2_score(conn, code, name)
            if score is not None:
                logger.info("%s 定性评分使用 source-grounded v2", code)
                return score
    except (ProductionV2Error, sqlite3.Error) as exc:
        logger.error("%s 定性评分 v2 fail-closed，逐股回退 v1：%s", code, exc)
    return legacy_getter(code, name)


__all__ = [
    "MODE_ENV",
    "ProductionV2Error",
    "get_production_qualitative_score",
    "is_v2_eligible",
    "load_usable_v2_score",
    "production_mode",
    "promote_shadow_record",
]
