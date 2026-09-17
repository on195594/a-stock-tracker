"""Read-only, manifest-driven evaluation helpers for the S2 research report."""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator, Mapping, Sequence


EVALUATION_VERSION = "2026-09-17.e2"
WINDOWS = (30, 60, 90)
MAX_LAG_DAYS = 10
MINIMUM_SECTIONS = {30: 3, 60: 2, 90: 1}
MIN_COVERAGE = 0.90
EXPECTED_FRAMEWORK = "A"


class ManifestError(ValueError):
    """A manifest is missing, malformed, or not applicable to this report."""


class EvaluationInputError(ValueError):
    """The read-only evaluation inputs cannot support an evidence claim."""


@dataclass(frozen=True)
class CalendarEvidence:
    """Explicit calendar dates plus the range/source that make them auditable."""

    dates: tuple[date, ...]
    covered_from: date
    covered_to: date
    as_of: date
    source: str

    @classmethod
    def from_dates(
        cls,
        dates: Sequence[str | date],
        *,
        covered_from: str | date,
        covered_to: str | date,
        as_of: str | date,
        source: str,
    ) -> "CalendarEvidence":
        try:
            parsed = tuple(
                sorted({item if type(item) is date else _parse_date(item, "calendar date") for item in dates})
            )
            start = covered_from if type(covered_from) is date else _parse_date(covered_from, "covered_from")
            end = covered_to if type(covered_to) is date else _parse_date(covered_to, "covered_to")
            evidence_as_of = as_of if type(as_of) is date else _parse_date(as_of, "calendar as_of")
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid calendar evidence") from exc
        if not isinstance(source, str) or not source.strip() or start > end or evidence_as_of < end:
            raise ValueError("invalid calendar evidence metadata")
        if any(item < start or item > end for item in parsed):
            raise ValueError("calendar date outside covered range")
        return cls(parsed, start, end, evidence_as_of, source)


@dataclass(frozen=True)
class ExperimentManifest:
    schema_version: int
    experiment_id: str
    framework: str
    universe_source: dict[str, str]
    expected_codes: tuple[str, ...]
    universe_hash: str
    scoring_hashes: tuple[str, ...]
    effective_from: date | None
    effective_to: date | None
    registration_status: str
    evidence_ref: str | None
    windows: tuple[int, ...]
    minimum_sections: dict[int, int]
    min_coverage: float
    manifest_hash: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExperimentManifest":
        if not isinstance(value, Mapping):
            raise ManifestError("manifest must be an object")
        if type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
            raise ManifestError("schema_version must be integer 1")
        experiment_id = _non_empty_text(value.get("experiment_id"), "experiment_id")
        framework = _non_empty_text(value.get("framework"), "framework")
        if framework != EXPECTED_FRAMEWORK:
            raise ManifestError("framework must be A")

        source = value.get("universe_source")
        if not isinstance(source, Mapping):
            raise ManifestError("universe_source must be an object")
        universe_source = {
            key: _non_empty_text(source.get(key), f"universe_source.{key}")
            for key in ("repository", "commit", "config_path", "file_sha256")
        }
        if not re.fullmatch(r"[0-9a-f]{40}", universe_source["commit"]):
            raise ManifestError("universe_source.commit must be a 40-character lowercase hex hash")
        if not re.fullmatch(r"[0-9a-f]{64}", universe_source["file_sha256"]):
            raise ManifestError("universe_source.file_sha256 must be a 64-character lowercase hex hash")

        raw_codes = value.get("expected_codes")
        if not isinstance(raw_codes, list) or not raw_codes:
            raise ManifestError("expected_codes must be a non-empty array")
        if any(not isinstance(code, str) for code in raw_codes):
            raise ManifestError("expected_codes must contain strings")
        codes = tuple(raw_codes)
        if len(set(codes)) != len(codes) or any(len(code) != 6 or not code.isdigit() for code in codes):
            raise ManifestError("expected_codes must contain unique six-digit codes")
        expected_hash = hashlib.sha256(_canonical_json(sorted(codes)).encode()).hexdigest()
        if value.get("universe_hash") != expected_hash:
            raise ManifestError("universe_hash does not match sorted expected_codes")

        registration_status = value.get("registration_status")
        if not isinstance(registration_status, str) or registration_status not in {"pending", "verified"}:
            raise ManifestError("registration_status must be pending or verified")
        raw_hashes = value.get("scoring_hashes")
        if not isinstance(raw_hashes, list):
            raise ManifestError("scoring_hashes must be an array")
        if registration_status == "verified" and len(raw_hashes) != 1:
            raise ManifestError("verified scoring_hashes must contain exactly one cohort hash")
        if any(not isinstance(item, str) or not item.strip() or item != item.strip() for item in raw_hashes):
            raise ManifestError("scoring_hashes must contain one non-empty exact hash")
        scoring_hashes = tuple(raw_hashes)

        raw_effective_from = value.get("effective_from")
        raw_effective_to = value.get("effective_to")
        if registration_status == "pending":
            effective_from = (
                _parse_date(raw_effective_from, "effective_from") if raw_effective_from is not None else None
            )
            effective_to = _parse_date(raw_effective_to, "effective_to") if raw_effective_to is not None else None
        else:
            effective_from = _parse_date(raw_effective_from, "effective_from")
            effective_to = _parse_date(raw_effective_to, "effective_to")
        if effective_from is not None and effective_to is not None and effective_from > effective_to:
            raise ManifestError("effective_from must not be after effective_to")
        evidence_ref = (
            _non_empty_text(value.get("evidence_ref"), "evidence_ref")
            if registration_status == "verified"
            else value.get("evidence_ref")
        )
        if registration_status == "pending" and evidence_ref is not None and not isinstance(evidence_ref, str):
            raise ManifestError("pending evidence_ref must be null or string")

        if value.get("windows") != list(WINDOWS):
            raise ManifestError("windows must be [30, 60, 90]")
        raw_minimum = value.get("minimum_sections")
        if not isinstance(raw_minimum, Mapping):
            raise ManifestError("minimum_sections must be an object")
        minimum_sections = {}
        for window in WINDOWS:
            item = raw_minimum.get(str(window), raw_minimum.get(window))
            if isinstance(item, bool) or not isinstance(item, int) or item != MINIMUM_SECTIONS[window]:
                raise ManifestError(f"minimum_sections[{window}] is not fixed")
            minimum_sections[window] = item
        if isinstance(value.get("min_coverage"), bool) or value.get("min_coverage") != MIN_COVERAGE:
            raise ManifestError("min_coverage must be 0.90")

        canonical = _canonical_json(value)
        return cls(
            schema_version=1,
            experiment_id=experiment_id,
            framework=framework,
            universe_source=universe_source,
            expected_codes=codes,
            universe_hash=expected_hash,
            scoring_hashes=scoring_hashes,
            effective_from=effective_from,
            effective_to=effective_to,
            registration_status=registration_status,
            evidence_ref=evidence_ref,
            windows=WINDOWS,
            minimum_sections=minimum_sections,
            min_coverage=MIN_COVERAGE,
            manifest_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        )


