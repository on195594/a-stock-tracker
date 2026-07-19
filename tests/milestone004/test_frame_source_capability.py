from __future__ import annotations

import hashlib
import fcntl
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

import pytest

from a_stock_tracker.qualitative.audit import SUPER_STRATA, SW2021_INDUSTRIES, canonical_json_bytes
from a_stock_tracker.qualitative.m4.capability import (
    API_URL,
    AUTHORIZATION_SCHEMA_VERSION,
    PROTOCOL_HASH_PATH,
    PROTOCOL_PATH,
    CapabilityAuthorizationError,
    CapabilityCall,
    CapabilityResponse,
    CapabilitySecurityError,
    CapabilityTransportError,
    CapabilityVerificationError,
    capability_call_matrix,
    load_capability_authorization,
    probe_m4_frame_source,
    reject_non_adoptable_probe,
    verify_capability_probe,
)
from scripts.export_m4_sampling_frame_akshare import ExportError
from scripts.export_m4_sampling_frame_historical_hybrid import assemble_historical_sampling_frame


VALID_TIME = datetime.fromisoformat("2026-07-17T10:00:00+08:00")
TOKEN = "synthetic-secret-token-847291"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _protocol_hash() -> str:
    raw = PROTOCOL_PATH.read_bytes()
    expected = PROTOCOL_HASH_PATH.read_text(encoding="ascii").split()[0]
    assert _sha256(raw) == expected
    return expected


def _authorization_value(output_root: Path, attempt_id: str) -> dict[str, object]:
    matrix = capability_call_matrix(datetime.fromisoformat("2026-07-15").date())
    return {
        "allowed_calls": [call.authorization_value() for call in matrix],
        "attempt_id": attempt_id,
        "authorization_id": f"synthetic-{attempt_id}",
        "not_after": "2026-07-18T00:00:00+08:00",
        "not_before": "2026-07-17T00:00:00+08:00",
        "output_root": str(output_root.resolve()),
        "probe_trade_date": "2026-07-15",
        "protocol_sha256": _protocol_hash(),
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
    }


def _make_authorization(
    tmp_path: Path,
    output_root: Path,
    attempt_id: str,
    mutate: Callable[[dict[str, object]], None] | None = None,
) -> tuple[Path, Path]:
    value = _authorization_value(output_root, attempt_id)
    if mutate is not None:
        mutate(value)
    raw = canonical_json_bytes(value)
    path = tmp_path / f"{attempt_id}.json"
    checksum = tmp_path / f"{attempt_id}.sha256"
    path.write_bytes(raw)
    checksum.write_text(f"{_sha256(raw)}  {path.name}\n", encoding="ascii")
    return path, checksum


def _stock_rows(count: int = 4_030) -> list[list[object]]:
    industries = list(SW2021_INDUSTRIES.items())
    rows: list[list[object]] = []
    for index in range(count):
        industry_code, industry_name = industries[index % len(industries)]
        sequence = index // 2
        number = 600000 + sequence if index % 2 == 0 else 300000 + sequence
        suffix = "SH" if index % 2 == 0 else "SZ"
        rows.append([industry_code, industry_name, f"{number:06d}.{suffix}", f"公司{index:04d}", "Y"])
    return rows


