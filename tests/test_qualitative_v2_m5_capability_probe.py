"""Offline tests for the M5 v1.2 three-axis source-capability probe."""

from __future__ import annotations

import json
import socket
import stat
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

import a_stock_tracker.qualitative.m5.capability_probe as capability
import a_stock_tracker.qualitative.m5.source_frontdoors as frontdoors
from a_stock_tracker.qualitative.m5.data_auth import seal_data_authorization
from scripts.probe_qualitative_v2_m5_source_capability import main as cli_main

SAMPLE = Path(__file__).parent / "fixtures" / "milestone005" / "sample.csv"
AUTHORIZATION_ID = "m5-capability-test-01"
DATA_RUN_ID = "m5-capability-data-01"
NOT_BEFORE = "2026-07-19T00:00:00+08:00"
NOT_AFTER = "2026-08-01T23:59:59+08:00"
ACTIVE_NOW = datetime.fromisoformat("2026-07-20T12:00:00+08:00")
CNINFO_CONTROLLER = """
var searchTypeList = [{value: 1, key: '标题+全文'}];
var pageSize: 20;
var endpoint = '/fulltextSearch/full';
var payload = {isfulltext: self.searchType == 0 ? false : true};
""".encode()
SSE_CONTROLLER = b"""
var announceUrl = 'security/stock/queryCompanyBulletinNew.do';
var announceParam = {TITLE: ""};
announceParam.BULLETIN_TYPE = announceParam.BULLETIN_TYPE.replace('hasDelMain','');
"""


def _authorization(tmp_path: Path) -> tuple[Path, Path]:
    authorization = tmp_path / "authorization.json"
    checksum = tmp_path / "authorization.sha256"
    seal_data_authorization(
        SAMPLE,
        authorization,
        checksum,
        authorization_id=AUTHORIZATION_ID,
        data_run_id=DATA_RUN_ID,
        not_before=NOT_BEFORE,
        not_after=NOT_AFTER,
    )
    return authorization, checksum


def _fetch_result(
    url: str,
    *,
    body: bytes,
    status_code: int | None = 200,
    error: str | None = None,
) -> frontdoors.FetchResult:
    return frontdoors.FetchResult(
        requested_url=url,
        final_url=url,
        status_code=status_code,
        content_type="application/javascript" if url.endswith(".js") else "text/html",
        body=body,
        http_attempts=1,
        redirect_count=0,
        error_class=error,
    )


