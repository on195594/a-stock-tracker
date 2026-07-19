"""Synthetic fixture-first tests for the bounded M5 batch orchestrator."""

from __future__ import annotations

import csv
import hashlib
import json
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

import a_stock_tracker.qualitative.m5.pipeline as m5
from a_stock_tracker.qualitative.client import GeminiCallResult, ValidationStatus
from a_stock_tracker.qualitative.m5.pipeline import ModelResponse
from a_stock_tracker.qualitative.types import QualitativeContext
from a_stock_tracker.qualitative.validator import validate_model_output
from scripts.run_qualitative_v2_m5 import main as cli_main

SAMPLE = Path(__file__).parent / "fixtures" / "milestone005" / "sample.csv"
CLAUDE_MODEL = "claude-test-20260718"


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()


def _evidence() -> list[dict[str, object]]:
    return [
        {
            "evidence_id": "fundamentals.roe_3y_avg",
            "evidence_type": "financial_metric",
            "claim_category": "financial_performance",
            "allowed_dimensions": ["moat"],
            "directness": "supporting",
            "freshness_policy": "max_age_550d",
            "value": 12.3,
            "unit": "percent",
            "source": "synthetic_fixture",
            "source_date": "2026-03-31",
            "freshness_status": "fresh",
        },
        {
            "evidence_id": "ip.synthetic_patent",
            "evidence_type": "ip_record",
            "claim_category": "competitive_moat",
            "allowed_dimensions": ["moat"],
            "directness": "direct",
            "freshness_policy": "max_age_365d",
            "value": "synthetic grant",
            "unit": None,
            "source": "synthetic_fixture",
            "source_date": "2026-01-01",
            "freshness_status": "fresh",
            "state_type": "status",
            "effective_until": "2030-01-01",
        },
        {
            "evidence_id": "disclosure.synthetic_position",
            "evidence_type": "company_disclosure",
            "claim_category": "industry_position",
            "allowed_dimensions": ["market_pos"],
            "directness": "direct",
            "freshness_policy": "max_age_365d",
            "value": "synthetic top three",
            "unit": None,
            "source": "synthetic_fixture",
            "source_date": "2026-01-01",
            "freshness_status": "fresh",
        },
        {
            "evidence_id": "news.synthetic_sentiment",
            "evidence_type": "news_report",
            "claim_category": "market_sentiment",
            "allowed_dimensions": ["sentiment"],
            "directness": "direct",
            "freshness_policy": "max_age_30d",
            "value": "synthetic durable event",
            "unit": None,
            "source": "synthetic_fixture",
            "source_date": "2026-07-01",
            "freshness_status": "fresh",
            "persistence_horizon": "multi_quarter",
            "materiality": "major",
        },
    ]


def _build_bundle(tmp_path: Path, *, with_evidence: bool = True) -> Path:
    rows = list(csv.DictReader(SAMPLE.read_text(encoding="utf-8").splitlines()))
    bundle_root = tmp_path / "bundle"
    contexts = bundle_root / "contexts"
    documents = bundle_root / "documents"
    contexts.mkdir(parents=True)
    documents.mkdir()
    document = documents / "synthetic.txt"
    document.write_text("synthetic fixture document\n", encoding="utf-8")
    document_hash = hashlib.sha256(document.read_bytes()).hexdigest()
    companies: list[dict[str, object]] = []
    evidence = _evidence() if with_evidence else []
    for index, row in enumerate(rows):
        context = {
            "code": row["ts_code"],
            "name": row["name"],
            "industry": row["industry_name"],
            "as_of_date": "2026-07-17",
            "schema_version": "qualitative-score-v2",
            "rubric_version": "rubric-v1",
            "taxonomy_version": "taxonomy-v1",
            "evidence": evidence,
        }
        context_raw = _json_bytes(context)
        context_path = contexts / f"{index:02d}-{row['ts_code']}.json"
        context_path.write_bytes(context_raw)
        provenance = [
            {
                "evidence_id": item["evidence_id"],
                "official_source": True,
                "source_scope": "synthetic",
                "document_path": "documents/synthetic.txt",
                "document_sha256": document_hash,
                "locator": f"fixture:{item['evidence_id']}",
            }
            for item in evidence
        ]
        companies.append(
            {
                "code": row["ts_code"],
                "name": row["name"],
                "industry": row["industry_name"],
                "super_stratum": row["super_stratum"],
                "market_cap_stratum": row["market_cap_stratum"],
                "context_path": f"contexts/{context_path.name}",
                "context_sha256": hashlib.sha256(context_raw).hexdigest(),
                "evidence_provenance": provenance,
            }
        )
    manifest = {
        "bundle_version": "m5-bundle-v1",
        "bundle_kind": "synthetic",
        "as_of_date": "2026-07-17",
        "sample_sha256": m5.EXPECTED_SAMPLE_SHA256,
        "provenance_schema_version": "m5-provenance-v1",
        "source_scopes": ["synthetic"],
        "coverage_report": None,
        "source_approval_id": None,
        "excluded_layers": [],
        "companies": companies,
    }
    manifest_path = bundle_root / "bundle-manifest.json"
    manifest_path.write_bytes(_json_bytes(manifest))
    return manifest_path


