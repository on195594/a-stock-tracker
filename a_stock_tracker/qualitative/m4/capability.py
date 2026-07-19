"""Bounded, non-adoptable Tushare capability gate for MILESTONE-004 v1.2."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import ssl
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from a_stock_tracker.qualitative.audit import (
    SAMPLING_SEED,
    SW2021_INDUSTRIES,
    FrameRow,
    PreregistrationRef,
    canonical_json_bytes,
    select_sample,
    validate_frame,
)
from a_stock_tracker.paths import PROJECT_ROOT

API_URL = "https://api.tushare.pro"
AUTHORIZATION_SCHEMA_VERSION = "m4-frame-source-capability-authorization-v1"
RECEIPT_SCHEMA_VERSION = "m4-frame-source-capability-receipt-v1"
MANIFEST_SCHEMA_VERSION = "m4-frame-source-capability-manifest-v1"
PROTOCOL_ID = "qualitative-v2-m4-prereg-v1.2"
PROTOCOL_VERSION = "1.2.0"
PROTOCOL_PATH = (
    PROJECT_ROOT / "docs" / "plans" / "2026-07-16-milestone-004-frame-source-capability-preregistration-v1.2.md"
)
PROTOCOL_HASH_PATH = PROJECT_ROOT / "reviews" / "milestone-004-preregistration-v1.2" / "preregistration.sha256"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "milestone-004" / "v1.2" / "capability-probes"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 96 * 1024 * 1024
MINIMUM_ROWS = 4_000
MEMBER_SERVICE_LIMIT = 5_000
DAILY_SERVICE_LIMIT = 6_000


@dataclass(frozen=True, slots=True)
class CapabilityCall:
    """One immutable call in the capability matrix."""

    api_name: str
    params: tuple[tuple[str, str], ...]
    fields: tuple[str, ...]

    @property
    def call_id(self) -> str:
        return f"tushare:{self.api_name}"

    def authorization_value(self) -> Mapping[str, object]:
        return {"api_name": self.api_name, "fields": list(self.fields), "params": dict(self.params)}


CLASSIFICATION_CALL = CapabilityCall(
    "index_classify",
    (("level", "L1"), ("src", "SW2021")),
    ("index_code", "industry_name", "level", "src"),
)
MEMBERS_CALL = CapabilityCall(
    "index_member_all",
    (("is_new", "Y"),),
    ("l1_code", "l1_name", "ts_code", "name", "is_new"),
)
DAILY_CALL_FIELDS = ("ts_code", "trade_date", "total_mv")


def capability_call_matrix(probe_trade_date: date) -> tuple[CapabilityCall, ...]:
    """Return the exact three-call matrix for an authorization-bound trade date."""
    return (
        CLASSIFICATION_CALL,
        MEMBERS_CALL,
        CapabilityCall("daily_basic", (("trade_date", probe_trade_date.strftime("%Y%m%d")),), DAILY_CALL_FIELDS),
    )


class CapabilityError(RuntimeError):
    """Base class for finite, credential-free capability failures."""

    error_code = "capability_error"

    def __init__(self, error_code: str = "capability_error") -> None:
        super().__init__(error_code)
        self.error_code = error_code


class CapabilityAuthorizationError(CapabilityError):
    """Authorization is absent, invalid, changed, or outside its window."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class CapabilitySecurityError(CapabilityError):
    """A transport or secret boundary was violated."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class CapabilityVerificationError(CapabilityError):
    """A sealed probe failed offline integrity verification."""


class CapabilityTransportError(CapabilityError):
    """A finite transport failure with no raw exception persistence."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class CapabilityAuthorization:
    """Canonical external authorization for one bounded capability attempt."""

    authorization_id: str
    attempt_id: str
    protocol_sha256: str
    output_root: str
    probe_trade_date: date
    not_before: datetime
    not_after: datetime
    allowed_calls: tuple[CapabilityCall, ...]
    sha256: str
    _document_json: bytes

    @property
    def document(self) -> Mapping[str, object]:
        """Return a detached copy of the canonical authorization document."""
        value = json.loads(self._document_json)
        if not isinstance(value, dict):
            raise CapabilityAuthorizationError("authorization_json_invalid")
        return value


@dataclass(frozen=True, slots=True)
class CapabilityCallReceipt:
    """Sanitized terminal receipt for one matrix ordinal."""

    ordinal: int
    call_id: str
    captured_at: datetime
    status: str
    error_code: str | None
    http_status_category: str | None
    content_type: str | None
    byte_count: int
    blob_sha256: str | None
    blob_relative_path: str | None
    row_count: int | None
    receipt_relative_path: str
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class CapabilityProbeResult:
    """Identity and separated completion/capability outcome for a sealed probe."""

    attempt_dir: Path
    manifest_path: Path
    manifest_sha256: str
    authorization_id: str
    attempt_id: str
    complete: bool
    capability_pass: bool
    terminal_state: str
    receipts: tuple[CapabilityCallReceipt, ...]


@dataclass(frozen=True, slots=True)
class CapabilityResponse:
    """Exact response bytes and a credential-free transport summary."""

    request_url: str
    response_url: str
    status_code: int
    content_type: str
    raw: bytes


class CapabilityBackend(Protocol):
    """Single-call backend used by production transport and synthetic tests."""

    def call(self, call: CapabilityCall) -> CapabilityResponse: ...


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _transport_error_code(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError,)):
        return "transport_timeout"
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, (TimeoutError,)):
        return "transport_timeout"
    if isinstance(reason, ssl.SSLError):
        return "transport_tls"
    if isinstance(reason, ConnectionError):
        return "transport_connection"
    return "transport_unavailable"


