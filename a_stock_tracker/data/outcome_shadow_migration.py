"""Fail-closed additive migration for versioned outcome-shadow runs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Sequence

from a_stock_tracker.data.outcome_shadow import (
    _IMMUTABILITY_DDL,
    _OBSERVATIONS_DDL,
    _RESULT_COLUMNS,
    _RESULTS_DDL,
    _RUNS_DDL,
    BENCHMARK_SOURCE,
    QFQ_TOTAL_RETURN_V1,
    RAW_PRICE_RETURN_V1,
    ShadowAlgorithm,
    _predictions_protection,
    _stable_hash,
    extended_predictions_protection,
)

SHADOW_TABLES = (
    "outcome_shadow_runs",
    "outcome_shadow_observations",
    "outcome_shadow_results",
)
SHADOW_OBJECT_COUNT = 9
BUSY_TIMEOUT_MS = 100
# A run stores the protection hash of whichever variant its algorithm uses, so validating
# it requires resolving that same variant. Comparing an extended hash against the ordinary
# one reports a spurious PREDICTIONS_PROTECTION_DRIFT.
KNOWN_ALGORITHMS = {algorithm.version: algorithm for algorithm in (RAW_PRICE_RETURN_V1, QFQ_TOTAL_RETURN_V1)}


class MigrationContractError(RuntimeError):
    """Raised when a production migration contract fails closed."""


@dataclass(frozen=True)
class MigrationResult:
    """Machine-readable outcome from inspect, import, or revert."""

    status: str
    run_id: str
    manifest_hash: str
    result_count: int
    observation_count: int
    production_db: str
    backup_db: str | None = None


class MigrationEvidenceError(MigrationContractError):
    """Report that DB state changed but evidence finalization failed."""

    def __init__(
        self,
        result: MigrationResult,
        evidence_path: Path,
        *,
        write_performed: bool,
    ) -> None:
        self.result = result
        self.evidence_path = evidence_path
        self.write_performed = write_performed
        super().__init__(
            "EVIDENCE_WRITE_FAILED:"
            f"write_performed={str(write_performed).lower()}:"
            f"status={result.status}:run={result.run_id}:path={evidence_path}"
        )


@dataclass(frozen=True)
class _Payload:
    run_columns: tuple[str, ...]
    run_row: tuple[Any, ...]
    observation_columns: tuple[str, ...]
    observations: tuple[tuple[Any, ...], ...]
    result_columns: tuple[str, ...]
    results: tuple[tuple[Any, ...], ...]
    result_hashes: tuple[str, ...]
    observation_hashes: tuple[str, ...]
    protection_hash: str
    predictions_count: int


@dataclass(frozen=True)
class _LogicalSnapshot:
    quick_check: str
    user_version: int
    page_size: int
    encoding: str
    schema_hash: str
    table_hashes: tuple[tuple[str, int, str], ...]
    sequence_hash: str
    predictions_count: int
    predictions_protection_hash: str
    journal_mode: str


def inspect_shadow_import(
    production_db: str | Path,
    candidate_db: str | Path,
    expected_run: str,
    expected_manifest: str,
) -> MigrationResult:
    """Validate candidate and inspect production without writing either database."""
    production, candidate = _validate_paths(production_db, candidate_db)
    payload = _load_payload(candidate, expected_run, expected_manifest)
    status = _production_state(production, payload, expected_run, expected_manifest)
    if status in {"partial_present", "already_exists_diverged"}:
        raise MigrationContractError(status.upper())
    return _result(status, production, payload, expected_run, expected_manifest)


def apply_shadow_import(
    production_db: str | Path,
    candidate_db: str | Path,
    expected_run: str,
    expected_manifest: str,
    backup_db: str | Path,
    evidence_json: str | Path,
) -> MigrationResult:
    """Back up production and atomically import one validated shadow run."""
    production, candidate = _validate_paths(production_db, candidate_db)
    backup = Path(backup_db).expanduser().resolve()
    evidence = _validate_evidence_path(evidence_json, create_parent=True)
    payload = _load_payload(candidate, expected_run, expected_manifest)
    state = _production_state(production, payload, expected_run, expected_manifest)
    if state == "already_present":
        result = _result(state, production, payload, expected_run, expected_manifest)
        _write_evidence_or_raise(evidence, result, {"write_performed": False}, write_performed=False)
        return result
    if state != "absent":
        raise MigrationContractError(state.upper())
    backup_snapshot = _create_verified_backup(production, backup)
    _apply_transaction(production, payload, expected_run, expected_manifest, backup_snapshot)
    after = _logical_snapshot(production)
    _assert_non_shadow_equal(backup_snapshot, after)
    result = _result("imported", production, payload, expected_run, expected_manifest, str(backup))
    _write_evidence_or_raise(
        evidence,
        result,
        {
            "write_performed": True,
            "backup_sha256": _file_sha256(backup),
            "backup_snapshot": asdict(backup_snapshot),
            "non_shadow_before": asdict(backup_snapshot),
            "non_shadow_after": asdict(after),
        },
        write_performed=True,
    )
    return result


def revert_shadow_import(
    production_db: str | Path,
    expected_run: str,
    expected_manifest: str,
    evidence_json: str | Path | None,
    *,
    apply: bool,
) -> MigrationResult:
    """Inspect or transactionally remove exactly one matching shadow run."""
    production = Path(production_db).expanduser().resolve()
    payload = _load_payload(production, expected_run, expected_manifest)
    if not apply:
        return _result("revert_ready", production, payload, expected_run, expected_manifest)
    if evidence_json is None:
        raise MigrationContractError("EVIDENCE_OUTPUT_REQUIRED")
    evidence = _validate_evidence_path(evidence_json, create_parent=True)
    before = _logical_snapshot(production)
    _revert_transaction(production, expected_run, expected_manifest, before)
    after = _logical_snapshot(production)
    _assert_non_shadow_equal(before, after)
    result = _result("reverted", production, payload, expected_run, expected_manifest)
    _write_evidence_or_raise(
        evidence,
        result,
        {"non_shadow_before": asdict(before), "non_shadow_after": asdict(after)},
        write_performed=True,
    )
    return result


def _algorithm_for_run(algorithm_version: str) -> ShadowAlgorithm:
    """Resolve the algorithm contract a run was produced under.

    Rejecting unknown versions is deliberate: importing a run whose source and protection
    semantics this module does not understand would silently validate it against the
    wrong column sets and the wrong expected sources.
    """
    algorithm = KNOWN_ALGORITHMS.get(str(algorithm_version))
    if algorithm is None:
        raise MigrationContractError(f"UNKNOWN_ALGORITHM_VERSION:{algorithm_version}")
    return algorithm


def _protection_for_run(algorithm_version: str) -> Callable[[sqlite3.Connection], tuple[int, str]]:
    algorithm = _algorithm_for_run(algorithm_version)
    return extended_predictions_protection if algorithm.use_extended_protection else _predictions_protection


def _validate_paths(production_db: str | Path, candidate_db: str | Path) -> tuple[Path, Path]:
    production = Path(production_db).expanduser().resolve()
    candidate = Path(candidate_db).expanduser().resolve()
    if production == candidate:
        raise MigrationContractError("PRODUCTION_CANDIDATE_PATH_CONFLICT")
    if not production.is_file():
        raise MigrationContractError("PRODUCTION_DB_MISSING")
    if not candidate.is_file():
        raise MigrationContractError("CANDIDATE_DB_MISSING")
    return production, candidate


def _validate_evidence_path(path: str | Path, *, create_parent: bool) -> Path:
    resolved = Path(path).expanduser().resolve()
    if resolved.suffix != ".json":
        raise MigrationContractError("EVIDENCE_OUTPUT_MUST_BE_JSON")
    if resolved.exists():
        raise MigrationContractError("EVIDENCE_OUTPUT_EXISTS")
    if create_parent:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _open_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _load_payload(path: Path, expected_run: str, expected_manifest: str) -> _Payload:
    before_hash = _file_sha256(path)
    with _open_read_only(path) as conn:
        _validate_shadow_schema(conn)
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise MigrationContractError("SHADOW_DB_QUICK_CHECK_FAILED")
        run_rows = conn.execute("SELECT * FROM outcome_shadow_runs").fetchall()
        if len(run_rows) != 1:
            raise MigrationContractError("SHADOW_RUN_COUNT_INVALID")
        run = run_rows[0]
        if run["run_id"] != expected_run:
            raise MigrationContractError("CANDIDATE_RUN_MISMATCH")
        if run["manifest_hash"] != expected_manifest:
            raise MigrationContractError("CANDIDATE_MANIFEST_MISMATCH")
        count, protection_hash = _protection_for_run(run["algorithm_version"])(conn)
        if count != run["predictions_count"] or protection_hash != run["predictions_protection_hash"]:
            raise MigrationContractError("PREDICTIONS_PROTECTION_DRIFT")
        observations = conn.execute(
            "SELECT * FROM outcome_shadow_observations ORDER BY instrument_type,instrument_code,trade_date"
        ).fetchall()
        results = conn.execute("SELECT * FROM outcome_shadow_results ORDER BY prediction_id,window_days").fetchall()
        _validate_rows(run, observations, results)
        # Expected sources are algorithm-specific; qfq_total_return_v1 legitimately carries
        # tushare.pro_bar.qfq / qfq rather than the raw-price pair.
        algorithm = _algorithm_for_run(run["algorithm_version"])
        if conn.execute(
            """SELECT COUNT(*) FROM outcome_shadow_results
               WHERE stock_source!=? OR benchmark_source!=? OR adjusted!=?""",
            (algorithm.stock_source, BENCHMARK_SOURCE, algorithm.stock_adjusted),
        ).fetchone()[0]:
            raise MigrationContractError("SOURCE_IMPURE")
        payload = _payload_from_rows(conn, run, observations, results, protection_hash, count)
    if _file_sha256(path) != before_hash:
        raise MigrationContractError("SHADOW_DB_CHANGED_DURING_READ")
    return payload


def _payload_from_rows(
    conn: sqlite3.Connection,
    run: sqlite3.Row,
    observations: Sequence[sqlite3.Row],
    results: Sequence[sqlite3.Row],
    protection_hash: str,
    count: int,
) -> _Payload:
    run_columns = _table_columns(conn, "outcome_shadow_runs")
    observation_columns = _table_columns(conn, "outcome_shadow_observations")
    result_columns = _table_columns(conn, "outcome_shadow_results")
    return _Payload(
        run_columns,
        tuple(run[column] for column in run_columns),
        observation_columns,
        tuple(tuple(row[column] for column in observation_columns) for row in observations),
        result_columns,
        tuple(tuple(row[column] for column in result_columns) for row in results),
        tuple(str(row["row_hash"]) for row in results),
        tuple(str(row["observation_hash"]) for row in observations),
        protection_hash,
        count,
    )


def _validate_rows(
    run: sqlite3.Row,
    observations: Sequence[sqlite3.Row],
    results: Sequence[sqlite3.Row],
) -> None:
    if len(results) != run["event_count"]:
        raise MigrationContractError("RESULT_COUNT_MISMATCH")
    computed = sum(str(row["status"]).startswith("computed_") for row in results)
    if computed != run["computed_count"] or len(results) - computed != run["missing_count"]:
        raise MigrationContractError("STATUS_COUNT_MISMATCH")
    for row in results:
        values = tuple(row[column] for column in _RESULT_COLUMNS)
        if _stable_hash(values) != row["row_hash"]:
            raise MigrationContractError("RESULT_HASH_MISMATCH")
    observation_fields = (
        "instrument_type",
        "instrument_code",
        "trade_date",
        "close",
        "source",
        "adjusted",
        "fetched_at",
    )
    for row in observations:
        values = tuple(row[column] for column in observation_fields)
        if _stable_hash(values) != row["observation_hash"]:
            raise MigrationContractError("OBSERVATION_HASH_MISMATCH")


def _production_state(
    production: Path,
    candidate_payload: _Payload,
    expected_run: str,
    expected_manifest: str,
) -> str:
    with _open_read_only(production) as conn:
        names = _shadow_object_names(conn)
    if not names:
        return "absent"
    expected_names = set(_expected_schema())
    if names != expected_names:
        return "partial_present"
    try:
        existing = _load_payload(production, expected_run, expected_manifest)
    except MigrationContractError:
        return "already_exists_diverged"
    return (
        "already_present"
        if _payload_signature(existing) == _payload_signature(candidate_payload)
        else "already_exists_diverged"
    )


def _apply_transaction(
    production: Path,
    payload: _Payload,
    expected_run: str,
    expected_manifest: str,
    before: _LogicalSnapshot,
) -> None:
    conn = sqlite3.connect(production, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise MigrationContractError("PRODUCTION_LOCKED") from exc
        if _shadow_object_names(conn):
            raise MigrationContractError("PRODUCTION_STATE_CHANGED")
        current = _logical_snapshot_from_connection(conn)
        try:
            _assert_backup_equal(before, current)
        except MigrationContractError as exc:
            raise MigrationContractError("PRODUCTION_CHANGED_AFTER_BACKUP") from exc
        _create_schema_in_order(conn)
        _insert_rows(conn, "outcome_shadow_runs", payload.run_columns, (payload.run_row,))
        _insert_rows(conn, "outcome_shadow_observations", payload.observation_columns, payload.observations)
        _insert_rows(conn, "outcome_shadow_results", payload.result_columns, payload.results)
        _validate_connection_matches(conn, payload, expected_run, expected_manifest)
        current = _logical_snapshot_from_connection(conn)
        _assert_non_shadow_equal(before, current)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _revert_transaction(
    production: Path,
    expected_run: str,
    expected_manifest: str,
    before: _LogicalSnapshot,
) -> None:
    conn = sqlite3.connect(production, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise MigrationContractError("PRODUCTION_LOCKED") from exc
        run = conn.execute("SELECT run_id,manifest_hash FROM outcome_shadow_runs").fetchall()
        if len(run) != 1 or tuple(run[0]) != (expected_run, expected_manifest):
            raise MigrationContractError("REVERT_IDENTITY_MISMATCH")
        for table in reversed(SHADOW_TABLES):
            conn.execute(f"DROP TABLE {table}")
        if _shadow_object_names(conn):
            raise MigrationContractError("REVERT_SHADOW_OBJECTS_REMAIN")
        _assert_non_shadow_equal(before, _logical_snapshot_from_connection(conn))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _create_verified_backup(source: Path, backup: Path) -> _LogicalSnapshot:
    if backup.exists():
        raise MigrationContractError("BACKUP_PATH_EXISTS")
    backup.parent.mkdir(parents=True, exist_ok=True)
    source_snapshot = _logical_snapshot(source)
    try:
        with _open_read_only(source) as source_conn, sqlite3.connect(backup) as backup_conn:
            source_conn.backup(backup_conn)
        backup_snapshot = _logical_snapshot(backup)
        _assert_backup_equal(source_snapshot, backup_snapshot)
    except Exception:
        backup.unlink(missing_ok=True)
        raise
    return backup_snapshot


def _logical_snapshot(path: Path) -> _LogicalSnapshot:
    with _open_read_only(path) as conn:
        return _logical_snapshot_from_connection(conn)


def _logical_snapshot_from_connection(conn: sqlite3.Connection) -> _LogicalSnapshot:
    quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    schema_rows = conn.execute(
        """SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_master
           WHERE name NOT LIKE 'outcome_shadow_%'
             AND tbl_name NOT LIKE 'outcome_shadow_%'
           ORDER BY type,name"""
    ).fetchall()
    tables = [
        row[0]
        for row in conn.execute(
            """SELECT name FROM sqlite_master WHERE type='table'
               AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'outcome_shadow_%'
               ORDER BY name"""
        )
    ]
    table_hashes = tuple(_table_hash(conn, table) for table in tables)
    sequence = []
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
        sequence = conn.execute("SELECT name,seq FROM sqlite_sequence ORDER BY name").fetchall()
    prediction_count, protection_hash = _predictions_protection(conn)
    return _LogicalSnapshot(
        quick,
        int(conn.execute("PRAGMA user_version").fetchone()[0]),
        int(conn.execute("PRAGMA page_size").fetchone()[0]),
        str(conn.execute("PRAGMA encoding").fetchone()[0]),
        _stable_hash([tuple(row) for row in schema_rows]),
        table_hashes,
        _stable_hash([tuple(row) for row in sequence]),
        prediction_count,
        protection_hash,
        str(conn.execute("PRAGMA journal_mode").fetchone()[0]),
    )


def _table_hash(conn: sqlite3.Connection, table: str) -> tuple[str, int, str]:
    columns = _table_columns(conn, table)
    quoted = ",".join(_quote_identifier(column) for column in columns)
    order = _order_columns(conn, table, columns)
    rows = conn.execute(f"SELECT {quoted} FROM {_quote_identifier(table)} ORDER BY {order}").fetchall()
    return table, len(rows), _stable_hash([tuple(row) for row in rows])


def _order_columns(conn: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> str:
    info = conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    primary = [str(row[1]) for row in sorted(info, key=lambda row: row[5]) if row[5]]
    selected = primary or list(columns)
    return ",".join(_quote_identifier(column) for column in selected)


def _assert_backup_equal(source: _LogicalSnapshot, backup: _LogicalSnapshot) -> None:
    fields = (
        "quick_check",
        "user_version",
        "page_size",
        "encoding",
        "schema_hash",
        "table_hashes",
        "sequence_hash",
        "predictions_count",
        "predictions_protection_hash",
    )
    if any(getattr(source, field) != getattr(backup, field) for field in fields):
        raise MigrationContractError("BACKUP_LOGICAL_MISMATCH")


def _assert_non_shadow_equal(before: _LogicalSnapshot, after: _LogicalSnapshot) -> None:
    fields = (
        "user_version",
        "page_size",
        "encoding",
        "schema_hash",
        "table_hashes",
        "sequence_hash",
        "predictions_count",
        "predictions_protection_hash",
    )
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        raise MigrationContractError("NON_SHADOW_DRIFT")
    if after.quick_check != "ok":
        raise MigrationContractError("PRODUCTION_QUICK_CHECK_FAILED")


@lru_cache(maxsize=1)
def _expected_schema() -> dict[str, str]:
    conn = sqlite3.connect(":memory:")
    try:
        _create_schema_in_order(conn)
        rows = conn.execute(
            """SELECT name,sql FROM sqlite_master
               WHERE name LIKE 'outcome_shadow_%' ORDER BY name"""
        ).fetchall()
        return {str(name): _normalize_sql(str(sql)) for name, sql in rows}
    finally:
        conn.close()


def _validate_shadow_schema(conn: sqlite3.Connection) -> None:
    actual_rows = conn.execute(
        """SELECT name,sql FROM sqlite_master
           WHERE name LIKE 'outcome_shadow_%' ORDER BY name"""
    ).fetchall()
    actual = {str(name): _normalize_sql(str(sql)) for name, sql in actual_rows}
    if actual != _expected_schema():
        raise MigrationContractError("SHADOW_SCHEMA_INVALID")


def _create_schema_in_order(conn: sqlite3.Connection) -> None:
    trigger_by_table: dict[str, list[str]] = {table: [] for table in SHADOW_TABLES}
    for statement in _IMMUTABILITY_DDL:
        for table in SHADOW_TABLES:
            if f" ON {table}" in statement:
                trigger_by_table[table].append(statement)
                break
    for table, ddl in zip(SHADOW_TABLES, (_RUNS_DDL, _OBSERVATIONS_DDL, _RESULTS_DDL), strict=True):
        conn.execute(ddl)
        for trigger in trigger_by_table[table]:
            conn.execute(trigger)


def _validate_connection_matches(
    conn: sqlite3.Connection,
    payload: _Payload,
    expected_run: str,
    expected_manifest: str,
) -> None:
    _validate_shadow_schema(conn)
    runs = conn.execute("SELECT run_id,manifest_hash,algorithm_version FROM outcome_shadow_runs").fetchall()
    if len(runs) != 1 or (runs[0]["run_id"], runs[0]["manifest_hash"]) != (expected_run, expected_manifest):
        raise MigrationContractError("IMPORTED_RUN_MISMATCH")
    result_hashes = tuple(
        row[0] for row in conn.execute("SELECT row_hash FROM outcome_shadow_results ORDER BY prediction_id,window_days")
    )
    observation_hashes = tuple(
        row[0]
        for row in conn.execute(
            """SELECT observation_hash FROM outcome_shadow_observations
               ORDER BY instrument_type,instrument_code,trade_date"""
        )
    )
    if result_hashes != payload.result_hashes:
        raise MigrationContractError("IMPORTED_RESULT_HASH_MISMATCH")
    if observation_hashes != payload.observation_hashes:
        raise MigrationContractError("IMPORTED_OBSERVATION_HASH_MISMATCH")
    count, protection = _protection_for_run(str(runs[0]["algorithm_version"]))(conn)
    if (count, protection) != (payload.predictions_count, payload.protection_hash):
        raise MigrationContractError("PREDICTIONS_PROTECTION_DRIFT")


def _insert_rows(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    rows: Sequence[tuple[Any, ...]],
) -> None:
    names = ",".join(_quote_identifier(column) for column in columns)
    placeholders = ",".join("?" for _ in columns)
    conn.executemany(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", rows)


def _table_columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in conn.execute(f"PRAGMA table_xinfo({_quote_identifier(table)})"))


def _shadow_object_names(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'outcome_shadow_%'")}


def _payload_signature(payload: _Payload) -> str:
    return _stable_hash(
        (
            payload.run_row,
            payload.result_hashes,
            payload.observation_hashes,
            payload.protection_hash,
            payload.predictions_count,
        )
    )


def _result(
    status: str,
    production: Path,
    payload: _Payload,
    run_id: str,
    manifest: str,
    backup: str | None = None,
) -> MigrationResult:
    return MigrationResult(
        status,
        run_id,
        manifest,
        len(payload.results),
        len(payload.observations),
        str(production),
        backup,
    )


def _write_evidence(path: Path, result: MigrationResult, extra: dict[str, Any]) -> None:
    payload = {"schema_version": 1, "result": asdict(result), **extra}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_evidence_or_raise(
    path: Path,
    result: MigrationResult,
    extra: dict[str, Any],
    *,
    write_performed: bool,
) -> None:
    try:
        _write_evidence(path, result, extra)
    except OSError as exc:
        raise MigrationEvidenceError(result, path, write_performed=write_performed) from exc


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.split())


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