@dataclass(frozen=True)
class QualifiedScore:
    code: str
    score_date: date
    total_score: float
    l3_v2_signal: int | None


@dataclass(frozen=True)
class ScoreSection:
    score_date: date
    rows: tuple[QualifiedScore, ...]
    gaps: tuple[str, ...] = ()


@dataclass(frozen=True)
class SectionEvaluation:
    score_date: date
    entry_date: date | None
    endpoint: date | None
    rows: tuple[QualifiedScore, ...]
    q5_weights: dict[str, float]
    q1_weights: dict[str, float]
    all_scores_tied: bool
    metrics: dict[str, float | None]
    outcome_computable: int
    path_ready: bool
    equal_path_ready: bool
    benchmark_path_ready: bool
    tie_disclosure: dict[str, Any]
    gaps: tuple[str, ...]


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ManifestError("manifest contains unsupported or non-finite values") from exc


def _non_empty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be a non-empty string")
    return value


def _parse_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise ManifestError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ManifestError(f"{field} must be an ISO date") from exc


def _parse_json_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationInputError(f"{field}_missing")

    def reject_constant(raw: str) -> None:
        raise EvaluationInputError(f"{field}_non_finite")

    try:
        parsed = json.loads(value, parse_float=Decimal, parse_int=Decimal, parse_constant=reject_constant)
    except EvaluationInputError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError, UnicodeError) as exc:
        raise EvaluationInputError(f"{field}_invalid_json") from exc
    if not isinstance(parsed, dict):
        raise EvaluationInputError(f"{field}_not_object")
    return parsed


