#!/usr/bin/env python3
"""Export the 2026-07-15 M4 frame from bounded historical/static sources."""

from __future__ import annotations

import argparse
import fcntl
import io
import json
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence
from urllib.parse import parse_qs, parse_qsl, urlsplit
from zoneinfo import ZoneInfo

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qualitative_v2_audit import (  # noqa: E402
    SW2021_INDUSTRIES,
    FrameRow,
    canonical_json_bytes,
    load_preregistration_ref,
    reject_v131_segmented_attempt,
    select_sample,
    validate_frame,
)
from scripts.export_m4_sampling_frame import (  # noqa: E402
    API_URL,
    ExportError as TushareExportError,
    TushareApiClient,
    Transport,
    _default_transport,
    _load_token,
)
from scripts.export_m4_sampling_frame_akshare import (  # noqa: E402
    AkshareRunner,
    CallResult,
    ExportError,
    ExportResult,
    Runner,
    _decimal,
    _records,
    _remove_tree,
    _render_csv,
    _render_dataframe,
    _seal_tree,
    _sha256,
    _summary_date,
    _validate_exchange_summaries,
    _write_new,
)
from scripts.export_m4_sampling_frame_akshare_szse_https import (  # noqa: E402
    SZSE_ENDPOINT,
    _parse_szse_xlsx,
)

AUTHORIZATION_ID = "qualitative-v2-m4-historical-hybrid-frame-export-2026-07-16-01"
AUTHORIZED_TUSHARE_APIS = frozenset({"daily_basic"})
AUTHORIZED_AKSHARE_FUNCTIONS = frozenset({"index_component_sw", "stock_sse_summary"})
DEFAULT_SAMPLING_DATE = date(2026, 7, 15)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "milestone-004" / "incoming"
DEFAULT_SZSE_PACKAGE = DEFAULT_OUTPUT_ROOT / "manual-szse"
PROTOCOL_PATH = (
    PROJECT_ROOT / "docs" / "plans" / "2026-07-15-milestone-004-evidence-feasibility-preregistration-v1.1.md"
)
PROTOCOL_HASH_PATH = PROJECT_ROOT / "reviews" / "milestone-004-preregistration-v1.1" / "preregistration.sha256"
BASE_GENERATORS = (
    Path(__file__),
    PROJECT_ROOT / "scripts" / "export_m4_sampling_frame.py",
    PROJECT_ROOT / "scripts" / "export_m4_sampling_frame_akshare.py",
    PROJECT_ROOT / "scripts" / "export_m4_sampling_frame_akshare_szse_https.py",
)
CAPTURE_SCHEMA_VERSION = "m4-historical-capture-v1"
AUTHORIZATION_SCHEMA_VERSION = "m4-capture-authorization-v1"
RECEIPT_SCHEMA_VERSION = "m4-raw-capture-receipt-v1"
MANIFEST_SCHEMA_VERSION = "m4-capture-manifest-v1"
ASSEMBLY_SCHEMA_VERSION = "m4-frame-provenance-v2"
MAX_CAPTURE_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_CAPTURE_TOTAL_BYTES = 256 * 1024 * 1024
CAPTURE_CODE_PATHS = BASE_GENERATORS
CALL_ID_DAILY_BASIC = "tushare:daily_basic:20260715"
CALL_ID_SSE_SUMMARY = "akshare:stock_sse_summary"


def _component_call_id(industry_code: str) -> str:
    return f"akshare:index_component_sw:{industry_code.removesuffix('.SI')}"


FROZEN_CALL_IDS = (
    CALL_ID_DAILY_BASIC,
    CALL_ID_SSE_SUMMARY,
    *(_component_call_id(code) for code in SW2021_INDUSTRIES),
)


@dataclass(frozen=True, slots=True)
class CaptureAuthorization:
    """Canonical, pre-existing authority for one bounded capture attempt."""

    authorization_id: str
    attempt_id: str
    sampling_date: date
    not_before: datetime
    not_after: datetime
    allowed_calls: tuple[str, ...]
    sha256: str
    path: Path
    document: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RawCaptureReceipt:
    """Sanitized provenance for one immutable provider response."""

    call_id: str
    provider: str
    function_name: str
    request_arguments: Mapping[str, str]
    request_method: str
    request_url: str
    request_query: tuple[tuple[str, str], ...]
    response_url: str
    status_code: int
    content_type: str
    captured_at: datetime
    byte_count: int
    blob_sha256: str
    blob_relative_path: str
    parse_status: str
    parse_error_class: str | None
    source_attempt_id: str
    authorization_id: str


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """Published location and identity of a sealed capture attempt."""

    attempt_dir: Path
    manifest_path: Path
    manifest_sha256: str
    attempt_id: str
    complete: bool
    captured_call_ids: tuple[str, ...]
    missing_call_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssemblyResult:
    """Published location and identity of a deterministic offline frame."""

    package_dir: Path
    frame_path: Path
    provenance_path: Path
    frame_sha256: str
    sampling_date: date
    row_count: int
    sample_count: int


@dataclass(frozen=True, slots=True)
class CapturedResponse:
    """Exact response bytes plus a credential-free transport description."""

    request_url: str
    response_url: str
    status_code: int
    content_type: str
    raw: bytes
    request_params: tuple[tuple[str, str], ...] = ()
    parsed_rows: tuple[Mapping[str, object], ...] | None = None


class CaptureBackend(Protocol):
    """Provider adapter used by capture; tests inject a network-free implementation."""

    def capture(self, call_id: str, request: Mapping[str, object]) -> CapturedResponse: ...


class CaptureBlockedError(ExportError):
    """Raised after a fail-closed attempt has been sealed when possible."""

    def __init__(self, message: str, result: CaptureResult | None = None) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True, slots=True)
class StaticSzsePackage:
    source_date: date
    dataframe: Any
    files: tuple[Path, ...]
    provenance: Mapping[str, Any]
    semantic_sha256: str


class HistoricalAkshareRunner:
    """Expose only the two AKShare functions authorized for this export."""

    def __init__(self, delegate: Runner | None = None) -> None:
        self._delegate = delegate or AkshareRunner()
        self.version = self._delegate.version

    def call(self, function_name: str, arguments: Mapping[str, str]) -> CallResult:
        if function_name not in AUTHORIZED_AKSHARE_FUNCTIONS:
            raise ExportError(f"AKShare function is not authorized by this historical export: {function_name}")
        return self._delegate.call(function_name, arguments)


def _parse_iso_date(value: object, *, label: str) -> date:
    text = str(value).strip()
    for pattern in (r"(\d{4})-(\d{2})-(\d{2})", r"(\d{4})(\d{2})(\d{2})"):
        matched = re.fullmatch(pattern, text)
        if matched is not None:
            try:
                return date(*(int(part) for part in matched.groups()))
            except ValueError as exc:
                raise ExportError(f"invalid date for {label}") from exc
    raise ExportError(f"invalid date for {label}")


def _read_checksum_manifest(package: Path) -> Mapping[str, str]:
    manifest = package / "SHA256SUMS"
    if manifest.is_symlink() or not manifest.is_file():
        raise ExportError("static SZSE package is missing SHA256SUMS")
    result: dict[str, str] = {}
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ExportError("static SZSE SHA256SUMS is unreadable") from exc
    for line in lines:
        matched = re.fullmatch(r"([0-9a-f]{64})  ([^\\/]+)", line)
        if matched is None or matched.group(2) in result or matched.group(2) == "SHA256SUMS":
            raise ExportError("static SZSE SHA256SUMS has an invalid entry")
        result[matched.group(2)] = matched.group(1)
    if not result or "provenance.json" not in result or "ShowReport.xlsx" not in result:
        raise ExportError("static SZSE SHA256SUMS is incomplete")
    actual_names = {path.name for path in package.iterdir() if path.is_file() and path.name != "SHA256SUMS"}
    if actual_names != set(result):
        raise ExportError("static SZSE package file set differs from SHA256SUMS")
    return result


