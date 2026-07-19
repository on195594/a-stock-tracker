"""Zero-call tests for the bounded M5 v1.2 evidence-quality pilot authorization."""

from __future__ import annotations

import hashlib
import json
import socket
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

import a_stock_tracker.qualitative.m5.quality_pilot_auth as auth
from a_stock_tracker.qualitative.m5.pipeline import EXPECTED_SAMPLE_SHA256, _canonical_json, _load_sample
from scripts.prepare_qualitative_v2_m5_quality_pilot import main as cli_main

SAMPLE = Path(__file__).parent / "fixtures" / "milestone005" / "sample.csv"
AUTHORIZATION_ID = "m5-evidence-quality-pilot-test-01"
PILOT_RUN_ID = "m5-evidence-quality-pilot-test-run-01"
NOT_BEFORE = "2026-07-20T00:00:00+08:00"
NOT_AFTER = "2026-07-22T23:59:59+08:00"


def _canonical_bytes(value: object) -> bytes:
    return (_canonical_json(value) + "\n").encode("utf-8")


def _capability_value() -> dict[str, object]:
    source_rows = []
    for source in ("CNINFO", "SSE", "SZSE", "CNIPA"):
        eligible = source == "CNINFO"
        source_rows.append(
            {
                "source": source,
                "transport": {"status": "available" if eligible else "technical_error"},
                "full_text_semantics": {"status": "demonstrated" if eligible else "not_evaluated"},
                "evidence_quality": {"status": "not_evaluated"},
                "candidate_fulltext_collection_eligible": eligible,
            }
        )
    return {
        "schema_version": auth.CAPABILITY_REPORT_SCHEMA_VERSION,
        "sample_sha256": EXPECTED_SAMPLE_SHA256,
        "sources": source_rows,
        "routing": {
            "viable_fulltext_sources": ["CNINFO"],
            "minimum_viable_official_fulltext_route": True,
            "global_capability_blocked": False,
        },
        "lifecycle": {
            "collection_protocol_frozen": False,
            "protocol_freeze_ready": False,
            "next_action": "authorize_bounded_evidence_quality_pilot",
        },
        "network_calls_performed": 0,
        "database_reads_performed": 0,
        "credential_variables_read": 0,
        "reviewer_calls_performed": 0,
        "model_calls_performed": 0,
    }