def _strict_manifest_json(value: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [key for key, _ in pairs]
        if len(set(keys)) != len(keys):
            raise ManifestError("manifest_duplicate_key")
        return dict(pairs)

    def reject_constant(raw: str) -> None:
        raise ManifestError(f"manifest_non_finite:{raw}")

    try:
        value = json.loads(value, object_pairs_hook=reject_duplicates, parse_constant=reject_constant)
    except ManifestError:
        raise
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError("manifest_unreadable") from exc
    if not isinstance(value, dict):
        raise ManifestError("manifest must be an object")
    return value


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        decimal_value = Decimal(value)
        converted = float(value)
    except (OverflowError, TypeError, ValueError, InvalidOperation):
        return None
    return converted if decimal_value.is_finite() and math.isfinite(converted) else None


def _finite_snapshot_numbers(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(value).is_finite()
        except (InvalidOperation, TypeError, ValueError, ArithmeticError):
            return False
    if isinstance(value, Mapping):
        return all(_finite_snapshot_numbers(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite_snapshot_numbers(item) for item in value)
    return True


def _validate_known_numeric_fields(inputs: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    numeric_inputs = {
        "roe_3y_avg",
        "net_profit_growth",
        "debt_ratio",
        "gross_margin",
        "pb_percentile_10y",
        "moat_fixed",
        "market_pos_fixed",
        "sentiment_fixed",
    }
    for field in numeric_inputs:
        if field in inputs and inputs[field] is not None and _finite_float(inputs[field]) is None:
            raise EvaluationInputError(f"scoring_input_invalid:{field}")
    for field in ("quant_score", "total_score", "data_quality"):
        if field in result and _finite_float(result[field]) is None:
            raise EvaluationInputError(f"snapshot_result_invalid:{field}")
    component_scores = result.get("component_scores")
    if not isinstance(component_scores, Mapping):
        raise EvaluationInputError("snapshot_component_scores_missing")
    if any(_finite_float(item) is None for item in component_scores.values()):
        raise EvaluationInputError("snapshot_component_scores_invalid")


def _same_recorded_precision(db_value: Any, snapshot_value: Any) -> bool:
    try:
        recorded = Decimal(str(snapshot_value))
        actual = Decimal(str(db_value))
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return False
    if not recorded.is_finite() or not actual.is_finite():
        return False
    exponent = recorded.as_tuple().exponent
    if not isinstance(exponent, int):
        return False
    quantum = Decimal(1).scaleb(exponent)
    try:
        return actual.quantize(quantum) == recorded
    except (InvalidOperation, ValueError, ArithmeticError):
        return False


def _validate_snapshot(
    *,
    score_date: date,
    total_score: Any,
    quant_score: Any,
    scoring_snapshot: Any,
    qualitative_snapshot: Any,
    qualitative_sources: Any,
    qualitative_mode: Any,
) -> None:
    snapshot = _parse_json_object(scoring_snapshot, "scoring_snapshot")
    for key in ("scoring_inputs", "weights", "result", "implementation"):
        if not isinstance(snapshot.get(key), Mapping):
            raise EvaluationInputError(f"scoring_snapshot_{key}_missing")
    if (
        not _finite_snapshot_numbers(snapshot)
        or _finite_float(total_score) is None
        or _finite_float(quant_score) is None
    ):
        raise EvaluationInputError("snapshot_non_finite")
    result = snapshot["result"]
    if _finite_float(quant_score) is None:
        raise EvaluationInputError("quant_score_missing")
    if "total_score" not in result or "quant_score" not in result:
        raise EvaluationInputError("snapshot_result_scores_missing")
    component_scores = result.get("component_scores")
    if not isinstance(component_scores, Mapping):
        raise EvaluationInputError("snapshot_component_scores_missing")
    if not _same_recorded_precision(total_score, result["total_score"]):
        raise EvaluationInputError("snapshot_total_score_conflict")
    if not _same_recorded_precision(quant_score, result["quant_score"]):
        raise EvaluationInputError("snapshot_quant_score_conflict")
    scoring_inputs = snapshot["scoring_inputs"]
    _validate_known_numeric_fields(scoring_inputs, result)
    qualitative = _parse_json_object(qualitative_snapshot, "qualitative_snapshot")
    sources = _parse_json_object(qualitative_sources, "qualitative_sources")
    dimensions = {"moat", "market_pos", "sentiment"}
    if set(qualitative) != dimensions or set(sources) != dimensions:
        raise EvaluationInputError("qualitative_snapshot_conflict")
    if not all(
        isinstance(value, (int, Decimal))
        and not isinstance(value, bool)
        and Decimal(value) == Decimal(value).to_integral_value()
        and 0 <= value <= {"moat": 10, "market_pos": 5, "sentiment": 5}[key]
        for key, value in qualitative.items()
    ):
        raise EvaluationInputError("qualitative_value_invalid")
    if not all(isinstance(source, str) and source in {"v1", "v2"} for source in sources.values()):
        raise EvaluationInputError("qualitative_source_invalid")
    if not isinstance(qualitative_mode, str) or qualitative_mode not in {"v1", "v2", "hybrid_v2"}:
        raise EvaluationInputError("qualitative_mode_invalid")
    expected_mode = "v2" if all(source == "v2" for source in sources.values()) else "v1"
    if any(source == "v2" for source in sources.values()) and any(source == "v1" for source in sources.values()):
        expected_mode = "hybrid_v2"
    if qualitative_mode != expected_mode:
        raise EvaluationInputError("qualitative_mode_conflict")
    fixed_fields = {"moat": "moat_fixed", "market_pos": "market_pos_fixed", "sentiment": "sentiment_fixed"}
    if any(
        field not in scoring_inputs
        or isinstance(scoring_inputs[field], bool)
        or not _same_recorded_precision(qualitative[dimension], scoring_inputs[field])
        for dimension, field in fixed_fields.items()
    ):
        raise EvaluationInputError("qualitative_scoring_input_conflict")
    if any(
        field not in component_scores or not _same_recorded_precision(qualitative[dimension], component_scores[field])
        for dimension, field in fixed_fields.items()
    ):
        raise EvaluationInputError("qualitative_component_score_conflict")

    as_of = snapshot.get("qualitative_as_of")
    if not isinstance(as_of, Mapping) or set(as_of) != dimensions:
        raise EvaluationInputError("qualitative_as_of_conflict")
    for dimension, raw_as_of in as_of.items():
        if raw_as_of is None:
            continue
        if not isinstance(raw_as_of, str):
            raise EvaluationInputError("qualitative_as_of_invalid")
        try:
            source_date = date.fromisoformat(raw_as_of)
        except ValueError as exc:
            raise EvaluationInputError("qualitative_as_of_invalid") from exc
        if source_date > score_date:
            raise EvaluationInputError("qualitative_as_of_future")


def load_experiment_manifest(source: str | Path | Mapping[str, Any] | ExperimentManifest) -> ExperimentManifest:
    """Load and validate an explicitly supplied manifest.

    The default tracked manifest is selected by the report layer from the central
    project paths module; this loader remains usable with explicit sources.
    """
    if isinstance(source, ExperimentManifest):
        return source
    if isinstance(source, Mapping):
        value = source
    else:
        try:
            path = Path(source)
        except TypeError as exc:
            raise ManifestError("manifest source must be an object or path") from exc
        try:
            raw_manifest = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ManifestError(f"manifest_missing:{path}") from exc
        except OSError as exc:
            raise ManifestError(f"manifest_unreadable:{path}") from exc
        except UnicodeError as exc:
            raise ManifestError(f"manifest_unreadable:{path}") from exc
        value = _strict_manifest_json(raw_manifest)
    if not isinstance(value, Mapping):
        raise ManifestError("manifest must be an object")
    if "experiments" in value:
        experiments = value["experiments"]
        if not isinstance(experiments, list) or len(experiments) != 1 or not isinstance(experiments[0], Mapping):
            raise ManifestError("manifest experiments must contain exactly one experiment")
        value = experiments[0]
    return ExperimentManifest.from_mapping(value)


def build_bucket_weights(scores: Mapping[str, float], bucket: str = "q5") -> dict[str, float]:
    """Return fixed fractional Q5/Q1 weights without using future outcomes."""
    if bucket not in {"q5", "q1", "top", "bottom"}:
        raise ValueError("bucket must be q5/top or q1/bottom")
    if not scores:
        return {}
    values = {str(code): float(score) for code, score in scores.items()}
    if any(not math.isfinite(score) for score in values.values()):
        raise ValueError("scores must be finite")
    k = max(1, len(values) // 5)
    ordered = sorted(values.values(), reverse=bucket in {"q5", "top"})
    boundary = ordered[k - 1]
    strict_codes = (
        [code for code, score in values.items() if score > boundary]
        if bucket in {"q5", "top"}
        else [code for code, score in values.items() if score < boundary]
    )
    boundary_codes = [code for code, score in values.items() if score == boundary]
    weights = {code: 0.0 for code in values}
    if len(strict_codes) >= k:
        for code in sorted(strict_codes)[:k]:
            weights[code] = 1.0 / k
        return weights
    for code in strict_codes:
        weights[code] = 1.0 / k
    boundary_weight = (k - len(strict_codes)) / (len(boundary_codes) * k)
    for code in boundary_codes:
        weights[code] = boundary_weight
    return weights


def select_non_overlapping_sections(sections: Sequence[ScoreSection], window_days: int) -> list[ScoreSection]:
    """Select fixed score dates before any future price is inspected."""
    selected: list[ScoreSection] = []
    next_eligible = date.min
    for section in sorted(sections, key=lambda item: item.score_date):
        if section.score_date < next_eligible:
            continue
        selected.append(section)
        next_eligible = section.score_date + timedelta(days=window_days)
    return selected


def load_qualified_score_sections(
    db: sqlite3.Connection,
    manifest: ExperimentManifest,
    evaluation_as_of: date,
) -> tuple[list[ScoreSection], dict[str, int], list[str]]:
    """Load score-time-qualified sections without reading future prices."""
    try:
        column_names = {str(row[1]) for row in db.execute("PRAGMA table_info(predictions)").fetchall()}
        if "quant_score" not in column_names:
            raise EvaluationInputError("predictions_quant_score_missing")
        raw_rows = db.execute(
            f"""SELECT code, framework, score_date, total_score, l3_v2_signal,
                      weights_hash, scoring_snapshot_json, qualitative_snapshot_json,
                      qualitative_sources_json, qualitative_mode, quant_score
               FROM predictions
               WHERE framework=? AND score_date BETWEEN ? AND ?
               ORDER BY score_date, code""",
            (
                EXPECTED_FRAMEWORK,
                (manifest.effective_from or evaluation_as_of).isoformat(),
                min(manifest.effective_to or evaluation_as_of, evaluation_as_of).isoformat(),
            ),
        ).fetchall()
    except EvaluationInputError:
        raise
    except sqlite3.Error as exc:
        raise EvaluationInputError(f"predictions_unreadable:{exc}") from exc

    by_date_code: dict[tuple[date, str], list[tuple[Any, ...]]] = defaultdict(list)
    gaps: list[str] = []
    expected = set(manifest.expected_codes)
    counts = {"expected": len(expected), "actual_unique_predictions": 0, "qualified_snapshots": 0}
    for row in raw_rows:
        code, _framework, raw_date = str(row[0]), str(row[1]), str(row[2])
        try:
            score_date = date.fromisoformat(raw_date)
        except ValueError:
            gaps.append(f"invalid_score_date:{raw_date}")
            continue
        if code in expected:
            by_date_code[(score_date, code)].append(row)
        else:
            gaps.append(f"pool_outside:{score_date.isoformat()}:{code}")

    qualified_by_date: dict[date, list[QualifiedScore]] = defaultdict(list)
    for (score_date, code), raw_rows in sorted(by_date_code.items()):
        registered_rows = [row for row in raw_rows if row[5] in manifest.scoring_hashes]
        if not registered_rows:
            hashes = sorted({str(row[5]) for row in raw_rows})
            gaps.append(f"unregistered_scoring_hash:{score_date.isoformat()}:{','.join(hashes)}")
            continue
        if len(registered_rows) != 1:
            gaps.append(f"conflicting_prediction:{score_date.isoformat()}:{code}")
            continue
        row = registered_rows[0]
        counts["actual_unique_predictions"] += 1
        score_value = _finite_float(row[3])
        if score_value is None:
            gaps.append(f"invalid_score:{score_date.isoformat()}:{code}")
            continue
        try:
            _validate_snapshot(
                score_date=score_date,
                total_score=row[3],
                quant_score=row[10],
                scoring_snapshot=row[6],
                qualitative_snapshot=row[7],
                qualitative_sources=row[8],
                qualitative_mode=row[9],
            )
        except EvaluationInputError as exc:
            gaps.append(f"{exc}:{score_date.isoformat()}:{code}")
            continue
        signal = int(row[4]) if row[4] in (0, 1) else None
        qualified_by_date[score_date].append(QualifiedScore(code, score_date, score_value, signal))

    sections: list[ScoreSection] = []
    required_coverage = math.ceil(len(expected) * MIN_COVERAGE)
    for score_date, score_rows in sorted(qualified_by_date.items()):
        score_rows.sort(key=lambda item: item.code)
        counts["qualified_snapshots"] += len(score_rows)
        if len(score_rows) < required_coverage:
            gaps.append(f"coverage_below_minimum:{score_date.isoformat()}:{len(score_rows)}/{required_coverage}")
            continue
        sections.append(ScoreSection(score_date, tuple(score_rows)))
    return sections, counts, gaps


def _load_price_series(
    db: sqlite3.Connection,
    evaluation_as_of: date,
) -> tuple[dict[str, dict[str, float]], dict[str, float], list[str]]:
    gaps: list[str] = []
    stock_rows = db.execute(
        """SELECT code, trade_date, close FROM daily_bars
           WHERE adjusted='qfq' AND source='tushare.pro_bar.qfq'
             AND close IS NOT NULL AND trade_date <= ?
           ORDER BY code, trade_date""",
        (evaluation_as_of.isoformat(),),
    ).fetchall()
    stocks: dict[str, dict[str, float]] = defaultdict(dict)
    stock_counts: dict[tuple[str, str], int] = defaultdict(int)
    for code, trade_date, close in stock_rows:
        code_text, date_text = str(code), str(trade_date)
        try:
            parsed_date = date.fromisoformat(date_text)
        except ValueError:
            gaps.append(f"stock_price_invalid_date:{code_text}:{date_text}")
            continue
        if parsed_date > evaluation_as_of:
            continue
        key = (code_text, date_text)
        stock_counts[key] += 1
        try:
            value = float(close)
        except (OverflowError, TypeError, ValueError):
            gaps.append(f"stock_price_invalid:{code_text}:{date_text}")
            continue
        if math.isfinite(value) and value > 0:
            stocks[code_text][date_text] = value
        else:
            gaps.append(f"stock_price_invalid:{code_text}:{date_text}")
    for (code, date_text), count in stock_counts.items():
        if count > 1:
            stocks[code].pop(date_text, None)
            gaps.append(f"stock_price_duplicate:{code}:{date_text}")
    benchmark_rows = db.execute(
        "SELECT date, close FROM index_prices WHERE symbol='H00300' AND date <= ? ORDER BY date",
        (evaluation_as_of.isoformat(),),
    ).fetchall()
    benchmark: dict[str, float] = {}
    benchmark_counts: dict[str, int] = defaultdict(int)
    for raw_date, close in benchmark_rows:
        date_text = str(raw_date)
        try:
            parsed_date = date.fromisoformat(date_text)
        except ValueError:
            gaps.append(f"benchmark_price_invalid_date:{date_text}")
            continue
        if parsed_date > evaluation_as_of:
            continue
        benchmark_counts[date_text] += 1
        try:
            value = float(close)
        except (OverflowError, TypeError, ValueError):
            gaps.append(f"benchmark_price_invalid:{date_text}")
            continue
        if math.isfinite(value) and value > 0:
            benchmark[date_text] = value
        else:
            gaps.append(f"benchmark_price_invalid:{date_text}")
    for date_text, count in benchmark_counts.items():
        if count > 1:
            benchmark.pop(date_text, None)
            gaps.append(f"benchmark_price_duplicate:{date_text}")
    return dict(stocks), benchmark, list(dict.fromkeys(gaps))


def _local_calendar(_db: sqlite3.Connection, _evaluation_as_of: date) -> tuple[list[date], str | None]:
    # No such interface is part of this repository's data contract.
    return [], "calendar_unavailable"


def _normalise_calendar(
    db: sqlite3.Connection,
    calendar: CalendarEvidence | Sequence[str | date] | None,
    evaluation_as_of: date,
) -> tuple[list[date], str | None]:
    if calendar is None:
        return _local_calendar(db, evaluation_as_of)
    if not isinstance(calendar, CalendarEvidence):
        return [], "calendar_unavailable:explicit_coverage_required"
    try:
        calendar = CalendarEvidence.from_dates(
            calendar.dates,
            covered_from=calendar.covered_from,
            covered_to=calendar.covered_to,
            as_of=calendar.as_of,
            source=calendar.source,
        )
    except ValueError as exc:
        return [], f"calendar_invalid:{exc}"
    if calendar.as_of < evaluation_as_of or calendar.covered_to < evaluation_as_of:
        return [], "calendar_coverage_insufficient"
    if calendar.covered_from > min(evaluation_as_of, calendar.covered_to):
        return [], "calendar_coverage_insufficient"
    try:
        parsed = sorted({item for item in calendar.dates if item <= evaluation_as_of})
    except (TypeError, ValueError) as exc:
        return [], f"calendar_invalid:{exc}"
    parsed = [item for item in parsed if item <= evaluation_as_of]
    return parsed, None if parsed else "calendar_empty"


def _endpoint_date(score_date: date, window_days: int, calendar: Sequence[date]) -> date | None:
    target = score_date + timedelta(days=window_days)
    available = [item for item in calendar if item <= target and (target - item).days <= MAX_LAG_DAYS]
    return available[-1] if available else None


def _aligned_calendar_date(anchor: date, calendar: Sequence[date]) -> date | None:
    available = [item for item in calendar if item <= anchor and (anchor - item).days <= MAX_LAG_DAYS]
    return available[-1] if available else None


def _weighted(values: Mapping[str, float | None], weights: Mapping[str, float]) -> float | None:
    total = 0.0
    for code, weight in weights.items():
        if weight <= 0:
            continue
        value = values.get(code)
        if value is None or not math.isfinite(weight) or not math.isfinite(value):
            return None
        try:
            total += weight * value
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(total):
            return None
    return total


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        rank = (start + 1 + end) / 2.0
        for index in ordered[start:end]:
            ranks[index] = rank
        start = end
    return ranks


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    if any(not math.isfinite(value) for value in (*left, *right)):
        return None
    try:
        left_mean = sum(left) / len(left)
        right_mean = sum(right) / len(right)
        numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True))
        left_scale = math.sqrt(sum((x - left_mean) ** 2 for x in left))
        right_scale = math.sqrt(sum((y - right_mean) ** 2 for y in right))
        result = None if left_scale == 0 or right_scale == 0 else numerator / (left_scale * right_scale)
    except (OverflowError, ValueError, ZeroDivisionError):
        return None
    return result if result is None or math.isfinite(result) else None


def _daily_nav_path(
    weights: Mapping[str, float],
    entry: date,
    endpoint: date,
    calendar: Sequence[date],
    prices: Mapping[str, Mapping[str, float]],
) -> list[tuple[date, float]] | None:
    dates = [item for item in calendar if entry <= item <= endpoint]
    if not dates:
        return None
    initial = {code: prices.get(code, {}).get(entry.isoformat()) for code in weights if weights[code] > 0}
    if any(value is None or value <= 0 for value in initial.values()):
        return None
    path: list[tuple[date, float]] = []
    for day in dates:
        nav = 0.0
        for code, weight in weights.items():
            if weight == 0:
                continue
            mark = prices.get(code, {}).get(day.isoformat())
            if mark is None or mark <= 0:
                return None
            entry_price = initial[code]
            if entry_price is None:
                return None
            ratio = _safe_ratio(mark, entry_price)
            if ratio is None:
                return None
            try:
                nav += weight * ratio
            except (OverflowError, ValueError):
                return None
            if not math.isfinite(nav) or nav <= 0:
                return None
        path.append((day, nav))
    return path


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if not math.isfinite(numerator) or not math.isfinite(denominator) or denominator <= 0:
        return None
    try:
        result = numerator / denominator
    except (OverflowError, ZeroDivisionError):
        return None
    return result if math.isfinite(result) else None


def _safe_return(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    ratio = _safe_ratio(numerator, denominator)
    if ratio is None:
        return None
    result = (ratio - 1.0) * 100.0
    return result if math.isfinite(result) else None


def _safe_difference(left: float, right: float) -> float | None:
    try:
        result = left - right
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _finite_average(values: Sequence[float]) -> float | None:
    if not values:
        return None
    total = 0.0
    for value in values:
        if not math.isfinite(value):
            return None
        try:
            total += value
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(total):
            return None
    result = total / len(values)
    return result if math.isfinite(result) else None


def _max_drawdown(path: Sequence[tuple[date, float]]) -> float | None:
    if not path:
        return None
    peak = path[0][1]
    if not math.isfinite(peak) or peak <= 0:
        return None
    drawdown = 0.0
    for _day, nav in path:
        if not math.isfinite(nav) or nav <= 0:
            return None
        peak = max(peak, nav)
        ratio = _safe_ratio(nav, peak)
        if ratio is None:
            return None
        drawdown = min(drawdown, ratio - 1.0)
    result = drawdown * 100.0
    return result if math.isfinite(result) else None


def _chained_nav_path(
    evaluations: Sequence[SectionEvaluation],
    calendar: Sequence[date],
    prices: Mapping[str, Mapping[str, float]],
    weight_factory: Any,
) -> tuple[list[tuple[date, float]], str | None]:
    chain: list[tuple[date, float]] = []
    previous_endpoint: date | None = None
    base = 1.0
    for item in evaluations:
        if item.entry_date is None or item.endpoint is None:
            return [], f"nav_path_missing:{item.score_date.isoformat()}"
        if previous_endpoint is not None and item.entry_date != previous_endpoint:
            return [], f"nav_path_gap:{previous_endpoint.isoformat()}:{item.entry_date.isoformat()}"
        path = _daily_nav_path(
            weight_factory(item),
            item.entry_date,
            item.endpoint,
            calendar,
            prices,
        )
        if path is None:
            return [], f"nav_path_missing:{item.score_date.isoformat()}"
        for index, (day, local_nav) in enumerate(path):
            if previous_endpoint is not None and index == 0 and day == previous_endpoint:
                continue
            try:
                chained_nav = base * local_nav
            except (OverflowError, ValueError):
                return [], f"nav_non_finite:{item.score_date.isoformat()}"
            if not math.isfinite(chained_nav) or chained_nav <= 0:
                return [], f"nav_non_finite:{item.score_date.isoformat()}"
            chain.append((day, chained_nav))
        base = chain[-1][1]
        if not math.isfinite(base) or base <= 0:
            return [], f"nav_non_finite:{item.score_date.isoformat()}"
        previous_endpoint = item.endpoint
    return chain, None


def _chained_mdd(
    evaluations: Sequence[SectionEvaluation],
    calendar: Sequence[date],
    stocks: Mapping[str, Mapping[str, float]],
) -> tuple[float | None, str | None]:
    chain, gap = _chained_nav_path(evaluations, calendar, stocks, lambda item: item.q5_weights)
    if gap is not None:
        return None, f"q5_{gap}"
    return _max_drawdown(chain), None


def _tie_disclosure(scores: Mapping[str, float]) -> dict[str, Any]:
    if not scores:
        return {}
    k = max(1, len(scores) // 5)
    result: dict[str, Any] = {}
    for bucket, reverse in (("q5", True), ("q1", False)):
        boundary = sorted(scores.values(), reverse=reverse)[k - 1]
        boundary_codes = sorted(code for code, score in scores.items() if score == boundary)
        strict_count = sum(score > boundary if reverse else score < boundary for score in scores.values())
        weight = (k - strict_count) / (len(boundary_codes) * k)
        result[bucket] = {
            "k": k,
            "strict_count": strict_count,
            "boundary_count": len(boundary_codes),
            "boundary_weight_each": weight,
            "boundary_codes": boundary_codes,
        }
    q5_positive = {code for code, weight in build_bucket_weights(scores, "q5").items() if weight > 0}
    q1_positive = {code for code, weight in build_bucket_weights(scores, "q1").items() if weight > 0}
    result["q5_q1_overlap_count"] = len(q5_positive & q1_positive)
    return result


def evaluate_section(
    section: ScoreSection,
    window_days: int,
    *,
    calendar: Sequence[date],
    stocks: Mapping[str, Mapping[str, float]],
    benchmark: Mapping[str, float],
    evaluation_as_of: date,
) -> SectionEvaluation:
    scores = {row.code: row.total_score for row in section.rows}
    q5_weights = build_bucket_weights(scores, "q5")
    q1_weights = build_bucket_weights(scores, "q1")
    all_tied = len(set(scores.values())) == 1
    gaps = list(section.gaps)
    tie_disclosure = _tie_disclosure(scores)
    entry_date = _aligned_calendar_date(section.score_date, calendar)
    target = section.score_date + timedelta(days=window_days)
    endpoint = _endpoint_date(section.score_date, window_days, calendar)
    if evaluation_as_of < target:
        gaps.append("immature")
        return SectionEvaluation(
            section.score_date,
            entry_date,
            endpoint,
            section.rows,
            q5_weights,
            q1_weights,
            all_tied,
            {},
            0,
            False,
            False,
            False,
            tie_disclosure,
            tuple(gaps),
        )
    if entry_date is None:
        gaps.append("calendar_entry_missing")
    if endpoint is None:
        gaps.append("calendar_endpoint_missing")
        return SectionEvaluation(
            section.score_date,
            entry_date,
            None,
            section.rows,
            q5_weights,
            q1_weights,
            all_tied,
            {},
            0,
            False,
            False,
            False,
            tie_disclosure,
            tuple(gaps),
        )
    if endpoint > evaluation_as_of:
        gaps.append("immature_endpoint")
        return SectionEvaluation(
            section.score_date,
            entry_date,
            endpoint,
            section.rows,
            q5_weights,
            q1_weights,
            all_tied,
            {},
            0,
            False,
            False,
            False,
            tie_disclosure,
            tuple(gaps),
        )
    if entry_date is None:
        return SectionEvaluation(
            section.score_date,
            None,
            endpoint,
            section.rows,
            q5_weights,
            q1_weights,
            all_tied,
            {},
            0,
            False,
            False,
            False,
            tie_disclosure,
            tuple(gaps),
        )
    entry_key = entry_date.isoformat()
    endpoint_key = endpoint.isoformat()
    stock_returns: dict[str, float | None] = {}
    benchmark_returns: dict[str, float | None] = {}
    alpha: dict[str, float | None] = {}
    for row in section.rows:
        series = stocks.get(row.code, {})
        entry_price = series.get(entry_key)
        endpoint_value = series.get(endpoint_key)
        benchmark_entry = benchmark.get(entry_key)
        benchmark_target = benchmark.get(endpoint_key)
        if entry_price is None or endpoint_value is None:
            gaps.append(f"stock_path_missing:{row.code}")
        if benchmark_entry is None or benchmark_target is None:
            gaps.append("benchmark_path_missing")
        stock_return = _safe_return(endpoint_value, entry_price)
        benchmark_return = _safe_return(benchmark_target, benchmark_entry)
        if entry_price is not None and endpoint_value is not None and stock_return is None:
            gaps.append(f"stock_return_non_finite:{row.code}")
        if benchmark_entry is not None and benchmark_target is not None and benchmark_return is None:
            gaps.append("benchmark_return_non_finite")
        stock_returns[row.code] = stock_return
        benchmark_returns[row.code] = benchmark_return
        alpha[row.code] = (
            None
            if stock_return is None or benchmark_return is None
            else _safe_difference(stock_return, benchmark_return)
        )
    outcome_computable = sum(value is not None for value in alpha.values())

    equal_weights = {row.code: 1.0 / len(section.rows) for row in section.rows}
    metrics: dict[str, float | None] = {}
    q5_path = _daily_nav_path(q5_weights, entry_date, endpoint, calendar, stocks)
    equal_path = _daily_nav_path(equal_weights, entry_date, endpoint, calendar, stocks)
    benchmark_path = _daily_nav_path({"H00300": 1.0}, entry_date, endpoint, calendar, {"H00300": benchmark})
    path_ready = q5_path is not None
    if q5_path is None:
        gaps.append("q5_daily_path_missing")
    if equal_path is None:
        gaps.append("equal_daily_path_missing")
    if benchmark_path is None:
        gaps.append("benchmark_daily_path_missing")
    if all_tied:
        metrics.update({"ic": None, "spread": None, "q5_return": None, "q5_minus_equal": None, "q5_mdd": None})
        gaps.append("ALL_SCORES_TIED")
    else:
        alpha_values = [alpha[row.code] for row in section.rows]
        complete_alpha = [value for value in alpha_values if value is not None]
        metrics["ic"] = (
            _correlation(
                _average_ranks([row.total_score for row in section.rows]),
                _average_ranks([float(value) for value in complete_alpha]),
            )
            if len(complete_alpha) == len(alpha_values)
            else None
        )
        q5_alpha = _weighted(alpha, q5_weights)
        q1_alpha = _weighted(alpha, q1_weights)
        q5_return = _weighted(stock_returns, q5_weights)
        equal_return = _weighted(stock_returns, equal_weights)
        metrics["spread"] = None if q5_alpha is None or q1_alpha is None else q5_alpha - q1_alpha
        metrics["q5_return"] = q5_return
        metrics["q5_minus_equal"] = None if q5_return is None or equal_return is None else q5_return - equal_return
        metrics["q5_mdd"] = _max_drawdown(q5_path) if q5_path is not None else None
    metrics["equal_return"] = _weighted(stock_returns, equal_weights)
    metrics["benchmark_return"] = _weighted(benchmark_returns, q5_weights)
    return SectionEvaluation(
        section.score_date,
        entry_date,
        endpoint,
        section.rows,
        q5_weights,
        q1_weights,
        all_tied,
        metrics,
        outcome_computable,
        path_ready,
        equal_path is not None,
        benchmark_path is not None,
        tie_disclosure,
        tuple(dict.fromkeys(gaps)),
    )


def pending_manifest_summary(manifest: ExperimentManifest, evaluation_as_of: date | None) -> dict[str, Any]:
    metrics = {
        "ic": None,
        "spread": None,
        "q5_return": None,
        "equal_return": None,
        "q5_minus_equal": None,
        "benchmark_return": None,
        "q5_mdd": None,
    }
    windows = {
        str(window): {
            "expected": len(manifest.expected_codes),
            "actual_unique_predictions": None,
            "qualified_snapshots": None,
            "selected_snapshot_count": None,
            "outcome_computable": None,
            "required_coverage": math.ceil(len(manifest.expected_codes) * manifest.min_coverage),
            "selected_dates": [],
            "selected_sections": None,
            "due_sections": None,
            "mature_sections": None,
            "immature_sections": None,
            "due_dates": [],
            "metric_scope": "due_fixed_sections_no_outcome_filter",
            "gaps": ["manifest_pending"],
            "metrics": dict(metrics),
            "all_scores_tied": False,
            "path_ready": None,
            "q5_path_ready": None,
            "equal_path_ready": None,
            "benchmark_path_ready": None,
            "tie_disclosure": [],
            "l3_recorded": {"normal": None, "triggered": None, "unknown": None},
            "price_diagnostics": [],
            "endpoint_diagnostics": [],
            "readiness_status": "INSUFFICIENT_EVIDENCE",
            "readiness_gaps": ["manifest_pending"],
        }
        for window in WINDOWS
    }
    return {
        "experiment_id": manifest.experiment_id,
        "manifest_hash": manifest.manifest_hash,
        "evaluation_version": None,
        "protocol_status": "MANIFEST_PENDING",
        "scoring_hashes": list(manifest.scoring_hashes),
        "as_of": evaluation_as_of.isoformat() if evaluation_as_of else None,
        "evidence_status": "INSUFFICIENT_EVIDENCE",
        "verdict": None,
        "gaps": ["manifest_pending"],
        "price_diagnostics": [],
        "windows": windows,
    }


def _evaluate_manifest(
    db: sqlite3.Connection,
    manifest: ExperimentManifest,
    *,
    evaluation_as_of: date,
    calendar: CalendarEvidence | Sequence[str | date] | None = None,
) -> dict[str, Any]:
    if manifest.registration_status != "verified":
        return pending_manifest_summary(manifest, evaluation_as_of)
    if manifest.effective_from is None or manifest.effective_to is None:
        raise EvaluationInputError("verified_manifest_dates_missing")
    calendar_dates, calendar_gap = _normalise_calendar(db, calendar, evaluation_as_of)
    sections, counts, score_gaps = load_qualified_score_sections(db, manifest, evaluation_as_of)
    selected_by_window = {
        window_days: select_non_overlapping_sections(sections, window_days) for window_days in WINDOWS
    }
    stocks, benchmark, price_gaps = _load_price_series(db, evaluation_as_of)
    windows: dict[str, Any] = {}
    overall_gaps = list(score_gaps)
    for window_days in WINDOWS:
        selected = selected_by_window[window_days]
        evaluations = [
            evaluate_section(
                section,
                window_days,
                calendar=calendar_dates,
                stocks=stocks,
                benchmark=benchmark,
                evaluation_as_of=evaluation_as_of,
            )
            for section in selected
        ]
        scheduled_evaluations = evaluations
        # Maturity depends only on the fixed score date and report cutoff, never prices.
        evaluations = [
            item for item in scheduled_evaluations if item.score_date + timedelta(days=window_days) <= evaluation_as_of
        ]
        aggregated: dict[str, float | None] = {}
        for metric in ("ic", "spread"):
            values = [item.metrics.get(metric) for item in evaluations]
            numeric_values = [float(value) for value in values if value is not None]
            aggregated[metric] = _finite_average(numeric_values)
        chains: dict[str, tuple[list[tuple[date, float]], str | None]] = {}
        path_ready_by_metric = {
            "q5_return": all(item.path_ready for item in evaluations) and bool(evaluations),
            "equal_return": all(item.equal_path_ready for item in evaluations) and bool(evaluations),
            "benchmark_return": all(item.benchmark_path_ready for item in evaluations) and bool(evaluations),
        }
        if path_ready_by_metric["q5_return"]:
            chains["q5_return"] = _chained_nav_path(evaluations, calendar_dates, stocks, lambda item: item.q5_weights)
        if path_ready_by_metric["equal_return"]:
            chains["equal_return"] = _chained_nav_path(
                evaluations,
                calendar_dates,
                stocks,
                lambda item: {row.code: 1.0 / len(item.rows) for row in item.rows},
            )
        if path_ready_by_metric["benchmark_return"]:
            chains["benchmark_return"] = _chained_nav_path(
                evaluations, calendar_dates, {"H00300": benchmark}, lambda _item: {"H00300": 1.0}
            )
        chain_gaps = [
            f"{metric.removesuffix('_return')}_{result[1]}"
            for metric, result in chains.items()
            if result[1] is not None
        ]
        chain_gaps.extend(
            f"{metric.removesuffix('_return')}_path_incomplete"
            for metric, ready_for_path in path_ready_by_metric.items()
            if not ready_for_path
        )
        for metric in ("q5_return", "equal_return", "benchmark_return"):
            values = [item.metrics.get(metric) for item in evaluations]
            numeric_values = [float(value) for value in values if value is not None]
            chain = chains.get(metric)
            if (
                len(numeric_values) != len(values)
                or not values
                or not path_ready_by_metric[metric]
                or (chain is not None and chain[1] is not None)
            ):
                aggregated[metric] = None
            else:
                equity = 1.0
                for value in numeric_values:
                    try:
                        equity *= 1.0 + value / 100.0
                    except (OverflowError, ValueError):
                        equity = math.inf
                        break
                compound_return = (equity - 1.0) * 100.0 if math.isfinite(equity) else math.inf
                aggregated[metric] = compound_return if math.isfinite(compound_return) else None
        if aggregated["q5_return"] is None or aggregated["equal_return"] is None:
            aggregated["q5_minus_equal"] = None
        else:
            aggregated["q5_minus_equal"] = _safe_difference(aggregated["q5_return"], aggregated["equal_return"])
        if any(item.all_scores_tied for item in evaluations):
            aggregated["q5_mdd"] = None
            mdd_gaps = chain_gaps
        else:
            aggregated["q5_mdd"], mdd_gap = _chained_mdd(evaluations, calendar_dates, stocks)
            mdd_gaps = list(chain_gaps)
            if mdd_gap is not None:
                # Added to the same window below so N/A has an adjacent reason.
                mdd_gaps.append(mdd_gap)
        window_gaps = [] if calendar_gap is None else [calendar_gap]
        window_gaps.extend(gap for item in evaluations for gap in item.gaps)
        window_gaps.extend(mdd_gaps)
        if not selected:
            window_gaps.append("no_selected_sections")
        if len(evaluations) < MINIMUM_SECTIONS[window_days]:
            window_gaps.append(f"sections_below_minimum:{len(evaluations)}/{MINIMUM_SECTIONS[window_days]}")
        windows[str(window_days)] = {
            "expected": counts["expected"],
            "actual_unique_predictions": counts["actual_unique_predictions"],
            "qualified_snapshots": counts["qualified_snapshots"],
            "selected_snapshot_count": sum(len(item.rows) for item in scheduled_evaluations),
            "outcome_computable": sum(item.outcome_computable for item in evaluations),
            "required_coverage": math.ceil(counts["expected"] * manifest.min_coverage),
            "selected_dates": [item.score_date.isoformat() for item in scheduled_evaluations],
            "selected_sections": len(scheduled_evaluations),
            "due_sections": len(evaluations),
            "mature_sections": sum(item.entry_date is not None and item.endpoint is not None for item in evaluations),
            "due_dates": [item.score_date.isoformat() for item in evaluations],
            "immature_sections": len(scheduled_evaluations) - len(evaluations),
            "metric_scope": "due_fixed_sections_no_outcome_filter",
            "price_diagnostics": price_gaps,
            "endpoint_diagnostics": [
                {
                    "score_date": item.score_date.isoformat(),
                    "maturity": (
                        "immature"
                        if item.score_date + timedelta(days=window_days) > evaluation_as_of
                        else "mature"
                        if item.entry_date is not None and item.endpoint is not None
                        else "calendar_unresolved"
                    ),
                    "entry_date": item.entry_date.isoformat() if item.entry_date else None,
                    "endpoint": item.endpoint.isoformat() if item.endpoint else None,
                    "metrics": dict(item.metrics),
                    "path_ready": item.path_ready,
                    "gaps": list(item.gaps),
                }
                for item in scheduled_evaluations
            ],
            "gaps": list(dict.fromkeys(window_gaps)),
            "metrics": aggregated,
            "all_scores_tied": any(item.all_scores_tied for item in evaluations),
            "path_ready": all(item.path_ready for item in evaluations) if evaluations else False,
            "q5_path_ready": all(item.path_ready for item in evaluations) if evaluations else False,
            "equal_path_ready": all(item.equal_path_ready for item in evaluations) if evaluations else False,
            "benchmark_path_ready": all(item.benchmark_path_ready for item in evaluations) if evaluations else False,
            "tie_disclosure": [item.tie_disclosure for item in evaluations],
            "l3_recorded": {
                "normal": sum(row.l3_v2_signal == 0 for item in evaluations for row in item.rows),
                "triggered": sum(row.l3_v2_signal == 1 for item in evaluations for row in item.rows),
                "unknown": sum(row.l3_v2_signal is None for item in evaluations for row in item.rows),
            },
            "evaluations": evaluations,
        }
        overall_gaps.extend(window_gaps)
    ready = True
    for key, window in windows.items():
        blocking_gaps = [gap for gap in window["gaps"] if gap != "ALL_SCORES_TIED"]
        complete = all(
            window["metrics"].get(metric) is not None
            for metric in ("ic", "spread", "q5_return", "equal_return", "q5_minus_equal", "benchmark_return", "q5_mdd")
        )
        if window["all_scores_tied"]:
            complete = (
                window["metrics"].get("equal_return") is not None
                and window["metrics"].get("benchmark_return") is not None
                and window["equal_path_ready"]
                and window["benchmark_path_ready"]
            )
        window_ready = window["mature_sections"] >= MINIMUM_SECTIONS[int(key)] and not blocking_gaps and complete
        window["readiness_status"] = "READY_FOR_DIRECTION_REVIEW" if window_ready else "INSUFFICIENT_EVIDENCE"
        window["readiness_gaps"] = blocking_gaps
        ready = ready and window_ready
    all_tied = ready and any(window["all_scores_tied"] for window in windows.values())
    return {
        "experiment_id": manifest.experiment_id,
        "manifest_hash": manifest.manifest_hash,
        "evaluation_version": EVALUATION_VERSION,
        "protocol_status": "S2_EVALUATION",
        "scoring_hashes": list(manifest.scoring_hashes),
        "as_of": evaluation_as_of.isoformat(),
        "evidence_status": "READY_FOR_DIRECTION_REVIEW" if ready else "INSUFFICIENT_EVIDENCE",
        "verdict": "MANUAL_REVIEW_REQUIRED" if all_tied else None,
        "gaps": list(dict.fromkeys(overall_gaps)),
        "price_diagnostics": price_gaps,
        "windows": windows,
    }


@contextmanager
def _read_transaction(db: sqlite3.Connection) -> Iterator[None]:
    """Use one consistent read snapshot; never release a caller-owned transaction."""
    own_transaction = not db.in_transaction
    if own_transaction:
        db.execute("BEGIN")
    try:
        yield
    except Exception:
        if own_transaction:
            db.rollback()
        raise
    else:
        if own_transaction:
            db.commit()


def evaluate_manifest(
    db: sqlite3.Connection,
    manifest: ExperimentManifest,
    *,
    evaluation_as_of: date,
    calendar: CalendarEvidence | Sequence[str | date] | None = None,
) -> dict[str, Any]:
    with _read_transaction(db):
        return _evaluate_manifest(db, manifest, evaluation_as_of=evaluation_as_of, calendar=calendar)


def strip_internal_evaluations(summary: dict[str, Any]) -> dict[str, Any]:
    """Remove row objects before a summary is serialized or exposed to callers."""
    result = dict(summary)
    result["windows"] = {
        key: {field: value for field, value in window.items() if field != "evaluations"}
        for key, window in summary["windows"].items()
    }
    return result