def _inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cninfo_controller: bytes = CNINFO_CONTROLLER,
) -> tuple[Path, Path, Path, Path, Path]:
    authorization, checksum = _authorization(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setattr(frontdoors, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(capability, "PROJECT_ROOT", project_root)
    asset_by_source = {source: url for source, url, _filename in frontdoors.UI_ASSETS}

    def page_fetch(url: str) -> frontdoors.FetchResult:
        source = next(source for source, page_url in frontdoors.FRONT_DOOR_PAGES if page_url == url)
        if source == "SZSE":
            return _fetch_result(url, body=b"", status_code=None, error="URLError")
        if source == "CNIPA":
            return _fetch_result(url, body=b"precondition", status_code=412, error="http_412")
        return _fetch_result(url, body=asset_by_source[source].encode())

    frontdoor_result = frontdoors.capture_source_frontdoors(
        SAMPLE,
        authorization,
        checksum,
        now=ACTIVE_NOW,
        fetcher=page_fetch,
    )

    def asset_fetch(url: str) -> frontdoors.FetchResult:
        source = next(source for source, asset_url, _filename in frontdoors.UI_ASSETS if asset_url == url)
        body = cninfo_controller if source == "CNINFO" else SSE_CONTROLLER
        return _fetch_result(url, body=body)

    asset_result = frontdoors.capture_source_ui_assets(
        SAMPLE,
        authorization,
        checksum,
        now=ACTIVE_NOW,
        fetcher=asset_fetch,
    )
    frontdoor_manifest = project_root / cast(str, frontdoor_result["attempt_root"]) / "manifest.json"
    asset_manifest = project_root / cast(str, asset_result["attempt_root"]) / "manifest.json"
    return project_root, authorization, checksum, frontdoor_manifest, asset_manifest


def _report(inputs: tuple[Path, Path, Path, Path, Path]) -> dict[str, object]:
    _project_root, authorization, checksum, frontdoors_path, assets_path = inputs
    return capability.capability_report(SAMPLE, authorization, checksum, frontdoors_path, assets_path)


def test_probe_separates_transport_semantics_and_quality_without_global_source_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _report(_inputs(tmp_path, monkeypatch))
    sources = {row["source"]: row for row in cast(list[dict[str, object]], report["sources"])}

    assert cast(dict[str, object], sources["CNINFO"]["transport"])["status"] == "available"
    assert cast(dict[str, object], sources["CNINFO"]["full_text_semantics"])["status"] == "demonstrated"
    assert sources["CNINFO"]["candidate_fulltext_collection_eligible"] is True
    assert cast(dict[str, object], sources["SSE"]["transport"])["status"] == "available"
    assert cast(dict[str, object], sources["SSE"]["full_text_semantics"])["status"] == "not_demonstrated"
    assert cast(dict[str, object], sources["SZSE"]["transport"])["status"] == "technical_error"
    assert cast(dict[str, object], sources["CNIPA"]["transport"])["status"] == "technical_error"
    assert all(
        cast(dict[str, object], row["evidence_quality"])["status"] == "not_evaluated" for row in sources.values()
    )

    routing = cast(dict[str, object], report["routing"])
    assert routing["viable_fulltext_sources"] == ["CNINFO"]
    assert routing["minimum_viable_official_fulltext_route"] is True
    assert routing["single_source_failure_global_block"] is False
    assert routing["global_capability_blocked"] is False
    lifecycle = cast(dict[str, object], report["lifecycle"])
    assert lifecycle["current_v1_1_run_resumable"] is False
    assert lifecycle["collection_protocol_frozen"] is False
    assert lifecycle["protocol_freeze_ready"] is False
    assert report["network_calls_performed"] == report["database_reads_performed"] == 0
    assert report["model_calls_performed"] == report["reviewer_calls_performed"] == 0


def test_probe_globally_blocks_only_when_no_official_fulltext_route_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _report(_inputs(tmp_path, monkeypatch, cninfo_controller=b"title search only"))
    routing = cast(dict[str, object], report["routing"])

    assert routing["viable_fulltext_sources"] == []
    assert routing["minimum_viable_official_fulltext_route"] is False
    assert routing["global_capability_blocked"] is True


def test_probe_rejects_controller_hash_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _inputs(tmp_path, monkeypatch)
    _project_root, authorization, checksum, frontdoor_manifest, asset_manifest = inputs
    value = json.loads(asset_manifest.read_bytes())
    cninfo_path = asset_manifest.parent / value["assets"][0]["raw_path"]
    cninfo_path.write_bytes(b"tampered")

    with pytest.raises(capability.CapabilityProbeError, match="hash or size drift"):
        capability.capability_report(SAMPLE, authorization, checksum, frontdoor_manifest, asset_manifest)


def test_probe_rejects_rewritten_cross_origin_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _inputs(tmp_path, monkeypatch)
    _project_root, authorization, checksum, frontdoor_manifest, asset_manifest = inputs
    value = json.loads(frontdoor_manifest.read_bytes())
    value["pages"][0]["final_url"] = "https://evil.example/"
    frontdoor_manifest.write_text(
        json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(capability.CapabilityProbeError, match="same-origin HTTPS"):
        capability.capability_report(SAMPLE, authorization, checksum, frontdoor_manifest, asset_manifest)


def test_preview_is_zero_artifact_and_seal_is_private_create_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_root, authorization, checksum, frontdoor_manifest, asset_manifest = _inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("network access"))
    common = [
        "--sample",
        str(SAMPLE),
        "--authorization",
        str(authorization),
        "--checksum",
        str(checksum),
        "--frontdoors",
        str(frontdoor_manifest),
        "--ui-assets",
        str(asset_manifest),
    ]
    output = (
        project_root
        / "artifacts"
        / "milestone-005"
        / "data"
        / DATA_RUN_ID
        / "capability-probe-v1.2"
        / capability.REPORT_FILENAME
    )

    assert cli_main(["preview", *common]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["report"]["routing"]["viable_fulltext_sources"] == ["CNINFO"]
    assert not output.exists()

    assert cli_main(["seal", *common, "--output", str(output)]) == 0
    sealed = json.loads(capsys.readouterr().out)
    assert sealed["validated"] is True
    assert sealed["collection_protocol_frozen"] is False
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(output.stat().st_mode) == 0o600

    assert cli_main(["seal", *common, "--output", str(output)]) == 2
    assert "already exists" in capsys.readouterr().err
