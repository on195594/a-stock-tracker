"""Immutable, candidate-only historical outcome shadow materialization."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = 1
ALGORITHM_VERSION = "tushare_raw_price_return_v1"
STOCK_SOURCE = "tushare.daily"
BENCHMARK_SOURCE = "tushare.index_daily"
RAW_ADJUSTED = "none"
WINDOW_DAYS = (30, 60, 90)
PROTECTION_COLUMNS = (
    "id",
    "code",
    "framework",
    "score_date",
    "price_at_score",
    "quant_score",
    "total_score",
    "weights_hash",
    "report_period",
    "created_at",
)
EVENT_COLUMNS = ("id", "code", "name", *PROTECTION_COLUMNS[2:])
OUTCOME_COLUMNS = (
    "outcome_30d",
    "outcome_60d",
    "outcome_90d",
    "benchmark_30d",
    "benchmark_60d",
    "benchmark_90d",
)
# PROTECTION_COLUMNS covers prediction identity and scoring only. Runs whose subject
# matter is the outcome/benchmark values themselves must additionally pin those
# columns, so mutating them cannot slip past the pre/post comparison. Never fold these
# into PROTECTION_COLUMNS itself: doing so would change the protection hash of the
# already-frozen Phase 1/2 runs and make them unverifiable.
EXTENDED_PROTECTION_COLUMNS = PROTECTION_COLUMNS + OUTCOME_COLUMNS


@dataclass(frozen=True)
class ShadowAlgorithm:
    """One shadow computation contract.

    Every field that differs between algorithm versions lives here, so a caller that
    passes no algorithm keeps the exact Phase 1/2 behaviour byte for byte.
    """

    version: str
    stock_adjusted: str
    stock_source: str
    benchmark_symbol: str
    windows: tuple[int, ...]
    compute_stored_entry: bool
    use_extended_protection: bool


RAW_PRICE_RETURN_V1 = ShadowAlgorithm(
    version=ALGORITHM_VERSION,
    stock_adjusted=RAW_ADJUSTED,
    stock_source=STOCK_SOURCE,
    benchmark_symbol="000300",
    windows=WINDOW_DAYS,
    compute_stored_entry=True,
    use_extended_protection=False,
)
# Stock side is qfq (total return), so the benchmark must be the total-return index or
# alpha is overstated by the index dividend yield. `stored_entry` is dropped because
# pairing a raw price_at_score with a qfq target mixes two price scales.
QFQ_TOTAL_RETURN_V1 = ShadowAlgorithm(
    version="qfq_total_return_v1",
    stock_adjusted="qfq",
    stock_source="tushare.pro_bar.qfq",
    benchmark_symbol="H00300.CSI",
    windows=(30, 60),
    compute_stored_entry=False,
    use_extended_protection=True,
)


class ShadowContractError(RuntimeError):
    """Raised when the outcome shadow contract cannot be satisfied."""


@dataclass(frozen=True)
class CohortInspection:
    """Read-only frozen cohort summary."""

    event_count: int
    window_counts: dict[int, int]
    cohort_hash: str
    predictions_count: int
    predictions_protection_hash: str


@dataclass(frozen=True)
class BuildResult:
    """Completed candidate run summary."""

    run_id: str
    manifest_hash: str
    event_count: int
    computed_count: int
    missing_count: int
    mismatch_count: int
    candidate_db: Path


@dataclass(frozen=True)
class ReportResult:
    """Paths and row count for one generated read-only report."""

    json_path: Path
    markdown_path: Path
    detail_count: int


@dataclass(frozen=True)
class FrozenEvent:
    """One immutable prediction/window event."""

    prediction_id: int
    code: str
    name: str | None
    framework: str
    score_date: str
    price_at_score: float
    quant_score: float | None
    total_score: float | None
    weights_hash: str | None
    report_period: str | None
    created_at: str | None
    window_days: int
    old_outcome: float | None
    old_benchmark: float | None


@dataclass(frozen=True)
class Observation:
    """One normalized source price observation."""

    instrument_type: str
    instrument_code: str
    trade_date: str
    close: float
    source: str
    adjusted: str
    fetched_at: str

    @property
    def observation_hash(self) -> str:
        return _stable_hash(
            (
                self.instrument_type,
                self.instrument_code,
                self.trade_date,
                self.close,
                self.source,
                self.adjusted,
                self.fetched_at,
            )
        )


@dataclass(frozen=True)
class ShadowRow:
    """One fully classified shadow result row."""

    values: tuple[Any, ...]
    row_hash: str
    status: str


def inspect_frozen_cohort(
    source_db: Path | str,
    as_of_date: str,
    algorithm: ShadowAlgorithm = RAW_PRICE_RETURN_V1,
) -> CohortInspection:
    """Inspect the due cohort through a read-only SQLite connection."""
    _parse_date(as_of_date)
    # Must match the variant build/verify will use, otherwise the hash this inspection
    # reports cannot be compared against the resulting run.
    protection = extended_predictions_protection if algorithm.use_extended_protection else _predictions_protection
    with _open_read_only(source_db) as conn:
        events = _load_frozen_events(conn, as_of_date, algorithm.windows)
        predictions_count, protection_hash = protection(conn)
    counts = {window: sum(event.window_days == window for event in events) for window in algorithm.windows}
    return CohortInspection(
        event_count=len(events),
        window_counts=counts,
        cohort_hash=_events_hash(events),
        predictions_count=predictions_count,
        predictions_protection_hash=protection_hash,
    )


def resolve_observation(
    observations: Mapping[str, float],
    anchor_date: str,
) -> tuple[str, float, int] | None:
    """Resolve the latest observation on/before anchor within 10 natural days."""
    anchor = _parse_date(anchor_date)
    for lag in range(11):
        candidate = (anchor - timedelta(days=lag)).isoformat()
        value = observations.get(candidate)
        if value is not None:
            return candidate, float(value), lag
    return None


def build_shadow_candidate(
    source_db: Path | str,
    candidate_db: Path | str,
    benchmark_snapshot: Path | str,
    *,
    as_of_date: str,
    algorithm: ShadowAlgorithm = RAW_PRICE_RETURN_V1,
) -> BuildResult:
    """Build one immutable shadow run inside a new isolated database copy."""
    source_path, candidate_path = _validate_paths(source_db, candidate_db)
    _parse_date(as_of_date)
    protection = extended_predictions_protection if algorithm.use_extended_protection else _predictions_protection
    try:
        _copy_source_database(source_path, candidate_path)
        with _open_read_only(candidate_path) as snapshot_conn:
            events = _load_frozen_events(snapshot_conn, as_of_date, algorithm.windows)
            prediction_count, protection_hash = protection(snapshot_conn)
            stock_observations = _load_stock_observations(snapshot_conn, events, as_of_date, algorithm)
        benchmark_observations = _load_benchmark_snapshot(benchmark_snapshot, events, as_of_date, algorithm)
        manifest = _build_manifest(
            events, stock_observations, benchmark_observations, as_of_date, protection_hash, algorithm
        )
        rows = _build_shadow_rows(events, stock_observations, benchmark_observations, algorithm)
        _write_candidate(
            candidate_path,
            manifest,
            prediction_count,
            protection_hash,
            stock_observations,
            benchmark_observations,
            rows,
        )
        _verify_candidate(candidate_path, prediction_count, protection_hash, len(events), algorithm)
    except Exception:
        candidate_path.unlink(missing_ok=True)
        raise
    computed = sum(row.status.startswith("computed_") for row in rows)
    mismatch = sum(row.status == "computed_calendar_mismatch" for row in rows)
    return BuildResult(
        run_id=manifest["run_id"],
        manifest_hash=manifest["manifest_hash"],
        event_count=len(events),
        computed_count=computed,
        missing_count=len(rows) - computed,
        mismatch_count=mismatch,
        candidate_db=candidate_path,
    )


def write_shadow_report(candidate_db: Path | str, run_id: str, output_json: Path | str) -> ReportResult:
    """Write a JSON evidence report and a concise Markdown companion."""
    output_path = Path(output_json).expanduser().resolve()
    if output_path.suffix.lower() != ".json":
        raise ShadowContractError("REPORT_OUTPUT_MUST_BE_JSON")
    markdown_path = output_path.with_suffix(".md")
    if output_path.exists() or markdown_path.exists():
        raise ShadowContractError("REPORT_OUTPUT_ALREADY_EXISTS")
    with _open_read_only(candidate_db) as conn:
        run = conn.execute("SELECT * FROM outcome_shadow_runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise ShadowContractError("RUN_NOT_FOUND")
        rows = conn.execute(
            "SELECT * FROM outcome_shadow_results WHERE run_id=? ORDER BY prediction_id,window_days",
            (run_id,),
        ).fetchall()
    payload = _report_payload(dict(run), [dict(row) for row in rows])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(_report_markdown(payload), encoding="utf-8")
    return ReportResult(output_path, markdown_path, len(rows))


def _report_payload(run: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    comparable = [row for row in rows if row["status"].startswith("computed_")]
    aligned = [row for row in comparable if row["aligned_alpha_eligible"] == 1]
    return {
        "schema_version": 1,
        "run": run,
        "summary": {
            "events": len(rows),
            "comparable": len(comparable),
            "unavailable": len(rows) - len(comparable),
            "aligned_alpha": len(aligned),
            "status_counts": _count_by(rows, "status"),
            "old_vs_stored_entry_shadow_changed": _difference_count(
                comparable, "old_outcome", "stored_entry_shadow_outcome"
            ),
            "stored_vs_reconstructed_changed": _difference_count(
                comparable, "stored_entry_shadow_outcome", "reconstructed_shadow_outcome"
            ),
            "old_vs_shadow_benchmark_changed": _difference_count(comparable, "old_benchmark", "shadow_benchmark"),
            "metrics": _metric_comparison(aligned),
        },
        "groups": {
            "framework": _group_summary(rows, "framework"),
            "window": _group_summary(rows, "window_days"),
            "score_date": _group_summary(rows, "score_date"),
            "code": _group_summary(rows, "code"),
            "score_quantile": _quantile_summary(rows),
            "pre_post_fix": _pre_post_summary(rows),
        },
        "caveats": [
            "TuShare entry is ex-post reconstructed, not a decision-time price snapshot.",
            "Raw price return excludes cash dividends and is not total shareholder return.",
            "Rows sharing one score_date have correlated market exposure and are not independent samples.",
            "Only 14 BaoStock outcome fallbacks are provable; broader differences must not be attributed wholesale to BaoStock.",
            "This report is descriptive and does not switch production outcome fields or reporting semantics.",
        ],
        "details": rows,
    }


def _count_by(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row[field])
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _difference_count(rows: Sequence[Mapping[str, Any]], left: str, right: str) -> int:
    return sum(
        row[left] is not None and row[right] is not None and abs(float(row[left]) - float(row[right])) > 1e-8
        for row in rows
    )


def _metric_comparison(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "old": _metrics(rows, "old_outcome", "old_alpha"),
        "stored_entry_shadow": _metrics(rows, "stored_entry_shadow_outcome", "stored_entry_shadow_alpha"),
        "reconstructed": _metrics(rows, "reconstructed_shadow_outcome", "reconstructed_shadow_alpha"),
    }


def _metrics(rows: Sequence[Mapping[str, Any]], outcome_field: str, alpha_field: str) -> dict[str, float | int | None]:
    outcomes = [float(row[outcome_field]) for row in rows if row[outcome_field] is not None]
    alphas = [float(row[alpha_field]) for row in rows if row[alpha_field] is not None]
    return {
        "n": min(len(outcomes), len(alphas)),
        "outcome_hit_rate": sum(value > 0 for value in outcomes) / len(outcomes) if outcomes else None,
        "alpha_hit_rate": sum(value > 0 for value in alphas) / len(alphas) if alphas else None,
        "avg_outcome": sum(outcomes) / len(outcomes) if outcomes else None,
        "avg_alpha": sum(alphas) / len(alphas) if alphas else None,
    }


def _group_summary(rows: Sequence[Mapping[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row[field]), []).append(row)
    return [
        {
            "group": key,
            "events": len(group_rows),
            "comparable": sum(str(row["status"]).startswith("computed_") for row in group_rows),
            "status_counts": _count_by(group_rows, "status"),
        }
        for key, group_rows in sorted(groups.items())
    ]


def _quantile_summary(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    scored = sorted((row for row in rows if row["total_score"] is not None), key=lambda row: float(row["total_score"]))
    if not scored:
        return []
    groups: dict[int, list[Mapping[str, Any]]] = {index: [] for index in range(1, 6)}
    for index, row in enumerate(scored):
        quantile = min(5, index * 5 // len(scored) + 1)
        groups[quantile].append(row)
    return [
        {"group": f"Q{key}", "events": len(value), "status_counts": _count_by(value, "status")}
        for key, value in groups.items()
    ]


def _pre_post_summary(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    labeled = [dict(row, period="post_fix" if str(row["score_date"]) >= "2026-05-15" else "pre_fix") for row in rows]
    return _group_summary(labeled, "period")


def _report_markdown(payload: Mapping[str, Any]) -> str:
    summary = payload["summary"]
    status_lines = "\n".join(f"- `{key}`: {value}" for key, value in summary["status_counts"].items())
    caveats = "\n".join(f"- {item}" for item in payload["caveats"])
    return (
        "# Historical outcome shadow Phase 1 report\n\n"
        f"Run: `{payload['run']['run_id']}`\n\n"
        f"- Events: {summary['events']}\n"
        f"- Comparable: {summary['comparable']}\n"
        f"- Unavailable: {summary['unavailable']}\n"
        f"- Aligned alpha rows: {summary['aligned_alpha']}\n"
        f"- Old vs stored-entry shadow changed: {summary['old_vs_stored_entry_shadow_changed']}\n"
        f"- Stored-entry vs reconstructed changed: {summary['stored_vs_reconstructed_changed']}\n"
        f"- Old vs TuShare benchmark changed: {summary['old_vs_shadow_benchmark_changed']}\n\n"
        f"## Status\n\n{status_lines}\n\n"
        f"## Interpretation limits\n\n{caveats}\n"
    )


def _validate_paths(source_db: Path | str, candidate_db: Path | str) -> tuple[Path, Path]:
    source = Path(source_db).expanduser().resolve()
    candidate = Path(candidate_db).expanduser().resolve()
    if not source.is_file():
        raise ShadowContractError("SOURCE_DB_NOT_FOUND")
    if source == candidate:
        raise ShadowContractError("SOURCE_AND_CANDIDATE_MUST_DIFFER")
    if candidate.exists():
        raise ShadowContractError("CANDIDATE_ALREADY_EXISTS")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return source, candidate


def _open_read_only(path: Path | str) -> sqlite3.Connection:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ShadowContractError("SOURCE_DB_NOT_FOUND")
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ShadowContractError(f"INVALID_DATE:{value}") from exc


def _load_frozen_events(
    conn: sqlite3.Connection,
    as_of_date: str,
    windows: Sequence[int] = WINDOW_DAYS,
) -> list[FrozenEvent]:
    events: list[FrozenEvent] = []
    for window in windows:
        if window not in WINDOW_DAYS:
            raise ShadowContractError(f"UNSUPPORTED_WINDOW:{window}")
        rows = conn.execute(
            f"""SELECT {",".join(EVENT_COLUMNS)}, outcome_{window}d, benchmark_{window}d
                FROM predictions
                WHERE date(score_date, '+{window} days') <= ?
                ORDER BY id""",
            (as_of_date,),
        ).fetchall()
        events.extend(_event_from_row(row, window) for row in rows)
    return sorted(events, key=lambda event: (event.prediction_id, event.window_days))


def _event_from_row(row: sqlite3.Row, window: int) -> FrozenEvent:
    price = row["price_at_score"]
    if price is None or float(price) <= 0:
        raise ShadowContractError(f"INVALID_PRICE_AT_SCORE:{row['id']}")
    return FrozenEvent(
        prediction_id=int(row["id"]),
        code=str(row["code"]),
        name=row["name"],
        framework=str(row["framework"]),
        score_date=str(row["score_date"]),
        price_at_score=float(price),
        quant_score=_optional_float(row["quant_score"]),
        total_score=_optional_float(row["total_score"]),
        weights_hash=row["weights_hash"],
        report_period=row["report_period"],
        created_at=row["created_at"],
        window_days=window,
        old_outcome=_optional_float(row[f"outcome_{window}d"]),
        old_benchmark=_optional_float(row[f"benchmark_{window}d"]),
    )


def _predictions_protection(conn: sqlite3.Connection) -> tuple[int, str]:
    rows = conn.execute(f"SELECT {','.join(PROTECTION_COLUMNS)} FROM predictions ORDER BY id").fetchall()
    payload = [tuple(row[column] for column in PROTECTION_COLUMNS) for row in rows]
    return len(rows), _stable_hash(payload)


def extended_predictions_protection(conn: sqlite3.Connection) -> tuple[int, str]:
    """Protection hash that also pins every outcome/benchmark column.

    Used by callers whose run is about the outcome values themselves; the ordinary
    `_predictions_protection` would not notice those columns changing.
    """
    columns = ",".join(EXTENDED_PROTECTION_COLUMNS)
    rows = conn.execute(f"SELECT {columns} FROM predictions ORDER BY id").fetchall()
    payload = [tuple(row[column] for column in EXTENDED_PROTECTION_COLUMNS) for row in rows]
    return len(rows), _stable_hash(payload)


def _events_hash(events: Sequence[FrozenEvent]) -> str:
    return _stable_hash([_event_identity_payload(event) for event in events])


def _legacy_outcomes_hash(events: Sequence[FrozenEvent]) -> str:
    return _stable_hash(
        [(event.prediction_id, event.window_days, event.old_outcome, event.old_benchmark) for event in events]
    )


def _event_identity_payload(event: FrozenEvent) -> tuple[Any, ...]:
    return (
        event.prediction_id,
        event.code,
        event.framework,
        event.score_date,
        event.price_at_score,
        event.quant_score,
        event.total_score,
        event.weights_hash,
        event.report_period,
        event.created_at,
        event.window_days,
    )


def _load_stock_observations(
    conn: sqlite3.Connection,
    events: Sequence[FrozenEvent],
    as_of_date: str,
    algorithm: ShadowAlgorithm = RAW_PRICE_RETURN_V1,
) -> list[Observation]:
    if not events:
        return []
    codes = sorted({event.code for event in events})
    earliest = min(_parse_date(event.score_date) for event in events) - timedelta(days=10)
    placeholders = ",".join("?" for _ in codes)
    params: tuple[Any, ...] = (*codes, earliest.isoformat(), as_of_date)
    # Both predicates are required: production qfq rows and the qfq_tushare_shadow
    # research rows share the same `source`, so filtering on source alone would let
    # 4,620 shadow rows in.
    impurity = conn.execute(
        f"""SELECT COUNT(*) FROM daily_bars
            WHERE code IN ({placeholders}) AND trade_date BETWEEN ? AND ?
              AND adjusted=? AND source<>?""",
        (*params, algorithm.stock_adjusted, algorithm.stock_source),
    ).fetchone()[0]
    if impurity:
        raise ShadowContractError(f"SOURCE_IMPURE:{impurity}")
    rows = conn.execute(
        f"""SELECT code,trade_date,close,source,adjusted,fetched_at
            FROM daily_bars
            WHERE code IN ({placeholders}) AND trade_date BETWEEN ? AND ?
              AND adjusted=? AND source=?
            ORDER BY code,trade_date""",
        (*params, algorithm.stock_adjusted, algorithm.stock_source),
    ).fetchall()
    return [
        Observation("stock", str(row[0]), str(row[1]), float(row[2]), str(row[3]), str(row[4]), str(row[5]))
        for row in rows
    ]


def _load_benchmark_snapshot(
    snapshot_path: Path | str,
    events: Sequence[FrozenEvent],
    as_of_date: str,
    algorithm: ShadowAlgorithm = RAW_PRICE_RETURN_V1,
) -> list[Observation]:
    path = Path(snapshot_path).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowContractError("BENCHMARK_SNAPSHOT_INVALID") from exc
    if payload.get("schema_version") != 1:
        raise ShadowContractError("BENCHMARK_SCHEMA_VERSION_INVALID")
    if payload.get("source") != BENCHMARK_SOURCE or payload.get("symbol") != algorithm.benchmark_symbol:
        raise ShadowContractError("BENCHMARK_SOURCE_INVALID")
    fetched_at = str(payload.get("fetched_at") or "")
    if not fetched_at:
        raise ShadowContractError("BENCHMARK_FETCHED_AT_MISSING")
    try:
        datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ShadowContractError("BENCHMARK_FETCHED_AT_INVALID") from exc
    earliest = min((_parse_date(event.score_date) for event in events), default=_parse_date(as_of_date)) - timedelta(
        days=10
    )
    all_rows = _normalize_benchmark_rows(payload.get("rows"), fetched_at, algorithm)
    if len({row.trade_date for row in all_rows}) != len(all_rows):
        raise ShadowContractError("BENCHMARK_DUPLICATE_DATE")
    rows = [row for row in all_rows if earliest.isoformat() <= row.trade_date <= as_of_date]
    if not rows:
        raise ShadowContractError("BENCHMARK_COVERAGE_EMPTY")
    return rows


def _normalize_benchmark_rows(
    raw_rows: Any,
    fetched_at: str,
    algorithm: ShadowAlgorithm = RAW_PRICE_RETURN_V1,
) -> list[Observation]:
    if not isinstance(raw_rows, list):
        raise ShadowContractError("BENCHMARK_ROWS_INVALID")
    observations: list[Observation] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise ShadowContractError("BENCHMARK_ROW_INVALID")
        trade_date = str(raw.get("trade_date") or "")
        try:
            normalized_date = _parse_date(trade_date).isoformat()
        except ShadowContractError as exc:
            raise ShadowContractError("BENCHMARK_DATE_INVALID") from exc
        close = raw.get("close")
        if isinstance(close, bool) or not isinstance(close, (int, float)):
            raise ShadowContractError("BENCHMARK_CLOSE_INVALID")
        normalized_close = float(close)
        if not math.isfinite(normalized_close) or normalized_close <= 0:
            raise ShadowContractError("BENCHMARK_CLOSE_INVALID")
        observations.append(
            Observation(
                "benchmark",
                algorithm.benchmark_symbol,
                normalized_date,
                normalized_close,
                BENCHMARK_SOURCE,
                RAW_ADJUSTED,
                fetched_at,
            )
        )
    return sorted(observations, key=lambda item: item.trade_date)


def _build_manifest(
    events: Sequence[FrozenEvent],
    stock: Sequence[Observation],
    benchmark: Sequence[Observation],
    as_of_date: str,
    protection_hash: str,
    algorithm: ShadowAlgorithm = RAW_PRICE_RETURN_V1,
) -> dict[str, Any]:
    base = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": algorithm.version,
        "as_of_date": as_of_date,
        "cohort_hash": _events_hash(events),
        "legacy_outcomes_hash": _legacy_outcomes_hash(events),
        "stock_input_hash": _observations_hash(stock),
        "benchmark_input_hash": _observations_hash(benchmark),
        "predictions_protection_hash": protection_hash,
        "event_count": len(events),
    }
    manifest_hash = _stable_hash(base)
    return {**base, "manifest_hash": manifest_hash, "run_id": f"outcome-shadow-{manifest_hash[:24]}"}


def _observations_hash(rows: Sequence[Observation]) -> str:
    return _stable_hash([row.observation_hash for row in rows])


def _build_shadow_rows(
    events: Sequence[FrozenEvent],
    stock_observations: Sequence[Observation],
    benchmark_observations: Sequence[Observation],
    algorithm: ShadowAlgorithm = RAW_PRICE_RETURN_V1,
) -> list[ShadowRow]:
    stock_maps: dict[str, dict[str, float]] = {}
    for observation in stock_observations:
        stock_maps.setdefault(observation.instrument_code, {})[observation.trade_date] = observation.close
    benchmark_map = {observation.trade_date: observation.close for observation in benchmark_observations}
    return [_build_shadow_row(event, stock_maps.get(event.code, {}), benchmark_map, algorithm) for event in events]


def _build_shadow_row(
    event: FrozenEvent,
    stock: Mapping[str, float],
    benchmark: Mapping[str, float],
    algorithm: ShadowAlgorithm = RAW_PRICE_RETURN_V1,
) -> ShadowRow:
    target_date = (_parse_date(event.score_date) + timedelta(days=event.window_days)).isoformat()
    entry = resolve_observation(stock, event.score_date)
    target = resolve_observation(stock, target_date)
    benchmark_entry = resolve_observation(benchmark, event.score_date)
    benchmark_target = resolve_observation(benchmark, target_date)
    status = _result_status(entry, target, benchmark_entry, benchmark_target)
    # Left NULL when the entry price scale differs from the target's: pairing a raw
    # price_at_score against a qfq target yields a number with no interpretation.
    stored_outcome = _return_pct(event.price_at_score, target[1]) if algorithm.compute_stored_entry and target else None
    reconstructed_outcome = _return_pct(entry[1], target[1]) if entry and target else None
    benchmark_return = (
        _return_pct(benchmark_entry[1], benchmark_target[1]) if benchmark_entry and benchmark_target else None
    )
    aligned = int(
        status.startswith("computed_")
        and entry is not None
        and target is not None
        and benchmark_entry is not None
        and benchmark_target is not None
        and entry[0] == benchmark_entry[0]
        and target[0] == benchmark_target[0]
    )
    if status.startswith("computed_") and not aligned:
        status = "computed_calendar_mismatch"
    values = _result_values(
        event,
        target_date,
        entry,
        target,
        benchmark_entry,
        benchmark_target,
        stored_outcome,
        reconstructed_outcome,
        benchmark_return,
        status,
        aligned,
        algorithm,
    )
    return ShadowRow(values=values, row_hash=_stable_hash(values), status=status)


def _result_status(
    entry: tuple[str, float, int] | None,
    target: tuple[str, float, int] | None,
    benchmark_entry: tuple[str, float, int] | None,
    benchmark_target: tuple[str, float, int] | None,
) -> str:
    if entry is None:
        return "missing_stock_entry"
    if target is None:
        return "missing_stock_target"
    if benchmark_entry is None:
        return "missing_benchmark_entry"
    if benchmark_target is None:
        return "missing_benchmark_target"
    return "computed_aligned"


def _result_values(
    event: FrozenEvent,
    target_date: str,
    entry: tuple[str, float, int] | None,
    target: tuple[str, float, int] | None,
    benchmark_entry: tuple[str, float, int] | None,
    benchmark_target: tuple[str, float, int] | None,
    stored_outcome: float | None,
    reconstructed_outcome: float | None,
    benchmark_return: float | None,
    status: str,
    aligned: int,
    algorithm: ShadowAlgorithm = RAW_PRICE_RETURN_V1,
) -> tuple[Any, ...]:
    return (
        *_event_result_values(event),
        *_resolved_observation_values(event.score_date, target_date, entry, target, benchmark_entry, benchmark_target),
        stored_outcome,
        reconstructed_outcome,
        benchmark_return,
        _difference(stored_outcome, benchmark_return),
        _difference(reconstructed_outcome, benchmark_return),
        "ex_post_reconstructed",
        algorithm.stock_source,
        BENCHMARK_SOURCE,
        algorithm.stock_adjusted,
        status,
        status.upper(),
        aligned,
    )


def _event_result_values(event: FrozenEvent) -> tuple[Any, ...]:
    return (
        event.prediction_id,
        event.window_days,
        event.code,
        event.name,
        event.framework,
        event.score_date,
        event.price_at_score,
        event.quant_score,
        event.total_score,
        event.weights_hash,
        event.report_period,
        event.created_at,
        event.old_outcome,
        event.old_benchmark,
        _difference(event.old_outcome, event.old_benchmark),
    )


def _resolved_observation_values(
    score_date: str,
    target_date: str,
    entry: tuple[str, float, int] | None,
    target: tuple[str, float, int] | None,
    benchmark_entry: tuple[str, float, int] | None,
    benchmark_target: tuple[str, float, int] | None,
) -> tuple[Any, ...]:
    return (
        score_date,
        _item(entry, 0),
        _item(entry, 1),
        _item(entry, 2),
        target_date,
        _item(target, 0),
        _item(target, 1),
        _item(target, 2),
        score_date,
        _item(benchmark_entry, 0),
        _item(benchmark_entry, 1),
        _item(benchmark_entry, 2),
        target_date,
        _item(benchmark_target, 0),
        _item(benchmark_target, 1),
        _item(benchmark_target, 2),
    )


def _item(value: tuple[str, float, int] | None, index: int) -> Any:
    return value[index] if value is not None else None


def _return_pct(start: float, end: float) -> float:
    return (float(end) / float(start) - 1.0) * 100.0


def _difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _copy_source_database(source: Path, candidate: Path) -> None:
    with _open_read_only(source) as source_conn, sqlite3.connect(candidate) as candidate_conn:
        source_conn.backup(candidate_conn)


def _write_candidate(
    candidate: Path,
    manifest: Mapping[str, Any],
    prediction_count: int,
    protection_hash: str,
    stock: Sequence[Observation],
    benchmark: Sequence[Observation],
    rows: Sequence[ShadowRow],
) -> None:
    conn = sqlite3.connect(candidate)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        _create_schema(conn)
        _insert_run(conn, manifest, prediction_count, protection_hash, rows)
        _insert_observations(conn, str(manifest["run_id"]), (*stock, *benchmark))
        _insert_results(conn, str(manifest["run_id"]), rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _create_schema(conn: sqlite3.Connection) -> None:
    # The candidate is a full copy of the source database, and since the Phase 2 import
    # production carries these three tables with a previous run inside them. A candidate
    # must contain only its own run, so drop the inherited copies before recreating.
    # Dropping a table also drops its triggers. This only ever touches the isolated
    # candidate: _validate_paths rejects candidate == source and refuses to overwrite an
    # existing file.
    for table in ("outcome_shadow_results", "outcome_shadow_observations", "outcome_shadow_runs"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(_RUNS_DDL)
    conn.execute(_OBSERVATIONS_DDL)
    conn.execute(_RESULTS_DDL)
    for statement in _IMMUTABILITY_DDL:
        conn.execute(statement)


def _insert_observations(conn: sqlite3.Connection, run_id: str, rows: Iterable[Observation]) -> None:
    conn.executemany(
        """INSERT INTO outcome_shadow_observations
           (run_id,instrument_type,instrument_code,trade_date,close,source,adjusted,fetched_at,observation_hash)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        [
            (
                run_id,
                row.instrument_type,
                row.instrument_code,
                row.trade_date,
                row.close,
                row.source,
                row.adjusted,
                row.fetched_at,
                row.observation_hash,
            )
            for row in rows
        ],
    )


