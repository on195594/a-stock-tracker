from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import is_dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import qualitative_v2_m4_segmented_core as core
from qualitative_v2_audit import (
    AuditBlockedError,
    SW2021_INDUSTRIES,
    canonical_json_bytes,
    reject_v131_segmented_attempt,
)


DETAILED_RUNTIME_PHASES = (
    "dns",
    "connect",
    "tls",
    "write",
    "headers",
    "body",
    "token_scan",
    "blob_fsync",
    "parse",
    "receipt_fsync",
    "gates",
    "candidate_verification",
    "manifest_publication",
)
from qualitative_v2_m4_segmented_core import AuthorizationError, SecurityError, VerificationError
from qualitative_v2_m4_segmented_core import row_sha256, strict_json_loads
from qualitative_v2_m4_segmented_verify import ParsedResponse, parse_provider_response, reproduce_date, reproduce_frame
from qualitative_v2_m4_segmented_rest import (
    CallReceipt,
    DateEvidenceAuthorization,
    FrameAuthorization,
    SegmentedRestCall,
    date_evidence_call_matrix,
    execute_segmented_rest_attempt,
    frame_call_matrix,
    load_authorization,
    require_capture_input_eligible,
    verify_segmented_rest_attempt,
)


def _workspace() -> tuple[Path, str]:
    identifier = f"synthetic-{uuid.uuid4().hex}"
    relative = f"artifacts/milestone-004/v1.3.1/test-only/{identifier}"
    path = core.PROJECT_ROOT / relative
    path.mkdir(parents=True)
    return path, relative


def _remove(path: Path) -> None:
    if not path.exists():
        return
    for item in path.rglob("*"):
        if item.exists():
            item.chmod(0o700 if item.is_dir() else 0o600)
    path.chmod(0o700)
    shutil.rmtree(path)


def _write_authorization(directory: Path, value: dict[str, object]) -> tuple[Path, Path]:
    path = directory / "authorization.json"
    raw = canonical_json_bytes(value)
    path.write_bytes(raw)
    relative = path.relative_to(core.PROJECT_ROOT).as_posix()
    checksum = directory / "authorization.sha256"
    checksum.write_text(f"{core.sha256_bytes(raw)}  {relative}\n", encoding="ascii")
    return path, checksum


def _capability_value(attempt_id: str, output: str, *, now: datetime) -> dict[str, object]:
    selected = date(2026, 7, 15)
    return {
        "attempt_byte_limit": core.FRAME_ATTEMPT_LIMIT,
        "attempt_id": attempt_id,
        "attempt_output_dir": output,
        "authorization_id": f"auth-{attempt_id}",
        "calls": [call.authorization_value() for call in frame_call_matrix(selected)],
        "capability_manifest_ref": None,
        "credential_env_var": "TUSHARE_TOKEN",
        "date_evidence_ref": None,
        "max_attempts_per_ordinal": 1,
        "mode": "capability",
        "not_after": (now + timedelta(minutes=5)).isoformat(timespec="seconds"),
        "not_before": (now - timedelta(minutes=5)).isoformat(timespec="seconds"),
        "origin": "https://api.tushare.pro",
        "protocol_path": core.PROTOCOL_RELATIVE_PATH,
        "protocol_sha256": core.protocol_sha256(),
        "response_byte_limit": core.FRAME_RESPONSE_LIMIT,
        "schema_version": core.FRAME_AUTH_SCHEMA,
        "supersedes_sha256": core.SUPERSEDES_SHA256,
        "trade_date": selected.isoformat(),
        "trade_date_kind": "probe_trade_date",
    }


