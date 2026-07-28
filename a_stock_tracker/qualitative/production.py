"""Read-only selection of existing local qualitative scores."""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

logger = logging.getLogger(__name__)

DIMENSION_NAMES = ("moat", "market_pos", "sentiment")
SCORE_RANGES = {
    "moat": (0, 10),
    "market_pos": (0, 5),
    "sentiment": (0, 5),
}
MODE_ENV = "QUALITATIVE_V2_MODE"
VALID_MODES = frozenset({"off", "on"})

LegacyGetter = Callable[[str, str], dict[str, int]]


@dataclass(frozen=True)
class ProductionQualitativeSelection:
    """Qualitative values and provenance stored with one prediction."""

    scores: dict[str, int]
    sources: dict[str, str]
    mode: str


class ProductionQualitativeError(ValueError):
    """A local qualitative row or runtime setting is invalid."""


def production_mode(environment: Mapping[str, str] | None = None) -> str:
    source = os.environ if environment is None else environment
    mode = source.get(MODE_ENV, "off")
    if mode not in VALID_MODES:
        raise ProductionQualitativeError(f"{MODE_ENV} must be exactly off or on")
    return mode


def _validate_scores(scores: Mapping[str, int]) -> dict[str, int]:
    if set(scores) != set(DIMENSION_NAMES):
        raise ProductionQualitativeError("qualitative score must contain exactly moat/market_pos/sentiment")
    validated: dict[str, int] = {}
    for dimension in DIMENSION_NAMES:
        score = scores[dimension]
        minimum, maximum = SCORE_RANGES[dimension]
        if isinstance(score, bool) or not isinstance(score, int) or not minimum <= score <= maximum:
            raise ProductionQualitativeError(f"{dimension} is outside [{minimum},{maximum}]")
        validated[dimension] = score
    return validated


def _legacy_selection(code: str, name: str, legacy_getter: LegacyGetter) -> ProductionQualitativeSelection:
    scores = _validate_scores(legacy_getter(code, name))
    return ProductionQualitativeSelection(
        scores=scores,
        sources={dimension: "v1" for dimension in DIMENSION_NAMES},
        mode="v1",
    )


def _load_existing_v2(
    conn: sqlite3.Connection,
    code: str,
    name: str,
) -> dict[str, int | None] | None:
    row = conn.execute(
        """SELECT name, as_of_date, overall_status, moat, market_pos, sentiment
           FROM qualitative_scores_v2
           WHERE code=?
           ORDER BY as_of_date DESC, scored_at DESC
           LIMIT 1""",
        (code,),
    ).fetchone()
    if row is None:
        return None

    stored_name, as_of_date, status, moat, market_pos, sentiment = row
    if stored_name != name:
        raise ProductionQualitativeError("v2 cache company identity drift")
    try:
        scored_date = date.fromisoformat(as_of_date)
    except (TypeError, ValueError) as exc:
        raise ProductionQualitativeError("v2 cache as_of_date is invalid") from exc
    if scored_date > date.today():
        raise ProductionQualitativeError("v2 cache as_of_date is in the future")
    if status not in {"scored", "insufficient_data"}:
        raise ProductionQualitativeError("v2 cache status is invalid")

    scores: dict[str, int | None] = {
        "moat": moat,
        "market_pos": market_pos,
        "sentiment": sentiment,
    }
    for dimension, score in scores.items():
        if score is None:
            continue
        minimum, maximum = SCORE_RANGES[dimension]
        if isinstance(score, bool) or not isinstance(score, int) or not minimum <= score <= maximum:
            raise ProductionQualitativeError(f"v2 {dimension} is outside [{minimum},{maximum}]")
    return scores if any(value is not None for value in scores.values()) else None


def get_production_qualitative_selection(
    conn: sqlite3.Connection,
    code: str,
    name: str,
    *,
    legacy_getter: LegacyGetter,
    mode: str | None = None,
) -> ProductionQualitativeSelection:
    """Use an existing local v2 row when enabled; otherwise use the local v1 cache."""
    try:
        selected_mode = production_mode() if mode is None else mode
        if selected_mode not in VALID_MODES:
            raise ProductionQualitativeError("invalid qualitative mode")
        if selected_mode == "off":
            return _legacy_selection(code, name, legacy_getter)
        v2 = _load_existing_v2(conn, code, name)
    except Exception as exc:
        logger.error("%s 本地定性 v2 不可用，回退本地 v1：%s", code, exc)
        return _legacy_selection(code, name, legacy_getter)

    if v2 is None:
        return _legacy_selection(code, name, legacy_getter)

    legacy = _validate_scores(legacy_getter(code, name))
    combined: dict[str, int] = {}
    for dimension in DIMENSION_NAMES:
        v2_score = v2[dimension]
        combined[dimension] = legacy[dimension] if v2_score is None else v2_score
    sources = {dimension: "v2" if v2[dimension] is not None else "v1" for dimension in DIMENSION_NAMES}
    mode_name = "v2" if all(v2[dimension] is not None for dimension in DIMENSION_NAMES) else "hybrid_v2"
    return ProductionQualitativeSelection(
        scores=combined,
        sources=sources,
        mode=mode_name,
    )


__all__ = [
    "DIMENSION_NAMES",
    "MODE_ENV",
    "ProductionQualitativeError",
    "ProductionQualitativeSelection",
    "SCORE_RANGES",
    "get_production_qualitative_selection",
    "production_mode",
]
