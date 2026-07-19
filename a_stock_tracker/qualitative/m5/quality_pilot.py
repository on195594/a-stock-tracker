"""Create-only CNINFO structural-quality pilot runner for the approved M5 v1.2 boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlencode, urlsplit

from a_stock_tracker.qualitative.m5.pipeline import (
    MAX_JSON_BYTES,
    _canonical_json,
    _load_json_bytes,
    _read_regular_file,
)
from a_stock_tracker.qualitative.m5.quality_pilot_auth import (
    MAX_ARTIFACT_BYTES,
    MAX_HTTP_ATTEMPTS_TOTAL,
    MAX_RESPONSE_BYTES,
    QUALITY_PILOT_AUTH_SCHEMA_VERSION,
    QualityPilotAuthorization,
    load_quality_pilot_authorization,
)
from a_stock_tracker.qualitative.m5.source_frontdoors import FetchResult, fetch_official_frontdoor
from a_stock_tracker.paths import PROJECT_ROOT

ATTEMPT_SCHEMA_VERSION = "m5-evidence-quality-pilot-attempt-v1.2"
REPORT_SCHEMA_VERSION = "m5-evidence-quality-pilot-structural-report-v1.2"
REPORT_FILENAME = "structural-report.json"
REPORT_CHECKSUM_FILENAME = "structural-report.sha256"
_MIME_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+*-]+/[A-Za-z0-9!#$&^_.+*-]+$")
_SEARCH_RECORD_FIELDS = frozenset(
    {
        "kind",
        "ordinal",
        "code",
        "requested_url",
        "final_url",
        "status_code",
        "content_type",
        "byte_count",
        "raw_path",
        "raw_sha256",
        "http_attempts",
        "redirect_count",
        "status",
        "sanitized_error_class",
        "candidates",
    }
)
_DOCUMENT_RECORD_FIELDS = frozenset(
    {
        "kind",
        "document_id",
        "requested_url",
        "final_url",
        "status_code",
        "content_type",
        "byte_count",
        "raw_path",
        "raw_sha256",
        "http_attempts",
        "redirect_count",
        "status",
        "sanitized_error_class",
    }
)


class QualityPilotError(ValueError):
    """The pilot input, response, artifact, or call would violate the authorization."""


Fetcher = Callable[[str], FetchResult]


def _canonical_bytes(value: object) -> bytes:
    return (_canonical_json(value) + "\n").encode("utf-8")


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


def _ensure_artifact_budget(run_root: Path, additional_bytes: int) -> None:
    current_bytes = sum(path.stat().st_size for path in run_root.rglob("*") if path.is_file())
    if current_bytes + additional_bytes > MAX_ARTIFACT_BYTES:
        raise QualityPilotError("pilot artifact byte budget exceeded")


def _safe_root(authorization: QualityPilotAuthorization) -> Path:
    relative = Path(authorization.artifact_root)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != authorization.artifact_root:
        raise QualityPilotError("authorization artifact root is unsafe")
    root = PROJECT_ROOT / relative
    project = PROJECT_ROOT.resolve(strict=True)
    if project not in root.resolve(strict=False).parents:
        raise QualityPilotError("pilot artifact root escapes the project")
    cursor = PROJECT_ROOT
    for part in root.relative_to(PROJECT_ROOT).parts:
        cursor /= part
        if cursor.is_symlink():
            raise QualityPilotError("pilot artifact ancestry contains a symlink")
        if cursor.exists() and not cursor.is_dir():
            raise QualityPilotError("pilot artifact ancestry contains a non-directory")
    return root


def _make_private_tree(path: Path, run_root: Path) -> None:
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    cursor = path
    while True:
        metadata = cursor.stat(follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode) or cursor.is_symlink():
            raise QualityPilotError("pilot artifact tree is unsafe")
        cursor.chmod(0o700)
        if cursor == run_root:
            break
        cursor = cursor.parent


def _authorization_value(authorization: QualityPilotAuthorization) -> dict[str, object]:
    value = _load_json_bytes(authorization.raw, label="M5 quality-pilot authorization")
    if value.get("schema_version") != QUALITY_PILOT_AUTH_SCHEMA_VERSION:
        raise QualityPilotError("quality-pilot authorization schema drift")
    return value


def _official_url(url: str, allowed_hosts: tuple[str, ...]) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or host not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise QualityPilotError("resource URL is outside the exact approved CNINFO hosts")
    return host


def _search_url(row: Mapping[str, object], endpoint: str, allowed_hosts: tuple[str, ...]) -> str:
    _official_url(endpoint, allowed_hosts)
    params = {
        key: str(row[key]).lower() if isinstance(row[key], bool) else str(row[key])
        for key in (
            "searchkey",
            "sdate",
            "edate",
            "isfulltext",
            "sortName",
            "sortType",
            "pageNum",
            "pageSize",
            "type",
        )
    }
    return f"{endpoint}?{urlencode(params)}"


def _mime(headers_value: str | None) -> str | None:
    if headers_value is None:
        return None
    value = headers_value.split(";", 1)[0].strip().lower()
    return value if _MIME_RE.fullmatch(value) else None


def _response_metadata(result: FetchResult, *, expected_url: str, allowed_hosts: tuple[str, ...]) -> dict[str, object]:
    if result.requested_url != expected_url:
        raise QualityPilotError("transport requested URL identity drift")
    requested_host = _official_url(expected_url, allowed_hosts)
    final_host = _official_url(result.final_url, allowed_hosts)
    if requested_host != final_host:
        raise QualityPilotError("transport returned a cross-origin final URL")
    if (
        isinstance(result.http_attempts, bool)
        or result.http_attempts < 1
        or isinstance(result.redirect_count, bool)
        or result.redirect_count < 0
        or result.http_attempts != 1 + result.redirect_count
    ):
        raise QualityPilotError("transport returned invalid HTTP attempt accounting")
    if len(result.body) > MAX_RESPONSE_BYTES:
        raise QualityPilotError("transport exceeded the per-response byte limit")
    return {
        "requested_url": expected_url,
        "final_url": result.final_url,
        "status_code": result.status_code,
        "content_type": _mime(result.content_type),
        "byte_count": len(result.body),
        "http_attempts": result.http_attempts,
        "redirect_count": result.redirect_count,
    }


def _announcement_date(value: object) -> str:
    if isinstance(value, bool):
        raise QualityPilotError("announcement time has an invalid type")
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        return (
            datetime.fromtimestamp(seconds, tz=datetime.fromisoformat("2026-01-01T00:00:00+08:00").tzinfo)
            .date()
            .isoformat()
        )
    if isinstance(value, str):
        normalized = value.strip().replace("/", "-")
        if len(normalized) >= 10:
            try:
                return datetime.fromisoformat(normalized[:10]).date().isoformat()
            except ValueError as exc:
                raise QualityPilotError("announcement time is not an ISO-like date") from exc
    raise QualityPilotError("announcement time is missing")


def _document_url(adjunct_url: object, allowed_hosts: tuple[str, ...]) -> str:
    if not isinstance(adjunct_url, str) or not adjunct_url.strip():
        raise QualityPilotError("candidate adjunctUrl is missing")
    raw = adjunct_url.strip()
    if raw.startswith("http://"):
        raise QualityPilotError("candidate document URL is not HTTPS")
    if raw.startswith("https://"):
        url = raw
    else:
        decoded_path = unquote(urlsplit(raw).path)
        if raw.startswith("//") or ".." in Path(decoded_path).parts or "\\" in decoded_path:
            raise QualityPilotError("candidate document path is unsafe")
        url = "https://static.cninfo.com.cn/" + raw.lstrip("/")
    if _official_url(url, allowed_hosts) != "static.cninfo.com.cn":
        raise QualityPilotError("candidate document is not on the approved static host")
    return url


def _parse_candidates(
    raw: bytes,
    row: Mapping[str, object],
    allowed_hosts: tuple[str, ...],
) -> list[dict[str, object]]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualityPilotError("CNINFO search response is not JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("announcements"), list):
        raise QualityPilotError("CNINFO search response contract drift")
    announcements = cast(list[object], value["announcements"])
    retained = announcements[: int(cast(int, row["results_per_company"]))]
    expected_code = cast(str, row["code"]).split(".", 1)[0]
    expected_name = cast(str, row["name"])
    candidates: list[dict[str, object]] = []
    for rank, item in enumerate(retained, start=1):
        if not isinstance(item, dict):
            raise QualityPilotError("CNINFO announcement row is not an object")
        sec_code = str(item.get("secCode") or "").strip()
        sec_name = str(item.get("secName") or "").strip()
        if sec_code != expected_code or sec_name != expected_name:
            raise QualityPilotError("CNINFO retained result does not match the authorized company")
        announcement_id = str(item.get("announcementId") or "").strip()
        title = str(item.get("announcementTitle") or "").strip()
        if not announcement_id or not title:
            raise QualityPilotError("CNINFO candidate identity is incomplete")
        publication_date = _announcement_date(item.get("announcementTime"))
        if not cast(str, row["sdate"]) <= publication_date <= cast(str, row["edate"]):
            raise QualityPilotError("CNINFO candidate is outside the authorized evidence window")
        document_url = _document_url(item.get("adjunctUrl"), allowed_hosts)
        document_id = hashlib.sha256(f"{announcement_id}\n{document_url}".encode()).hexdigest()
        candidates.append(
            {
                "document_id": document_id,
                "announcement_id": announcement_id,
                "code": cast(str, row["code"]),
                "name": expected_name,
                "default_rank": rank,
                "title": title,
                "publication_date": publication_date,
                "document_url": document_url,
                "locator": f"announcementId={announcement_id}",
            }
        )
    return candidates


def _raw_record(
    attempt_root: Path,
    run_root: Path,
    result: FetchResult,
    metadata: dict[str, object],
    *,
    relative_path: str,
) -> tuple[str | None, str | None]:
    if not result.body:
        return None, None
    _ensure_artifact_budget(run_root, len(result.body))
    path = attempt_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    _secure_write(path, result.body)
    digest = hashlib.sha256(result.body).hexdigest()
    metadata["raw_path"] = relative_path
    metadata["raw_sha256"] = digest
    return relative_path, digest


def _success(result: FetchResult) -> bool:
    return (
        result.error_class is None
        and result.status_code is not None
        and 200 <= result.status_code < 300
        and bool(result.body)
    )


def _load_previous_attempts(
    run_root: Path,
    authorization: QualityPilotAuthorization,
    auth_value: Mapping[str, object],
    current_date: str,
) -> tuple[list[dict[str, object]], str | None]:
    attempts_root = run_root / "attempts"
    if not attempts_root.exists():
        return [], None
    if attempts_root.is_symlink() or not attempts_root.is_dir():
        raise QualityPilotError("attempts root is unsafe")
    allowed_dates = cast(list[str], auth_value["attempt_local_dates"])
    manifests: list[dict[str, object]] = []
    previous_sha: str | None = None
    cumulative_attempts = 0
    matrix = {
        cast(int, row["ordinal"]): {**row, "results_per_company": auth_value["results_per_company"]}
        for row in cast(list[dict[str, object]], auth_value["pilot_companies"])
    }
    allowed_hosts = tuple(cast(list[str], auth_value["allowed_hosts"]))
    endpoint = cast(str, auth_value["search_endpoint"])
    known_documents: dict[str, str] = {}
    terminal_seen = False
    for day in allowed_dates:
        if day >= current_date:
            break
        day_root = attempts_root / day.replace("-", "")
        if not day_root.exists():
            continue
        if terminal_seen:
            raise QualityPilotError("attempt exists after the run became terminal")
        if day_root.is_symlink() or not day_root.is_dir():
            raise QualityPilotError("prior attempt root is unsafe")
        manifest_path = day_root / "manifest.json"
        raw = _read_regular_file(manifest_path, maximum_bytes=MAX_JSON_BYTES)
        manifest = _load_json_bytes(raw, label="M5 quality-pilot attempt manifest")
        if raw != _canonical_bytes(manifest):
            raise QualityPilotError("prior attempt manifest is non-canonical")
        if (
            manifest.get("schema_version") != ATTEMPT_SCHEMA_VERSION
            or manifest.get("authorization_sha256") != authorization.sha256
            or manifest.get("attempt_local_date") != day
            or manifest.get("previous_attempt_manifest_sha256") != previous_sha
        ):
            raise QualityPilotError("prior attempt manifest identity or chain drift")
        records = manifest.get("records")
        if not isinstance(records, list):
            raise QualityPilotError("prior attempt records are invalid")
        day_attempts = 0
        for record in records:
            if not isinstance(record, dict):
                raise QualityPilotError("prior attempt record is invalid")
            fields = _SEARCH_RECORD_FIELDS if record.get("kind") == "search" else _DOCUMENT_RECORD_FIELDS
            if set(record) != fields:
                raise QualityPilotError("prior attempt record contract drift")
            http_attempts = record.get("http_attempts")
            redirect_count = record.get("redirect_count")
            if (
                isinstance(http_attempts, bool)
                or not isinstance(http_attempts, int)
                or http_attempts < 1
                or isinstance(redirect_count, bool)
                or not isinstance(redirect_count, int)
                or redirect_count < 0
                or http_attempts != 1 + redirect_count
            ):
                raise QualityPilotError("prior attempt has invalid HTTP accounting")
            day_attempts += http_attempts
            requested_url = record.get("requested_url")
            final_url = record.get("final_url")
            if not isinstance(requested_url, str) or not isinstance(final_url, str):
                raise QualityPilotError("prior attempt URL metadata is invalid")
            if _official_url(requested_url, allowed_hosts) != _official_url(final_url, allowed_hosts):
                raise QualityPilotError("prior attempt crossed an origin")
            raw_path = record.get("raw_path")
            raw_sha = record.get("raw_sha256")
            response_raw: bytes | None = None
            if raw_path is None:
                if raw_sha is not None or record.get("byte_count") != 0:
                    raise QualityPilotError("prior empty response metadata is inconsistent")
            else:
                if not isinstance(raw_path, str) or Path(raw_path).is_absolute() or ".." in Path(raw_path).parts:
                    raise QualityPilotError("prior raw response path is unsafe")
                response_raw = _read_regular_file(day_root / raw_path, maximum_bytes=MAX_RESPONSE_BYTES)
                if hashlib.sha256(response_raw).hexdigest() != raw_sha or len(response_raw) != record.get("byte_count"):
                    raise QualityPilotError("prior raw response hash or size drift")
            status = record.get("status")
            status_code = record.get("status_code")
            if status == "success" and (
                isinstance(status_code, bool)
                or not isinstance(status_code, int)
                or not 200 <= status_code < 300
                or response_raw is None
                or record.get("sanitized_error_class") is not None
            ):
                raise QualityPilotError("prior successful response metadata is inconsistent")
            if status != "success" and not isinstance(record.get("sanitized_error_class"), str):
                raise QualityPilotError("prior failed response metadata is inconsistent")
            if record["kind"] == "search":
                ordinal = record.get("ordinal")
                if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal not in matrix:
                    raise QualityPilotError("prior search ordinal is invalid")
                row = matrix[ordinal]
                if record.get("code") != row["code"] or requested_url != _search_url(row, endpoint, allowed_hosts):
                    raise QualityPilotError("prior search identity drift")
                if status == "success":
                    if response_raw is None:
                        raise QualityPilotError("prior successful search has no raw response")
                    candidates = _parse_candidates(response_raw, row, allowed_hosts)
                    if record.get("candidates") != candidates:
                        raise QualityPilotError("prior search candidates drift from raw response")
                    for candidate in candidates:
                        known_documents[cast(str, candidate["document_id"])] = cast(str, candidate["document_url"])
                elif status not in {"technical_error", "contract_error"} or record.get("candidates") != []:
                    raise QualityPilotError("prior search status is invalid")
            else:
                document_id = record.get("document_id")
                if not isinstance(document_id, str) or known_documents.get(document_id) != requested_url:
                    raise QualityPilotError("prior document is not bound to a verified candidate")
                if status not in {"success", "technical_error"}:
                    raise QualityPilotError("prior document status is invalid")
                if status == "success" and response_raw is None:
                    raise QualityPilotError("prior successful document has no raw response")
        cumulative_attempts += day_attempts
        if (
            manifest.get("http_attempts") != day_attempts
            or manifest.get("cumulative_http_attempts") != cumulative_attempts
            or cumulative_attempts > MAX_HTTP_ATTEMPTS_TOTAL
        ):
            raise QualityPilotError("prior manifest HTTP totals drift or exceed the budget")
        search_attempts = sum(
            cast(int, record["http_attempts"])
            for record in cast(list[dict[str, object]], manifest["records"])
            if record["kind"] == "search"
        )
        document_attempts = day_attempts - search_attempts
        prior_search_total = sum(cast(int, prior_manifest["search_http_attempts"]) for prior_manifest in manifests)
        prior_document_total = sum(cast(int, prior_manifest["document_http_attempts"]) for prior_manifest in manifests)
        if (
            manifest.get("search_http_attempts") != search_attempts
            or manifest.get("document_http_attempts") != document_attempts
            or manifest.get("cumulative_search_http_attempts") != prior_search_total + search_attempts
            or manifest.get("cumulative_document_http_attempts") != prior_document_total + document_attempts
            or prior_search_total + search_attempts > cast(int, auth_value["max_search_http_attempts"])
            or prior_document_total + document_attempts > cast(int, auth_value["max_document_http_attempts"])
        ):
            raise QualityPilotError("prior manifest category HTTP totals drift or exceed the budget")
        trial_manifests = [*manifests, manifest]
        successful_searches, successful_documents, candidates, _attempts, contract_error = _state(trial_manifests)
        all_searches = len(successful_searches) == len(matrix)
        all_documents = successful_documents == {cast(str, candidate["document_id"]) for candidate in candidates}
        if contract_error or (day == allowed_dates[-1] and not (all_searches and all_documents)):
            expected_status = "FAIL"
        elif all_searches and all_documents:
            expected_status = "COMPLETE"
        else:
            expected_status = "PROVISIONAL"
        if manifest.get("run_status") != expected_status:
            raise QualityPilotError("prior manifest run status drifts from its records")
        terminal_seen = expected_status in {"COMPLETE", "FAIL"}
        previous_sha = hashlib.sha256(raw).hexdigest()
        manifests.append(manifest)
    return manifests, previous_sha


def _state(manifests: list[dict[str, object]]) -> tuple[set[int], set[str], list[dict[str, object]], int, bool]:
    successful_searches: set[int] = set()
    successful_documents: set[str] = set()
    candidates: dict[str, dict[str, object]] = {}
    attempts = 0
    contract_error = False
    for manifest in manifests:
        attempts += cast(int, manifest["http_attempts"])
        for raw_record in cast(list[dict[str, object]], manifest["records"]):
            status = raw_record["status"]
            contract_error = contract_error or status == "contract_error"
            if raw_record["kind"] == "search" and status == "success":
                successful_searches.add(cast(int, raw_record["ordinal"]))
                for candidate in cast(list[dict[str, object]], raw_record["candidates"]):
                    candidates[cast(str, candidate["document_id"])] = candidate
            elif raw_record["kind"] == "document" and status == "success":
                successful_documents.add(cast(str, raw_record["document_id"]))
    return successful_searches, successful_documents, list(candidates.values()), attempts, contract_error


def _report_value(
    authorization: QualityPilotAuthorization,
    auth_value: Mapping[str, object],
    manifests: list[dict[str, object]],
) -> dict[str, object]:
    if not manifests or manifests[-1].get("run_status") not in {"COMPLETE", "FAIL"}:
        raise QualityPilotError("quality-pilot attempt chain is not terminal")
    successful_searches, successful_documents, candidates, attempts, _contract_error = _state(manifests)
    candidates_by_id = {cast(str, item["document_id"]): item for item in candidates}
    attempt_records: list[dict[str, object]] = []
    for manifest in manifests:
        attempt_date = cast(str, manifest["attempt_local_date"])
        for record in cast(list[dict[str, object]], manifest["records"]):
            attempt_records.append(
                {
                    **record,
                    "attempt_local_date": attempt_date,
                    "raw_path": (
                        f"attempts/{attempt_date.replace('-', '')}/{record['raw_path']}"
                        if record["raw_path"] is not None
                        else None
                    ),
                }
            )
    search_attempts = [record for record in attempt_records if record["kind"] == "search"]
    document_attempts = [record for record in attempt_records if record["kind"] == "document"]
    successful_document_rows = [record for record in document_attempts if record["status"] == "success"]
    search_http_attempts = sum(cast(int, record["http_attempts"]) for record in search_attempts)
    document_http_attempts = sum(cast(int, record["http_attempts"]) for record in document_attempts)
    final_manifest_raw = _canonical_bytes(manifests[-1])
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": authorization.sha256,
        "pilot_run_id": authorization.pilot_run_id,
        "sample_sha256": auth_value["sample_sha256"],
        "capability_report_sha256": authorization.capability_report_sha256,
        "query_matrix_sha256": auth_value["query_matrix_sha256"],
        "status": manifests[-1]["run_status"],
        "quality_scope": "structural_only",
        "semantic_quality_authorized": False,
        "collection_protocol_frozen": False,
        "query_groups_total": len(cast(list[object], auth_value["pilot_companies"])),
        "query_groups_successful": len(successful_searches),
        "unique_documents_discovered": len(candidates_by_id),
        "documents_successful": len(successful_documents),
        "documents_with_mime": sum(record["content_type"] is not None for record in successful_document_rows),
        "documents_with_raw_sha256": sum(isinstance(record["raw_sha256"], str) for record in successful_document_rows),
        "http_attempts": attempts,
        "search_http_attempts": search_http_attempts,
        "document_http_attempts": document_http_attempts,
        "final_attempt_manifest_sha256": hashlib.sha256(final_manifest_raw).hexdigest(),
        "candidates": [candidates_by_id[key] for key in sorted(candidates_by_id)],
        "search_attempts": search_attempts,
        "document_attempts": document_attempts,
        "network_calls_performed": attempts,
        "database_reads_performed": 0,
        "credential_variables_read": 0,
        "reviewer_calls_performed": 0,
        "model_calls_performed": 0,
    }


def load_quality_pilot_report(
    sample_path: str | os.PathLike[str],
    capability_report_path: str | os.PathLike[str],
    authorization_path: str | os.PathLike[str],
    checksum_path: str | os.PathLike[str],
) -> dict[str, object]:
    """Rebuild and verify the terminal report from the authorization-bound raw attempt chain."""
    authorization = load_quality_pilot_authorization(
        sample_path, capability_report_path, authorization_path, checksum_path
    )
    auth_value = _authorization_value(authorization)
    run_root = _safe_root(authorization)
    manifests, _previous_sha = _load_previous_attempts(run_root, authorization, auth_value, "9999-12-31")
    expected = _report_value(authorization, auth_value, manifests)
    report_path = run_root / REPORT_FILENAME
    raw = _read_regular_file(report_path, maximum_bytes=MAX_JSON_BYTES)
    report = _load_json_bytes(raw, label="M5 quality-pilot structural report")
    if report != expected or raw != _canonical_bytes(expected):
        raise QualityPilotError("structural report drifts from the sealed raw attempt chain")
    digest = hashlib.sha256(raw).hexdigest()
    checksum_raw = _read_regular_file(run_root / REPORT_CHECKSUM_FILENAME, maximum_bytes=1024)
    if checksum_raw != f"{digest}  {REPORT_FILENAME}\n".encode("ascii"):
        raise QualityPilotError("structural report checksum is missing, ambiguous, or drifted")
    return {
        "validated": True,
        "status": report["status"],
        "report_sha256": digest,
        "authorization_sha256": authorization.sha256,
        "final_attempt_manifest_sha256": report["final_attempt_manifest_sha256"],
        "quality_scope": "structural_only",
        "semantic_quality_authorized": False,
        "collection_protocol_frozen": False,
        "network_calls_performed": 0,
        "database_reads_performed": 0,
        "credential_variables_read": 0,
        "reviewer_calls_performed": 0,
        "model_calls_performed": 0,
    }


def run_quality_pilot_day(
    sample_path: str | os.PathLike[str],
    capability_report_path: str | os.PathLike[str],
    authorization_path: str | os.PathLike[str],
    checksum_path: str | os.PathLike[str],
    *,
    now: datetime | None = None,
    fetcher: Fetcher = fetch_official_frontdoor,
) -> dict[str, object]:
    """Run at most one attempt per pending operation on the current authorized local date."""
    authorization = load_quality_pilot_authorization(
        sample_path, capability_report_path, authorization_path, checksum_path
    )
    auth_value = _authorization_value(authorization)
    current = now or datetime.now().astimezone()
    if current.utcoffset() != timedelta(hours=8):
        current = current.astimezone(authorization.not_before.tzinfo)
    local_date = current.date().isoformat()
    allowed_dates = cast(list[str], auth_value["attempt_local_dates"])
    if local_date not in allowed_dates or current < authorization.not_before or current > authorization.not_after:
        raise QualityPilotError("quality-pilot authorization window is not active for this local date")

    run_root = _safe_root(authorization)
    day_root = run_root / "attempts" / local_date.replace("-", "")
    if day_root.exists() or day_root.is_symlink():
        raise QualityPilotError("quality-pilot attempt already exists for this local date")
    previous, previous_sha = _load_previous_attempts(run_root, authorization, auth_value, local_date)
    successful_searches, successful_documents, prior_candidates, prior_attempts, prior_contract_error = _state(previous)
    prior_search_attempts = sum(cast(int, manifest["search_http_attempts"]) for manifest in previous)
    prior_document_attempts = sum(cast(int, manifest["document_http_attempts"]) for manifest in previous)
    if prior_contract_error:
        raise QualityPilotError("quality-pilot run is already terminal after a contract error")
    report_path = run_root / REPORT_FILENAME
    report_checksum_path = run_root / REPORT_CHECKSUM_FILENAME
    if (
        report_path.exists()
        or report_path.is_symlink()
        or report_checksum_path.exists()
        or report_checksum_path.is_symlink()
    ):
        raise QualityPilotError("quality-pilot run is already terminal")

    _make_private_tree(day_root, run_root)
    records: list[dict[str, object]] = []
    daily_attempts = 0
    daily_search_attempts = 0
    daily_document_attempts = 0
    allowed_hosts = tuple(cast(list[str], auth_value["allowed_hosts"]))
    endpoint = cast(str, auth_value["search_endpoint"])
    matrix = cast(list[dict[str, object]], auth_value["pilot_companies"])
    matrix = [dict(row, results_per_company=auth_value["results_per_company"]) for row in matrix]
    contract_error = False
    candidates_by_id = {cast(str, item["document_id"]): item for item in prior_candidates}

    for row in matrix:
        ordinal = cast(int, row["ordinal"])
        if ordinal in successful_searches:
            continue
        url = _search_url(row, endpoint, allowed_hosts)
        result = fetcher(url)
        metadata = _response_metadata(result, expected_url=url, allowed_hosts=allowed_hosts)
        daily_attempts += result.http_attempts
        daily_search_attempts += result.http_attempts
        if prior_attempts + daily_attempts > MAX_HTTP_ATTEMPTS_TOTAL:
            raise QualityPilotError("quality-pilot HTTP attempt budget exceeded")
        if prior_search_attempts + daily_search_attempts > cast(int, auth_value["max_search_http_attempts"]):
            raise QualityPilotError("quality-pilot search HTTP attempt budget exceeded")
        metadata.update(
            {
                "kind": "search",
                "ordinal": ordinal,
                "code": row["code"],
                "raw_path": None,
                "raw_sha256": None,
                "status": "technical_error",
                "sanitized_error_class": result.error_class or "invalid_response",
                "candidates": [],
            }
        )
        _raw_record(day_root, run_root, result, metadata, relative_path=f"search/{ordinal:03d}.bin")
        if _success(result):
            try:
                candidates = _parse_candidates(result.body, row, allowed_hosts)
            except QualityPilotError as exc:
                metadata["status"] = "contract_error"
                metadata["sanitized_error_class"] = type(exc).__name__
                contract_error = True
            else:
                metadata["status"] = "success"
                metadata["sanitized_error_class"] = None
                metadata["candidates"] = candidates
                successful_searches.add(ordinal)
                for candidate in candidates:
                    candidates_by_id[cast(str, candidate["document_id"])] = candidate
        records.append(metadata)
        if contract_error:
            break

    if len(candidates_by_id) > cast(int, auth_value["max_unique_documents"]):
        raise QualityPilotError("quality-pilot unique-document budget exceeded")

    if not contract_error:
        for document_id, candidate in sorted(candidates_by_id.items()):
            if document_id in successful_documents:
                continue
            url = cast(str, candidate["document_url"])
            result = fetcher(url)
            metadata = _response_metadata(result, expected_url=url, allowed_hosts=allowed_hosts)
            daily_attempts += result.http_attempts
            daily_document_attempts += result.http_attempts
            if prior_attempts + daily_attempts > MAX_HTTP_ATTEMPTS_TOTAL:
                raise QualityPilotError("quality-pilot HTTP attempt budget exceeded")
            if prior_document_attempts + daily_document_attempts > cast(int, auth_value["max_document_http_attempts"]):
                raise QualityPilotError("quality-pilot document HTTP attempt budget exceeded")
            success = _success(result)
            metadata.update(
                {
                    "kind": "document",
                    "document_id": document_id,
                    "raw_path": None,
                    "raw_sha256": None,
                    "status": "success" if success else "technical_error",
                    "sanitized_error_class": None if success else result.error_class or "invalid_response",
                }
            )
            _raw_record(day_root, run_root, result, metadata, relative_path=f"documents/{document_id}.bin")
            if success:
                successful_documents.add(document_id)
            records.append(metadata)

    all_searches = len(successful_searches) == len(matrix)
    all_documents = successful_documents == set(candidates_by_id)
    last_date = local_date == allowed_dates[-1]
    if contract_error or (last_date and not (all_searches and all_documents)):
        run_status = "FAIL"
    elif all_searches and all_documents:
        run_status = "COMPLETE"
    else:
        run_status = "PROVISIONAL"
    manifest: dict[str, object] = {
        "schema_version": ATTEMPT_SCHEMA_VERSION,
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": authorization.sha256,
        "pilot_run_id": authorization.pilot_run_id,
        "attempt_local_date": local_date,
        "captured_at": current.isoformat(timespec="seconds"),
        "previous_attempt_manifest_sha256": previous_sha,
        "http_attempts": daily_attempts,
        "search_http_attempts": daily_search_attempts,
        "document_http_attempts": daily_document_attempts,
        "cumulative_http_attempts": prior_attempts + daily_attempts,
        "cumulative_search_http_attempts": prior_search_attempts + daily_search_attempts,
        "cumulative_document_http_attempts": prior_document_attempts + daily_document_attempts,
        "records": records,
        "run_status": run_status,
    }
    manifest_raw = _canonical_bytes(manifest)
    _ensure_artifact_budget(run_root, len(manifest_raw))
    _secure_write(day_root / "manifest.json", manifest_raw)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()

    report_sha256: str | None = None
    if run_status in {"COMPLETE", "FAIL"}:
        report = _report_value(authorization, auth_value, [*previous, manifest])
        report_raw = _canonical_bytes(report)
        report_sha256 = hashlib.sha256(report_raw).hexdigest()
        report_checksum_raw = f"{report_sha256}  {REPORT_FILENAME}\n".encode("ascii")
        _ensure_artifact_budget(run_root, len(report_raw) + len(report_checksum_raw))
        _secure_write(report_path, report_raw)
        _secure_write(report_checksum_path, report_checksum_raw)

    return {
        "validated": True,
        "status": run_status,
        "attempt_local_date": local_date,
        "attempt_manifest_sha256": manifest_sha,
        "query_groups_successful": len(successful_searches),
        "unique_documents_discovered": len(candidates_by_id),
        "documents_successful": len(successful_documents),
        "daily_http_attempts": daily_attempts,
        "daily_search_http_attempts": daily_search_attempts,
        "daily_document_http_attempts": daily_document_attempts,
        "cumulative_http_attempts": prior_attempts + daily_attempts,
        "report_created": run_status in {"COMPLETE", "FAIL"},
        "report_sha256": report_sha256,
        "semantic_quality_authorized": False,
        "collection_protocol_frozen": False,
        "database_reads": 0,
        "credential_variables_read": 0,
        "model_calls": 0,
    }


__all__ = [
    "ATTEMPT_SCHEMA_VERSION",
    "QualityPilotError",
    "REPORT_CHECKSUM_FILENAME",
    "REPORT_FILENAME",
    "REPORT_SCHEMA_VERSION",
    "load_quality_pilot_report",
    "run_quality_pilot_day",
]
