"""Offline tests for the authorization-bound CNINFO structural-quality pilot."""

from __future__ import annotations

import hashlib
import json
import stat
from datetime import datetime
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest

import qualitative_v2_m5_quality_pilot as pilot
from qualitative_v2_m5 import EXPECTED_SAMPLE_SHA256, _canonical_json
from qualitative_v2_m5_quality_pilot_auth import seal_quality_pilot_authorization
from qualitative_v2_m5_source_frontdoors import FetchResult
from scripts.run_qualitative_v2_m5_quality_pilot import main as cli_main

SAMPLE = Path(__file__).parent / "fixtures" / "milestone005" / "sample.csv"
ACTIVE_D = datetime.fromisoformat("2026-07-20T12:00:00+08:00")
ACTIVE_D1 = datetime.fromisoformat("2026-07-21T12:00:00+08:00")

COMPANIES = {
    "002807": "江阴银行",
    "301057": "汇隆新材",
    "603507": "振江股份",
    "300531": "优博讯",
    "002084": "海鸥住工",
    "002412": "汉森制药",
}


def _canonical_bytes(value: object) -> bytes:
    return (_canonical_json(value) + "\n").encode("utf-8")


def _capability_report(tmp_path: Path) -> tuple[Path, str]:
    sources = []
    for source in ("CNINFO", "SSE", "SZSE", "CNIPA"):
        eligible = source == "CNINFO"
        sources.append(
            {
                "source": source,
                "transport": {"status": "available" if eligible else "technical_error"},
                "full_text_semantics": {"status": "demonstrated" if eligible else "not_evaluated"},
                "evidence_quality": {"status": "not_evaluated"},
                "candidate_fulltext_collection_eligible": eligible,
            }
        )
    value = {
        "schema_version": "m5-source-capability-probe-v1.2",
        "sample_sha256": EXPECTED_SAMPLE_SHA256,
        "sources": sources,
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
    path = tmp_path / "capability.json"
    raw = _canonical_bytes(value)
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def _authorization(tmp_path: Path) -> tuple[Path, Path, Path]:
    capability, capability_sha = _capability_report(tmp_path)
    authorization = tmp_path / "authorization.json"
    checksum = tmp_path / "authorization.sha256"
    seal_quality_pilot_authorization(
        SAMPLE,
        capability,
        authorization,
        checksum,
        capability_report_sha256=capability_sha,
        authorization_id="m5-evidence-quality-pilot-test-01",
        pilot_run_id="m5-evidence-quality-pilot-test-run-01",
        not_before="2026-07-20T00:00:00+08:00",
        not_after="2026-07-22T23:59:59+08:00",
    )
    return capability, authorization, checksum


def _result(url: str, body: bytes, *, error: str | None = None, attempts: int = 1) -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200 if error is None else 503,
        content_type="application/json" if "/fulltextSearch/full?" in url else "application/pdf",
        body=body,
        http_attempts=attempts,
        redirect_count=attempts - 1,
        error_class=error,
    )


def _search_body(code: str, *, mismatched: bool = False) -> bytes:
    actual_code = "999999" if mismatched else code
    actual_name = "错误公司" if mismatched else COMPANIES[code]
    return json.dumps(
        {
            "announcements": [
                {
                    "secCode": actual_code,
                    "secName": actual_name,
                    "announcementId": f"{code}-{rank}",
                    "announcementTitle": f"{actual_name} 核心技术公告 {rank}",
                    "announcementTime": "2026-06-01 09:00:00",
                    "adjunctUrl": f"finalpage/2026-06-01/{code}-{rank}.PDF",
                }
                for rank in range(1, 4)
            ]
        },
        ensure_ascii=False,
    ).encode()


def _code_from_search_url(url: str) -> str:
    searchkey = parse_qs(urlsplit(url).query)["searchkey"][0]
    return searchkey.split(",", 1)[0]


def _successful_fetch(url: str) -> FetchResult:
    if "/fulltextSearch/full?" in url:
        return _result(url, _search_body(_code_from_search_url(url)))
    return _result(url, f"official document:{url}".encode())


