"""Authorization-bound, read-only fundamentals snapshot for the M5 data Sprint."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import stat
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast
from urllib.parse import quote

from a_stock_tracker.qualitative.m5.pipeline import EXPECTED_SAMPLE_SHA256, _canonical_json, _load_sample, _sha256_bytes
from a_stock_tracker.qualitative.m5.data_auth import (
    AS_OF_DATE,
    DATABASE_FIELDS,
    DataAuthorization,
    load_data_authorization,
    preflight_data_authorization,
)
from a_stock_tracker.paths import PROJECT_ROOT

SNAPSHOT_SCHEMA_VERSION = "m5-fundamentals-snapshot-v1"
SNAPSHOT_FILENAME = "fundamentals-snapshot.json"
MAX_DATA_JSON_BYTES = 1024 * 1024
_DATA_FIELDS = tuple(field.removeprefix("data.") for field in DATABASE_FIELDS if field.startswith("data."))
_TABLE_COLUMNS = ("code", "name", "industry", "data", "updated_at", "ttl_hours")


class FundamentalsSnapshotError(ValueError):
    """The read-only source or snapshot violates the D1 authorization."""


def _canonical_bytes(value: object) -> bytes:
    return (_canonical_json(value) + "\n").encode("utf-8")


def _file_identity(metadata: os.stat_result) -> dict[str, int]:
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
    }


def _database_file(path: Path) -> tuple[os.stat_result, str]:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise FundamentalsSnapshotError("authorized tracker.db is missing") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise FundamentalsSnapshotError("authorized tracker.db must be a non-symlink regular file")
    uri = f"file:{quote(str(path.resolve(strict=True)), safe='/')}?mode=ro"
    return metadata, uri


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _data_object(raw: object) -> dict[str, object]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_DATA_JSON_BYTES:
        raise FundamentalsSnapshotError("fundamentals data is absent or exceeds the input limit")
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise FundamentalsSnapshotError("fundamentals data is not strict JSON") from exc
    if not isinstance(value, dict):
        raise FundamentalsSnapshotError("fundamentals data must be a JSON object")
    return cast(dict[str, object], value)


def _metric_values(data: dict[str, object]) -> dict[str, object]:
    values: dict[str, object] = {}
    for field in _DATA_FIELDS:
        value = data.get(field)
        if field == "report_period":
            if value is not None and not isinstance(value, str):
                raise FundamentalsSnapshotError("report_period must be a string or null")
        elif value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
        ):
            raise FundamentalsSnapshotError(f"{field} must be a finite number or null")
        values[field] = value
    return values


def _source_status(
    *,
    sample_name: str,
    source_name: object,
    values: dict[str, object],
    updated_at: object,
    ttl_hours: object,
) -> str:
    if source_name != sample_name:
        return "identity_mismatch"
    if not isinstance(updated_at, str):
        return "invalid_updated_at"
    try:
        datetime.fromisoformat(updated_at)
    except ValueError:
        return "invalid_updated_at"
    if isinstance(ttl_hours, bool) or not isinstance(ttl_hours, int) or ttl_hours <= 0:
        return "invalid_ttl"
    report_period = values["report_period"]
    if not isinstance(report_period, str):
        return "missing_report_period"
    try:
        parsed_report_period = date.fromisoformat(report_period[:10])
    except ValueError:
        return "invalid_report_period"
    if parsed_report_period > date.fromisoformat(AS_OF_DATE):
        return "future_report_period"
    return "usable"


def _safe_output(authorization: DataAuthorization, output_path: str | os.PathLike[str]) -> Path:
    relative = Path(authorization.artifact_root)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != authorization.artifact_root:
        raise FundamentalsSnapshotError("authorization artifact root is unsafe")
    expected = PROJECT_ROOT / relative / SNAPSHOT_FILENAME
    output = Path(output_path).absolute()
    if output != expected:
        raise FundamentalsSnapshotError("snapshot output must use the authorization-bound artifact path")
    current = PROJECT_ROOT
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise FundamentalsSnapshotError("snapshot artifact ancestry contains a symlink")
        if current.exists() and not current.is_dir():
            raise FundamentalsSnapshotError("snapshot artifact ancestry contains a non-directory")
    if output.exists() or output.is_symlink():
        raise FundamentalsSnapshotError("fundamentals snapshot output already exists")
    return output


def _secure_write(path: Path, raw: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _read_rows(database_uri: str, database_codes: list[str]) -> tuple[list[tuple[object, ...]], int, int]:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database_uri, uri=True, isolation_level=None)
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone() != (1,):
            raise FundamentalsSnapshotError("SQLite query_only mode was not enforced")
        connection.execute("BEGIN")
        schema_version = int(connection.execute("PRAGMA schema_version").fetchone()[0])
        data_version = int(connection.execute("PRAGMA data_version").fetchone()[0])
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(stock_fundamentals)").fetchall()}
        if not set(_TABLE_COLUMNS) <= columns:
            raise FundamentalsSnapshotError("stock_fundamentals schema is incomplete")
        placeholders = ",".join("?" for _item in database_codes)
        rows = connection.execute(
            f"SELECT code,name,industry,data,updated_at,ttl_hours "
            f"FROM stock_fundamentals WHERE code IN ({placeholders}) ORDER BY code",
            database_codes,
        ).fetchall()
        connection.execute("ROLLBACK")
        return rows, schema_version, data_version
    except sqlite3.Error as exc:
        raise FundamentalsSnapshotError("read-only fundamentals transaction failed") from exc
    finally:
        if connection is not None:
            connection.close()


def build_fundamentals_snapshot(
    sample_path: str | os.PathLike[str],
    authorization_path: str | os.PathLike[str],
    checksum_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Read one authorized SQLite snapshot and publish one create-only JSON artifact."""
    sample = Path(sample_path)
    authorization = load_data_authorization(sample, authorization_path, checksum_path)
    current = now or datetime.now().astimezone()
    preflight_data_authorization(
        sample,
        authorization_path,
        checksum_path,
        now=current,
        require_active=True,
    )
    _sample_sha256, companies = _load_sample(sample)
    output = _safe_output(authorization, output_path)

    database = PROJECT_ROOT / authorization.database_path
    before, database_uri = _database_file(database)
    database_codes = [company.code.split(".", 1)[0] for company in companies]
    rows, schema_version, data_version = _read_rows(database_uri, database_codes)
    try:
        after = database.stat(follow_symlinks=False)
    except OSError as exc:
        raise FundamentalsSnapshotError("tracker.db disappeared after the snapshot") from exc
    if _file_identity(before) != _file_identity(after):
        raise FundamentalsSnapshotError("tracker.db changed during the read-only snapshot")

    by_code: dict[str, tuple[object, ...]] = {}
    for row in rows:
        code = row[0]
        if not isinstance(code, str) or code in by_code:
            raise FundamentalsSnapshotError("stock_fundamentals returned an invalid or duplicate code")
        by_code[code] = row

    snapshot_rows: list[dict[str, object]] = []
    status_counts: Counter[str] = Counter()
    for company in companies:
        database_code = company.code.split(".", 1)[0]
        source = by_code.get(database_code)
        if source is None:
            status = "missing"
            row_value: dict[str, object] = {
                "code": company.code,
                "status": status,
                "source_code": database_code,
                "source_name": None,
                "source_industry": None,
                "updated_at": None,
                "ttl_hours": None,
                "values": {},
            }
        else:
            _code, source_name, source_industry, raw_data, updated_at, ttl_hours = source
            if not isinstance(source_name, str) or not isinstance(source_industry, str):
                values: dict[str, object] = {}
                status = "invalid_data"
            else:
                try:
                    values = _metric_values(_data_object(raw_data))
                    status = _source_status(
                        sample_name=company.name,
                        source_name=source_name,
                        values=values,
                        updated_at=updated_at,
                        ttl_hours=ttl_hours,
                    )
                except FundamentalsSnapshotError:
                    values = {}
                    status = "invalid_data"
            row_value = {
                "code": company.code,
                "status": status,
                "source_code": database_code,
                "source_name": source_name,
                "source_industry": source_industry,
                "updated_at": updated_at if isinstance(updated_at, str) else None,
                "ttl_hours": ttl_hours if isinstance(ttl_hours, int) and not isinstance(ttl_hours, bool) else None,
                "values": values if status == "usable" else {},
            }
        status_counts[status] += 1
        snapshot_rows.append(row_value)

    if current.utcoffset() != timedelta(hours=8):
        current = current.astimezone(authorization.not_before.tzinfo)
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": authorization.sha256,
        "data_run_id": authorization.data_run_id,
        "sample_sha256": EXPECTED_SAMPLE_SHA256,
        "as_of_date": AS_OF_DATE,
        "captured_at": current.isoformat(timespec="seconds"),
        "database": {
            "path": authorization.database_path,
            "mode": "ro",
            "query_only": True,
            "table": "stock_fundamentals",
            "fields": list(DATABASE_FIELDS),
            "transaction_count": 1,
            "schema_version": schema_version,
            "data_version": data_version,
            "file_identity_before": _file_identity(before),
            "file_identity_after": _file_identity(after),
        },
        "company_count": len(snapshot_rows),
        "status_counts": dict(sorted(status_counts.items())),
        "companies": snapshot_rows,
    }
    raw = _canonical_bytes(payload)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.parent.is_symlink() or output.parent.resolve(strict=True) != output.parent:
        raise FundamentalsSnapshotError("snapshot artifact root is symlinked or non-canonical")
    output.parent.chmod(0o700)
    _secure_write(output, raw)
    return {
        "validated": True,
        "output_path": str(output.relative_to(PROJECT_ROOT)),
        "snapshot_sha256": _sha256_bytes(raw),
        "company_count": len(snapshot_rows),
        "status_counts": dict(sorted(status_counts.items())),
        "database_transactions": 1,
        "database_writes": 0,
        "network_calls": 0,
        "model_calls": 0,
    }


__all__ = ["FundamentalsSnapshotError", "SNAPSHOT_SCHEMA_VERSION", "build_fundamentals_snapshot"]