class TushareCapabilityBackend:
    """No-retry HTTPS backend restricted to the fixed Tushare origin."""

    def __init__(self, token: str, *, timeout_seconds: float = 30.0, min_interval_seconds: float = 1.3) -> None:
        if not token or any(character.isspace() for character in token):
            raise CapabilitySecurityError("token_missing_or_malformed")
        if timeout_seconds <= 0 or min_interval_seconds < 0:
            raise CapabilitySecurityError("transport_configuration_invalid")
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._min_interval_seconds = min_interval_seconds
        self._last_started: float | None = None

    def call(self, call: CapabilityCall) -> CapabilityResponse:
        now = time.monotonic()
        if self._last_started is not None:
            remaining = self._min_interval_seconds - (now - self._last_started)
            if remaining > 0:
                time.sleep(remaining)
        self._last_started = time.monotonic()
        payload = {
            "api_name": call.api_name,
            "token": self._token,
            "params": dict(call.params),
            "fields": ",".join(call.fields),
        }
        request = Request(
            API_URL,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "a-stock-tracker-m4-capability/1"},
            method="POST",
        )
        opener = build_opener(_RejectRedirects())
        try:
            with opener.open(request, timeout=self._timeout_seconds) as response:  # noqa: S310 - fixed HTTPS origin
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                result = CapabilityResponse(
                    request.full_url,
                    response.geturl(),
                    int(response.status),
                    str(response.headers.get_content_type()),
                    raw,
                )
        except HTTPError as exc:
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
            result = CapabilityResponse(
                request.full_url,
                str(exc.geturl()),
                int(exc.code),
                str(exc.headers.get_content_type()) if exc.headers is not None else "application/octet-stream",
                raw,
            )
        except (URLError, TimeoutError, OSError) as exc:
            raise CapabilityTransportError(_transport_error_code(exc)) from None
        if len(result.raw) > MAX_RESPONSE_BYTES:
            raise CapabilityTransportError("response_too_large")
        return result


@dataclass(frozen=True, slots=True)
class _ParsedResponse:
    rows: tuple[Mapping[str, object], ...]
    pagination_residue: bool