@pytest.fixture
def sealed_failure_attempt(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    attempt_id = directory.name
    auth_dir = directory.parent / f"auth-{attempt_id}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        now = datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0)
        value = _capability_value(attempt_id, f"{root}/{attempt_id}", now=now)
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        result = execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        yield result, path, checksum
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


def test_both_authorization_schemas_reject_every_missing_and_unknown_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, relative = _workspace()
    previous_roots = dict(core.ROOTS)
    root = relative.rsplit("/", 1)[0]
    core.ROOTS.update({"capability": root, "date_selection_evidence": root, "capture": root})
    try:
        import qualitative_v2_m4_segmented_verify as verifier

        monkeypatch.setattr(verifier, "_verify_reference_chains", lambda _authorization, _visited: None)
        zone = timezone(timedelta(hours=8))
        now = datetime(2026, 7, 15, 19, tzinfo=zone)
        frame = _capability_value(directory.name, f"{root}/{directory.name}", now=now)
        proposed = date(2026, 7, 15)
        date_value = {
            "attempt_byte_limit": core.DATE_ATTEMPT_LIMIT,
            "attempt_id": directory.name,
            "attempt_output_dir": f"{root}/{directory.name}",
            "authorization_id": f"auth-{directory.name}",
            "calendar_end": (proposed + timedelta(days=62)).isoformat(),
            "calendar_start": (proposed - timedelta(days=62)).isoformat(),
            "calls": [call.authorization_value() for call in date_evidence_call_matrix(proposed)],
            "capability_manifest_ref": {
                "attempt_id": "fake-capability",
                "relative_path": "artifacts/fake/attempt-manifest.json",
                "sha256": "1" * 64,
            },
            "credential_env_var": "TUSHARE_TOKEN",
            "max_attempts_per_ordinal": 1,
            "not_after": datetime(2026, 7, 15, 23, 59, 59, tzinfo=zone).isoformat(timespec="seconds"),
            "not_before": datetime(2026, 7, 15, 18, tzinfo=zone).isoformat(timespec="seconds"),
            "origin": core.ORIGIN,
            "proposed_sampling_date": proposed.isoformat(),
            "protocol_path": core.PROTOCOL_RELATIVE_PATH,
            "protocol_sha256": core.protocol_sha256(),
            "purpose": "date_selection_evidence",
            "response_byte_limit": core.DATE_RESPONSE_LIMIT,
            "schema_version": core.DATE_AUTH_SCHEMA,
            "supersedes_sha256": core.SUPERSEDES_SHA256,
        }
        for base in (frame, date_value):
            for field in tuple(base):
                candidate = dict(base)
                candidate.pop(field)
                path, checksum = _write_authorization(directory, candidate)
                with pytest.raises(AuthorizationError):
                    load_authorization(path, checksum)
            path, checksum = _write_authorization(directory, {**base, "unknown_property": True})
            with pytest.raises(AuthorizationError):
                load_authorization(path, checksum)
    finally:
        core.ROOTS.clear()
        core.ROOTS.update(previous_roots)
        _remove(directory)


def test_authorization_cross_field_mismatch_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    directory, relative = _workspace()
    previous_roots = dict(core.ROOTS)
    root = relative.rsplit("/", 1)[0]
    core.ROOTS.update({"capability": root, "capture": root, "date_selection_evidence": root})
    try:
        import qualitative_v2_m4_segmented_verify as verifier

        monkeypatch.setattr(verifier, "_verify_reference_chains", lambda _authorization, _visited: None)
        zone = timezone(timedelta(hours=8))
        now = datetime(2026, 7, 15, 19, tzinfo=zone)
        base = _capability_value(directory.name, f"{root}/{directory.name}", now=now)
        mutations: tuple[dict[str, object], ...] = (
            {"mode": "invalid"},
            {"trade_date_kind": "sampling_date"},
            {"origin": "https://invalid.example"},
            {"credential_env_var": "OTHER_TOKEN"},
            {"max_attempts_per_ordinal": 2},
            {"response_byte_limit": core.FRAME_RESPONSE_LIMIT - 1},
            {"attempt_byte_limit": core.FRAME_ATTEMPT_LIMIT - 1},
            {"supersedes_sha256": "0" * 64},
            {"protocol_sha256": "0" * 64},
            {"calls": []},
            {"attempt_output_dir": f"{root}/wrong"},
            {"capability_manifest_ref": {"unexpected": True}},
            {
                "not_before": (now + timedelta(hours=1)).isoformat(timespec="seconds"),
                "not_after": now.isoformat(timespec="seconds"),
            },
        )
        for mutation in mutations:
            path, checksum = _write_authorization(directory, {**base, **mutation})
            with pytest.raises(AuthorizationError):
                load_authorization(path, checksum)

        capture = {
            **base,
            "mode": "capture",
            "trade_date_kind": "sampling_date",
            "capability_manifest_ref": {
                "attempt_id": "fake-capability",
                "relative_path": "artifacts/fake/attempt-manifest.json",
                "sha256": "1" * 64,
            },
            "date_evidence_ref": {
                "attempt_id": "fake-date",
                "authorization_id": "auth-fake-date",
                "relative_path": "artifacts/fake/date-selection-evidence.json",
                "sha256": "2" * 64,
            },
            "not_before": datetime(2026, 7, 15, 18, tzinfo=zone).isoformat(timespec="seconds"),
            "not_after": datetime(2026, 7, 15, 23, tzinfo=zone).isoformat(timespec="seconds"),
        }
        capture_mutations: tuple[dict[str, object], ...] = (
            {"capability_manifest_ref": None},
            {"date_evidence_ref": None},
            {"trade_date_kind": "probe_trade_date"},
            {"not_before": datetime(2026, 7, 15, 17, tzinfo=zone).isoformat(timespec="seconds")},
            {"not_after": datetime(2026, 7, 16, 0, tzinfo=zone).isoformat(timespec="seconds")},
        )
        for mutation in capture_mutations:
            path, checksum = _write_authorization(directory, {**capture, **mutation})
            with pytest.raises(AuthorizationError):
                load_authorization(path, checksum)

        proposed = date(2026, 7, 15)
        date_base = {
            "attempt_byte_limit": core.DATE_ATTEMPT_LIMIT,
            "attempt_id": directory.name,
            "attempt_output_dir": f"{root}/{directory.name}",
            "authorization_id": f"auth-{directory.name}",
            "calendar_end": (proposed + timedelta(days=62)).isoformat(),
            "calendar_start": (proposed - timedelta(days=62)).isoformat(),
            "calls": [call.authorization_value() for call in date_evidence_call_matrix(proposed)],
            "capability_manifest_ref": {
                "attempt_id": "fake-capability",
                "relative_path": "artifacts/fake/attempt-manifest.json",
                "sha256": "1" * 64,
            },
            "credential_env_var": "TUSHARE_TOKEN",
            "max_attempts_per_ordinal": 1,
            "not_after": datetime(2026, 7, 15, 23, 59, 59, tzinfo=zone).isoformat(timespec="seconds"),
            "not_before": datetime(2026, 7, 15, 18, tzinfo=zone).isoformat(timespec="seconds"),
            "origin": core.ORIGIN,
            "proposed_sampling_date": proposed.isoformat(),
            "protocol_path": core.PROTOCOL_RELATIVE_PATH,
            "protocol_sha256": core.protocol_sha256(),
            "purpose": "date_selection_evidence",
            "response_byte_limit": core.DATE_RESPONSE_LIMIT,
            "schema_version": core.DATE_AUTH_SCHEMA,
            "supersedes_sha256": core.SUPERSEDES_SHA256,
        }
        date_mutations: tuple[dict[str, object], ...] = (
            {"purpose": "capture"},
            {"calendar_start": (proposed - timedelta(days=61)).isoformat()},
            {"calendar_end": (proposed + timedelta(days=61)).isoformat()},
            {"calls": []},
            {"response_byte_limit": core.DATE_RESPONSE_LIMIT - 1},
            {"attempt_byte_limit": core.DATE_ATTEMPT_LIMIT - 1},
            {"capability_manifest_ref": None},
            {"origin": "https://invalid.example"},
            {"max_attempts_per_ordinal": 2},
        )
        for mutation in date_mutations:
            path, checksum = _write_authorization(directory, {**date_base, **mutation})
            with pytest.raises((AuthorizationError, VerificationError)):
                load_authorization(path, checksum)
    finally:
        core.ROOTS.clear()
        core.ROOTS.update(previous_roots)
        _remove(directory)


def test_v13_frozen_hash_and_v131_direct_lineage() -> None:
    old = core.PROJECT_ROOT / "docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md"
    assert core.sha256_bytes(old.read_bytes()) == core.SUPERSEDES_SHA256
    assert core.protocol_sha256() == "f494c1973f002536874782af41221d37a2ca618c329fa48a2ae650b544c88042"


def test_v131_golden_vector_index_is_frozen_and_complete() -> None:
    path = core.PROJECT_ROOT / "reviews/milestone-004-preregistration-v1.3.1/golden-vectors.json"
    raw = path.read_bytes()
    value = json.loads(raw)
    assert canonical_json_bytes(value) == raw
    assert value["schema_version"] == "m4-segmented-rest-golden-vectors-v1"
    assert value["protocol_sha256"] == core.protocol_sha256()
    assert set(value) == {
        "schema_version",
        "protocol_sha256",
        "frame_authorization",
        "date_authorization",
        "membership_row",
        "exclusion_ledger",
        "stop_record",
        "date_evidence",
        "manifest",
    }
    assert value["membership_row"]["sha256"] == row_sha256(
        (
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
        ),
        ("801010.SI", "农林牧渔", None, None, None, None, "600000.SH", "浦发银行", "20200101", None, "Y"),
    )

    def assert_json_vector(name: str, item: object) -> None:
        encoded = canonical_json_bytes(item)
        assert len(encoded) == value[name]["byte_count"]
        assert core.sha256_bytes(encoded) == value[name]["sha256"]

    zone = timezone(timedelta(hours=8))
    frame_authorization = _capability_value(
        "golden-capability",
        "artifacts/milestone-004/v1.3.1/capability-probes/golden-capability",
        now=datetime(2026, 7, 17, 20, tzinfo=zone),
    )
    assert_json_vector("frame_authorization", frame_authorization)

    date_authorization, calendars, receipts, date_sealed_at = _valid_date_reproduction()
    date_value = {
        "attempt_byte_limit": core.DATE_ATTEMPT_LIMIT,
        "attempt_id": date_authorization.attempt_id,
        "attempt_output_dir": date_authorization.attempt_output_dir,
        "authorization_id": date_authorization.authorization_id,
        "calls": [call.authorization_value() for call in date_authorization.calls],
        "calendar_end": date_authorization.calendar_end.isoformat(),
        "calendar_start": date_authorization.calendar_start.isoformat(),
        "capability_manifest_ref": dict(date_authorization.capability_manifest_ref),
        "credential_env_var": "TUSHARE_TOKEN",
        "max_attempts_per_ordinal": 1,
        "not_after": date_authorization.not_after.isoformat(timespec="seconds"),
        "not_before": date_authorization.not_before.isoformat(timespec="seconds"),
        "origin": core.ORIGIN,
        "proposed_sampling_date": date_authorization.proposed_sampling_date.isoformat(),
        "protocol_path": core.PROTOCOL_RELATIVE_PATH,
        "protocol_sha256": core.protocol_sha256(),
        "purpose": "date_selection_evidence",
        "response_byte_limit": core.DATE_RESPONSE_LIMIT,
        "schema_version": core.DATE_AUTH_SCHEMA,
        "supersedes_sha256": core.SUPERSEDES_SHA256,
    }
    assert_json_vector("date_authorization", date_value)
    date_reproduction = reproduce_date(
        date_authorization,
        calendars,
        receipts,
        complete=True,
        authorization_window=True,
        sealed_at=date_sealed_at,
    )
    assert_json_vector("date_evidence", date_reproduction.evidence)

    frame_gate_authorization, responses = _valid_frame_reproduction()
    membership = list(responses[2].rows[0])
    membership[6] = "920001.BJ"
    responses[2] = ParsedResponse(responses[2].fields, (tuple(membership), *responses[2].rows[1:]))
    ledger = reproduce_frame(frame_gate_authorization, responses, complete=True, authorization_window=True).ledger
    assert_json_vector("exclusion_ledger", ledger)

    stop = {
        "attempt_id": "golden-capability",
        "authorization_id": "auth-golden-capability",
        "next_ordinal": 1,
        "schema_version": core.STOP_SCHEMA,
        "stop_code": "credential_unavailable",
        "stopped_at": "2026-07-17T20:00:00+08:00",
    }
    assert_json_vector("stop_record", stop)
    stop_raw = canonical_json_bytes(stop)
    stop_ref = {
        "byte_count": len(stop_raw),
        "kind": "stop_record",
        "relative_path": "stop-record.json",
        "sha256": core.sha256_bytes(stop_raw),
    }
    commit = {
        "attempt_id": "golden-capability",
        "authorization_id": "auth-golden-capability",
        "execution_deadline": "2026-07-17T20:05:00+08:00",
        "nonce": "a" * 64,
        "outcome": "fail",
        "schema_version": core.SUPERVISOR_COMMIT_SCHEMA,
    }
    commit_raw = canonical_json_bytes(commit)
    commit_ref = {
        "byte_count": len(commit_raw),
        "kind": "supervisor_commit",
        "relative_path": "supervisor-commit.json",
        "sha256": core.sha256_bytes(commit_raw),
    }
    ledger = {
        "attempt_id": "golden-capability",
        "authorization_id": "auth-golden-capability",
        "entries": [],
        "mode": "capability",
        "schema_version": core.LEDGER_SCHEMA,
    }
    ledger_raw = canonical_json_bytes(ledger)
    ledger_ref = {
        "byte_count": len(ledger_raw),
        "kind": "exclusion_ledger",
        "relative_path": "exclusion-ledger.json",
        "sha256": core.sha256_bytes(ledger_raw),
    }
    gates = {
        "authorization_window": True,
        "calendar": None,
        "classification": None,
        "daily": None,
        "date_derivation": None,
        "frame": None,
        "membership": None,
        "overall_pass": False,
        "sampling_cells": None,
        "stock_basic": None,
        "stock_st": None,
    }
    manifest = {
        "artifact_files": sorted([stop_ref, commit_ref, ledger_ref], key=lambda item: str(item["relative_path"])),
        "attempt_id": "golden-capability",
        "attempt_kind": "capability",
        "authorization_id": "auth-golden-capability",
        "authorization_relative_path": "reviews/golden/authorization.json",
        "authorization_sha256": "1" * 64,
        "blob_refs": [],
        "capture_input_eligible": False,
        "complete": False,
        "date_evidence_ref": None,
        "date_selection_only": False,
        "disposition": "NO_QUALIFIED_FRAME_SOURCE",
        "exclusion_counts": {
            "by_reason": {
                reason: 0
                for reason in (
                    "delisting_consolidation",
                    "exchange_out_of_scope_bse",
                    "listing_age_below_36_months",
                    "not_in_frame_eligible_membership",
                    "not_in_frozen_sw2021_membership",
                    "security_type_out_of_scope",
                    "st_risk_warning",
                )
            },
            "by_source": {source: 0 for source in ("membership", "stock_basic", "stock_st", "daily")},
            "total": 0,
        },
        "exclusion_ledger_ref": ledger_ref,
        "execution_deadline": "2026-07-17T20:05:00+08:00",
        "expected_ordinals": list(range(1, 37)),
        "failed_ordinal": None,
        "gate_results": gates,
        "generator_files": [
            {"relative_path": name, "sha256": str(index) * 64} for index, name in enumerate(core.GENERATOR_FILES, 1)
        ],
        "non_adoptable": True,
        "protocol_path": core.PROTOCOL_RELATIVE_PATH,
        "protocol_sha256": core.protocol_sha256(),
        "receipt_refs": [],
        "response_byte_total": 0,
        "schema_version": core.MANIFEST_SCHEMA,
        "sealed_at": "2026-07-17T20:00:00+08:00",
        "stop_record_ref": stop_ref,
        "supervisor_commit_ref": {**commit_ref, "nonce": "a" * 64},
        "successful_ordinals": [],
        "supersedes_sha256": core.SUPERSEDES_SHA256,
        "terminal_failed_ordinals": [],
        "unattempted_ordinals": list(range(1, 37)),
    }
    assert_json_vector("manifest", manifest)


def test_exact_frame_matrix() -> None:
    calls = frame_call_matrix(date(2026, 7, 15))
    assert len(calls) == 36
    assert [call.ordinal for call in calls] == list(range(1, 37))
    assert [dict(call.params)["l1_code"] for call in calls[1:32]] == list(SW2021_INDUSTRIES)
    assert [call.api_name for call in calls[-4:]] == ["stock_basic", "stock_basic", "stock_st", "daily_basic"]
    assert dict(calls[-1].params) == {"trade_date": "20260715"}


def test_exact_date_matrix_uses_gregorian_62_day_bounds() -> None:
    calls = date_evidence_call_matrix(date(2026, 7, 15))
    assert [call.call_id for call in calls] == ["tushare:trade_cal:SSE", "tushare:trade_cal:SZSE"]
    assert dict(calls[0].params) == {"exchange": "SSE", "start_date": "20260514", "end_date": "20260915"}


def test_load_strict_capability_authorization() -> None:
    directory, relative = _workspace()
    attempt_id = directory.name
    core.ROOTS["capability"] = relative
    try:
        value = _capability_value(
            attempt_id, f"{relative}/{attempt_id}", now=datetime.now(timezone(timedelta(hours=8)))
        )
        # The root itself must not repeat the attempt id.
        value["attempt_output_dir"] = f"{relative}/{attempt_id}"
        path, checksum = _write_authorization(directory, value)
        authorization = load_authorization(path, checksum)
        assert isinstance(authorization, FrameAuthorization)
        assert authorization.mode == "capability"
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)


def test_unknown_authorization_property_is_rejected() -> None:
    directory, relative = _workspace()
    attempt_id = directory.name
    root = relative.rsplit("/", 1)[0]
    core.ROOTS["capability"] = root
    try:
        value = _capability_value(attempt_id, f"{root}/{attempt_id}", now=datetime.now(timezone(timedelta(hours=8))))
        value["extra"] = True
        path, checksum = _write_authorization(directory, value)
        with pytest.raises(AuthorizationError, match="frame_authorization_schema_invalid"):
            load_authorization(path, checksum)
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)