def _write_capability(tmp_path: Path, value: dict[str, object] | None = None) -> tuple[Path, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "capability-report.json"
    raw = _canonical_bytes(value or _capability_value())
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def _seal(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    report, report_sha256 = _write_capability(tmp_path)
    authorization = tmp_path / "authorization.json"
    checksum = tmp_path / "authorization.sha256"
    auth.seal_quality_pilot_authorization(
        SAMPLE,
        report,
        authorization,
        checksum,
        capability_report_sha256=report_sha256,
        authorization_id=AUTHORIZATION_ID,
        pilot_run_id=PILOT_RUN_ID,
        not_before=NOT_BEFORE,
        not_after=NOT_AFTER,
    )
    return report, authorization, checksum, report_sha256


def test_query_matrix_selects_exactly_first_company_per_super_stratum(tmp_path: Path) -> None:
    report, report_sha256 = _write_capability(tmp_path)
    _sample_sha256, companies = _load_sample(SAMPLE)
    matrix = auth.query_matrix(companies)
    value = auth.authorization_value(
        SAMPLE,
        report,
        capability_report_sha256=report_sha256,
        authorization_id=AUTHORIZATION_ID,
        pilot_run_id=PILOT_RUN_ID,
        not_before=NOT_BEFORE,
        not_after=NOT_AFTER,
    )

    assert [row["code"] for row in matrix] == [row[0] for row in auth.EXPECTED_PILOT_COMPANIES]
    assert [row["super_stratum"] for row in matrix] == list(auth.SUPER_STRATA)
    assert matrix[0]["searchkey"] == "002807,核心技术"
    assert matrix[0]["sdate"] == "2025-07-18"
    assert matrix[0]["edate"] == "2026-07-17"
    assert matrix[-1]["retained_default_ranks"] == [1, 2, 3]
    assert len(matrix) == value["query_group_count"] == 6
    assert value["max_unique_documents"] == 18
    assert value["max_search_http_attempts"] == 18
    assert value["max_document_http_attempts"] == 54
    assert value["max_http_attempts_total"] == 72
    assert value["allowed_hosts"] == ["www.cninfo.com.cn", "static.cninfo.com.cn"]
    assert value["attempt_local_dates"] == ["2026-07-20", "2026-07-21", "2026-07-22"]
    assert value["quality_scope"] == "structural_only"
    assert value["semantic_quality_authorized"] is False
    assert value["collection_protocol_frozen"] is False
    assert value["allow_database_read"] is False
    assert value["allow_models"] is False


def test_capability_hash_and_semantics_are_both_fail_closed(tmp_path: Path) -> None:
    report, report_sha256 = _write_capability(tmp_path)
    with pytest.raises(auth.QualityPilotAuthorizationError, match="SHA-256 drift"):
        auth.authorization_value(
            SAMPLE,
            report,
            capability_report_sha256="0" * 64,
            authorization_id=AUTHORIZATION_ID,
            pilot_run_id=PILOT_RUN_ID,
            not_before=NOT_BEFORE,
            not_after=NOT_AFTER,
        )

    value = _capability_value()
    cast(dict[str, object], value["routing"])["viable_fulltext_sources"] = []
    raw = _canonical_bytes(value)
    report.write_bytes(raw)
    with pytest.raises(auth.QualityPilotAuthorizationError, match="candidate route"):
        auth.authorization_value(
            SAMPLE,
            report,
            capability_report_sha256=hashlib.sha256(raw).hexdigest(),
            authorization_id=AUTHORIZATION_ID,
            pilot_run_id=PILOT_RUN_ID,
            not_before=NOT_BEFORE,
            not_after=NOT_AFTER,
        )
    assert report_sha256 != hashlib.sha256(raw).hexdigest()


def test_evidence_quality_cannot_be_self_asserted_as_evaluated(tmp_path: Path) -> None:
    value = _capability_value()
    sources = cast(list[dict[str, object]], value["sources"])
    cast(dict[str, object], sources[0]["evidence_quality"])["status"] = "passed"
    report, report_sha256 = _write_capability(tmp_path, value)
    with pytest.raises(auth.QualityPilotAuthorizationError, match="capability axes"):
        auth.authorization_value(
            SAMPLE,
            report,
            capability_report_sha256=report_sha256,
            authorization_id=AUTHORIZATION_ID,
            pilot_run_id=PILOT_RUN_ID,
            not_before=NOT_BEFORE,
            not_after=NOT_AFTER,
        )


def test_preview_is_zero_external_access_and_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report, report_sha256 = _write_capability(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-read")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-read")
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("network access"))
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: pytest.fail("database access"))
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}

    assert (
        cli_main(
            [
                "preview",
                "--sample",
                str(SAMPLE),
                "--capability-report",
                str(report),
                "--capability-report-sha256",
                report_sha256,
                "--authorization-id",
                AUTHORIZATION_ID,
                "--pilot-run-id",
                PILOT_RUN_ID,
                "--not-before",
                NOT_BEFORE,
                "--not-after",
                NOT_AFTER,
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    preview = json.loads(output.out)
    assert preview["authorization"]["max_http_attempts_total"] == 72
    assert "must-not-read" not in output.out + output.err
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_seal_load_and_preflight_are_private_create_only_and_zero_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("network access"))
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: pytest.fail("database access"))
    report, authorization, checksum, report_sha256 = _seal(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}

    result = auth.preflight_quality_pilot_authorization(
        SAMPLE,
        report,
        authorization,
        checksum,
        now=datetime.fromisoformat("2026-07-21T12:00:00+08:00"),
        require_active=True,
    )
    assert result["validated"] is True
    assert result["window_state"] == "active"
    assert result["network_calls_performed"] == 0
    assert result["database_reads_performed"] == 0
    assert result["credential_variables_read"] == 0
    assert result["artifacts_created"] == 0
    assert authorization.stat().st_mode & 0o777 == 0o600
    assert checksum.stat().st_mode & 0o777 == 0o600
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == before

    with pytest.raises(auth.QualityPilotAuthorizationError, match="already exists"):
        auth.seal_quality_pilot_authorization(
            SAMPLE,
            report,
            authorization,
            checksum,
            capability_report_sha256=report_sha256,
            authorization_id=AUTHORIZATION_ID,
            pilot_run_id=PILOT_RUN_ID,
            not_before=NOT_BEFORE,
            not_after=NOT_AFTER,
        )


def test_authorization_and_capability_tampering_are_detected_after_seal(tmp_path: Path) -> None:
    report, authorization, checksum, _report_sha256 = _seal(tmp_path)
    value = cast(dict[str, object], json.loads(authorization.read_text(encoding="utf-8")))
    value["allow_models"] = True
    raw = _canonical_bytes(value)
    authorization.write_bytes(raw)
    checksum.write_text(f"{hashlib.sha256(raw).hexdigest()}  {authorization.name}\n", encoding="ascii")
    with pytest.raises(auth.QualityPilotAuthorizationError, match="drifts from the frozen boundary"):
        auth.load_quality_pilot_authorization(SAMPLE, report, authorization, checksum)

    report, authorization, checksum, _report_sha256 = _seal(tmp_path / "second")
    capability = cast(dict[str, object], json.loads(report.read_text(encoding="utf-8")))
    capability["network_calls_performed"] = 1
    report.write_bytes(_canonical_bytes(capability))
    with pytest.raises(auth.QualityPilotAuthorizationError, match="SHA-256 drift"):
        auth.load_quality_pilot_authorization(SAMPLE, report, authorization, checksum)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ("2026-07-19T23:59:59+08:00", "not_yet_valid"),
        ("2026-07-23T00:00:00+08:00", "expired"),
    ],
)
def test_preflight_window_states(tmp_path: Path, now: str, expected: str) -> None:
    report, authorization, checksum, _report_sha256 = _seal(tmp_path)
    result = auth.preflight_quality_pilot_authorization(
        SAMPLE, report, authorization, checksum, now=datetime.fromisoformat(now)
    )
    assert result["window_state"] == expected
    with pytest.raises(auth.QualityPilotAuthorizationError, match=expected):
        auth.preflight_quality_pilot_authorization(
            SAMPLE,
            report,
            authorization,
            checksum,
            now=datetime.fromisoformat(now),
            require_active=True,
        )
