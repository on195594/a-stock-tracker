from __future__ import annotations

import json
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import qualitative_v2_audit_review as review_module
from qualitative_v2_audit import BlobRef, CandidateCorpus, CandidateDocument, SampleEntry, SampleManifest
from qualitative_v2_audit_review import (
    InvocationPreview,
    ReviewAttempt,
    ReviewBundle,
    ReviewValidationError,
    ReviewerExecutionAuthorization,
    ReviewerSpec,
    build_review_index,
    compare_reviews,
    create_review_bundle,
    run_reviewer,
    seal_review,
    validate_adjudication,
    validate_review_output,
)


def corpus_fixture(tmp_path: Path) -> tuple[CandidateCorpus, Path, Path]:
    blob_root = tmp_path / "blobs"
    blob_root.mkdir()
    raw = b"%PDF-1.7\nlocal evidence"
    import hashlib

    sha = hashlib.sha256(raw).hexdigest()
    blob_path = blob_root / "doc.pdf"
    blob_path.write_bytes(raw)
    blob = BlobRef("doc.pdf", sha, len(raw), "application/pdf", "application/pdf")
    entry = SampleEntry("000001.SH", "合成公司", "801780.SI", "银行", "金融地产", "low", Decimal("1"), "b" * 64)
    sample = SampleManifest("a" * 64, date(2026, 7, 14), "qualitative-v2-m4-prereg-v1", (entry,))
    document = CandidateDocument("000001.SH", "SSE", "专利", 1, 1, "本地文档", "https://sse.com.cn/doc", blob)
    corpus = CandidateCorpus("a" * 64, sample, (document,), (blob,), (), "complete")
    manifest = tmp_path / "corpus-manifest.json"
    manifest.write_text('{"synthetic":true}\n', encoding="utf-8")
    return corpus, manifest, blob_root


def label(
    bundle: ReviewBundle, *, decision: str = "reject", reason: str = "count_only", rationale: str = "仅有数量"
) -> dict[str, object]:
    return {
        "schema_version": "m4-review-label-v1",
        "protocol_sha256": bundle.protocol_sha256,
        "corpus_manifest_sha256": bundle.corpus_manifest_sha256,
        "document_evaluations": [
            {
                "document_sha256": bundle.document_keys[0][2],
                "ts_code": "000001.SH",
                "decision": decision,
                "reason_code": reason,
                "locator": "p.1",
                "quoted_text": "合成文本",
                "subject_entity": "合成公司" if decision == "accept" else None,
                "effective_until": None,
                "effectiveness_basis": "截至抽样日有效" if decision == "accept" else None,
                "relationship_document_sha256": "c" * 64 if reason == "core_active_patent" else None,
                "relationship_locator": "年报 p.2" if reason == "core_active_patent" else None,
                "rationale": rationale,
            }
        ],
    }


def test_bundles_are_byte_identical_but_isolated(tmp_path: Path) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    a = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "same prompt")
    b = create_review_bundle(tmp_path / "reviewer-b", corpus, manifest, blob_root, "same prompt")
    assert a.bundle_sha256 == b.bundle_sha256
    assert a.workspace != b.workspace and a.cache_dir != b.cache_dir
    assert (a.input_dir / "corpus" / next((a.input_dir / "corpus").iterdir()).name).stat().st_mode & 0o222 == 0


def test_preview_is_default_and_authorization_must_match(tmp_path: Path) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    spec = ReviewerSpec("A", "codex", "synthetic-model", sys.executable)
    preview = run_reviewer(bundle, spec)
    assert isinstance(preview, InvocationPreview)
    assert preview.execute is False and "--ephemeral" in preview.command
    with pytest.raises(Exception, match="authorization"):
        run_reviewer(bundle, spec, True, ReviewerExecutionAuthorization("0" * 64, bundle.corpus_manifest_sha256, "A"))


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["document_evaluations"].clear(),
        lambda value: value["document_evaluations"].append(dict(value["document_evaluations"][0])),
        lambda value: value["document_evaluations"][0].update(reason_code="unknown"),
        lambda value: value["document_evaluations"][0].update(
            decision="accept", reason_code="exclusive_right", subject_entity=None
        ),
    ],
)
def test_label_omission_duplicate_unknown_and_missing_accept_fields_fail(tmp_path: Path, mutator: object) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    value = label(bundle)
    mutator(value)  # type: ignore[operator]
    with pytest.raises(ReviewValidationError):
        validate_review_output(bundle, value)


