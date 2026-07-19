"""Offline tests for the bounded production CNINFO context collector."""

from __future__ import annotations

import json
import sqlite3
import stat
import urllib.parse
from datetime import date
from pathlib import Path

import pytest

from a_stock_tracker.qualitative.production_contexts import (
    AUTHORIZATION_LEDGER_FILENAME,
    ContextCollectionError,
    ProductionContextAuthorization,
    SnippetFetchResult,
    collect_production_contexts,
    create_context_run_root,
    load_active_authorization,
    load_prior_http_attempts,
)
from a_stock_tracker.qualitative.validator import validate_context_dict

COMPANIES = {
    "000963": ("华东医药", "医药制造"),
    "002594": ("比亚迪", "汽车制造"),
    "600036": ("招商银行", "银行"),
    "600941": ("中国移动", "通信运营"),
    "601088": ("中国神华", "煤炭"),
}
TEST_AUTHORIZATION = ProductionContextAuthorization("test-production-canary-01", "canary", 45)


def _db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE stock_fundamentals (code TEXT PRIMARY KEY, data TEXT)")
    for code in COMPANIES:
        connection.execute(
            "INSERT INTO stock_fundamentals VALUES (?, ?)",
            (code, json.dumps({"roe_3y_avg": 12.5, "report_period": "2025"})),
        )
    return connection


def _success_fetcher(url: str) -> SnippetFetchResult:
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    name, keyword = query["searchkey"][0].rsplit(" ", 1)
    code = next(code for code, identity in COMPANIES.items() if identity[0] == name)
    content = {
        "核心技术": "公司持续投入核心技术研发并形成产品优势。",
        "市场占有率": "公司披露市场占有率保持行业前列。",
        "合同期限": "重大客户合同期限为3年，正在持续履行。",
    }[keyword]
    body = json.dumps(
        {
            "announcements": [
                {
                    "secCode": code,
                    "secName": name,
                    "announcementId": f"{code}-{keyword}",
                    "announcementTitle": f"{name}{keyword}公告",
                    "announcementContent": content,
                    "announcementTime": "2026-07-10",
                }
            ]
        },
        ensure_ascii=False,
    ).encode()
    return SnippetFetchResult(url, url, 200, "application/json", body)


def test_collects_exact_canary_with_sealed_valid_contexts(tmp_path: Path) -> None:
    root = create_context_run_root(tmp_path, "canary-01")
    result = collect_production_contexts(
        _db(),
        COMPANIES,
        authorization=TEST_AUTHORIZATION,
        scope="canary",
        run_root=root,
        as_of_date=date(2026, 7, 19),
        fetcher=_success_fetcher,
        sleep=lambda _seconds: None,
    )
    assert result["http_attempts"] == 15
    assert result["queries"] == 15
    assert result["queries_with_evidence"] == 15
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    for path in root.rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    for code in COMPANIES:
        raw = json.loads((root / "contexts" / f"{code}.json").read_text(encoding="utf-8"))
        validation = validate_context_dict(raw)
        assert validation.valid, validation.rejection_reason
        assert len(raw["evidence"]) == 4
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["authorization_id"] == TEST_AUTHORIZATION.authorization_id
    assert manifest["database_reads"] == 1
    assert manifest["database_writes"] == 0
    assert manifest["model_calls"] == 0
    assert (
        load_prior_http_attempts(
            tmp_path,
            authorization_id=TEST_AUTHORIZATION.authorization_id,
            scope="canary",
        )
        == 15
    )


def test_prior_attempts_are_rebuilt_and_enforced(tmp_path: Path) -> None:
    root = create_context_run_root(tmp_path, "prior")
    collect_production_contexts(
        _db(),
        COMPANIES,
        authorization=TEST_AUTHORIZATION,
        scope="canary",
        run_root=root,
        as_of_date=date(2026, 7, 19),
        prior_http_attempts=30,
        fetcher=_success_fetcher,
        sleep=lambda _seconds: None,
    )
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cumulative_http_attempts"] == 45
    with pytest.raises(ContextCollectionError, match="no authorized"):
        collect_production_contexts(
            _db(),
            COMPANIES,
            authorization=TEST_AUTHORIZATION,
            scope="canary",
            run_root=root,
            as_of_date=date(2026, 7, 19),
            prior_http_attempts=45,
            fetcher=_success_fetcher,
        )


def test_transient_failure_retries_without_exceeding_budget(tmp_path: Path) -> None:
    calls: dict[str, int] = {}

    def fetcher(url: str) -> SnippetFetchResult:
        calls[url] = calls.get(url, 0) + 1
        if calls[url] == 1:
            return SnippetFetchResult(url, url, 503, "application/json", b"", "http_503")
        return _success_fetcher(url)

    root = create_context_run_root(tmp_path, "canary-retry")
    result = collect_production_contexts(
        _db(),
        COMPANIES,
        authorization=TEST_AUTHORIZATION,
        scope="canary",
        run_root=root,
        as_of_date=date(2026, 7, 19),
        fetcher=fetcher,
        sleep=lambda _seconds: None,
    )
    assert result["http_attempts"] == 30
    assert max(calls.values()) == 2