def _validate_static_szse_package(package: Path, sampling_date: date) -> StaticSzsePackage:
    if package.is_symlink() or not package.is_dir():
        raise ExportError("static SZSE package must be a real directory")
    resolved_package = package.resolve(strict=True)
    checksums = _read_checksum_manifest(package)
    files: list[Path] = []
    for filename, expected_sha256 in sorted(checksums.items()):
        path = package / filename
        if path.is_symlink() or not path.is_file() or path.resolve(strict=True).parent != resolved_package:
            raise ExportError("static SZSE package contains an unsafe file")
        raw = path.read_bytes()
        if _sha256(raw) != expected_sha256:
            raise ExportError(f"SZSE checksum drift: {filename}")
        files.append(path)

    provenance_path = package / "provenance.json"
    provenance_raw = provenance_path.read_bytes()
    try:
        value = json.loads(provenance_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError("static SZSE provenance is invalid JSON") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != provenance_raw:
        raise ExportError("static SZSE provenance is not canonical JSON")
    if value.get("schema_version") != "m4-manual-szse-capture-v1" or value.get("publisher") != "深圳证券交易所":
        raise ExportError("static SZSE provenance identity drift")
    if value.get("source_date") != sampling_date.isoformat():
        raise ExportError("static SZSE source-date drift")
    request = value.get("request")
    if not isinstance(request, dict) or not isinstance(request.get("parameters"), dict):
        raise ExportError("static SZSE request provenance is incomplete")
    request_url = str(request.get("url", ""))
    parts = urlsplit(request_url)
    query = parse_qs(parts.query, keep_blank_values=True)
    parameters = request["parameters"]
    if (
        parts.scheme != "https"
        or parts.hostname != "www.szse.cn"
        or parts.path != urlsplit(SZSE_ENDPOINT).path
        or parameters.get("SHOWTYPE") != "xlsx"
        or parameters.get("CATALOGID") != "1803_sczm"
        or parameters.get("TABKEY") != "tab1"
        or parameters.get("txtQueryDate") != sampling_date.isoformat()
        or query.get("txtQueryDate") != [sampling_date.isoformat()]
    ):
        raise ExportError("static SZSE request escaped the approved date-bound HTTPS endpoint")
    acquisition = value.get("acquisition")
    validation = value.get("validation")
    if (
        not isinstance(acquisition, dict)
        or acquisition.get("final_origin") != "https://www.szse.cn"
        or acquisition.get("redirect_policy") != "reject_cross_domain"
        or not isinstance(validation, dict)
        or validation.get("cross_domain_redirect_observed") is not False
        or validation.get("xlsx_magic_valid") is not True
        or validation.get("xlsx_zip_integrity_valid") is not True
    ):
        raise ExportError("static SZSE transport/integrity provenance is invalid")

    declared_files = value.get("files")
    if not isinstance(declared_files, list) or not declared_files:
        raise ExportError("static SZSE provenance omitted file records")
    declared_by_name: dict[str, Mapping[str, Any]] = {}
    for record in declared_files:
        if not isinstance(record, dict) or not isinstance(record.get("filename"), str):
            raise ExportError("static SZSE provenance contains an invalid file record")
        filename = record["filename"]
        if filename in declared_by_name:
            raise ExportError("static SZSE provenance contains duplicate file records")
        declared_by_name[filename] = record
    xlsx_names = sorted(name for name in checksums if name.lower().endswith(".xlsx") or name == "download")
    if set(declared_by_name) != set(xlsx_names):
        raise ExportError("static SZSE provenance/file records differ")

    parsed_frames = []
    semantic_hashes = []
    for filename in xlsx_names:
        path = package / filename
        raw = path.read_bytes()
        record = declared_by_name[filename]
        if record.get("sha256") != checksums[filename] or record.get("byte_count") != len(raw):
            raise ExportError(f"static SZSE file provenance drift: {filename}")
        dataframe = _parse_szse_xlsx(raw)
        try:
            source_dataframe = pd.read_excel(io.BytesIO(raw), engine="openpyxl")
        except Exception as exc:
            raise ExportError(f"static SZSE semantic parse failure: {type(exc).__name__}") from exc
        if (
            record.get("row_count") != len(source_dataframe.index)
            or record.get("format") != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ):
            raise ExportError(f"static SZSE schema provenance drift: {filename}")
        semantic_raw = source_dataframe.to_csv(index=False, lineterminator="\n").encode("utf-8")
        parsed_frames.append(dataframe)
        semantic_hashes.append(_sha256(semantic_raw))
    if len(set(semantic_hashes)) != 1:
        raise ExportError("static SZSE XLSX files are not semantically identical")
    declared_semantic = validation.get("normalized_table_sha256")
    if declared_semantic not in {"synthetic", semantic_hashes[0]}:
        raise ExportError("static SZSE normalized table hash drift")
    return StaticSzsePackage(sampling_date, parsed_frames[0], tuple(files), value, semantic_hashes[0])


def _component_date(value: object, *, ts_code: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return _parse_iso_date(value, label=f"SWS beginning date/{ts_code}")


def _generator_records(paths: Sequence[Path]) -> list[Mapping[str, object]]:
    result: list[Mapping[str, object]] = []
    for path in paths:
        if path.is_symlink():
            raise ExportError("generator path may not be a symlink")
        resolved = path.resolve(strict=True)
        if PROJECT_ROOT not in resolved.parents or not resolved.is_file():
            raise ExportError("generator path must be a real project file")
        result.append(
            {"relative_path": str(resolved.relative_to(PROJECT_ROOT)), "sha256": _sha256(resolved.read_bytes())}
        )
    return result


def _validate_attempt_id(attempt_id: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,95}", attempt_id):
        raise ExportError("attempt ID must be 1-96 safe lowercase ASCII characters")
    return attempt_id


def _ensure_real_root(root: Path, *, create: bool) -> Path:
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise ExportError("capture/output root must be a real directory")
    return root.resolve(strict=True)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_create_only(path: Path, raw: bytes, *, mode: int = 0o444) -> None:
    """Publish complete bytes without ever replacing an existing path."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise ExportError("artifact parent may not be a symlink")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.chmod(mode)
        try:
            os.link(temp_path, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise ExportError(f"create-only artifact already exists: {path.name}") from exc
        _fsync_directory(path.parent)
    finally:
        temp_path.unlink(missing_ok=True)


def _publish_content_addressed_blob(path: Path, raw: bytes) -> None:
    """Create a blob once, accepting only a byte-identical existing hash path."""
    try:
        _atomic_create_only(path, raw)
    except ExportError:
        if not path.exists() or path.is_symlink():
            raise
        existing, _ = _secure_read(path, maximum=MAX_CAPTURE_RESPONSE_BYTES)
        if existing != raw or path.name != f"{_sha256(raw)}.blob":
            raise


def _secure_read(path: Path, *, maximum: int | None = None) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ExportError(f"unsafe or unreadable artifact: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ExportError(f"artifact is not a regular file: {path.name}")
        if maximum is not None and before.st_size > maximum:
            raise ExportError(f"artifact exceeds size limit: {path.name}")
        chunks: list[bytes] = []
        remaining = maximum + 1 if maximum is not None else None
        while True:
            chunk = os.read(descriptor, 64 * 1024 if remaining is None else min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            if remaining is not None:
                remaining -= len(chunk)
                if remaining <= 0:
                    raise ExportError(f"artifact exceeds size limit: {path.name}")
        after = os.fstat(descriptor)
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if identity_before != identity_after:
            raise ExportError(f"artifact changed while being read: {path.name}")
        return b"".join(chunks), after
    finally:
        os.close(descriptor)


@contextmanager
def _capture_root_lock(root: Path) -> Iterator[None]:
    resolved = _ensure_real_root(root, create=True)
    lock_path = resolved / ".capture.lock"
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ExportError("capture root is locked by another capture/recover/assemble process") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _parse_authorization_time(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ExportError(f"authorization {field} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ExportError(f"authorization {field} must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExportError(f"authorization {field} must be timezone-aware")
    return parsed


def _read_hash_manifest(path: Path, expected_filename: str) -> str:
    raw, _ = _secure_read(path, maximum=4096)
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ExportError("authorization hash manifest must be ASCII") from exc
    matched = re.fullmatch(r"([0-9a-f]{64})(?:  ([^/\\\r\n]+))?\n?", text)
    if matched is None or (matched.group(2) is not None and matched.group(2) != expected_filename):
        raise ExportError("authorization hash manifest is invalid")
    return matched.group(1)


def load_capture_authorization(
    authorization_path: Path,
    authorization_hash_path: Path,
    *,
    attempt_id: str,
    sampling_date: date,
    now: datetime,
) -> CaptureAuthorization:
    """Load and bind a canonical authorization created outside this tool."""
    _validate_attempt_id(attempt_id)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ExportError("capture timestamp must be timezone-aware")
    raw, _ = _secure_read(authorization_path, maximum=1024 * 1024)
    expected_hash = _read_hash_manifest(authorization_hash_path, authorization_path.name)
    actual_hash = _sha256(raw)
    if actual_hash != expected_hash:
        raise ExportError("authorization JSON hash does not match its manifest")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError("authorization is invalid JSON") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise ExportError("authorization JSON must be canonical")
    exact_fields = {
        "schema_version",
        "authorization_id",
        "attempt_id",
        "sampling_date",
        "not_before",
        "not_after",
        "allowed_calls",
    }
    if set(value) != exact_fields or value.get("schema_version") != AUTHORIZATION_SCHEMA_VERSION:
        raise ExportError("authorization schema or fields differ from the frozen contract")
    authorization_id = value.get("authorization_id")
    if not isinstance(authorization_id, str) or not authorization_id or len(authorization_id) > 200:
        raise ExportError("authorization ID is missing or invalid")
    if value.get("attempt_id") != attempt_id or value.get("sampling_date") != sampling_date.isoformat():
        raise ExportError("authorization attempt or sampling date does not match the CLI request")
    calls = value.get("allowed_calls")
    if not isinstance(calls, list) or not all(isinstance(item, str) for item in calls):
        raise ExportError("authorization call matrix is invalid")
    if tuple(calls) != FROZEN_CALL_IDS or len(set(calls)) != len(FROZEN_CALL_IDS):
        raise ExportError("authorization call matrix differs from the frozen 33 calls")
    not_before = _parse_authorization_time(value.get("not_before"), field="not_before")
    not_after = _parse_authorization_time(value.get("not_after"), field="not_after")
    now_utc = now.astimezone(timezone.utc)
    if not_before.astimezone(timezone.utc) > now_utc or now_utc > not_after.astimezone(timezone.utc):
        raise ExportError("authorization is not currently valid")
    if not_before >= not_after:
        raise ExportError("authorization time window is invalid")
    return CaptureAuthorization(
        authorization_id,
        attempt_id,
        sampling_date,
        not_before,
        not_after,
        tuple(calls),
        actual_hash,
        authorization_path.resolve(strict=True),
        value,
    )


def _read_capture_clock(clock: Callable[[], datetime]) -> datetime:
    captured_at = clock()
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ExportError("capture clock must return a timezone-aware datetime")
    return captured_at.astimezone(ZoneInfo("Asia/Shanghai"))


def _require_authorization_current(authorization: CaptureAuthorization, current_time: datetime) -> None:
    current_utc = current_time.astimezone(timezone.utc)
    if current_utc < authorization.not_before.astimezone(
        timezone.utc
    ) or current_utc > authorization.not_after.astimezone(timezone.utc):
        raise ExportError("authorization expired or is not yet valid during live capture")


def _request_for_call(call_id: str, sampling_date: date) -> Mapping[str, object]:
    if call_id == CALL_ID_DAILY_BASIC:
        return {
            "provider": "tushare",
            "function_name": "daily_basic",
            "method": "POST",
            "url": API_URL,
            "arguments": {"trade_date": sampling_date.strftime("%Y%m%d")},
            "fields": ("ts_code", "trade_date", "total_mv"),
        }
    if call_id == CALL_ID_SSE_SUMMARY:
        return {
            "provider": "akshare",
            "function_name": "stock_sse_summary",
            "method": "GET",
            "arguments": {},
        }
    prefix = "akshare:index_component_sw:"
    if call_id.startswith(prefix):
        symbol = call_id.removeprefix(prefix)
        expected = {code.removesuffix(".SI") for code in SW2021_INDUSTRIES}
        if symbol not in expected:
            raise ExportError("SWS symbol is outside the frozen SW2021 set")
        return {
            "provider": "akshare",
            "function_name": "index_component_sw",
            "method": "GET",
            "arguments": {"symbol": symbol},
        }
    raise ExportError(f"call ID is outside the frozen matrix: {call_id}")


def _validate_capture_transport(call_id: str, request: Mapping[str, object], response: CapturedResponse) -> None:
    if isinstance(response.status_code, bool) or not 200 <= response.status_code < 300:
        raise ExportError(f"provider returned non-success status for {call_id}")
    if len(response.raw) > MAX_CAPTURE_RESPONSE_BYTES:
        raise ExportError(f"response exceeds 32 MiB for {call_id}")
    request_parts = urlsplit(response.request_url)
    response_parts = urlsplit(response.response_url)
    if request_parts.scheme != "https" or response_parts.scheme != "https":
        raise ExportError(f"non-HTTPS transport for {call_id}")
    if any(part.username is not None or part.password is not None for part in (request_parts, response_parts)):
        raise ExportError(f"credential-bearing URL for {call_id}")
    if request_parts.port not in (None, 443) or response_parts.port not in (None, 443):
        raise ExportError(f"non-default HTTPS port for {call_id}")
    provider = request["provider"]
    allowed_host = (
        "api.tushare.pro"
        if provider == "tushare"
        else ("query.sse.com.cn" if call_id == CALL_ID_SSE_SUMMARY else "www.swsresearch.com")
    )
    if request_parts.hostname != allowed_host or response_parts.hostname != allowed_host:
        raise ExportError(f"request or redirect escaped the approved host for {call_id}")
    if provider == "tushare":
        if request.get("method") != "POST" or response.request_params:
            raise ExportError("Tushare transport must use one body-only POST")
        if response.request_url != API_URL or response.response_url != API_URL:
            raise ExportError("Tushare request URL or redirect policy drift")
    else:
        arguments = request.get("arguments")
        if call_id == CALL_ID_SSE_SUMMARY and arguments != {}:
            raise ExportError("SSE top-level arguments must be empty")
        if call_id != CALL_ID_SSE_SUMMARY:
            symbol = str(cast_mapping(arguments).get("symbol", ""))
            if symbol not in {code.removesuffix(".SI") for code in SW2021_INDUSTRIES}:
                raise ExportError("SWS symbol is outside the frozen set")
        url_query = tuple(parse_qsl(request_parts.query, keep_blank_values=True))
        if url_query and response.request_params and url_query != response.request_params:
            raise ExportError("third-party request query provenance is inconsistent")
        query = response.request_params or url_query
        sensitive = re.compile(r"token|secret|password|api[_-]?key|authorization|cookie", re.IGNORECASE)
        if any(sensitive.search(key) for key, _ in query):
            raise ExportError("third-party query contains a credential-like key")
        if len({key for key, _ in query}) != len(query):
            raise ExportError("third-party query contains duplicate parameters")
        if call_id == CALL_ID_SSE_SUMMARY:
            expected_query = {
                "sqlId": "COMMON_SSE_SJ_GPSJ_GPSJZM_TJSJ_L",
                "PRODUCT_NAME": "股票,主板,科创板",
                "type": "inParams",
            }
            if request_parts.path != "/commonQuery.do" or dict(query) != expected_query:
                raise ExportError("SSE read-only query parameters differ from the frozen adapter")
        else:
            symbol = str(cast_mapping(arguments).get("symbol", ""))
            expected_query = {"swindexcode": symbol, "page": "1", "page_size": "10000"}
            if (
                request_parts.path != "/institute-sw/api/index_publish/details/component_stocks/"
                or dict(query) != expected_query
            ):
                raise ExportError("SWS read-only query parameters differ from the frozen adapter")


def cast_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ExportError("request arguments must be a mapping")
    return value


class HistoricalCaptureBackend:
    """Current bounded provider adapters, with exact bytes returned to capture."""

    def __init__(
        self,
        *,
        token: str,
        runner: Runner | None = None,
        tushare_transport: Transport = _default_transport,
        min_interval_seconds: float = 1.3,
    ) -> None:
        self._capture_sink: Callable[[str, Mapping[str, object], CapturedResponse], Mapping[str, object]] | None = None
        self._active_call_id: str | None = None
        self._active_request: Mapping[str, object] | None = None
        self._prepublished: dict[str, Mapping[str, object]] = {}
        delegate = runner if runner is not None else AkshareRunner(capture_callback=self._capture_akshare_raw)
        self._runner = HistoricalAkshareRunner(delegate)
        self._tushare = TushareApiClient(
            token,
            transport=tushare_transport,
            min_interval_seconds=min_interval_seconds,
            capture_callback=self._capture_tushare_raw,
        )

    def set_capture_sink(
        self,
        sink: Callable[[str, Mapping[str, object], CapturedResponse], Mapping[str, object]],
    ) -> None:
        self._capture_sink = sink

    def take_prepublished(self, call_id: str) -> Mapping[str, object] | None:
        return self._prepublished.pop(call_id, None)

    def _publish_early(self, response: CapturedResponse) -> None:
        if self._capture_sink is None or self._active_call_id is None or self._active_request is None:
            raise ExportError("raw capture sink was not bound before provider parsing")
        if self._active_call_id in self._prepublished:
            raise ExportError(f"provider issued multiple responses for one frozen call: {self._active_call_id}")
        self._prepublished[self._active_call_id] = self._capture_sink(
            self._active_call_id,
            self._active_request,
            response,
        )

    def _capture_tushare_raw(self, raw: bytes) -> None:
        self._publish_early(CapturedResponse(API_URL, API_URL, 200, "application/json", raw))

    def _capture_akshare_raw(self, function_name: str, capture: Any) -> None:
        if self._active_request is None or function_name != self._active_request.get("function_name"):
            raise ExportError("AKShare raw response does not match the active frozen call")
        self._publish_early(
            CapturedResponse(
                capture.request_url,
                capture.response_url,
                capture.status_code,
                capture.content_type,
                capture.raw,
                tuple((str(key), str(value)) for key, value in capture.request_params.items()),
            )
        )

    def capture(self, call_id: str, request: Mapping[str, object]) -> CapturedResponse:
        self._active_call_id = call_id
        self._active_request = request
        if call_id == CALL_ID_DAILY_BASIC:
            tushare_arguments = cast_mapping(request["arguments"])
            fields = request["fields"]
            if not isinstance(fields, tuple) or not all(isinstance(field, str) for field in fields):
                raise ExportError("invalid frozen Tushare fields")
            snapshot = self._tushare.call(
                "daily_basic",
                {str(key): str(value) for key, value in tushare_arguments.items()},
                fields,
            )
            return CapturedResponse(
                API_URL,
                API_URL,
                200,
                "application/json",
                snapshot.raw,
                parsed_rows=snapshot.rows,
            )
        function_name = str(request["function_name"])
        akshare_arguments = {str(key): str(value) for key, value in cast_mapping(request["arguments"]).items()}
        call = self._runner.call(function_name, akshare_arguments)
        if call.function_name != function_name or dict(call.arguments) != akshare_arguments:
            raise ExportError(f"AKShare adapter returned mismatched provenance for {call_id}")
        if len(call.captures) != 1:
            raise ExportError(f"AKShare {call_id} must issue exactly one HTTP response")
        capture = call.captures[0]
        records = tuple(_records(call, tuple(str(column) for column in call.dataframe.columns)))
        return CapturedResponse(
            capture.request_url,
            capture.response_url,
            capture.status_code,
            capture.content_type,
            capture.raw,
            tuple((str(key), str(value)) for key, value in capture.request_params.items()),
            records,
        )


def _json_rows(raw: bytes, *, call_id: str) -> tuple[Mapping[str, object], ...]:
    try:
        value = json.loads(raw.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError(f"{call_id} returned invalid UTF-8 JSON") from exc
    candidate: object = value
    if isinstance(value, dict):
        for key in ("rows", "result", "data"):
            if key in value:
                candidate = value[key]
                break
    if isinstance(candidate, dict):
        if isinstance(candidate.get("rows"), list):
            candidate = candidate["rows"]
        elif isinstance(candidate.get("items"), list) and isinstance(candidate.get("fields"), list):
            fields = candidate["fields"]
            items = candidate["items"]
            if not all(isinstance(field, str) for field in fields):
                raise ExportError(f"{call_id} returned invalid field names")
            candidate = [dict(zip(fields, item, strict=True)) for item in items if isinstance(item, list)]
    if not isinstance(candidate, list) or not all(isinstance(row, dict) for row in candidate):
        raise ExportError(f"{call_id} response does not contain a row array")
    return tuple(candidate)


def _parse_captured_rows(call_id: str, response: CapturedResponse) -> tuple[Mapping[str, object], ...]:
    if response.parsed_rows is not None:
        rows = response.parsed_rows
    elif call_id == CALL_ID_SSE_SUMMARY:
        try:
            value = json.loads(response.raw.decode("utf-8"), parse_float=Decimal)
            result = value["result"]
            dataframe = pd.DataFrame(result).T
            dataframe.reset_index(inplace=True)
            dataframe["index"] = [
                "流通股本",
                "总市值",
                "平均市盈率",
                "上市公司",
                "上市股票",
                "流通市值",
                "报告时间",
                "-",
                "总股本",
                "项目",
            ]
            dataframe = dataframe[dataframe["index"] != "-"].iloc[:-1, :]
            dataframe.columns = ["项目", "股票", "主板", "科创板"]
            rows = tuple(dataframe.to_dict(orient="records"))
        except Exception as exc:
            raise ExportError("SSE summary raw schema is invalid") from exc
    elif call_id.startswith("akshare:index_component_sw:"):
        try:
            value = json.loads(response.raw.decode("utf-8"), parse_float=Decimal)
            results = value["data"]["results"]
        except (TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExportError(f"SWS component raw schema is invalid for {call_id}") from exc
        if not isinstance(results, list) or not all(isinstance(row, dict) for row in results):
            raise ExportError(f"SWS component raw rows are invalid for {call_id}")
        rows = tuple(
            {
                "证券代码": row.get("stockcode"),
                "证券名称": row.get("stockname"),
                "计入日期": row.get("beginningdate"),
            }
            for row in results
        )
    else:
        rows = _json_rows(response.raw, call_id=call_id)
    if call_id == CALL_ID_DAILY_BASIC:
        # Reparse raw even when the live adapter supplied rows. This proves raw-only promotion.
        try:
            value = json.loads(response.raw.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExportError("daily_basic returned invalid UTF-8 JSON") from exc
        if not isinstance(value, dict) or value.get("code") != 0 or not isinstance(value.get("data"), dict):
            raise ExportError("daily_basic response envelope is invalid")
        data = value["data"]
        if set(data) - {"fields", "items", "count", "has_more"} or not {"fields", "items"} <= set(data):
            raise ExportError("daily_basic data schema is invalid")
        fields = data["fields"]
        items = data["items"]
        if (
            not isinstance(fields, list)
            or fields != ["ts_code", "trade_date", "total_mv"]
            or not isinstance(items, list)
            or data.get("has_more") is True
        ):
            raise ExportError("daily_basic fields or pagination is invalid")
        count = data.get("count")
        if count is not None and (
            isinstance(count, bool) or not isinstance(count, int) or count < 0 or (count != 0 and count < len(items))
        ):
            raise ExportError("daily_basic pagination count is invalid")
        parsed: list[Mapping[str, object]] = []
        for item in items:
            if not isinstance(item, list) or len(item) != len(fields):
                raise ExportError("daily_basic returned a malformed row")
            parsed.append(dict(zip(fields, item, strict=True)))
        rows = tuple(parsed)
    if call_id == CALL_ID_SSE_SUMMARY:
        matches = [row for row in rows if str(row.get("项目", "")).strip() == "报告时间"]
        if len(matches) != 1:
            raise ExportError("SSE summary must contain exactly one report-time row")
    elif call_id.startswith("akshare:index_component_sw:"):
        if not rows or any(not {"证券代码", "证券名称", "计入日期"} <= set(row) for row in rows):
            raise ExportError(f"SWS component schema is invalid for {call_id}")
    return rows


def _receipt_filename(call_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", call_id) + ".json"


def _receipt_value(receipt: RawCaptureReceipt) -> Mapping[str, object]:
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "call_id": receipt.call_id,
        "provider": receipt.provider,
        "function_name": receipt.function_name,
        "request": {
            "arguments": dict(receipt.request_arguments),
            "method": receipt.request_method,
            "url": receipt.request_url,
            "query": [[key, value] for key, value in receipt.request_query],
        },
        "response": {
            "url": receipt.response_url,
            "status_code": receipt.status_code,
            "content_type": receipt.content_type,
            "captured_at": receipt.captured_at.isoformat(),
            "byte_count": receipt.byte_count,
            "sha256": receipt.blob_sha256,
            "blob_relative_path": receipt.blob_relative_path,
        },
        "parsing": {"status": receipt.parse_status, "error_class": receipt.parse_error_class},
        "origin": {
            "attempt_id": receipt.source_attempt_id,
            "authorization_id": receipt.authorization_id,
        },
    }


def _publish_response(
    staging: Path,
    *,
    call_id: str,
    request: Mapping[str, object],
    response: CapturedResponse,
    captured_at: datetime,
    attempt_id: str,
    authorization_id: str,
    token: str | None,
) -> Mapping[str, object]:
    _validate_capture_transport(call_id, request, response)
    if token and token.encode("utf-8") in response.raw:
        raise ExportError(f"provider response echoed the Tushare token for {call_id}")
    blob_sha256 = _sha256(response.raw)
    blob_relative_path = f"blobs/{blob_sha256}.blob"
    _publish_content_addressed_blob(staging / blob_relative_path, response.raw)
    parse_status = "parsed"
    parse_error: str | None = None
    try:
        _parse_captured_rows(call_id, response)
    except ExportError as exc:
        parse_status = "raw_only"
        parse_error = type(exc).__name__
    receipt = RawCaptureReceipt(
        call_id=call_id,
        provider=str(request["provider"]),
        function_name=str(request["function_name"]),
        request_arguments={str(key): str(value) for key, value in cast_mapping(request["arguments"]).items()},
        request_method=str(request["method"]),
        request_url=response.request_url,
        request_query=response.request_params
        or tuple(parse_qsl(urlsplit(response.request_url).query, keep_blank_values=True)),
        response_url=response.response_url,
        status_code=response.status_code,
        content_type=response.content_type,
        captured_at=captured_at,
        byte_count=len(response.raw),
        blob_sha256=blob_sha256,
        blob_relative_path=blob_relative_path,
        parse_status=parse_status,
        parse_error_class=parse_error,
        source_attempt_id=attempt_id,
        authorization_id=authorization_id,
    )
    receipt_raw = canonical_json_bytes(_receipt_value(receipt))
    receipt_relative_path = f"receipts/{_receipt_filename(call_id)}"
    _atomic_create_only(staging / receipt_relative_path, receipt_raw)
    return {
        "call_id": call_id,
        "receipt_relative_path": receipt_relative_path,
        "receipt_sha256": _sha256(receipt_raw),
        "blob_relative_path": blob_relative_path,
        "blob_sha256": blob_sha256,
        "byte_count": len(response.raw),
        "parse_status": parse_status,
        "origin_attempt_id": attempt_id,
        "origin_authorization_id": authorization_id,
        "captured_at": captured_at.isoformat(),
    }


def _authorization_manifest_value(authorization: CaptureAuthorization) -> Mapping[str, object]:
    document = dict(authorization.document)
    return {
        **document,
        "authorization_sha256": authorization.sha256,
        "canonical_document": document,
    }


def _static_manifest_value(package: Path, sampling_date: date) -> Mapping[str, object]:
    validated = _validate_static_szse_package(package, sampling_date)
    records: list[Mapping[str, object]] = []
    for path in (*validated.files, package / "SHA256SUMS"):
        raw, _ = _secure_read(path)
        records.append({"filename": path.name, "byte_count": len(raw), "sha256": _sha256(raw)})
    return {
        "root": str(package.resolve(strict=True)),
        "source_date": sampling_date.isoformat(),
        "semantic_sha256": validated.semantic_sha256,
        "files": records,
    }


def _code_manifest_value() -> list[Mapping[str, object]]:
    return _generator_records(CAPTURE_CODE_PATHS)


def _manifest_value(
    *,
    attempt_id: str,
    sampling_date: date,
    complete: bool,
    calls: Sequence[Mapping[str, object]],
    missing_calls: Sequence[str],
    authorization: CaptureAuthorization | None,
    static_szse: Mapping[str, object] | None,
    parent_manifest_sha256: str | None,
    parent_lineage: Sequence[Mapping[str, object]],
    failure_class: str | None,
    recovered: bool = False,
    orphan_files: Sequence[Mapping[str, object]] = (),
) -> Mapping[str, object]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "capture_schema_version": CAPTURE_SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "sampling_date": sampling_date.isoformat(),
        "complete": complete,
        "authorization": _authorization_manifest_value(authorization) if authorization is not None else None,
        "expected_call_ids": list(FROZEN_CALL_IDS),
        "calls": list(calls),
        "missing_call_ids": list(missing_calls),
        "parent_manifest_sha256": parent_manifest_sha256,
        "resume_lineage": list(parent_lineage),
        "static_szse": static_szse,
        "capture_code": _code_manifest_value(),
        "failure_class": failure_class,
        "recovered_staging": recovered,
        "orphan_files": list(orphan_files),
    }


def _safe_relative_path(value: object, *, prefix: str) -> str:
    if not isinstance(value, str):
        raise ExportError("manifest path must be a string")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not value.startswith(prefix + "/"):
        raise ExportError("manifest contains path traversal")
    return value


def _verify_receipt(attempt_dir: Path, item: Mapping[str, object]) -> tuple[Mapping[str, object], bytes]:
    receipt_relative = _safe_relative_path(item.get("receipt_relative_path"), prefix="receipts")
    blob_relative = _safe_relative_path(item.get("blob_relative_path"), prefix="blobs")
    receipt_raw, _ = _secure_read(attempt_dir / receipt_relative, maximum=1024 * 1024)
    if _sha256(receipt_raw) != item.get("receipt_sha256"):
        raise ExportError("capture receipt hash drift")
    try:
        receipt = json.loads(receipt_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError("capture receipt is invalid JSON") from exc
    if not isinstance(receipt, dict) or canonical_json_bytes(receipt) != receipt_raw:
        raise ExportError("capture receipt is not canonical JSON")
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION or receipt.get("call_id") != item.get("call_id"):
        raise ExportError("capture receipt identity drift")
    response = receipt.get("response")
    if not isinstance(response, dict) or response.get("blob_relative_path") != blob_relative:
        raise ExportError("capture receipt blob reference drift")
    blob_raw, _ = _secure_read(attempt_dir / blob_relative, maximum=MAX_CAPTURE_RESPONSE_BYTES)
    if (
        _sha256(blob_raw) != item.get("blob_sha256")
        or _sha256(blob_raw) != response.get("sha256")
        or len(blob_raw) != item.get("byte_count")
        or len(blob_raw) != response.get("byte_count")
    ):
        raise ExportError("capture blob hash or size drift")
    if Path(blob_relative).name != f"{_sha256(blob_raw)}.blob":
        raise ExportError("capture blob is not content-addressed by its SHA-256")
    return receipt, blob_raw


def _verify_static_from_manifest(value: object, *, sampling_date: date, override: Path | None = None) -> Path:
    if not isinstance(value, dict) or not isinstance(value.get("root"), str):
        raise ExportError("capture manifest omitted static SZSE provenance")
    declared_root = Path(value["root"])
    package = override if override is not None else declared_root
    if package.resolve(strict=True) != declared_root.resolve(strict=True):
        raise ExportError("static SZSE root differs from captured provenance")
    validated = _validate_static_szse_package(package, sampling_date)
    if (
        value.get("source_date") != sampling_date.isoformat()
        or value.get("semantic_sha256") != validated.semantic_sha256
    ):
        raise ExportError("static SZSE semantic provenance drift")
    records = value.get("files")
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ExportError("static SZSE file records are invalid")
    declared = {str(item.get("filename")): item for item in records}
    actual_paths = (*validated.files, package / "SHA256SUMS")
    if set(declared) != {path.name for path in actual_paths}:
        raise ExportError("static SZSE file set differs from capture manifest")
    for path in actual_paths:
        raw, _ = _secure_read(path)
        record = declared[path.name]
        if record.get("sha256") != _sha256(raw) or record.get("byte_count") != len(raw):
            raise ExportError(f"static SZSE captured hash drift: {path.name}")
    return package


def _verify_code_records(value: object) -> None:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ExportError("capture code records are invalid")
    expected = {str(item["relative_path"]): str(item["sha256"]) for item in _code_manifest_value()}
    actual = {str(item.get("relative_path")): str(item.get("sha256")) for item in value}
    if actual != expected:
        raise ExportError("capture/assemble code hash drift")


def _verify_attempt(
    attempt_dir: Path,
    *,
    require_complete: bool,
    szse_package: Path | None = None,
) -> tuple[Mapping[str, object], str, list[Mapping[str, object]]]:
    reject_v131_segmented_attempt(attempt_dir)
    if (attempt_dir / "capability-probe-manifest.json").exists():
        raise ExportError("non-adoptable capability probe cannot be assembled or resumed")
    if attempt_dir.name.startswith(".in-progress-") or attempt_dir.is_symlink() or not attempt_dir.is_dir():
        raise ExportError("only a sealed attempt directory is accepted")
    directory_before = attempt_dir.stat(follow_symlinks=False)
    manifest_names = [
        name for name in ("capture-manifest.json", "incomplete-capture-manifest.json") if (attempt_dir / name).exists()
    ]
    if len(manifest_names) != 1:
        raise ExportError("sealed attempt must contain exactly one fixed manifest name")
    manifest_name = manifest_names[0]
    raw, _ = _secure_read(attempt_dir / manifest_name, maximum=8 * 1024 * 1024)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError("capture manifest is invalid JSON") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise ExportError("capture manifest is not canonical JSON")
    if (
        value.get("schema_version") != MANIFEST_SCHEMA_VERSION
        or value.get("capture_schema_version") != CAPTURE_SCHEMA_VERSION
    ):
        raise ExportError("capture manifest schema drift")
    complete = value.get("complete")
    if not isinstance(complete, bool) or complete != (manifest_name == "capture-manifest.json"):
        raise ExportError("capture manifest filename/complete status mismatch")
    if require_complete and not complete:
        raise ExportError("assemble accepts only a complete capture")
    attempt_id = value.get("attempt_id")
    if not isinstance(attempt_id, str):
        raise ExportError("capture manifest omitted attempt ID")
    _validate_attempt_id(attempt_id)
    try:
        sampling_date = date.fromisoformat(str(value.get("sampling_date")))
    except ValueError as exc:
        raise ExportError("capture manifest sampling date is invalid") from exc
    if tuple(value.get("expected_call_ids", ())) != FROZEN_CALL_IDS:
        raise ExportError("capture expected-call matrix drift")
    calls = value.get("calls")
    if not isinstance(calls, list) or not all(isinstance(item, dict) for item in calls):
        raise ExportError("capture call records are invalid")
    call_ids = [item.get("call_id") for item in calls]
    if len(call_ids) != len(set(call_ids)) or any(item not in FROZEN_CALL_IDS for item in call_ids):
        raise ExportError("capture contains duplicate or extra calls")
    missing = value.get("missing_call_ids")
    if not isinstance(missing, list) or missing != [item for item in FROZEN_CALL_IDS if item not in call_ids]:
        raise ExportError("capture missing-call matrix is inconsistent")
    if complete and tuple(call_ids) != FROZEN_CALL_IDS:
        raise ExportError("complete capture does not contain the exact 33 calls in order")
    _verify_authorization_binding(value, require_current=complete)
    expected_files = {manifest_name}
    for item in calls:
        receipt_relative = _safe_relative_path(item.get("receipt_relative_path"), prefix="receipts")
        blob_relative = _safe_relative_path(item.get("blob_relative_path"), prefix="blobs")
        expected_files.update({receipt_relative, blob_relative})
        _verify_receipt(attempt_dir, item)
    orphan_files = value.get("orphan_files")
    if not isinstance(orphan_files, list) or not all(isinstance(item, dict) for item in orphan_files):
        raise ExportError("capture orphan-file records are invalid")
    for item in orphan_files:
        relative = _safe_relative_path(item.get("relative_path"), prefix="blobs")
        orphan_raw, _ = _secure_read(attempt_dir / relative, maximum=MAX_CAPTURE_RESPONSE_BYTES)
        if item.get("sha256") != _sha256(orphan_raw) or item.get("byte_count") != len(orphan_raw):
            raise ExportError("orphan blob hash drift")
        expected_files.add(relative)
    actual_files: set[str] = set()
    for path in attempt_dir.rglob("*"):
        if path.is_symlink():
            raise ExportError("capture attempt contains a symlink")
        if path.is_file():
            actual_files.add(path.relative_to(attempt_dir).as_posix())
    if actual_files != expected_files:
        raise ExportError("capture attempt contains unknown, missing, or orphan files")
    _verify_code_records(value.get("capture_code"))
    _verify_static_from_manifest(value.get("static_szse"), sampling_date=sampling_date, override=szse_package)
    directory_after = attempt_dir.stat(follow_symlinks=False)
    before_identity = (
        directory_before.st_dev,
        directory_before.st_ino,
        directory_before.st_mtime_ns,
        directory_before.st_ctime_ns,
    )
    after_identity = (
        directory_after.st_dev,
        directory_after.st_ino,
        directory_after.st_mtime_ns,
        directory_after.st_ctime_ns,
    )
    if before_identity != after_identity:
        raise ExportError("capture attempt changed during verification")
    return value, _sha256(raw), calls


def _seal_and_publish_attempt(
    root: Path,
    staging: Path,
    *,
    manifest_value: Mapping[str, object],
    complete: bool,
) -> CaptureResult:
    manifest_name = "capture-manifest.json" if complete else "incomplete-capture-manifest.json"
    manifest_raw = canonical_json_bytes(manifest_value)
    _atomic_create_only(staging / manifest_name, manifest_raw)
    attempt_id = str(manifest_value["attempt_id"])
    suffix = "" if complete else "-incomplete"
    final = root / f"capture-{attempt_id}{suffix}"
    if final.exists() or final.is_symlink():
        raise ExportError("sealed capture attempt already exists")
    _seal_tree(staging)
    os.rename(staging, final)
    _fsync_directory(root)
    calls = manifest_value["calls"]
    missing = manifest_value["missing_call_ids"]
    assert isinstance(calls, list) and isinstance(missing, list)
    return CaptureResult(
        final,
        final / manifest_name,
        _sha256(manifest_raw),
        attempt_id,
        complete,
        tuple(str(item["call_id"]) for item in calls),
        tuple(str(item) for item in missing),
    )


def _response_from_receipt(receipt: Mapping[str, object], raw: bytes) -> CapturedResponse:
    request = receipt.get("request")
    response = receipt.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise ExportError("receipt request/response description is invalid")
    query = request.get("query")
    if not isinstance(query, list) or not all(
        isinstance(pair, list) and len(pair) == 2 and all(isinstance(item, str) for item in pair) for pair in query
    ):
        raise ExportError("receipt query provenance is invalid")
    status_code = response.get("status_code")
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        raise ExportError("receipt status code is invalid")
    return CapturedResponse(
        str(request.get("url", "")),
        str(response.get("url", "")),
        status_code,
        str(response.get("content_type", "")),
        raw,
        tuple((pair[0], pair[1]) for pair in query),
    )


def capture_historical_sources(
    *,
    capture_root: Path,
    attempt_id: str,
    now: datetime,
    sampling_date: date = DEFAULT_SAMPLING_DATE,
    szse_package: Path = DEFAULT_SZSE_PACKAGE,
    authorization_path: Path | None = None,
    authorization_hash_path: Path | None = None,
    backend: CaptureBackend | None = None,
    token: str | None = None,
    runner: Runner | None = None,
    tushare_transport: Transport = _default_transport,
    min_interval_seconds: float = 1.3,
    resume_attempt: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> CaptureResult:
    """Capture the exact frozen response matrix before any downstream assembly."""
    _validate_attempt_id(attempt_id)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ExportError("capture timestamp must be timezone-aware")
    if sampling_date != DEFAULT_SAMPLING_DATE:
        raise ExportError("capture sampling date differs from the frozen historical date")
    root = _ensure_real_root(capture_root, create=True)
    capture_clock = clock or (lambda: datetime.now(tz=ZoneInfo("Asia/Shanghai")))
    with _capture_root_lock(root):
        parent_value: Mapping[str, object] | None = None
        parent_hash: str | None = None
        parent_calls: list[Mapping[str, object]] = []
        if resume_attempt is not None:
            if resume_attempt.resolve(strict=True).parent != root:
                raise ExportError("resume attempt must be a sealed child of the locked capture root")
            parent_value, parent_hash, parent_calls = _verify_attempt(
                resume_attempt,
                require_complete=False,
                szse_package=szse_package,
            )
            if parent_value.get("sampling_date") != sampling_date.isoformat():
                raise ExportError("resume attempt sampling date differs")
            _verify_resume_chain(resume_attempt, parent_value)
        parent_copy_stat = resume_attempt.stat(follow_symlinks=False) if resume_attempt is not None else None
        parent_copy_identity = (
            (
                parent_copy_stat.st_dev,
                parent_copy_stat.st_ino,
                parent_copy_stat.st_mtime_ns,
                parent_copy_stat.st_ctime_ns,
            )
            if parent_copy_stat is not None
            else None
        )

        reusable: dict[str, tuple[Mapping[str, object], Mapping[str, object], bytes]] = {}
        for item in parent_calls:
            receipt, raw = _verify_receipt(resume_attempt, item)  # type: ignore[arg-type]
            call_id = str(item["call_id"])
            try:
                response = _response_from_receipt(receipt, raw)
                request = _request_for_call(call_id, sampling_date)
                _validate_capture_transport(call_id, request, response)
                _parse_captured_rows(call_id, response)
            except ExportError:
                continue
            reusable[call_id] = (item, receipt, raw)
        missing_before_network = [call_id for call_id in FROZEN_CALL_IDS if call_id not in reusable]

        authorization: CaptureAuthorization | None = None
        if authorization_path is not None or authorization_hash_path is not None:
            if authorization_path is None or authorization_hash_path is None:
                raise ExportError("authorization JSON and hash manifest must be supplied together")
            authorization = load_capture_authorization(
                authorization_path,
                authorization_hash_path,
                attempt_id=attempt_id,
                sampling_date=sampling_date,
                now=now,
            )
        if missing_before_network and authorization is None:
            raise ExportError("missing or invalid calls require a new valid authorization; no network was attempted")

        static_value = _static_manifest_value(szse_package, sampling_date)
        active_backend = backend
        active_token = token
        if missing_before_network and active_backend is None:
            active_token = active_token if active_token is not None else _load_token()
            active_backend = HistoricalCaptureBackend(
                token=active_token,
                runner=runner,
                tushare_transport=tushare_transport,
                min_interval_seconds=min_interval_seconds,
            )

        staging = root / f".in-progress-{attempt_id}"
        try:
            staging.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise ExportError("attempt staging directory already exists; use recover, never resume it") from exc
        if isinstance(active_backend, HistoricalCaptureBackend):
            assert authorization is not None

            def publish_early(
                call_id: str,
                request: Mapping[str, object],
                response: CapturedResponse,
            ) -> Mapping[str, object]:
                captured_at = _read_capture_clock(capture_clock)
                _require_authorization_current(authorization, captured_at)
                return _publish_response(
                    staging,
                    call_id=call_id,
                    request=request,
                    response=response,
                    captured_at=captured_at,
                    attempt_id=attempt_id,
                    authorization_id=authorization.authorization_id,
                    token=active_token,
                )

            active_backend.set_capture_sink(publish_early)
        call_records: list[Mapping[str, object]] = []
        total_bytes = 0
        parent_lineage: list[Mapping[str, object]] = []
        if parent_value is not None and parent_hash is not None:
            parent_authorization = parent_value.get("authorization")
            parent_lineage.append(
                {
                    "attempt_id": parent_value.get("attempt_id"),
                    "authorization_id": (
                        parent_authorization.get("authorization_id") if isinstance(parent_authorization, dict) else None
                    ),
                    "manifest_sha256": parent_hash,
                }
            )
            existing_lineage = parent_value.get("resume_lineage")
            if isinstance(existing_lineage, list):
                parent_lineage.extend(item for item in existing_lineage if isinstance(item, dict))
        active_call_id: str | None = None
        try:
            for call_id in FROZEN_CALL_IDS:
                active_call_id = call_id
                if call_id in reusable:
                    old_item, _, raw = reusable[call_id]
                    blob_relative = _safe_relative_path(old_item["blob_relative_path"], prefix="blobs")
                    receipt_relative = _safe_relative_path(old_item["receipt_relative_path"], prefix="receipts")
                    receipt_raw, _ = _secure_read(resume_attempt / receipt_relative)  # type: ignore[operator]
                    if _sha256(receipt_raw) != old_item.get("receipt_sha256"):
                        raise ExportError("resume receipt changed between verification and copy")
                    _publish_content_addressed_blob(staging / blob_relative, raw)
                    _atomic_create_only(staging / receipt_relative, receipt_raw)
                    copied = dict(old_item)
                    copied["offline_validation_status"] = "parsed"
                    call_records.append(copied)
                    total_bytes += len(raw)
                    continue
                assert authorization is not None and active_backend is not None
                request = _request_for_call(call_id, sampling_date)
                _require_authorization_current(authorization, _read_capture_clock(capture_clock))
                try:
                    response = active_backend.capture(call_id, request)
                except Exception:
                    prepublished = (
                        active_backend.take_prepublished(call_id)
                        if isinstance(active_backend, HistoricalCaptureBackend)
                        else None
                    )
                    if prepublished is None:
                        raise
                    prepublished_bytes = prepublished.get("byte_count")
                    if isinstance(prepublished_bytes, bool) or not isinstance(prepublished_bytes, int):
                        raise ExportError("prepublished capture byte count is invalid")
                    total_bytes += prepublished_bytes
                    call_records.append(prepublished)
                    if total_bytes > MAX_CAPTURE_TOTAL_BYTES:
                        raise ExportError("capture responses exceed the 256 MiB aggregate limit")
                    continue
                prepublished = (
                    active_backend.take_prepublished(call_id)
                    if isinstance(active_backend, HistoricalCaptureBackend)
                    else None
                )
                total_bytes += len(response.raw)
                if prepublished is not None:
                    call_records.append(prepublished)
                else:
                    if total_bytes > MAX_CAPTURE_TOTAL_BYTES:
                        raise ExportError("capture responses exceed the 256 MiB aggregate limit")
                    captured_at = _read_capture_clock(capture_clock)
                    _require_authorization_current(authorization, captured_at)
                    call_records.append(
                        _publish_response(
                            staging,
                            call_id=call_id,
                            request=request,
                            response=response,
                            captured_at=captured_at,
                            attempt_id=attempt_id,
                            authorization_id=authorization.authorization_id,
                            token=active_token,
                        )
                    )
                if total_bytes > MAX_CAPTURE_TOTAL_BYTES:
                    raise ExportError("capture responses exceed the 256 MiB aggregate limit")
            if resume_attempt is not None and parent_copy_identity is not None:
                parent_after = resume_attempt.stat(follow_symlinks=False)
                if parent_copy_identity != (
                    parent_after.st_dev,
                    parent_after.st_ino,
                    parent_after.st_mtime_ns,
                    parent_after.st_ctime_ns,
                ):
                    raise ExportError("resume attempt changed during verified copy")
            missing = [
                call_id for call_id in FROZEN_CALL_IDS if call_id not in {item["call_id"] for item in call_records}
            ]
            if missing:
                raise ExportError("capture ended without the exact frozen call matrix")
            manifest = _manifest_value(
                attempt_id=attempt_id,
                sampling_date=sampling_date,
                complete=True,
                calls=call_records,
                missing_calls=(),
                authorization=authorization,
                static_szse=static_value,
                parent_manifest_sha256=parent_hash,
                parent_lineage=parent_lineage,
                failure_class=None,
            )
            return _seal_and_publish_attempt(root, staging, manifest_value=manifest, complete=True)
        except BaseException as exc:
            if staging.exists():
                if isinstance(active_backend, HistoricalCaptureBackend) and active_call_id is not None:
                    prepublished = active_backend.take_prepublished(active_call_id)
                    recorded_call_ids = {str(item["call_id"]) for item in call_records}
                    if prepublished is not None and active_call_id not in recorded_call_ids:
                        call_records.append(prepublished)
                missing = [
                    call_id for call_id in FROZEN_CALL_IDS if call_id not in {item["call_id"] for item in call_records}
                ]
                referenced = {str(item["blob_relative_path"]) for item in call_records}
                orphan_records: list[Mapping[str, object]] = []
                blobs_dir = staging / "blobs"
                if blobs_dir.is_dir() and not blobs_dir.is_symlink():
                    for blob_path in sorted(blobs_dir.iterdir()):
                        relative = blob_path.relative_to(staging).as_posix()
                        if relative in referenced:
                            continue
                        orphan_raw, _ = _secure_read(blob_path, maximum=MAX_CAPTURE_RESPONSE_BYTES)
                        orphan_records.append(
                            {
                                "relative_path": relative,
                                "sha256": _sha256(orphan_raw),
                                "byte_count": len(orphan_raw),
                            }
                        )
                try:
                    incomplete_manifest = _manifest_value(
                        attempt_id=attempt_id,
                        sampling_date=sampling_date,
                        complete=False,
                        calls=call_records,
                        missing_calls=missing,
                        authorization=authorization,
                        static_szse=static_value,
                        parent_manifest_sha256=parent_hash,
                        parent_lineage=parent_lineage,
                        failure_class=type(exc).__name__,
                        orphan_files=orphan_records,
                    )
                    result = _seal_and_publish_attempt(
                        root,
                        staging,
                        manifest_value=incomplete_manifest,
                        complete=False,
                    )
                except BaseException:
                    raise exc
                if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                    raise
                raise CaptureBlockedError(str(exc), result) from exc
            raise


def recover_incomplete_capture(
    *,
    capture_root: Path,
    attempt_id: str,
    sampling_date: date = DEFAULT_SAMPLING_DATE,
    szse_package: Path = DEFAULT_SZSE_PACKAGE,
) -> CaptureResult:
    """Seal a crash-left staging directory as incomplete; never resume or assemble it."""
    _validate_attempt_id(attempt_id)
    if sampling_date != DEFAULT_SAMPLING_DATE:
        raise ExportError("recovery sampling date differs from the frozen historical date")
    root = _ensure_real_root(capture_root, create=False)
    with _capture_root_lock(root):
        staging = root / f".in-progress-{attempt_id}"
        if staging.is_symlink() or not staging.is_dir():
            raise ExportError("stale staging attempt does not exist as a real directory")
        call_records: list[Mapping[str, object]] = []
        referenced_blobs: set[str] = set()
        receipt_paths = sorted((staging / "receipts").glob("*.json")) if (staging / "receipts").is_dir() else []
        for receipt_path in receipt_paths:
            if receipt_path.is_symlink():
                raise ExportError("staging receipt may not be a symlink")
            raw, _ = _secure_read(receipt_path, maximum=1024 * 1024)
            try:
                receipt = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(receipt, dict) or canonical_json_bytes(receipt) != raw:
                continue
            call_id = receipt.get("call_id")
            response = receipt.get("response")
            if not isinstance(call_id, str) or call_id not in FROZEN_CALL_IDS or not isinstance(response, dict):
                continue
            blob_relative = response.get("blob_relative_path")
            if not isinstance(blob_relative, str):
                continue
            item = {
                "call_id": call_id,
                "receipt_relative_path": receipt_path.relative_to(staging).as_posix(),
                "receipt_sha256": _sha256(raw),
                "blob_relative_path": blob_relative,
                "blob_sha256": response.get("sha256"),
                "byte_count": response.get("byte_count"),
                "parse_status": cast_mapping(receipt.get("parsing")).get("status"),
                "origin_attempt_id": cast_mapping(receipt.get("origin")).get("attempt_id"),
                "origin_authorization_id": cast_mapping(receipt.get("origin")).get("authorization_id"),
                "captured_at": response.get("captured_at"),
            }
            if call_id in {str(record["call_id"]) for record in call_records}:
                continue
            try:
                _verify_receipt(staging, item)
            except ExportError:
                continue
            referenced_blobs.add(blob_relative)
            call_records.append(item)
        call_records.sort(key=lambda item: FROZEN_CALL_IDS.index(str(item["call_id"])))
        orphan_records: list[Mapping[str, object]] = []
        blobs_dir = staging / "blobs"
        if blobs_dir.exists():
            if blobs_dir.is_symlink() or not blobs_dir.is_dir():
                raise ExportError("staging blobs path is unsafe")
            for path in sorted(blobs_dir.iterdir()):
                if path.is_symlink() or not path.is_file():
                    raise ExportError("staging blobs contain an unsafe entry")
                relative = path.relative_to(staging).as_posix()
                raw, _ = _secure_read(path, maximum=MAX_CAPTURE_RESPONSE_BYTES)
                if relative not in referenced_blobs:
                    orphan_records.append({"relative_path": relative, "sha256": _sha256(raw), "byte_count": len(raw)})
        allowed_files = (
            {str(item["receipt_relative_path"]) for item in call_records}
            | referenced_blobs
            | {str(item["relative_path"]) for item in orphan_records}
        )
        actual_files = {
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        if any(path.is_symlink() for path in staging.rglob("*")) or actual_files != allowed_files:
            raise ExportError("stale staging contains unverified or unknown files")
        missing = [call_id for call_id in FROZEN_CALL_IDS if call_id not in {item["call_id"] for item in call_records}]
        manifest = _manifest_value(
            attempt_id=attempt_id,
            sampling_date=sampling_date,
            complete=False,
            calls=call_records,
            missing_calls=missing,
            authorization=None,
            static_szse=_static_manifest_value(szse_package, sampling_date),
            parent_manifest_sha256=None,
            parent_lineage=(),
            failure_class="RecoveredStaleStaging",
            recovered=True,
            orphan_files=orphan_records,
        )
        return _seal_and_publish_attempt(root, staging, manifest_value=manifest, complete=False)


def _verify_authorization_binding(value: Mapping[str, object], *, require_current: bool) -> None:
    authorization = value.get("authorization")
    lineage = value.get("resume_lineage")
    if authorization is None:
        if require_current and (not isinstance(lineage, list) or not lineage):
            raise ExportError("capture has neither authorization nor resume lineage")
        return
    if not isinstance(authorization, dict):
        raise ExportError("capture authorization binding is invalid")
    if (
        authorization.get("attempt_id") != value.get("attempt_id")
        or authorization.get("sampling_date") != value.get("sampling_date")
        or tuple(authorization.get("allowed_calls", ())) != FROZEN_CALL_IDS
        or not re.fullmatch(r"[0-9a-f]{64}", str(authorization.get("authorization_sha256", "")))
    ):
        raise ExportError("capture authorization binding drift")
    document = authorization.get("canonical_document")
    if not isinstance(document, dict) or _sha256(canonical_json_bytes(document)) != authorization.get(
        "authorization_sha256"
    ):
        raise ExportError("captured authorization canonical hash drift")
    if any(document.get(key) != authorization.get(key) for key in document if key != "schema_version"):
        raise ExportError("captured authorization document differs from its binding")
    if document.get("schema_version") != AUTHORIZATION_SCHEMA_VERSION:
        raise ExportError("captured authorization schema drift")
    not_before = _parse_authorization_time(authorization.get("not_before"), field="not_before")
    not_after = _parse_authorization_time(authorization.get("not_after"), field="not_after")
    if not_before >= not_after:
        raise ExportError("captured authorization window is invalid")
    calls = value.get("calls")
    if not isinstance(calls, list):
        raise ExportError("capture calls are invalid")
    for item in calls:
        if isinstance(item, dict) and item.get("origin_attempt_id") == value.get("attempt_id"):
            if item.get("origin_authorization_id") != authorization.get("authorization_id"):
                raise ExportError("call receipt authorization provenance drift")


def _verify_resume_chain(attempt_dir: Path, manifest: Mapping[str, object]) -> None:
    lineage = manifest.get("resume_lineage")
    if not isinstance(lineage, list) or not all(isinstance(item, dict) for item in lineage):
        raise ExportError("resume lineage is invalid")
    parent_hash = manifest.get("parent_manifest_sha256")
    if parent_hash is None:
        if lineage:
            raise ExportError("resume lineage exists without a parent hash")
        return
    if not lineage or lineage[0].get("manifest_sha256") != parent_hash:
        raise ExportError("parent manifest hash is not the head of resume lineage")
    seen_attempts: set[str] = set()
    for record in lineage:
        attempt_id = record.get("attempt_id")
        expected_hash = record.get("manifest_sha256")
        if not isinstance(attempt_id, str) or attempt_id in seen_attempts:
            raise ExportError("resume lineage has a duplicate or invalid attempt ID")
        seen_attempts.add(attempt_id)
        candidates = [
            attempt_dir.parent / f"capture-{attempt_id}",
            attempt_dir.parent / f"capture-{attempt_id}-incomplete",
        ]
        existing = [path for path in candidates if path.is_dir() and not path.is_symlink()]
        if len(existing) != 1:
            raise ExportError("resume lineage attempt is missing or ambiguous")
        parent_value, actual_hash, _ = _verify_attempt(existing[0], require_complete=False)
        if actual_hash != expected_hash:
            raise ExportError("resume lineage manifest hash drift")
        parent_auth = parent_value.get("authorization")
        actual_auth_id = parent_auth.get("authorization_id") if isinstance(parent_auth, dict) else None
        if record.get("authorization_id") != actual_auth_id:
            raise ExportError("resume lineage authorization ID drift")


def _rows_from_verified_call(
    attempt_dir: Path,
    item: Mapping[str, object],
    sampling_date: date,
) -> tuple[Mapping[str, object], ...]:
    receipt, raw = _verify_receipt(attempt_dir, item)
    call_id = str(item["call_id"])
    response = _response_from_receipt(receipt, raw)
    request = _request_for_call(call_id, sampling_date)
    receipt_request = cast_mapping(receipt.get("request"))
    if receipt_request.get("method") != request.get("method"):
        raise ExportError(f"request method drift for {call_id}")
    arguments = receipt_request.get("arguments")
    expected_arguments = request.get("arguments")
    if arguments != expected_arguments:
        raise ExportError(f"request argument drift for {call_id}")
    _validate_capture_transport(call_id, request, response)
    return _parse_captured_rows(call_id, response)


def _strict_decimal(value: object, *, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ExportError(f"missing or invalid decimal for {label}")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ExportError(f"missing or invalid decimal for {label}") from exc
    if not number.is_finite() or number <= 0:
        raise ExportError(f"non-positive or non-finite decimal for {label}")
    return number


def _assemble_rows(
    attempt_dir: Path,
    calls: Sequence[Mapping[str, object]],
    *,
    sampling_date: date,
    minimum_market_rows: int,
) -> tuple[list[FrameRow], list[Mapping[str, object]], list[Mapping[str, object]]]:
    indexed = {str(item["call_id"]): item for item in calls}
    daily_rows: dict[str, Decimal] = {}
    for row in _rows_from_verified_call(attempt_dir, indexed[CALL_ID_DAILY_BASIC], sampling_date):
        ts_code = str(row.get("ts_code", ""))
        if not re.fullmatch(r"\d{6}\.(SH|SZ)", ts_code):
            raise ExportError(f"daily_basic returned an invalid security code: {ts_code}")
        if _parse_iso_date(row.get("trade_date"), label=f"daily_basic/{ts_code}") != sampling_date:
            raise ExportError(f"daily_basic date drift for {ts_code}")
        if ts_code in daily_rows:
            raise ExportError(f"daily_basic returned duplicate code: {ts_code}")
        daily_rows[ts_code] = _strict_decimal(row.get("total_mv"), label=f"daily_basic/{ts_code}")
    if len(daily_rows) < minimum_market_rows:
        raise ExportError("daily_basic did not return a plausible full SSE/SZSE A-share market")

    sse_rows = _rows_from_verified_call(attempt_dir, indexed[CALL_ID_SSE_SUMMARY], sampling_date)
    report_rows = [row for row in sse_rows if str(row.get("项目", "")).strip() == "报告时间"]
    if len(report_rows) != 1 or _parse_iso_date(report_rows[0].get("股票"), label="SSE report date") != sampling_date:
        raise ExportError("SSE source-date drift")
    market_rows = [row for row in sse_rows if str(row.get("项目", "")).strip() == "总市值"]
    if len(market_rows) != 1:
        raise ExportError("SSE summary omitted total market value")
    _strict_decimal(market_rows[0].get("股票"), label="SSE total market value")

    memberships: dict[str, tuple[str, str, str, date]] = {}
    for industry_code, industry_name in SW2021_INDUSTRIES.items():
        call_id = _component_call_id(industry_code)
        rows = _rows_from_verified_call(attempt_dir, indexed[call_id], sampling_date)
        if not rows:
            raise ExportError(f"SWS component source returned no rows for {industry_code}")
        for row in rows:
            code = str(row.get("证券代码", "")).strip()
            if not re.fullmatch(r"[036]\d{5}", code):
                raise ExportError(f"SWS component returned an invalid security code: {code}")
            ts_code = f"{code}.SH" if code.startswith("6") else f"{code}.SZ"
            name = str(row.get("证券名称", "")).strip()
            if not name:
                raise ExportError(f"SWS component returned an empty name: {ts_code}")
            beginning_date = _component_date(row.get("计入日期"), ts_code=ts_code)
            if beginning_date > sampling_date:
                raise ExportError(f"SWS component begins after sampling date: {ts_code}")
            if ts_code in memberships:
                previous = memberships[ts_code]
                raise ExportError(
                    f"SWS component membership is not unique: {ts_code} in {previous[0]} and {industry_code}"
                )
            memberships[ts_code] = (industry_code, industry_name, name, beginning_date)
    if len(memberships) < minimum_market_rows:
        raise ExportError("SWS component snapshot did not return a plausible full SW2021 frame")

    frame_rows: list[FrameRow] = []
    csv_rows: list[Mapping[str, object]] = []
    exclusions: list[Mapping[str, object]] = []
    for ts_code, (industry_code, industry_name, name, _) in sorted(memberships.items()):
        total_mv = daily_rows.get(ts_code)
        if total_mv is None:
            exclusions.append({"ts_code": ts_code, "name": name, "reason": "missing_20260715_daily_basic"})
            continue
        exchange = "SSE" if ts_code.endswith(".SH") else "SZSE"
        frame_rows.append(FrameRow(ts_code, name, industry_code, industry_name, total_mv, sampling_date, exchange))
        csv_rows.append(
            {
                "ts_code": ts_code,
                "name": name,
                "industry_code": industry_code,
                "industry_name": industry_name,
                "total_mv": str(total_mv),
                "trade_date": sampling_date.isoformat(),
                "exchange": exchange,
            }
        )
    for ts_code in sorted(set(daily_rows) - set(memberships)):
        exclusions.append({"ts_code": ts_code, "name": "", "reason": "missing_sw2021_l1_membership"})
    if len(frame_rows) < minimum_market_rows:
        raise ExportError("historical source intersection did not yield a plausible full frame")
    return frame_rows, csv_rows, exclusions


def assemble_historical_sampling_frame(
    *,
    attempt_dir: Path,
    output_root: Path,
    szse_package: Path | None = None,
    minimum_market_rows: int = 4000,
) -> AssemblyResult:
    """Verify one sealed capture and publish the frame without any network or token access."""
    if minimum_market_rows < 4000:
        raise ExportError("minimum market-row guard may not be lowered below 4,000")
    capture_root = _ensure_real_root(attempt_dir.parent, create=False)
    output = _ensure_real_root(output_root, create=True)
    with _capture_root_lock(capture_root):
        manifest, manifest_hash, calls = _verify_attempt(
            attempt_dir,
            require_complete=True,
            szse_package=szse_package,
        )
        _verify_authorization_binding(manifest, require_current=True)
        _verify_resume_chain(attempt_dir, manifest)
        try:
            sampling_date = date.fromisoformat(str(manifest["sampling_date"]))
        except ValueError as exc:
            raise ExportError("capture sampling date is invalid") from exc
        if sampling_date != DEFAULT_SAMPLING_DATE:
            raise ExportError("capture sampling date differs from the frozen historical date")
        frame_rows, csv_rows, exclusions = _assemble_rows(
            attempt_dir,
            calls,
            sampling_date=sampling_date,
            minimum_market_rows=minimum_market_rows,
        )
        prereg = load_preregistration_ref(PROTOCOL_PATH, PROTOCOL_HASH_PATH)
        validated = validate_frame(frame_rows, sampling_date, prereg)
        sample = select_sample(validated)
        if len(sample.entries) != 36:
            raise ExportError("validated frame did not produce the frozen 36-company sample")
        frame_raw = _render_csv(
            ("ts_code", "name", "industry_code", "industry_name", "total_mv", "trade_date", "exchange"),
            csv_rows,
        )
        excluded_raw = _render_csv(("ts_code", "name", "reason"), exclusions)
        sample_raw = canonical_json_bytes(sample)
        authorization = manifest.get("authorization")
        acquisition_records = [
            {
                "call_id": item["call_id"],
                "origin_attempt_id": item["origin_attempt_id"],
                "origin_authorization_id": item["origin_authorization_id"],
                "captured_at": item["captured_at"],
                "receipt_sha256": item["receipt_sha256"],
                "blob_sha256": item["blob_sha256"],
            }
            for item in calls
        ]
        schema_hash = _sha256(
            canonical_json_bytes(
                {
                    "capture": CAPTURE_SCHEMA_VERSION,
                    "authorization": AUTHORIZATION_SCHEMA_VERSION,
                    "receipt": RECEIPT_SCHEMA_VERSION,
                    "manifest": MANIFEST_SCHEMA_VERSION,
                    "assembly": ASSEMBLY_SCHEMA_VERSION,
                }
            )
        )
        provenance = {
            "schema_version": ASSEMBLY_SCHEMA_VERSION,
            "sampling_trade_date": sampling_date.isoformat(),
            "protocol_id": prereg.protocol_id,
            "protocol_sha256": prereg.protocol_sha256,
            "capture": {
                "attempt_id": manifest["attempt_id"],
                "manifest_sha256": manifest_hash,
                "authorization": authorization,
                "parent_manifest_sha256": manifest.get("parent_manifest_sha256"),
                "resume_lineage": manifest.get("resume_lineage"),
                "calls": acquisition_records,
            },
            "static_szse": manifest["static_szse"],
            "generators": _code_manifest_value(),
            "schema_sha256": schema_hash,
            "network_access": "none; assembly accepts only immutable capture bytes and static SZSE files",
            "frame": {
                "relative_path": "frame.csv",
                "byte_count": len(frame_raw),
                "sha256": _sha256(frame_raw),
                "row_count": len(csv_rows),
            },
            "exclusions": {
                "relative_path": "excluded.csv",
                "byte_count": len(excluded_raw),
                "sha256": _sha256(excluded_raw),
                "row_count": len(exclusions),
            },
            "sample": {
                "relative_path": "sample-manifest.json",
                "byte_count": len(sample_raw),
                "sha256": _sha256(sample_raw),
                "row_count": len(sample.entries),
                "seed": sample.seed,
            },
        }
        provenance_raw = canonical_json_bytes(provenance)
        temp_dir = Path(tempfile.mkdtemp(prefix=".m4-assemble-", dir=output))
        try:
            outputs = {
                "frame.csv": frame_raw,
                "excluded.csv": excluded_raw,
                "sample-manifest.json": sample_raw,
                "frame-provenance.json": provenance_raw,
            }
            for filename, raw in outputs.items():
                _atomic_create_only(temp_dir / filename, raw)
            checksum_raw = (
                "\n".join(f"{_sha256(raw)}  {filename}" for filename, raw in sorted(outputs.items())) + "\n"
            ).encode("utf-8")
            _atomic_create_only(temp_dir / "SHA256SUMS", checksum_raw)
            final = output / f"historical-frame-{sampling_date.isoformat()}-{_sha256(frame_raw)[:16]}"
            if final.exists() or final.is_symlink():
                raise ExportError("assembled frame package already exists")
            _seal_tree(temp_dir)
            os.rename(temp_dir, final)
            _fsync_directory(output)
        except BaseException:
            _remove_tree(temp_dir)
            raise
        return AssemblyResult(
            final,
            final / "frame.csv",
            final / "frame-provenance.json",
            _sha256(frame_raw),
            sampling_date,
            len(csv_rows),
            len(sample.entries),
        )


def export_historical_sampling_frame(
    *,
    token: str,
    output_root: Path,
    now: datetime,
    sampling_date: date = DEFAULT_SAMPLING_DATE,
    szse_package: Path = DEFAULT_SZSE_PACKAGE,
    runner: Runner | None = None,
    tushare_transport: Transport = _default_transport,
    min_interval_seconds: float = 1.3,
    minimum_market_rows: int = 4000,
) -> ExportResult:
    """Create a read-only historical frame/sample package or fail without publication."""
    raise ExportError("legacy historical M4 exporter is disabled; use capture_historical_sources")
    if now.tzinfo is None:
        raise ExportError("export timestamp must be timezone-aware")
    if minimum_market_rows < 36:
        raise ExportError("minimum market row guard may not be below the frozen sample size")
    shanghai_now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    lag_days = (shanghai_now.date() - sampling_date).days
    if lag_days < 0 or lag_days > 1:
        raise ExportError("historical hybrid export requires acquisition within one day of the sampling date")
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output_root.is_symlink() or not output_root.is_dir():
        raise ExportError("output root must be a real directory")

    static_szse = _validate_static_szse_package(szse_package, sampling_date)
    active_runner = HistoricalAkshareRunner(runner)
    temp_dir = Path(tempfile.mkdtemp(prefix=".m4-historical-hybrid-", dir=output_root))
    try:
        raw_akshare_dir = temp_dir / "raw-akshare"
        raw_tushare_dir = temp_dir / "raw-tushare"
        output_dir = temp_dir / "function-outputs"
        static_dir = temp_dir / "static-inputs" / "szse"
        raw_akshare_dir.mkdir(mode=0o700)
        raw_tushare_dir.mkdir(mode=0o700)
        output_dir.mkdir(mode=0o700)
        static_dir.mkdir(parents=True, mode=0o700)

        calls: list[CallResult] = []
        sse = active_runner.call("stock_sse_summary", {})
        calls.append(sse)
        if _summary_date(sse) != sampling_date:
            raise ExportError("SSE source-date drift from the requested historical sampling date")
        szse_call = CallResult(
            "static_szse_market_overview",
            {"date": sampling_date.isoformat()},
            static_szse.dataframe,
            (),
        )
        _validate_exchange_summaries(sse, szse_call)

        daily_client = TushareApiClient(
            token,
            transport=tushare_transport,
            min_interval_seconds=min_interval_seconds,
        )
        try:
            daily = daily_client.call(
                "daily_basic",
                {"trade_date": sampling_date.strftime("%Y%m%d")},
                ("ts_code", "trade_date", "total_mv"),
            )
        except TushareExportError as exc:
            raise ExportError(str(exc)) from exc
        daily_rows: dict[str, Decimal] = {}
        for row in daily.rows:
            ts_code = str(row.get("ts_code") or "")
            if not re.fullmatch(r"\d{6}\.(SH|SZ)", ts_code):
                continue
            if _parse_iso_date(row.get("trade_date"), label=f"daily_basic/{ts_code}") != sampling_date:
                raise ExportError(f"daily_basic date drift for {ts_code}")
            if ts_code in daily_rows:
                raise ExportError(f"daily_basic returned duplicate code: {ts_code}")
            daily_rows[ts_code] = _decimal(row.get("total_mv"), label=f"daily_basic total_mv/{ts_code}")
        if len(daily_rows) < minimum_market_rows:
            raise ExportError("daily_basic did not return a plausible full SSE/SZSE A-share market")

        memberships: dict[str, tuple[str, str, str, date]] = {}
        for industry_code, industry_name in SW2021_INDUSTRIES.items():
            call = active_runner.call("index_component_sw", {"symbol": industry_code.removesuffix(".SI")})
            calls.append(call)
            rows = _records(call, ("证券代码", "证券名称", "计入日期"))
            if not rows:
                raise ExportError(f"SWS component source returned no rows for {industry_code}")
            for row in rows:
                code = str(row["证券代码"]).strip().zfill(6)
                if not re.fullmatch(r"[036]\d{5}", code):
                    continue
                ts_code = f"{code}.SH" if code.startswith("6") else f"{code}.SZ"
                name = str(row["证券名称"]).strip()
                if not name:
                    raise ExportError(f"SWS component source returned empty name: {ts_code}")
                beginning_date = _component_date(row["计入日期"], ts_code=ts_code)
                if beginning_date > sampling_date:
                    raise ExportError(f"SWS component begins after sampling date: {ts_code}")
                if ts_code in memberships:
                    previous = memberships[ts_code]
                    raise ExportError(
                        f"SWS component membership is not unique: {ts_code} in {previous[0]} and {industry_code}"
                    )
                memberships[ts_code] = (industry_code, industry_name, name, beginning_date)
        if len(memberships) < minimum_market_rows:
            raise ExportError("SWS component snapshot did not return a plausible full SW2021 A-share frame")

        frame_rows: list[FrameRow] = []
        csv_rows: list[Mapping[str, object]] = []
        exclusions: list[Mapping[str, object]] = []
        for ts_code, (industry_code, industry_name, name, _) in sorted(memberships.items()):
            total_mv = daily_rows.get(ts_code)
            if total_mv is None:
                exclusions.append({"ts_code": ts_code, "name": name, "reason": "missing_20260715_daily_basic"})
                continue
            exchange = "SSE" if ts_code.endswith(".SH") else "SZSE"
            frame_rows.append(FrameRow(ts_code, name, industry_code, industry_name, total_mv, sampling_date, exchange))
            csv_rows.append(
                {
                    "ts_code": ts_code,
                    "name": name,
                    "industry_code": industry_code,
                    "industry_name": industry_name,
                    "total_mv": str(total_mv),
                    "trade_date": sampling_date.isoformat(),
                    "exchange": exchange,
                }
            )
        for ts_code in sorted(set(daily_rows) - set(memberships)):
            exclusions.append({"ts_code": ts_code, "name": "", "reason": "missing_sw2021_l1_membership"})
        if len(frame_rows) < minimum_market_rows:
            raise ExportError("historical source intersection did not yield a plausible full frame")

        prereg = load_preregistration_ref(PROTOCOL_PATH, PROTOCOL_HASH_PATH)
        validated = validate_frame(frame_rows, sampling_date, prereg)
        sample = select_sample(validated)
        if len(sample.entries) != 36:
            raise ExportError("validated frame did not produce the frozen 36-company sample")

        frame_raw = _render_csv(
            ("ts_code", "name", "industry_code", "industry_name", "total_mv", "trade_date", "exchange"),
            csv_rows,
        )
        excluded_raw = _render_csv(("ts_code", "name", "reason"), exclusions)
        sample_raw = canonical_json_bytes(sample)
        _write_new(temp_dir / "frame.csv", frame_raw)
        _write_new(temp_dir / "excluded.csv", excluded_raw)
        _write_new(temp_dir / "sample-manifest.json", sample_raw)

        static_records: list[Mapping[str, object]] = []
        for source in static_szse.files + (szse_package / "SHA256SUMS",):
            raw = source.read_bytes()
            target = static_dir / source.name
            _write_new(target, raw)
            static_records.append(
                {
                    "relative_path": target.relative_to(temp_dir).as_posix(),
                    "byte_count": len(raw),
                    "sha256": _sha256(raw),
                }
            )

        function_records: list[Mapping[str, object]] = []
        raw_akshare_records: list[Mapping[str, object]] = []
        capture_index = 0
        for call_index, call in enumerate(calls, start=1):
            qualifier = call.arguments.get("symbol") or "all"
            stem = f"{call_index:03d}-{call.function_name}-{qualifier}"
            output_raw = _render_dataframe(call)
            output_path = output_dir / f"{stem}.csv"
            _write_new(output_path, output_raw)
            function_records.append(
                {
                    "function_name": call.function_name,
                    "arguments": dict(call.arguments),
                    "relative_path": output_path.relative_to(temp_dir).as_posix(),
                    "byte_count": len(output_raw),
                    "sha256": _sha256(output_raw),
                    "row_count": len(call.dataframe.index),
                }
            )
            for capture in call.captures:
                capture_index += 1
                capture_path = raw_akshare_dir / f"{capture_index:03d}-{call.function_name}.json"
                _write_new(capture_path, capture.raw)
                raw_akshare_records.append(
                    {
                        "function_name": call.function_name,
                        "request_url": capture.request_url,
                        "request_params": dict(capture.request_params),
                        "response_url": capture.response_url,
                        "status_code": capture.status_code,
                        "content_type": capture.content_type,
                        "relative_path": capture_path.relative_to(temp_dir).as_posix(),
                        "byte_count": len(capture.raw),
                        "sha256": _sha256(capture.raw),
                    }
                )

        tushare_raw_path = raw_tushare_dir / "001-daily_basic-20260715.json"
        _write_new(tushare_raw_path, daily.raw)
        tushare_record = {
            "api_name": "daily_basic",
            "params": {"trade_date": sampling_date.strftime("%Y%m%d")},
            "requested_fields": list(daily.requested_fields),
            "relative_path": tushare_raw_path.relative_to(temp_dir).as_posix(),
            "byte_count": len(daily.raw),
            "sha256": _sha256(daily.raw),
            "row_count": len(daily.rows),
            "response_count": daily.response_count,
            "has_more": daily.has_more,
        }

        provenance = {
            "authorization_id": AUTHORIZATION_ID,
            "dataset_id": f"historical-hybrid-sw2021-frame-{sampling_date.isoformat()}-{_sha256(frame_raw)[:16]}",
            "publishers": ["Tushare Pro", "申万宏源研究", "上海证券交易所", "深圳证券交易所"],
            "acquired_at": shanghai_now.isoformat(),
            "sampling_trade_date": sampling_date.isoformat(),
            "protocol_id": prereg.protocol_id,
            "protocol_sha256": prereg.protocol_sha256,
            "generators": _generator_records(BASE_GENERATORS),
            "adapters": {"akshare_version": active_runner.version, "tushare_endpoint": API_URL},
            "authorized_tushare_apis": sorted(AUTHORIZED_TUSHARE_APIS),
            "authorized_akshare_functions": sorted(AUTHORIZED_AKSHARE_FUNCTIONS),
            "network_policy": "Exact provider allowlists; HTTPS/TLS required; redirects disabled by guarded clients.",
            "scope": "SSE/SZSE securities in the captured SWS SW2021 L1 component lists with positive Tushare daily_basic total_mv for 2026-07-15.",
            "membership_temporal_rule": "Capture lag is at most one day and every retained SWS current component has a provider beginning date on or before 2026-07-15; post-date additions block publication.",
            "membership_source_limitation": "index_component_sw exposes current membership and beginning date but no removal history; raw snapshots are preserved for audit.",
            "total_mv_unit": "万元 (Tushare daily_basic provider-native value)",
            "field_mapping": {
                "ts_code": "index_component_sw.证券代码 with official .SH/.SZ suffix",
                "name": "index_component_sw.证券名称",
                "industry_code": "queried frozen SW2021 index code plus .SI",
                "industry_name": "frozen protocol mapping for queried SW2021 code",
                "total_mv": "daily_basic.total_mv (unrounded provider value)",
                "trade_date": "daily_basic.trade_date; exactly 2026-07-15",
                "exchange": "derived from six-digit official security code",
            },
            "integrity_gates": {
                "acquisition_lag_days": lag_days,
                "sse_report_date_equals_sampling_date": True,
                "szse_request_date_equals_sampling_date": True,
                "szse_static_package_verified": True,
                "daily_basic_date_equals_sampling_date": True,
                "all_31_frozen_sw2021_queries_nonempty": True,
                "no_sw_entry_after_sampling_date": True,
                "unique_sw2021_l1_membership": True,
                "minimum_intersection_rows": minimum_market_rows,
            },
            "frame": {
                "relative_path": "frame.csv",
                "format": "text/csv; charset=utf-8; header=present",
                "byte_count": len(frame_raw),
                "sha256": _sha256(frame_raw),
                "row_count": len(csv_rows),
            },
            "sample": {
                "relative_path": "sample-manifest.json",
                "format": "application/json",
                "byte_count": len(sample_raw),
                "sha256": _sha256(sample_raw),
                "row_count": len(sample.entries),
                "seed": sample.seed,
            },
            "exclusions": {
                "relative_path": "excluded.csv",
                "byte_count": len(excluded_raw),
                "sha256": _sha256(excluded_raw),
                "row_count": len(exclusions),
                "reason_counts": {
                    reason: sum(item["reason"] == reason for item in exclusions)
                    for reason in sorted({str(item["reason"]) for item in exclusions})
                },
            },
            "static_szse": {
                "source_date": static_szse.source_date.isoformat(),
                "semantic_sha256": static_szse.semantic_sha256,
                "files": static_records,
            },
            "function_outputs": function_records,
            "raw_akshare_responses": raw_akshare_records,
            "raw_tushare_response": tushare_record,
            "secret_handling": "TUSHARE_TOKEN used only in the HTTPS request body; absent from package and provenance.",
        }
        provenance_raw = canonical_json_bytes(provenance)
        _write_new(temp_dir / "frame-provenance.json", provenance_raw)

        token_bytes = token.encode("utf-8")
        for path in temp_dir.rglob("*"):
            if path.is_file() and token_bytes in path.read_bytes():
                raise ExportError(f"secret leakage detected in generated package: {path.name}")

        checksum_rows = []
        for path in sorted(item for item in temp_dir.rglob("*") if item.is_file()):
            checksum_rows.append(f"{_sha256(path.read_bytes())}  {path.relative_to(temp_dir).as_posix()}")
        _write_new(temp_dir / "SHA256SUMS", ("\n".join(checksum_rows) + "\n").encode("utf-8"))
        final_dir = output_root / f"historical-hybrid-frame-{sampling_date.isoformat()}-{_sha256(frame_raw)[:16]}"
        if final_dir.exists():
            raise ExportError(f"static frame package already exists: {final_dir}")
        _seal_tree(temp_dir)
        os.rename(temp_dir, final_dir)
        descriptor = os.open(output_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return ExportResult(
            final_dir,
            final_dir / "frame.csv",
            final_dir / "frame-provenance.json",
            _sha256(frame_raw),
            sampling_date,
            len(csv_rows),
        )
    except BaseException:
        _remove_tree(temp_dir)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture_parser = subparsers.add_parser("capture", help="capture and seal the frozen 33 raw responses")
    capture_parser.add_argument("--capture-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "captures")
    capture_parser.add_argument("--attempt-id", required=True)
    capture_parser.add_argument("--authorization", type=Path, required=True)
    capture_parser.add_argument("--authorization-sha256", type=Path, required=True)
    capture_parser.add_argument("--szse-package", type=Path, default=DEFAULT_SZSE_PACKAGE)
    capture_parser.add_argument("--resume-attempt", type=Path)
    capture_parser.add_argument("--min-interval-seconds", type=float, default=1.3)

    recover_parser = subparsers.add_parser("recover", help="seal stale staging as incomplete")
    recover_parser.add_argument("--capture-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "captures")
    recover_parser.add_argument("--attempt-id", required=True)
    recover_parser.add_argument("--szse-package", type=Path, default=DEFAULT_SZSE_PACKAGE)

    assemble_parser = subparsers.add_parser("assemble", help="assemble a verified complete capture offline")
    assemble_parser.add_argument("--attempt-dir", type=Path, required=True)
    assemble_parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "frames")
    assemble_parser.add_argument("--szse-package", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            capture_result = capture_historical_sources(
                capture_root=args.capture_root,
                attempt_id=args.attempt_id,
                now=datetime.now(tz=ZoneInfo("Asia/Shanghai")),
                szse_package=args.szse_package,
                authorization_path=args.authorization,
                authorization_hash_path=args.authorization_sha256,
                min_interval_seconds=args.min_interval_seconds,
                resume_attempt=args.resume_attempt,
            )
            print(f"attempt={capture_result.attempt_dir}")
            print(f"complete={str(capture_result.complete).lower()}")
            print(f"manifest_sha256={capture_result.manifest_sha256}")
            return 0
        if args.command == "recover":
            recovered = recover_incomplete_capture(
                capture_root=args.capture_root,
                attempt_id=args.attempt_id,
                szse_package=args.szse_package,
            )
            print(f"attempt={recovered.attempt_dir}")
            print("complete=false")
            print(f"manifest_sha256={recovered.manifest_sha256}")
            return 0
        result = assemble_historical_sampling_frame(
            attempt_dir=args.attempt_dir,
            output_root=args.output_root,
            szse_package=args.szse_package,
        )
    except (ExportError, TushareExportError) as exc:
        print(f"M4_CAPTURE_FIRST_BLOCKED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"M4_CAPTURE_FIRST_BLOCKED: unexpected {type(exc).__name__}", file=sys.stderr)
        return 2
    print(f"package={result.package_dir}")
    print(f"sampling_date={result.sampling_date.isoformat()}")
    print(f"rows={result.row_count}")
    print(f"frame_sha256={result.frame_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
