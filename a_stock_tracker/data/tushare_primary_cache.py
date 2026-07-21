"""Isolated SQLite and artifact store for TuShare primary-source shadow ingestion."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

PIT_STATUSES = {"backfilled_latest", "prospective_observed"}
FINANCIAL_ENDPOINTS = {"fina_indicator", "income", "balancesheet", "cashflow"}
ALLOWED_ENDPOINTS = FINANCIAL_ENDPOINTS | {"daily_basic", "dividend"}
SHADOW_TABLES = {
    "ingestion_runs",
    "observation_events",
    "valuation_observations",
    "financial_observations",
    "dividend_observations",
}
_SECRET_PATTERNS = (
    re.compile(r"(?i)(TUSHARE_TOKEN\s*=\s*)[^\s,;]+"),
    re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(token[=:]\s*)[^\s,;]+"),
)


class ShadowStoreError(RuntimeError):
    """Raised when the explicit shadow storage contract is violated."""


@dataclass(frozen=True)
class ArtifactRecord:
    """Metadata for one atomically persisted raw response artifact."""

    path: Path
    relative_path: str
    sha256: str


def open_shadow_store(db_path: Path | str) -> sqlite3.Connection:
    """Open and initialize only the explicit TuShare shadow schema."""
    path = _validated_db_path(db_path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        _assert_shadow_compatible(conn)
        _create_schema(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _validated_db_path(db_path: Path | str) -> Path:
    raw = str(db_path).strip()
    if not raw:
        raise ShadowStoreError("DB_PATH_EMPTY")
    path = Path(raw).expanduser().resolve()
    if path.is_dir():
        raise ShadowStoreError("DB_PATH_IS_DIRECTORY")
    if path.name == "tracker.db":
        raise ShadowStoreError("PRODUCTION_DB_PATH_REJECTED")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ShadowStoreError(f"DB_PARENT_UNAVAILABLE:{path.parent}") from exc
    if not os.access(path.parent, os.W_OK):
        raise ShadowStoreError(f"DB_PARENT_NOT_WRITABLE:{path.parent}")
    return path


def _assert_shadow_compatible(conn: sqlite3.Connection) -> None:
    tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    unexpected = tables - SHADOW_TABLES - {"sqlite_sequence"}
    if unexpected:
        raise ShadowStoreError(f"NON_SHADOW_SCHEMA_DETECTED:{sorted(unexpected)}")


def _create_schema(conn: sqlite3.Connection) -> None:
    statements = (
        _INGESTION_RUNS_DDL,
        _VALUATION_DDL,
        _FINANCIAL_DDL,
        _DIVIDEND_DDL,
        _EVENTS_DDL,
        "CREATE INDEX IF NOT EXISTS idx_valuation_code_date ON valuation_observations(code, trade_date)",
        "CREATE INDEX IF NOT EXISTS idx_valuation_date ON valuation_observations(trade_date)",
        "CREATE INDEX IF NOT EXISTS idx_financial_code_endpoint_period ON financial_observations(code, endpoint, end_date)",
        "CREATE INDEX IF NOT EXISTS idx_dividend_code_end_date ON dividend_observations(code, end_date)",
        "CREATE INDEX IF NOT EXISTS idx_events_record_observed ON observation_events(record_key, observed_at)",
    )
    for statement in statements:
        conn.execute(statement)
    conn.commit()


def create_ingestion_run(
    conn: sqlite3.Connection,
    endpoint: str,
    request_fingerprint: str,
    requested_at: str,
) -> int:
    """Create a durable request checkpoint before provider execution."""
    cursor = conn.execute(
        """INSERT INTO ingestion_runs
           (endpoint, request_fingerprint, requested_at, status, row_count)
           VALUES (?, ?, ?, 'running', 0)""",
        (endpoint, request_fingerprint, requested_at),
    )
    conn.commit()
    if cursor.lastrowid is None:
        raise ShadowStoreError("RUN_ID_UNAVAILABLE")
    return int(cursor.lastrowid)


def complete_ingestion_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    completed_at: str,
    row_count: int = 0,
    source_as_of: str | None = None,
    payload_sha256: str | None = None,
    raw_artifact_path: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Close one checkpoint with sanitized terminal metadata."""
    conn.execute(
        """UPDATE ingestion_runs
           SET completed_at=?, status=?, error_code=?, error_message=?, row_count=?,
               source_as_of=?, payload_sha256=?, raw_artifact_path=?
           WHERE run_id=?""",
        (
            completed_at,
            status,
            error_code,
            sanitize_error_message(error_message),
            row_count,
            source_as_of,
            payload_sha256,
            raw_artifact_path,
            run_id,
        ),
    )
    conn.commit()


