#!/usr/bin/env python3
"""Pure-stdlib golden oracle for the M4 v1.3.2 freeze candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = ROOT / "reviews/milestone-004-preregistration-v1.3.2/golden"
INDEX_PATH = ROOT / "reviews/milestone-004-preregistration-v1.3.2/golden-vectors.json"
PROTOCOL_PATH = ROOT / "docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.2.md"
PROTOCOL_RELATIVE = PROTOCOL_PATH.relative_to(ROOT).as_posix()
PROTOCOL_ID = "qualitative-v2-m4-prereg-v1.3.2"
PREDECESSOR = "f494c1973f002536874782af41221d37a2ca618c329fa48a2ae650b544c88042"
TRADE_DATE = "2026-07-15"
COMPACT_DATE = "20260715"
NOT_BEFORE = "2026-07-15T18:00:00+08:00"
NOT_AFTER = "2026-07-15T23:59:59+08:00"
SEALED_AT = "2026-07-15T18:30:00+08:00"
NONCE = "0123456789abcdef" * 4
HEX = {letter: letter * 64 for letter in "abcdef"}
INDUSTRIES = (
    ("801010.SI", "农林牧渔"),
    ("801030.SI", "基础化工"),
    ("801040.SI", "钢铁"),
    ("801050.SI", "有色金属"),
    ("801080.SI", "电子"),
    ("801110.SI", "家用电器"),
    ("801120.SI", "食品饮料"),
    ("801130.SI", "纺织服饰"),
    ("801140.SI", "轻工制造"),
    ("801150.SI", "医药生物"),
    ("801160.SI", "公用事业"),
    ("801170.SI", "交通运输"),
    ("801180.SI", "房地产"),
    ("801200.SI", "商贸零售"),
    ("801210.SI", "社会服务"),
    ("801230.SI", "综合"),
    ("801710.SI", "建筑材料"),
    ("801720.SI", "建筑装饰"),
    ("801730.SI", "电力设备"),
    ("801740.SI", "国防军工"),
    ("801750.SI", "计算机"),
    ("801760.SI", "传媒"),
    ("801770.SI", "通信"),
    ("801780.SI", "银行"),
    ("801790.SI", "非银金融"),
    ("801880.SI", "汽车"),
    ("801890.SI", "机械设备"),
    ("801950.SI", "煤炭"),
    ("801960.SI", "石油石化"),
    ("801970.SI", "环保"),
    ("801980.SI", "美容护理"),
)
MEMBER_FIELDS = [
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
]
GENERATOR_PATHS = sorted(
    [
        "qualitative_v2_audit_v132.py",
        "qualitative_v2_m4_segmented_core_v132.py",
        "qualitative_v2_m4_segmented_rest_v132.py",
        "qualitative_v2_m4_segmented_runtime_v132.py",
        "qualitative_v2_m4_segmented_verify_v132.py",
    ]
)
GATE_FIELDS = (
    "authorization_window",
    "calendar",
    "classification",
    "daily",
    "date_derivation",
    "frame",
    "membership",
    "overall_pass",
    "sampling_cells",
    "stock_basic",
)


def canonical(value: Any) -> bytes:
    """Encode canonical fixture bytes without importing project canonicalizers."""
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def frame_calls() -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = [
        {
            "api_name": "index_classify",
            "fields": ["index_code", "industry_name", "level", "src"],
            "ordinal": 1,
            "params": {"level": "L1", "src": "SW2021"},
        }
    ]
    for ordinal, (code, _name) in enumerate(INDUSTRIES, 2):
        calls.append(
            {
                "api_name": "index_member_all",
                "fields": MEMBER_FIELDS,
                "ordinal": ordinal,
                "params": {"is_new": "Y", "l1_code": code},
            }
        )
    calls.extend(
        [
            {
                "api_name": "stock_basic",
                "fields": ["ts_code", "name", "market", "exchange", "curr_type", "list_status", "list_date"],
                "ordinal": 33,
                "params": {"exchange": "SSE", "list_status": "L"},
            },
            {
                "api_name": "stock_basic",
                "fields": ["ts_code", "name", "market", "exchange", "curr_type", "list_status", "list_date"],
                "ordinal": 34,
                "params": {"exchange": "SZSE", "list_status": "L"},
            },
            {
                "api_name": "daily_basic",
                "fields": ["ts_code", "trade_date", "total_mv"],
                "ordinal": 35,
                "params": {"trade_date": COMPACT_DATE},
            },
        ]
    )
    return calls


def calendar_calls() -> list[dict[str, Any]]:
    return [
        {
            "api_name": "trade_cal",
            "fields": ["exchange", "cal_date", "is_open", "pretrade_date"],
            "ordinal": ordinal,
            "params": {"end_date": "20260915", "exchange": exchange, "start_date": "20260514"},
        }
        for ordinal, exchange in enumerate(("SSE", "SZSE"), 1)
    ]


def protocol_hash() -> str:
    return digest(PROTOCOL_PATH.read_bytes())


def frame_authorization(mode: str) -> dict[str, Any]:
    capability = mode == "capability"
    attempt_id = "golden-capability" if capability else "golden-capture"
    capability_ref = (
        None
        if capability
        else {
            "attempt_id": "golden-capability",
            "relative_path": "artifacts/milestone-004/v1.3.2/capability-probes/golden-capability/attempt-manifest.json",
            "sha256": HEX["a"],
        }
    )
    evidence_ref = (
        None
        if capability
        else {
            "attempt_id": "golden-date",
            "authorization_id": "auth-golden-date",
            "relative_path": "artifacts/milestone-004/v1.3.2/date-selection-evidence/golden-date/date-selection-evidence.json",
            "sha256": HEX["b"],
        }
    )
    return {
        "attempt_byte_limit": 134217728,
        "attempt_id": attempt_id,
        "attempt_output_dir": f"artifacts/milestone-004/v1.3.2/{'capability-probes' if capability else 'captures'}/{attempt_id}",
        "authorization_id": f"auth-{attempt_id}",
        "calls": frame_calls(),
        "capability_manifest_ref": capability_ref,
        "credential_env_var": "TUSHARE_TOKEN",
        "date_evidence_ref": evidence_ref,
        "max_attempts_per_ordinal": 1,
        "mode": mode,
        "not_after": NOT_AFTER,
        "not_before": NOT_BEFORE,
        "origin": "https://api.tushare.pro",
        "protocol_path": PROTOCOL_RELATIVE,
        "protocol_sha256": protocol_hash(),
        "response_byte_limit": 33554432,
        "schema_version": "m4-segmented-rest-frame-authorization-v3",
        "supersedes_sha256": PREDECESSOR,
        "trade_date": TRADE_DATE,
        "trade_date_kind": "probe_trade_date" if capability else "sampling_date",
    }


def date_authorization() -> dict[str, Any]:
    return {
        "attempt_byte_limit": 8388608,
        "attempt_id": "golden-date",
        "attempt_output_dir": "artifacts/milestone-004/v1.3.2/date-selection-evidence/golden-date",
        "authorization_id": "auth-golden-date",
        "calendar_end": "2026-09-15",
        "calendar_start": "2026-05-14",
        "calls": calendar_calls(),
        "capability_manifest_ref": {
            "attempt_id": "golden-capability",
            "relative_path": "artifacts/milestone-004/v1.3.2/capability-probes/golden-capability/attempt-manifest.json",
            "sha256": HEX["a"],
        },
        "credential_env_var": "TUSHARE_TOKEN",
        "max_attempts_per_ordinal": 1,
        "not_after": NOT_AFTER,
        "not_before": NOT_BEFORE,
        "origin": "https://api.tushare.pro",
        "proposed_sampling_date": TRADE_DATE,
        "protocol_path": PROTOCOL_RELATIVE,
        "protocol_sha256": protocol_hash(),
        "purpose": "date_selection_evidence",
        "response_byte_limit": 4194304,
        "schema_version": "m4-date-selection-authorization-v3",
        "supersedes_sha256": PREDECESSOR,
    }


def request_description(call: dict[str, Any]) -> dict[str, Any]:
    return {
        "api_name": call["api_name"],
        "fields": call["fields"],
        "method": "POST",
        "origin": "https://api.tushare.pro",
        "params": call["params"],
    }


def receipt(state: str) -> dict[str, Any]:
    blob = state != "terminal_failure_no_blob"
    success = state == "success_blob"
    return {
        "attempt_id": "golden-capability",
        "authorization_id": "auth-golden-capability",
        "blob_relative_path": f"blobs/0001-{HEX['c']}.bin" if blob else None,
        "blob_sha256": HEX["c"] if blob else None,
        "byte_count": 127 if blob else None,
        "call_id": "tushare:index_classify",
        "captured_at": "2026-07-15T18:00:01+08:00",
        "content_type": "application/json" if blob else None,
        "error_code": None if success else ("provider_error" if blob else "transport_error"),
        "http_status": 200 if blob else None,
        "ordinal": 1,
        "provider_code": 0 if success else (40203 if blob else None),
        "request_description": request_description(frame_calls()[0]),
        "schema_version": "m4-segmented-rest-receipt-v3",
        "state": state,
        "status_category": "success" if success else ("provider" if blob else "transport"),
    }


def typed_row_hash() -> str:
    row = {
        "fields": ["ts_code", "trade_date", "total_mv", "note", "flag"],
        "values": [
            {"type": "string", "value": "600000.SH"},
            {"type": "string", "value": COMPACT_DATE},
            {"type": "number", "value": "00123.4500e+2"},
            {"type": "null", "value": None},
            {"type": "boolean", "value": True},
        ],
    }
    return digest(canonical(row))


def ledger(entries: list[dict[str, Any]], mode: str = "capability") -> dict[str, Any]:
    return {
        "attempt_id": f"golden-{mode}",
        "authorization_id": f"auth-golden-{mode}",
        "entries": entries,
        "mode": mode,
        "schema_version": "m4-exclusion-ledger-v3",
    }


def entry(code: str, reason: str, source: str, ordinal: int, name: str | None, row_hash: str) -> dict[str, Any]:
    return {
        "canonical_row_sha256": row_hash,
        "l1_code": None,
        "l1_name": None,
        "name": name,
        "provider_row_ordinal": 1,
        "reason_code": reason,
        "source_kind": source,
        "source_ordinal": ordinal,
        "ts_code": code,
    }


def gates(**updates: bool | None) -> dict[str, bool | None]:
    value: dict[str, bool | None] = {name: None for name in GATE_FIELDS}
    value.update({"authorization_window": True, "overall_pass": False})
    value.update(updates)
    return value


def manifest(
    kind: str,
    disposition: str,
    gate_values: dict[str, bool | None],
    *,
    complete: bool,
    success: list[int],
    terminal: list[int],
    unattempted: list[int],
    failed: int | None,
    commit: str,
) -> dict[str, Any]:
    expected = list(range(1, 3)) if kind == "date_selection_evidence" else list(range(1, 36))
    return {
        "artifact_files": [],
        "attempt_id": f"golden-{kind}",
        "attempt_kind": kind,
        "authorization_id": f"auth-golden-{kind}",
        "authorization_relative_path": f"golden-authorizations/{kind}.json",
        "authorization_sha256": HEX["a"],
        "blob_refs": [],
        "capture_input_eligible": disposition == "FRAME_CAPTURE_ELIGIBLE",
        "complete": complete,
        "date_evidence_ref": {
            "byte_count": 999,
            "kind": "date_evidence",
            "relative_path": "date-selection-evidence.json",
            "sha256": HEX["b"],
        }
        if disposition == "DATE_EVIDENCE_VALID"
        else None,
        "date_selection_only": kind == "date_selection_evidence",
        "disposition": disposition,
        "exclusion_counts": None
        if kind == "date_selection_evidence"
        else {
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
            "by_source": {source: 0 for source in ("membership", "stock_basic", "daily")},
            "total": 0,
        },
        "exclusion_ledger_ref": None
        if kind == "date_selection_evidence"
        else {
            "byte_count": 128,
            "kind": "exclusion_ledger",
            "relative_path": "exclusion-ledger.json",
            "sha256": HEX["c"],
        },
        "execution_deadline": NOT_AFTER,
        "expected_ordinals": expected,
        "failed_ordinal": failed,
        "gate_results": gate_values,
        "generator_files": [{"relative_path": path, "sha256": HEX["d"]} for path in GENERATOR_PATHS],
        "non_adoptable": disposition != "FRAME_CAPTURE_ELIGIBLE",
        "protocol_path": PROTOCOL_RELATIVE,
        "protocol_sha256": protocol_hash(),
        "receipt_refs": [],
        "response_byte_total": 0,
        "schema_version": "m4-segmented-rest-attempt-manifest-v3",
        "sealed_at": SEALED_AT,
        "stop_record_ref": None,
        "successful_ordinals": success,
        "supersedes_sha256": PREDECESSOR,
        "supervisor_commit_ref": {
            "byte_count": 301,
            "kind": "supervisor_commit",
            "nonce": NONCE,
            "relative_path": "supervisor-commit.json",
            "sha256": commit,
        },
        "terminal_failed_ordinals": terminal,
        "unattempted_ordinals": unattempted,
    }


def fixtures() -> dict[str, bytes]:
    capability = frame_authorization("capability")
    capture = frame_authorization("capture")
    full_gates = gates(
        classification=True,
        membership=True,
        stock_basic=True,
        daily=True,
        frame=True,
        sampling_cells=True,
        overall_pass=True,
    )
    failed_gates = gates(
        classification=True, membership=True, stock_basic=True, daily=True, frame=False, sampling_cells=False
    )
    date_ok = gates(calendar=True, date_derivation=True, overall_pass=True)
    date_bad = gates(calendar=True, date_derivation=False)
    st_entry = entry("600001.SH", "st_risk_warning", "stock_basic", 33, "  ＳＴ示例  ", HEX["a"])
    bse_entry = entry("430001.BJ", "exchange_out_of_scope_bse", "membership", 2, "北交示例", HEX["b"])
    daily_entry = entry("600000.SH", "not_in_frozen_sw2021_membership", "daily", 35, None, typed_row_hash())
    date_evidence = {
        "attempt_id": "golden-date",
        "authorization_id": "auth-golden-date",
        "calendar_end": "2026-09-15",
        "calendar_start": "2026-05-14",
        "date_selection_only": True,
        "next_common_open_date": "2026-07-16",
        "protocol_sha256": protocol_hash(),
        "sampling_date": TRADE_DATE,
        "schema_version": "m4-date-selection-evidence-v3",
        "sealed_at": SEALED_AT,
        "sources": [
            {
                "blob_relative_path": f"blobs/000{ordinal}-{HEX['c']}.bin",
                "blob_sha256": HEX["c"],
                "byte_count": 4096,
                "call_id": f"tushare:trade_cal:{exchange}",
                "exchange": exchange,
                "receipt_relative_path": f"receipts/000{ordinal}.json",
                "receipt_sha256": HEX["d"],
            }
            for ordinal, exchange in enumerate(("SSE", "SZSE"), 1)
        ],
        "valid_from": NOT_BEFORE,
        "valid_until": "2026-07-16T17:59:59+08:00",
    }
    values: dict[str, Any] = {
        "001-frame-matrix-35.json": frame_calls(),
        "002-calendar-matrix-2.json": calendar_calls(),
        "003-frame-authorization-capability.json": capability,
        "004-frame-authorization-capture.json": capture,
        "005-date-authorization.json": date_authorization(),
        "006-request-description-daily.json": request_description(frame_calls()[-1]),
        "007-receipt-success-blob.json": receipt("success_blob"),
        "008-receipt-terminal-failure-blob.json": receipt("terminal_failure_blob"),
        "009-receipt-terminal-failure-no-blob.json": receipt("terminal_failure_no_blob"),
        "010-unattempted-inventory.json": {
            "expected_ordinals": list(range(1, 36)),
            "successful_ordinals": [1],
            "terminal_failed_ordinals": [],
            "unattempted_ordinals": list(range(2, 36)),
        },
        "011-stop-record.json": {
            "attempt_id": "golden-capability",
            "authorization_id": "auth-golden-capability",
            "next_ordinal": 1,
            "schema_version": "m4-attempt-stop-v3",
            "stop_code": "credential_unavailable",
            "stopped_at": "2026-07-15T18:00:00+08:00",
        },
        "012-supervisor-commit-pass.json": {
            "attempt_id": "golden-capture",
            "authorization_id": "auth-golden-capture",
            "execution_deadline": NOT_AFTER,
            "nonce": NONCE,
            "outcome": "PASS",
            "schema_version": "m4-segmented-rest-supervisor-commit-v3",
        },
        "013-supervisor-commit-fail.json": {
            "attempt_id": "golden-capability",
            "authorization_id": "auth-golden-capability",
            "execution_deadline": NOT_AFTER,
            "nonce": NONCE,
            "outcome": "FAIL",
            "schema_version": "m4-segmented-rest-supervisor-commit-v3",
        },
        "014-ledger-empty.json": ledger([]),
        "015-ledger-bse.json": ledger([bse_entry]),
        "016-ledger-name-only-st.json": ledger([st_entry]),
        "017-ledger-daily-exact-unicode-null-sort.json": ledger([bse_entry, st_entry, daily_entry]),
        "018-date-pass-evidence.json": date_evidence,
        "019-manifest-capability-pass.json": manifest(
            "capability",
            "ELIGIBLE_FOR_FULL_CAPTURE_AUTHORIZATION",
            full_gates,
            complete=True,
            success=list(range(1, 36)),
            terminal=[],
            unattempted=[],
            failed=None,
            commit=HEX["e"],
        ),
        "020-manifest-pretransport-fail.json": manifest(
            "capability",
            "NO_QUALIFIED_FRAME_SOURCE",
            gates(authorization_window=False),
            complete=False,
            success=[],
            terminal=[],
            unattempted=list(range(1, 36)),
            failed=None,
            commit=HEX["e"],
        ),
        "021-manifest-terminal-blob-fail.json": manifest(
            "capability",
            "NO_QUALIFIED_FRAME_SOURCE",
            gates(),
            complete=False,
            success=[],
            terminal=[1],
            unattempted=list(range(2, 36)),
            failed=1,
            commit=HEX["e"],
        ),
        "022-manifest-complete-gate-fail.json": manifest(
            "capability",
            "NO_QUALIFIED_FRAME_SOURCE",
            failed_gates,
            complete=True,
            success=list(range(1, 36)),
            terminal=[],
            unattempted=[],
            failed=None,
            commit=HEX["e"],
        ),
        "023-manifest-date-pass.json": manifest(
            "date_selection_evidence",
            "DATE_EVIDENCE_VALID",
            date_ok,
            complete=True,
            success=[1, 2],
            terminal=[],
            unattempted=[],
            failed=None,
            commit=HEX["e"],
        ),
        "024-manifest-date-fail-no-evidence.json": manifest(
            "date_selection_evidence",
            "DATE_EVIDENCE_FAILED",
            date_bad,
            complete=True,
            success=[1, 2],
            terminal=[],
            unattempted=[],
            failed=None,
            commit=HEX["e"],
        ),
        "025-manifest-capture-pass.json": manifest(
            "capture",
            "FRAME_CAPTURE_ELIGIBLE",
            full_gates,
            complete=True,
            success=list(range(1, 36)),
            terminal=[],
            unattempted=[],
            failed=None,
            commit=HEX["e"],
        ),
        "026-manifest-capture-fail.json": manifest(
            "capture",
            "CAPTURE_FAILED_CLOSED",
            failed_gates,
            complete=True,
            success=list(range(1, 36)),
            terminal=[],
            unattempted=[],
            failed=None,
            commit=HEX["e"],
        ),
        "027-closed-set-prospective.json": {
            "allowed_absent": ["supervisor-commit.json"],
            "files": ["attempt-manifest.json", "exclusion-ledger.json"],
            "phase": "candidate",
        },
        "028-closed-set-final.json": {
            "allowed_absent": [],
            "files": ["attempt-manifest.json", "exclusion-ledger.json", "supervisor-commit.json"],
            "phase": "post_publication",
        },
    }
    encoded = {name: canonical(value) for name, value in values.items()}
    auth_path = "golden-authorizations/frame-capability.json"
    encoded["029-authorization-checksum-line.txt"] = (
        f"{digest(encoded['003-frame-authorization-capability.json'])}  {auth_path}\n".encode("ascii")
    )
    encoded["030-provider-count-zero.json"] = canonical(
        {
            "code": 0,
            "data": {
                "count": 0,
                "fields": ["index_code", "industry_name", "level", "src"],
                "items": [],
            },
            "msg": None,
        }
    )
    return encoded


def index_bytes(items: dict[str, bytes]) -> bytes:
    vectors = {
        name.removesuffix(".json").removesuffix(".txt"): {
            "byte_count": len(raw),
            "path": f"reviews/milestone-004-preregistration-v1.3.2/golden/{name}",
            "sha256": digest(raw),
        }
        for name, raw in sorted(items.items())
    }
    return canonical(
        {"protocol_sha256": protocol_hash(), "schema_version": "m4-segmented-rest-golden-index-v2", "vectors": vectors}
    )


def verify() -> None:
    items = fixtures()
    failures: list[str] = []
    for name, expected in items.items():
        path = GOLDEN_ROOT / name
        actual = path.read_bytes() if path.is_file() else b""
        if actual != expected:
            failures.append(name)
    expected_index = index_bytes(items)
    if not INDEX_PATH.is_file() or INDEX_PATH.read_bytes() != expected_index:
        failures.append(INDEX_PATH.name)
    if failures:
        raise SystemExit("golden drift: " + ", ".join(failures))
    print(f"PASS m4-v132-spec-oracle vectors={len(items)} protocol_sha256={protocol_hash()}")


def emit_patch() -> None:
    items = fixtures()
    print("*** Begin Patch")
    for name, raw in {**items, "../golden-vectors.json": index_bytes(items)}.items():
        target = f"reviews/milestone-004-preregistration-v1.3.2/golden/{name}"
        print(f"*** Add File: {target}")
        text = raw.decode("utf-8")
        for line in text.splitlines():
            print("+" + line)
    print("*** End Patch")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-patch", action="store_true")
    args = parser.parse_args()
    if args.emit_patch:
        emit_patch()
    else:
        verify()


if __name__ == "__main__":
    main()