class SyntheticBackend:
    def __init__(self, mutation: str | None = None) -> None:
        self.mutation = mutation
        self.calls: list[str] = []
        self.members = _stock_rows()

    def _classification(self) -> list[list[object]]:
        rows: list[list[object]] = [[code, name, "L1", "SW2021"] for code, name in SW2021_INDUSTRIES.items()]
        if self.mutation == "classification_missing":
            rows.pop()
        elif self.mutation == "classification_duplicate":
            rows.append(rows[0].copy())
        elif self.mutation == "classification_crossed":
            rows[0][1] = rows[1][1]
        return rows

    def _members(self) -> list[list[object]]:
        rows = [row.copy() for row in self.members]
        if self.mutation == "members_too_few":
            rows = rows[:3_999]
        elif self.mutation == "members_service_limit":
            rows = _stock_rows(5_000)
        elif self.mutation == "members_invalid_code":
            rows[0][2] = "830001.BJ"
        elif self.mutation == "members_duplicate":
            rows[-1] = rows[0].copy()
        elif self.mutation == "members_unknown_mapping":
            rows[0][0:2] = ["999999.SI", "未知"]
        elif self.mutation == "members_missing_industry":
            missing = list(SW2021_INDUSTRIES)[-1]
            replacement = list(SW2021_INDUSTRIES.items())[0]
            for row in rows:
                if row[0] == missing:
                    row[0:2] = replacement
        elif self.mutation == "intersection_cell_short":
            financial = set(SUPER_STRATA["金融地产"])
            replacement = next((code, name) for code, name in SW2021_INDUSTRIES.items() if name == "电子")
            kept: set[str] = set()
            for row in rows:
                if row[1] in financial:
                    if row[1] in kept:
                        row[0:2] = replacement
                    else:
                        kept.add(str(row[1]))
        return rows

    def _daily(self) -> list[list[object]]:
        rows = [[row[2], "20260715", f"{index + 1}.25"] for index, row in enumerate(self.members)]
        if self.mutation == "daily_too_few":
            rows = rows[:3_999]
        elif self.mutation == "daily_service_limit":
            extra = _stock_rows(6_000)
            rows = [[row[2], "20260715", f"{index + 1}.25"] for index, row in enumerate(extra)]
        elif self.mutation == "daily_date_drift":
            rows[0][1] = "20260714"
        elif self.mutation == "daily_zero":
            rows[0][2] = "0"
        elif self.mutation == "daily_negative":
            rows[0][2] = "-1"
        elif self.mutation == "daily_nan":
            rows[0][2] = "NaN"
        elif self.mutation == "daily_duplicate":
            rows[-1] = rows[0].copy()
        return rows

    def call(self, call: CapabilityCall) -> CapabilityResponse:
        self.calls.append(call.call_id)
        if self.mutation == "transport_first" and len(self.calls) == 1:
            raise CapabilityTransportError("transport_timeout")
        if self.mutation == "cancel_second" and len(self.calls) == 2:
            raise KeyboardInterrupt
        if call.api_name == "index_classify":
            rows = self._classification()
        elif call.api_name == "index_member_all":
            rows = self._members()
        else:
            rows = self._daily()
        data: dict[str, object] = {"fields": list(call.fields), "items": rows}
        if self.mutation == "members_pagination" and call.api_name == "index_member_all":
            data["has_more"] = False
        if self.mutation == "daily_pagination" and call.api_name == "daily_basic":
            data["count"] = len(rows)
        value: dict[str, object] = {"code": 0, "msg": None, "data": data}
        if self.mutation == "provider_first" and len(self.calls) == 1:
            value = {"code": 40203, "msg": "denied", "data": None}
        raw = canonical_json_bytes(value)
        if self.mutation == "invalid_json_first" and len(self.calls) == 1:
            raw = b"not-json"
        if self.mutation == "token_echo_first" and len(self.calls) == 1:
            raw += TOKEN.encode()
        response_url = "https://evil.example" if self.mutation == "redirect_first" and len(self.calls) == 1 else API_URL
        status = 429 if self.mutation == "http_first" and len(self.calls) == 1 else 200
        return CapabilityResponse(API_URL, response_url, status, "application/json", raw)


def _run(tmp_path: Path, mutation: str | None = None, *, attempt_id: str = "attempt-one"):
    output_root = tmp_path / "probes"
    auth, checksum = _make_authorization(tmp_path, output_root, attempt_id)
    backend = SyntheticBackend(mutation)
    result = probe_m4_frame_source(
        authorization_path=auth,
        authorization_hash_path=checksum,
        attempt_id=attempt_id,
        output_root=output_root,
        token=TOKEN,
        backend=backend,
        now=VALID_TIME,
        clock=lambda: VALID_TIME,
    )
    return result, backend


def test_capability_pass_is_complete_non_adoptable_and_offline_verifiable(tmp_path: Path) -> None:
    result, backend = _run(tmp_path)
    assert backend.calls == ["tushare:index_classify", "tushare:index_member_all", "tushare:daily_basic"]
    assert result.complete and result.capability_pass
    assert result.terminal_state == "ELIGIBLE_FOR_FULL_CAPTURE_AUTHORIZATION"
    assert verify_capability_probe(result.attempt_dir) == result
    manifest = json.loads(result.manifest_path.read_bytes())
    assert manifest["non_adoptable"] is True
    assert all(
        json.loads((result.attempt_dir / item["relative_path"]).read_bytes())["non_adoptable"]
        for item in manifest["receipts"]
    )
    assert not any(TOKEN.encode() in path.read_bytes() for path in result.attempt_dir.rglob("*") if path.is_file())


