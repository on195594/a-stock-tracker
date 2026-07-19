"""Production canary tests; all model/network work remains fake or disabled."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

import config
import qualitative_v2_production as production_mod
import scripts.run_qualitative_v2_production as production_cli
from lib import cache as cache_mod
from qualitative_v2_orient_cable_hybrid import HybridAuthorization
from qualitative_v2_production import (
    ProductionV2Error,
    context_missing_score_dimensions,
    get_production_qualitative_score,
    is_v2_eligible,
    load_usable_v2_score,
    production_mode,
    promote_shadow_record,
)
from qualitative_v2_shadow import _context_payload
from qualitative_v2_shadow import ShadowRunOutcome
from qualitative_v2_validator import validate_context_dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AS_OF_DATE = "2026-07-19"


def _context(code: str = "600036", name: str = "招商银行") -> dict[str, object]:
    return {
        "code": code,
        "name": name,
        "industry": "fixture-industry",
        "as_of_date": AS_OF_DATE,
        "schema_version": "qualitative-score-v2",
        "rubric_version": "rubric-v1",
        "taxonomy_version": "taxonomy-v1",
        "evidence": [
            {
                "evidence_id": "disclosure.moat",
                "evidence_type": "company_disclosure",
                "claim_category": "competitive_moat",
                "allowed_dimensions": ["moat"],
                "directness": "direct",
                "freshness_policy": "max_age_365d",
                "value": "核心技术",
                "unit": None,
                "source": "CNINFO annual report sha256 fixture locator p1",
                "source_date": "2026-03-31",
                "freshness_status": "fresh",
            },
            {
                "evidence_id": "fundamentals.roe",
                "evidence_type": "financial_metric",
                "claim_category": "financial_performance",
                "allowed_dimensions": ["moat"],
                "directness": "supporting",
                "freshness_policy": "max_age_550d",
                "value": 12.5,
                "unit": "percent",
                "source": "read-only fundamentals snapshot sha256 fixture",
                "source_date": "2026-03-31",
                "freshness_status": "fresh",
            },
            {
                "evidence_id": "disclosure.position",
                "evidence_type": "company_disclosure",
                "claim_category": "industry_position",
                "allowed_dimensions": ["market_pos"],
                "directness": "direct",
                "freshness_policy": "max_age_365d",
                "value": "市场占有率披露",
                "unit": None,
                "source": "CNINFO annual report sha256 fixture locator p2",
                "source_date": "2026-03-31",
                "freshness_status": "fresh",
            },
            {
                "evidence_id": "disclosure.sentiment",
                "evidence_type": "company_disclosure",
                "claim_category": "market_sentiment",
                "allowed_dimensions": ["sentiment"],
                "directness": "direct",
                "freshness_policy": "max_age_30d",
                "value": "重大合同持续履行",
                "unit": None,
                "source": "CNINFO disclosure sha256 fixture locator p3",
                "source_date": "2026-07-10",
                "freshness_status": "fresh",
                "persistence_horizon": "multi_quarter",
                "materiality": "major",
            },
        ],
    }


def _result(*, insufficient: bool = False, partial: bool = False) -> dict[str, object]:
    if insufficient and partial:
        raise ValueError("fixture result cannot be both fully insufficient and partial")
    dimensions: dict[str, object] = {}
    citations = {
        "moat": ["disclosure.moat", "fundamentals.roe"],
        "market_pos": ["disclosure.position"],
        "sentiment": ["disclosure.sentiment"],
    }
    scores = {"moat": 7, "market_pos": 4, "sentiment": 4}
    for dimension in ("moat", "market_pos", "sentiment"):
        dimension_insufficient = insufficient or (partial and dimension == "sentiment")
        dimensions[dimension] = {
            "status": "insufficient_data" if dimension_insufficient else "scored",
            "score": None if dimension_insufficient else scores[dimension],
            "confidence": "low" if dimension_insufficient else "medium",
            "evidence_ids": [] if dimension_insufficient else citations[dimension],
            "rationale": "证据不足" if dimension_insufficient else "评分仅基于所引证据",
        }
    return {
        "schema_version": "qualitative-score-v2",
        "overall_status": "insufficient_data" if insufficient or partial else "scored",
        "as_of_date": AS_OF_DATE,
        "dimensions": dimensions,
    }


def _record(
    code: str = "600036",
    name: str = "招商银行",
    *,
    insufficient: bool = False,
    partial: bool = False,
) -> dict[str, object]:
    raw_context = _context(code, name)
    if partial:
        evidence = raw_context["evidence"]
        assert isinstance(evidence, list)
        raw_context["evidence"] = [item for item in evidence if item["claim_category"] != "market_sentiment"]
    validation = validate_context_dict(raw_context)
    assert validation.valid and validation.context is not None
    input_hash = validation.context.compute_input_hash()
    artifact_context = {**raw_context, "input_hash": input_hash}
    result = _result(insufficient=insufficient, partial=partial)
    return {
        "code": code,
        "context_json": json.dumps(artifact_context, ensure_ascii=False, sort_keys=True),
        "created_at": "2026-07-19T01:00:00+08:00",
        "input_hash": input_hash,
        "model": "gemini-2.5-flash",
        "overall_status": result["overall_status"],
        "result_json": json.dumps(result, ensure_ascii=False, sort_keys=True),
        "scored_date": AS_OF_DATE,
        "validation_status": "VALID_INSUFFICIENT_DATA" if insufficient or partial else "VALID_SCORED",
    }


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cache_mod, "DB_PATH", str(tmp_path / "tracker.db"))
    connection = cache_mod.get_db()
    yield connection
    connection.close()


def test_mode_is_exact_and_defaults_off() -> None:
    assert production_mode({}) == "off"
    assert production_mode({"QUALITATIVE_V2_MODE": "canary"}) == "canary"
    assert production_mode({"QUALITATIVE_V2_MODE": "on"}) == "on"
    with pytest.raises(ProductionV2Error):
        production_mode({"QUALITATIVE_V2_MODE": "ON"})


def test_orient_cable_pilot_is_eligible_only_in_canary_or_on() -> None:
    production_canaries = config.QUALITATIVE_V2_CANARY_CODES | config.QUALITATIVE_V2_PILOT_CODES
    assert "603606" not in config.QUALITATIVE_V2_CANARY_CODES
    assert is_v2_eligible("603606", mode="canary", canary_codes=production_canaries)
    assert is_v2_eligible("603606", mode="on", canary_codes=production_canaries)
    assert not is_v2_eligible("603606", mode="off", canary_codes=production_canaries)


def test_nonempty_shadow_context_remains_valid_wire_input() -> None:
    validation = validate_context_dict(_context())
    assert validation.valid and validation.context is not None
    payload = _context_payload(validation.context)
    payload.pop("input_hash")
    round_trip = validate_context_dict(payload)
    assert round_trip.valid, round_trip.rejection_reason
    assert context_missing_score_dimensions(validation.context) == ()


def test_deterministic_readiness_rejects_supporting_only_context() -> None:
    raw = _context()
    evidence = raw["evidence"]
    assert isinstance(evidence, list)
    raw["evidence"] = [evidence[1]]
    validation = validate_context_dict(raw)
    assert validation.valid and validation.context is not None
    assert context_missing_score_dimensions(validation.context) == ("moat", "market_pos", "sentiment")


def test_promoted_scored_row_is_revalidated_on_every_read(db) -> None:
    assert promote_shadow_record(db, _record(), expected_codes=frozenset({"600036"}))
    db.commit()
    assert load_usable_v2_score(db, "600036", "招商银行", today=date(2026, 7, 19)) == {
        "moat": 7,
        "market_pos": 4,
        "sentiment": 4,
    }
    assert not promote_shadow_record(db, _record(), expected_codes=frozenset({"600036"}))


def test_partial_row_preserves_v2_dimensions_and_composes_with_v1(db, caplog: pytest.LogCaptureFixture) -> None:
    assert promote_shadow_record(db, _record(partial=True), expected_codes=frozenset({"600036"}))
    db.commit()
    assert load_usable_v2_score(db, "600036", "招商银行", today=date(2026, 7, 19)) == {
        "moat": 7,
        "market_pos": 4,
        "sentiment": None,
    }
    calls: list[tuple[str, str]] = []

    def legacy(code: str, name: str) -> dict[str, int]:
        calls.append((code, name))
        return {"moat": 5, "market_pos": 2, "sentiment": 3}

    with caplog.at_level("INFO"):
        assert get_production_qualitative_score(
            db,
            "600036",
            "招商银行",
            canary_codes=frozenset({"600036"}),
            legacy_getter=legacy,
            mode="canary",
        ) == {"moat": 7, "market_pos": 4, "sentiment": 3}
    assert calls == [("600036", "招商银行")]
    assert "hybrid_v2" in caplog.text
    assert "'sentiment': 'v1'" in caplog.text


def test_full_v2_does_not_call_legacy(db) -> None:
    promote_shadow_record(db, _record(), expected_codes=frozenset({"600036"}))
    db.commit()

    def forbidden_legacy(_code: str, _name: str) -> dict[str, int]:
        raise AssertionError("full v2 must not load v1")

    assert get_production_qualitative_score(
        db,
        "600036",
        "招商银行",
        canary_codes=frozenset({"600036"}),
        legacy_getter=forbidden_legacy,
        mode="canary",
    ) == {"moat": 7, "market_pos": 4, "sentiment": 4}


def test_tampered_partial_dimension_fails_closed_to_v1(db) -> None:
    promote_shadow_record(db, _record(partial=True), expected_codes=frozenset({"600036"}))
    db.execute("UPDATE qualitative_scores_v2 SET sentiment=4 WHERE code='600036'")
    db.commit()
    legacy = lambda _code, _name: {"moat": 5, "market_pos": 2, "sentiment": 3}
    assert get_production_qualitative_score(
        db,
        "600036",
        "招商银行",
        canary_codes=frozenset({"600036"}),
        legacy_getter=legacy,
        mode="canary",
    ) == legacy("", "")


def test_invalid_legacy_hybrid_fallback_is_called_only_once(db) -> None:
    promote_shadow_record(db, _record(partial=True), expected_codes=frozenset({"600036"}))
    db.commit()
    calls = 0

    def invalid_legacy(_code: str, _name: str) -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"moat": 5, "market_pos": 2}

    assert get_production_qualitative_score(
        db,
        "600036",
        "招商银行",
        canary_codes=frozenset({"600036"}),
        legacy_getter=invalid_legacy,
        mode="canary",
    ) == {"moat": 5, "market_pos": 2}
    assert calls == 1


def test_tampered_v2_row_falls_back_per_stock(db) -> None:
    promote_shadow_record(db, _record(), expected_codes=frozenset({"600036"}))
    db.execute("UPDATE qualitative_scores_v2 SET moat=10 WHERE code='600036'")
    db.commit()
    calls: list[tuple[str, str]] = []

    def legacy(code: str, name: str) -> dict[str, int]:
        calls.append((code, name))
        return {"moat": 5, "market_pos": 2, "sentiment": 3}

    assert get_production_qualitative_score(
        db,
        "600036",
        "招商银行",
        canary_codes=frozenset({"600036"}),
        legacy_getter=legacy,
        mode="canary",
    ) == {"moat": 5, "market_pos": 2, "sentiment": 3}
    assert calls == [("600036", "招商银行")]


def test_unexpected_v2_read_error_falls_back_per_stock(db, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_error(*_args: object, **_kwargs: object) -> dict[str, int] | None:
        raise RuntimeError("validator implementation changed")

    monkeypatch.setattr(production_mod, "load_usable_v2_score", unexpected_error)
    legacy = lambda _code, _name: {"moat": 5, "market_pos": 2, "sentiment": 3}
    assert get_production_qualitative_score(
        db,
        "600036",
        "招商银行",
        canary_codes=frozenset({"600036"}),
        legacy_getter=legacy,
        mode="canary",
    ) == legacy("", "")


def test_off_non_canary_and_insufficient_always_use_v1(db) -> None:
    promote_shadow_record(db, _record(insufficient=True), expected_codes=frozenset({"600036"}))
    db.commit()
    legacy = lambda _code, _name: {"moat": 5, "market_pos": 2, "sentiment": 3}
    for mode, canaries in (("off", frozenset({"600036"})), ("canary", frozenset({"601088"}))):
        assert get_production_qualitative_score(
            db,
            "600036",
            "招商银行",
            canary_codes=canaries,
            legacy_getter=legacy,
            mode=mode,
        ) == legacy("", "")
    assert get_production_qualitative_score(
        db,
        "600036",
        "招商银行",
        canary_codes=frozenset({"600036"}),
        legacy_getter=legacy,
        mode="canary",
    ) == legacy("", "")


def test_promotion_rejects_scope_model_and_overwrite_drift(db) -> None:
    with pytest.raises(ProductionV2Error):
        promote_shadow_record(db, _record(), expected_codes=frozenset({"601088"}))
    wrong_model = _record()
    wrong_model["model"] = "gemini-moving-alias"
    with pytest.raises(ProductionV2Error):
        promote_shadow_record(db, wrong_model, expected_codes=frozenset({"600036"}))
    promote_shadow_record(db, _record(), expected_codes=frozenset({"600036"}))
    drifted = _record()
    drifted["created_at"] = "2026-07-19T02:00:00+08:00"
    with pytest.raises(ProductionV2Error):
        promote_shadow_record(db, drifted, expected_codes=frozenset({"600036"}))


def test_preview_uses_exact_canary_without_credentials_or_artifacts(tmp_path: Path) -> None:
    contexts = tmp_path / "contexts"
    contexts.mkdir()
    names = {item["code"]: item["name"] for item in config.WATCHLIST}
    for code in config.QUALITATIVE_V2_CANARY_CODES:
        (contexts / f"{code}.json").write_text(
            json.dumps(_context(code, names[code]), ensure_ascii=False), encoding="utf-8"
        )
    artifact_root = PROJECT_ROOT / "artifacts" / "qualitative-v2-production"
    before = set(artifact_root.iterdir()) if artifact_root.exists() else set()
    environment = dict(os.environ)
    environment.pop("GEMINI_API_KEY", None)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_qualitative_v2_production.py",
            "preview",
            "--contexts",
            str(contexts),
            "--scope",
            "canary",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["logical_call_limit"] == 5
    assert result["maximum_http_attempts"] == 15
    assert result["hybrid_ready"] is True
    assert result["score_ready"] is True
    after = set(artifact_root.iterdir()) if artifact_root.exists() else set()
    assert after == before


def test_preview_accepts_orient_cable_for_hybrid_without_credentials_or_artifacts(tmp_path: Path) -> None:
    contexts = tmp_path / "contexts"
    contexts.mkdir()
    raw = _context("603606", "东方电缆")
    evidence = raw["evidence"]
    assert isinstance(evidence, list)
    raw["evidence"] = [item for item in evidence if item["claim_category"] != "market_sentiment"]
    (contexts / "603606.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    artifact_root = PROJECT_ROOT / "artifacts" / "qualitative-v2-production"
    before = set(artifact_root.iterdir()) if artifact_root.exists() else set()
    environment = dict(os.environ)
    environment.pop("GEMINI_API_KEY", None)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_qualitative_v2_production.py",
            "preview",
            "--contexts",
            str(contexts),
            "--scope",
            "orient-cable",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["hybrid_ready"] is True
    assert result["score_ready"] is False
    assert result["scoreable_dimensions"] == {"603606": ["moat", "market_pos"]}
    assert result["missing_score_dimensions"] == {"603606": ["sentiment"]}
    after = set(artifact_root.iterdir()) if artifact_root.exists() else set()
    assert after == before


def test_execute_reports_and_persists_hybrid_as_production_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "tracker.db"
    monkeypatch.setattr(cache_mod, "DB_PATH", str(database_path))
    monkeypatch.setattr(production_cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(production_cli, "get_db", cache_mod.get_db)
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-key")
    record = _record("603606", "东方电缆", partial=True)

    def fake_shadow(*_args: object, **_kwargs: object) -> ShadowRunOutcome:
        return ShadowRunOutcome(record=record, api_called=True, persisted=True)

    monkeypatch.setattr(production_cli, "run_shadow_evaluation", fake_shadow)
    raw_context = _context("603606", "东方电缆")
    evidence = raw_context["evidence"]
    assert isinstance(evidence, list)
    raw_context["evidence"] = [item for item in evidence if item["claim_category"] != "market_sentiment"]
    validation = validate_context_dict(raw_context)
    assert validation.valid and validation.context is not None
    authorization = HybridAuthorization(
        "qualitative-v2-orient-cable-hybrid-fixture",
        "603606",
        validation.context.compute_input_hash(),
        "fixture-context-run",
        "fixture-source-run",
        "a" * 64,
        "b" * 64,
        "c" * 64,
        "gemini-2.5-flash",
        1,
        3,
        0,
        0,
    )

    result, success = production_cli._execute(
        [validation.context],
        "orient-cable",
        authorization.authorization_id,
        authorization=authorization,
    )

    assert success is True
    assert result["status"] == "READY_HYBRID"
    assert result["fully_scored"] == 0
    assert result["hybrid_scored"] == 1
    assert result["production_usable"] == 1
    outcomes = result["outcomes"]
    assert isinstance(outcomes, list)
    assert outcomes[0]["adoption_mode"] == "hybrid_v2"
    assert outcomes[0]["scored_dimensions"] == ["moat", "market_pos"]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT overall_status, moat, market_pos, sentiment FROM qualitative_scores_v2 WHERE code='603606'"
        ).fetchone() == ("insufficient_data", 7, 4, None)
