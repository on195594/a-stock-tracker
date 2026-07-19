"""Frozen protocol primitives for the M4 v1.3.1 segmented REST attempts."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, TypeAlias

from a_stock_tracker.qualitative.audit import SW2021_INDUSTRIES, canonical_json_bytes
from a_stock_tracker.paths import PROJECT_ROOT

PROTOCOL_ID = "qualitative-v2-m4-prereg-v1.3.1"
PROTOCOL_VERSION = "1.3.1"
PROTOCOL_RELATIVE_PATH = "docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.1.md"
PROTOCOL_PATH = PROJECT_ROOT / PROTOCOL_RELATIVE_PATH
PROTOCOL_HASH_RELATIVE_PATH = "reviews/milestone-004-preregistration-v1.3.1/preregistration.sha256"
PROTOCOL_HASH_PATH = PROJECT_ROOT / PROTOCOL_HASH_RELATIVE_PATH
GOLDEN_VECTORS_RELATIVE_PATH = "reviews/milestone-004-preregistration-v1.3.1/golden-vectors.json"
GOLDEN_VECTORS_PATH = PROJECT_ROOT / GOLDEN_VECTORS_RELATIVE_PATH
SUPERSEDES_SHA256 = "0ad8c6b3d5175af96409e570bdc17839ac7cf266227410f2e9e9ea756a7a12df"
ORIGIN = "https://api.tushare.pro"
CREDENTIAL_ENV_VAR = "TUSHARE_TOKEN"
FRAME_AUTH_SCHEMA = "m4-segmented-rest-frame-authorization-v2"
DATE_AUTH_SCHEMA = "m4-date-selection-authorization-v2"
RECEIPT_SCHEMA = "m4-segmented-rest-receipt-v2"
STOP_SCHEMA = "m4-attempt-stop-v2"
LEDGER_SCHEMA = "m4-exclusion-ledger-v2"
DATE_EVIDENCE_SCHEMA = "m4-date-selection-evidence-v2"
MANIFEST_SCHEMA = "m4-segmented-rest-attempt-manifest-v2"
SUPERVISOR_COMMIT_SCHEMA = "m4-segmented-rest-supervisor-commit-v2"
FRAME_RESPONSE_LIMIT = 33_554_432
FRAME_ATTEMPT_LIMIT = 134_217_728
DATE_RESPONSE_LIMIT = 4_194_304
DATE_ATTEMPT_LIMIT = 8_388_608

GENERATOR_FILES = (
    "qualitative_v2_audit.py",
    "qualitative_v2_m4_segmented_core.py",
    "qualitative_v2_m4_segmented_rest.py",
    "qualitative_v2_m4_segmented_runtime.py",
    "qualitative_v2_m4_segmented_verify.py",
)
GENERATOR_PATHS = {
    "qualitative_v2_audit.py": "a_stock_tracker/qualitative/audit.py",
    "qualitative_v2_m4_segmented_core.py": "a_stock_tracker/qualitative/archive_m4/segmented_core.py",
    "qualitative_v2_m4_segmented_rest.py": "a_stock_tracker/qualitative/archive_m4/segmented_rest.py",
    "qualitative_v2_m4_segmented_runtime.py": "a_stock_tracker/qualitative/archive_m4/segmented_runtime.py",
    "qualitative_v2_m4_segmented_verify.py": "a_stock_tracker/qualitative/archive_m4/segmented_verify.py",
}

ROOTS = {
    "capability": "artifacts/milestone-004/v1.3.1/capability-probes",
    "capture": "artifacts/milestone-004/v1.3.1/captures",
    "date_selection_evidence": "artifacts/milestone-004/v1.3.1/date-selection-evidence",
}

ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
HASH_RE = re.compile(r"[0-9a-f]{64}")
TOKEN_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
CODE_RE = re.compile(r"[0-9]{6}\.(SH|SZ|BJ)")
DATE_TEXT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+08:00")


class SegmentedRestError(RuntimeError):
    """Finite, credential-free base failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AuthorizationError(SegmentedRestError):
    """Authorization or frozen provenance is invalid."""


class VerificationError(SegmentedRestError):
    """A sealed attempt cannot be reproduced offline."""


