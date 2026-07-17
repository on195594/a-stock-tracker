#!/usr/bin/env python3
"""Independent stdlib cross-checker for M4 v1.3.2 golden bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "reviews/milestone-004-preregistration-v1.3.2"
PROTOCOL = ROOT / "docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.2.md"
PROTOCOL_HASH = "555b1d4c410da1dc68be3279a2070f55b5e73f7b6ef9ca7d850f89d0533dbeea"
EXPECTED: dict[str, dict[str, Any]] = json.loads(
    '{"001-frame-matrix-35":{"byte_count":7073,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/001-frame-matrix-35.json","sha256":"8d07aefa2cf1c3dbb10789af3ba7816249c68b62f307dd84c010213f155c9071"},"002-calendar-matrix-2":{"byte_count":343,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/002-calendar-matrix-2.json","sha256":"7dd6aa4b52ec3ebbb66a260b030c35b84da03de7b7a06a26c4e4e6402e4e65e3"},"003-frame-authorization-capability":{"byte_count":7960,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/003-frame-authorization-capability.json","sha256":"775df35b5d944b2aa90261888ed20ca0b68770e0a33efd0ad8433449e89fc858"},"004-frame-authorization-capture":{"byte_count":8401,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/004-frame-authorization-capture.json","sha256":"7f0ce5ff004267a83fd65522efe560ad289fa419f5d4ac7e4194f57de6bb951d"},"005-date-authorization":{"byte_count":1446,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/005-date-authorization.json","sha256":"b076f6eeca19e3bdabd28d89b841e607d7c7fb30ddd5f14b944358dc19096d98"},"006-request-description-daily":{"byte_count":158,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/006-request-description-daily.json","sha256":"27a01f149ae78f13acd6251dab23c28fc9043bd7a573237dd97b3b790477caea"},"007-receipt-success-blob":{"byte_count":752,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/007-receipt-success-blob.json","sha256":"27b6864adb3f9c1fb6efa3353c128afb367e1a2fa5b5ef036fcffa9aa30e9512"},"008-receipt-terminal-failure-blob":{"byte_count":778,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/008-receipt-terminal-failure-blob.json","sha256":"b1843b74a09418ace7b014c96211dddea81c2fc5b04f7977fcb9210620602cc2"},"009-receipt-terminal-failure-no-blob":{"byte_count":631,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/009-receipt-terminal-failure-no-blob.json","sha256":"e5756726ebdf1a9430b212d67c8ef6d3292dcaf3f4fd15a772aac82415b90365"},"010-unattempted-inventory":{"byte_count":295,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/010-unattempted-inventory.json","sha256":"1e870eebaa4ab1e7384e02f2b3e985b23fd4402179b9dc3a909e744f69d04bb9"},"011-stop-record":{"byte_count":212,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/011-stop-record.json","sha256":"abe9bedea2a28c254689ea05e9b48c7896d1167fb5013afc5b3a8b6515aefbc4"},"012-supervisor-commit-pass":{"byte_count":272,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/012-supervisor-commit-pass.json","sha256":"78110d32c0bd9e2aeccb67f9cb9609023b59575d76391b6330edc13c75b74bba"},"013-supervisor-commit-fail":{"byte_count":278,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/013-supervisor-commit-fail.json","sha256":"d38154135b69e47fa5dcfc82a5de19a59ca3f511e22d39ba4d1446363da1cdb0"},"014-ledger-empty":{"byte_count":154,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/014-ledger-empty.json","sha256":"754baab8b8677b64577cc0ad033983185559c0448b76487b9339b94e9c202e28"},"015-ledger-bse":{"byte_count":432,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/015-ledger-bse.json","sha256":"6442504fb4c786fc7675e7932d18cb78ebe8c9bd13403de56ffc3a68e3265138"},"016-ledger-name-only-st":{"byte_count":428,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/016-ledger-name-only-st.json","sha256":"d4bea872b7c80ae41b3f3299045b92d079654041a5ef9d446fcd03c202be7611"},"017-ledger-daily-exact-unicode-null-sort":{"byte_count":978,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/017-ledger-daily-exact-unicode-null-sort.json","sha256":"1599ad31728eca52e764de9b17c4fd73ff89a1dd5fd752ae4932bb06380a5918"},"018-date-pass-evidence":{"byte_count":1257,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/018-date-pass-evidence.json","sha256":"ad1af3470d36781d35b9882423ddc49f787be838931ebf70a8cca8b188fc3e76"},"019-manifest-capability-pass":{"byte_count":2942,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/019-manifest-capability-pass.json","sha256":"50d38152c6dc279c1ce5654fd3c67280686ff3c849fcc0512c5607307f6a6320"},"020-manifest-pretransport-fail":{"byte_count":2931,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/020-manifest-pretransport-fail.json","sha256":"8821954d431b95f0a535951904b27cc51f75cb166e484ecb56cdd6743a2fda0a"},"021-manifest-terminal-blob-fail":{"byte_count":2926,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/021-manifest-terminal-blob-fail.json","sha256":"82348f38e55c465c0e2e7479f7b065f09b14c93a7c854fdd7dc3402100dc9356"},"022-manifest-complete-gate-fail":{"byte_count":2931,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/022-manifest-complete-gate-fail.json","sha256":"0aaeb6015d03e512fe554c2821d86ccbfbaffce73a79bf3a0850e0d27ce48557"},"023-manifest-date-pass":{"byte_count":2503,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/023-manifest-date-pass.json","sha256":"ce746dc51dbe62de116f1a0066eda19a8e405b6e9bd02b1e741c59ab37e49685"},"024-manifest-date-fail-no-evidence":{"byte_count":2346,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/024-manifest-date-fail-no-evidence.json","sha256":"c748da3dc927b4fdbc1507cf3673f9ca5937ec5aced1a8efa02d4046d9a8f38c"},"025-manifest-capture-pass":{"byte_count":2913,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/025-manifest-capture-pass.json","sha256":"85a437b53d45634bbba90376cbc4f722ee36b30bb828d29535dc91de7927020e"},"026-manifest-capture-fail":{"byte_count":2915,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/026-manifest-capture-fail.json","sha256":"f3866f87f0868c55e6ddff7552507c0b108eed4ce70cf46e56fb9811f079f73b"},"027-closed-set-prospective":{"byte_count":124,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/027-closed-set-prospective.json","sha256":"4ef872d8bf03fcbb8ce3d28714a3a44001a71479b3244210bf7cbe1ad9d5dac5"},"028-closed-set-final":{"byte_count":132,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/028-closed-set-final.json","sha256":"1beb43c721943fcf022f29664918f97df43c7f2916542a18923dbd627424e5e1"},"029-authorization-checksum-line":{"byte_count":110,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/029-authorization-checksum-line.txt","sha256":"800b76741c5969601ef8cd93cb59f6da259a6fbff366f10645dda1021579a00c"},"030-provider-count-zero":{"byte_count":106,"path":"reviews/milestone-004-preregistration-v1.3.2/golden/030-provider-count-zero.json","sha256":"218fbfc9b71c8d1d36b6cadf3ba7b5821293d2add92579451202abc9b276a071"}}'
)
MANIFEST_KEYS = {
    "artifact_files",
    "attempt_id",
    "attempt_kind",
    "authorization_id",
    "authorization_relative_path",
    "authorization_sha256",
    "blob_refs",
    "capture_input_eligible",
    "complete",
    "date_evidence_ref",
    "date_selection_only",
    "disposition",
    "exclusion_counts",
    "exclusion_ledger_ref",
    "execution_deadline",
    "expected_ordinals",
    "failed_ordinal",
    "gate_results",
    "generator_files",
    "non_adoptable",
    "protocol_path",
    "protocol_sha256",
    "receipt_refs",
    "response_byte_total",
    "schema_version",
    "sealed_at",
    "stop_record_ref",
    "successful_ordinals",
    "supersedes_sha256",
    "supervisor_commit_ref",
    "terminal_failed_ordinals",
    "unattempted_ordinals",
}
GATE_KEYS = {
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
}


def hash_chunks(raw: bytes) -> str:
    """Hash in deliberately segmented chunks, independently of the spec oracle."""
    state = hashlib.sha256()
    view = memoryview(raw)
    for offset in range(0, len(view), 97):
        state.update(view[offset : offset + 97])
    return state.hexdigest()


def encode_json(value: Any) -> bytes:
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        allow_nan=False,
        check_circular=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (encoder.encode(value) + "\n").encode("utf-8")


def load_vector(identifier: str) -> tuple[bytes, Any]:
    meta = EXPECTED[identifier]
    path = ROOT / meta["path"]
    raw = path.read_bytes()
    assert len(raw) == meta["byte_count"], identifier
    assert hash_chunks(raw) == meta["sha256"], identifier
    if path.suffix == ".json":
        value = json.loads(raw)
        assert raw == encode_json(value), identifier
        return raw, value
    return raw, None


def typed_row_digest() -> str:
    value = {
        "fields": ["ts_code", "trade_date", "total_mv", "note", "flag"],
        "values": [
            {"type": "string", "value": "600000.SH"},
            {"type": "string", "value": "20260715"},
            {"type": "number", "value": "00123.4500e+2"},
            {"type": "null", "value": None},
            {"type": "boolean", "value": True},
        ],
    }
    return hash_chunks(encode_json(value))


def verify_semantics(vectors: dict[str, Any], raw_vectors: dict[str, bytes]) -> None:
    matrix = vectors["001-frame-matrix-35"]
    assert len(matrix) == 35
    assert [call["ordinal"] for call in matrix] == list(range(1, 36))
    assert [call["api_name"] for call in matrix[-3:]] == ["stock_basic", "stock_basic", "daily_basic"]
    assert matrix[-1]["params"] == {"trade_date": "20260715"}
    assert all(call["api_name"] != "stock_st" for call in matrix)

    calendars = vectors["002-calendar-matrix-2"]
    assert [(item["ordinal"], item["params"]["exchange"]) for item in calendars] == [(1, "SSE"), (2, "SZSE")]
    assert all(
        item["params"]["start_date"] == "20260514" and item["params"]["end_date"] == "20260915" for item in calendars
    )

    capability = vectors["003-frame-authorization-capability"]
    capture = vectors["004-frame-authorization-capture"]
    date_auth = vectors["005-date-authorization"]
    assert capability["calls"] == capture["calls"] == matrix
    assert capability["capability_manifest_ref"] is capability["date_evidence_ref"] is None
    assert capture["capability_manifest_ref"]["attempt_id"] == "golden-capability"
    assert capture["date_evidence_ref"]["authorization_id"] == "auth-golden-date"
    assert date_auth["calls"] == calendars
    for auth in (capability, capture, date_auth):
        assert auth["protocol_sha256"] == PROTOCOL_HASH
        assert auth["supersedes_sha256"] == "f494c1973f002536874782af41221d37a2ca618c329fa48a2ae650b544c88042"
        assert auth["not_before"] == "2026-07-15T18:00:00+08:00"
        assert auth["not_after"] == "2026-07-15T23:59:59+08:00"

    assert vectors["006-request-description-daily"] == {
        "api_name": "daily_basic",
        "fields": ["ts_code", "trade_date", "total_mv"],
        "method": "POST",
        "origin": "https://api.tushare.pro",
        "params": {"trade_date": "20260715"},
    }

    receipts = [
        vectors[f"{number:03d}-{name}"]
        for number, name in (
            (7, "receipt-success-blob"),
            (8, "receipt-terminal-failure-blob"),
            (9, "receipt-terminal-failure-no-blob"),
        )
    ]
    assert [item["state"] for item in receipts] == [
        "success_blob",
        "terminal_failure_blob",
        "terminal_failure_no_blob",
    ]
    assert receipts[0]["blob_sha256"] and receipts[1]["blob_sha256"]
    assert receipts[2]["blob_sha256"] is receipts[2]["blob_relative_path"] is receipts[2]["byte_count"] is None
    inventory = vectors["010-unattempted-inventory"]
    assert inventory["expected_ordinals"] == list(range(1, 36))
    assert inventory["unattempted_ordinals"] == list(range(2, 36))

    assert vectors["011-stop-record"]["stop_code"] == "credential_unavailable"
    assert vectors["012-supervisor-commit-pass"]["outcome"] == "PASS"
    assert vectors["013-supervisor-commit-fail"]["outcome"] == "FAIL"
    assert vectors["012-supervisor-commit-pass"]["nonce"] == "0123456789abcdef" * 4

    assert vectors["014-ledger-empty"]["entries"] == []
    bse = vectors["015-ledger-bse"]["entries"][0]
    name_only = vectors["016-ledger-name-only-st"]["entries"][0]
    daily = vectors["017-ledger-daily-exact-unicode-null-sort"]["entries"][-1]
    assert (bse["source_kind"], bse["reason_code"]) == ("membership", "exchange_out_of_scope_bse")
    assert name_only["name"] == "  ＳＴ示例  "
    assert (name_only["source_kind"], name_only["reason_code"]) == ("stock_basic", "st_risk_warning")
    assert daily["source_ordinal"] == 35 and daily["name"] is None
    assert daily["canonical_row_sha256"] == typed_row_digest()

    evidence = vectors["018-date-pass-evidence"]
    assert evidence["sampling_date"] == "2026-07-15"
    assert evidence["next_common_open_date"] == "2026-07-16"
    assert [source["exchange"] for source in evidence["sources"]] == ["SSE", "SZSE"]

    for number in range(19, 27):
        identifier = next(key for key in vectors if key.startswith(f"{number:03d}-"))
        manifest = vectors[identifier]
        assert set(manifest) == MANIFEST_KEYS
        assert set(manifest["gate_results"]) == GATE_KEYS
        expected = set(manifest["expected_ordinals"])
        partition = (
            set(manifest["successful_ordinals"])
            | set(manifest["terminal_failed_ordinals"])
            | set(manifest["unattempted_ordinals"])
        )
        assert partition == expected
        assert not (set(manifest["successful_ordinals"]) & set(manifest["terminal_failed_ordinals"]))
        assert all(ref["relative_path"].endswith("_v132.py") for ref in manifest["generator_files"])
    assert vectors["019-manifest-capability-pass"]["disposition"] == "ELIGIBLE_FOR_FULL_CAPTURE_AUTHORIZATION"
    assert vectors["020-manifest-pretransport-fail"]["successful_ordinals"] == []
    assert vectors["021-manifest-terminal-blob-fail"]["failed_ordinal"] == 1
    assert vectors["022-manifest-complete-gate-fail"]["complete"] is True
    assert vectors["023-manifest-date-pass"]["date_evidence_ref"] is not None
    assert vectors["024-manifest-date-fail-no-evidence"]["date_evidence_ref"] is None
    assert vectors["025-manifest-capture-pass"]["capture_input_eligible"] is True
    assert vectors["026-manifest-capture-fail"]["capture_input_eligible"] is False

    assert vectors["027-closed-set-prospective"]["allowed_absent"] == ["supervisor-commit.json"]
    assert vectors["028-closed-set-final"]["allowed_absent"] == []
    checksum = raw_vectors["029-authorization-checksum-line"]
    expected_line = (
        hash_chunks(raw_vectors["003-frame-authorization-capability"])
        + "  golden-authorizations/frame-capability.json\n"
    ).encode("ascii")
    assert checksum == expected_line

    count_zero = vectors["030-provider-count-zero"]
    assert count_zero["data"]["count"] == len(count_zero["data"]["items"]) == 0

    schema_raw = (BASE / "schema-contract.json").read_bytes()
    schema = json.loads(schema_raw)
    assert schema_raw == encode_json(schema)
    assert schema["schema_version"] == "m4-v132-schema-contract-v1"
    assert schema["schemas"]["attempt_manifest"]["gate_fields"] == sorted(GATE_KEYS)
    assert schema["schemas"]["exclusion_ledger"]["enums"]["source_kind"] == ["membership", "stock_basic", "daily"]
    assert "stock_st" not in schema_raw.decode("utf-8")


def main() -> None:
    assert hash_chunks(PROTOCOL.read_bytes()) == PROTOCOL_HASH
    index_raw = (BASE / "golden-vectors.json").read_bytes()
    index = json.loads(index_raw)
    assert index_raw == encode_json(index)
    assert index["schema_version"] == "m4-segmented-rest-golden-index-v2"
    assert index["protocol_sha256"] == PROTOCOL_HASH
    assert index["vectors"] == EXPECTED

    values: dict[str, Any] = {}
    raws: dict[str, bytes] = {}
    for identifier in sorted(EXPECTED):
        raw, value = load_vector(identifier)
        raws[identifier] = raw
        if value is not None:
            values[identifier] = value
    verify_semantics(values, raws)
    print(f"PASS m4-v132-independent-crosscheck vectors={len(EXPECTED)} protocol_sha256={PROTOCOL_HASH}")


if __name__ == "__main__":
    main()