def test_authorization_checksum_must_use_the_exact_sidecar_path() -> None:
    directory, relative = _workspace()
    attempt_id = directory.name
    root = relative.rsplit("/", 1)[0]
    core.ROOTS["capability"] = root
    try:
        value = _capability_value(attempt_id, f"{root}/{attempt_id}", now=datetime.now(timezone(timedelta(hours=8))))
        path, checksum = _write_authorization(directory, value)
        alternate = directory / "alternate.sha256"
        alternate.write_bytes(checksum.read_bytes())
        with pytest.raises(AuthorizationError, match="authorization_hash_path_invalid"):
            load_authorization(path, alternate)
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)


def test_missing_credential_seals_offline_verifiable_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    attempt_id = directory.name
    # Authorization lives beside, not inside, the create-only attempt child.
    auth_dir = directory.parent / f"auth-{attempt_id}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        now = datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0)
        value = _capability_value(attempt_id, f"{root}/{attempt_id}", now=now)
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        from qualitative_v2_m4_segmented_runtime import RuntimeHooks, _runtime_hooks

        def forbidden(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("transport must not be built without a credential")

        hooks = RuntimeHooks(forbidden, lambda: now, lambda: 1.0, lambda _phase, _ordinal: None)  # type: ignore[arg-type]
        with _runtime_hooks(hooks):
            result = execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        assert result.disposition == "NO_QUALIFIED_FRAME_SOURCE"
        assert not result.complete and not result.overall_pass and not result.capture_input_eligible
        assert verify_segmented_rest_attempt(result.attempt_dir).manifest_sha256 == result.manifest_sha256
        with pytest.raises(VerificationError, match="capture_input_not_eligible"):
            require_capture_input_eligible(result.attempt_dir)
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


@pytest.mark.parametrize("token", ["a" * 63, "A" * 64, "g" * 64])
def test_invalid_credentials_seal_credential_invalid_without_transport(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    auth_dir = directory.parent / f"auth-{directory.name}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        import qualitative_v2_m4_segmented_runtime as runtime

        now = datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0)
        value = _capability_value(directory.name, f"{root}/{directory.name}", now=now)
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.setenv("TUSHARE_TOKEN", token)

        def forbidden(
            call: SegmentedRestCall, token: str, byte_limit: int, timeout: float
        ) -> runtime.TransportResponse:
            del call, token, byte_limit, timeout
            raise AssertionError("invalid credentials must stop before transport")

        hooks = runtime.RuntimeHooks(
            forbidden,
            lambda: now,
            lambda: 1.0,
            lambda _phase, _ordinal: None,
        )
        with runtime._runtime_hooks(hooks):
            result = execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        stop = strict_json_loads((result.attempt_dir / "stop-record.json").read_bytes(), numbers=False)
        assert isinstance(stop, dict) and stop["stop_code"] == "credential_invalid"
        assert result.receipts == ()
        assert verify_segmented_rest_attempt(result.attempt_dir).manifest_sha256 == result.manifest_sha256
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


def test_legacy_guard_rejects_v131_path() -> None:
    with pytest.raises(AuditBlockedError, match="require_capture_input_eligible"):
        reject_v131_segmented_attempt(core.PROJECT_ROOT / "artifacts/milestone-004/v1.3.1/captures/example")


def test_public_types_are_immutable() -> None:
    call = frame_call_matrix("2026-07-15")[0]
    with pytest.raises((AttributeError, TypeError)):
        call.api_name = "daily"  # type: ignore[misc]


def test_date_authorization_type_is_public() -> None:
    assert is_dataclass(DateEvidenceAuthorization)


@pytest.mark.parametrize(
    "raw",
    [b'{"x":1,"x":2}', b'{"x":-0}', b'{"x":-0e1}', b'{"x":-0.0e-2}', b'{"x":-0E+10}', b'{"x":NaN}'],
)
def test_strict_json_rejects_ambiguous_numbers_and_keys(raw: bytes) -> None:
    with pytest.raises(VerificationError, match="json_invalid"):
        strict_json_loads(raw)


@pytest.mark.parametrize("raw", [b'{"x":1.0}', b'{"x":-0}', b'{"x":-0.0}', b'{"x":1e0}'])
def test_structural_json_rejects_numeric_type_substitution(raw: bytes) -> None:
    with pytest.raises(VerificationError, match="json_invalid"):
        strict_json_loads(raw, numbers=False)


def test_exact_numeric_lexeme_row_golden() -> None:
    parsed = strict_json_loads(b'["920001.BJ","20260715",123.4500]')
    assert isinstance(parsed, list)
    assert row_sha256(("ts_code", "trade_date", "total_mv"), tuple(parsed)) == (
        "72242aacb24b33de57135e82bae4ab1917a173d67c4ad521dd5da394e5f8023e"
    )


def test_provider_count_must_match_nonempty_rows() -> None:
    call = frame_call_matrix("2026-07-15")[0]
    raw = canonical_json_bytes(
        {
            "code": 0,
            "data": {
                "count": 0,
                "fields": list(call.fields),
                "items": [["801010.SI", "农林牧渔", "L1", "SW2021"]],
            },
            "msg": "",
        }
    )

    with pytest.raises(VerificationError, match="provider_count_invalid"):
        parse_provider_response(raw, call)


def test_token_echo_is_terminal_no_blob_and_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    attempt_id = directory.name
    auth_dir = directory.parent / f"auth-{attempt_id}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        now = datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0)
        value = _capability_value(attempt_id, f"{root}/{attempt_id}", now=now)
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        token = "a" * 64
        monkeypatch.setenv("TUSHARE_TOKEN", token)
        from qualitative_v2_m4_segmented_runtime import RuntimeHooks, TransportResponse, _runtime_hooks

        def echo(*_args: object, **_kwargs: object) -> TransportResponse:
            return TransportResponse(
                "https://api.tushare.pro",
                "https://api.tushare.pro",
                200,
                "application/json",
                json.dumps({"token": token}).encode(),
            )

        hooks = RuntimeHooks(echo, lambda: now, lambda: 1.0, lambda _phase, _ordinal: None)
        with _runtime_hooks(hooks):
            result = execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        assert result.receipts[0].state == "terminal_failure_no_blob"
        assert result.receipts[0].error_code == "token_echo"
        assert result.receipts[0].blob_relative_path is None
        assert [item.ordinal for item in result.receipts] == [1]
        assert not any((result.attempt_dir / "blobs").glob("*.bin"))
        assert token.encode() not in (result.attempt_dir / "attempt-manifest.json").read_bytes()
        receipt_path = result.attempt_dir / result.receipts[0].receipt_relative_path
        receipt_stat = receipt_path.stat(follow_symlinks=False)
        import os

        os.utime(
            receipt_path,
            ns=(receipt_stat.st_atime_ns, receipt_stat.st_mtime_ns + 1_000_000_000),
            follow_symlinks=False,
        )
        with pytest.raises(VerificationError, match="receipt_seal_timestamp_drift"):
            verify_segmented_rest_attempt(result.attempt_dir)
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


@pytest.mark.parametrize(
    ("raw", "expected_error", "expected_provider_code"),
    [
        (b'{"code":0,"msg":"warning"}', "provider_error", 0),
        (b'{"code":-1,"msg":"failed"}', "provider_error", -1),
        (b'{"msg":"missing code"}', "schema_error", None),
        (b'{"code":999999999999999999999999999999,"msg":"huge"}', "schema_error", None),
    ],
)
def test_provider_failures_seal_with_reproducible_classification(
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
    expected_error: str,
    expected_provider_code: int | None,
) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    attempt_id = directory.name
    auth_dir = directory.parent / f"auth-{attempt_id}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        now = datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0)
        value = _capability_value(attempt_id, f"{root}/{attempt_id}", now=now)
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.setenv("TUSHARE_TOKEN", "a" * 64)
        from qualitative_v2_m4_segmented_runtime import RuntimeHooks, TransportResponse, _runtime_hooks

        def response(*_args: object, **_kwargs: object) -> TransportResponse:
            return TransportResponse("https://api.tushare.pro", "https://api.tushare.pro", 200, "application/json", raw)

        hooks = RuntimeHooks(response, lambda: now, lambda: 1.0, lambda _phase, _ordinal: None)
        with _runtime_hooks(hooks):
            result = execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        receipt = result.receipts[0]
        assert receipt.error_code == expected_error
        stored = json.loads((result.attempt_dir / receipt.receipt_relative_path).read_bytes())
        assert stored["provider_code"] == expected_provider_code
        assert verify_segmented_rest_attempt(result.attempt_dir).manifest_sha256 == result.manifest_sha256
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


def _valid_frame_reproduction() -> tuple[FrameAuthorization, dict[int, ParsedResponse]]:
    selected = date(2026, 7, 15)
    calls = frame_call_matrix(selected)
    now = datetime(2026, 7, 17, tzinfo=timezone(timedelta(hours=8)))
    authorization = FrameAuthorization(
        "auth-synthetic-gates",
        "synthetic-gates",
        "capability",
        core.PROTOCOL_RELATIVE_PATH,
        core.protocol_sha256(),
        core.SUPERSEDES_SHA256,
        "artifacts/milestone-004/v1.3.1/capability-probes/synthetic-gates",
        selected,
        now,
        now + timedelta(minutes=10),
        calls,
        None,
        None,
        "1" * 64,
        "reviews/synthetic/auth.json",
        b"{}\n",
    )
    codes: list[str] = []
    for index in range(2015):
        prefix = ("600", "601", "603", "605")[index % 4]
        codes.append(f"{prefix}{index // 4:03d}.SH")
    for index in range(2015):
        prefix = ("000", "001", "002", "003")[index % 4]
        codes.append(f"{prefix}{index // 4:03d}.SZ")
    parsed: dict[int, ParsedResponse] = {
        1: ParsedResponse(
            calls[0].fields,
            tuple(
                (industry_code, industry_name, "L1", "SW2021")
                for industry_code, industry_name in SW2021_INDUSTRIES.items()
            ),
        )
    }
    for index, call in enumerate(calls[1:32]):
        industry_code = dict(call.params)["l1_code"]
        industry_name = SW2021_INDUSTRIES[industry_code]
        industry_codes = codes[index * 130 : (index + 1) * 130]
        parsed[call.ordinal] = ParsedResponse(
            call.fields,
            tuple(
                (industry_code, industry_name, None, None, None, None, code, f"公司{code}", "20200101", None, "Y")
                for code in industry_codes
            ),
        )
    for ordinal, suffix, exchange in ((33, ".SH", "SSE"), (34, ".SZ", "SZSE")):
        call = calls[ordinal - 1]
        parsed[ordinal] = ParsedResponse(
            call.fields,
            tuple(
                (code, f"公司{code}", "主板", exchange, "CNY", "L", "20200101")
                for code in codes
                if code.endswith(suffix)
            ),
        )
    parsed[35] = ParsedResponse(calls[34].fields, ())
    parsed[36] = ParsedResponse(
        calls[35].fields,
        tuple((code, "20260715", core.JsonNumber(str(index + 1))) for index, code in enumerate(codes)),
    )
    return authorization, parsed


def _provider_bytes(response: ParsedResponse) -> bytes:
    def primitive(cell: object) -> object:
        return int(cell.lexeme) if isinstance(cell, core.JsonNumber) else cell

    return json.dumps(
        {
            "code": 0,
            "msg": "",
            "data": {
                "fields": list(response.fields),
                "items": [[primitive(cell) for cell in row] for row in response.rows],
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def test_all_frame_and_sampling_cell_gates_pass_on_exhaustive_synthetic_matrix() -> None:
    authorization, parsed = _valid_frame_reproduction()
    reproduction = reproduce_frame(authorization, parsed, complete=True, authorization_window=True)
    assert reproduction.gates == {
        "authorization_window": True,
        "calendar": None,
        "classification": True,
        "daily": True,
        "date_derivation": None,
        "frame": True,
        "membership": True,
        "overall_pass": True,
        "sampling_cells": True,
        "stock_basic": True,
        "stock_st": True,
    }
    assert reproduction.ledger is not None and reproduction.ledger["entries"] == []


def test_complete_is_separate_from_data_gate_pass() -> None:
    authorization, parsed = _valid_frame_reproduction()
    parsed[2] = ParsedResponse(parsed[2].fields, ())
    reproduction = reproduce_frame(authorization, parsed, complete=True, authorization_window=True)
    assert reproduction.gates["membership"] is False
    assert reproduction.gates["overall_pass"] is False


def test_malformed_stock_cells_fail_gate_without_aborting_sealing_logic() -> None:
    authorization, parsed = _valid_frame_reproduction()
    first = list(parsed[33].rows[0])
    first[2] = {"nested": "market"}
    parsed[33] = ParsedResponse(parsed[33].fields, (tuple(first), *parsed[33].rows[1:]))
    reproduction = reproduce_frame(authorization, parsed, complete=True, authorization_window=True)
    assert reproduction.gates["stock_basic"] is False
    assert reproduction.gates["overall_pass"] is False


def _valid_date_reproduction() -> tuple[
    DateEvidenceAuthorization, dict[int, ParsedResponse], list[CallReceipt], datetime
]:
    proposed = date(2026, 7, 15)
    calls = date_evidence_call_matrix(proposed)
    zone = timezone(timedelta(hours=8))
    start = proposed - timedelta(days=62)
    end = proposed + timedelta(days=62)
    authorization = DateEvidenceAuthorization(
        "auth-date-synthetic",
        "date-synthetic",
        "date_selection_evidence",
        core.PROTOCOL_RELATIVE_PATH,
        core.protocol_sha256(),
        core.SUPERSEDES_SHA256,
        "artifacts/milestone-004/v1.3.1/date-selection-evidence/date-synthetic",
        proposed,
        start,
        end,
        datetime(2026, 7, 15, 18, tzinfo=zone),
        datetime(2026, 7, 15, 23, 59, 59, tzinfo=zone),
        calls,
        {"attempt_id": "cap-synthetic", "relative_path": "artifacts/cap/attempt-manifest.json", "sha256": "1" * 64},
        "2" * 64,
        "reviews/synthetic/date-auth.json",
        b"{}\n",
    )
    parsed: dict[int, ParsedResponse] = {}
    for call, exchange in zip(calls, ("SSE", "SZSE"), strict=True):
        rows = []
        for offset in range(125):
            selected = start + timedelta(days=offset)
            rows.append(
                (
                    exchange,
                    selected.strftime("%Y%m%d"),
                    core.JsonNumber("1" if selected.weekday() < 5 else "0"),
                    None,
                )
            )
        parsed[call.ordinal] = ParsedResponse(call.fields, tuple(rows))
    captured = datetime(2026, 7, 15, 19, tzinfo=zone)
    receipts = [
        CallReceipt(
            call.ordinal,
            call.call_id,
            "success_blob",
            captured,
            "success",
            None,
            100,
            f"blobs/{call.ordinal:04d}-{'3' * 64}.bin",
            "3" * 64,
            f"receipts/{call.ordinal:04d}.json",
            "4" * 64,
        )
        for call in calls
    ]
    return authorization, parsed, receipts, datetime(2026, 7, 15, 20, tzinfo=zone)


def test_date_evidence_exists_only_when_all_date_gates_pass() -> None:
    authorization, parsed, receipts, sealed_at = _valid_date_reproduction()
    reproduction = reproduce_date(
        authorization,
        parsed,
        receipts,
        complete=True,
        authorization_window=True,
        sealed_at=sealed_at,
    )
    assert reproduction.gates["calendar"] is True
    assert reproduction.gates["date_derivation"] is True
    assert reproduction.gates["overall_pass"] is True
    assert reproduction.evidence is not None
    assert reproduction.evidence["sampling_date"] == "2026-07-15"
    assert reproduction.evidence["next_common_open_date"] == "2026-07-16"

    parsed[1] = ParsedResponse(parsed[1].fields, parsed[1].rows[:-1])
    failed = reproduce_date(
        authorization,
        parsed,
        receipts,
        complete=True,
        authorization_window=True,
        sealed_at=sealed_at,
    )
    assert failed.gates["calendar"] is False
    assert failed.gates["date_derivation"] is False
    assert failed.evidence is None


def test_incomplete_date_attempt_keeps_date_gates_null_and_no_evidence() -> None:
    authorization, parsed, receipts, sealed_at = _valid_date_reproduction()
    reproduction = reproduce_date(
        authorization,
        parsed,
        receipts[:1],
        complete=False,
        authorization_window=True,
        sealed_at=sealed_at,
    )
    assert reproduction.gates["calendar"] is None
    assert reproduction.gates["date_derivation"] is None
    assert reproduction.evidence is None


@pytest.mark.parametrize(
    "scenario",
    ["missing_day", "duplicate_day", "exchange", "is_open", "no_next_open", "proposed_closed"],
)
def test_calendar_and_date_derivation_negative_matrix(scenario: str) -> None:
    authorization, parsed, receipts, sealed_at = _valid_date_reproduction()
    rows = list(parsed[1].rows)
    if scenario == "missing_day":
        rows.pop()
    elif scenario == "duplicate_day":
        rows.append(rows[0])
    elif scenario == "exchange":
        row = list(rows[0])
        row[0] = "SZSE"
        rows[0] = tuple(row)
    elif scenario == "is_open":
        row = list(rows[0])
        row[2] = core.JsonNumber("2")
        rows[0] = tuple(row)
    else:
        for index, cells in enumerate(rows):
            selected = datetime.strptime(str(cells[1]), "%Y%m%d").date()
            if (scenario == "no_next_open" and selected > authorization.proposed_sampling_date) or (
                scenario == "proposed_closed" and selected == authorization.proposed_sampling_date
            ):
                row = list(cells)
                row[2] = core.JsonNumber("0")
                rows[index] = tuple(row)
        if scenario == "proposed_closed":
            other = list(parsed[2].rows)
            for index, cells in enumerate(other):
                if str(cells[1]) == authorization.proposed_sampling_date.strftime("%Y%m%d"):
                    row = list(cells)
                    row[2] = core.JsonNumber("0")
                    other[index] = tuple(row)
            parsed[2] = ParsedResponse(parsed[2].fields, tuple(other))
    parsed[1] = ParsedResponse(parsed[1].fields, tuple(rows))
    reproduction = reproduce_date(
        authorization,
        parsed,
        receipts,
        complete=True,
        authorization_window=True,
        sealed_at=sealed_at,
    )
    assert reproduction.gates["overall_pass"] is False
    assert reproduction.evidence is None


def test_production_supervisor_path_seals_missing_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    attempt_id = directory.name
    auth_dir = directory.parent / f"auth-{attempt_id}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        now = datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0)
        value = _capability_value(attempt_id, f"{root}/{attempt_id}", now=now)
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        result = execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        assert result.disposition == "NO_QUALIFIED_FRAME_SOURCE"
        assert result.receipts == ()
        assert verify_segmented_rest_attempt(result.attempt_dir).manifest_sha256 == result.manifest_sha256
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


def test_symlinked_project_path_ancestry_is_rejected(tmp_path: Path) -> None:
    directory, _relative = _workspace()
    link = directory / "escape"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(VerificationError, match="symlinked_path_ancestry"):
            core.require_safe_project_path(link / "missing", allow_missing_leaf=True)
    finally:
        link.unlink(missing_ok=True)
        _remove(directory)


def test_positive_supervisor_commit_is_required_after_controls_are_gone(sealed_failure_attempt) -> None:  # type: ignore[no-untyped-def]
    result, _authorization, _checksum = sealed_failure_attempt
    commit = result.attempt_dir / "supervisor-commit.json"
    result.attempt_dir.chmod(0o755)
    commit.chmod(0o600)
    commit.unlink()
    result.attempt_dir.chmod(0o555)
    with pytest.raises(VerificationError):
        verify_segmented_rest_attempt(result.attempt_dir)


def test_closed_set_rejects_extra_empty_directory(sealed_failure_attempt) -> None:  # type: ignore[no-untyped-def]
    result, _authorization, _checksum = sealed_failure_attempt
    result.attempt_dir.chmod(0o755)
    extra = result.attempt_dir / "empty-extra"
    extra.mkdir(mode=0o555)
    result.attempt_dir.chmod(0o555)
    with pytest.raises(VerificationError, match="artifact_directory_set_invalid"):
        verify_segmented_rest_attempt(result.attempt_dir)


@pytest.mark.parametrize("external_kind", ["lock", "phase-journal", "publication-tmp"])
def test_sealed_attempt_rejects_stale_external_orphan_state(sealed_failure_attempt, external_kind: str) -> None:  # type: ignore[no-untyped-def]
    result, _authorization, _checksum = sealed_failure_attempt
    if external_kind == "lock":
        external = result.attempt_dir.parent / f".{result.attempt_id}.lock"
        external.write_bytes(b"")
    elif external_kind == "phase-journal":
        external = result.attempt_dir.parent / f".{result.attempt_id}.phase-journal.json"
        external.write_bytes(b"{}\n")
    else:
        external = result.attempt_dir.parent / f".{result.attempt_id}.publication-tmp"
        external.mkdir()
    try:
        with pytest.raises(VerificationError, match="stale_external_attempt_state"):
            verify_segmented_rest_attempt(result.attempt_dir)
    finally:
        external.rmdir() if external.is_dir() else external.unlink(missing_ok=True)


def test_closed_set_rejects_hardlinks_and_permission_drift(sealed_failure_attempt) -> None:  # type: ignore[no-untyped-def]
    result, _authorization, _checksum = sealed_failure_attempt
    result.attempt_dir.chmod(0o755)
    stop = result.attempt_dir / "stop-record.json"
    linked = result.attempt_dir / "linked-stop.json"
    linked.hardlink_to(stop)
    result.attempt_dir.chmod(0o555)
    with pytest.raises(VerificationError):
        verify_segmented_rest_attempt(result.attempt_dir)
    result.attempt_dir.chmod(0o755)
    linked.unlink()
    stop.chmod(0o644)
    result.attempt_dir.chmod(0o555)
    with pytest.raises(VerificationError):
        verify_segmented_rest_attempt(result.attempt_dir)


@pytest.mark.parametrize("tamper_kind", ["path_traversal", "mixed_attempt"])
def test_manifest_path_traversal_and_mixed_attempt_identity_fail_closed(
    sealed_failure_attempt, tamper_kind: str
) -> None:  # type: ignore[no-untyped-def]
    result, _authorization, _checksum = sealed_failure_attempt
    manifest_path = result.attempt_dir / "attempt-manifest.json"
    manifest = strict_json_loads(manifest_path.read_bytes(), numbers=False)
    assert isinstance(manifest, dict)
    result.attempt_dir.chmod(0o755)
    manifest_path.chmod(0o600)
    if tamper_kind == "path_traversal":
        manifest["authorization_relative_path"] = "../mixed/authorization.json"
        expected = "authorization_relative_path_invalid"
    else:
        stop_path = result.attempt_dir / "stop-record.json"
        stop = strict_json_loads(stop_path.read_bytes(), numbers=False)
        assert isinstance(stop, dict)
        stop["attempt_id"] = "different-attempt"
        stop_raw = canonical_json_bytes(stop)
        stop_path.chmod(0o600)
        stop_path.write_bytes(stop_raw)
        stop_path.chmod(0o444)
        replacement_ref = {
            "byte_count": len(stop_raw),
            "kind": "stop_record",
            "relative_path": "stop-record.json",
            "sha256": core.sha256_bytes(stop_raw),
        }
        manifest["stop_record_ref"] = replacement_ref
        artifact_files = manifest["artifact_files"]
        assert isinstance(artifact_files, list)
        manifest["artifact_files"] = [
            replacement_ref if isinstance(item, dict) and item.get("relative_path") == "stop-record.json" else item
            for item in artifact_files
        ]
        expected = "stop_identity_invalid"
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    manifest_path.chmod(0o444)
    result.attempt_dir.chmod(0o555)
    with pytest.raises(VerificationError, match=expected):
        verify_segmented_rest_attempt(result.attempt_dir)


def test_external_authorization_and_generator_drift_fail_closed(
    sealed_failure_attempt, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    result, authorization, _checksum = sealed_failure_attempt
    original = authorization.read_bytes()
    authorization.write_bytes(original + b" ")
    with pytest.raises((AuthorizationError, VerificationError)):
        verify_segmented_rest_attempt(result.attempt_dir)
    authorization.write_bytes(original)

    import qualitative_v2_m4_segmented_verify as verifier

    monkeypatch.setattr(verifier, "generator_references", lambda: [])
    with pytest.raises(VerificationError, match="provenance_drift"):
        verify_segmented_rest_attempt(result.attempt_dir)


def test_date_authorization_rejects_a_nonpass_capability_reference(
    sealed_failure_attempt,
) -> None:  # type: ignore[no-untyped-def]
    result, _authorization, _checksum = sealed_failure_attempt
    directory, relative = _workspace()
    attempt_id = directory.name
    root = relative.rsplit("/", 1)[0]
    core.ROOTS["date_selection_evidence"] = root
    try:
        proposed = date(2026, 7, 15)
        zone = timezone(timedelta(hours=8))
        value = {
            "attempt_byte_limit": core.DATE_ATTEMPT_LIMIT,
            "attempt_id": attempt_id,
            "attempt_output_dir": f"{root}/{attempt_id}",
            "authorization_id": f"auth-{attempt_id}",
            "calendar_end": (proposed + timedelta(days=62)).isoformat(),
            "calendar_start": (proposed - timedelta(days=62)).isoformat(),
            "calls": [call.authorization_value() for call in date_evidence_call_matrix(proposed)],
            "capability_manifest_ref": {
                "attempt_id": result.attempt_id,
                "relative_path": result.manifest_path.relative_to(core.PROJECT_ROOT).as_posix(),
                "sha256": result.manifest_sha256,
            },
            "credential_env_var": "TUSHARE_TOKEN",
            "max_attempts_per_ordinal": 1,
            "not_after": datetime(2026, 7, 15, 23, 59, 59, tzinfo=zone).isoformat(timespec="seconds"),
            "not_before": datetime(2026, 7, 15, 18, tzinfo=zone).isoformat(timespec="seconds"),
            "origin": core.ORIGIN,
            "proposed_sampling_date": proposed.isoformat(),
            "protocol_path": core.PROTOCOL_RELATIVE_PATH,
            "protocol_sha256": core.protocol_sha256(),
            "purpose": "date_selection_evidence",
            "response_byte_limit": core.DATE_RESPONSE_LIMIT,
            "schema_version": core.DATE_AUTH_SCHEMA,
            "supersedes_sha256": core.SUPERSEDES_SHA256,
        }
        path, checksum = _write_authorization(directory, value)
        with pytest.raises(VerificationError, match="capability_reference_invalid"):
            load_authorization(path, checksum)
    finally:
        core.ROOTS["date_selection_evidence"] = "artifacts/milestone-004/v1.3.1/date-selection-evidence"
        _remove(directory)


def test_full_synthetic_reference_chain_commits_capture_eligibility(monkeypatch: pytest.MonkeyPatch) -> None:
    base, relative = _workspace()
    previous_roots = dict(core.ROOTS)
    core.ROOTS.update(
        {
            "capability": f"{relative}/capability",
            "date_selection_evidence": f"{relative}/date-evidence",
            "capture": f"{relative}/captures",
        }
    )
    try:
        from qualitative_v2_m4_segmented_runtime import RuntimeHooks, TransportResponse, _runtime_hooks

        _frame_authorization, frame_responses = _valid_frame_reproduction()
        date_authorization, calendar_responses, _receipts, _sealed = _valid_date_reproduction()

        current_responses = frame_responses

        def transport(call: SegmentedRestCall, token: str, byte_limit: int, timeout: float) -> TransportResponse:
            del token, byte_limit, timeout
            return TransportResponse(
                core.ORIGIN,
                core.ORIGIN,
                200,
                "application/json",
                _provider_bytes(current_responses[call.ordinal]),
            )

        zone = timezone(timedelta(hours=8))
        now = datetime(2026, 7, 15, 19, tzinfo=zone)
        hooks = RuntimeHooks(transport, lambda: now, lambda: 1.0, lambda _phase, _ordinal: None)
        monkeypatch.setenv("TUSHARE_TOKEN", "a" * 64)

        cap_id = "golden-chain-capability"
        cap_auth_dir = base / "auth-capability"
        cap_auth_dir.mkdir()
        cap_value = _capability_value(cap_id, f"{core.ROOTS['capability']}/{cap_id}", now=now)
        cap_path, cap_checksum = _write_authorization(cap_auth_dir, cap_value)
        with _runtime_hooks(hooks):
            capability = execute_segmented_rest_attempt(
                authorization_path=cap_path, authorization_hash_path=cap_checksum
            )
        assert capability.overall_pass and not capability.capture_input_eligible

        date_id = "golden-chain-date"
        date_auth_dir = base / "auth-date"
        date_auth_dir.mkdir()
        proposed = date(2026, 7, 15)
        date_value = {
            "attempt_byte_limit": core.DATE_ATTEMPT_LIMIT,
            "attempt_id": date_id,
            "attempt_output_dir": f"{core.ROOTS['date_selection_evidence']}/{date_id}",
            "authorization_id": f"auth-{date_id}",
            "calendar_end": (proposed + timedelta(days=62)).isoformat(),
            "calendar_start": (proposed - timedelta(days=62)).isoformat(),
            "calls": [call.authorization_value() for call in date_authorization.calls],
            "capability_manifest_ref": {
                "attempt_id": capability.attempt_id,
                "relative_path": capability.manifest_path.relative_to(core.PROJECT_ROOT).as_posix(),
                "sha256": capability.manifest_sha256,
            },
            "credential_env_var": "TUSHARE_TOKEN",
            "max_attempts_per_ordinal": 1,
            "not_after": datetime(2026, 7, 15, 23, 59, 59, tzinfo=zone).isoformat(timespec="seconds"),
            "not_before": datetime(2026, 7, 15, 18, tzinfo=zone).isoformat(timespec="seconds"),
            "origin": core.ORIGIN,
            "proposed_sampling_date": proposed.isoformat(),
            "protocol_path": core.PROTOCOL_RELATIVE_PATH,
            "protocol_sha256": core.protocol_sha256(),
            "purpose": "date_selection_evidence",
            "response_byte_limit": core.DATE_RESPONSE_LIMIT,
            "schema_version": core.DATE_AUTH_SCHEMA,
            "supersedes_sha256": core.SUPERSEDES_SHA256,
        }
        date_path, date_checksum = _write_authorization(date_auth_dir, date_value)
        current_responses = calendar_responses
        with _runtime_hooks(hooks):
            date_result = execute_segmented_rest_attempt(
                authorization_path=date_path, authorization_hash_path=date_checksum
            )
        assert date_result.overall_pass and not date_result.capture_input_eligible

        evidence = date_result.attempt_dir / "date-selection-evidence.json"
        capture_id = "golden-chain-capture"
        capture_auth_dir = base / "auth-capture"
        capture_auth_dir.mkdir()
        capture_value = _capability_value(capture_id, f"{core.ROOTS['capture']}/{capture_id}", now=now)
        capture_value.update(
            {
                "authorization_id": f"auth-{capture_id}",
                "capability_manifest_ref": {
                    "attempt_id": capability.attempt_id,
                    "relative_path": capability.manifest_path.relative_to(core.PROJECT_ROOT).as_posix(),
                    "sha256": capability.manifest_sha256,
                },
                "date_evidence_ref": {
                    "attempt_id": date_result.attempt_id,
                    "authorization_id": date_result.authorization_id,
                    "relative_path": evidence.relative_to(core.PROJECT_ROOT).as_posix(),
                    "sha256": core.sha256_bytes(evidence.read_bytes()),
                },
                "mode": "capture",
                "not_after": datetime(2026, 7, 15, 23, tzinfo=zone).isoformat(timespec="seconds"),
                "not_before": now.isoformat(timespec="seconds"),
                "trade_date_kind": "sampling_date",
            }
        )
        capture_path, capture_checksum = _write_authorization(capture_auth_dir, capture_value)
        current_responses = frame_responses
        with _runtime_hooks(hooks):
            capture = execute_segmented_rest_attempt(
                authorization_path=capture_path, authorization_hash_path=capture_checksum
            )
        assert capture.overall_pass and capture.capture_input_eligible
        assert require_capture_input_eligible(capture.attempt_dir).manifest_sha256 == capture.manifest_sha256
        import os

        tamper_targets = [
            capture.manifest_path,
            capture.attempt_dir / capture.receipts[0].receipt_relative_path,
            capture.attempt_dir / str(capture.receipts[0].blob_relative_path),
            capture.attempt_dir / "exclusion-ledger.json",
            evidence,
        ]
        for target in tamper_targets:
            original = target.read_bytes()
            metadata = target.stat(follow_symlinks=False)
            target.chmod(0o600)
            target.write_bytes(original + b" ")
            with pytest.raises((AuthorizationError, VerificationError)):
                require_capture_input_eligible(capture.attempt_dir)
            target.write_bytes(original)
            os.utime(
                target,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns),
                follow_symlinks=False,
            )
            target.chmod(0o444)
        assert require_capture_input_eligible(capture.attempt_dir).manifest_sha256 == capture.manifest_sha256
        capture.attempt_dir.chmod(0o755)
        commit = capture.attempt_dir / "supervisor-commit.json"
        commit.chmod(0o600)
        commit.unlink()
        capture.attempt_dir.chmod(0o555)
        with pytest.raises(VerificationError):
            require_capture_input_eligible(capture.attempt_dir)
    finally:
        core.ROOTS.clear()
        core.ROOTS.update(previous_roots)
        _remove(base)


@pytest.mark.parametrize(
    "blocked_phase",
    [
        "before_transport",
        "transport_started",
        "blob_sealed",
        "receipt_sealed",
        "gates_complete",
        "manifest_candidate",
        *DETAILED_RUNTIME_PHASES,
    ],
)
def test_real_supervisor_deadline_recovers_each_publication_phase(
    monkeypatch: pytest.MonkeyPatch, blocked_phase: str
) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    auth_dir = directory.parent / f"auth-{directory.name}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        import os
        import time

        from qualitative_v2_m4_segmented_runtime import RuntimeHooks, TransportResponse, _runtime_hooks

        zone = timezone(timedelta(hours=8))

        def wall_clock() -> datetime:
            return datetime.now(zone).replace(microsecond=0)

        started = wall_clock()
        value = _capability_value(directory.name, f"{root}/{directory.name}", now=started)
        value["not_after"] = (started + timedelta(seconds=1)).isoformat(timespec="seconds")
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.setenv("TUSHARE_TOKEN", "a" * 64)

        def transport(call: SegmentedRestCall, token: str, byte_limit: int, timeout: float) -> TransportResponse:
            del token, byte_limit, timeout
            if blocked_phase in DETAILED_RUNTIME_PHASES[:6]:
                phase(blocked_phase, call.ordinal)
            success = json.dumps(
                {"code": 0, "msg": "", "data": {"fields": list(call.fields), "items": []}},
                separators=(",", ":"),
            ).encode()
            raw = success if blocked_phase in {"receipt_sealed", "receipt_fsync"} else b"not-json"
            return TransportResponse(core.ORIGIN, core.ORIGIN, 200, "application/json", raw)

        parent_pid = os.getpid()

        def phase(name: str, _ordinal: int) -> None:
            if os.getpid() != parent_pid and name == blocked_phase:
                time.sleep(20)

        hooks = RuntimeHooks(transport, wall_clock, time.monotonic, phase)
        with _runtime_hooks(hooks, force_worker=True):
            result = execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        assert not result.overall_pass
        assert (result.attempt_dir / "supervisor-commit.json").is_file()
        assert verify_segmented_rest_attempt(result.attempt_dir).manifest_sha256 == result.manifest_sha256
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


@pytest.mark.parametrize(
    ("signal_name", "expected_exception"),
    [("SIGINT", KeyboardInterrupt), ("SIGTERM", SystemExit)],
)
def test_real_supervisor_seals_then_reraises_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    signal_name: str,
    expected_exception: type[BaseException],
) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    auth_dir = directory.parent / f"auth-{directory.name}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        import os
        import signal
        import time

        from qualitative_v2_m4_segmented_runtime import RuntimeHooks, TransportResponse, _runtime_hooks

        zone = timezone(timedelta(hours=8))

        def wall_clock() -> datetime:
            return datetime.now(zone).replace(microsecond=0)

        started = wall_clock()
        value = _capability_value(directory.name, f"{root}/{directory.name}", now=started)
        value["not_after"] = (started + timedelta(seconds=20)).isoformat(timespec="seconds")
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.setenv("TUSHARE_TOKEN", "a" * 64)

        def transport(call: SegmentedRestCall, token: str, byte_limit: int, timeout: float) -> TransportResponse:
            del call, token, byte_limit, timeout
            raise AssertionError("transport must remain interrupted")

        def phase(name: str, _ordinal: int) -> None:
            if name == "transport_started":
                os.kill(os.getppid(), getattr(signal, signal_name))
                time.sleep(20)

        hooks = RuntimeHooks(transport, wall_clock, time.monotonic, phase)
        with pytest.raises(expected_exception), _runtime_hooks(hooks, force_worker=True):
            execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        result = verify_segmented_rest_attempt(directory)
        assert result.receipts[0].error_code == "process_control_interrupt_before_blob"
        assert (directory / "supervisor-commit.json").is_file()
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


@pytest.mark.parametrize("cancel_phase", DETAILED_RUNTIME_PHASES)
def test_real_supervisor_cancellation_covers_detailed_runtime_phases(
    monkeypatch: pytest.MonkeyPatch, cancel_phase: str
) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    auth_dir = directory.parent / f"auth-{directory.name}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        import os
        import signal
        import time

        from qualitative_v2_m4_segmented_runtime import RuntimeHooks, TransportResponse, _runtime_hooks

        zone = timezone(timedelta(hours=8))

        def wall_clock() -> datetime:
            return datetime.now(zone).replace(microsecond=0)

        started = wall_clock()
        value = _capability_value(directory.name, f"{root}/{directory.name}", now=started)
        value["not_after"] = (started + timedelta(seconds=20)).isoformat(timespec="seconds")
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.setenv("TUSHARE_TOKEN", "a" * 64)
        parent_pid = os.getpid()
        sent = False

        def phase(name: str, _ordinal: int) -> None:
            nonlocal sent
            if os.getpid() != parent_pid and name == cancel_phase and not sent:
                sent = True
                os.kill(parent_pid, signal.SIGINT)
                time.sleep(20)

        def transport(call: SegmentedRestCall, token: str, byte_limit: int, timeout: float) -> TransportResponse:
            del token, byte_limit, timeout
            if cancel_phase in DETAILED_RUNTIME_PHASES[:6]:
                phase(cancel_phase, call.ordinal)
            success = json.dumps(
                {"code": 0, "msg": "", "data": {"fields": list(call.fields), "items": []}},
                separators=(",", ":"),
            ).encode()
            raw = success if cancel_phase == "receipt_fsync" else b"not-json"
            return TransportResponse(core.ORIGIN, core.ORIGIN, 200, "application/json", raw)

        hooks = RuntimeHooks(transport, wall_clock, time.monotonic, phase)
        with pytest.raises(KeyboardInterrupt), _runtime_hooks(hooks, force_worker=True):
            execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        result = verify_segmented_rest_attempt(directory)
        assert not result.overall_pass
        assert (directory / "supervisor-commit.json").is_file()
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


def test_simultaneous_deadline_and_cancellation_classifies_deadline_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    auth_dir = directory.parent / f"auth-{directory.name}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        import multiprocessing
        import os
        import signal
        import time

        import qualitative_v2_m4_segmented_runtime as runtime

        zone = timezone(timedelta(hours=8))
        before = datetime.now(zone).replace(microsecond=0)
        after = before + timedelta(seconds=10)
        expired = multiprocessing.Value("b", 0)

        def shared_now() -> datetime:
            return after if expired.value else before

        value = _capability_value(directory.name, f"{root}/{directory.name}", now=before)
        value["not_after"] = (before + timedelta(seconds=5)).isoformat(timespec="seconds")
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.setenv("TUSHARE_TOKEN", "a" * 64)
        monkeypatch.setattr(runtime, "_now", shared_now)
        parent_pid = os.getpid()

        def phase(name: str, _ordinal: int) -> None:
            if os.getpid() != parent_pid and name == "transport_started":
                expired.value = 1
                os.kill(parent_pid, signal.SIGINT)
                time.sleep(20)

        def transport(
            call: SegmentedRestCall, token: str, byte_limit: int, timeout: float
        ) -> runtime.TransportResponse:
            del call, token, byte_limit, timeout
            raise AssertionError("cancel must stop transport")

        hooks = runtime.RuntimeHooks(transport, shared_now, time.monotonic, phase)
        with pytest.raises(KeyboardInterrupt), runtime._runtime_hooks(hooks, force_worker=True):
            execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        result = verify_segmented_rest_attempt(directory)
        assert result.receipts[0].error_code == "authorization_window_overrun_before_blob"
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


@pytest.mark.parametrize("late_phase", ["candidate_verification", "manifest_publication"])
def test_deadline_during_late_pass_publication_never_commits_eligible_pass(
    monkeypatch: pytest.MonkeyPatch, late_phase: str
) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    auth_dir = directory.parent / f"auth-{directory.name}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    lock = directory.parent / f".{directory.name}.lock"
    journal = directory.parent / f".{directory.name}.phase-journal.json"
    publication_root = directory.parent / f".{directory.name}.publication-tmp"
    try:
        import multiprocessing
        import os
        import signal
        import time

        import qualitative_v2_m4_segmented_runtime as runtime

        _authorization, responses = _valid_frame_reproduction()
        zone = timezone(timedelta(hours=8))
        before = datetime.now(zone).replace(microsecond=0)
        after = before + timedelta(seconds=60)
        expired = multiprocessing.Value("b", 0)

        def shared_now() -> datetime:
            return after if expired.value else before

        value = _capability_value(directory.name, f"{root}/{directory.name}", now=before)
        value["not_after"] = (before + timedelta(seconds=30)).isoformat(timespec="seconds")
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.setenv("TUSHARE_TOKEN", "a" * 64)
        monkeypatch.setattr(runtime, "_now", shared_now)
        parent_pid = os.getpid()

        def phase(name: str, _ordinal: int) -> None:
            if os.getpid() != parent_pid and name == late_phase:
                expired.value = 1
                os.kill(parent_pid, signal.SIGINT)
                time.sleep(20)

        def transport(
            call: SegmentedRestCall, token: str, byte_limit: int, timeout: float
        ) -> runtime.TransportResponse:
            del token, byte_limit, timeout
            return runtime.TransportResponse(
                core.ORIGIN,
                core.ORIGIN,
                200,
                "application/json",
                _provider_bytes(responses[call.ordinal]),
            )

        hooks = runtime.RuntimeHooks(transport, shared_now, time.monotonic, phase)
        with pytest.raises((KeyboardInterrupt, SecurityError)), runtime._runtime_hooks(hooks, force_worker=True):
            execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)

        commit = directory / "supervisor-commit.json"
        if late_phase == "candidate_verification":
            result = verify_segmented_rest_attempt(directory)
            assert not result.overall_pass
            assert commit.is_file()
        else:
            manifest = strict_json_loads((directory / "attempt-manifest.json").read_bytes(), numbers=False)
            assert isinstance(manifest, dict)
            gates = manifest["gate_results"]
            assert isinstance(gates, dict) and gates["overall_pass"] is True
            assert not commit.exists()
            with pytest.raises(VerificationError):
                verify_segmented_rest_attempt(directory)
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)
        for control in (lock, journal):
            if control.exists():
                control.chmod(0o600)
                control.unlink()
        _remove(publication_root)


@pytest.mark.parametrize("cancel_phase", ["post_candidate_verify", "commit_publication"])
def test_parent_finalize_defers_cancellation_until_after_seal(
    monkeypatch: pytest.MonkeyPatch, cancel_phase: str
) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    auth_dir = directory.parent / f"auth-{directory.name}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        import os
        import signal

        import qualitative_v2_m4_segmented_runtime as runtime

        zone = timezone(timedelta(hours=8))
        now = datetime.now(zone).replace(microsecond=0)
        value = _capability_value(directory.name, f"{root}/{directory.name}", now=now)
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

        def forbidden_transport(
            call: SegmentedRestCall, token: str, byte_limit: int, timeout: float
        ) -> runtime.TransportResponse:
            del call, token, byte_limit, timeout
            raise AssertionError("no transport")

        hooks = runtime.RuntimeHooks(forbidden_transport, lambda: now, lambda: 1.0, lambda _phase, _ordinal: None)
        if cancel_phase == "post_candidate_verify":
            original_verify = runtime.verify_manifest_candidate
            calls = 0

            def interrupting_verify(attempt_dir: Path, raw: bytes):  # type: ignore[no-untyped-def]
                nonlocal calls
                calls += 1
                result = original_verify(attempt_dir, raw)
                if calls == 2:
                    os.kill(os.getpid(), signal.SIGINT)
                return result

            monkeypatch.setattr(runtime, "verify_manifest_candidate", interrupting_verify)
        else:
            original_create = runtime._create_only

            def interrupting_create(target: Path, raw: bytes, **kwargs):  # type: ignore[no-untyped-def]
                result = original_create(target, raw, **kwargs)
                if target.name == "supervisor-commit.json":
                    os.kill(os.getpid(), signal.SIGINT)
                return result

            monkeypatch.setattr(runtime, "_create_only", interrupting_create)
        with pytest.raises(KeyboardInterrupt), runtime._runtime_hooks(hooks):
            execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        result = verify_segmented_rest_attempt(directory)
        assert (directory / "supervisor-commit.json").is_file()
        assert result.disposition == "NO_QUALIFIED_FRAME_SOURCE"
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


@pytest.mark.parametrize(
    ("failure_kind", "expected_error"),
    [
        ("origin", "transport_error"),
        ("redirect", "transport_error"),
        ("size", "response_too_large"),
        ("transport", "transport_error"),
    ],
)
def test_transport_boundary_and_size_failures_seal_without_socket(
    monkeypatch: pytest.MonkeyPatch, failure_kind: str, expected_error: str
) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    auth_dir = directory.parent / f"auth-{directory.name}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        import qualitative_v2_m4_segmented_runtime as runtime

        zone = timezone(timedelta(hours=8))
        now = datetime.now(zone).replace(microsecond=0)
        value = _capability_value(directory.name, f"{root}/{directory.name}", now=now)
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.setenv("TUSHARE_TOKEN", "a" * 64)

        def failing_transport(
            call: SegmentedRestCall, token: str, byte_limit: int, timeout: float
        ) -> runtime.TransportResponse:
            del call, token, timeout
            if failure_kind == "transport":
                raise runtime._TransportFailure("synthetic")
            return runtime.TransportResponse(
                "https://invalid.example" if failure_kind == "origin" else core.ORIGIN,
                core.ORIGIN,
                302 if failure_kind == "redirect" else 200,
                "application/json",
                b"x" * (byte_limit + 1) if failure_kind == "size" else b"{}",
            )

        hooks = runtime.RuntimeHooks(failing_transport, lambda: now, lambda: 1.0, lambda _phase, _ordinal: None)
        with runtime._runtime_hooks(hooks):
            result = execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        assert result.receipts[0].error_code == expected_error
        assert result.receipts[0].blob_relative_path is None
        assert verify_segmented_rest_attempt(directory).manifest_sha256 == result.manifest_sha256
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


def test_cumulative_attempt_size_limit_fails_before_second_blob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    import qualitative_v2_m4_segmented_runtime as runtime

    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    auth_dir = directory.parent / f"auth-{directory.name}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        now = datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0)
        value = _capability_value(directory.name, f"{root}/{directory.name}", now=now)
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.setenv("TUSHARE_TOKEN", "a" * 64)
        calls = frame_call_matrix("2026-07-15")
        raws = {call.ordinal: _provider_bytes(ParsedResponse(call.fields, ())) for call in calls[:2]}
        total_limit = len(raws[1]) + len(raws[2]) - 1
        original_load = runtime.load_authorization

        def lowered_limit(authorization_path: Path, checksum_path: Path):  # type: ignore[no-untyped-def]
            return replace(original_load(authorization_path, checksum_path), attempt_byte_limit=total_limit)

        monkeypatch.setattr(runtime, "load_authorization", lowered_limit)
        transport_calls: list[int] = []

        def transport(
            call: SegmentedRestCall, token: str, byte_limit: int, timeout: float
        ) -> runtime.TransportResponse:
            del token, byte_limit, timeout
            transport_calls.append(call.ordinal)
            return runtime.TransportResponse(core.ORIGIN, core.ORIGIN, 200, "application/json", raws[call.ordinal])

        hooks = runtime.RuntimeHooks(transport, lambda: now, lambda: 1.0, lambda _phase, _ordinal: None)
        with runtime._runtime_hooks(hooks):
            result = execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        assert transport_calls == [1, 2]
        assert [receipt.state for receipt in result.receipts] == [
            "success_blob",
            "terminal_failure_no_blob",
        ]
        assert result.receipts[1].error_code == "attempt_total_too_large"
        assert len(list((result.attempt_dir / "blobs").glob("*.bin"))) == 1
        assert verify_segmented_rest_attempt(result.attempt_dir).manifest_sha256 == result.manifest_sha256
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


def test_attempt_blob_replaced_by_symlink_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import qualitative_v2_m4_segmented_runtime as runtime

    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    auth_dir = directory.parent / f"auth-{directory.name}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        now = datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0)
        value = _capability_value(directory.name, f"{root}/{directory.name}", now=now)
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.setenv("TUSHARE_TOKEN", "a" * 64)

        def malformed(
            call: SegmentedRestCall, token: str, byte_limit: int, timeout: float
        ) -> runtime.TransportResponse:
            del call, token, byte_limit, timeout
            return runtime.TransportResponse(core.ORIGIN, core.ORIGIN, 200, "application/json", b"not-json")

        hooks = runtime.RuntimeHooks(malformed, lambda: now, lambda: 1.0, lambda _phase, _ordinal: None)
        with runtime._runtime_hooks(hooks):
            result = execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        blob_relative = result.receipts[0].blob_relative_path
        assert blob_relative is not None
        blob = result.attempt_dir / blob_relative
        outside = tmp_path / "original-blob.bin"
        outside.write_bytes(blob.read_bytes())
        result.attempt_dir.chmod(0o755)
        blob.parent.chmod(0o755)
        blob.chmod(0o600)
        blob.unlink()
        blob.symlink_to(outside)
        blob.parent.chmod(0o555)
        result.attempt_dir.chmod(0o555)
        with pytest.raises(VerificationError, match="file_missing_or_unsafe"):
            verify_segmented_rest_attempt(result.attempt_dir)
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


def test_default_rest_request_has_exact_method_origin_headers_body_and_no_proxy_or_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualitative_v2_m4_segmented_runtime as runtime

    observed: dict[str, object] = {}

    class Response:
        status = 200

        def getheader(self, name: str) -> str | None:
            return "application/json" if name == "Content-Type" else None

        def read(self, _size: int) -> bytes:
            if observed.get("read"):
                return b""
            observed["read"] = True
            return b'{"code":0,"msg":"","data":{"fields":[],"items":[]}}'

    class Connection:
        def __init__(self, host: str, port: int, *, timeout: float, context: object) -> None:
            observed.update(host=host, port=port, timeout=timeout, context=context)

        def request(self, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> None:
            observed.update(method=method, path=path, body=body, headers=headers)

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            observed["closed"] = True

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:9999")
    monkeypatch.setenv("HTTP_COOKIE", "secret=cookie")
    monkeypatch.setattr(runtime.http.client, "HTTPSConnection", Connection)
    call = frame_call_matrix("2026-07-15")[0]
    token = "a" * 64
    response = runtime._default_transport(call, token, 4096, 3.0)
    assert response.status_code == 200
    assert (observed["host"], observed["port"], observed["method"], observed["path"]) == (
        "api.tushare.pro",
        443,
        "POST",
        "/",
    )
    headers = observed["headers"]
    assert isinstance(headers, dict)
    assert set(headers) == {"Content-Type", "Content-Length", "Connection"}
    encoded_body = observed["body"]
    assert isinstance(encoded_body, bytes)
    assert headers == {
        "Connection": "close",
        "Content-Length": str(len(encoded_body)),
        "Content-Type": "application/json",
    }
    body = json.loads(encoded_body)
    assert body == {
        "api_name": call.api_name,
        "fields": ",".join(call.fields),
        "params": dict(call.params),
        "token": token,
    }


def test_stale_lock_and_create_only_collision_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    auth_dir = directory.parent / f"auth-{directory.name}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        import qualitative_v2_m4_segmented_runtime as runtime

        zone = timezone(timedelta(hours=8))
        now = datetime.now(zone).replace(microsecond=0)
        value = _capability_value(directory.name, f"{root}/{directory.name}", now=now)
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        lock = directory.parent / f".{directory.name}.lock"
        lock.write_bytes(b"")
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        with pytest.raises(SecurityError, match="stale_or_concurrent_lock"):
            execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        lock.unlink()

        publication_root = directory.parent / ".create-only-test"
        publication_root.mkdir()
        directory.mkdir()
        target = directory / "artifact.bin"
        runtime._create_only(target, b"first", temporary_root=publication_root)
        with pytest.raises(SecurityError, match="artifact_already_exists"):
            runtime._create_only(target, b"second", temporary_root=publication_root)
        assert target.read_bytes() == b"first"
        assert target.stat().st_nlink == 1
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)
        _remove(directory.parent / ".create-only-test")


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("classification", "classification"),
        ("classification_name", "classification"),
        ("classification_duplicate", "classification"),
        ("membership_code", "membership"),
        ("membership_l1", "membership"),
        ("membership_inactive", "membership"),
        ("membership_out_date", "membership"),
        ("membership_future", "membership"),
        ("membership_name", "membership"),
        ("membership_duplicate", "membership"),
        ("membership_oversize", "membership"),
        ("bse", "exchange_out_of_scope_bse"),
        ("stock_empty", "stock_basic"),
        ("stock_exchange", "stock_basic"),
        ("st", "st_risk_warning"),
        ("st_date", "stock_st"),
        ("delisting", "delisting_consolidation"),
        ("listing_age", "listing_age_below_36_months"),
        ("daily_coverage", "daily"),
        ("daily_date", "daily"),
        ("daily_value", "daily"),
        ("daily_duplicate", "daily"),
        ("sampling_cells", "sampling_cells"),
    ],
)
def test_section10_negative_gate_and_ledger_scenarios(scenario: str, expected: str) -> None:
    authorization, responses = _valid_frame_reproduction()
    if scenario == "classification":
        responses[1] = ParsedResponse(responses[1].fields, responses[1].rows[:-1])
    elif scenario.startswith("classification_"):
        rows = list(responses[1].rows)
        row = list(rows[0])
        if scenario == "classification_name":
            row[1] = "错误行业"
            rows[0] = tuple(row)
        else:
            rows[1] = rows[0]
        responses[1] = ParsedResponse(responses[1].fields, tuple(rows))
    elif scenario == "membership_oversize":
        oversize_rows = (responses[2].rows * 16)[:2000]
        responses[2] = ParsedResponse(responses[2].fields, oversize_rows)
    elif scenario.startswith("membership_"):
        ordinal = 3 if scenario == "membership_duplicate" else 2
        row = list(responses[ordinal].rows[0])
        if scenario == "membership_code":
            row[6] = "BAD"
        elif scenario == "membership_l1":
            row[0] = "999999.SI"
        elif scenario == "membership_inactive":
            row[10] = "N"
        elif scenario == "membership_out_date":
            row[9] = "20260701"
        elif scenario == "membership_future":
            row[8] = "20260716"
        elif scenario == "membership_name":
            row[7] = ""
        else:
            row[6] = responses[2].rows[0][6]
        responses[ordinal] = ParsedResponse(responses[ordinal].fields, (tuple(row), *responses[ordinal].rows[1:]))
    elif scenario == "bse":
        row = list(responses[2].rows[0])
        row[6] = "920001.BJ"
        responses[2] = ParsedResponse(responses[2].fields, (tuple(row), *responses[2].rows[1:]))
    elif scenario == "st":
        code = str(responses[2].rows[0][6])
        responses[35] = ParsedResponse(
            responses[35].fields,
            ((code, f"ST公司{code}", "20260715", "S", "ST"),),
        )
    elif scenario == "st_date":
        code = str(responses[2].rows[0][6])
        responses[35] = ParsedResponse(
            responses[35].fields,
            ((code, f"ST公司{code}", "20260714", "S", "ST"),),
        )
    elif scenario == "stock_empty":
        responses[33] = ParsedResponse(responses[33].fields, ())
    elif scenario == "stock_exchange":
        row = list(responses[33].rows[0])
        row[3] = "SZSE"
        responses[33] = ParsedResponse(responses[33].fields, (tuple(row), *responses[33].rows[1:]))
    elif scenario in {"delisting", "listing_age"}:
        row = list(responses[33].rows[0])
        if scenario == "delisting":
            row[1] = "退市公司"
        else:
            row[6] = "20250101"
        responses[33] = ParsedResponse(responses[33].fields, (tuple(row), *responses[33].rows[1:]))
    elif scenario == "daily_coverage":
        responses[36] = ParsedResponse(responses[36].fields, responses[36].rows[:-1])
    elif scenario.startswith("daily_"):
        rows = list(responses[36].rows)
        row = list(rows[0])
        if scenario == "daily_date":
            row[1] = "20260714"
        elif scenario == "daily_value":
            row[2] = core.JsonNumber("-1")
        else:
            second = list(rows[1])
            second[0] = row[0]
            rows[1] = tuple(second)
        rows[0] = tuple(row)
        responses[36] = ParsedResponse(responses[36].fields, tuple(rows))
    else:
        target_names = {"银行", "非银金融", "房地产"}
        target_codes = [
            str(row[6]) for ordinal in range(2, 33) for row in responses[ordinal].rows if str(row[1]) in target_names
        ]
        responses[35] = ParsedResponse(
            responses[35].fields,
            tuple((code, f"ST公司{code}", "20260715", "S", "ST") for code in target_codes[5:]),
        )
    reproduction = reproduce_frame(authorization, responses, complete=True, authorization_window=True)
    if expected in {"classification", "membership", "stock_basic", "stock_st", "daily", "sampling_cells"}:
        assert reproduction.gates[expected] is False
    else:
        assert reproduction.ledger is not None
        entries = reproduction.ledger["entries"]
        assert isinstance(entries, list)
        assert expected in {
            str(entry["reason_code"]) for entry in entries if isinstance(entry, dict) and "reason_code" in entry
        }