def persist_observations(
    conn: sqlite3.Connection,
    endpoint: str,
    rows: Iterable[dict[str, Any]],
    run_id: int,
    observed_at: str,
    pit_status: str,
    source: str,
    source_as_of: str | None,
    commit: bool = True,
) -> tuple[int, int]:
    """Persist immutable content records and separate observation events."""
    if pit_status not in PIT_STATUSES:
        raise ShadowStoreError(f"INVALID_PIT_STATUS:{pit_status}")
    record_count = 0
    event_count = 0
    for raw_row in rows:
        row = _normalize_mapping(raw_row)
        record_key, payload_json, payload_sha = _record_identity(endpoint, row)
        record_count += _insert_record(conn, endpoint, record_key, row, payload_json, payload_sha, source, source_as_of)
        event_count += _insert_event(conn, run_id, record_key, observed_at, pit_status)
    if commit:
        conn.commit()
    return record_count, event_count


def _record_identity(endpoint: str, row: dict[str, Any]) -> tuple[str, str, str]:
    payload_json = _canonical_json(row)
    payload_sha = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    natural_key = _natural_key(endpoint, row)
    identity = _canonical_json({"endpoint": endpoint, "natural_key": natural_key, "payload_sha256": payload_sha})
    return hashlib.sha256(identity.encode("utf-8")).hexdigest(), payload_json, payload_sha


def _natural_key(endpoint: str, row: dict[str, Any]) -> dict[str, Any]:
    keys: tuple[str, ...]
    if endpoint == "daily_basic":
        keys = ("ts_code", "trade_date")
    elif endpoint in FINANCIAL_ENDPOINTS:
        keys = (
            "ts_code",
            "ann_date",
            "f_ann_date",
            "end_date",
            "report_type",
            "comp_type",
            "end_type",
            "update_flag",
        )
    elif endpoint == "dividend":
        keys = ("ts_code", "ann_date", "end_date", "record_date", "ex_date", "div_proc")
    else:
        raise ShadowStoreError(f"UNSUPPORTED_ENDPOINT:{endpoint}")
    return {key: row.get(key) for key in keys}


def _insert_record(
    conn: sqlite3.Connection,
    endpoint: str,
    record_key: str,
    row: dict[str, Any],
    payload_json: str,
    payload_sha: str,
    source: str,
    source_as_of: str | None,
) -> int:
    if endpoint == "daily_basic":
        return _insert_valuation(conn, record_key, row, payload_sha, source, source_as_of)
    if endpoint in FINANCIAL_ENDPOINTS:
        return _insert_financial(conn, endpoint, record_key, row, payload_json, payload_sha, source)
    if endpoint == "dividend":
        return _insert_dividend(conn, record_key, row, payload_json, payload_sha, source)
    raise ShadowStoreError(f"UNSUPPORTED_ENDPOINT:{endpoint}")


def _insert_valuation(
    conn: sqlite3.Connection,
    record_key: str,
    row: dict[str, Any],
    payload_sha: str,
    source: str,
    source_as_of: str | None,
) -> int:
    cursor = conn.execute(
        """INSERT OR IGNORE INTO valuation_observations
           (record_key, code, trade_date, close, pe, pe_ttm, pb, ps, ps_ttm,
            dv_ratio, dv_ttm, total_mv, circ_mv, source, source_as_of, payload_sha256)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            record_key,
            _plain_code(row),
            row.get("trade_date"),
            row.get("close"),
            row.get("pe"),
            row.get("pe_ttm"),
            row.get("pb"),
            row.get("ps"),
            row.get("ps_ttm"),
            row.get("dv_ratio"),
            row.get("dv_ttm"),
            row.get("total_mv"),
            row.get("circ_mv"),
            source,
            source_as_of or row.get("trade_date"),
            payload_sha,
        ),
    )
    return cursor.rowcount


def _insert_financial(
    conn: sqlite3.Connection,
    endpoint: str,
    record_key: str,
    row: dict[str, Any],
    payload_json: str,
    payload_sha: str,
    source: str,
) -> int:
    cursor = conn.execute(
        """INSERT OR IGNORE INTO financial_observations
           (record_key, code, endpoint, ann_date, f_ann_date, end_date, report_type,
            comp_type, end_type, update_flag, source, payload_json, payload_sha256)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            record_key,
            _plain_code(row),
            endpoint,
            row.get("ann_date"),
            row.get("f_ann_date"),
            row.get("end_date"),
            row.get("report_type"),
            row.get("comp_type"),
            row.get("end_type"),
            row.get("update_flag"),
            source,
            payload_json,
            payload_sha,
        ),
    )
    return cursor.rowcount


