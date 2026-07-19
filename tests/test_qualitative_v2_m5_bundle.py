"""Offline tests for create-only construction of real M5 bundles."""

from __future__ import annotations

import csv
import hashlib
import json
import stat
from pathlib import Path
from typing import cast

import pytest

import a_stock_tracker.qualitative.m5.pipeline as m5
from a_stock_tracker.qualitative.m5.bundle import build_real_bundle
from scripts.build_qualitative_v2_m5_bundle import main as cli_main

SAMPLE = Path(__file__).parent / "fixtures" / "milestone005" / "sample.csv"


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()


def _coverage_report() -> dict[str, object]:
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


def _build_staging(tmp_path: Path) -> Path:
    staging = tmp_path / "staging"
    contexts = staging / "contexts"
    documents = staging / "documents"
    contexts.mkdir(parents=True)
    documents.mkdir()
    document = documents / "cnipa-record.json"
    document.write_bytes(b'{"record":"fixture-only"}\n')
    document_sha256 = hashlib.sha256(document.read_bytes()).hexdigest()
    coverage = staging / "coverage-report.json"
    coverage.write_bytes(_json_bytes(_coverage_report()))

    rows = list(csv.DictReader(SAMPLE.read_text(encoding="utf-8").splitlines()))
    companies: list[dict[str, object]] = []
    for index, row in enumerate(rows):
        context = {
            "code": row["ts_code"],
            "name": row["name"],
            "industry": row["industry_name"],
            "as_of_date": "2026-07-17",
            "schema_version": "qualitative-score-v2",
            "rubric_version": "rubric-v1",
            "taxonomy_version": "taxonomy-v1",
            "evidence": [
                {
                    "evidence_id": f"ip.fixture.{index:02d}",
                    "evidence_type": "ip_record",
                    "claim_category": "competitive_moat",
                    "allowed_dimensions": ["moat"],
                    "directness": "direct",
                    "freshness_policy": "max_age_365d",
                    "value": "fixture active patent",
                    "unit": None,
                    "source": "CNIPA fixture",
                    "source_date": "2026-01-01",
                    "freshness_status": "fresh",
                    "state_type": "status",
                    "effective_until": "2030-01-01",
                }
            ],
        }
        context_name = f"{index:02d}-{row['ts_code']}.json"
        (contexts / context_name).write_bytes(_json_bytes(context))
        companies.append(
            {
                "code": row["ts_code"],
                "context_path": f"contexts/{context_name}",
                "evidence_provenance": [
                    {
                        "evidence_id": f"ip.fixture.{index:02d}",
                        "official_source": True,
                        "source_scope": "cnipa",
                        "document_path": "documents/cnipa-record.json",
                        "document_sha256": document_sha256,
                        "locator": f"fixture:{index}",
                    }
                ],
            }
        )
    input_manifest = {
        "input_version": "m5-real-bundle-input-v1",
        "as_of_date": "2026-07-17",
        "source_approval_id": "m5-data-readiness-test-only",
        "source_scopes": ["cnipa"],
        "coverage_report_path": "coverage-report.json",
        "companies": companies,
    }
    path = staging / "bundle-input.json"
    path.write_bytes(_json_bytes(input_manifest))
    return path


def test_build_real_bundle_is_private_deduplicated_and_self_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_manifest = _build_staging(tmp_path)
    output = tmp_path / "published" / "bundle"
    monkeypatch.setenv("GEMINI_API_KEY", "builder-must-not-read-this")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "builder-must-not-read-this-either")

    result = build_real_bundle(SAMPLE, input_manifest, output)

    assert result["validated"] is True
    assert result["bundle_kind"] == "real"
    assert result["company_count"] == 36
    assert result["sample_sha256"] == m5.EXPECTED_SAMPLE_SHA256
    assert len(list((output / "documents").iterdir())) == 1
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    for path in output.rglob("*"):
        expected_mode = 0o700 if path.is_dir() else 0o600
        assert stat.S_IMODE(path.stat().st_mode) == expected_mode
    loaded = m5.load_bundle(SAMPLE, output / "bundle-manifest.json")
    assert loaded.source_approval_id == "m5-data-readiness-test-only"
    assert loaded.coverage_report_sha256 == hashlib.sha256((output / "coverage-report.json").read_bytes()).hexdigest()


def test_build_real_bundle_rejects_failed_coverage_before_output(tmp_path: Path) -> None:
    input_manifest = _build_staging(tmp_path)
    coverage = input_manifest.parent / "coverage-report.json"
    value = cast(dict[str, object], json.loads(coverage.read_text(encoding="utf-8")))
    value["overall_passed"] = False
    coverage.write_bytes(_json_bytes(value))
    output = tmp_path / "bundle"

    with pytest.raises(ValueError, match="did not pass overall"):
        build_real_bundle(SAMPLE, input_manifest, output)

    assert not output.exists()


@pytest.mark.parametrize("unsafe", ["traversal", "symlink"])
def test_build_real_bundle_rejects_unsafe_context_paths_and_cleans_temp(tmp_path: Path, unsafe: str) -> None:
    input_manifest = _build_staging(tmp_path)
    value = cast(dict[str, object], json.loads(input_manifest.read_text(encoding="utf-8")))
    companies = cast(list[dict[str, object]], value["companies"])
    if unsafe == "traversal":
        companies[0]["context_path"] = "../outside.json"
    else:
        original = input_manifest.parent / cast(str, companies[0]["context_path"])
        link = input_manifest.parent / "contexts" / "linked.json"
        link.symlink_to(original)
        companies[0]["context_path"] = "contexts/linked.json"
    input_manifest.write_bytes(_json_bytes(value))
    output_parent = tmp_path / "published"
    output = output_parent / "bundle"

    with pytest.raises(ValueError, match="unsafe bundle path|symlink bundle path"):
        build_real_bundle(SAMPLE, input_manifest, output)

    assert not output.exists()
    assert list(output_parent.iterdir()) == []


def test_build_real_bundle_is_create_only(tmp_path: Path) -> None:
    input_manifest = _build_staging(tmp_path)
    output = tmp_path / "bundle"
    output.mkdir()

    with pytest.raises(ValueError, match="already exists"):
        build_real_bundle(SAMPLE, input_manifest, output)


def test_real_bundle_builder_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    input_manifest = _build_staging(tmp_path)
    output = tmp_path / "bundle"

    assert cli_main(["--sample", str(SAMPLE), "--input", str(input_manifest), "--output", str(output)]) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["validated"] is True
    assert printed["bundle_kind"] == "real"
