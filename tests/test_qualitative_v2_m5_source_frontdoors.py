"""Offline tests for the authorization-bound official front-door capture."""

from __future__ import annotations

import hashlib
import json
import stat
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

import a_stock_tracker.qualitative.m5.source_frontdoors as frontdoors
from a_stock_tracker.qualitative.m5.data_auth import seal_data_authorization
from scripts.capture_qualitative_v2_m5_source_frontdoors import main as cli_main

SAMPLE = Path(__file__).parent / "fixtures" / "milestone005" / "sample.csv"
AUTHORIZATION_ID = "m5-data-readiness-test-01"
DATA_RUN_ID = "m5-data-test-01"
NOT_BEFORE = "2026-07-19T00:00:00+08:00"
NOT_AFTER = "2026-08-01T23:59:59+08:00"
ACTIVE_NOW = datetime.fromisoformat("2026-07-20T12:00:00+08:00")


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


def _result(url: str, *, body: bytes = b"official page", error: str | None = None) -> frontdoors.FetchResult:
    return frontdoors.FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200 if error is None else 503,
        content_type="text/html",
        body=body,
        http_attempts=1,
        redirect_count=0,
        error_class=error,
    )


def test_capture_is_private_create_only_and_hash_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    authorization, checksum = _authorization(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setattr(frontdoors, "PROJECT_ROOT", project_root)
    observed: list[str] = []

    def fake_fetch(url: str) -> frontdoors.FetchResult:
        observed.append(url)
        return _result(url, body=f"official:{url}".encode())

    result = frontdoors.capture_source_frontdoors(
        SAMPLE,
        authorization,
        checksum,
        now=ACTIVE_NOW,
        fetcher=fake_fetch,
    )

    assert observed == [url for _source, url in frontdoors.FRONT_DOOR_PAGES]
    assert result["validated"] is True
    assert result["source_count"] == result["success_count"] == 4
    assert result["technical_error_count"] == 0
    assert result["http_attempts"] == 4
    attempt_root = project_root / cast(str, result["attempt_root"])
    manifest = attempt_root / "manifest.json"
    raw = manifest.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == result["manifest_sha256"]
    payload = cast(dict[str, object], json.loads(raw))
    assert payload["authorization_id"] == AUTHORIZATION_ID
    assert payload["http_attempts"] == payload["search_http_budget_consumed"] == 4
    pages = cast(list[dict[str, object]], payload["pages"])
    assert [page["source"] for page in pages] == ["CNINFO", "SSE", "SZSE", "CNIPA"]
    assert all(page["status"] == "success" for page in pages)
    assert stat.S_IMODE(attempt_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(attempt_root.parent.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in attempt_root.iterdir())

    with pytest.raises(frontdoors.SourceFrontdoorError, match="already exists"):
        frontdoors.capture_source_frontdoors(
            SAMPLE,
            authorization,
            checksum,
            now=ACTIVE_NOW,
            fetcher=lambda _url: pytest.fail("same-day capture retried"),
        )


def test_capture_records_technical_response_without_claiming_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization, checksum = _authorization(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setattr(frontdoors, "PROJECT_ROOT", project_root)
    failed_url = frontdoors.FRONT_DOOR_PAGES[2][1]

    def fake_fetch(url: str) -> frontdoors.FetchResult:
        return _result(url, body=b"service unavailable", error="http_503") if url == failed_url else _result(url)

    result = frontdoors.capture_source_frontdoors(
        SAMPLE,
        authorization,
        checksum,
        now=ACTIVE_NOW,
        fetcher=fake_fetch,
    )

    assert result["success_count"] == 3
    assert result["technical_error_count"] == 1
    payload = json.loads((project_root / cast(str, result["attempt_root"]) / "manifest.json").read_bytes())
    failed = next(page for page in payload["pages"] if page["source"] == "SZSE")
    assert failed["status"] == "technical_error"
    assert failed["sanitized_error_class"] == "http_503"
    assert failed["raw_sha256"] == hashlib.sha256(b"service unavailable").hexdigest()


def test_ui_assets_bind_the_sealed_successful_frontdoors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    authorization, checksum = _authorization(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setattr(frontdoors, "PROJECT_ROOT", project_root)
    asset_by_source = {source: url for source, url, _filename in frontdoors.UI_ASSETS}

    def frontdoor_fetch(url: str) -> frontdoors.FetchResult:
        source = next(source for source, page_url in frontdoors.FRONT_DOOR_PAGES if page_url == url)
        body = asset_by_source.get(source, "no controller").encode()
        return _result(url, body=body)

    frontdoor = frontdoors.capture_source_frontdoors(
        SAMPLE,
        authorization,
        checksum,
        now=ACTIVE_NOW,
        fetcher=frontdoor_fetch,
    )

    result = frontdoors.capture_source_ui_assets(
        SAMPLE,
        authorization,
        checksum,
        now=ACTIVE_NOW,
        fetcher=lambda url: _result(url, body=f"controller:{url}".encode()),
    )

    assert result["asset_count"] == result["success_count"] == 2
    assert result["technical_error_count"] == 0
    assert result["http_attempts"] == 2
    assert result["parent_frontdoors_manifest_sha256"] == frontdoor["manifest_sha256"]
    attempt_root = project_root / cast(str, result["attempt_root"])
    payload = json.loads((attempt_root / "manifest.json").read_bytes())
    assert payload["parent_frontdoors_manifest_sha256"] == frontdoor["manifest_sha256"]
    assert [asset["source"] for asset in payload["assets"]] == ["CNINFO", "SSE"]
    assert stat.S_IMODE(attempt_root.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in attempt_root.iterdir())


def test_inactive_window_and_cli_without_flag_make_zero_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    authorization, checksum = _authorization(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setattr(frontdoors, "PROJECT_ROOT", project_root)

    with pytest.raises(ValueError, match="not_yet_valid"):
        frontdoors.capture_source_frontdoors(
            SAMPLE,
            authorization,
            checksum,
            now=datetime.fromisoformat("2026-07-18T23:59:59+08:00"),
            fetcher=lambda _url: pytest.fail("network called before active window"),
        )
    assert not (project_root / "artifacts").exists()

    monkeypatch.setattr(
        frontdoors,
        "fetch_official_frontdoor",
        lambda _url: pytest.fail("network called without execute flag"),
    )
    assert (
        cli_main(
            [
                "--sample",
                str(SAMPLE),
                "--authorization",
                str(authorization),
                "--checksum",
                str(checksum),
            ]
        )
        == 2
    )
    assert "requires --execute-network" in capsys.readouterr().err


def test_mismatched_or_non_https_transport_identity_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization, checksum = _authorization(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setattr(frontdoors, "PROJECT_ROOT", project_root)

    def bad_fetch(url: str) -> frontdoors.FetchResult:
        return frontdoors.FetchResult(url, "https://evil.example/", 200, "text/html", b"bad", 1, 0)

    with pytest.raises(frontdoors.SourceFrontdoorError, match="mismatched source identity"):
        frontdoors.capture_source_frontdoors(
            SAMPLE,
            authorization,
            checksum,
            now=ACTIVE_NOW,
            fetcher=bad_fetch,
        )

    with pytest.raises(frontdoors.SourceFrontdoorError, match="non-official HTTPS"):
        frontdoors._official_host("CNINFO", "http://www.cninfo.com.cn/new/fulltextSearch")