def _insert_dividend(
    conn: sqlite3.Connection,
    record_key: str,
    row: dict[str, Any],
    payload_json: str,
    payload_sha: str,
    source: str,
) -> int:
    cursor = conn.execute(
        """INSERT OR IGNORE INTO dividend_observations
           (record_key, code, ann_date, end_date, record_date, ex_date, div_proc,
            source, payload_json, payload_sha256)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            record_key,
            _plain_code(row),
            row.get("ann_date"),
            row.get("end_date"),
            row.get("record_date"),
            row.get("ex_date"),
            row.get("div_proc"),
            source,
            payload_json,
            payload_sha,
        ),
    )
    return cursor.rowcount


def _insert_event(
    conn: sqlite3.Connection,
    run_id: int,
    record_key: str,
    observed_at: str,
    pit_status: str,
) -> int:
    event_key = hashlib.sha256(f"{run_id}:{record_key}".encode("utf-8")).hexdigest()
    cursor = conn.execute(
        """INSERT OR IGNORE INTO observation_events
           (event_key, record_key, run_id, observed_at, pit_status)
           VALUES (?, ?, ?, ?, ?)""",
        (event_key, record_key, run_id, observed_at, pit_status),
    )
    return cursor.rowcount


def write_raw_artifact(
    artifact_root: Path | str,
    run_id: int,
    endpoint: str,
    rows: Iterable[dict[str, Any]],
) -> ArtifactRecord:
    """Atomically write canonical JSONL as deterministic gzip without credentials."""
    if endpoint not in ALLOWED_ENDPOINTS:
        raise ShadowStoreError(f"UNSUPPORTED_ENDPOINT:{endpoint}")
    if run_id < 1:
        raise ShadowStoreError(f"INVALID_RUN_ID:{run_id}")
    root = Path(artifact_root).expanduser().resolve()
    relative_path = Path(str(run_id)) / f"{endpoint}.jsonl.gz"
    path = root / relative_path
    lines = (_canonical_json(_normalize_mapping(row)) for row in rows)
    raw = "".join(f"{line}\n" for line in lines).encode("utf-8")
    payload = gzip.compress(raw, mtime=0)
    _atomic_write_bytes(path, payload)
    return ArtifactRecord(path, relative_path.as_posix(), hashlib.sha256(payload).hexdigest())


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def sanitize_error_message(message: str | None) -> str | None:
    """Redact common credential forms before persistence or output."""
    if message is None:
        return None
    sanitized = message
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub(r"\1[REDACTED]", sanitized)
    return sanitized


def _normalize_mapping(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _normalize_value(value) for key, value in row.items()}


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return None
    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            return _normalize_value(item_method())
        except (TypeError, ValueError):
            return None
    try:
        missing = value != value
    except Exception:
        missing = False
    if isinstance(missing, bool) and missing:
        return None
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _plain_code(row: dict[str, Any]) -> str:
    ts_code = str(row.get("ts_code") or "")
    code = ts_code.split(".", maxsplit=1)[0]
    if not re.fullmatch(r"\d{6}", code):
        raise ShadowStoreError(f"INVALID_TS_CODE:{ts_code}")
    return code


_INGESTION_RUNS_DDL = """CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    row_count INTEGER NOT NULL,
    source_as_of TEXT,
    payload_sha256 TEXT,
    raw_artifact_path TEXT
)"""

_EVENTS_DDL = """CREATE TABLE IF NOT EXISTS observation_events (
    event_key TEXT PRIMARY KEY,
    record_key TEXT NOT NULL,
    run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
    observed_at TEXT NOT NULL,
    pit_status TEXT NOT NULL CHECK (pit_status IN ('backfilled_latest', 'prospective_observed')),
    UNIQUE(run_id, record_key)
)"""

_VALUATION_DDL = """CREATE TABLE IF NOT EXISTS valuation_observations (
    record_key TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    close REAL,
    pe REAL,
    pe_ttm REAL,
    pb REAL,
    ps REAL,
    ps_ttm REAL,
    dv_ratio REAL,
    dv_ttm REAL,
    total_mv REAL,
    circ_mv REAL,
    source TEXT NOT NULL,
    source_as_of TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
)"""

_FINANCIAL_DDL = """CREATE TABLE IF NOT EXISTS financial_observations (
    record_key TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    ann_date TEXT,
    f_ann_date TEXT,
    end_date TEXT NOT NULL,
    report_type TEXT,
    comp_type TEXT,
    end_type TEXT,
    update_flag TEXT,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
)"""

_DIVIDEND_DDL = """CREATE TABLE IF NOT EXISTS dividend_observations (
    record_key TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    ann_date TEXT,
    end_date TEXT,
    record_date TEXT,
    ex_date TEXT,
    div_proc TEXT,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
)"""
