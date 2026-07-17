"""Private supervised runtime for M4 v1.3.1 segmented REST attempts."""

from __future__ import annotations

import http.client
import multiprocessing
import os
import signal
import ssl
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterator, Mapping, Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from qualitative_v2_audit import canonical_json_bytes
from qualitative_v2_m4_segmented_core import (
    CREDENTIAL_ENV_VAR,
    MANIFEST_SCHEMA,
    ORIGIN,
    PROJECT_ROOT,
    RECEIPT_SCHEMA,
    STOP_SCHEMA,
    SUPERVISOR_COMMIT_SCHEMA,
    TOKEN_RE,
    Authorization,
    CallReceipt,
    FrameAuthorization,
    JsonNumber,
    SecurityError,
    SegmentedRestCall,
    VerificationError,
    generator_references,
    load_authorization,
    read_regular,
    require_safe_project_path,
    sha256_bytes,
    strict_json_loads,
    time_text,
)
from qualitative_v2_m4_segmented_verify import (
    exclusion_counts,
    parse_provider_response,
    reproduce_date,
    reproduce_frame,
    verify_manifest_candidate,
    verify_segmented_rest_attempt,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


class Transport(Protocol):
    def __call__(self, call: SegmentedRestCall, token: str, byte_limit: int, timeout: float) -> "TransportResponse": ...


@dataclass(frozen=True, slots=True)
class TransportResponse:
    request_url: str
    response_url: str
    status_code: int
    content_type: str
    raw: bytes


@dataclass(frozen=True, slots=True)
class RuntimeHooks:
    transport: Transport
    now: Callable[[], datetime]
    monotonic: Callable[[], float]
    phase: Callable[[str, int], None]


class _Deadline(RuntimeError):
    pass


class _TransportFailure(RuntimeError):
    pass


class _SupervisorSignal(SystemExit):
    pass


_TEST_HOOKS: RuntimeHooks | None = None
_FORCE_TEST_WORKER = False


def _now() -> datetime:
    return datetime.now(SHANGHAI).replace(microsecond=0)


def _default_transport(call: SegmentedRestCall, token: str, byte_limit: int, timeout: float) -> TransportResponse:
    parsed = urlsplit(ORIGIN)
    if parsed.scheme != "https" or parsed.hostname != "api.tushare.pro" or parsed.port is not None or parsed.path:
        raise SecurityError("origin_invalid")
    payload = canonical_json_bytes(
        {
            "api_name": call.api_name,
            "fields": ",".join(call.fields),
            "params": dict(call.params),
            "token": token,
        }
    )[:-1]
    context = ssl.create_default_context()
    connection = http.client.HTTPSConnection("api.tushare.pro", 443, timeout=timeout, context=context)
    try:
        connection.request(
            "POST",
            "/",
            body=payload,
            headers={"Content-Type": "application/json", "Content-Length": str(len(payload)), "Connection": "close"},
        )
        response = connection.getresponse()
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(min(65_536, byte_limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > byte_limit:
                raise SecurityError("response_too_large")
        return TransportResponse(
            ORIGIN,
            ORIGIN,
            int(response.status),
            str(response.getheader("Content-Type") or "application/octet-stream"),
            b"".join(chunks),
        )
    except SecurityError:
        raise
    except (OSError, TimeoutError, ssl.SSLError, http.client.HTTPException):
        raise _TransportFailure("transport_error") from None
    finally:
        connection.close()


def _hooks() -> RuntimeHooks:
    return _TEST_HOOKS or RuntimeHooks(_default_transport, _now, time.monotonic, lambda _phase, _ordinal: None)


@contextmanager
def _runtime_hooks(hooks: RuntimeHooks, *, force_worker: bool = False) -> Iterator[None]:
    """Install private synthetic seams; production callers never receive hooks."""
    global _FORCE_TEST_WORKER, _TEST_HOOKS
    previous = _TEST_HOOKS
    previous_force = _FORCE_TEST_WORKER
    _TEST_HOOKS = hooks
    _FORCE_TEST_WORKER = force_worker
    try:
        yield
    finally:
        _TEST_HOOKS = previous
        _FORCE_TEST_WORKER = previous_force


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publication_root(attempt_dir: Path) -> Path:
    return attempt_dir.parent / f".{attempt_dir.name}.publication-tmp"


@contextmanager
def _defer_publication_signals() -> Iterator[None]:
    blocked = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def _create_only(
    path: Path,
    raw: bytes,
    *,
    temporary_root: Path,
    sealed_callback: Callable[[], None] | None = None,
) -> None:
    if not path.parent.exists():
        path.parent.mkdir(mode=0o700)
        _fsync_directory(path.parent.parent)
    if path.parent.is_symlink():
        raise SecurityError("artifact_parent_unsafe")
    if temporary_root.is_symlink() or not temporary_root.is_dir():
        raise SecurityError("publication_temp_root_unsafe")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=temporary_root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o444)
        with _defer_publication_signals():
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise SecurityError("artifact_already_exists") from exc
            _fsync_directory(path.parent)
            if sealed_callback is not None:
                sealed_callback()
    finally:
        temporary.unlink(missing_ok=True)


def _replace_journal(path: Path, value: Mapping[str, object]) -> None:
    raw = canonical_json_bytes(value)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _journal(path: Path, authorization: Authorization, phase: str, ordinal: int, **extra: object) -> None:
    value: dict[str, object] = {
        "attempt_id": authorization.attempt_id,
        "authorization_id": authorization.authorization_id,
        "ordinal": ordinal,
        "phase": phase,
    }
    value.update(extra)
    _replace_journal(path, value)
    _hooks().phase(phase, ordinal)


def _clock() -> datetime:
    value = _hooks().now()
    if value.tzinfo is None or value.utcoffset() != timedelta(hours=8) or value.microsecond:
        raise SecurityError("clock_invalid")
    return value


def _remaining(deadline: datetime) -> float:
    return (deadline - _clock()).total_seconds()


def _deadline_check(deadline: datetime) -> None:
    if _clock() > deadline:
        raise _Deadline("authorization_window_overrun")


def _load_token() -> str:
    try:
        token = os.environ[CREDENTIAL_ENV_VAR]
    except (KeyError, OSError):
        raise SecurityError("credential_unavailable") from None
    if TOKEN_RE.fullmatch(token) is None:
        raise SecurityError("credential_invalid")
    return token


def _reload(
    expected: Authorization,
    path: Path,
    checksum_path: Path,
    *,
    attempt_dir: Path,
    lock_path: Path,
    lock_identity: tuple[int, int],
    startup_generators: list[dict[str, object]],
    receipts: list[CallReceipt],
) -> None:
    try:
        current = load_authorization(path, checksum_path)
    except Exception as exc:
        code = "protocol_drift" if getattr(exc, "code", "") == "protocol_drift" else "authorization_drift"
        raise SecurityError(code) from None
    if current.sha256 != expected.sha256 or current.canonical_bytes != expected.canonical_bytes:
        raise SecurityError("authorization_drift")
    try:
        lock = lock_path.stat(follow_symlinks=False)
    except OSError:
        raise SecurityError("lock_lost") from None
    if (
        lock_path.is_symlink()
        or not stat.S_ISREG(lock.st_mode)
        or lock.st_nlink != 1
        or (lock.st_dev, lock.st_ino) != lock_identity
    ):
        raise SecurityError("lock_lost")
    if generator_references() != startup_generators:
        raise SecurityError("protocol_drift")
    expected_files = {
        item
        for receipt in receipts
        for item in (receipt.receipt_relative_path, receipt.blob_relative_path)
        if item is not None
    }
    actual_files = {
        item.relative_to(attempt_dir).as_posix()
        for item in attempt_dir.rglob("*")
        if item.is_file() or item.is_symlink()
    }
    if actual_files != expected_files:
        raise SecurityError("output_drift")


def _publish_blob(
    attempt_dir: Path,
    call: SegmentedRestCall,
    raw: bytes,
    *,
    sealed_callback: Callable[[], None] | None = None,
) -> tuple[str, str]:
    digest = sha256_bytes(raw)
    relative = f"blobs/{call.ordinal:04d}-{digest}.bin"
    _create_only(
        attempt_dir / relative,
        raw,
        temporary_root=_publication_root(attempt_dir),
        sealed_callback=sealed_callback,
    )
    return relative, digest


def _provider_code(raw: bytes) -> int | None:
    try:
        value = strict_json_loads(raw)
    except VerificationError:
        return None
    if not isinstance(value, dict):
        return None
    code = value.get("code")
    if isinstance(code, JsonNumber) and re_full_int(code.lexeme):
        return int(code.lexeme)
    return None


def re_full_int(value: str) -> bool:
    unsigned = value[1:] if value.startswith("-") else value
    return len(unsigned) <= 20 and bool(
        unsigned == "0" or (unsigned.isascii() and unsigned.isdigit() and not unsigned.startswith("0"))
    )


def _stored_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SecurityError(f"{field}_invalid")
    return value


def _receipt_value(
    authorization: Authorization,
    call: SegmentedRestCall,
    *,
    captured_at: datetime,
    state: str,
    status_category: str,
    error_code: str | None,
    response: TransportResponse | None,
    blob_relative: str | None,
    blob_sha256: str | None,
) -> dict[str, object]:
    blob = state in {"success_blob", "terminal_failure_blob"}
    return {
        "attempt_id": authorization.attempt_id,
        "authorization_id": authorization.authorization_id,
        "blob_relative_path": blob_relative if blob else None,
        "blob_sha256": blob_sha256 if blob else None,
        "byte_count": len(response.raw) if blob and response is not None else None,
        "call_id": call.call_id,
        "captured_at": time_text(captured_at),
        "content_type": response.content_type if blob and response is not None else None,
        "error_code": error_code,
        "http_status": response.status_code if response is not None else None,
        "ordinal": call.ordinal,
        "provider_code": _provider_code(response.raw) if response is not None else None,
        "request_description": call.request_description(),
        "schema_version": RECEIPT_SCHEMA,
        "state": state,
        "status_category": status_category,
    }


def _publish_receipt(
    attempt_dir: Path,
    value: Mapping[str, object],
    *,
    sealed_callback: Callable[[], None] | None = None,
) -> CallReceipt:
    ordinal = _stored_int(value["ordinal"], field="ordinal")
    relative = f"receipts/{ordinal:04d}.json"
    raw = canonical_json_bytes(value)
    sealed_times: list[datetime] = []

    def record_seal() -> None:
        sealed_at = _clock()
        timestamp_ns = int(sealed_at.timestamp()) * 1_000_000_000
        os.utime(attempt_dir / relative, ns=(timestamp_ns, timestamp_ns), follow_symlinks=False)
        descriptor = os.open(attempt_dir / relative, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory((attempt_dir / relative).parent)
        sealed_times.append(sealed_at)
        if sealed_callback is not None:
            sealed_callback()

    _create_only(
        attempt_dir / relative,
        raw,
        temporary_root=_publication_root(attempt_dir),
        sealed_callback=record_seal,
    )
    captured_at = datetime.fromisoformat(str(value["captured_at"]))
    return CallReceipt(
        ordinal,
        str(value["call_id"]),
        str(value["state"]),
        captured_at,
        str(value["status_category"]),
        str(value["error_code"]) if value["error_code"] is not None else None,
        _stored_int(value["byte_count"], field="byte_count") if value["byte_count"] is not None else None,
        str(value["blob_relative_path"]) if value["blob_relative_path"] is not None else None,
        str(value["blob_sha256"]) if value["blob_sha256"] is not None else None,
        relative,
        sha256_bytes(raw),
        sealed_times[0],
    )


def _publish_stop(
    attempt_dir: Path, authorization: Authorization, *, next_ordinal: int | None, code: str, stopped_at: datetime
) -> dict[str, object]:
    value = {
        "attempt_id": authorization.attempt_id,
        "authorization_id": authorization.authorization_id,
        "next_ordinal": next_ordinal,
        "schema_version": STOP_SCHEMA,
        "stop_code": code,
        "stopped_at": time_text(stopped_at),
    }
    raw = canonical_json_bytes(value)
    _create_only(attempt_dir / "stop-record.json", raw, temporary_root=_publication_root(attempt_dir))
    return {
        "byte_count": len(raw),
        "kind": "stop_record",
        "relative_path": "stop-record.json",
        "sha256": sha256_bytes(raw),
    }


def _artifact_ref(attempt_dir: Path, relative: str, kind: str) -> dict[str, object]:
    raw = read_regular(attempt_dir / relative, maximum=256 * 1024 * 1024, require_read_only=True)
    return {"byte_count": len(raw), "kind": kind, "relative_path": relative, "sha256": sha256_bytes(raw)}


def _closed_files(attempt_dir: Path) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for path in sorted(item for item in attempt_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(attempt_dir).as_posix()
        if relative == "attempt-manifest.json":
            continue
        if relative.startswith("blobs/"):
            kind = "blob"
        elif relative.startswith("receipts/"):
            kind = "receipt"
        elif relative == "stop-record.json":
            kind = "stop_record"
        elif relative == "exclusion-ledger.json":
            kind = "exclusion_ledger"
        elif relative == "date-selection-evidence.json":
            kind = "date_evidence"
        elif relative == "supervisor-commit.json":
            kind = "supervisor_commit"
        else:
            raise SecurityError("unknown_artifact")
        values.append(_artifact_ref(attempt_dir, relative, kind))
    return sorted(values, key=lambda item: str(item["relative_path"]))


def _receipt_refs(receipts: list[CallReceipt]) -> list[dict[str, object]]:
    return [
        {
            "ordinal": receipt.ordinal,
            "relative_path": receipt.receipt_relative_path,
            "sealed_at": time_text(receipt.sealed_at or receipt.captured_at),
            "sha256": receipt.receipt_sha256,
            "state": receipt.state,
        }
        for receipt in receipts
    ]


def _blob_refs(receipts: list[CallReceipt]) -> list[dict[str, object]]:
    return [
        {
            "byte_count": receipt.byte_count,
            "ordinal": receipt.ordinal,
            "relative_path": receipt.blob_relative_path,
            "sealed_at": time_text(receipt.sealed_at or receipt.captured_at),
            "sha256": receipt.blob_sha256,
        }
        for receipt in receipts
        if receipt.blob_relative_path is not None
    ]


def _seal_tree(path: Path) -> None:
    for child in path.rglob("*"):
        if child.is_file():
            child.chmod(0o444)
    for child in sorted((item for item in path.rglob("*") if item.is_dir()), reverse=True):
        child.chmod(0o555)
    path.chmod(0o555)


def _manifest(
    attempt_dir: Path,
    authorization: Authorization,
    receipts: list[CallReceipt],
    *,
    stop_ref: Mapping[str, object] | None,
    sealed_at: datetime,
) -> bytes:
    successful = [item.ordinal for item in receipts if item.state == "success_blob"]
    terminal = [item.ordinal for item in receipts if item.state != "success_blob"]
    expected = list(range(1, len(authorization.calls) + 1))
    attempted = set(successful + terminal)
    unattempted = [ordinal for ordinal in expected if ordinal not in attempted]
    complete = successful == expected and not terminal
    window = sealed_at <= authorization.not_after and all(
        item.captured_at <= authorization.not_after for item in receipts
    )
    if stop_ref is not None:
        stop_value = strict_json_loads(
            read_regular(attempt_dir / "stop-record.json", maximum=1_000_000, require_read_only=True), numbers=False
        )
        if not isinstance(stop_value, dict):
            raise SecurityError("stop_record_invalid")
        stopped_at = datetime.fromisoformat(str(stop_value.get("stopped_at")))
        if stop_value.get("stop_code") in {"authorization_not_yet_valid", "authorization_expired"}:
            window = False
        if stopped_at > authorization.not_after:
            window = False
    parsed = {}
    for receipt in receipts:
        if receipt.state == "success_blob" and receipt.blob_relative_path is not None:
            raw = read_regular(
                attempt_dir / receipt.blob_relative_path,
                maximum=authorization.response_byte_limit,
                require_read_only=True,
            )
            parsed[receipt.ordinal] = parse_provider_response(raw, authorization.calls[receipt.ordinal - 1])
    if isinstance(authorization, FrameAuthorization):
        reproduction = reproduce_frame(authorization, parsed, complete=complete, authorization_window=window)
        assert reproduction.ledger is not None
        ledger_raw = canonical_json_bytes(reproduction.ledger)
        if not (attempt_dir / "exclusion-ledger.json").exists():
            _create_only(
                attempt_dir / "exclusion-ledger.json",
                ledger_raw,
                temporary_root=_publication_root(attempt_dir),
            )
        elif (
            read_regular(attempt_dir / "exclusion-ledger.json", maximum=32 * 1024 * 1024, require_read_only=True)
            != ledger_raw
        ):
            raise SecurityError("ledger_drift")
        ledger_ref: Mapping[str, object] | None = _artifact_ref(
            attempt_dir, "exclusion-ledger.json", "exclusion_ledger"
        )
        counts: Mapping[str, object] | None = exclusion_counts(reproduction.ledger)
        evidence_ref: Mapping[str, object] | None = None
        kind = authorization.mode
    else:
        reproduction = reproduce_date(
            authorization, parsed, receipts, complete=complete, authorization_window=window, sealed_at=sealed_at
        )
        ledger_ref = None
        counts = None
        kind = authorization.purpose
        if reproduction.evidence is not None:
            evidence_raw = canonical_json_bytes(reproduction.evidence)
            _create_only(
                attempt_dir / "date-selection-evidence.json",
                evidence_raw,
                temporary_root=_publication_root(attempt_dir),
            )
            evidence_ref = _artifact_ref(attempt_dir, "date-selection-evidence.json", "date_evidence")
        else:
            evidence_ref = None
    overall_pass = reproduction.gates["overall_pass"] is True
    disposition = (
        ("ELIGIBLE_FOR_FULL_CAPTURE_AUTHORIZATION" if overall_pass else "NO_QUALIFIED_FRAME_SOURCE")
        if kind == "capability"
        else ("FRAME_CAPTURE_ELIGIBLE" if overall_pass else "CAPTURE_FAILED_CLOSED")
        if kind == "capture"
        else ("DATE_EVIDENCE_VALID" if overall_pass else "DATE_EVIDENCE_FAILED")
    )
    eligible = kind == "capture" and overall_pass
    commit_nonce = os.urandom(32).hex()
    commit_value = {
        "attempt_id": authorization.attempt_id,
        "authorization_id": authorization.authorization_id,
        "execution_deadline": time_text(authorization.not_after),
        "nonce": commit_nonce,
        "outcome": "pass" if overall_pass else "fail",
        "schema_version": SUPERVISOR_COMMIT_SCHEMA,
    }
    commit_raw = canonical_json_bytes(commit_value)
    commit_artifact = {
        "byte_count": len(commit_raw),
        "kind": "supervisor_commit",
        "relative_path": "supervisor-commit.json",
        "sha256": sha256_bytes(commit_raw),
    }
    closed_files = _closed_files(attempt_dir)
    closed_files.append(commit_artifact)
    closed_files.sort(key=lambda item: str(item["relative_path"]))
    value = {
        "artifact_files": closed_files,
        "attempt_id": authorization.attempt_id,
        "attempt_kind": kind,
        "authorization_id": authorization.authorization_id,
        "authorization_relative_path": authorization.relative_path,
        "authorization_sha256": authorization.sha256,
        "blob_refs": _blob_refs(receipts),
        "capture_input_eligible": eligible,
        "complete": complete,
        "date_evidence_ref": evidence_ref,
        "date_selection_only": kind == "date_selection_evidence",
        "disposition": disposition,
        "exclusion_counts": counts,
        "exclusion_ledger_ref": ledger_ref,
        "execution_deadline": time_text(authorization.not_after),
        "expected_ordinals": expected,
        "failed_ordinal": terminal[0] if terminal else None,
        "gate_results": reproduction.gates,
        "generator_files": generator_references(),
        "non_adoptable": not eligible,
        "protocol_path": authorization.protocol_path,
        "protocol_sha256": authorization.protocol_sha256,
        "receipt_refs": _receipt_refs(receipts),
        "response_byte_total": sum(item.byte_count or 0 for item in receipts if item.blob_relative_path is not None),
        "schema_version": MANIFEST_SCHEMA,
        "sealed_at": time_text(sealed_at),
        "stop_record_ref": stop_ref,
        "supervisor_commit_ref": {**commit_artifact, "nonce": commit_nonce},
        "successful_ordinals": successful,
        "supersedes_sha256": authorization.supersedes_sha256,
        "terminal_failed_ordinals": terminal,
        "unattempted_ordinals": unattempted,
    }
    return canonical_json_bytes(value)


def _finish(
    attempt_dir: Path,
    authorization: Authorization,
    receipts: list[CallReceipt],
    *,
    stop_ref: Mapping[str, object] | None,
    journal_path: Path,
) -> None:
    sealed_at = _clock()
    watchdog = (
        sealed_at <= authorization.not_after
        and len(receipts) == len(authorization.calls)
        and all(receipt.state == "success_blob" for receipt in receipts)
    )
    raw = _manifest(attempt_dir, authorization, receipts, stop_ref=stop_ref, sealed_at=sealed_at)
    if watchdog:
        _deadline_check(authorization.not_after)
    _journal(journal_path, authorization, "gates_complete", len(receipts) + 1)
    _hooks().phase("gates", len(receipts) + 1)
    _journal(journal_path, authorization, "manifest_candidate", len(receipts) + 1)
    verify_manifest_candidate(attempt_dir, raw)
    _hooks().phase("candidate_verification", len(receipts) + 1)
    if watchdog:
        _deadline_check(authorization.not_after)
    _create_only(attempt_dir / "attempt-manifest.json", raw, temporary_root=_publication_root(attempt_dir))
    _hooks().phase("manifest_publication", len(receipts) + 1)
    if watchdog:
        _deadline_check(authorization.not_after)


def _failure_receipt(
    attempt_dir: Path,
    authorization: Authorization,
    call: SegmentedRestCall,
    *,
    response: TransportResponse | None,
    blob_relative: str | None,
    blob_sha: str | None,
    error_code: str,
    status: str,
    journal_path: Path | None = None,
) -> CallReceipt:
    state = "terminal_failure_blob" if blob_relative is not None else "terminal_failure_no_blob"
    return _publish_receipt(
        attempt_dir,
        _receipt_value(
            authorization,
            call,
            captured_at=_clock(),
            state=state,
            status_category=status,
            error_code=error_code,
            response=response,
            blob_relative=blob_relative,
            blob_sha256=blob_sha,
        ),
        sealed_callback=(
            (lambda: _journal(journal_path, authorization, "receipt_sealed", call.ordinal))
            if journal_path is not None
            else None
        ),
    )


def _execute_worker(
    authorization_path: Path,
    checksum_path: Path,
    journal_path: Path,
    inherited_signal_mask: set[int | signal.Signals] | None = None,
) -> None:
    if multiprocessing.parent_process() is not None:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGHUP, signal.SIG_DFL)
        if inherited_signal_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, inherited_signal_mask)
    authorization = load_authorization(authorization_path, checksum_path)
    attempt_dir = PROJECT_ROOT / authorization.attempt_output_dir
    lock_path = attempt_dir.parent / f".{authorization.attempt_id}.lock"
    try:
        lock = lock_path.stat(follow_symlinks=False)
    except OSError:
        raise SecurityError("lock_lost") from None
    lock_identity = (lock.st_dev, lock.st_ino)
    startup_generators = generator_references()
    receipts: list[CallReceipt] = []
    stop_ref: Mapping[str, object] | None = None
    try:
        now = _clock()
        if now < authorization.not_before:
            stop_ref = _publish_stop(
                attempt_dir, authorization, next_ordinal=1, code="authorization_not_yet_valid", stopped_at=now
            )
            _finish(attempt_dir, authorization, receipts, stop_ref=stop_ref, journal_path=journal_path)
            return
        if now > authorization.not_after:
            stop_ref = _publish_stop(
                attempt_dir, authorization, next_ordinal=1, code="authorization_expired", stopped_at=now
            )
            _finish(attempt_dir, authorization, receipts, stop_ref=stop_ref, journal_path=journal_path)
            return
        try:
            token = _load_token()
        except SecurityError as exc:
            stop_ref = _publish_stop(attempt_dir, authorization, next_ordinal=1, code=exc.code, stopped_at=_clock())
            _finish(attempt_dir, authorization, receipts, stop_ref=stop_ref, journal_path=journal_path)
            return
        total = 0
        for call in authorization.calls:
            _journal(journal_path, authorization, "before_transport", call.ordinal)
            try:
                _reload(
                    authorization,
                    authorization_path,
                    checksum_path,
                    attempt_dir=attempt_dir,
                    lock_path=lock_path,
                    lock_identity=lock_identity,
                    startup_generators=startup_generators,
                    receipts=receipts,
                )
                # Transport must begin strictly before the authorization boundary.
                if _clock() >= authorization.not_after:
                    raise _Deadline("authorization_window_overrun")
            except _Deadline:
                stop_ref = _publish_stop(
                    attempt_dir,
                    authorization,
                    next_ordinal=call.ordinal,
                    code="authorization_expired",
                    stopped_at=_clock(),
                )
                break
            except SecurityError as exc:
                stop_code = (
                    exc.code
                    if exc.code in {"authorization_drift", "protocol_drift", "lock_lost", "output_drift"}
                    else "authorization_invalid"
                )
                stop_ref = _publish_stop(
                    attempt_dir,
                    authorization,
                    next_ordinal=call.ordinal,
                    code=stop_code,
                    stopped_at=_clock(),
                )
                break
            response: TransportResponse | None = None
            blob_relative: str | None = None
            blob_sha: str | None = None
            try:
                _journal(journal_path, authorization, "transport_started", call.ordinal)
                remaining = _remaining(authorization.not_after)
                if remaining <= 0:
                    raise _Deadline
                response = _hooks().transport(call, token, authorization.response_byte_limit, remaining)
                if (
                    response.request_url != ORIGIN
                    or response.response_url != ORIGIN
                    or not 100 <= response.status_code <= 599
                    or 300 <= response.status_code < 400
                ):
                    raise SecurityError("transport_boundary_invalid")
                if len(response.raw) > authorization.response_byte_limit:
                    raise SecurityError("response_too_large")
                if token.encode("ascii") in response.raw or token in response.content_type:
                    raise SecurityError("token_echo")
                _hooks().phase("token_scan", call.ordinal)
                total += len(response.raw)
                if total > authorization.attempt_byte_limit:
                    raise SecurityError("attempt_total_too_large")
                _deadline_check(authorization.not_after)
                expected_blob_sha = sha256_bytes(response.raw)
                expected_blob_relative = f"blobs/{call.ordinal:04d}-{expected_blob_sha}.bin"
                blob_relative, blob_sha = _publish_blob(
                    attempt_dir,
                    call,
                    response.raw,
                    sealed_callback=lambda: _journal(
                        journal_path,
                        authorization,
                        "blob_sealed",
                        call.ordinal,
                        blob_relative_path=expected_blob_relative,
                        blob_sha256=expected_blob_sha,
                        byte_count=len(response.raw),
                        content_type=response.content_type,
                        http_status=response.status_code,
                    ),
                )
                _hooks().phase("blob_fsync", call.ordinal)
                _deadline_check(authorization.not_after)
                if response.status_code != 200:
                    receipt = _failure_receipt(
                        attempt_dir,
                        authorization,
                        call,
                        response=response,
                        blob_relative=blob_relative,
                        blob_sha=blob_sha,
                        error_code="http_error",
                        status="http",
                        journal_path=journal_path,
                    )
                    receipts.append(receipt)
                    break
                try:
                    _hooks().phase("parse", call.ordinal)
                    parse_provider_response(response.raw, call)
                except VerificationError as exc:
                    if exc.code == "provider_error":
                        error, status = "provider_error", "provider"
                    elif exc.code == "json_invalid":
                        error, status = "malformed_json", "parse"
                    else:
                        error, status = "schema_error", "schema"
                    receipt = _failure_receipt(
                        attempt_dir,
                        authorization,
                        call,
                        response=response,
                        blob_relative=blob_relative,
                        blob_sha=blob_sha,
                        error_code=error,
                        status=status,
                        journal_path=journal_path,
                    )
                    receipts.append(receipt)
                    break
                receipt = _publish_receipt(
                    attempt_dir,
                    _receipt_value(
                        authorization,
                        call,
                        captured_at=_clock(),
                        state="success_blob",
                        status_category="success",
                        error_code=None,
                        response=response,
                        blob_relative=blob_relative,
                        blob_sha256=blob_sha,
                    ),
                    sealed_callback=lambda: _journal(journal_path, authorization, "receipt_sealed", call.ordinal),
                )
                receipts.append(receipt)
                _hooks().phase("receipt_fsync", call.ordinal)
                _deadline_check(authorization.not_after)
            except _Deadline:
                if receipts and receipts[-1].ordinal == call.ordinal and receipts[-1].state == "success_blob":
                    stop_ref = _publish_stop(
                        attempt_dir,
                        authorization,
                        next_ordinal=call.ordinal + 1 if call.ordinal < len(authorization.calls) else None,
                        code="authorization_expired",
                        stopped_at=_clock(),
                    )
                else:
                    receipts.append(
                        _failure_receipt(
                            attempt_dir,
                            authorization,
                            call,
                            response=response,
                            blob_relative=blob_relative,
                            blob_sha=blob_sha,
                            error_code="authorization_window_overrun_after_blob"
                            if blob_relative
                            else "authorization_window_overrun_before_blob",
                            status="authorization",
                            journal_path=journal_path,
                        )
                    )
                break
            except _TransportFailure:
                receipts.append(
                    _failure_receipt(
                        attempt_dir,
                        authorization,
                        call,
                        response=None,
                        blob_relative=None,
                        blob_sha=None,
                        error_code="transport_error",
                        status="transport",
                        journal_path=journal_path,
                    )
                )
                break
            except SecurityError as exc:
                code = exc.code
                if code not in {"response_too_large", "attempt_total_too_large", "token_echo"}:
                    code = "transport_error"
                status = "size" if "too_large" in code else "security" if code == "token_echo" else "transport"
                receipts.append(
                    _failure_receipt(
                        attempt_dir,
                        authorization,
                        call,
                        response=None,
                        blob_relative=None,
                        blob_sha=None,
                        error_code=code,
                        status=status,
                        journal_path=journal_path,
                    )
                )
                break
        _finish(attempt_dir, authorization, receipts, stop_ref=stop_ref, journal_path=journal_path)
    except BaseException:
        if not (attempt_dir / "attempt-manifest.json").exists():
            try:
                next_ordinal = len(receipts) + 1 if len(receipts) < len(authorization.calls) else None
                if not receipts or receipts[-1].state == "success_blob":
                    stop_ref = _publish_stop(
                        attempt_dir,
                        authorization,
                        next_ordinal=next_ordinal,
                        code="process_control_interrupt",
                        stopped_at=_clock(),
                    )
                _finish(attempt_dir, authorization, receipts, stop_ref=stop_ref, journal_path=journal_path)
            except BaseException:
                pass
        raise


def _read_receipts(attempt_dir: Path) -> list[CallReceipt]:
    results: list[CallReceipt] = []
    receipts_dir = attempt_dir / "receipts"
    if not receipts_dir.exists():
        return results
    for path in sorted(receipts_dir.glob("*.json")):
        raw = read_regular(path, maximum=2 * 1024 * 1024, require_read_only=True)
        value = strict_json_loads(raw, numbers=False)
        if not isinstance(value, dict):
            raise SecurityError("receipt_schema_invalid")
        results.append(
            CallReceipt(
                int(value["ordinal"]),
                str(value["call_id"]),
                str(value["state"]),
                datetime.fromisoformat(value["captured_at"]),
                str(value["status_category"]),
                value["error_code"],
                value["byte_count"],
                value["blob_relative_path"],
                value["blob_sha256"],
                path.relative_to(attempt_dir).as_posix(),
                sha256_bytes(raw),
                datetime.fromtimestamp(path.stat(follow_symlinks=False).st_mtime, SHANGHAI).replace(microsecond=0),
            )
        )
    return results


def _recover_after_termination(authorization: Authorization, journal_path: Path, *, deadline: bool) -> None:
    attempt_dir = PROJECT_ROOT / authorization.attempt_output_dir
    receipts = _read_receipts(attempt_dir)
    stop_ref: Mapping[str, object] | None = None
    journal: dict[str, object] = {}
    if journal_path.exists():
        value = strict_json_loads(read_regular(journal_path, maximum=1_000_000), numbers=False)
        if isinstance(value, dict):
            journal = value
    ordinal = _stored_int(journal.get("ordinal", len(receipts) + 1), field="journal_ordinal")
    phase = journal.get("phase")
    stop_path = attempt_dir / "stop-record.json"
    if stop_path.exists() or stop_path.is_symlink():
        # The stop record is itself a create-only, fsync-sealed decision.  It
        # takes precedence over an older journal phase if the worker was
        # terminated between stop publication and the next journal update.
        stop_ref = _artifact_ref(attempt_dir, "stop-record.json", "stop_record")
        _finish(attempt_dir, authorization, receipts, stop_ref=stop_ref, journal_path=journal_path)
        return
    if phase in {"gates_complete", "manifest_candidate"}:
        if receipts and receipts[-1].state == "success_blob" and len(receipts) < len(authorization.calls):
            stop_ref = _publish_stop(
                attempt_dir,
                authorization,
                next_ordinal=len(receipts) + 1,
                code="authorization_expired" if deadline else "process_control_interrupt",
                stopped_at=_clock(),
            )
        _finish(attempt_dir, authorization, receipts, stop_ref=stop_ref, journal_path=journal_path)
        return
    if (not journal or phase == "before_transport") and ordinal <= len(authorization.calls):
        stop_ref = _publish_stop(
            attempt_dir,
            authorization,
            next_ordinal=ordinal,
            code="authorization_expired" if deadline else "process_control_interrupt",
            stopped_at=_clock(),
        )
    elif ordinal <= len(authorization.calls) and not any(item.ordinal == ordinal for item in receipts):
        call = authorization.calls[ordinal - 1]
        blob_relative = journal.get("blob_relative_path")
        blob_sha = journal.get("blob_sha256")
        if not isinstance(blob_relative, str) or not isinstance(blob_sha, str):
            sealed_blobs = list((attempt_dir / "blobs").glob(f"{ordinal:04d}-*.bin"))
            if len(sealed_blobs) == 1:
                blob_relative = sealed_blobs[0].relative_to(attempt_dir).as_posix()
                blob_sha = sealed_blobs[0].stem.split("-", 1)[1]
        response = None
        if isinstance(blob_relative, str) and isinstance(blob_sha, str) and (attempt_dir / blob_relative).exists():
            raw = read_regular(
                attempt_dir / blob_relative, maximum=authorization.response_byte_limit, require_read_only=True
            )
            response = TransportResponse(
                ORIGIN,
                ORIGIN,
                _stored_int(journal.get("http_status", 200), field="journal_http_status"),
                str(journal.get("content_type", "application/octet-stream")),
                raw,
            )
        receipts.append(
            _failure_receipt(
                attempt_dir,
                authorization,
                call,
                response=response,
                blob_relative=str(blob_relative) if response else None,
                blob_sha=str(blob_sha) if response else None,
                error_code=(
                    "authorization_window_overrun_after_blob"
                    if response
                    else "authorization_window_overrun_before_blob"
                )
                if deadline
                else ("process_control_interrupt_after_blob" if response else "process_control_interrupt_before_blob"),
                status="authorization" if deadline else "process_control",
                journal_path=journal_path,
            )
        )
    elif not receipts or receipts[-1].state == "success_blob":
        next_ordinal = len(receipts) + 1 if len(receipts) < len(authorization.calls) else None
        stop_ref = _publish_stop(
            attempt_dir,
            authorization,
            next_ordinal=next_ordinal,
            code="authorization_expired" if deadline else "process_control_interrupt",
            stopped_at=_clock(),
        )
    _finish(attempt_dir, authorization, receipts, stop_ref=stop_ref, journal_path=journal_path)


def _discard_terminated_worker_temps(publication_root: Path) -> None:
    for path in publication_root.iterdir():
        if path.is_symlink() or not path.is_file():
            raise SecurityError("publication_temp_unsafe")
        path.unlink()
    _fsync_directory(publication_root)


def _finalize_supervised(
    authorization: Authorization,
    attempt_dir: Path,
    *,
    parent: Path,
    lock_path: Path,
    journal_path: Path,
    publication_root: Path,
    normal_deadline_reached: bool,
    supervisor_monotonic_deadline: float | None,
):
    manifest_path = attempt_dir / "attempt-manifest.json"
    manifest_raw = read_regular(manifest_path, maximum=8 * 1024 * 1024, require_read_only=True)
    manifest_value = strict_json_loads(manifest_raw, numbers=False)
    if not isinstance(manifest_value, dict):
        raise SecurityError("manifest_invalid")
    manifest_gates = manifest_value.get("gate_results")
    overall_pass = isinstance(manifest_gates, dict) and manifest_gates.get("overall_pass") is True
    commit_ref = manifest_value.get("supervisor_commit_ref")
    if not isinstance(commit_ref, dict) or not isinstance(commit_ref.get("nonce"), str):
        raise SecurityError("supervisor_commit_ref_invalid")
    commit_value = {
        "attempt_id": authorization.attempt_id,
        "authorization_id": authorization.authorization_id,
        "execution_deadline": time_text(authorization.not_after),
        "nonce": commit_ref["nonce"],
        "outcome": "pass" if overall_pass else "fail",
        "schema_version": SUPERVISOR_COMMIT_SCHEMA,
    }
    commit_raw = canonical_json_bytes(commit_value)
    if (
        commit_ref.get("relative_path") != "supervisor-commit.json"
        or commit_ref.get("kind") != "supervisor_commit"
        or commit_ref.get("byte_count") != len(commit_raw)
        or commit_ref.get("sha256") != sha256_bytes(commit_raw)
    ):
        raise SecurityError("supervisor_commit_ref_invalid")
    commit_path = attempt_dir / "supervisor-commit.json"
    if commit_path.exists():
        if read_regular(commit_path, maximum=4096, require_read_only=True) != commit_raw:
            raise SecurityError("supervisor_commit_drift")
    else:
        if normal_deadline_reached and overall_pass:
            raise SecurityError("pass_manifest_missed_deadline")
        # Re-run the exact candidate verifier after manifest fsync. Only the
        # parent can then publish the manifest-bound positive commit record.
        verify_manifest_candidate(attempt_dir, manifest_raw)
        post_verify_deadline_reached = (
            supervisor_monotonic_deadline is not None and time.monotonic() > supervisor_monotonic_deadline
        ) or _clock() > authorization.not_after
        if post_verify_deadline_reached and overall_pass:
            raise SecurityError("pass_manifest_missed_deadline")
        _create_only(commit_path, commit_raw, temporary_root=publication_root)
    _seal_tree(attempt_dir)
    if publication_root.exists():
        if any(publication_root.iterdir()):
            raise SecurityError("publication_temp_not_empty")
        publication_root.rmdir()
    journal_path.unlink(missing_ok=True)
    lock_path.unlink(missing_ok=True)
    _fsync_directory(parent)
    return verify_segmented_rest_attempt(attempt_dir)


def execute_supervised(authorization_path: Path, checksum_path: Path):
    """Run one worker under a monotonic parent deadline and return offline verification."""
    authorization = load_authorization(authorization_path, checksum_path)
    attempt_dir = PROJECT_ROOT / authorization.attempt_output_dir
    parent = attempt_dir.parent
    current = PROJECT_ROOT.resolve()
    for part in Path(authorization.attempt_output_dir).parent.parts:
        current /= part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if current.is_symlink() or not current.is_dir():
            raise SecurityError("attempt_parent_unsafe")
    require_safe_project_path(parent)
    if parent.is_symlink() or attempt_dir.exists() or attempt_dir.is_symlink():
        raise SecurityError("attempt_already_exists")
    lock_path = parent / f".{authorization.attempt_id}.lock"
    journal_path = parent / f".{authorization.attempt_id}.phase-journal.json"
    publication_root = _publication_root(attempt_dir)
    user_signals = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
    setup_signal_mask = signal.pthread_sigmask(signal.SIG_BLOCK, user_signals)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        try:
            lock_descriptor = os.open(lock_path, flags, 0o600)
        except FileExistsError as exc:
            raise SecurityError("stale_or_concurrent_lock") from exc
        os.close(lock_descriptor)
        try:
            publication_root.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise SecurityError("stale_publication_temp") from exc
        _fsync_directory(parent)
        attempt_dir.mkdir(mode=0o700)
        _fsync_directory(parent)
    except BaseException:
        signal.pthread_sigmask(signal.SIG_SETMASK, setup_signal_mask)
        raise
    interrupted: BaseException | None = None
    normal_deadline_reached = False
    supervisor_monotonic_deadline: float | None = None
    post_worker_signal_mask: set[int | signal.Signals] | None = None
    previous_handlers = {selected: signal.getsignal(selected) for selected in (signal.SIGTERM, signal.SIGHUP)}

    def supervisor_signal(signum: int, _frame: object) -> None:
        raise _SupervisorSignal(128 + signum)

    for selected in previous_handlers:
        signal.signal(selected, supervisor_signal)
    try:
        if _TEST_HOOKS is not None and not _FORCE_TEST_WORKER:
            try:
                signal.pthread_sigmask(signal.SIG_SETMASK, setup_signal_mask)
                _execute_worker(authorization_path, checksum_path, journal_path)
            except (KeyboardInterrupt, SystemExit, GeneratorExit) as exc:
                interrupted = exc
            post_worker_signal_mask = signal.pthread_sigmask(signal.SIG_BLOCK, user_signals)
            if interrupted is not None and not (attempt_dir / "attempt-manifest.json").exists():
                _discard_terminated_worker_temps(publication_root)
                _recover_after_termination(authorization, journal_path, deadline=False)
        else:
            context = multiprocessing.get_context("fork")
            process = context.Process(
                target=_execute_worker,
                args=(authorization_path, checksum_path, journal_path, setup_signal_mask),
                name=f"m4-segmented-{authorization.attempt_id}",
            )
            wall_remaining = max(0.0, (authorization.not_after - _now()).total_seconds())
            monotonic_deadline = time.monotonic() + wall_remaining
            supervisor_monotonic_deadline = monotonic_deadline
            try:
                process.start()
                signal.pthread_sigmask(signal.SIG_SETMASK, setup_signal_mask)
                process.join(max(0.0, monotonic_deadline - time.monotonic()))
            except (KeyboardInterrupt, SystemExit, GeneratorExit) as exc:
                interrupted = exc
            post_worker_signal_mask = signal.pthread_sigmask(signal.SIG_BLOCK, user_signals)
            normal_deadline_reached = time.monotonic() > monotonic_deadline or _now() > authorization.not_after
            worker_started = process.pid is not None
            deadline = (
                worker_started
                and process.is_alive()
                and (time.monotonic() >= monotonic_deadline or _now() >= authorization.not_after)
            )
            if worker_started and process.is_alive():
                process.terminate()
                process.join(0.25)
                if process.is_alive():
                    if process.pid is None:
                        raise SecurityError("worker_pid_unavailable")
                    os.kill(process.pid, signal.SIGKILL)
                    process.join(0.25)
                deadline = deadline or time.monotonic() >= monotonic_deadline or _now() >= authorization.not_after
                _discard_terminated_worker_temps(publication_root)
                if not (attempt_dir / "attempt-manifest.json").exists():
                    _recover_after_termination(authorization, journal_path, deadline=deadline)
            elif worker_started and process.exitcode != 0 and not (attempt_dir / "attempt-manifest.json").exists():
                _discard_terminated_worker_temps(publication_root)
                _recover_after_termination(authorization, journal_path, deadline=False)
            elif not worker_started:
                _recover_after_termination(authorization, journal_path, deadline=False)
        try:
            with _defer_publication_signals():
                result = _finalize_supervised(
                    authorization,
                    attempt_dir,
                    parent=parent,
                    lock_path=lock_path,
                    journal_path=journal_path,
                    publication_root=publication_root,
                    normal_deadline_reached=normal_deadline_reached,
                    supervisor_monotonic_deadline=supervisor_monotonic_deadline,
                )
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as exc:
            # Finalization is idempotent once the manifest exists. Complete it
            # under deferred user signals, then propagate the original cancel.
            with _defer_publication_signals():
                _finalize_supervised(
                    authorization,
                    attempt_dir,
                    parent=parent,
                    lock_path=lock_path,
                    journal_path=journal_path,
                    publication_root=publication_root,
                    normal_deadline_reached=normal_deadline_reached,
                    supervisor_monotonic_deadline=supervisor_monotonic_deadline,
                )
            raise exc
        if interrupted is not None:
            raise interrupted
        return result
    finally:
        for selected, handler in previous_handlers.items():
            signal.signal(selected, handler)
        if post_worker_signal_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, post_worker_signal_mask)
        else:
            signal.pthread_sigmask(signal.SIG_SETMASK, setup_signal_mask)


__all__: list[str] = []