class SecurityError(SegmentedRestError):
    """A path, secret, publication, or transport invariant failed."""


@dataclass(frozen=True, slots=True)
class JsonNumber:
    """A JSON number retained as its exact source lexeme."""

    lexeme: str


def _number(value: str) -> JsonNumber:
    if value.startswith("-"):
        try:
            if Decimal(value).is_zero():
                raise ValueError("negative_zero")
        except InvalidOperation as exc:
            raise ValueError("number_invalid") from exc
    return JsonNumber(value)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non_finite:{value}")


def _structural_integer(value: str) -> int:
    if value == "-0":
        raise ValueError("negative_zero")
    return int(value)


def _reject_structural_float(value: str) -> None:
    raise ValueError(f"structural_float:{value}")


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def strict_json_loads(raw: bytes, *, numbers: bool = True) -> object:
    """Parse UTF-8 JSON while rejecting duplicate keys and lossy numbers."""
    try:
        text = raw.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_object,
            parse_int=_number if numbers else _structural_integer,
            parse_float=_number if numbers else _reject_structural_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise VerificationError("json_invalid") from exc


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_regular(path: Path, *, maximum: int, require_read_only: bool = False) -> bytes:
    """Read one stable, unlinked regular file without following a symlink."""
    try:
        before = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise VerificationError("file_missing_or_unsafe") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size > maximum
        or (require_read_only and stat.S_IMODE(before.st_mode) != 0o444)
    ):
        raise VerificationError("file_missing_or_unsafe")
    try:
        raw = path.read_bytes()
        after = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise VerificationError("file_read_failed") from exc
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_nlink)
    if identity(before) != identity(after):
        raise VerificationError("file_changed_during_read")
    return raw


def safe_project_relative(value: object, *, field: str = "path") -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise VerificationError(f"{field}_invalid")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or path.as_posix() != value:
        raise VerificationError(f"{field}_invalid")
    return value


def require_safe_project_path(path: Path, *, allow_missing_leaf: bool = False) -> None:
    """Reject symlinked ancestry and lexical escape from the real project root."""
    root = PROJECT_ROOT.resolve()
    try:
        relative = path.absolute().relative_to(root)
    except ValueError as exc:
        raise VerificationError("path_outside_project") from exc
    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        if not current.exists() and not current.is_symlink():
            if allow_missing_leaf and index == len(relative.parts) - 1:
                return
            raise VerificationError("path_missing")
        if current.is_symlink():
            raise VerificationError("symlinked_path_ancestry")
    if path.resolve() != current:
        raise VerificationError("path_alias_invalid")