@pytest.mark.parametrize(
    "mutation",
    [
        "classification_missing",
        "classification_duplicate",
        "classification_crossed",
        "members_too_few",
        "members_service_limit",
        "members_invalid_code",
        "members_duplicate",
        "members_unknown_mapping",
        "members_missing_industry",
        "members_pagination",
        "daily_too_few",
        "daily_service_limit",
        "daily_date_drift",
        "daily_zero",
        "daily_negative",
        "daily_nan",
        "daily_duplicate",
        "daily_pagination",
        "intersection_cell_short",
    ],
)
def test_data_gate_failure_completes_diagnostic_but_fails_capability(tmp_path: Path, mutation: str) -> None:
    result, backend = _run(tmp_path, mutation)
    assert len(backend.calls) == 3
    assert result.complete is True
    assert result.capability_pass is False
    assert result.terminal_state == "NO_QUALIFIED_FRAME_SOURCE"
    assert verify_capability_probe(result.attempt_dir).manifest_sha256 == result.manifest_sha256


@pytest.mark.parametrize("mutation", ["transport_first", "provider_first", "invalid_json_first", "http_first"])
def test_ordinary_failure_records_receipt_and_completes_matrix(tmp_path: Path, mutation: str) -> None:
    result, backend = _run(tmp_path, mutation)
    assert len(backend.calls) == 3
    assert result.complete and not result.capability_pass
    assert result.receipts[0].status == "failed"
    assert result.receipts[0].error_code in {
        "transport_timeout",
        "provider_error",
        "response_json_invalid",
        "http_4xx",
    }


@pytest.mark.parametrize(
    ("field", "replacement", "error_code"),
    [
        ("attempt_id", "different", "authorization_attempt_mismatch"),
        ("protocol_sha256", "0" * 64, "authorization_protocol_mismatch"),
        ("probe_trade_date", "2026-07-14", "authorization_call_matrix_mismatch"),
        ("output_root", "/tmp/outside", "authorization_output_root_mismatch"),
        ("not_before", "2026-07-18T01:00:00+08:00", "authorization_window_invalid"),
    ],
)
def test_authorization_binding_mismatch_blocks_before_output(
    tmp_path: Path, field: str, replacement: object, error_code: str
) -> None:
    output = tmp_path / "probes"
    attempt = "auth-mismatch"
    auth, checksum = _make_authorization(tmp_path, output, attempt, lambda value: value.__setitem__(field, replacement))
    with pytest.raises(CapabilityAuthorizationError) as caught:
        probe_m4_frame_source(
            authorization_path=auth,
            authorization_hash_path=checksum,
            attempt_id=attempt,
            output_root=output,
            token=TOKEN,
            backend=SyntheticBackend(),
            now=VALID_TIME,
            clock=lambda: VALID_TIME,
        )
    assert caught.value.error_code == error_code
    assert not output.exists()


@pytest.mark.parametrize(
    ("now", "error_code"),
    [
        (datetime.fromisoformat("2026-07-16T23:59:59+08:00"), "authorization_not_yet_valid"),
        (datetime.fromisoformat("2026-07-18T00:00:01+08:00"), "authorization_expired"),
    ],
)
def test_authorization_window_blocks_without_network_or_artifact(
    tmp_path: Path, now: datetime, error_code: str
) -> None:
    output = tmp_path / "probes"
    auth, checksum = _make_authorization(tmp_path, output, "window")
    backend = SyntheticBackend()
    with pytest.raises(CapabilityAuthorizationError) as caught:
        probe_m4_frame_source(
            authorization_path=auth,
            authorization_hash_path=checksum,
            attempt_id="window",
            output_root=output,
            token=TOKEN,
            backend=backend,
            now=now,
            clock=lambda: now,
        )
    assert caught.value.error_code == error_code
    assert backend.calls == []
    assert not output.exists()