def test_successful_pilot_is_private_bounded_and_structural_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability, authorization, checksum = _authorization(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(pilot, "PROJECT_ROOT", project)
    observed: list[str] = []

    def fetch(url: str) -> FetchResult:
        observed.append(url)
        return _successful_fetch(url)

    result = pilot.run_quality_pilot_day(SAMPLE, capability, authorization, checksum, now=ACTIVE_D, fetcher=fetch)

    assert result["status"] == "COMPLETE"
    assert result["query_groups_successful"] == 6
    assert result["unique_documents_discovered"] == result["documents_successful"] == 18
    assert result["daily_http_attempts"] == result["cumulative_http_attempts"] == 24
    assert len(observed) == 24
    assert result["semantic_quality_authorized"] is False
    assert result["collection_protocol_frozen"] is False
    run_root = project / "artifacts/milestone-005/quality-pilot/m5-evidence-quality-pilot-test-run-01"
    attempt_root = run_root / "attempts/20260720"
    report = cast(dict[str, object], json.loads((run_root / pilot.REPORT_FILENAME).read_bytes()))
    assert report["status"] == "COMPLETE"
    assert report["quality_scope"] == "structural_only"
    assert report["http_attempts"] == 24
    assert report["documents_with_mime"] == report["documents_with_raw_sha256"] == 18
    assert len(cast(list[object], report["search_attempts"])) == 6
    assert len(cast(list[object], report["document_attempts"])) == 18
    assert isinstance(result["report_sha256"], str)
    verified = pilot.load_quality_pilot_report(SAMPLE, capability, authorization, checksum)
    assert verified["validated"] is True
    assert verified["report_sha256"] == result["report_sha256"]
    assert verified["network_calls_performed"] == 0
    assert stat.S_IMODE(run_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(attempt_root.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in attempt_root.rglob("*") if path.is_file())

    with pytest.raises(pilot.QualityPilotError, match="already exists|already terminal"):
        pilot.run_quality_pilot_day(
            SAMPLE, capability, authorization, checksum, now=ACTIVE_D, fetcher=lambda _url: pytest.fail("retried")
        )


def test_transient_search_failure_retries_only_on_next_authorized_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability, authorization, checksum = _authorization(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(pilot, "PROJECT_ROOT", project)
    failed_code = "603507"

    def day_zero(url: str) -> FetchResult:
        if "/fulltextSearch/full?" in url and _code_from_search_url(url) == failed_code:
            return _result(url, b"unavailable", error="http_503")
        return _successful_fetch(url)

    first = pilot.run_quality_pilot_day(SAMPLE, capability, authorization, checksum, now=ACTIVE_D, fetcher=day_zero)
    assert first["status"] == "PROVISIONAL"
    assert first["query_groups_successful"] == 5
    assert first["unique_documents_discovered"] == first["documents_successful"] == 15
    assert first["daily_http_attempts"] == 21

    observed: list[str] = []

    def day_one(url: str) -> FetchResult:
        observed.append(url)
        return _successful_fetch(url)

    second = pilot.run_quality_pilot_day(SAMPLE, capability, authorization, checksum, now=ACTIVE_D1, fetcher=day_one)
    assert second["status"] == "COMPLETE"
    assert second["daily_http_attempts"] == 4
    assert second["cumulative_http_attempts"] == 25
    assert len(observed) == 4
    assert _code_from_search_url(observed[0]) == failed_code


def test_company_mismatch_is_terminal_and_skips_remaining_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability, authorization, checksum = _authorization(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(pilot, "PROJECT_ROOT", project)
    observed: list[str] = []

    def mismatch(url: str) -> FetchResult:
        observed.append(url)
        return _result(url, _search_body(_code_from_search_url(url), mismatched=True))

    result = pilot.run_quality_pilot_day(SAMPLE, capability, authorization, checksum, now=ACTIVE_D, fetcher=mismatch)
    assert result["status"] == "FAIL"
    assert result["daily_http_attempts"] == 1
    assert len(observed) == 1
    assert result["report_created"] is True


def test_inactive_window_and_cli_without_flag_make_zero_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    capability, authorization, checksum = _authorization(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(pilot, "PROJECT_ROOT", project)
    with pytest.raises(pilot.QualityPilotError, match="not active"):
        pilot.run_quality_pilot_day(
            SAMPLE,
            capability,
            authorization,
            checksum,
            now=datetime.fromisoformat("2026-07-19T23:59:59+08:00"),
            fetcher=lambda _url: pytest.fail("network before window"),
        )
    assert not (project / "artifacts").exists()

    assert (
        cli_main(
            [
                "--sample",
                str(SAMPLE),
                "--capability-report",
                str(capability),
                "--authorization",
                str(authorization),
                "--checksum",
                str(checksum),
            ]
        )
        == 2
    )
    assert "select exactly one" in capsys.readouterr().err


def test_prior_raw_tamper_blocks_next_date_before_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capability, authorization, checksum = _authorization(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(pilot, "PROJECT_ROOT", project)

    def one_failure(url: str) -> FetchResult:
        if "/fulltextSearch/full?" in url and _code_from_search_url(url) == "603507":
            return _result(url, b"unavailable", error="http_503")
        return _successful_fetch(url)

    pilot.run_quality_pilot_day(SAMPLE, capability, authorization, checksum, now=ACTIVE_D, fetcher=one_failure)
    raw_path = (
        project
        / "artifacts/milestone-005/quality-pilot/m5-evidence-quality-pilot-test-run-01/attempts/20260720/search/001.bin"
    )
    raw_path.write_bytes(b"tampered")
    with pytest.raises(pilot.QualityPilotError, match="hash or size drift"):
        pilot.run_quality_pilot_day(
            SAMPLE, capability, authorization, checksum, now=ACTIVE_D1, fetcher=lambda _url: pytest.fail("network")
        )


def test_http_attempt_budget_is_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capability, authorization, checksum = _authorization(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(pilot, "PROJECT_ROOT", project)

    def excessive(url: str) -> FetchResult:
        return _result(url, _search_body(_code_from_search_url(url)), attempts=73)

    with pytest.raises(pilot.QualityPilotError, match="attempt budget exceeded"):
        pilot.run_quality_pilot_day(SAMPLE, capability, authorization, checksum, now=ACTIVE_D, fetcher=excessive)


def test_forged_report_is_rejected_even_with_recomputed_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability, authorization, checksum = _authorization(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(pilot, "PROJECT_ROOT", project)
    pilot.run_quality_pilot_day(SAMPLE, capability, authorization, checksum, now=ACTIVE_D, fetcher=_successful_fetch)
    run_root = project / "artifacts/milestone-005/quality-pilot/m5-evidence-quality-pilot-test-run-01"
    report_path = run_root / pilot.REPORT_FILENAME
    report = cast(dict[str, object], json.loads(report_path.read_bytes()))
    report["status"] = "FAIL"
    raw = _canonical_bytes(report)
    report_path.write_bytes(raw)
    (run_root / pilot.REPORT_CHECKSUM_FILENAME).write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {pilot.REPORT_FILENAME}\n", encoding="ascii"
    )
    with pytest.raises(pilot.QualityPilotError, match="drifts from the sealed raw attempt chain"):
        pilot.load_quality_pilot_report(SAMPLE, capability, authorization, checksum)