def _exact_integer(value: object, expected: int, *, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise AuthorizationError(f"{field}_invalid")


def parse_date(value: object, *, field: str) -> date:
    if not isinstance(value, str) or DATE_TEXT_RE.fullmatch(value) is None:
        raise AuthorizationError(f"{field}_invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AuthorizationError(f"{field}_invalid") from exc


def parse_time(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or TIME_RE.fullmatch(value) is None:
        raise AuthorizationError(f"{field}_invalid")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None or parsed.utcoffset() != timedelta(hours=8) or parsed.microsecond:
        raise AuthorizationError(f"{field}_invalid")
    return parsed


def time_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(hours=8) or value.microsecond:
        raise SecurityError("clock_invalid")
    return value.isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class SegmentedRestCall:
    """One immutable authorization-bound REST call."""

    ordinal: int
    api_name: str
    params: tuple[tuple[str, str], ...]
    fields: tuple[str, ...]

    @property
    def call_id(self) -> str:
        params = dict(self.params)
        if self.api_name == "index_member_all":
            return f"tushare:index_member_all:{params['l1_code']}"
        if self.api_name == "stock_basic":
            return f"tushare:stock_basic:{params['exchange']}"
        if self.api_name in {"stock_st", "daily_basic"}:
            return f"tushare:{self.api_name}:{params['trade_date']}"
        if self.api_name == "trade_cal":
            return f"tushare:trade_cal:{params['exchange']}"
        return f"tushare:{self.api_name}"

    def authorization_value(self) -> dict[str, object]:
        return {
            "api_name": self.api_name,
            "fields": list(self.fields),
            "ordinal": self.ordinal,
            "params": dict(self.params),
        }

    def request_description(self) -> dict[str, object]:
        return {
            "api_name": self.api_name,
            "fields": list(self.fields),
            "method": "POST",
            "origin": ORIGIN,
            "params": dict(self.params),
        }


MEMBER_FIELDS = (
    "l1_code",
    "l1_name",
    "l2_code",
    "l2_name",
    "l3_code",
    "l3_name",
    "ts_code",
    "name",
    "in_date",
    "out_date",
    "is_new",
)


def _coerce_date(value: date | str, *, field: str) -> date:
    if isinstance(value, date):
        return value
    return parse_date(value, field=field)


def frame_call_matrix(trade_date: date | str) -> tuple[SegmentedRestCall, ...]:
    """Return the exact ordered 36-call frame matrix."""
    selected = _coerce_date(trade_date, field="trade_date")
    calls = [
        SegmentedRestCall(
            1, "index_classify", (("level", "L1"), ("src", "SW2021")), ("index_code", "industry_name", "level", "src")
        )
    ]
    for ordinal, code in enumerate(SW2021_INDUSTRIES, 2):
        calls.append(
            SegmentedRestCall(ordinal, "index_member_all", (("l1_code", code), ("is_new", "Y")), MEMBER_FIELDS)
        )
    calls.extend(
        (
            SegmentedRestCall(
                33,
                "stock_basic",
                (("exchange", "SSE"), ("list_status", "L")),
                ("ts_code", "name", "market", "exchange", "curr_type", "list_status", "list_date"),
            ),
            SegmentedRestCall(
                34,
                "stock_basic",
                (("exchange", "SZSE"), ("list_status", "L")),
                ("ts_code", "name", "market", "exchange", "curr_type", "list_status", "list_date"),
            ),
            SegmentedRestCall(
                35,
                "stock_st",
                (("trade_date", selected.strftime("%Y%m%d")),),
                ("ts_code", "name", "trade_date", "type", "type_name"),
            ),
            SegmentedRestCall(
                36, "daily_basic", (("trade_date", selected.strftime("%Y%m%d")),), ("ts_code", "trade_date", "total_mv")
            ),
        )
    )
    return tuple(calls)


def date_evidence_call_matrix(proposed_sampling_date: date | str) -> tuple[SegmentedRestCall, ...]:
    """Return the exact SSE/SZSE 125-day calendar matrix."""
    selected = _coerce_date(proposed_sampling_date, field="proposed_sampling_date")
    start = (selected - timedelta(days=62)).strftime("%Y%m%d")
    end = (selected + timedelta(days=62)).strftime("%Y%m%d")
    fields = ("exchange", "cal_date", "is_open", "pretrade_date")
    return tuple(
        SegmentedRestCall(
            ordinal,
            "trade_cal",
            (("exchange", exchange), ("start_date", start), ("end_date", end)),
            fields,
        )
        for ordinal, exchange in enumerate(("SSE", "SZSE"), 1)
    )


@dataclass(frozen=True, slots=True)
class FrameAuthorization:
    """Strict canonical authorization for capability or formal capture."""

    authorization_id: str
    attempt_id: str
    mode: str
    protocol_path: str
    protocol_sha256: str
    supersedes_sha256: str
    attempt_output_dir: str
    trade_date: date
    not_before: datetime
    not_after: datetime
    calls: tuple[SegmentedRestCall, ...]
    capability_manifest_ref: Mapping[str, object] | None
    date_evidence_ref: Mapping[str, object] | None
    sha256: str
    relative_path: str
    canonical_bytes: bytes
    response_byte_limit: int = FRAME_RESPONSE_LIMIT
    attempt_byte_limit: int = FRAME_ATTEMPT_LIMIT


@dataclass(frozen=True, slots=True)
class DateEvidenceAuthorization:
    """Strict canonical authorization for one calendar-evidence attempt."""

    authorization_id: str
    attempt_id: str
    purpose: str
    protocol_path: str
    protocol_sha256: str
    supersedes_sha256: str
    attempt_output_dir: str
    proposed_sampling_date: date
    calendar_start: date
    calendar_end: date
    not_before: datetime
    not_after: datetime
    calls: tuple[SegmentedRestCall, ...]
    capability_manifest_ref: Mapping[str, object]
    sha256: str
    relative_path: str
    canonical_bytes: bytes
    response_byte_limit: int = DATE_RESPONSE_LIMIT
    attempt_byte_limit: int = DATE_ATTEMPT_LIMIT


Authorization: TypeAlias = FrameAuthorization | DateEvidenceAuthorization


@dataclass(frozen=True, slots=True)
class CallReceipt:
    """Sanitized receipt identity for one attempted ordinal."""

    ordinal: int
    call_id: str
    state: str
    captured_at: datetime
    status_category: str
    error_code: str | None
    byte_count: int | None
    blob_relative_path: str | None
    blob_sha256: str | None
    receipt_relative_path: str
    receipt_sha256: str
    sealed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AttemptResult:
    """Offline-reproduced identity and outcome for a sealed attempt."""

    attempt_dir: Path
    manifest_path: Path
    manifest_sha256: str
    authorization_id: str
    attempt_id: str
    attempt_kind: str
    complete: bool
    overall_pass: bool
    disposition: str
    capture_input_eligible: bool
    receipts: tuple[CallReceipt, ...]


FRAME_AUTH_FIELDS = {
    "attempt_byte_limit",
    "attempt_id",
    "attempt_output_dir",
    "authorization_id",
    "calls",
    "capability_manifest_ref",
    "credential_env_var",
    "date_evidence_ref",
    "max_attempts_per_ordinal",
    "mode",
    "not_after",
    "not_before",
    "origin",
    "protocol_path",
    "protocol_sha256",
    "response_byte_limit",
    "schema_version",
    "supersedes_sha256",
    "trade_date",
    "trade_date_kind",
}
DATE_AUTH_FIELDS = {
    "attempt_byte_limit",
    "attempt_id",
    "attempt_output_dir",
    "authorization_id",
    "calendar_end",
    "calendar_start",
    "calls",
    "capability_manifest_ref",
    "credential_env_var",
    "max_attempts_per_ordinal",
    "not_after",
    "not_before",
    "origin",
    "proposed_sampling_date",
    "protocol_path",
    "protocol_sha256",
    "purpose",
    "response_byte_limit",
    "schema_version",
    "supersedes_sha256",
}


def protocol_sha256() -> str:
    require_safe_project_path(PROTOCOL_PATH)
    require_safe_project_path(PROTOCOL_HASH_PATH)
    raw = read_regular(PROTOCOL_PATH, maximum=2 * 1024 * 1024)
    golden = read_regular(GOLDEN_VECTORS_PATH, maximum=2 * 1024 * 1024)
    checksum = read_regular(PROTOCOL_HASH_PATH, maximum=4096)
    expected = (
        f"{sha256_bytes(raw)}  {PROTOCOL_RELATIVE_PATH}\n{sha256_bytes(golden)}  {GOLDEN_VECTORS_RELATIVE_PATH}\n"
    ).encode("ascii")
    if checksum != expected:
        raise AuthorizationError("protocol_drift")
    return sha256_bytes(raw)


def generator_references() -> list[dict[str, object]]:
    for path in GENERATOR_PATHS.values():
        require_safe_project_path(PROJECT_ROOT / path)
    return [
        {
            "relative_path": name,
            "sha256": sha256_bytes(read_regular(PROJECT_ROOT / GENERATOR_PATHS[name], maximum=4 * 1024 * 1024)),
        }
        for name in sorted(GENERATOR_FILES)
    ]


def _canonical_authorization(path: Path, checksum_path: Path) -> tuple[dict[str, object], bytes, str, str]:
    if checksum_path.absolute() != path.with_suffix(".sha256").absolute():
        raise AuthorizationError("authorization_hash_path_invalid")
    require_safe_project_path(path)
    require_safe_project_path(checksum_path)
    raw = read_regular(path, maximum=2 * 1024 * 1024)
    digest = sha256_bytes(raw)
    try:
        relative = path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise AuthorizationError("authorization_path_outside_project") from exc
    expected_checksum = f"{digest}  {relative}\n".encode("ascii")
    if read_regular(checksum_path, maximum=4096) != expected_checksum:
        raise AuthorizationError("authorization_hash_invalid")
    value = strict_json_loads(raw, numbers=False)
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise AuthorizationError("authorization_not_canonical")
    return value, raw, digest, relative


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None or value in {".", ".."}:
        raise AuthorizationError(f"{field}_invalid")
    return value


def _reference(value: object, *, date_evidence: bool = False) -> Mapping[str, object]:
    fields = {"attempt_id", "relative_path", "sha256"} | ({"authorization_id"} if date_evidence else set())
    if not isinstance(value, dict) or set(value) != fields:
        raise AuthorizationError("reference_invalid")
    _identity(value.get("attempt_id"), "reference_attempt_id")
    if date_evidence:
        _identity(value.get("authorization_id"), "reference_authorization_id")
    safe_project_relative(value.get("relative_path"), field="reference_path")
    if not isinstance(value.get("sha256"), str) or HASH_RE.fullmatch(str(value["sha256"])) is None:
        raise AuthorizationError("reference_hash_invalid")
    return MappingProxyType(dict(value))


def _common(value: Mapping[str, object], *, attempt_kind: str) -> tuple[str, str, datetime, datetime, str]:
    auth_id = _identity(value.get("authorization_id"), "authorization_id")
    attempt_id = _identity(value.get("attempt_id"), "attempt_id")
    if value.get("protocol_path") != PROTOCOL_RELATIVE_PATH or value.get("protocol_sha256") != protocol_sha256():
        raise AuthorizationError("protocol_mismatch")
    if value.get("supersedes_sha256") != SUPERSEDES_SHA256:
        raise AuthorizationError("lineage_mismatch")
    if value.get("origin") != ORIGIN or value.get("credential_env_var") != CREDENTIAL_ENV_VAR:
        raise AuthorizationError("transport_boundary_invalid")
    _exact_integer(value.get("max_attempts_per_ordinal"), 1, field="attempt_count")
    not_before = parse_time(value.get("not_before"), field="not_before")
    not_after = parse_time(value.get("not_after"), field="not_after")
    if not_before > not_after:
        raise AuthorizationError("authorization_window_invalid")
    expected_output = f"{ROOTS[attempt_kind]}/{attempt_id}"
    if value.get("attempt_output_dir") != expected_output:
        raise AuthorizationError("attempt_output_dir_invalid")
    return auth_id, attempt_id, not_before, not_after, expected_output


def _load_frame(value: dict[str, object], raw: bytes, digest: str, relative: str) -> FrameAuthorization:
    if set(value) != FRAME_AUTH_FIELDS or value.get("schema_version") != FRAME_AUTH_SCHEMA:
        raise AuthorizationError("frame_authorization_schema_invalid")
    mode = value.get("mode")
    if mode not in {"capability", "capture"}:
        raise AuthorizationError("frame_mode_invalid")
    auth_id, attempt_id, not_before, not_after, output = _common(value, attempt_kind=str(mode))
    trade_date = parse_date(value.get("trade_date"), field="trade_date")
    matrix = frame_call_matrix(trade_date)
    if value.get("calls") != [call.authorization_value() for call in matrix]:
        raise AuthorizationError("call_matrix_invalid")
    _exact_integer(value.get("response_byte_limit"), FRAME_RESPONSE_LIMIT, field="response_byte_limit")
    _exact_integer(value.get("attempt_byte_limit"), FRAME_ATTEMPT_LIMIT, field="attempt_byte_limit")
    capability_ref = value.get("capability_manifest_ref")
    evidence_ref = value.get("date_evidence_ref")
    if mode == "capability":
        if value.get("trade_date_kind") != "probe_trade_date" or capability_ref is not None or evidence_ref is not None:
            raise AuthorizationError("capability_cross_field_invalid")
    else:
        if value.get("trade_date_kind") != "sampling_date":
            raise AuthorizationError("capture_cross_field_invalid")
        capability_ref = _reference(capability_ref)
        evidence_ref = _reference(evidence_ref, date_evidence=True)
        day_start = datetime.combine(trade_date, datetime.min.time(), tzinfo=not_before.tzinfo).replace(hour=18)
        day_end = datetime.combine(trade_date, datetime.min.time(), tzinfo=not_before.tzinfo).replace(
            hour=23, minute=59, second=59
        )
        if not_before < day_start or not_after > day_end:
            raise AuthorizationError("capture_window_invalid")
    return FrameAuthorization(
        auth_id,
        attempt_id,
        str(mode),
        PROTOCOL_RELATIVE_PATH,
        str(value["protocol_sha256"]),
        SUPERSEDES_SHA256,
        output,
        trade_date,
        not_before,
        not_after,
        matrix,
        capability_ref,
        evidence_ref,
        digest,
        relative,
        raw,
    )


def _load_date(value: dict[str, object], raw: bytes, digest: str, relative: str) -> DateEvidenceAuthorization:
    if set(value) != DATE_AUTH_FIELDS or value.get("schema_version") != DATE_AUTH_SCHEMA:
        raise AuthorizationError("date_authorization_schema_invalid")
    if value.get("purpose") != "date_selection_evidence":
        raise AuthorizationError("date_purpose_invalid")
    auth_id, attempt_id, not_before, not_after, output = _common(value, attempt_kind="date_selection_evidence")
    proposed = parse_date(value.get("proposed_sampling_date"), field="proposed_sampling_date")
    start = parse_date(value.get("calendar_start"), field="calendar_start")
    end = parse_date(value.get("calendar_end"), field="calendar_end")
    matrix = date_evidence_call_matrix(proposed)
    if start != proposed - timedelta(days=62) or end != proposed + timedelta(days=62):
        raise AuthorizationError("calendar_range_invalid")
    if value.get("calls") != [call.authorization_value() for call in matrix]:
        raise AuthorizationError("call_matrix_invalid")
    _exact_integer(value.get("response_byte_limit"), DATE_RESPONSE_LIMIT, field="response_byte_limit")
    _exact_integer(value.get("attempt_byte_limit"), DATE_ATTEMPT_LIMIT, field="attempt_byte_limit")
    capability_ref = _reference(value.get("capability_manifest_ref"))
    return DateEvidenceAuthorization(
        auth_id,
        attempt_id,
        "date_selection_evidence",
        PROTOCOL_RELATIVE_PATH,
        str(value["protocol_sha256"]),
        SUPERSEDES_SHA256,
        output,
        proposed,
        start,
        end,
        not_before,
        not_after,
        matrix,
        capability_ref,
        digest,
        relative,
        raw,
    )


def load_authorization(path: str | Path, checksum_path: str | Path) -> Authorization:
    """Load exactly one canonical v2 frame or date-evidence authorization."""
    value, raw, digest, relative = _canonical_authorization(Path(path), Path(checksum_path))
    schema = value.get("schema_version")
    if schema == FRAME_AUTH_SCHEMA:
        result: Authorization = _load_frame(value, raw, digest, relative)
    elif schema == DATE_AUTH_SCHEMA:
        result = _load_date(value, raw, digest, relative)
    else:
        raise AuthorizationError("authorization_schema_unknown")
    if isinstance(result, DateEvidenceAuthorization) or result.mode == "capture":
        from a_stock_tracker.qualitative.archive_m4.segmented_verify import _verify_reference_chains

        _verify_reference_chains(result, set())
    return result


def canonical_cell(cell: object) -> dict[str, object]:
    if isinstance(cell, JsonNumber):
        return {"type": "number", "value": cell.lexeme}
    if cell is None:
        return {"type": "null", "value": None}
    if isinstance(cell, bool):
        return {"type": "boolean", "value": cell}
    if isinstance(cell, str):
        return {"type": "string", "value": cell}
    raise VerificationError("row_cell_type_invalid")


def row_sha256(fields: tuple[str, ...], cells: tuple[object, ...]) -> str:
    return sha256_bytes(canonical_json_bytes({"fields": list(fields), "values": [canonical_cell(v) for v in cells]}))


__all__ = [
    "AttemptResult",
    "CallReceipt",
    "DateEvidenceAuthorization",
    "FrameAuthorization",
    "SegmentedRestCall",
    "date_evidence_call_matrix",
    "frame_call_matrix",
    "load_authorization",
]