def test_worker_deadline_after_success_receipt_keeps_success_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    auth_dir = directory.parent / f"auth-{directory.name}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        import qualitative_v2_m4_segmented_runtime as runtime

        zone = timezone(timedelta(hours=8))
        before = datetime.now(zone).replace(microsecond=0)
        after = before + timedelta(seconds=10)
        value = _capability_value(directory.name, f"{root}/{directory.name}", now=before)
        value["not_after"] = (before + timedelta(seconds=5)).isoformat(timespec="seconds")
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.setenv("TUSHARE_TOKEN", "a" * 64)
        expired = False

        def now() -> datetime:
            return after if expired else before

        def transport(
            call: SegmentedRestCall, token: str, byte_limit: int, timeout: float
        ) -> runtime.TransportResponse:
            del token, byte_limit, timeout
            raw = json.dumps(
                {"code": 0, "msg": "", "data": {"fields": list(call.fields), "items": []}},
                separators=(",", ":"),
            ).encode()
            return runtime.TransportResponse(core.ORIGIN, core.ORIGIN, 200, "application/json", raw)

        def phase(name: str, _ordinal: int) -> None:
            nonlocal expired
            if name == "receipt_sealed":
                expired = True

        hooks = runtime.RuntimeHooks(transport, now, lambda: 1.0, phase)
        with runtime._runtime_hooks(hooks):
            result = execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        assert len(result.receipts) == 1
        assert result.receipts[0].state == "success_blob"
        manifest = json.loads((result.attempt_dir / "attempt-manifest.json").read_bytes())
        assert manifest["stop_record_ref"] is not None
        assert manifest["unattempted_ordinals"][0] == 2
        assert manifest["gate_results"]["authorization_window"] is False
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


