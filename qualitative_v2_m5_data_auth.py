"""Machine-verifiable authorization boundary for the M5 data-readiness Sprint."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from qualitative_v2_audit import QUERIES
from qualitative_v2_m5 import (
    MAX_JSON_BYTES,
    EXPECTED_SAMPLE_SHA256,
    SampleCompany,
    _canonical_json,
    _load_json_bytes,
    _load_sample,
    _read_regular_file,
    _sha256_bytes,
)

DATA_AUTH_SCHEMA_VERSION = "m5-data-readiness-authorization-v1"
PROTOCOL_PATH = "docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration-v1.1.md"
PROTOCOL_SHA256 = "6990c12859da94ec5648fc26c3c3969282226a4c703d2cb4c189990ba1f2529f"
AS_OF_DATE = "2026-07-17"
FROZEN_QUERIES = ("护城河", "壁垒", "专利", "特许经营", "独占", "授权", "核心技术")
SOURCE_SCOPES = ("cninfo", "sse", "szse", "cnipa", "local_fundamentals")
ALLOWED_HOSTS = (
    "cninfo.com.cn",
    "sse.com.cn",
    "szse.cn",
    "cnipa.gov.cn",
    "cponline.cnipa.gov.cn",
)
DATABASE_FIELDS = (
    "code",
    "name",
    "industry",
    "data.roe_3y_avg",
    "data.net_profit_growth",
    "data.debt_ratio",
    "data.gross_margin",
    "data.report_period",
    "updated_at",
    "ttl_hours",
)
SEARCH_GROUP_COUNT = 36 * 3 * len(FROZEN_QUERIES)
MAX_ATTEMPTS_PER_OPERATION = 3
MAX_SEARCH_HTTP_ATTEMPTS = SEARCH_GROUP_COUNT * MAX_ATTEMPTS_PER_OPERATION
MAX_CANDIDATE_DOCUMENTS = 36 * 20
MAX_DOCUMENT_HTTP_ATTEMPTS = MAX_CANDIDATE_DOCUMENTS * MAX_ATTEMPTS_PER_OPERATION
RELATIONSHIP_REPORT_COUNT = 36
MAX_RELATIONSHIP_HTTP_ATTEMPTS = RELATIONSHIP_REPORT_COUNT * MAX_ATTEMPTS_PER_OPERATION
MAX_HTTP_ATTEMPTS_TOTAL = MAX_SEARCH_HTTP_ATTEMPTS + MAX_DOCUMENT_HTTP_ATTEMPTS + MAX_RELATIONSHIP_HTTP_ATTEMPTS
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 60
MAX_AUTHORIZATION_WINDOW = timedelta(days=14)
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+08:00")

_AUTHORIZATION_FIELDS = frozenset(
    {
        "schema_version",
        "authorization_id",
        "data_run_id",
        "sample_sha256",
        "sample_codes_sha256",
        "as_of_date",
        "protocol_path",
        "protocol_sha256",
        "source_scopes",
        "allowed_hosts",
        "queries",
        "search_matrix_sha256",
        "search_group_count",
        "max_results_per_group",
        "max_candidates_per_company",
        "max_candidate_documents_total",
        "relationship_report_count",
        "max_attempts_per_operation",
        "attempt_schedule",
        "max_search_http_attempts",
        "max_document_http_attempts",
        "max_relationship_http_attempts",
        "max_http_attempts_total",
        "request_timeout_seconds",
        "max_response_bytes",
        "max_artifact_bytes",
        "http_methods",
        "https_verification",
        "redirect_policy",
        "database_path",
        "database_mode",
        "database_table",
        "database_fields",
        "database_transaction_limit",
        "artifact_root",
        "not_before",
        "not_after",
        "allow_network",
        "allow_database_read",
        "allow_database_write",
        "allow_reviewers",
        "allow_models",
        "allow_production_paths",
    }
)


class DataAuthorizationError(ValueError):
    """The proposed D1 authorization does not match the frozen boundary."""


@dataclass(frozen=True, slots=True)
class DataAuthorization:
    """Validated immutable identity for one D1 data-readiness run."""

    authorization_id: str
    data_run_id: str
    not_before: datetime
    not_after: datetime
    sha256: str
    raw: bytes


def _canonical_bytes(value: object) -> bytes:
    return (_canonical_json(value) + "\n").encode("utf-8")


def _timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise DataAuthorizationError(f"{field} must use second-precision Asia/Shanghai offset time")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(hours=8) or parsed.microsecond:
        raise DataAuthorizationError(f"{field} must use second-precision Asia/Shanghai offset time")
    return parsed


def _identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None or value in {".", ".."}:
        raise DataAuthorizationError(f"invalid {field}")
    return value


def _sample_codes_sha256(companies: tuple[SampleCompany, ...]) -> str:
    return _sha256_bytes(_canonical_bytes([company.code for company in companies]))


def _verify_protocol() -> None:
    if QUERIES != FROZEN_QUERIES:
        raise DataAuthorizationError("M4 query constants drift from the frozen D1 protocol")
    protocol = Path(__file__).resolve().parent / PROTOCOL_PATH
    raw = _read_regular_file(protocol, maximum_bytes=MAX_JSON_BYTES)
    if _sha256_bytes(raw) != PROTOCOL_SHA256:
        raise DataAuthorizationError("M4 v1.1 protocol SHA-256 drift")


def search_matrix(companies: tuple[SampleCompany, ...]) -> tuple[dict[str, object], ...]:
    """Return the exact 756-group source/query matrix without contacting a source."""
    groups: list[dict[str, object]] = []
    ordinal = 0
    for company in companies:
        exchange = "SSE" if company.code.endswith(".SH") else "SZSE"
        for source in ("CNINFO", exchange, "CNIPA"):
            for query in FROZEN_QUERIES:
                ordinal += 1
                groups.append(
                    {
                        "ordinal": ordinal,
                        "code": company.code,
                        "source": source,
                        "query": query,
                        "max_results": 20,
                    }
                )
    if len(groups) != SEARCH_GROUP_COUNT:
        raise DataAuthorizationError("fixed search matrix count drift")
    return tuple(groups)


def authorization_value(
    sample_path: str | os.PathLike[str],
    *,
    authorization_id: str,
    data_run_id: str,
    not_before: str,
    not_after: str,
) -> dict[str, object]:
    """Build the canonical authorization value without credentials, database, or network."""
    _verify_protocol()
    sample_sha256, companies = _load_sample(Path(sample_path))
    if sample_sha256 != EXPECTED_SAMPLE_SHA256:
        raise DataAuthorizationError("fixed sample SHA-256 drift")
    auth_id = _identifier(authorization_id, field="authorization_id")
    run_id = _identifier(data_run_id, field="data_run_id")
    start = _timestamp(not_before, field="not_before")
    end = _timestamp(not_after, field="not_after")
    if start >= end or end - start > MAX_AUTHORIZATION_WINDOW:
        raise DataAuthorizationError("authorization window must be positive and no longer than 14 days")
    if (end.date() - start.date()).days < 2:
        raise DataAuthorizationError("authorization window must contain at least three calendar dates")
    matrix = search_matrix(companies)
    matrix_sha256 = _sha256_bytes(_canonical_bytes(matrix))
    return {
        "schema_version": DATA_AUTH_SCHEMA_VERSION,
        "authorization_id": auth_id,
        "data_run_id": run_id,
        "sample_sha256": sample_sha256,
        "sample_codes_sha256": _sample_codes_sha256(companies),
        "as_of_date": AS_OF_DATE,
        "protocol_path": PROTOCOL_PATH,
        "protocol_sha256": PROTOCOL_SHA256,
        "source_scopes": list(SOURCE_SCOPES),
        "allowed_hosts": list(ALLOWED_HOSTS),
        "queries": list(FROZEN_QUERIES),
        "search_matrix_sha256": matrix_sha256,
        "search_group_count": SEARCH_GROUP_COUNT,
        "max_results_per_group": 20,
        "max_candidates_per_company": 20,
        "max_candidate_documents_total": MAX_CANDIDATE_DOCUMENTS,
        "relationship_report_count": RELATIONSHIP_REPORT_COUNT,
        "max_attempts_per_operation": MAX_ATTEMPTS_PER_OPERATION,
        "attempt_schedule": "D_Dplus1_Dplus2_one_attempt_per_local_date",
        "max_search_http_attempts": MAX_SEARCH_HTTP_ATTEMPTS,
        "max_document_http_attempts": MAX_DOCUMENT_HTTP_ATTEMPTS,
        "max_relationship_http_attempts": MAX_RELATIONSHIP_HTTP_ATTEMPTS,
        "max_http_attempts_total": MAX_HTTP_ATTEMPTS_TOTAL,
        "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "max_artifact_bytes": MAX_ARTIFACT_BYTES,
        "http_methods": ["GET", "POST"],
        "https_verification": "required",
        "redirect_policy": "same_origin_only",
        "database_path": "tracker.db",
        "database_mode": "ro",
        "database_table": "stock_fundamentals",
        "database_fields": list(DATABASE_FIELDS),
        "database_transaction_limit": 1,
        "artifact_root": f"artifacts/milestone-005/data/{run_id}",
        "not_before": not_before,
        "not_after": not_after,
        "allow_network": True,
        "allow_database_read": True,
        "allow_database_write": False,
        "allow_reviewers": False,
        "allow_models": False,
        "allow_production_paths": False,
    }


def _expected_value(value: dict[str, object], sample_path: Path) -> dict[str, object]:
    if set(value) != _AUTHORIZATION_FIELDS:
        raise DataAuthorizationError("authorization has missing or unknown fields")
    return authorization_value(
        sample_path,
        authorization_id=_identifier(value["authorization_id"], field="authorization_id"),
        data_run_id=_identifier(value["data_run_id"], field="data_run_id"),
        not_before=cast(str, value["not_before"]),
        not_after=cast(str, value["not_after"]),
    )


def load_data_authorization(
    sample_path: str | os.PathLike[str],
    authorization_path: str | os.PathLike[str],
    checksum_path: str | os.PathLike[str],
) -> DataAuthorization:
    """Verify canonical bytes, checksum, fixed matrix, limits, window, and prohibitions."""
    sample = Path(sample_path)
    authorization_file = Path(authorization_path)
    raw = _read_regular_file(authorization_file, maximum_bytes=MAX_JSON_BYTES)
    value = _load_json_bytes(raw, label="M5 data authorization")
    expected = _expected_value(value, sample)
    if value != expected or raw != _canonical_bytes(expected):
        raise DataAuthorizationError("authorization is non-canonical or drifts from the frozen boundary")
    digest = _sha256_bytes(raw)
    checksum_raw = _read_regular_file(Path(checksum_path), maximum_bytes=1024)
    expected_checksum = f"{digest}  {authorization_file.name}\n".encode("ascii")
    if checksum_raw != expected_checksum:
        raise DataAuthorizationError("authorization checksum is missing, ambiguous, or drifted")
    return DataAuthorization(
        authorization_id=cast(str, value["authorization_id"]),
        data_run_id=cast(str, value["data_run_id"]),
        not_before=_timestamp(value["not_before"], field="not_before"),
        not_after=_timestamp(value["not_after"], field="not_after"),
        sha256=digest,
        raw=raw,
    )


def seal_data_authorization(
    sample_path: str | os.PathLike[str],
    authorization_path: str | os.PathLike[str],
    checksum_path: str | os.PathLike[str],
    *,
    authorization_id: str,
    data_run_id: str,
    not_before: str,
    not_after: str,
) -> DataAuthorization:
    """Create one authorization/checksum pair without overwriting existing files."""
    authorization_file = Path(authorization_path)
    checksum_file = Path(checksum_path)
    if (
        authorization_file.exists()
        or authorization_file.is_symlink()
        or checksum_file.exists()
        or checksum_file.is_symlink()
    ):
        raise DataAuthorizationError("authorization or checksum output already exists")
    if authorization_file == checksum_file:
        raise DataAuthorizationError("authorization and checksum outputs must differ")
    if authorization_file.parent != checksum_file.parent:
        raise DataAuthorizationError("authorization and checksum must share one directory")
    value = authorization_value(
        sample_path,
        authorization_id=authorization_id,
        data_run_id=data_run_id,
        not_before=not_before,
        not_after=not_after,
    )
    raw = _canonical_bytes(value)
    digest = _sha256_bytes(raw)
    authorization_file.parent.mkdir(parents=True, exist_ok=True)
    authorization_created = False
    checksum_created = False
    descriptor = os.open(authorization_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    authorization_created = True
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        checksum_raw = f"{digest}  {authorization_file.name}\n".encode("ascii")
        checksum_descriptor = os.open(checksum_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        checksum_created = True
        with os.fdopen(checksum_descriptor, "wb") as stream:
            stream.write(checksum_raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        created_paths = []
        if checksum_created:
            created_paths.append(checksum_file)
        if authorization_created:
            created_paths.append(authorization_file)
        for path in created_paths:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    return load_data_authorization(sample_path, authorization_file, checksum_file)


def preflight_data_authorization(
    sample_path: str | os.PathLike[str],
    authorization_path: str | os.PathLike[str],
    checksum_path: str | os.PathLike[str],
    *,
    now: datetime | None = None,
    require_active: bool = False,
) -> dict[str, object]:
    """Return a zero-network, zero-database readiness summary for one authorization."""
    authorization = load_data_authorization(sample_path, authorization_path, checksum_path)
    current = now or datetime.now().astimezone()
    if current.utcoffset() != timedelta(hours=8):
        current = current.astimezone(authorization.not_before.tzinfo)
    if current < authorization.not_before:
        window_state = "not_yet_valid"
    elif current > authorization.not_after:
        window_state = "expired"
    else:
        window_state = "active"
    if require_active and window_state != "active":
        raise DataAuthorizationError(f"authorization window is {window_state}")
    return {
        "validated": True,
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": authorization.sha256,
        "data_run_id": authorization.data_run_id,
        "sample_sha256": EXPECTED_SAMPLE_SHA256,
        "as_of_date": AS_OF_DATE,
        "search_group_count": SEARCH_GROUP_COUNT,
        "max_http_attempts_total": MAX_HTTP_ATTEMPTS_TOTAL,
        "max_artifact_bytes": MAX_ARTIFACT_BYTES,
        "database_transaction_limit": 1,
        "window_state": window_state,
        "network_calls_performed": 0,
        "database_reads_performed": 0,
        "credential_variables_read": 0,
        "artifacts_created": 0,
        "reviewer_calls_performed": 0,
        "model_calls_performed": 0,
    }


__all__ = [
    "DATA_AUTH_SCHEMA_VERSION",
    "DataAuthorization",
    "DataAuthorizationError",
    "authorization_value",
    "load_data_authorization",
    "preflight_data_authorization",
    "seal_data_authorization",
    "search_matrix",
]