def _insert_results(conn: sqlite3.Connection, run_id: str, rows: Sequence[ShadowRow]) -> None:
    placeholders = ",".join("?" for _ in range(len(_RESULT_COLUMNS) + 2))
    conn.executemany(
        f"INSERT INTO outcome_shadow_results ({','.join(('run_id', *_RESULT_COLUMNS, 'row_hash'))}) VALUES ({placeholders})",
        [(run_id, *row.values, row.row_hash) for row in rows],
    )


def _insert_run(
    conn: sqlite3.Connection,
    manifest: Mapping[str, Any],
    prediction_count: int,
    protection_hash: str,
    rows: Sequence[ShadowRow],
) -> None:
    computed = sum(row.status.startswith("computed_") for row in rows)
    mismatch = sum(row.status == "computed_calendar_mismatch" for row in rows)
    conn.execute(
        """INSERT INTO outcome_shadow_runs
           (run_id,schema_version,algorithm_version,as_of_date,cohort_hash,legacy_outcomes_hash,stock_input_hash,
            benchmark_input_hash,manifest_hash,predictions_protection_hash,predictions_count,
            event_count,computed_count,missing_count,calendar_mismatch_count,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            manifest["run_id"],
            manifest["schema_version"],
            manifest["algorithm_version"],
            manifest["as_of_date"],
            manifest["cohort_hash"],
            manifest["legacy_outcomes_hash"],
            manifest["stock_input_hash"],
            manifest["benchmark_input_hash"],
            manifest["manifest_hash"],
            protection_hash,
            prediction_count,
            len(rows),
            computed,
            len(rows) - computed,
            mismatch,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _verify_candidate(
    candidate: Path,
    prediction_count: int,
    protection_hash: str,
    event_count: int,
    algorithm: ShadowAlgorithm = RAW_PRICE_RETURN_V1,
) -> None:
    with sqlite3.connect(f"file:{candidate.resolve()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ShadowContractError("CANDIDATE_QUICK_CHECK_FAILED")
        # Must use the same protection variant as the build, or the two hashes are
        # computed over different column sets and can never match.
        protection = extended_predictions_protection if algorithm.use_extended_protection else _predictions_protection
        actual_count, actual_hash = protection(conn)
        result_count = conn.execute("SELECT COUNT(*) FROM outcome_shadow_results").fetchone()[0]
    if (actual_count, actual_hash) != (prediction_count, protection_hash):
        raise ShadowContractError("PREDICTIONS_PROTECTION_DRIFT")
    if result_count != event_count:
        raise ShadowContractError("RESULT_COUNT_MISMATCH")


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_RUNS_DDL = """CREATE TABLE outcome_shadow_runs (
    run_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    algorithm_version TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    cohort_hash TEXT NOT NULL,
    legacy_outcomes_hash TEXT NOT NULL,
    stock_input_hash TEXT NOT NULL,
    benchmark_input_hash TEXT NOT NULL,
    manifest_hash TEXT NOT NULL UNIQUE,
    predictions_protection_hash TEXT NOT NULL,
    predictions_count INTEGER NOT NULL,
    event_count INTEGER NOT NULL,
    computed_count INTEGER NOT NULL,
    missing_count INTEGER NOT NULL,
    calendar_mismatch_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
)"""

_OBSERVATIONS_DDL = """CREATE TABLE outcome_shadow_observations (
    run_id TEXT NOT NULL REFERENCES outcome_shadow_runs(run_id),
    instrument_type TEXT NOT NULL CHECK(instrument_type IN ('stock','benchmark')),
    instrument_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    close REAL NOT NULL CHECK(close > 0),
    source TEXT NOT NULL,
    adjusted TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    observation_hash TEXT NOT NULL,
    PRIMARY KEY(run_id,instrument_type,instrument_code,trade_date,source,adjusted)
)"""

_RESULT_COLUMNS = (
    "prediction_id",
    "window_days",
    "code",
    "name",
    "framework",
    "score_date",
    "stored_entry_price",
    "quant_score",
    "total_score",
    "weights_hash",
    "report_period",
    "prediction_created_at",
    "old_outcome",
    "old_benchmark",
    "old_alpha",
    "entry_anchor_date",
    "entry_actual_trade_date",
    "reconstructed_entry_price",
    "entry_lag_days",
    "target_anchor_date",
    "target_actual_trade_date",
    "target_price",
    "target_lag_days",
    "benchmark_entry_anchor_date",
    "benchmark_entry_actual_trade_date",
    "benchmark_entry_price",
    "benchmark_entry_lag_days",
    "benchmark_target_anchor_date",
    "benchmark_target_actual_trade_date",
    "benchmark_target_price",
    "benchmark_target_lag_days",
    "stored_entry_shadow_outcome",
    "reconstructed_shadow_outcome",
    "shadow_benchmark",
    "stored_entry_shadow_alpha",
    "reconstructed_shadow_alpha",
    "entry_reconstruction",
    "stock_source",
    "benchmark_source",
    "adjusted",
    "status",
    "reason_code",
    "aligned_alpha_eligible",
)

_RESULTS_DDL = """CREATE TABLE outcome_shadow_results (
    run_id TEXT NOT NULL REFERENCES outcome_shadow_runs(run_id),
    prediction_id INTEGER NOT NULL,
    window_days INTEGER NOT NULL CHECK(window_days IN (30,60,90)),
    code TEXT NOT NULL,
    name TEXT,
    framework TEXT NOT NULL,
    score_date TEXT NOT NULL,
    stored_entry_price REAL NOT NULL,
    quant_score REAL,
    total_score REAL,
    weights_hash TEXT,
    report_period TEXT,
    prediction_created_at TEXT,
    old_outcome REAL,
    old_benchmark REAL,
    old_alpha REAL,
    entry_anchor_date TEXT NOT NULL,
    entry_actual_trade_date TEXT,
    reconstructed_entry_price REAL,
    entry_lag_days INTEGER,
    target_anchor_date TEXT NOT NULL,
    target_actual_trade_date TEXT,
    target_price REAL,
    target_lag_days INTEGER,
    benchmark_entry_anchor_date TEXT NOT NULL,
    benchmark_entry_actual_trade_date TEXT,
    benchmark_entry_price REAL,
    benchmark_entry_lag_days INTEGER,
    benchmark_target_anchor_date TEXT NOT NULL,
    benchmark_target_actual_trade_date TEXT,
    benchmark_target_price REAL,
    benchmark_target_lag_days INTEGER,
    stored_entry_shadow_outcome REAL,
    reconstructed_shadow_outcome REAL,
    shadow_benchmark REAL,
    stored_entry_shadow_alpha REAL,
    reconstructed_shadow_alpha REAL,
    entry_reconstruction TEXT NOT NULL,
    stock_source TEXT NOT NULL,
    benchmark_source TEXT NOT NULL,
    adjusted TEXT NOT NULL,
    status TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    aligned_alpha_eligible INTEGER NOT NULL CHECK(aligned_alpha_eligible IN (0,1)),
    row_hash TEXT NOT NULL,
    PRIMARY KEY(run_id,prediction_id,window_days)
)"""

_IMMUTABILITY_DDL = tuple(
    f"""CREATE TRIGGER {table}_{action.lower()}_immutable
        BEFORE {action} ON {table}
        BEGIN SELECT RAISE(ABORT, 'OUTCOME_SHADOW_IMMUTABLE'); END"""
    for table in ("outcome_shadow_runs", "outcome_shadow_observations", "outcome_shadow_results")
    for action in ("UPDATE", "DELETE")
)