def test_seal_index_compare_and_complete_adjudication(tmp_path: Path) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle_a = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    bundle_b = create_review_bundle(tmp_path / "reviewer-b", corpus, manifest, blob_root, "prompt")
    spec_a = ReviewerSpec("A", "codex", "model-a", sys.executable)
    spec_b = ReviewerSpec("B", "agy", "model-b", sys.executable)
    preview_a = run_reviewer(bundle_a, spec_a)
    preview_b = run_reviewer(bundle_b, spec_b)
    value_a = validate_review_output(bundle_a, label(bundle_a))
    value_b = validate_review_output(
        bundle_b, label(bundle_b, decision="accept", reason="exclusive_right", rationale="有排他权")
    )
    attempt_a = ReviewAttempt("A", "success", preview_a, 0, b"", b"", None, value_a)  # type: ignore[arg-type]
    attempt_b = ReviewAttempt("B", "success", preview_b, 0, b"", b"", None, value_b)  # type: ignore[arg-type]
    seal_a = seal_review(bundle_a, spec_a, attempt_a, tmp_path / "seals")
    seal_b = seal_review(bundle_b, spec_b, attempt_b, tmp_path / "seals")
    index = build_review_index(seal_a, seal_b)
    disagreements = compare_reviews(index)
    assert len(disagreements) == 1 and "decision" in disagreements[0].fields
    final_label = value_b["document_evaluations"][0]
    adjudication = {
        "adjudication_version": "1",
        "parent_review_index_sha256": index.index_sha256,
        "decisions": [
            {
                "disagreement_id": disagreements[0].disagreement_id,
                "disposition": "accept",
                "final_label": final_label,
                "rationale": "用户采用直接排他证据",
            }
        ],
    }
    assert validate_adjudication(index, disagreements, adjudication) == adjudication
    partial = dict(adjudication, decisions=[])
    with pytest.raises(Exception, match="every"):
        validate_adjudication(index, disagreements, partial)


def test_index_rejects_shared_cache_and_changed_corpus(tmp_path: Path) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle_a = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    bundle_b = create_review_bundle(tmp_path / "reviewer-b", corpus, manifest, blob_root, "prompt")
    spec_a = ReviewerSpec("A", "codex", "a", sys.executable)
    spec_b = ReviewerSpec("B", "agy", "b", sys.executable)
    preview_a = run_reviewer(bundle_a, spec_a)
    preview_b = run_reviewer(bundle_b, spec_b)
    assert isinstance(preview_a, InvocationPreview)
    assert isinstance(preview_b, InvocationPreview)
    seal_a = seal_review(
        bundle_a,
        spec_a,
        ReviewAttempt("A", "success", preview_a, 0, b"", b"", None, label(bundle_a)),
        tmp_path / "seals",
    )
    seal_b = seal_review(
        bundle_b,
        spec_b,
        ReviewAttempt("B", "success", preview_b, 0, b"", b"", None, label(bundle_b)),
        tmp_path / "seals",
    )
    from dataclasses import replace

    with pytest.raises(Exception, match="workspaces and caches"):
        build_review_index(seal_a, replace(seal_b, cache_dir=seal_a.cache_dir))
    with pytest.raises(Exception, match="same frozen input"):
        build_review_index(seal_a, replace(seal_b, corpus_manifest_sha256="0" * 64))


def test_harmless_subprocess_nonzero_and_output_limit_are_technical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    spec = ReviewerSpec("A", "codex", "model", sys.executable, timeout_seconds=10)
    authorization = ReviewerExecutionAuthorization(bundle.protocol_sha256, bundle.corpus_manifest_sha256, "A")
    script = tmp_path / "helper.py"
    script.write_text("import sys; sys.stdout.write('x' * (4 * 1024 * 1024 + 1))", encoding="utf-8")
    monkeypatch.setattr(review_module, "_command", lambda *args: (sys.executable, str(script)))
    attempt = run_reviewer(bundle, spec, True, authorization)
    assert isinstance(attempt, ReviewAttempt) and attempt.error_code == "output_limit"
    script.write_text("raise SystemExit(7)", encoding="utf-8")
    attempt = run_reviewer(bundle, spec, True, authorization)
    assert isinstance(attempt, ReviewAttempt) and attempt.error_code == "nonzero_exit"


def test_harmless_subprocess_timeout_terminates_process_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    spec = ReviewerSpec("A", "codex", "model", sys.executable, timeout_seconds=0.1)
    authorization = ReviewerExecutionAuthorization(bundle.protocol_sha256, bundle.corpus_manifest_sha256, "A")
    script = tmp_path / "sleep.py"
    script.write_text("import time; time.sleep(30)", encoding="utf-8")
    monkeypatch.setattr(review_module, "_command", lambda *args: (sys.executable, str(script)))
    real_killpg = os.killpg
    signals: list[int] = []

    def recording_killpg(process_group: int, sent_signal: int) -> None:
        signals.append(sent_signal)
        real_killpg(process_group, sent_signal)

    monkeypatch.setattr(review_module.os, "killpg", recording_killpg)
    attempt = run_reviewer(bundle, spec, True, authorization)
    assert isinstance(attempt, ReviewAttempt) and attempt.error_code == "timeout"
    assert signals