def test_hash_drift_and_noncanonical_authorization_fail_closed(tmp_path: Path) -> None:
    output = tmp_path / "probes"
    auth, checksum = _make_authorization(tmp_path, output, "hash-drift")
    auth.write_bytes(auth.read_bytes() + b"\n")
    with pytest.raises(CapabilityAuthorizationError, match="authorization_hash_mismatch"):
        load_capability_authorization(
            auth,
            checksum,
            attempt_id="hash-drift",
            output_root=output,
            now=VALID_TIME,
        )
    checksum.write_text(f"{_sha256(auth.read_bytes())}  {auth.name}\n", encoding="ascii")
    with pytest.raises(CapabilityAuthorizationError, match="authorization_not_canonical"):
        load_capability_authorization(
            auth,
            checksum,
            attempt_id="hash-drift",
            output_root=output,
            now=VALID_TIME,
        )


def test_token_echo_is_blocked_before_raw_write_and_stops_calls(tmp_path: Path) -> None:
    output = tmp_path / "probes"
    auth, checksum = _make_authorization(tmp_path, output, "token-echo")
    backend = SyntheticBackend("token_echo_first")
    with pytest.raises(CapabilitySecurityError, match="token_echo_detected"):
        probe_m4_frame_source(
            authorization_path=auth,
            authorization_hash_path=checksum,
            attempt_id="token-echo",
            output_root=output,
            token=TOKEN,
            backend=backend,
            now=VALID_TIME,
            clock=lambda: VALID_TIME,
        )
    assert backend.calls == ["tushare:index_classify"]
    sealed = output / "probe-token-echo"
    assert sealed.is_dir()
    assert not list((sealed / "blobs").glob("*")) if (sealed / "blobs").exists() else True
    assert not any(TOKEN.encode() in path.read_bytes() for path in sealed.rglob("*") if path.is_file())


def test_redirect_security_failure_stops_and_seals_incomplete(tmp_path: Path) -> None:
    output = tmp_path / "probes"
    auth, checksum = _make_authorization(tmp_path, output, "redirect")
    backend = SyntheticBackend("redirect_first")
    with pytest.raises(CapabilitySecurityError, match="transport_origin_or_redirect_invalid"):
        probe_m4_frame_source(
            authorization_path=auth,
            authorization_hash_path=checksum,
            attempt_id="redirect",
            output_root=output,
            token=TOKEN,
            backend=backend,
            now=VALID_TIME,
            clock=lambda: VALID_TIME,
        )
    assert backend.calls == ["tushare:index_classify"]
    assert verify_capability_probe(output / "probe-redirect").complete is False


def test_process_control_signal_stops_following_calls_and_propagates_unchanged(tmp_path: Path) -> None:
    output = tmp_path / "probes"
    auth, checksum = _make_authorization(tmp_path, output, "cancel")
    backend = SyntheticBackend("cancel_second")
    with pytest.raises(KeyboardInterrupt):
        probe_m4_frame_source(
            authorization_path=auth,
            authorization_hash_path=checksum,
            attempt_id="cancel",
            output_root=output,
            token=TOKEN,
            backend=backend,
            now=VALID_TIME,
            clock=lambda: VALID_TIME,
        )
    assert backend.calls == ["tushare:index_classify", "tushare:index_member_all"]
    verified = verify_capability_probe(output / "probe-cancel")
    assert not verified.complete and len(verified.receipts) == 1


def test_per_call_expiry_revalidation_stops_before_next_call(tmp_path: Path) -> None:
    output = tmp_path / "probes"
    auth, checksum = _make_authorization(tmp_path, output, "expires")
    current = [VALID_TIME]

    class ExpiringBackend(SyntheticBackend):
        def call(self, call: CapabilityCall) -> CapabilityResponse:
            response = super().call(call)
            current[0] = datetime.fromisoformat("2026-07-18T00:00:01+08:00")
            return response

    backend = ExpiringBackend()
    with pytest.raises(CapabilityAuthorizationError, match="authorization_expired"):
        probe_m4_frame_source(
            authorization_path=auth,
            authorization_hash_path=checksum,
            attempt_id="expires",
            output_root=output,
            token=TOKEN,
            backend=backend,
            now=VALID_TIME,
            clock=lambda: current[0],
        )
    assert backend.calls == ["tushare:index_classify"]
    assert not verify_capability_probe(output / "probe-expires").complete