def test_two_executors_contend_for_one_create_only_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import multiprocessing

    import qualitative_v2_m4_segmented_runtime as runtime

    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    auth_dir = directory.parent / f"auth-{directory.name}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    first_started = multiprocessing.Event()
    release_first = multiprocessing.Event()
    outcomes: Any = multiprocessing.Queue()
    try:
        now = datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0)
        value = _capability_value(directory.name, f"{root}/{directory.name}", now=now)
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.setenv("TUSHARE_TOKEN", "a" * 64)

        def phase(name: str, _ordinal: int) -> None:
            if name == "transport_started":
                first_started.set()
                if not release_first.wait(10):
                    raise AssertionError("concurrency release timed out")

        def malformed(
            call: SegmentedRestCall, token: str, byte_limit: int, timeout: float
        ) -> runtime.TransportResponse:
            del call, token, byte_limit, timeout
            return runtime.TransportResponse(core.ORIGIN, core.ORIGIN, 200, "application/json", b"not-json")

        hooks = runtime.RuntimeHooks(malformed, lambda: now, lambda: 1.0, phase)

        def run(label: str) -> None:
            try:
                result = execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
                outcomes.put((label, "sealed", result.manifest_sha256))
            except BaseException as exc:
                outcomes.put((label, "blocked", getattr(exc, "code", type(exc).__name__)))

        context = multiprocessing.get_context("fork")
        with runtime._runtime_hooks(hooks):
            first = context.Process(target=run, args=("first",))
            first.start()
            assert first_started.wait(10)
            second = context.Process(target=run, args=("second",))
            second.start()
            second.join(10)
            assert second.exitcode == 0
            release_first.set()
            first.join(10)
            assert first.exitcode == 0
        records = {label: (status, detail) for label, status, detail in [outcomes.get(), outcomes.get()]}
        assert records["second"] == ("blocked", "attempt_already_exists")
        assert records["first"][0] == "sealed"
        assert verify_segmented_rest_attempt(directory).manifest_sha256 == records["first"][1]
    finally:
        release_first.set()
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)