@dataclass(frozen=True, slots=True)
class _GateResult:
    passed: bool
    code: str
    metrics: Mapping[str, int]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_regular(path: Path, *, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise CapabilityVerificationError("unsafe_or_missing_file")
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
        raise CapabilityVerificationError("unsafe_or_oversized_file")
    raw = path.read_bytes()
    after = path.stat(follow_symlinks=False)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise CapabilityVerificationError("file_changed_during_read")
    return raw


def _read_hash_manifest(path: Path, expected_filename: str) -> str:
    try:
        text = _read_regular(path, maximum=4096).decode("ascii")
    except UnicodeDecodeError as exc:
        raise CapabilityAuthorizationError("authorization_hash_invalid") from exc
    matched = re.fullmatch(r"([0-9a-f]{64})(?:  ([^/\\\r\n]+))?\n?", text)
    if matched is None or (matched.group(2) is not None and matched.group(2) != expected_filename):
        raise CapabilityAuthorizationError("authorization_hash_invalid")
    return matched.group(1)


def _parse_time(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise CapabilityAuthorizationError(f"authorization_{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CapabilityAuthorizationError(f"authorization_{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CapabilityAuthorizationError(f"authorization_{field}_invalid")
    return parsed


def _protocol_sha256(protocol_path: Path, hash_path: Path) -> str:
    try:
        manifest = _read_regular(hash_path, maximum=4096).decode("ascii")
    except (UnicodeDecodeError, CapabilityVerificationError) as exc:
        raise CapabilityAuthorizationError("protocol_hash_invalid") from exc
    entries = re.findall(r"^([0-9a-f]{64})  (.+)$", manifest, re.MULTILINE)
    expected = next((digest for digest, name in entries if name == str(protocol_path.relative_to(PROJECT_ROOT))), None)
    if expected is None or _sha256(_read_regular(protocol_path, maximum=2 * 1024 * 1024)) != expected:
        raise CapabilityAuthorizationError("protocol_hash_invalid")
    return expected


def _authorization_error_for_time(now: datetime, not_before: datetime, not_after: datetime) -> str | None:
    current = now.astimezone(timezone.utc)
    if current < not_before.astimezone(timezone.utc):
        return "authorization_not_yet_valid"
    if current > not_after.astimezone(timezone.utc):
        return "authorization_expired"
    return None


def load_capability_authorization(
    authorization_path: Path,
    authorization_hash_path: Path,
    *,
    attempt_id: str,
    output_root: Path,
    now: datetime,
    protocol_path: Path = PROTOCOL_PATH,
    protocol_hash_path: Path = PROTOCOL_HASH_PATH,
) -> CapabilityAuthorization:
    """Read and fully bind an externally created canonical authorization."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise CapabilityAuthorizationError("clock_not_timezone_aware")
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,95}", attempt_id) is None:
        raise CapabilityAuthorizationError("attempt_id_invalid")
    try:
        raw = _read_regular(authorization_path, maximum=1024 * 1024)
    except CapabilityVerificationError as exc:
        raise CapabilityAuthorizationError("authorization_missing_or_unsafe") from exc
    expected_hash = _read_hash_manifest(authorization_hash_path, authorization_path.name)
    actual_hash = _sha256(raw)
    if actual_hash != expected_hash:
        raise CapabilityAuthorizationError("authorization_hash_mismatch")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityAuthorizationError("authorization_json_invalid") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise CapabilityAuthorizationError("authorization_not_canonical")
    exact_fields = {
        "allowed_calls",
        "attempt_id",
        "authorization_id",
        "not_after",
        "not_before",
        "output_root",
        "probe_trade_date",
        "protocol_sha256",
        "schema_version",
    }
    if set(value) != exact_fields or value.get("schema_version") != AUTHORIZATION_SCHEMA_VERSION:
        raise CapabilityAuthorizationError("authorization_schema_mismatch")
    authorization_id = value.get("authorization_id")
    if not isinstance(authorization_id, str) or not authorization_id or len(authorization_id) > 200:
        raise CapabilityAuthorizationError("authorization_id_invalid")
    if value.get("attempt_id") != attempt_id:
        raise CapabilityAuthorizationError("authorization_attempt_mismatch")
    try:
        probe_trade_date = date.fromisoformat(str(value.get("probe_trade_date")))
    except ValueError as exc:
        raise CapabilityAuthorizationError("authorization_date_invalid") from exc
    expected_protocol = _protocol_sha256(protocol_path, protocol_hash_path)
    if value.get("protocol_sha256") != expected_protocol:
        raise CapabilityAuthorizationError("authorization_protocol_mismatch")
    declared_root = value.get("output_root")
    if not isinstance(declared_root, str) or not declared_root or ".." in Path(declared_root).parts:
        raise CapabilityAuthorizationError("authorization_output_root_invalid")
    authorized_root = Path(declared_root)
    if not authorized_root.is_absolute():
        authorized_root = PROJECT_ROOT / authorized_root
    if authorized_root.resolve() != output_root.resolve():
        raise CapabilityAuthorizationError("authorization_output_root_mismatch")
    matrix = capability_call_matrix(probe_trade_date)
    if value.get("allowed_calls") != [item.authorization_value() for item in matrix]:
        raise CapabilityAuthorizationError("authorization_call_matrix_mismatch")
    not_before = _parse_time(value.get("not_before"), "not_before")
    not_after = _parse_time(value.get("not_after"), "not_after")
    if not_before >= not_after:
        raise CapabilityAuthorizationError("authorization_window_invalid")
    time_error = _authorization_error_for_time(now, not_before, not_after)
    if time_error is not None:
        raise CapabilityAuthorizationError(time_error)
    return CapabilityAuthorization(
        authorization_id,
        attempt_id,
        expected_protocol,
        declared_root,
        probe_trade_date,
        not_before,
        not_after,
        matrix,
        actual_hash,
        raw,
    )


def _reload_authorization(
    expected: CapabilityAuthorization,
    authorization_path: Path,
    authorization_hash_path: Path,
    output_root: Path,
    now: datetime,
    protocol_path: Path,
    protocol_hash_path: Path,
) -> CapabilityAuthorization:
    current = load_capability_authorization(
        authorization_path,
        authorization_hash_path,
        attempt_id=expected.attempt_id,
        output_root=output_root,
        now=now,
        protocol_path=protocol_path,
        protocol_hash_path=protocol_hash_path,
    )
    if current.sha256 != expected.sha256 or current.document != expected.document:
        raise CapabilityAuthorizationError("authorization_changed")
    return current


def _load_token() -> str:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file() or env_path.is_symlink():
        raise CapabilitySecurityError("token_missing_or_malformed")
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise CapabilitySecurityError("token_missing_or_malformed") from exc
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "TUSHARE_TOKEN":
            token = value.strip().strip('"').strip("'")
            if token and not any(character.isspace() for character in token):
                return token
    raise CapabilitySecurityError("token_missing_or_malformed")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_only(path: Path, raw: bytes, *, mode: int = 0o444) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise CapabilitySecurityError("artifact_parent_unsafe")
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
            raise CapabilitySecurityError("artifact_already_exists") from exc
        _fsync_directory(path.parent)
    finally:
        temp_path.unlink(missing_ok=True)


def _content_addressed_blob(staging: Path, raw: bytes) -> tuple[str, str]:
    digest = _sha256(raw)
    relative = f"blobs/{digest}.blob"
    path = staging / relative
    try:
        _create_only(path, raw)
    except CapabilitySecurityError as exc:
        if exc.error_code != "artifact_already_exists" or _read_regular(path, maximum=MAX_RESPONSE_BYTES) != raw:
            raise
    return digest, relative


@contextmanager
def _output_lock(output_root: Path) -> Iterator[None]:
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output_root.is_symlink() or not output_root.is_dir():
        raise CapabilitySecurityError("output_root_unsafe")
    lock_path = output_root / ".capability.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise CapabilitySecurityError("output_lock_unsafe") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise CapabilitySecurityError("output_lock_unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        current = os.stat(lock_path, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise CapabilitySecurityError("output_lock_changed")
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _validate_transport(response: CapabilityResponse) -> None:
    if response.request_url != API_URL or response.response_url != API_URL:
        raise CapabilitySecurityError("transport_origin_or_redirect_invalid")
    if isinstance(response.status_code, bool) or not 100 <= response.status_code <= 599:
        raise CapabilitySecurityError("http_status_invalid")
    if 300 <= response.status_code < 400:
        raise CapabilitySecurityError("redirect_response_rejected")
    if len(response.raw) > MAX_RESPONSE_BYTES:
        raise CapabilityTransportError("response_too_large")


def _parse_response(raw: bytes, call: CapabilityCall) -> _ParsedResponse:
    try:
        value = json.loads(raw.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityError("response_json_invalid") from exc
    if not isinstance(value, dict) or value.get("code") != 0:
        raise CapabilityError("provider_error")
    data = value.get("data")
    if not isinstance(data, dict) or not {"fields", "items"} <= set(data):
        raise CapabilityError("response_schema_invalid")
    if set(data) - {"fields", "items", "count", "has_more"}:
        raise CapabilityError("response_schema_invalid")
    fields = data.get("fields")
    items = data.get("items")
    if fields != list(call.fields) or not isinstance(items, list):
        raise CapabilityError("response_fields_invalid")
    rows: list[Mapping[str, object]] = []
    for item in items:
        if not isinstance(item, list) or len(item) != len(call.fields):
            raise CapabilityError("response_row_invalid")
        rows.append(dict(zip(call.fields, item, strict=True)))
    pagination_residue = "count" in data or "has_more" in data
    if data.get("has_more") is True:
        pagination_residue = True
    count = data.get("count")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count != len(rows)):
        pagination_residue = True
    return _ParsedResponse(tuple(rows), pagination_residue)


def _http_category(status_code: int) -> str:
    return f"http_{status_code // 100}xx"


def _receipt_value(
    *,
    ordinal: int,
    call: CapabilityCall,
    captured_at: datetime,
    status: str,
    error_code: str | None,
    response: CapabilityResponse | None,
    blob_sha256: str | None,
    blob_relative_path: str | None,
    row_count: int | None,
) -> Mapping[str, object]:
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "non_adoptable": True,
        "ordinal": ordinal,
        "call_id": call.call_id,
        "request": {
            "url": API_URL,
            "method": "POST",
            "api_name": call.api_name,
            "params": dict(call.params),
            "fields": list(call.fields),
            "redirect_policy": "deny",
        },
        "response": {
            "captured_at": captured_at.isoformat(),
            "status": status,
            "error_code": error_code,
            "http_status_category": _http_category(response.status_code) if response is not None else None,
            "content_type": response.content_type if response is not None else None,
            "byte_count": len(response.raw) if response is not None else 0,
            "blob_sha256": blob_sha256,
            "blob_relative_path": blob_relative_path,
            "row_count": row_count,
        },
    }


def _publish_receipt(staging: Path, value: Mapping[str, object]) -> CapabilityCallReceipt:
    ordinal_value = value["ordinal"]
    if isinstance(ordinal_value, bool) or not isinstance(ordinal_value, int):
        raise CapabilitySecurityError("receipt_internal_invalid")
    ordinal = ordinal_value
    relative = f"receipts/{ordinal:02d}-{str(value['call_id']).replace(':', '_')}.json"
    raw = canonical_json_bytes(value)
    _create_only(staging / relative, raw)
    response = value["response"]
    if not isinstance(response, Mapping):
        raise CapabilitySecurityError("receipt_internal_invalid")
    captured_at = datetime.fromisoformat(str(response["captured_at"]))
    return CapabilityCallReceipt(
        ordinal,
        str(value["call_id"]),
        captured_at,
        str(response["status"]),
        str(response["error_code"]) if response["error_code"] is not None else None,
        str(response["http_status_category"]) if response["http_status_category"] is not None else None,
        str(response["content_type"]) if response["content_type"] is not None else None,
        int(response["byte_count"]),
        str(response["blob_sha256"]) if response["blob_sha256"] is not None else None,
        str(response["blob_relative_path"]) if response["blob_relative_path"] is not None else None,
        int(response["row_count"]) if response["row_count"] is not None else None,
        relative,
        _sha256(raw),
    )


def _classification_gate(parsed: _ParsedResponse | None) -> _GateResult:
    if parsed is None:
        return _GateResult(False, "classification_unavailable", {})
    mapping: dict[str, str] = {}
    for row in parsed.rows:
        code = str(row.get("index_code", ""))
        name = str(row.get("industry_name", ""))
        if row.get("level") != "L1" or row.get("src") != "SW2021" or code in mapping:
            return _GateResult(False, "classification_row_invalid", {"rows": len(parsed.rows)})
        mapping[code] = name
    if mapping != dict(SW2021_INDUSTRIES):
        return _GateResult(False, "classification_mapping_mismatch", {"rows": len(parsed.rows)})
    return _GateResult(True, "pass", {"rows": len(parsed.rows), "industries": len(mapping)})


def _members_gate(parsed: _ParsedResponse | None) -> tuple[_GateResult, Mapping[str, tuple[str, str, str]]]:
    if parsed is None:
        return _GateResult(False, "members_unavailable", {}), {}
    if parsed.pagination_residue:
        return _GateResult(False, "members_pagination_residue", {"rows": len(parsed.rows)}), {}
    if len(parsed.rows) >= MEMBER_SERVICE_LIMIT:
        return _GateResult(False, "members_service_limit_reached", {"rows": len(parsed.rows)}), {}
    if len(parsed.rows) < MINIMUM_ROWS:
        return _GateResult(False, "members_too_few_rows", {"rows": len(parsed.rows)}), {}
    memberships: dict[str, tuple[str, str, str]] = {}
    represented: set[str] = set()
    for row in parsed.rows:
        code = str(row.get("ts_code", ""))
        industry_code = str(row.get("l1_code", ""))
        industry_name = str(row.get("l1_name", ""))
        name = str(row.get("name", "")).strip()
        if (
            re.fullmatch(r"[036]\d{5}\.(SH|SZ)", code) is None
            or row.get("is_new") != "Y"
            or not name
            or SW2021_INDUSTRIES.get(industry_code) != industry_name
        ):
            return _GateResult(False, "members_row_invalid", {"rows": len(parsed.rows)}), {}
        if code in memberships:
            return _GateResult(False, "members_duplicate_membership", {"rows": len(parsed.rows)}), {}
        memberships[code] = (industry_code, industry_name, name)
        represented.add(industry_code)
    if represented != set(SW2021_INDUSTRIES):
        return _GateResult(
            False,
            "members_industry_coverage_mismatch",
            {"rows": len(parsed.rows), "industries": len(represented)},
        ), {}
    return _GateResult(
        True,
        "pass",
        {"rows": len(parsed.rows), "unique_stocks": len(memberships), "industries": len(represented)},
    ), memberships


def _positive_decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite() or result <= 0:
        return None
    return result


def _daily_gate(parsed: _ParsedResponse | None, trade_date: date) -> tuple[_GateResult, Mapping[str, Decimal]]:
    if parsed is None:
        return _GateResult(False, "daily_unavailable", {}), {}
    if parsed.pagination_residue:
        return _GateResult(False, "daily_pagination_residue", {"rows": len(parsed.rows)}), {}
    if len(parsed.rows) >= DAILY_SERVICE_LIMIT:
        return _GateResult(False, "daily_service_limit_reached", {"rows": len(parsed.rows)}), {}
    if len(parsed.rows) < MINIMUM_ROWS:
        return _GateResult(False, "daily_too_few_rows", {"rows": len(parsed.rows)}), {}
    expected_date = trade_date.strftime("%Y%m%d")
    values: dict[str, Decimal] = {}
    for row in parsed.rows:
        code = str(row.get("ts_code", ""))
        market_value = _positive_decimal(row.get("total_mv"))
        if re.fullmatch(r"[036]\d{5}\.(SH|SZ)", code) is None or str(row.get("trade_date")) != expected_date:
            return _GateResult(False, "daily_row_invalid", {"rows": len(parsed.rows)}), {}
        if market_value is None:
            return _GateResult(False, "daily_market_value_invalid", {"rows": len(parsed.rows)}), {}
        if code in values:
            return _GateResult(False, "daily_duplicate_stock", {"rows": len(parsed.rows)}), {}
        values[code] = market_value
    return _GateResult(True, "pass", {"rows": len(parsed.rows), "unique_stocks": len(values)}), values


def _intersection_gate(
    memberships: Mapping[str, tuple[str, str, str]],
    daily: Mapping[str, Decimal],
    trade_date: date,
    protocol_sha256: str,
) -> _GateResult:
    rows = [
        FrameRow(
            code,
            memberships[code][2],
            memberships[code][0],
            memberships[code][1],
            daily[code],
            trade_date,
            "SSE" if code.endswith(".SH") else "SZSE",
        )
        for code in sorted(set(memberships) & set(daily))
    ]
    prereg = PreregistrationRef(
        PROTOCOL_ID,
        PROTOCOL_VERSION,
        str(PROTOCOL_PATH.relative_to(PROJECT_ROOT)),
        protocol_sha256,
        str(PROTOCOL_HASH_PATH.relative_to(PROJECT_ROOT)),
        SAMPLING_SEED,
    )
    try:
        sample = select_sample(validate_frame(rows, trade_date, prereg))
    except Exception:
        return _GateResult(False, "intersection_cells_unfillable", {"rows": len(rows), "sample_rows": 0})
    if len(sample.entries) != 36:
        return _GateResult(
            False,
            "intersection_cells_unfillable",
            {"rows": len(rows), "sample_rows": len(sample.entries)},
        )
    cells = {(entry.super_stratum, entry.market_cap_stratum) for entry in sample.entries}
    if len(cells) != 12:
        return _GateResult(
            False,
            "intersection_cells_unfillable",
            {"rows": len(rows), "sample_rows": len(sample.entries), "cells": len(cells)},
        )
    return _GateResult(True, "pass", {"rows": len(rows), "sample_rows": 36, "cells": 12})


def _gate_value(gate: _GateResult) -> Mapping[str, object]:
    return {"passed": gate.passed, "code": gate.code, "metrics": dict(gate.metrics)}


def _seal_tree(path: Path) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        child.chmod(0o555 if child.is_dir() else 0o444)
    path.chmod(0o555)


def _manifest_value(
    authorization: CapabilityAuthorization,
    receipts: Sequence[CapabilityCallReceipt],
    *,
    complete: bool,
    capability_pass: bool,
    terminal_state: str,
    gates: Mapping[str, _GateResult],
    stop_code: str | None,
) -> Mapping[str, object]:
    completed = {receipt.call_id for receipt in receipts}
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "non_adoptable": True,
        "attempt_id": authorization.attempt_id,
        "authorization": {
            "authorization_id": authorization.authorization_id,
            "sha256": authorization.sha256,
            "canonical_document": dict(authorization.document),
        },
        "protocol": {
            "protocol_id": PROTOCOL_ID,
            "protocol_version": PROTOCOL_VERSION,
            "sha256": authorization.protocol_sha256,
        },
        "probe_trade_date": authorization.probe_trade_date.isoformat(),
        "expected_call_ids": [call.call_id for call in authorization.allowed_calls],
        "receipts": [
            {
                "ordinal": receipt.ordinal,
                "call_id": receipt.call_id,
                "relative_path": receipt.receipt_relative_path,
                "sha256": receipt.receipt_sha256,
                "blob_relative_path": receipt.blob_relative_path,
                "blob_sha256": receipt.blob_sha256,
                "byte_count": receipt.byte_count,
                "status": receipt.status,
                "error_code": receipt.error_code,
            }
            for receipt in receipts
        ],
        "missing_call_ids": [call.call_id for call in authorization.allowed_calls if call.call_id not in completed],
        "complete": complete,
        "capability_pass": capability_pass,
        "terminal_state": terminal_state,
        "gates": {name: _gate_value(gate) for name, gate in sorted(gates.items())},
        "stop_code": stop_code,
        "generator": {
            "relative_path": Path(__file__).name,
            "sha256": _sha256(Path(__file__).read_bytes()),
        },
    }


def _publish_attempt(
    output_root: Path,
    staging: Path,
    authorization: CapabilityAuthorization,
    receipts: Sequence[CapabilityCallReceipt],
    *,
    complete: bool,
    capability_pass: bool,
    terminal_state: str,
    gates: Mapping[str, _GateResult],
    stop_code: str | None,
    token: str,
) -> CapabilityProbeResult:
    manifest = _manifest_value(
        authorization,
        receipts,
        complete=complete,
        capability_pass=capability_pass,
        terminal_state=terminal_state,
        gates=gates,
        stop_code=stop_code,
    )
    manifest_raw = canonical_json_bytes(manifest)
    token_bytes = token.encode("utf-8")
    if token_bytes in manifest_raw:
        raise CapabilitySecurityError("token_artifact_leak")
    for path in staging.rglob("*"):
        if path.is_symlink():
            raise CapabilitySecurityError("artifact_symlink_detected")
        if path.is_file() and token_bytes in path.read_bytes():
            raise CapabilitySecurityError("token_artifact_leak")
    manifest_path = staging / "capability-probe-manifest.json"
    _create_only(manifest_path, manifest_raw)
    final_dir = output_root / f"probe-{authorization.attempt_id}"
    if final_dir.exists() or final_dir.is_symlink():
        raise CapabilitySecurityError("attempt_already_exists")
    _seal_tree(staging)
    os.rename(staging, final_dir)
    _fsync_directory(output_root)
    result = CapabilityProbeResult(
        final_dir,
        final_dir / manifest_path.name,
        _sha256(manifest_raw),
        authorization.authorization_id,
        authorization.attempt_id,
        complete,
        capability_pass,
        terminal_state,
        tuple(receipts),
    )
    verify_capability_probe(final_dir)
    return result


def _clock_value(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise CapabilityAuthorizationError("clock_not_timezone_aware")
    return value.astimezone(ZoneInfo("Asia/Shanghai"))


def probe_m4_frame_source(
    *,
    authorization_path: Path,
    authorization_hash_path: Path,
    attempt_id: str,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    token: str | None = None,
    backend: CapabilityBackend | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
    protocol_path: Path = PROTOCOL_PATH,
    protocol_hash_path: Path = PROTOCOL_HASH_PATH,
) -> CapabilityProbeResult:
    """Execute at most one call per matrix item and seal a non-adoptable result."""
    active_clock = clock or (lambda: datetime.now(tz=ZoneInfo("Asia/Shanghai")))
    initial_now = now if now is not None else _clock_value(active_clock)
    authorization = load_capability_authorization(
        authorization_path,
        authorization_hash_path,
        attempt_id=attempt_id,
        output_root=output_root,
        now=initial_now,
        protocol_path=protocol_path,
        protocol_hash_path=protocol_hash_path,
    )
    active_token = token if token is not None else _load_token()
    if not active_token or any(character.isspace() for character in active_token):
        raise CapabilitySecurityError("token_missing_or_malformed")
    active_backend = backend if backend is not None else TushareCapabilityBackend(active_token)
    with _output_lock(output_root):
        final_dir = output_root / f"probe-{attempt_id}"
        staging = output_root / f".in-progress-{attempt_id}"
        if final_dir.exists() or staging.exists() or final_dir.is_symlink() or staging.is_symlink():
            raise CapabilitySecurityError("attempt_already_exists")
        staging.mkdir(mode=0o700)
        receipts: list[CapabilityCallReceipt] = []
        parsed_by_call: dict[str, _ParsedResponse] = {}
        total_bytes = 0
        try:
            for ordinal, call in enumerate(authorization.allowed_calls, start=1):
                current_time = _clock_value(active_clock)
                _reload_authorization(
                    authorization,
                    authorization_path,
                    authorization_hash_path,
                    output_root,
                    current_time,
                    protocol_path,
                    protocol_hash_path,
                )
                response: CapabilityResponse | None = None
                parsed: _ParsedResponse | None = None
                error_code: str | None = None
                try:
                    response = active_backend.call(call)
                    _reload_authorization(
                        authorization,
                        authorization_path,
                        authorization_hash_path,
                        output_root,
                        _clock_value(active_clock),
                        protocol_path,
                        protocol_hash_path,
                    )
                    _validate_transport(response)
                    token_bytes = active_token.encode("utf-8")
                    response_metadata = "\n".join(
                        (response.request_url, response.response_url, response.content_type)
                    ).encode("utf-8")
                    if token_bytes in response.raw or token_bytes in response_metadata:
                        raise CapabilitySecurityError("token_echo_detected")
                    total_bytes += len(response.raw)
                    if total_bytes > MAX_TOTAL_BYTES:
                        raise CapabilitySecurityError("aggregate_response_limit_exceeded")
                    blob_sha256, blob_relative = _content_addressed_blob(staging, response.raw)
                    if not 200 <= response.status_code < 300:
                        error_code = _http_category(response.status_code)
                    else:
                        try:
                            parsed = _parse_response(response.raw, call)
                        except CapabilityError as exc:
                            error_code = exc.error_code
                except CapabilityTransportError as exc:
                    error_code = exc.error_code
                    blob_sha256 = None
                    blob_relative = None
                status = "ok" if error_code is None else "failed"
                captured_at = _clock_value(active_clock)
                _reload_authorization(
                    authorization,
                    authorization_path,
                    authorization_hash_path,
                    output_root,
                    captured_at,
                    protocol_path,
                    protocol_hash_path,
                )
                receipt_value = _receipt_value(
                    ordinal=ordinal,
                    call=call,
                    captured_at=captured_at,
                    status=status,
                    error_code=error_code,
                    response=response,
                    blob_sha256=blob_sha256,
                    blob_relative_path=blob_relative,
                    row_count=len(parsed.rows) if parsed is not None else None,
                )
                receipt = _publish_receipt(staging, receipt_value)
                receipts.append(receipt)
                if parsed is not None:
                    parsed_by_call[call.call_id] = parsed
            classification = _classification_gate(parsed_by_call.get(CLASSIFICATION_CALL.call_id))
            members, memberships = _members_gate(parsed_by_call.get(MEMBERS_CALL.call_id))
            daily_call = authorization.allowed_calls[2]
            daily, daily_values = _daily_gate(parsed_by_call.get(daily_call.call_id), authorization.probe_trade_date)
            intersection = (
                _intersection_gate(
                    memberships,
                    daily_values,
                    authorization.probe_trade_date,
                    authorization.protocol_sha256,
                )
                if members.passed and daily.passed
                else _GateResult(False, "intersection_inputs_unqualified", {})
            )
            gates = {
                "classification": classification,
                "members": members,
                "daily": daily,
                "intersection": intersection,
            }
            complete = len(receipts) == len(authorization.allowed_calls)
            capability_pass = complete and all(gate.passed for gate in gates.values())
            terminal_state = (
                "ELIGIBLE_FOR_FULL_CAPTURE_AUTHORIZATION" if capability_pass else "NO_QUALIFIED_FRAME_SOURCE"
            )
            return _publish_attempt(
                output_root,
                staging,
                authorization,
                receipts,
                complete=complete,
                capability_pass=capability_pass,
                terminal_state=terminal_state,
                gates=gates,
                stop_code=None,
                token=active_token,
            )
        except BaseException as exc:
            if staging.exists():
                stop_code = exc.error_code if isinstance(exc, CapabilityError) else "process_control_interrupted"
                try:
                    _publish_attempt(
                        output_root,
                        staging,
                        authorization,
                        receipts,
                        complete=False,
                        capability_pass=False,
                        terminal_state="INCOMPLETE",
                        gates={},
                        stop_code=stop_code,
                        token=active_token,
                    )
                except BaseException:
                    pass
            raise


def _safe_relative(value: object, prefix: str) -> str:
    if not isinstance(value, str):
        raise CapabilityVerificationError("manifest_path_invalid")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not value.startswith(prefix + "/"):
        raise CapabilityVerificationError("manifest_path_invalid")
    return value


def verify_capability_probe(attempt_dir: Path) -> CapabilityProbeResult:
    """Reproduce manifest, receipt, blob, protocol, and generator integrity checks offline."""
    if attempt_dir.is_symlink() or not attempt_dir.is_dir() or attempt_dir.name.startswith(".in-progress-"):
        raise CapabilityVerificationError("attempt_directory_invalid")
    manifest_path = attempt_dir / "capability-probe-manifest.json"
    manifest_raw = _read_regular(manifest_path, maximum=8 * 1024 * 1024)
    try:
        value = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityVerificationError("manifest_json_invalid") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != manifest_raw:
        raise CapabilityVerificationError("manifest_not_canonical")
    exact_manifest_fields = {
        "schema_version",
        "non_adoptable",
        "attempt_id",
        "authorization",
        "protocol",
        "probe_trade_date",
        "expected_call_ids",
        "receipts",
        "missing_call_ids",
        "complete",
        "capability_pass",
        "terminal_state",
        "gates",
        "stop_code",
        "generator",
    }
    if set(value) != exact_manifest_fields:
        raise CapabilityVerificationError("manifest_schema_or_adoptability_invalid")
    if value.get("schema_version") != MANIFEST_SCHEMA_VERSION or value.get("non_adoptable") is not True:
        raise CapabilityVerificationError("manifest_schema_or_adoptability_invalid")
    protocol = value.get("protocol")
    if (
        not isinstance(protocol, dict)
        or set(protocol) != {"protocol_id", "protocol_version", "sha256"}
        or protocol.get("protocol_id") != PROTOCOL_ID
        or protocol.get("protocol_version") != PROTOCOL_VERSION
        or protocol.get("sha256") != _protocol_sha256(PROTOCOL_PATH, PROTOCOL_HASH_PATH)
    ):
        raise CapabilityVerificationError("manifest_protocol_invalid")
    generator = value.get("generator")
    if (
        not isinstance(generator, dict)
        or generator.get("relative_path") != Path(__file__).name
        or generator.get("sha256") != _sha256(Path(__file__).read_bytes())
    ):
        raise CapabilityVerificationError("manifest_generator_invalid")
    authorization = value.get("authorization")
    if (
        not isinstance(authorization, dict)
        or set(authorization) != {"authorization_id", "sha256", "canonical_document"}
        or not isinstance(authorization.get("canonical_document"), dict)
    ):
        raise CapabilityVerificationError("manifest_authorization_invalid")
    auth_raw = canonical_json_bytes(authorization["canonical_document"])
    if authorization.get("sha256") != _sha256(auth_raw):
        raise CapabilityVerificationError("manifest_authorization_invalid")
    try:
        trade_date = date.fromisoformat(str(value["probe_trade_date"]))
    except (KeyError, ValueError) as exc:
        raise CapabilityVerificationError("manifest_trade_date_invalid") from exc
    matrix = capability_call_matrix(trade_date)
    canonical_document = authorization["canonical_document"]
    if (
        canonical_document.get("schema_version") != AUTHORIZATION_SCHEMA_VERSION
        or canonical_document.get("authorization_id") != authorization.get("authorization_id")
        or canonical_document.get("attempt_id") != value.get("attempt_id")
        or canonical_document.get("protocol_sha256") != protocol.get("sha256")
        or canonical_document.get("probe_trade_date") != trade_date.isoformat()
        or canonical_document.get("allowed_calls") != [call.authorization_value() for call in matrix]
    ):
        raise CapabilityVerificationError("manifest_authorization_invalid")
    receipt_records = value.get("receipts")
    if not isinstance(receipt_records, list):
        raise CapabilityVerificationError("manifest_receipts_invalid")
    expected_files = {manifest_path.name}
    receipts: list[CapabilityCallReceipt] = []
    seen_calls: set[str] = set()
    parsed_by_call: dict[str, _ParsedResponse] = {}
    for record in receipt_records:
        if not isinstance(record, dict):
            raise CapabilityVerificationError("manifest_receipts_invalid")
        relative = _safe_relative(record.get("relative_path"), "receipts")
        receipt_raw = _read_regular(attempt_dir / relative, maximum=1024 * 1024)
        if record.get("sha256") != _sha256(receipt_raw):
            raise CapabilityVerificationError("receipt_hash_drift")
        try:
            receipt_value = json.loads(receipt_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CapabilityVerificationError("receipt_json_invalid") from exc
        if canonical_json_bytes(receipt_value) != receipt_raw or receipt_value.get("non_adoptable") is not True:
            raise CapabilityVerificationError("receipt_schema_or_adoptability_invalid")
        if receipt_value.get("schema_version") != RECEIPT_SCHEMA_VERSION:
            raise CapabilityVerificationError("receipt_schema_or_adoptability_invalid")
        if set(receipt_value) != {
            "schema_version",
            "non_adoptable",
            "ordinal",
            "call_id",
            "request",
            "response",
        }:
            raise CapabilityVerificationError("receipt_schema_or_adoptability_invalid")
        response = receipt_value.get("response")
        call_id = receipt_value.get("call_id")
        if not isinstance(response, dict) or not isinstance(call_id, str) or call_id in seen_calls:
            raise CapabilityVerificationError("receipt_manifest_mismatch")
        if set(response) != {
            "captured_at",
            "status",
            "error_code",
            "http_status_category",
            "content_type",
            "byte_count",
            "blob_sha256",
            "blob_relative_path",
            "row_count",
        }:
            raise CapabilityVerificationError("receipt_schema_or_adoptability_invalid")
        seen_calls.add(call_id)
        ordinal = receipt_value.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or not 1 <= ordinal <= len(matrix):
            raise CapabilityVerificationError("receipt_manifest_mismatch")
        expected_call = matrix[ordinal - 1]
        expected_request = {
            "url": API_URL,
            "method": "POST",
            "api_name": expected_call.api_name,
            "params": dict(expected_call.params),
            "fields": list(expected_call.fields),
            "redirect_policy": "deny",
        }
        if call_id != expected_call.call_id or receipt_value.get("request") != expected_request:
            raise CapabilityVerificationError("receipt_request_invalid")
        blob_relative = response.get("blob_relative_path")
        blob_sha256 = response.get("blob_sha256")
        byte_count = response.get("byte_count")
        if blob_relative is not None:
            safe_blob = _safe_relative(blob_relative, "blobs")
            blob_raw = _read_regular(attempt_dir / safe_blob, maximum=MAX_RESPONSE_BYTES)
            if blob_sha256 != _sha256(blob_raw) or byte_count != len(blob_raw):
                raise CapabilityVerificationError("blob_hash_or_size_drift")
            if safe_blob != f"blobs/{blob_sha256}.blob":
                raise CapabilityVerificationError("blob_path_invalid")
            expected_files.add(safe_blob)
        elif blob_sha256 is not None or byte_count != 0:
            raise CapabilityVerificationError("receipt_blob_binding_invalid")
        if (
            record.get("call_id") != call_id
            or record.get("blob_relative_path") != blob_relative
            or record.get("blob_sha256") != blob_sha256
            or record.get("byte_count") != byte_count
            or record.get("status") != response.get("status")
            or record.get("error_code") != response.get("error_code")
        ):
            raise CapabilityVerificationError("receipt_manifest_mismatch")
        status = response.get("status")
        error_code = response.get("error_code")
        category = response.get("http_status_category")
        row_count = response.get("row_count")
        if status not in {"ok", "failed"} or (status == "ok") != (error_code is None):
            raise CapabilityVerificationError("receipt_status_invalid")
        if blob_relative is None:
            if (
                error_code
                not in {
                    "transport_timeout",
                    "transport_tls",
                    "transport_connection",
                    "transport_unavailable",
                    "response_too_large",
                }
                or category is not None
                or row_count is not None
            ):
                raise CapabilityVerificationError("receipt_status_invalid")
        else:
            if category not in {"http_1xx", "http_2xx", "http_3xx", "http_4xx", "http_5xx"}:
                raise CapabilityVerificationError("receipt_status_invalid")
            if category == "http_2xx":
                try:
                    parsed = _parse_response(blob_raw, expected_call)
                except CapabilityError as exc:
                    if error_code != exc.error_code or row_count is not None:
                        raise CapabilityVerificationError("receipt_parse_outcome_invalid") from exc
                else:
                    if error_code is not None or row_count != len(parsed.rows):
                        raise CapabilityVerificationError("receipt_parse_outcome_invalid")
                    parsed_by_call[call_id] = parsed
            elif error_code != category or row_count is not None:
                raise CapabilityVerificationError("receipt_status_invalid")
        receipts.append(
            CapabilityCallReceipt(
                ordinal,
                call_id,
                datetime.fromisoformat(str(response["captured_at"])),
                str(response["status"]),
                str(response["error_code"]) if response["error_code"] is not None else None,
                str(response["http_status_category"]) if response["http_status_category"] is not None else None,
                str(response["content_type"]) if response["content_type"] is not None else None,
                int(byte_count),
                str(blob_sha256) if blob_sha256 is not None else None,
                str(blob_relative) if blob_relative is not None else None,
                int(row_count) if row_count is not None else None,
                relative,
                str(record["sha256"]),
            )
        )
        expected_files.add(relative)
    expected_calls = value.get("expected_call_ids")
    missing_calls = value.get("missing_call_ids")
    if (
        expected_calls != [call.call_id for call in matrix]
        or missing_calls != [call_id for call_id in expected_calls if call_id not in seen_calls]
        or value.get("complete") != (len(receipts) == len(expected_calls))
    ):
        raise CapabilityVerificationError("manifest_call_matrix_invalid")
    if [receipt.ordinal for receipt in receipts] != list(range(1, len(receipts) + 1)):
        raise CapabilityVerificationError("manifest_call_matrix_invalid")
    actual_files: set[str] = set()
    for path in attempt_dir.rglob("*"):
        if path.is_symlink():
            raise CapabilityVerificationError("attempt_contains_symlink")
        if path.is_file():
            actual_files.add(path.relative_to(attempt_dir).as_posix())
    if actual_files != expected_files:
        raise CapabilityVerificationError("attempt_file_set_invalid")
    complete = bool(value["complete"])
    capability_pass = bool(value.get("capability_pass"))
    terminal_state = str(value.get("terminal_state"))
    classification = _classification_gate(parsed_by_call.get(CLASSIFICATION_CALL.call_id))
    members, memberships = _members_gate(parsed_by_call.get(MEMBERS_CALL.call_id))
    daily, daily_values = _daily_gate(parsed_by_call.get(matrix[2].call_id), trade_date)
    intersection = (
        _intersection_gate(memberships, daily_values, trade_date, str(protocol["sha256"]))
        if members.passed and daily.passed
        else _GateResult(False, "intersection_inputs_unqualified", {})
    )
    reproduced_gates = {
        "classification": classification,
        "members": members,
        "daily": daily,
        "intersection": intersection,
    }
    expected_gates = {name: _gate_value(gate) for name, gate in sorted(reproduced_gates.items())} if complete else {}
    reproduced_pass = complete and all(gate.passed for gate in reproduced_gates.values())
    if value.get("gates") != expected_gates or capability_pass != reproduced_pass:
        raise CapabilityVerificationError("manifest_gate_outcome_invalid")
    if (complete and value.get("stop_code") is not None) or (
        not complete and not isinstance(value.get("stop_code"), str)
    ):
        raise CapabilityVerificationError("manifest_outcome_invalid")
    if capability_pass and (not complete or terminal_state != "ELIGIBLE_FOR_FULL_CAPTURE_AUTHORIZATION"):
        raise CapabilityVerificationError("manifest_outcome_invalid")
    if complete and not capability_pass and terminal_state != "NO_QUALIFIED_FRAME_SOURCE":
        raise CapabilityVerificationError("manifest_outcome_invalid")
    if not complete and (capability_pass or terminal_state != "INCOMPLETE"):
        raise CapabilityVerificationError("manifest_outcome_invalid")
    return CapabilityProbeResult(
        attempt_dir,
        manifest_path,
        _sha256(manifest_raw),
        str(authorization.get("authorization_id")),
        str(value.get("attempt_id")),
        complete,
        capability_pass,
        terminal_state,
        tuple(receipts),
    )


def reject_non_adoptable_probe(attempt_dir: Path) -> None:
    """Fail closed if a capability attempt is offered to capture assembly or resume."""
    manifest = attempt_dir / "capability-probe-manifest.json"
    if manifest.exists() or manifest.is_symlink():
        raise CapabilityVerificationError("non_adoptable_probe_rejected")


__all__ = [
    "AUTHORIZATION_SCHEMA_VERSION",
    "CapabilityAuthorization",
    "CapabilityAuthorizationError",
    "CapabilityBackend",
    "CapabilityCall",
    "CapabilityCallReceipt",
    "CapabilityError",
    "CapabilityProbeResult",
    "CapabilityResponse",
    "CapabilitySecurityError",
    "CapabilityVerificationError",
    "capability_call_matrix",
    "load_capability_authorization",
    "probe_m4_frame_source",
    "reject_non_adoptable_probe",
    "verify_capability_probe",
]
