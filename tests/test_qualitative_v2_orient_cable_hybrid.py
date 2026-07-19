"""Offline authorization and derivation tests for the 603606 hybrid run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

import qualitative_v2_orient_cable_hybrid as hybrid
from qualitative_v2_orient_cable_hybrid import (
    HybridAuthorization,
    HybridAuthorizationError,
    build_authorized_hybrid_context,
    load_active_hybrid_authorization,
)
from qualitative_v2_validator import validate_context_dict


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()


def _source_context() -> dict[str, object]:
    return {
        "code": "603606",
        "name": "东方电缆",
        "industry": "电力设备",
        "as_of_date": "2026-07-19",
        "schema_version": "qualitative-score-v2",
        "rubric_version": "rubric-v1",
        "taxonomy_version": "taxonomy-v1",
        "evidence": [
            {
                "evidence_id": "fundamentals.roe_3y_avg",
                "evidence_type": "financial_metric",
                "claim_category": "financial_performance",
                "allowed_dimensions": ["moat"],
                "directness": "supporting",
                "freshness_policy": "max_age_550d",
                "value": 16.38,
                "unit": "percent",
                "source": "sealed local fundamentals row hash fixture",
                "source_date": "2025-12-31",
                "freshness_status": "fresh",
            }
        ],
    }


def _fixture_authorization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HybridAuthorization:
    source_run_id = "source-fixture"
    source_root = tmp_path / "artifacts" / "qualitative-v2-orient-cable" / source_run_id
    (source_root / "contexts").mkdir(parents=True)
    (source_root / "raw").mkdir()
    source_context = _source_context()
    validation = validate_context_dict(source_context)
    assert validation.valid and validation.context is not None
    (source_root / "contexts" / "603606.json").write_bytes(_canonical(source_context))
    pdf_raw = b"fixture-pdf"
    (source_root / "raw" / "01-annual-report.pdf").write_bytes(pdf_raw)
    manifest_raw = _canonical({"schema_version": "fixture-source-v1"})
    manifest_hash = hashlib.sha256(manifest_raw).hexdigest()
    (source_root / "manifest.json").write_bytes(manifest_raw)
    (source_root / "manifest.sha256").write_text(f"{manifest_hash}  manifest.json\n", encoding="ascii")
    pages = [
        "公司是国内陆缆系统、海缆系统核心供应商，荣膺最具竞争力企业 10 强。",
        "公司拥有 500kV 及以下交流海缆、±535kV直流海缆系统研发生产能力。",
    ]
    monkeypatch.setattr(hybrid, "_pdf_pages", lambda _raw: pages)
    moat = hybrid._pdf_evidence(
        pages,
        raw_sha256=hashlib.sha256(pdf_raw).hexdigest(),
        dimension="moat",
        claim_category="competitive_moat",
        patterns=("拥有 500kV", "±535kV", "DNV 认证"),
    )
    position = hybrid._pdf_evidence(
        pages,
        raw_sha256=hashlib.sha256(pdf_raw).hexdigest(),
        dimension="market_pos",
        claim_category="industry_position",
        patterns=("国内陆缆系统、海缆系统核心供应商", "最具竞争力企业 10 强"),
    )
    source_evidence = source_context["evidence"]
    assert isinstance(source_evidence, list)
    candidate = {**source_context, "evidence": [source_evidence[0], moat, position]}
    candidate_validation = validate_context_dict(candidate)
    assert candidate_validation.valid and candidate_validation.context is not None
    return HybridAuthorization(
        "fixture-hybrid-authorization",
        "603606",
        candidate_validation.context.compute_input_hash(),
        "hybrid-context-fixture",
        source_run_id,
        manifest_hash,
        validation.context.compute_input_hash(),
        hashlib.sha256(pdf_raw).hexdigest(),
        "gemini-2.5-flash",
        1,
        3,
        0,
        0,
    )


def test_active_authorization_is_exact_and_retired_id_is_blocked(tmp_path: Path) -> None:
    authorization = HybridAuthorization(
        hybrid.APPROVED_AUTHORIZATION_ID,
        "603606",
        hybrid.APPROVED_CONTEXT_HASH,
        hybrid.APPROVED_CONTEXT_RUN_ID,
        hybrid.APPROVED_SOURCE_RUN_ID,
        hybrid.APPROVED_SOURCE_MANIFEST_HASH,
        hybrid.APPROVED_SOURCE_CONTEXT_HASH,
        hybrid.APPROVED_SOURCE_PDF_HASH,
        "gemini-2.5-flash",
        1,
        3,
        0,
        0,
    )
    ledger: dict[str, object] = {
        "schema_version": hybrid.AUTHORIZATION_SCHEMA,
        "active": asdict(authorization),
        "retired": [],
    }
    (tmp_path / hybrid.AUTHORIZATION_FILENAME).write_text(json.dumps(ledger), encoding="utf-8")
    assert load_active_hybrid_authorization(tmp_path, authorization.authorization_id) == authorization
    ledger["active"] = None
    ledger["retired"] = [{"authorization_id": authorization.authorization_id}]
    (tmp_path / hybrid.AUTHORIZATION_FILENAME).write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(HybridAuthorizationError, match="retired"):
        load_active_hybrid_authorization(tmp_path, authorization.authorization_id)


def test_builds_exact_network_free_hybrid_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    authorization = _fixture_authorization(tmp_path, monkeypatch)
    result = build_authorized_hybrid_context(tmp_path, authorization)
    assert result["context_input_hash"] == authorization.context_input_hash
    assert result["source_http_attempts"] == 0
    assert result["pdf_downloads"] == 0
    assert result["missing_score_dimensions"] == ["sentiment"]
    context_path = Path(str(result["contexts_path"])) / "603606.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    validation = validate_context_dict(context)
    assert validation.valid and validation.context is not None
    assert validation.context.compute_input_hash() == authorization.context_input_hash
    assert context_path.stat().st_mode & 0o777 == 0o600


def test_source_hash_drift_blocks_before_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    authorization = _fixture_authorization(tmp_path, monkeypatch)
    pdf = (
        tmp_path
        / "artifacts"
        / "qualitative-v2-orient-cable"
        / authorization.source_run_id
        / "raw"
        / "01-annual-report.pdf"
    )
    pdf.write_bytes(b"tampered")
    with pytest.raises(HybridAuthorizationError, match="PDF hash drift"):
        build_authorized_hybrid_context(tmp_path, authorization)
    assert not (tmp_path / "artifacts" / "qualitative-v2-orient-cable-hybrid").exists()
