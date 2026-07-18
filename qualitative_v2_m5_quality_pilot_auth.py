"""Zero-call authorization boundary for the M5 v1.2 evidence-quality pilot."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from qualitative_v2_m5 import (
    EXPECTED_SAMPLE_SHA256,
    MAX_JSON_BYTES,
    SUPER_STRATA,
    SampleCompany,
    _canonical_json,
    _load_json_bytes,
    _load_sample,
    _read_regular_file,
    _sha256_bytes,
)

QUALITY_PILOT_AUTH_SCHEMA_VERSION = "m5-evidence-quality-pilot-authorization-v1.2"
CAPABILITY_REPORT_SCHEMA_VERSION = "m5-source-capability-probe-v1.2"
SOURCE = "CNINFO"
QUERY = "核心技术"
FRONT_DOOR_URL = "https://www.cninfo.com.cn/new/fulltextSearch"
SEARCH_ENDPOINT = "https://www.cninfo.com.cn/new/fulltextSearch/full"
ALLOWED_HOSTS = ("www.cninfo.com.cn", "static.cninfo.com.cn")
RESULTS_PER_COMPANY = 3
QUERY_GROUP_COUNT = len(SUPER_STRATA)
MAX_UNIQUE_DOCUMENTS = QUERY_GROUP_COUNT * RESULTS_PER_COMPANY
MAX_ATTEMPTS_PER_OPERATION = 3
MAX_SEARCH_HTTP_ATTEMPTS = QUERY_GROUP_COUNT * MAX_ATTEMPTS_PER_OPERATION
MAX_DOCUMENT_HTTP_ATTEMPTS = MAX_UNIQUE_DOCUMENTS * MAX_ATTEMPTS_PER_OPERATION
MAX_HTTP_ATTEMPTS_TOTAL = MAX_SEARCH_HTTP_ATTEMPTS + MAX_DOCUMENT_HTTP_ATTEMPTS
REQUEST_TIMEOUT_SECONDS = 60
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_AUTHORIZATION_WINDOW = timedelta(days=14)
EVIDENCE_START_DATE = "2025-07-18"
AS_OF_DATE = "2026-07-17"
EXPECTED_PILOT_COMPANIES = (
    ("002807.SZ", "江阴银行", "银行", "金融地产", "low"),
    ("301057.SZ", "汇隆新材", "基础化工", "能源材料", "low"),
    ("603507.SH", "振江股份", "电力设备", "工业基础设施", "low"),
    ("300531.SZ", "优博讯", "计算机", "科技通信", "low"),
    ("002084.SZ", "海鸥住工", "轻工制造", "消费", "low"),
    ("002412.SZ", "汉森制药", "医药生物", "医疗公用事业", "low"),
)

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+08:00")
_AUTHORIZATION_FIELDS = frozenset(
    {
        "schema_version",
        "authorization_id",
        "pilot_run_id",
        "sample_sha256",
        "capability_report_sha256",
        "capability_report_schema_version",
        "evidence_start_date",
        "as_of_date",
        "source",
        "source_front_door_url",
        "search_endpoint",
        "allowed_hosts",
        "query",
        "pilot_companies",
        "query_matrix_sha256",
        "query_group_count",
        "results_per_company",
        "max_unique_documents",
        "max_attempts_per_operation",
        "attempt_schedule",
        "attempt_local_dates",
        "same_day_retry",
        "fallback_sources",
        "max_search_http_attempts",
        "max_document_http_attempts",
        "max_http_attempts_total",
        "request_timeout_seconds",
        "max_response_bytes",
        "max_artifact_bytes",
        "http_methods",
        "https_verification",
        "redirect_policy",
        "artifact_root",
        "not_before",
        "not_after",
        "quality_scope",
        "semantic_quality_authorized",
        "collection_protocol_frozen",
        "allow_network",
        "allow_credentials",
        "allow_database_read",
        "allow_database_write",
        "allow_reviewers",
        "allow_models",
        "allow_production_paths",
    }
)


class QualityPilotAuthorizationError(ValueError):
    """The proposed pilot authorization does not match the bounded contract."""


@dataclass(frozen=True, slots=True)
class QualityPilotAuthorization:
    """Validated immutable identity for one evidence-quality pilot."""

    authorization_id: str
    pilot_run_id: str
    not_before: datetime
    not_after: datetime
    artifact_root: str
    capability_report_sha256: str
    sha256: str
    raw: bytes


def _canonical_bytes(value: object) -> bytes:
    return (_canonical_json(value) + "\n").encode("utf-8")


def _identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None or value in {".", ".."}:
        raise QualityPilotAuthorizationError(f"invalid {field}")
    return value


def _sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise QualityPilotAuthorizationError(f"invalid {field}")
    return value


def _timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise QualityPilotAuthorizationError(f"{field} must use second-precision Asia/Shanghai offset time")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(hours=8) or parsed.microsecond:
        raise QualityPilotAuthorizationError(f"{field} must use second-precision Asia/Shanghai offset time")
    return parsed


def _pilot_companies(companies: tuple[SampleCompany, ...]) -> tuple[SampleCompany, ...]:
    selected: list[SampleCompany] = []
    for super_stratum in SUPER_STRATA:
        company = next((item for item in companies if item.super_stratum == super_stratum), None)
        if company is None:
            raise QualityPilotAuthorizationError(f"sample has no company for {super_stratum}")
        selected.append(company)
    identities = tuple(
        (item.code, item.name, item.industry, item.super_stratum, item.market_cap_stratum) for item in selected
    )
    if identities != EXPECTED_PILOT_COMPANIES:
        raise QualityPilotAuthorizationError("pilot company selection drift")
    return tuple(selected)


def query_matrix(companies: tuple[SampleCompany, ...]) -> tuple[dict[str, object], ...]:
    """Return the exact six-group CNINFO pilot matrix without external access."""
    selected = _pilot_companies(companies)
    return tuple(
        {
            "ordinal": ordinal,
            "code": company.code,
            "name": company.name,
            "industry": company.industry,
            "super_stratum": company.super_stratum,
            "market_cap_stratum": company.market_cap_stratum,
            "source": SOURCE,
            "query": QUERY,
            "searchkey": f"{company.code.split('.', 1)[0]},{QUERY}",
            "sdate": EVIDENCE_START_DATE,
            "edate": AS_OF_DATE,
            "isfulltext": "true",
            "sortName": "nothing",
            "sortType": "desc",
            "pageNum": 1,
            "pageSize": 20,
            "type": "shj",
            "retained_default_ranks": [1, 2, 3],
        }
        for ordinal, company in enumerate(selected, start=1)
    )


def _validated_capability_report(path: Path, expected_sha256: str) -> None:
    try:
        raw = _read_regular_file(path, maximum_bytes=MAX_JSON_BYTES)
        report = _load_json_bytes(raw, label="M5 v1.2 capability report")
    except (OSError, ValueError) as exc:
        raise QualityPilotAuthorizationError("capability report is missing or invalid") from exc
    if _sha256_bytes(raw) != expected_sha256:
        raise QualityPilotAuthorizationError("capability report SHA-256 drift")
    if raw != _canonical_bytes(report):
        raise QualityPilotAuthorizationError("capability report must use canonical JSON bytes")
    if (
        report.get("schema_version") != CAPABILITY_REPORT_SCHEMA_VERSION
        or report.get("sample_sha256") != EXPECTED_SAMPLE_SHA256
    ):
        raise QualityPilotAuthorizationError("capability report identity drift")
    routing = report.get("routing")
    lifecycle = report.get("lifecycle")
    if not isinstance(routing, dict) or not isinstance(lifecycle, dict):
        raise QualityPilotAuthorizationError("capability report routing or lifecycle is invalid")
    if (
        routing.get("viable_fulltext_sources") != [SOURCE]
        or routing.get("minimum_viable_official_fulltext_route") is not True
        or routing.get("global_capability_blocked") is not False
        or lifecycle.get("collection_protocol_frozen") is not False
        or lifecycle.get("protocol_freeze_ready") is not False
        or lifecycle.get("next_action") != "authorize_bounded_evidence_quality_pilot"
    ):
        raise QualityPilotAuthorizationError("capability report does not authorize the proposed candidate route")
    sources = report.get("sources")
    if not isinstance(sources, list) or len(sources) != 4:
        raise QualityPilotAuthorizationError("capability report must contain four source rows")
    cninfo_rows = [row for row in sources if isinstance(row, dict) and row.get("source") == SOURCE]
    if len(cninfo_rows) != 1:
        raise QualityPilotAuthorizationError("capability report must contain exactly one CNINFO row")
    cninfo = cninfo_rows[0]
    transport = cninfo.get("transport")
    full_text = cninfo.get("full_text_semantics")
    quality = cninfo.get("evidence_quality")
    if (
        not isinstance(transport, dict)
        or transport.get("status") != "available"
        or not isinstance(full_text, dict)
        or full_text.get("status") != "demonstrated"
        or not isinstance(quality, dict)
        or quality.get("status") != "not_evaluated"
        or cninfo.get("candidate_fulltext_collection_eligible") is not True
    ):
        raise QualityPilotAuthorizationError("CNINFO capability axes do not match the pilot prerequisite")
    for counter in (
        "network_calls_performed",
        "database_reads_performed",
        "credential_variables_read",
        "reviewer_calls_performed",
        "model_calls_performed",
    ):
        if report.get(counter) != 0:
            raise QualityPilotAuthorizationError(f"capability report has nonzero {counter}")


def authorization_value(
    sample_path: str | os.PathLike[str],
    capability_report_path: str | os.PathLike[str],
    *,
    capability_report_sha256: str,
    authorization_id: str,
    pilot_run_id: str,
    not_before: str,
    not_after: str,
) -> dict[str, object]:
    """Build the canonical pilot authorization without network, credentials, or artifacts."""
    report_sha256 = _sha256(capability_report_sha256, field="capability_report_sha256")
    _validated_capability_report(Path(capability_report_path), report_sha256)
    sample_sha256, companies = _load_sample(Path(sample_path))
    if sample_sha256 != EXPECTED_SAMPLE_SHA256:
        raise QualityPilotAuthorizationError("fixed sample SHA-256 drift")
    auth_id = _identifier(authorization_id, field="authorization_id")
    run_id = _identifier(pilot_run_id, field="pilot_run_id")
    start = _timestamp(not_before, field="not_before")
    end = _timestamp(not_after, field="not_after")
    if start >= end or end - start > MAX_AUTHORIZATION_WINDOW:
        raise QualityPilotAuthorizationError("authorization window must be positive and no longer than 14 days")
    if (end.date() - start.date()).days != 2:
        raise QualityPilotAuthorizationError("authorization window must contain exactly three calendar dates")
    if start.timetz().replace(tzinfo=None) != datetime.min.time() or end.timetz().replace(
        tzinfo=None
    ) != datetime.max.time().replace(microsecond=0):
        raise QualityPilotAuthorizationError("authorization window must cover three complete local dates")
    matrix = query_matrix(companies)
    matrix_sha256 = _sha256_bytes(_canonical_bytes(matrix))
    return {
        "schema_version": QUALITY_PILOT_AUTH_SCHEMA_VERSION,
        "authorization_id": auth_id,
        "pilot_run_id": run_id,
        "sample_sha256": sample_sha256,
        "capability_report_sha256": report_sha256,
        "capability_report_schema_version": CAPABILITY_REPORT_SCHEMA_VERSION,
        "evidence_start_date": EVIDENCE_START_DATE,
        "as_of_date": AS_OF_DATE,
        "source": SOURCE,
        "source_front_door_url": FRONT_DOOR_URL,
        "search_endpoint": SEARCH_ENDPOINT,
        "allowed_hosts": list(ALLOWED_HOSTS),
        "query": QUERY,
        "pilot_companies": [dict(row) for row in matrix],
        "query_matrix_sha256": matrix_sha256,
        "query_group_count": QUERY_GROUP_COUNT,
        "results_per_company": RESULTS_PER_COMPANY,
        "max_unique_documents": MAX_UNIQUE_DOCUMENTS,
        "max_attempts_per_operation": MAX_ATTEMPTS_PER_OPERATION,
        "attempt_schedule": "D_Dplus1_Dplus2_one_attempt_per_local_date",
        "attempt_local_dates": [
            (start + timedelta(days=offset)).date().isoformat() for offset in range(MAX_ATTEMPTS_PER_OPERATION)
        ],
        "same_day_retry": False,
        "fallback_sources": [],
        "max_search_http_attempts": MAX_SEARCH_HTTP_ATTEMPTS,
        "max_document_http_attempts": MAX_DOCUMENT_HTTP_ATTEMPTS,
        "max_http_attempts_total": MAX_HTTP_ATTEMPTS_TOTAL,
        "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "max_artifact_bytes": MAX_ARTIFACT_BYTES,
        "http_methods": ["GET"],
        "https_verification": "required",
        "redirect_policy": "same_origin_only",
        "artifact_root": f"artifacts/milestone-005/quality-pilot/{run_id}",
        "not_before": not_before,
        "not_after": not_after,
        "quality_scope": "structural_only",
        "semantic_quality_authorized": False,
        "collection_protocol_frozen": False,
        "allow_network": True,
        "allow_credentials": False,
        "allow_database_read": False,
        "allow_database_write": False,
        "allow_reviewers": False,
        "allow_models": False,
        "allow_production_paths": False,
    }


def _expected_value(value: dict[str, object], sample_path: Path, report_path: Path) -> dict[str, object]:
    if set(value) != _AUTHORIZATION_FIELDS:
        raise QualityPilotAuthorizationError("authorization has missing or unknown fields")
    return authorization_value(
        sample_path,
        report_path,
        capability_report_sha256=_sha256(value["capability_report_sha256"], field="capability_report_sha256"),
        authorization_id=_identifier(value["authorization_id"], field="authorization_id"),
        pilot_run_id=_identifier(value["pilot_run_id"], field="pilot_run_id"),
        not_before=cast(str, value["not_before"]),
        not_after=cast(str, value["not_after"]),
    )


def load_quality_pilot_authorization(
    sample_path: str | os.PathLike[str],
    capability_report_path: str | os.PathLike[str],
    authorization_path: str | os.PathLike[str],
    checksum_path: str | os.PathLike[str],
) -> QualityPilotAuthorization:
    """Verify the canonical authorization, checksum, capability report, matrix, and limits."""
    sample = Path(sample_path)
    report = Path(capability_report_path)
    authorization_file = Path(authorization_path)
    raw = _read_regular_file(authorization_file, maximum_bytes=MAX_JSON_BYTES)
    value = _load_json_bytes(raw, label="M5 quality-pilot authorization")
    expected = _expected_value(value, sample, report)
    if value != expected or raw != _canonical_bytes(expected):
        raise QualityPilotAuthorizationError("authorization is non-canonical or drifts from the frozen boundary")
    digest = _sha256_bytes(raw)
    checksum_raw = _read_regular_file(Path(checksum_path), maximum_bytes=1024)
    expected_checksum = f"{digest}  {authorization_file.name}\n".encode("ascii")
    if checksum_raw != expected_checksum:
        raise QualityPilotAuthorizationError("authorization checksum is missing, ambiguous, or drifted")
    return QualityPilotAuthorization(
        authorization_id=cast(str, value["authorization_id"]),
        pilot_run_id=cast(str, value["pilot_run_id"]),
        not_before=_timestamp(value["not_before"], field="not_before"),
        not_after=_timestamp(value["not_after"], field="not_after"),
        artifact_root=cast(str, value["artifact_root"]),
        capability_report_sha256=cast(str, value["capability_report_sha256"]),
        sha256=digest,
        raw=raw,
    )


def seal_quality_pilot_authorization(
    sample_path: str | os.PathLike[str],
    capability_report_path: str | os.PathLike[str],
    authorization_path: str | os.PathLike[str],
    checksum_path: str | os.PathLike[str],
    *,
    capability_report_sha256: str,
    authorization_id: str,
    pilot_run_id: str,
    not_before: str,
    not_after: str,
) -> QualityPilotAuthorization:
    """Create one private authorization/checksum pair without overwriting files."""
    authorization_file = Path(authorization_path)
    checksum_file = Path(checksum_path)
    if (
        authorization_file.exists()
        or authorization_file.is_symlink()
        or checksum_file.exists()
        or checksum_file.is_symlink()
    ):
        raise QualityPilotAuthorizationError("authorization or checksum output already exists")
    if authorization_file == checksum_file:
        raise QualityPilotAuthorizationError("authorization and checksum outputs must differ")
    if authorization_file.parent != checksum_file.parent:
        raise QualityPilotAuthorizationError("authorization and checksum must share one directory")
    value = authorization_value(
        sample_path,
        capability_report_path,
        capability_report_sha256=capability_report_sha256,
        authorization_id=authorization_id,
        pilot_run_id=pilot_run_id,
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
        for path, created in ((checksum_file, checksum_created), (authorization_file, authorization_created)):
            if created:
                try:
                    path.unlink()
                except OSError:
                    pass
        raise
    return load_quality_pilot_authorization(sample_path, capability_report_path, authorization_file, checksum_file)


def preflight_quality_pilot_authorization(
    sample_path: str | os.PathLike[str],
    capability_report_path: str | os.PathLike[str],
    authorization_path: str | os.PathLike[str],
    checksum_path: str | os.PathLike[str],
    *,
    now: datetime | None = None,
    require_active: bool = False,
) -> dict[str, object]:
    """Return a zero-external-access readiness summary for one sealed pilot authorization."""
    authorization = load_quality_pilot_authorization(
        sample_path, capability_report_path, authorization_path, checksum_path
    )
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
        raise QualityPilotAuthorizationError(f"authorization window is {window_state}")
    return {
        "validated": True,
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": authorization.sha256,
        "pilot_run_id": authorization.pilot_run_id,
        "sample_sha256": EXPECTED_SAMPLE_SHA256,
        "capability_report_sha256": authorization.capability_report_sha256,
        "query_group_count": QUERY_GROUP_COUNT,
        "max_unique_documents": MAX_UNIQUE_DOCUMENTS,
        "max_http_attempts_total": MAX_HTTP_ATTEMPTS_TOTAL,
        "quality_scope": "structural_only",
        "semantic_quality_authorized": False,
        "collection_protocol_frozen": False,
        "window_state": window_state,
        "network_calls_performed": 0,
        "database_reads_performed": 0,
        "credential_variables_read": 0,
        "artifacts_created": 0,
        "reviewer_calls_performed": 0,
        "model_calls_performed": 0,
    }


__all__ = [
    "QUALITY_PILOT_AUTH_SCHEMA_VERSION",
    "QualityPilotAuthorization",
    "QualityPilotAuthorizationError",
    "authorization_value",
    "load_quality_pilot_authorization",
    "preflight_quality_pilot_authorization",
    "query_matrix",
    "seal_quality_pilot_authorization",
]
