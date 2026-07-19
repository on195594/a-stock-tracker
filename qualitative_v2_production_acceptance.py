"""Read-only production acceptance and rollback verification for qualitative v2."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol, cast

from qualitative_v2_contract import DIMENSION_NAMES, SCORE_RANGES
from qualitative_v2_production import (
    MODE_ENV,
    get_production_qualitative_score,
    is_v2_eligible,
    load_usable_v2_score,
    production_mode,
)

BASELINE_SCHEMA_VERSION = "qualitative-v2-production-acceptance-v1"
MAX_BASELINE_BYTES = 65_536
MAX_ENV_BYTES = 1_048_576
IMMUTABLE_PREDICTION_COLUMNS = (
    "code",
    "name",
    "framework",
    "score_date",
    "price_at_score",
    "quant_score",
    "total_score",
    "weights_hash",
    "report_period",
    "estimate_flag",
    "threshold_adjusted",
    "created_at",
    "entry_signal",
    "entry_signal_version",
    "entry_signal_status",
    "entry_signal_reason",
    "entry_signal_source",
    "entry_signal_fetched_at",
    "l3_v2_signal",
    "l3_v2_version",
    "l3_v2_status",
    "l3_v2_reason",
    "l3_v2_fetched_at",
)


class ScoreLoader(Protocol):
    """Load one locally validated score at a caller-supplied date."""

    def __call__(
        self,
        connection: sqlite3.Connection,
        code: str,
        name: str,
        /,
        *,
        today: date | None = None,
    ) -> dict[str, int | None] | None: ...


class AcceptanceError(ValueError):
    """The acceptance baseline or local production inputs are invalid."""


@dataclass(frozen=True)
class PredictionSnapshot:
    """Digest immutable prediction fields through a fixed historical cutoff."""

    through_date: str
    row_count: int
    sha256: str


@dataclass(frozen=True)
class AcceptanceBaseline:
    """Expected production state that changes only through an intentional review."""

    expected_mode: str
    expected_scores: dict[str, dict[str, int | None]]
    prediction_snapshot: PredictionSnapshot
    watchlist_count: int


class _ForbiddenConnection:
    def execute(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("off rollback path accessed the v2 database")


def _canonical_json(value: object) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def load_baseline(path: Path) -> AcceptanceBaseline:
    """Load a strict, tracked acceptance baseline without accepting extra fields."""
    if path.is_symlink() or not path.is_file():
        raise AcceptanceError("acceptance baseline must be a regular file, not a symlink")
    if path.stat().st_size > MAX_BASELINE_BYTES:
        raise AcceptanceError("acceptance baseline exceeds the size limit")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AcceptanceError("acceptance baseline is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise AcceptanceError("acceptance baseline must contain one JSON object")
    expected_fields = {
        "schema_version",
        "expected_mode",
        "expected_scores",
        "prediction_snapshot",
        "watchlist_count",
    }
    if set(raw) != expected_fields or raw.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise AcceptanceError("acceptance baseline schema drift")
    expected_mode = raw.get("expected_mode")
    if not isinstance(expected_mode, str):
        raise AcceptanceError("acceptance baseline expected_mode is invalid")
    production_mode({MODE_ENV: expected_mode})
    watchlist_count = raw.get("watchlist_count")
    if isinstance(watchlist_count, bool) or not isinstance(watchlist_count, int) or watchlist_count <= 0:
        raise AcceptanceError("acceptance baseline watchlist_count is invalid")

    raw_scores = raw.get("expected_scores")
    if not isinstance(raw_scores, dict):
        raise AcceptanceError("acceptance baseline expected_scores is invalid")
    expected_scores: dict[str, dict[str, int | None]] = {}
    for code, scores in raw_scores.items():
        if not isinstance(code, str) or not isinstance(scores, dict) or set(scores) != set(DIMENSION_NAMES):
            raise AcceptanceError("acceptance baseline score entry is invalid")
        normalized: dict[str, int | None] = {}
        for dimension in DIMENSION_NAMES:
            score = scores[dimension]
            if score is not None:
                minimum, maximum = SCORE_RANGES[dimension]
                if isinstance(score, bool) or not isinstance(score, int) or not minimum <= score <= maximum:
                    raise AcceptanceError("acceptance baseline score value is invalid")
            normalized[dimension] = score
        if not any(score is not None for score in normalized.values()):
            raise AcceptanceError("acceptance baseline cannot adopt an all-null v2 score")
        expected_scores[code] = normalized

    raw_snapshot = raw.get("prediction_snapshot")
    if not isinstance(raw_snapshot, dict) or set(raw_snapshot) != {"through_date", "row_count", "sha256"}:
        raise AcceptanceError("acceptance baseline prediction_snapshot is invalid")
    through_date = raw_snapshot.get("through_date")
    row_count = raw_snapshot.get("row_count")
    digest = raw_snapshot.get("sha256")
    try:
        if not isinstance(through_date, str):
            raise ValueError
        date.fromisoformat(through_date)
    except ValueError as exc:
        raise AcceptanceError("acceptance baseline prediction cutoff is invalid") from exc
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0 or not _is_sha256(digest):
        raise AcceptanceError("acceptance baseline prediction snapshot values are invalid")
    return AcceptanceBaseline(
        expected_mode=expected_mode,
        expected_scores=expected_scores,
        prediction_snapshot=PredictionSnapshot(through_date, row_count, cast(str, digest)),
        watchlist_count=watchlist_count,
    )


def configured_mode(env_path: Path, environment: Mapping[str, str] | None = None) -> str:
    """Read only QUALITATIVE_V2_MODE, preserving pipeline environment precedence."""
    source = os.environ if environment is None else environment
    if MODE_ENV in source:
        return production_mode({MODE_ENV: source[MODE_ENV]})
    value = "off"
    if env_path.exists():
        if env_path.is_symlink() or not env_path.is_file():
            raise AcceptanceError("environment path must be a regular file, not a symlink")
        if env_path.stat().st_size > MAX_ENV_BYTES:
            raise AcceptanceError("environment file exceeds the size limit")
        with env_path.open(encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, _, candidate = stripped.partition("=")
                if key.strip() == MODE_ENV:
                    value = candidate.strip()
                    break
    return production_mode({MODE_ENV: value})


def open_readonly_database(path: Path) -> sqlite3.Connection:
    """Open the production database in SQLite read-only and query-only mode."""
    if path.is_symlink() or not path.is_file():
        raise AcceptanceError("production database must be a regular file, not a symlink")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def prediction_snapshot(connection: sqlite3.Connection, through_date: str) -> PredictionSnapshot:
    """Hash fields that daily/outcome processing must never rewrite historically."""
    date.fromisoformat(through_date)
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(predictions)")}
    missing = set(IMMUTABLE_PREDICTION_COLUMNS) - columns
    if missing:
        raise AcceptanceError(f"predictions schema is missing immutable fields: {sorted(missing)}")
    selected = ", ".join(IMMUTABLE_PREDICTION_COLUMNS)
    rows = connection.execute(
        f"SELECT {selected} FROM predictions WHERE score_date <= ? ORDER BY code, framework, score_date",  # noqa: S608
        (through_date,),
    )
    digest = hashlib.sha256()
    row_count = 0
    for row in rows:
        digest.update((_canonical_json(list(row)) + "\n").encode("utf-8"))
        row_count += 1
    return PredictionSnapshot(through_date, row_count, digest.hexdigest())


def _watchlist_map(watchlist: Sequence[Mapping[str, object]]) -> dict[str, str]:
    companies: dict[str, str] = {}
    for item in watchlist:
        code = item.get("code")
        name = item.get("name")
        if not isinstance(code, str) or not isinstance(name, str) or not code or not name or code in companies:
            raise AcceptanceError("production watchlist contains an invalid or duplicate company")
        companies[code] = name
    return companies


def verify_off_rollback(watchlist: Sequence[Mapping[str, object]], canary_codes: frozenset[str]) -> bool:
    """Prove mode=off returns v1 for every stock without touching the v2 database."""
    calls: list[tuple[str, str]] = []
    sentinel = {"moat": 5, "market_pos": 3, "sentiment": 3}

    def legacy(code: str, name: str) -> dict[str, int]:
        calls.append((code, name))
        return dict(sentinel)

    forbidden = cast(sqlite3.Connection, _ForbiddenConnection())
    for item in watchlist:
        code = cast(str, item["code"])
        name = cast(str, item["name"])
        result = get_production_qualitative_score(
            forbidden,
            code,
            name,
            canary_codes=canary_codes,
            legacy_getter=legacy,
            mode="off",
        )
        if result != sentinel:
            return False
    return calls == [(cast(str, item["code"]), cast(str, item["name"])) for item in watchlist]


def _daily_evidence(
    connection: sqlite3.Connection,
    *,
    required_score_date: str,
    watchlist_codes: set[str],
    usable_codes: set[str],
    daily_log_path: Path,
) -> tuple[list[str], dict[str, object]]:
    errors: list[str] = []
    try:
        date.fromisoformat(required_score_date)
    except ValueError as exc:
        raise AcceptanceError("required score date must use YYYY-MM-DD") from exc
    counts = {
        str(code): int(count)
        for code, count in connection.execute(
            "SELECT code, COUNT(*) FROM predictions WHERE framework='A' AND score_date=? GROUP BY code",
            (required_score_date,),
        )
    }
    if set(counts) != watchlist_codes or any(count != 1 for count in counts.values()):
        errors.append("required daily Framework A rows do not match the exact watchlist")

    observed_log_codes: set[str] = set()
    if daily_log_path.is_symlink() or not daily_log_path.is_file():
        errors.append("required daily log is missing or is not a regular file")
    else:
        with daily_log_path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.startswith(required_score_date):
                    continue
                for code in usable_codes:
                    if f"{code} 定性评分使用 hybrid_v2" in line or f"{code} 定性评分使用 source-grounded v2" in line:
                        observed_log_codes.add(code)
        if observed_log_codes != usable_codes:
            errors.append("required daily log does not prove every expected v2 adoption")
    return errors, {
        "framework_a_codes": sorted(counts),
        "observed_v2_log_codes": sorted(observed_log_codes),
        "score_date": required_score_date,
    }


def run_acceptance(
    *,
    database_path: Path,
    env_path: Path,
    baseline_path: Path,
    daily_log_path: Path,
    watchlist: Sequence[Mapping[str, object]],
    canary_codes: frozenset[str],
    environment: Mapping[str, str] | None = None,
    today: date | None = None,
    required_score_date: str | None = None,
    score_loader: ScoreLoader = load_usable_v2_score,
) -> dict[str, object]:
    """Return a machine-readable PASS/ROLLBACK decision without mutating production state."""
    baseline = load_baseline(baseline_path)
    companies = _watchlist_map(watchlist)
    errors: list[str] = []
    mode = configured_mode(env_path, environment)
    if mode != baseline.expected_mode:
        errors.append(f"configured mode is {mode}, expected {baseline.expected_mode}")
    if len(companies) != baseline.watchlist_count:
        errors.append("watchlist count drift")
    if set(baseline.expected_scores) - set(companies):
        errors.append("baseline v2 codes are outside the watchlist")
    eligible_codes = {code for code in companies if is_v2_eligible(code, mode=mode, canary_codes=canary_codes)}
    if mode == "on" and eligible_codes != set(companies):
        errors.append("global on mode did not make the exact watchlist eligible")

    current_date = today or date.today()
    usable_scores: dict[str, dict[str, int | None]] = {}
    loader_errors: dict[str, str] = {}
    with open_readonly_database(database_path) as connection:
        snapshot = prediction_snapshot(connection, baseline.prediction_snapshot.through_date)
        if snapshot != baseline.prediction_snapshot:
            errors.append("immutable historical predictions drift")
        for code, name in companies.items():
            if code not in eligible_codes:
                continue
            try:
                score = score_loader(connection, code, name, today=current_date)
            except Exception as exc:
                loader_errors[code] = type(exc).__name__
                continue
            if score is not None:
                usable_scores[code] = score
        if loader_errors:
            errors.append("one or more watchlist v2 rows failed local revalidation")
        if usable_scores != baseline.expected_scores:
            errors.append("usable v2 code, dimension, or score drift")
        daily: dict[str, object] | None = None
        if required_score_date is not None:
            daily_errors, daily = _daily_evidence(
                connection,
                required_score_date=required_score_date,
                watchlist_codes=set(companies),
                usable_codes=set(usable_scores),
                daily_log_path=daily_log_path,
            )
            errors.extend(daily_errors)

    try:
        rollback_verified = verify_off_rollback(watchlist, canary_codes)
    except Exception:
        rollback_verified = False
    if not rollback_verified:
        errors.append("mode=off rollback verification failed")
    hybrid_codes = sorted(
        code for code, scores in usable_scores.items() if any(value is None for value in scores.values())
    )
    full_v2_codes = sorted(
        code for code, scores in usable_scores.items() if all(value is not None for value in scores.values())
    )
    fallback_codes = sorted(set(companies) - set(usable_scores))
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "decision": "PASS" if not errors else "ROLLBACK",
        "mode": mode,
        "watchlist_count": len(companies),
        "eligible_count": len(eligible_codes),
        "hybrid_codes": hybrid_codes,
        "full_v2_codes": full_v2_codes,
        "fallback_codes": fallback_codes,
        "rollback_verified": rollback_verified,
        "prediction_snapshot": {
            "through_date": snapshot.through_date,
            "row_count": snapshot.row_count,
            "sha256": snapshot.sha256,
        },
        "daily": daily,
        "loader_errors": loader_errors,
        "errors": errors,
    }


__all__ = [
    "AcceptanceBaseline",
    "AcceptanceError",
    "BASELINE_SCHEMA_VERSION",
    "IMMUTABLE_PREDICTION_COLUMNS",
    "PredictionSnapshot",
    "ScoreLoader",
    "configured_mode",
    "load_baseline",
    "open_readonly_database",
    "prediction_snapshot",
    "run_acceptance",
    "verify_off_rollback",
]