def test_authorization_is_rehashed_after_response_before_raw_publication(tmp_path: Path) -> None:
    output = tmp_path / "probes"
    auth, checksum = _make_authorization(tmp_path, output, "auth-change")

    class MutatingBackend(SyntheticBackend):
        def call(self, call: CapabilityCall) -> CapabilityResponse:
            response = super().call(call)
            auth.chmod(0o644)
            auth.write_bytes(auth.read_bytes() + b"\n")
            return response

    backend = MutatingBackend()
    with pytest.raises(CapabilityAuthorizationError, match="authorization_hash_mismatch"):
        probe_m4_frame_source(
            authorization_path=auth,
            authorization_hash_path=checksum,
            attempt_id="auth-change",
            output_root=output,
            token=TOKEN,
            backend=backend,
            now=VALID_TIME,
            clock=lambda: VALID_TIME,
        )
    assert backend.calls == ["tushare:index_classify"]
    sealed = output / "probe-auth-change"
    assert sealed.is_dir()
    assert not (sealed / "blobs").exists()


def test_output_lock_serializes_attempt_publication(tmp_path: Path) -> None:
    output = tmp_path / "probes"
    output.mkdir()
    auth, checksum = _make_authorization(tmp_path, output, "locked")
    lock = (output / ".capability.lock").open("w")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    started = threading.Event()
    completed = threading.Event()
    errors: list[BaseException] = []

    def worker() -> None:
        started.set()
        try:
            probe_m4_frame_source(
                authorization_path=auth,
                authorization_hash_path=checksum,
                attempt_id="locked",
                output_root=output,
                token=TOKEN,
                backend=SyntheticBackend(),
                now=VALID_TIME,
                clock=lambda: VALID_TIME,
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            completed.set()

    thread = threading.Thread(target=worker)
    thread.start()
    assert started.wait(timeout=2)
    assert not completed.wait(timeout=0.1)
    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    lock.close()
    thread.join(timeout=10)
    assert completed.is_set() and errors == []
    assert verify_capability_probe(output / "probe-locked").capability_pass


@pytest.mark.parametrize("target", ["manifest", "receipt", "blob"])
def test_offline_verifier_rejects_tampering(tmp_path: Path, target: str) -> None:
    result, _ = _run(tmp_path, attempt_id=f"tamper-{target}")
    if target == "manifest":
        path = result.manifest_path
    elif target == "receipt":
        path = result.attempt_dir / result.receipts[0].receipt_relative_path
    else:
        assert result.receipts[0].blob_relative_path is not None
        path = result.attempt_dir / result.receipts[0].blob_relative_path
    path.chmod(0o644)
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(CapabilityVerificationError):
        verify_capability_probe(result.attempt_dir)


def test_symlink_path_traversal_extra_file_and_attempt_reuse_are_rejected(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, attempt_id="integrity")
    result.attempt_dir.chmod(0o755)
    extra = result.attempt_dir / "extra"
    extra.write_text("x", encoding="utf-8")
    with pytest.raises(CapabilityVerificationError, match="attempt_file_set_invalid"):
        verify_capability_probe(result.attempt_dir)
    extra.unlink()
    link = result.attempt_dir / "link"
    link.symlink_to(result.manifest_path)
    with pytest.raises(CapabilityVerificationError, match="attempt_contains_symlink"):
        verify_capability_probe(result.attempt_dir)
    link.unlink()
    auth = tmp_path / "integrity.json"
    checksum = tmp_path / "integrity.sha256"
    with pytest.raises(CapabilitySecurityError, match="attempt_already_exists"):
        probe_m4_frame_source(
            authorization_path=auth,
            authorization_hash_path=checksum,
            attempt_id="integrity",
            output_root=tmp_path / "probes",
            token=TOKEN,
            backend=SyntheticBackend(),
            now=VALID_TIME,
            clock=lambda: VALID_TIME,
        )


def test_non_adoptable_probe_is_rejected_by_new_and_legacy_assembly_boundaries(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, attempt_id="non-adoptable")
    with pytest.raises(CapabilityVerificationError, match="non_adoptable_probe_rejected"):
        reject_non_adoptable_probe(result.attempt_dir)
    with pytest.raises(ExportError, match="non-adoptable capability probe"):
        assemble_historical_sampling_frame(attempt_dir=result.attempt_dir, output_root=tmp_path / "frames")


def test_response_and_artifact_types_are_frozen() -> None:
    response = CapabilityResponse(API_URL, API_URL, 200, "application/json", b"{}")
    with pytest.raises((AttributeError, TypeError)):
        response.status_code = 500  # type: ignore[misc]
    call = capability_call_matrix(datetime.fromisoformat("2026-07-15").date())[0]
    assert call.params == (("level", "L1"), ("src", "SW2021"))


def test_authorization_file_from_plan_is_canonical_and_bound() -> None:
    root = Path(__file__).resolve().parents[2]
    auth = root / "reviews/milestone-004-preregistration-v1.2/capability-authorization-2026-07-16-01.json"
    checksum = auth.with_suffix(".sha256")
    expected_output = root / "artifacts/milestone-004/v1.2/capability-probes"
    loaded = load_capability_authorization(
        auth,
        checksum,
        attempt_id="m4-v1.2-capability-20260716-01",
        output_root=expected_output,
        now=VALID_TIME,
    )
    assert loaded.probe_trade_date.isoformat() == "2026-07-15"
    assert loaded.sha256 == checksum.read_text(encoding="ascii").split()[0]


def test_protocol_v1_v11_v12_hash_chain_is_frozen() -> None:
    root = Path(__file__).resolve().parents[2]
    v11 = root / "docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration-v1.1.md"
    v12 = PROTOCOL_PATH
    assert _sha256(v11.read_bytes()) == "6990c12859da94ec5648fc26c3c3969282226a4c703d2cb4c189990ba1f2529f"
    assert "Supersedes SHA-256: `6990c128" in v12.read_text(encoding="utf-8")
    assert _sha256(v12.read_bytes()) == _protocol_hash()


def test_no_token_in_exception_stdout_stderr_or_artifacts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "probes"
    auth, checksum = _make_authorization(tmp_path, output, "redaction")
    backend = SyntheticBackend("token_echo_first")
    with pytest.raises(CapabilitySecurityError) as caught:
        probe_m4_frame_source(
            authorization_path=auth,
            authorization_hash_path=checksum,
            attempt_id="redaction",
            output_root=output,
            token=TOKEN,
            backend=backend,
            now=VALID_TIME,
            clock=lambda: VALID_TIME,
        )
    streams = capsys.readouterr()
    assert TOKEN not in str(caught.value)
    assert TOKEN not in streams.out + streams.err
    assert TOKEN.encode() not in b"".join(path.read_bytes() for path in output.rglob("*") if path.is_file())


def test_missing_authorization_does_not_read_token_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    with pytest.raises(CapabilityAuthorizationError, match="authorization_missing_or_unsafe"):
        probe_m4_frame_source(
            authorization_path=tmp_path / "missing.json",
            authorization_hash_path=tmp_path / "missing.sha256",
            attempt_id="missing",
            output_root=tmp_path / "output",
        )
    assert not (tmp_path / "output").exists()


def test_output_root_symlink_is_rejected(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    output = tmp_path / "linked"
    output.symlink_to(actual, target_is_directory=True)
    auth, checksum = _make_authorization(tmp_path, output, "symlink-root")
    with pytest.raises(CapabilitySecurityError, match="output_root_unsafe"):
        probe_m4_frame_source(
            authorization_path=auth,
            authorization_hash_path=checksum,
            attempt_id="symlink-root",
            output_root=output,
            token=TOKEN,
            backend=SyntheticBackend(),
            now=VALID_TIME,
            clock=lambda: VALID_TIME,
        )


def test_authorization_dataclass_cannot_be_mutated(tmp_path: Path) -> None:
    output = tmp_path / "probes"
    auth, checksum = _make_authorization(tmp_path, output, "frozen-auth")
    loaded = load_capability_authorization(auth, checksum, attempt_id="frozen-auth", output_root=output, now=VALID_TIME)
    with pytest.raises((AttributeError, TypeError)):
        loaded.attempt_id = "changed"  # type: ignore[misc]


def test_call_matrix_has_no_extra_parameters_or_calls() -> None:
    matrix = capability_call_matrix(datetime.fromisoformat("2026-07-15").date())
    assert len(matrix) == 3
    assert dict(matrix[1].params) == {"is_new": "Y"}
    assert "l1_code" not in dict(matrix[1].params)
    assert matrix[2].fields == ("ts_code", "trade_date", "total_mv")


def test_environment_is_not_modified_by_probe(tmp_path: Path) -> None:
    before = dict(os.environ)
    _run(tmp_path, attempt_id="environment")
    assert dict(os.environ) == before
