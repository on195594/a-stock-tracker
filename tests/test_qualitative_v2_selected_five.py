"""Offline tests for the directly authorized selected-five production batch."""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import cast

import pytest

import a_stock_tracker.qualitative.selected_five as selected
from a_stock_tracker.qualitative.orient_cable_pilot import OfficialFetchResult
from a_stock_tracker.qualitative.production_contexts import ContextCollectionError
from a_stock_tracker.qualitative.selected_five import (
    SelectedFiveAuthorization,
    collect_selected_five,
    create_run_root,
    load_active_authorization,
    validate_context_manifest,
)


def _authorization() -> SelectedFiveAuthorization:
    return SelectedFiveAuthorization(
        selected.APPROVED_AUTHORIZATION_ID,
        selected.APPROVED_CODES,
        "2026-07-19",
        selected.APPROVED_HOSTS,
        60,
        8,
        "gemini-2.5-flash",
        5,
        15,
    )


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stock_fundamentals (code TEXT PRIMARY KEY, data TEXT)")
    for code in selected.APPROVED_CODES:
        conn.execute(
            "INSERT INTO stock_fundamentals VALUES (?, ?)",
            (code, json.dumps({"roe_3y_avg": 12.5, "report_period": "2025"})),
        )
    return conn


def _fetcher(url: str, _hosts: tuple[str, ...]) -> OfficialFetchResult:
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname != "www.cninfo.com.cn":
        return OfficialFetchResult(url, url, 200, "application/pdf", b"fixture-pdf")
    announcements: list[dict[str, object]] = []
    body = json.dumps({"announcements": announcements}, ensure_ascii=False).encode()
    return OfficialFetchResult(url, url, 200, "application/json", body)


def test_exact_authorization_retires_fail_closed(tmp_path: Path) -> None:
    authorization = _authorization()
    ledger: dict[str, object] = {
        "schema_version": selected.AUTHORIZATION_SCHEMA,
        "active": {
            **asdict(authorization),
            "target_codes": list(authorization.target_codes),
            "allowed_hosts": list(authorization.allowed_hosts),
        },
        "retired": [],
    }
    (tmp_path / selected.AUTHORIZATION_FILENAME).write_text(json.dumps(ledger), encoding="utf-8")
    assert load_active_authorization(tmp_path, authorization.authorization_id) == authorization
    ledger["active"] = None
    ledger["retired"] = [{"authorization_id": authorization.authorization_id}]
    (tmp_path / selected.AUTHORIZATION_FILENAME).write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ContextCollectionError, match="retired"):
        load_active_authorization(tmp_path, authorization.authorization_id)


def test_collects_five_pdfs_and_seals_context_hashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    all_patterns = " ".join(
        pattern
        for company_patterns in selected.EVIDENCE_PATTERNS.values()
        for patterns in company_patterns.values()
        for pattern in patterns
    )
    monkeypatch.setattr(selected, "_pdf_pages", lambda _raw: [all_patterns * 20])
    authorization = _authorization()
    root = create_run_root(tmp_path, "selected-five-fixture")
    result = collect_selected_five(
        _db(),
        authorization=authorization,
        run_root=root,
        as_of_date=date(2026, 7, 19),
        fetcher=_fetcher,
    )
    assert result["http_attempts"] == 20
    assert result["pdf_downloads"] == 5
    context_hashes = cast(dict[str, str], result["context_hashes"])
    readiness = cast(dict[str, dict[str, object]], result["readiness"])
    assert set(context_hashes) == set(selected.APPROVED_CODES)
    assert all(value["scoreable"] == ["moat", "market_pos"] for value in readiness.values())
    assert validate_context_manifest(tmp_path, authorization, root / "contexts") == context_hashes
    for path in root.rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)


def test_manifest_or_context_drift_blocks_model_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    all_patterns = " ".join(
        pattern
        for company_patterns in selected.EVIDENCE_PATTERNS.values()
        for patterns in company_patterns.values()
        for pattern in patterns
    )
    monkeypatch.setattr(selected, "_pdf_pages", lambda _raw: [all_patterns * 20])
    authorization = _authorization()
    root = create_run_root(tmp_path, "selected-five-drift")
    collect_selected_five(
        _db(),
        authorization=authorization,
        run_root=root,
        as_of_date=date(2026, 7, 19),
        fetcher=_fetcher,
    )
    context = root / "contexts" / "600036.json"
    value = json.loads(context.read_bytes())
    value["industry"] = "tampered"
    context.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ContextCollectionError, match="hash drift"):
        validate_context_manifest(tmp_path, authorization, root / "contexts")


def test_manifest_binding_accepts_relative_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    all_patterns = " ".join(
        pattern
        for company_patterns in selected.EVIDENCE_PATTERNS.values()
        for patterns in company_patterns.values()
        for pattern in patterns
    )
    monkeypatch.setattr(selected, "_pdf_pages", lambda _raw: [all_patterns * 20])
    authorization = _authorization()
    root = create_run_root(tmp_path, "selected-five-relative-root")
    result = collect_selected_five(
        _db(),
        authorization=authorization,
        run_root=root,
        as_of_date=date(2026, 7, 19),
        fetcher=_fetcher,
    )
    monkeypatch.chdir(tmp_path)
    assert validate_context_manifest(Path("."), authorization, root / "contexts") == result["context_hashes"]