def _manifest(path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def _write_manifest(path: Path, value: dict[str, object]) -> None:
    path.write_bytes(_json_bytes(value))


def _valid_coverage_report() -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for layer_type, layers, denominator, scorable in (
        ("industry", m5.SUPER_STRATA, 6, 4),
        ("market_cap", m5.CAP_STRATA, 18, 12),
    ):
        for layer in layers:
            lower, upper = m5._wilson_interval(scorable, denominator)
            rows.append(
                {
                    "layer_type": layer_type,
                    "layer": layer,
                    "denominator": denominator,
                    "scorable": scorable,
                    "insufficient": denominator - scorable,
                    "proportion": scorable / denominator,
                    "passed": True,
                    "wilson_lower": lower,
                    "wilson_upper": upper,
                    "wilson_lower_display": f"{lower:.6f}",
                    "wilson_upper_display": f"{upper:.6f}",
                    "required_action": "none",
                    "scope_disposition": "not_required",
                }
            )
    return {
        "rows": rows,
        "overall_passed": True,
        "milestone_005_approval_blocked": False,
        "corpus_manifest_sha256": "1" * 64,
        "review_index_sha256": "2" * 64,
        "reviewer_seal_sha256s": ["3" * 64, "4" * 64],
        "adjudication_sha256": "5" * 64,
    }


def _scored_output(as_of_date: str, *, scores: tuple[int, int, int] = (6, 3, 4)) -> dict[str, object]:
    return {
        "schema_version": "qualitative-score-v2",
        "overall_status": "scored",
        "as_of_date": as_of_date,
        "dimensions": {
            "moat": {
                "status": "scored",
                "score": scores[0],
                "confidence": "medium",
                "evidence_ids": ["fundamentals.roe_3y_avg", "ip.synthetic_patent"],
                "rationale": "Synthetic direct and financial evidence support this fixture score.",
            },
            "market_pos": {
                "status": "scored",
                "score": scores[1],
                "confidence": "medium",
                "evidence_ids": ["disclosure.synthetic_position"],
                "rationale": "Synthetic position evidence supports this fixture score.",
            },
            "sentiment": {
                "status": "scored",
                "score": scores[2],
                "confidence": "medium",
                "evidence_ids": ["news.synthetic_sentiment"],
                "rationale": "Synthetic persistent sentiment evidence supports this fixture score.",
            },
        },
    }


def test_preview_separates_sample_and_bundle_hash_without_credentials_or_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _build_bundle(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "preview-must-not-read-this")
    before = set(tmp_path.rglob("*"))

    result = m5.preview_bundle(SAMPLE, manifest)

    assert result["sample_sha256"] == m5.EXPECTED_SAMPLE_SHA256
    assert result["bundle_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert result["bundle_sha256"] != result["sample_sha256"]
    assert result["company_count"] == 36
    assert {cell["count"] for cell in cast(list[dict[str, object]], result["cells"])} == {3}
    assert set(tmp_path.rglob("*")) == before


def test_sample_hash_and_twelve_cell_contract_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _build_bundle(tmp_path)
    changed_sample = tmp_path / "changed.csv"
    rows = SAMPLE.read_text(encoding="utf-8").splitlines()
    fields = rows[1].split(",")
    fields[4] = "能源材料"
    rows[1] = ",".join(fields)
    changed_sample.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sample SHA-256 mismatch"):
        m5.load_bundle(changed_sample, manifest)

    changed_hash = hashlib.sha256(changed_sample.read_bytes()).hexdigest()
    monkeypatch.setattr(m5, "EXPECTED_SAMPLE_SHA256", changed_hash)
    raw_manifest = _manifest(manifest)
    raw_manifest["sample_sha256"] = changed_hash
    _write_manifest(manifest, raw_manifest)
    with pytest.raises(ValueError, match="12 registered cells"):
        m5.load_bundle(changed_sample, manifest)


@pytest.mark.parametrize("mutation", ["duplicate", "as_of", "context_hash", "provenance_id", "source_scope"])
def test_bundle_identity_context_and_provenance_errors_fail_closed(tmp_path: Path, mutation: str) -> None:
    manifest_path = _build_bundle(tmp_path)
    manifest = _manifest(manifest_path)
    companies = cast(list[dict[str, object]], manifest["companies"])
    first = companies[0]
    if mutation == "duplicate":
        companies[1] = dict(first)
    elif mutation == "as_of":
        context_path = manifest_path.parent / cast(str, first["context_path"])
        context = cast(dict[str, object], json.loads(context_path.read_text(encoding="utf-8")))
        context["as_of_date"] = "2026-07-16"
        raw = _json_bytes(context)
        context_path.write_bytes(raw)
        first["context_sha256"] = hashlib.sha256(raw).hexdigest()
    elif mutation == "context_hash":
        first["context_sha256"] = "0" * 64
    elif mutation == "provenance_id":
        provenance = cast(list[dict[str, object]], first["evidence_provenance"])
        provenance[0]["evidence_id"] = "fundamentals.unknown"
    else:
        provenance = cast(list[dict[str, object]], first["evidence_provenance"])
        provenance[0]["source_scope"] = "unapproved"
    _write_manifest(manifest_path, manifest)
    with pytest.raises(ValueError):
        m5.load_bundle(SAMPLE, manifest_path)


@pytest.mark.parametrize("unsafe", ["traversal", "symlink"])
def test_bundle_paths_reject_traversal_and_symlinks(tmp_path: Path, unsafe: str) -> None:
    manifest_path = _build_bundle(tmp_path)
    manifest = _manifest(manifest_path)
    first = cast(list[dict[str, object]], manifest["companies"])[0]
    if unsafe == "traversal":
        first["context_path"] = "../outside.json"
    else:
        original = manifest_path.parent / cast(str, first["context_path"])
        link = manifest_path.parent / "contexts" / "linked.json"
        link.symlink_to(original)
        first["context_path"] = "contexts/linked.json"
    _write_manifest(manifest_path, manifest)
    with pytest.raises(ValueError, match="unsafe|symlink"):
        m5.load_bundle(SAMPLE, manifest_path)


@pytest.mark.parametrize("mutation", ["missing", "hash"])
def test_source_document_missing_or_hash_drift_fails_closed(tmp_path: Path, mutation: str) -> None:
    manifest_path = _build_bundle(tmp_path)
    if mutation == "missing":
        (manifest_path.parent / "documents" / "synthetic.txt").unlink()
    else:
        (manifest_path.parent / "documents" / "synthetic.txt").write_text("drift\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing bundle file|source document hash drift"):
        m5.load_bundle(SAMPLE, manifest_path)


def test_real_bundle_requires_coverage_and_source_approval_before_execution(tmp_path: Path) -> None:
    manifest_path = _build_bundle(tmp_path, with_evidence=False)
    manifest = _manifest(manifest_path)
    manifest["bundle_kind"] = "real"
    manifest["source_scopes"] = ["CNINFO"]
    _write_manifest(manifest_path, manifest)
    with pytest.raises(ValueError, match="source_approval_id"):
        m5.load_bundle(SAMPLE, manifest_path)

    coverage = manifest_path.parent / "coverage.json"
    coverage.write_bytes(_json_bytes(_valid_coverage_report()))
    manifest["source_approval_id"] = "approval-2026-07-18"
    manifest["coverage_report"] = {
        "path": "coverage.json",
        "sha256": hashlib.sha256(coverage.read_bytes()).hexdigest(),
        "all_layers_approved": True,
    }
    _write_manifest(manifest_path, manifest)
    loaded = m5.load_bundle(SAMPLE, manifest_path)
    assert loaded.kind == "real"
    calls = 0

    def forbidden(_context: object, _model: str) -> ModelResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("must not call")

    with pytest.raises(ValueError, match="not authorized"):
        m5.run_blind_review(SAMPLE, manifest_path, tmp_path / "run", claude_model=CLAUDE_MODEL, client=forbidden)
    assert calls == 0


@pytest.mark.parametrize("mutation", ["overall", "blocking", "row", "lineage"])
def test_real_bundle_parses_coverage_report_semantics(tmp_path: Path, mutation: str) -> None:
    manifest_path = _build_bundle(tmp_path, with_evidence=False)
    manifest = _manifest(manifest_path)
    manifest["bundle_kind"] = "real"
    manifest["source_scopes"] = ["CNINFO"]
    manifest["source_approval_id"] = "approval-2026-07-18"
    report = _valid_coverage_report()
    if mutation == "overall":
        report["overall_passed"] = False
    elif mutation == "blocking":
        report["milestone_005_approval_blocked"] = True
    elif mutation == "row":
        cast(list[dict[str, object]], report["rows"])[0]["scope_disposition"] = "re_audit_new_version"
    else:
        report["review_index_sha256"] = "not-a-hash"
    coverage = manifest_path.parent / "coverage.json"
    coverage.write_bytes(_json_bytes(report))
    manifest["coverage_report"] = {
        "path": "coverage.json",
        "sha256": hashlib.sha256(coverage.read_bytes()).hexdigest(),
        "all_layers_approved": True,
    }
    _write_manifest(manifest_path, manifest)

    with pytest.raises(ValueError, match="coverage report"):
        m5.load_bundle(SAMPLE, manifest_path)


def test_claude_failure_is_terminal_and_gemini_cannot_start(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path)
    calls = 0

    def client(context: object, model: str) -> ModelResponse:
        nonlocal calls
        assert context and model
        calls += 1
        if calls == 3:
            return ModelResponse(raw_output=None, failure_reason="synthetic failure")
        return m5.synthetic_reference_client(cast(object, context), model)  # type: ignore[arg-type]

    run_root = tmp_path / "run"
    summary = m5.run_blind_review(SAMPLE, manifest, run_root, claude_model=CLAUDE_MODEL, client=client)
    assert summary["terminal_status"] == "FAIL"
    assert calls == 3
    assert len((run_root / "claude-reference.jsonl").read_text().splitlines()) == 3
    assert not (run_root / "gemini-shadow.jsonl").exists()
    with pytest.raises(ValueError, match="cannot be resumed"):
        m5.run_shadow(
            run_root,
            gemini_client=m5.synthetic_gemini_client,
            support_client=m5.synthetic_support_client,
        )


def test_gemini_first_error_stops_persists_failure_and_skips_support(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path)
    run_root = tmp_path / "run"
    m5.run_blind_review(SAMPLE, manifest, run_root, claude_model=CLAUDE_MODEL, client=m5.synthetic_reference_client)
    calls = 0
    support_calls = 0

    def gemini(context: object, model: str) -> GeminiCallResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            return GeminiCallResult(
                status=ValidationStatus.API_TIMEOUT,
                result=None,
                raw_output=None,
                failure_reason="TIMEOUT_EXHAUSTED",
                attempts=3,
            )
        return m5.synthetic_gemini_client(cast(object, context), model)  # type: ignore[arg-type]

    def support(*_args: object) -> m5.SupportAuditResponse:
        nonlocal support_calls
        support_calls += 1
        raise AssertionError("support audit must be skipped")

    summary = m5.run_shadow(run_root, gemini_client=gemini, support_client=support)
    assert summary["terminal_status"] == "FAIL"
    assert summary["gemini_attempted"] == 2
    assert summary["gemini_http_attempts"] == 4
    assert len(cast(list[object], summary["not_attempted"])) == 34
    assert support_calls == 0
    records = [json.loads(line) for line in (run_root / "gemini-shadow.jsonl").read_text().splitlines()]
    assert records[-1]["validation_status"] == "API_TIMEOUT"
    assert not (run_root / "claude-support-audit.jsonl").exists()
    with pytest.raises(ValueError, match="cannot be resumed"):
        m5.run_shadow(run_root, gemini_client=gemini, support_client=support)


def test_input_drift_before_gemini_becomes_terminal_fail_without_calls(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path)
    run_root = tmp_path / "run"
    m5.run_blind_review(SAMPLE, manifest, run_root, claude_model=CLAUDE_MODEL, client=m5.synthetic_reference_client)
    manifest.write_bytes(manifest.read_bytes() + b" \n")
    gemini_calls = 0

    def gemini(*_args: object) -> GeminiCallResult:
        nonlocal gemini_calls
        gemini_calls += 1
        raise AssertionError("must fail before Gemini")

    with pytest.raises(ValueError, match="hash drift"):
        m5.run_shadow(run_root, gemini_client=gemini, support_client=m5.synthetic_support_client)
    assert gemini_calls == 0
    summary = json.loads((run_root / "run-summary.json").read_text())
    report = json.loads((run_root / "aggregate-report.json").read_text())
    assert summary["terminal_status"] == "FAIL"
    assert summary["failure_code"] == "INPUT_OR_REFERENCE_ARTIFACT_DRIFT"
    assert report["status"] == "FAIL"


def test_reference_artifact_hash_drift_blocks_gemini_before_any_call(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path)
    run_root = tmp_path / "run"
    m5.run_blind_review(SAMPLE, manifest, run_root, claude_model=CLAUDE_MODEL, client=m5.synthetic_reference_client)
    reference_path = run_root / "claude-reference.jsonl"
    records = [json.loads(line) for line in reference_path.read_text(encoding="utf-8").splitlines()]
    records[0]["result"] = _scored_output("2026-07-17")
    records[0]["validation_status"] = "VALID_SCORED"
    reference_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    gemini_calls = 0

    def gemini(*_args: object) -> GeminiCallResult:
        nonlocal gemini_calls
        gemini_calls += 1
        raise AssertionError("must fail before Gemini")

    with pytest.raises(ValueError, match="sealed artifact hash drift"):
        m5.run_shadow(run_root, gemini_client=gemini, support_client=m5.synthetic_support_client)
    assert gemini_calls == 0
    summary = json.loads((run_root / "run-summary.json").read_text(encoding="utf-8"))
    assert summary["terminal_status"] == "FAIL"
    assert summary["failure_code"] == "INPUT_OR_REFERENCE_ARTIFACT_DRIFT"


def test_unexpected_shadow_artifact_is_terminal_and_never_overwritten(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path)
    run_root = tmp_path / "run"
    m5.run_blind_review(SAMPLE, manifest, run_root, claude_model=CLAUDE_MODEL, client=m5.synthetic_reference_client)
    occupied = run_root / "gemini-shadow.jsonl"
    occupied.write_text("pre-existing\n", encoding="utf-8")
    calls = 0

    def gemini(*_args: object) -> GeminiCallResult:
        nonlocal calls
        calls += 1
        raise AssertionError("must fail before Gemini")

    with pytest.raises(ValueError, match="pre-existing run artifact"):
        m5.run_shadow(run_root, gemini_client=gemini, support_client=m5.synthetic_support_client)
    assert calls == 0
    assert occupied.read_text(encoding="utf-8") == "pre-existing\n"
    summary = json.loads((run_root / "run-summary.json").read_text())
    assert summary["terminal_status"] == "FAIL"
    assert summary["failure_code"] == "UNEXPECTED_RUN_ARTIFACT"


def test_complete_synthetic_run_is_provisional_secure_and_secret_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "token-must-never-appear"
    monkeypatch.setenv("GEMINI_API_KEY", token)
    manifest = _build_bundle(tmp_path)
    run_root = tmp_path / "run"
    reference = m5.run_blind_review(
        SAMPLE, manifest, run_root, claude_model=CLAUDE_MODEL, client=m5.synthetic_reference_client
    )
    assert reference["state"] == "REFERENCE_COMPLETE"
    summary = m5.run_shadow(
        run_root,
        gemini_client=m5.synthetic_gemini_client,
        support_client=m5.synthetic_support_client,
    )
    report = m5.load_report(run_root)
    assert summary["terminal_status"] == "PROVISIONAL"
    assert report["status"] == "PROVISIONAL"
    assert all(cast(dict[str, bool], report["strict_gates"]).values())
    assert stat.S_IMODE(run_root.stat().st_mode) == 0o700
    for path in run_root.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert token not in path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        m5.run_blind_review(SAMPLE, manifest, run_root, claude_model=CLAUDE_MODEL, client=m5.synthetic_reference_client)


def test_load_report_rejects_forgery_even_when_summary_hash_is_also_changed(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path)
    run_root = tmp_path / "run"
    m5.run_blind_review(SAMPLE, manifest, run_root, claude_model=CLAUDE_MODEL, client=m5.synthetic_reference_client)
    m5.run_shadow(
        run_root,
        gemini_client=m5.synthetic_gemini_client,
        support_client=m5.synthetic_support_client,
    )
    report_path = run_root / "aggregate-report.json"
    report = cast(dict[str, object], json.loads(report_path.read_text(encoding="utf-8")))
    report["status"] = "PASS"
    report_path.write_bytes(_json_bytes(report))
    summary_path = run_root / "run-summary.json"
    summary = cast(dict[str, object], json.loads(summary_path.read_text(encoding="utf-8")))
    summary["aggregate_report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    summary_path.write_bytes(_json_bytes(summary))

    with pytest.raises(ValueError, match="does not match its sealed run artifacts"):
        m5.load_report(run_root)


def test_valid_unsupported_fact_audit_completes_then_fails_strict_gate(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path)
    run_root = tmp_path / "run"
    m5.run_blind_review(SAMPLE, manifest, run_root, claude_model=CLAUDE_MODEL, client=m5.synthetic_reference_client)
    calls = 0

    def support(
        context: QualitativeContext, gemini_output: Mapping[str, object], model: str
    ) -> m5.SupportAuditResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return m5.SupportAuditResponse(
                raw_output={
                    "schema_version": "m5-support-audit-v1",
                    "code": context.code,
                    "input_hash": context.compute_input_hash(),
                    "supported": False,
                    "unsupported_facts": ["Synthetic unsupported statement."],
                }
            )
        return m5.synthetic_support_client(context, gemini_output, model)

    summary = m5.run_shadow(
        run_root,
        gemini_client=m5.synthetic_gemini_client,
        support_client=support,
    )
    report = m5.load_report(run_root)
    assert calls == 36
    assert summary["terminal_status"] == "FAIL"
    assert report["unsupported_fact_count"] == 1
    assert cast(dict[str, bool], report["strict_gates"])["unsupported_fact_count_zero"] is False


def test_actual_scored_batch_can_pass_non_provisional_gate(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path)
    bundle = m5.load_bundle(SAMPLE, manifest)
    selected: set[str] = set()
    per_super: dict[str, int] = {}
    for company in bundle.companies:
        count = per_super.get(company.sample.super_stratum, 0)
        if count < 2:
            selected.add(company.sample.code)
        per_super[company.sample.super_stratum] = count + 1

    def reference(context: QualitativeContext, _model: str) -> ModelResponse:
        if context.code in selected:
            return ModelResponse(raw_output=_scored_output(context.as_of_date))
        return m5.synthetic_reference_client(context, CLAUDE_MODEL)

    def gemini(context: QualitativeContext, _model: str) -> GeminiCallResult:
        if context.code not in selected:
            return m5.synthetic_gemini_client(context, "gemini-2.5-flash")
        raw = _scored_output(context.as_of_date)
        validation = validate_model_output(raw, context=context)
        assert validation.valid and validation.result is not None
        return GeminiCallResult(
            status=ValidationStatus.VALID_SCORED,
            result=validation.result,
            raw_output=raw,
            failure_reason=None,
            attempts=1,
        )

    run_root = tmp_path / "run"
    m5.run_blind_review(SAMPLE, manifest, run_root, claude_model=CLAUDE_MODEL, client=reference)
    summary = m5.run_shadow(
        run_root,
        gemini_client=gemini,
        support_client=m5.synthetic_support_client,
    )
    report = m5.load_report(run_root)
    assert summary["terminal_status"] == "PASS"
    assert report["status"] == "PASS"
    for dimension in cast(dict[str, dict[str, object]], report["dimensions"]).values():
        assert dimension["reference_scored"] == 12
        assert dimension["agreement_required"] == 11
        assert dimension["six_super_strata_covered"] is True


def test_report_integer_agreement_completeness_and_selection_bias(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path)
    run_root = tmp_path / "run"
    m5.run_blind_review(SAMPLE, manifest, run_root, claude_model=CLAUDE_MODEL, client=m5.synthetic_reference_client)
    m5.run_shadow(
        run_root,
        gemini_client=m5.synthetic_gemini_client,
        support_client=m5.synthetic_support_client,
    )
    bundle = m5.load_bundle(SAMPLE, manifest)
    summary = cast(dict[str, object], json.loads((run_root / "run-summary.json").read_text()))
    summary["terminal_status"] = None
    reference = [json.loads(line) for line in (run_root / "claude-reference.jsonl").read_text().splitlines()]
    gemini = [json.loads(line) for line in (run_root / "gemini-shadow.jsonl").read_text().splitlines()]
    selected: set[str] = set()
    per_super: dict[str, int] = {}
    for company in bundle.companies:
        count = per_super.get(company.sample.super_stratum, 0)
        if count < 2:
            selected.add(company.sample.code)
        per_super[company.sample.super_stratum] = count + 1
    for ref_record, gemini_record in zip(reference, gemini, strict=True):
        if ref_record["code"] in selected:
            ref_record["result"] = _scored_output(bundle.as_of_date)
            gemini_record["result"] = _scored_output(bundle.as_of_date)
            ref_record["validation_status"] = "VALID_SCORED"
            gemini_record["validation_status"] = "VALID_SCORED"
    (run_root / "claude-reference.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in reference), encoding="utf-8"
    )
    (run_root / "gemini-shadow.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in gemini), encoding="utf-8"
    )
    report = m5.build_aggregate_report(run_root, summary, bundle)
    assert report["status"] == "PASS"
    moat = cast(dict[str, object], cast(dict[str, object], report["dimensions"])["moat"])
    assert moat["agreement_required"] == 11
    assert moat["six_super_strata_covered"] is True
    assert moat["non_provisional_sample_complete"] is True
    risks = cast(list[dict[str, object]], report["selection_bias_risks"])
    assert any(item["layer_type"] == "market_cap_stratum" and item["layer"] == "high" for item in risks)

    changed = 0
    for record in gemini:
        if record["code"] in selected and changed < 2:
            record["result"] = _scored_output(bundle.as_of_date, scores=(3, 1, 2))
            changed += 1
    (run_root / "gemini-shadow.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in gemini), encoding="utf-8"
    )
    failed = m5.build_aggregate_report(run_root, summary, bundle)
    assert failed["status"] == "FAIL"
    assert (
        cast(dict[str, object], cast(dict[str, object], failed["dimensions"])["moat"])["blind_reference_agreement_pass"]
        is False
    )


def test_cli_help_preview_and_synthetic_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = _build_bundle(tmp_path)
    assert cli_main(["preview", "--sample", str(SAMPLE), "--bundle", str(manifest)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["validated"] is True
    run_root = tmp_path / "cli-run"
    assert (
        cli_main(
            [
                "blind-review",
                "--sample",
                str(SAMPLE),
                "--bundle",
                str(manifest),
                "--run-root",
                str(run_root),
                "--claude-model",
                CLAUDE_MODEL,
                "--execute-claude",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert cli_main(["shadow", "--from-run", str(run_root), "--execute-gemini"]) == 0
    capsys.readouterr()
    assert cli_main(["report", "--from-run", str(run_root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "PROVISIONAL"


def test_moving_claude_alias_is_rejected_before_artifact(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path)
    run_root = tmp_path / "run"
    with pytest.raises(ValueError, match="moving aliases"):
        m5.run_blind_review(SAMPLE, manifest, run_root, claude_model="sonnet", client=m5.synthetic_reference_client)
    assert not run_root.exists()