def test_redirect_or_scope_drift_fails_closed(tmp_path: Path) -> None:
    root = create_context_run_root(tmp_path, "redirect")

    def redirected(url: str) -> SnippetFetchResult:
        return SnippetFetchResult(url, "https://example.com/", 200, "application/json", b"{}")

    with pytest.raises(ContextCollectionError, match="URL identity"):
        collect_production_contexts(
            _db(),
            COMPANIES,
            authorization=TEST_AUTHORIZATION,
            scope="canary",
            run_root=root,
            as_of_date=date(2026, 7, 19),
            fetcher=redirected,
        )
    with pytest.raises(ContextCollectionError, match="exactly 5"):
        collect_production_contexts(
            _db(),
            {"600036": COMPANIES["600036"]},
            authorization=TEST_AUTHORIZATION,
            scope="canary",
            run_root=root,
            as_of_date=date(2026, 7, 19),
            fetcher=_success_fetcher,
        )


def test_context_run_root_is_create_only_and_rejects_symlink(tmp_path: Path) -> None:
    create_context_run_root(tmp_path, "same")
    with pytest.raises(ContextCollectionError, match="create-only"):
        create_context_run_root(tmp_path, "same")
    other = tmp_path / "other"
    other.mkdir()
    artifacts = tmp_path / "symlinked" / "artifacts"
    artifacts.parent.mkdir()
    artifacts.symlink_to(other, target_is_directory=True)
    with pytest.raises(ContextCollectionError, match="symlink"):
        create_context_run_root(tmp_path / "symlinked", "unsafe")


def test_retired_authorization_stays_blocked_without_artifacts(tmp_path: Path) -> None:
    ledger = {
        "schema_version": "qualitative-v2-production-authorizations-v1",
        "active": None,
        "retired": [
            {
                "authorization_id": "retired-canary-01",
                "scope": "canary",
                "http_attempt_limit": 45,
                "consumed_http_attempts": 45,
                "retired_on": "2026-07-19",
                "reason": "test budget exhausted",
            }
        ],
    }
    (tmp_path / AUTHORIZATION_LEDGER_FILENAME).write_text(json.dumps(ledger), encoding="utf-8")

    assert not (tmp_path / "artifacts").exists()
    with pytest.raises(ContextCollectionError, match="retired"):
        load_active_authorization(tmp_path, authorization_id="retired-canary-01", scope="canary")


def test_active_authorization_is_exact_and_tracked(tmp_path: Path) -> None:
    ledger = {
        "schema_version": "qualitative-v2-production-authorizations-v1",
        "active": {
            "authorization_id": "active-canary-01",
            "scope": "canary",
            "http_attempt_limit": 45,
        },
        "retired": [],
    }
    (tmp_path / AUTHORIZATION_LEDGER_FILENAME).write_text(json.dumps(ledger), encoding="utf-8")

    assert load_active_authorization(
        tmp_path, authorization_id="active-canary-01", scope="canary"
    ) == ProductionContextAuthorization("active-canary-01", "canary", 45)
    with pytest.raises(ContextCollectionError, match="does not match"):
        load_active_authorization(tmp_path, authorization_id="different-canary-01", scope="canary")


def test_orient_cable_authorization_preserves_exact_extended_limits(tmp_path: Path) -> None:
    ledger = {
        "schema_version": "qualitative-v2-production-authorizations-v1",
        "active": {
            "authorization_id": "qualitative-v2-orient-cable-pilot-20260719-01",
            "scope": "orient-cable",
            "http_attempt_limit": 12,
            "target_codes": ["603606"],
            "pdf_download_limit": 3,
            "gemini_logical_call_limit": 1,
            "gemini_http_attempt_limit": 3,
            "allowed_hosts": [
                "www.cninfo.com.cn",
                "static.cninfo.com.cn",
                "www.sse.com.cn",
                "static.sse.com.cn",
                "www.orientcable.com",
            ],
            "as_of_date": "2026-07-19",
        },
        "retired": [],
    }
    (tmp_path / AUTHORIZATION_LEDGER_FILENAME).write_text(json.dumps(ledger), encoding="utf-8")

    assert load_active_authorization(
        tmp_path,
        authorization_id="qualitative-v2-orient-cable-pilot-20260719-01",
        scope="orient-cable",
    ) == ProductionContextAuthorization(
        "qualitative-v2-orient-cable-pilot-20260719-01",
        "orient-cable",
        12,
        target_codes=("603606",),
        pdf_download_limit=3,
        gemini_logical_call_limit=1,
        gemini_http_attempt_limit=3,
        allowed_hosts=(
            "www.cninfo.com.cn",
            "static.cninfo.com.cn",
            "www.sse.com.cn",
            "static.sse.com.cn",
            "www.orientcable.com",
        ),
        as_of_date="2026-07-19",
    )


def test_orient_cable_authorization_rejects_limit_drift(tmp_path: Path) -> None:
    ledger = {
        "schema_version": "qualitative-v2-production-authorizations-v1",
        "active": {
            "authorization_id": "qualitative-v2-orient-cable-pilot-20260719-01",
            "scope": "orient-cable",
            "http_attempt_limit": 12,
            "target_codes": ["603606"],
            "pdf_download_limit": 4,
            "gemini_logical_call_limit": 1,
            "gemini_http_attempt_limit": 3,
            "allowed_hosts": ["www.cninfo.com.cn"],
            "as_of_date": "2026-07-19",
        },
        "retired": [],
    }
    (tmp_path / AUTHORIZATION_LEDGER_FILENAME).write_text(json.dumps(ledger), encoding="utf-8")

    with pytest.raises(ContextCollectionError, match="contract drift"):
        load_active_authorization(
            tmp_path,
            authorization_id="qualitative-v2-orient-cable-pilot-20260719-01",
            scope="orient-cable",
        )