@pytest.mark.parametrize(
    "argv",
    [
        ["verify", "--attempt-d", "x"],
        ["execute", "--authorization", "x", "--authorization-s", "y"],
    ],
)
def test_cli_rejects_abbreviated_options(argv: list[str]) -> None:
    from qualitative_v2_m4_segmented_rest import _parser

    with pytest.raises(SystemExit):
        _parser().parse_args(argv)


@pytest.mark.parametrize("window", ["lock_creation", "process_start"])
def test_supervisor_setup_windows_defer_sigterm_and_seal(monkeypatch: pytest.MonkeyPatch, window: str) -> None:
    directory, relative = _workspace()
    root = relative.rsplit("/", 1)[0]
    auth_dir = directory.parent / f"auth-{directory.name}"
    auth_dir.mkdir()
    core.ROOTS["capability"] = root
    try:
        import os
        import signal

        import qualitative_v2_m4_segmented_runtime as runtime

        zone = timezone(timedelta(hours=8))
        now = datetime.now(zone).replace(microsecond=0)
        value = _capability_value(directory.name, f"{root}/{directory.name}", now=now)
        path, checksum = _write_authorization(auth_dir, value)
        _remove(directory)
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        if window == "lock_creation":
            original_open = runtime.os.open
            sent = False

            def interrupting_open(target, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal sent
                if not sent and Path(target).name == f".{directory.name}.lock":
                    sent = True
                    os.kill(os.getpid(), signal.SIGTERM)
                return original_open(target, flags, *args, **kwargs)

            monkeypatch.setattr(runtime.os, "open", interrupting_open)
        else:
            real_context = runtime.multiprocessing.get_context("fork")

            class ProcessProxy:
                def __init__(self, process) -> None:  # type: ignore[no-untyped-def]
                    self._process = process

                def start(self) -> None:
                    self._process.start()
                    os.kill(os.getpid(), signal.SIGTERM)

                def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
                    return getattr(self._process, name)

            class ContextProxy:
                def Process(self, *args, **kwargs):  # type: ignore[no-untyped-def, N802]
                    return ProcessProxy(real_context.Process(*args, **kwargs))

            monkeypatch.setattr(runtime.multiprocessing, "get_context", lambda _kind: ContextProxy())
        with pytest.raises(SystemExit):
            execute_segmented_rest_attempt(authorization_path=path, authorization_hash_path=checksum)
        result = verify_segmented_rest_attempt(directory)
        assert result.disposition == "NO_QUALIFIED_FRAME_SOURCE"
        assert (directory / "supervisor-commit.json").is_file()
    finally:
        core.ROOTS["capability"] = "artifacts/milestone-004/v1.3.1/capability-probes"
        _remove(directory)
        _remove(auth_dir)
