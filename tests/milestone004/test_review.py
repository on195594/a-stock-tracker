from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import qualitative_v2_audit_review as review_module
from qualitative_v2_audit import (
    BlobRef,
    CandidateCorpus,
    CandidateDocument,
    RelationshipReport,
    SUPER_STRATA,
    SampleEntry,
    SampleManifest,
    canonical_json_bytes,
)
from qualitative_v2_audit_review import (
    InvocationPreview,
    ReviewAttempt,
    ReviewBundle,
    ReviewValidationError,
    ReviewerExecutionAuthorization,
    ReviewerSpec,
    build_coverage_report,
    build_review_index,
    compare_reviews,
    create_review_bundle,
    run_reviewer,
    seal_review,
    validate_adjudication,
    validate_review_output,
)


def frozen_corpus_manifest(root: Path, corpus: CandidateCorpus, prefix: str = "") -> Path:
    parent_raw = canonical_json_bytes({"stage": "sample", "synthetic": True})
    parent_sha = hashlib.sha256(parent_raw).hexdigest()
    parent = root / f"{prefix}01-sample-{parent_sha}.json"
    parent.write_bytes(parent_raw)
    parent.chmod(0o444)
    manifest_raw = canonical_json_bytes(
        {
            "canonical_json": "UTF-8;LF;recursive-key-sort;compact;ensure_ascii=false;allow_nan=false",
            "parent_manifest": parent.name,
            "parent_sha256": parent_sha,
            "payload": corpus,
            "stage": "corpus",
        }
    )
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    manifest = root / f"{prefix}02-corpus-{manifest_sha}.json"
    manifest.write_bytes(manifest_raw)
    manifest.chmod(0o444)
    return manifest


def corpus_fixture(tmp_path: Path) -> tuple[CandidateCorpus, Path, Path]:
    blob_root = tmp_path / "blobs"
    blob_root.mkdir()
    raw = b"%PDF-1.7\nlocal evidence"
    sha = hashlib.sha256(raw).hexdigest()
    blob_path = blob_root / "doc.pdf"
    blob_path.write_bytes(raw)
    blob = BlobRef("doc.pdf", sha, len(raw), "application/pdf", "application/pdf")
    entry = SampleEntry("000001.SH", "合成公司", "801780.SI", "银行", "金融地产", "low", Decimal("1"), "b" * 64)
    sample = SampleManifest("a" * 64, date(2026, 7, 14), "qualitative-v2-m4-prereg-v1", (entry,))
    document = CandidateDocument("000001.SH", "SSE", "专利", 1, 1, "本地文档", "https://sse.com.cn/doc", blob)
    corpus = CandidateCorpus("a" * 64, sample, (document,), (RelationshipReport(entry.ts_code, blob),), (), "complete")
    manifest = frozen_corpus_manifest(tmp_path, corpus)
    return corpus, manifest, blob_root


def successful_attempt(bundle: ReviewBundle, spec: ReviewerSpec, value: dict[str, object]) -> ReviewAttempt:
    preview = run_reviewer(bundle, spec)
    assert isinstance(preview, InvocationPreview)
    executed_preview = replace(preview, execute=True)
    raw = canonical_json_bytes(value)
    return ReviewAttempt(spec.reviewer.upper(), "success", executed_preview, 0, raw, b"", None, value)


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
    assert a.input_dir.stat().st_mode & 0o222 == 0


def test_bundle_paths_are_absolute_and_workspace_parent_cannot_be_linked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    bundle = create_review_bundle("relative-reviewer", corpus, manifest, blob_root, "prompt")
    assert all(
        path.is_absolute()
        for path in (
            bundle.workspace,
            bundle.input_dir,
            bundle.output_dir,
            bundle.cache_dir,
            bundle.state_dir,
            bundle.tmp_dir,
        )
    )
    preview = run_reviewer(bundle, ReviewerSpec("A", "codex", "model", sys.executable))
    assert isinstance(preview, InvocationPreview)
    assert Path(preview.cwd).is_absolute()
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(Exception, match="parent may not traverse symlinks"):
        create_review_bundle(linked_parent / "reviewer", corpus, manifest, blob_root, "prompt")


def test_preview_is_default_and_authorization_must_match(tmp_path: Path) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    spec = ReviewerSpec("A", "codex", "synthetic-model", sys.executable)
    preview = run_reviewer(bundle, spec)
    assert isinstance(preview, InvocationPreview)
    assert preview.execute is False and "--ephemeral" in preview.command
    authorization = ReviewerExecutionAuthorization.from_preview(preview)
    with pytest.raises(Exception, match="authorization"):
        run_reviewer(bundle, spec, True, replace(authorization, protocol_sha256="0" * 64))
    with pytest.raises(Exception, match="authorization"):
        run_reviewer(bundle, spec, True, replace(authorization, command_sha256="0" * 64))
    with pytest.raises(Exception, match="authorization"):
        run_reviewer(bundle, spec, True, replace(authorization, environment_keys=("HOME",)))


def test_reviewer_environment_is_an_explicit_backend_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle_a = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    bundle_b = create_review_bundle(tmp_path / "reviewer-b", corpus, manifest, blob_root, "prompt")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unrelated")
    monkeypatch.setenv("GITHUB_TOKEN", "unrelated")
    monkeypatch.setenv("GEMINI_API_KEY", "unrelated")
    monkeypatch.setenv("OPENAI_API_KEY", "codex-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "agy-only")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    preview_a = run_reviewer(bundle_a, ReviewerSpec("A", "codex", "model-a", sys.executable))
    preview_b = run_reviewer(bundle_b, ReviewerSpec("B", "agy", "model-b", sys.executable))
    assert isinstance(preview_a, InvocationPreview) and isinstance(preview_b, InvocationPreview)
    assert "OPENAI_API_KEY" in preview_a.environment_keys and "ANTHROPIC_API_KEY" not in preview_a.environment_keys
    assert "ANTHROPIC_API_KEY" in preview_b.environment_keys and "OPENAI_API_KEY" not in preview_b.environment_keys
    assert {"AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "GEMINI_API_KEY"}.isdisjoint(preview_a.environment_keys)


@pytest.mark.parametrize("namespace", ["cache", "state", "tmp"])
def test_reviewer_retry_rejects_linked_writable_namespaces(tmp_path: Path, namespace: str) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    spec = ReviewerSpec("A", "codex", "model", sys.executable)
    assert isinstance(run_reviewer(bundle, spec), InvocationPreview)
    path = bundle.workspace / namespace
    if namespace == "state":
        (path / "home").rmdir()
    path.rmdir()
    outside = tmp_path / f"outside-{namespace}"
    outside.mkdir()
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(Exception, match="namespace escaped"):
        run_reviewer(bundle, spec)


def test_reviewer_retry_rejects_linked_home(tmp_path: Path) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    spec = ReviewerSpec("A", "codex", "model", sys.executable)
    assert isinstance(run_reviewer(bundle, spec), InvocationPreview)
    home = bundle.state_dir / "home"
    home.rmdir()
    outside = tmp_path / "outside-home"
    outside.mkdir()
    home.symlink_to(outside, target_is_directory=True)
    with pytest.raises(Exception, match="HOME"):
        run_reviewer(bundle, spec)


def test_reviewer_namespaces_must_be_exact_and_distinct(tmp_path: Path) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    crossed = replace(bundle, cache_dir=bundle.tmp_dir)
    with pytest.raises(Exception, match="distinct frozen workspace paths"):
        run_reviewer(crossed, ReviewerSpec("A", "codex", "model", sys.executable))


def test_bundle_rejects_manifest_drift_and_blob_path_traversal(tmp_path: Path) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    changed = replace(corpus, technical_status="pending_retry")
    with pytest.raises(ReviewValidationError, match="does not match"):
        create_review_bundle(tmp_path / "stale", changed, manifest, blob_root, "prompt")

    outside = tmp_path / "outside.pdf"
    outside.write_bytes((blob_root / "doc.pdf").read_bytes())
    bad_blob = replace(corpus.documents[0].blob, relative_path="../outside.pdf")
    bad_document = replace(corpus.documents[0], blob=bad_blob)
    bad_relationship = RelationshipReport("000001.SH", bad_blob)
    escaped = replace(corpus, documents=(bad_document,), relationship_reports=(bad_relationship,))
    parent = manifest.parent / "parent.json"
    parent_raw = canonical_json_bytes({"stage": "sample"})
    parent.write_bytes(parent_raw)
    parent.chmod(0o444)
    raw = canonical_json_bytes(
        {
            "canonical_json": "UTF-8;LF;recursive-key-sort;compact;ensure_ascii=false;allow_nan=false",
            "parent_manifest": parent.name,
            "parent_sha256": hashlib.sha256(parent_raw).hexdigest(),
            "payload": escaped,
            "stage": "corpus",
        }
    )
    escaped_manifest = manifest.parent / f"02-corpus-{hashlib.sha256(raw).hexdigest()}.json"
    escaped_manifest.write_bytes(raw)
    escaped_manifest.chmod(0o444)
    with pytest.raises(ReviewValidationError, match="unsafe corpus blob path"):
        create_review_bundle(tmp_path / "escaped", escaped, escaped_manifest, blob_root, "prompt")


def test_execution_and_sealing_rehash_frozen_input(tmp_path: Path) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    bundle.input_dir.chmod(0o755)
    (bundle.input_dir / "injected.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(ReviewValidationError, match="writable|changed"):
        run_reviewer(bundle, ReviewerSpec("A", "codex", "model", sys.executable))


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


def test_patent_relationship_must_belong_to_the_labeled_company(tmp_path: Path) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    other_company_sha = "d" * 64
    crossed = replace(
        bundle,
        relationship_sha256_by_ts_code=(
            ("000001.SH", corpus.relationship_reports[0].blob.sha256),
            ("000002.SH", other_company_sha),
        ),
    )
    value = label(crossed, decision="accept", reason="core_active_patent")
    evaluation = value["document_evaluations"][0]  # type: ignore[index]
    evaluation["relationship_document_sha256"] = other_company_sha  # type: ignore[index]
    with pytest.raises(ReviewValidationError, match="does not belong"):
        validate_review_output(crossed, value)


def test_seal_index_compare_and_complete_adjudication(tmp_path: Path) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle_a = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    bundle_b = create_review_bundle(tmp_path / "reviewer-b", corpus, manifest, blob_root, "prompt")
    spec_a = ReviewerSpec("A", "codex", "model-a", sys.executable)
    spec_b = ReviewerSpec("B", "agy", "model-b", sys.executable)
    value_a = validate_review_output(bundle_a, label(bundle_a))
    value_b = validate_review_output(
        bundle_b, label(bundle_b, decision="accept", reason="exclusive_right", rationale="有排他权")
    )
    attempt_a = successful_attempt(bundle_a, spec_a, value_a)
    attempt_b = successful_attempt(bundle_b, spec_b, value_b)
    seal_a = seal_review(bundle_a, spec_a, attempt_a, tmp_path / "seals")
    seal_b = seal_review(bundle_b, spec_b, attempt_b, tmp_path / "seals")
    index = build_review_index(seal_a, seal_b)
    disagreements = compare_reviews(index)
    assert len(disagreements) == 1 and "decision" in disagreements[0].fields
    final_label = value_b["document_evaluations"][0]
    adjudication: dict[str, Any] = {
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
    assert validate_adjudication(index, disagreements, adjudication, corpus) == adjudication
    partial = dict(adjudication, decisions=[])
    with pytest.raises(Exception, match="every"):
        validate_adjudication(index, disagreements, partial, corpus)

    invalid_label = dict(final_label, reason_code="count_only")
    invalid_adjudication = {
        **adjudication,
        "decisions": [{**adjudication["decisions"][0], "final_label": invalid_label}],
    }
    with pytest.raises(Exception, match="semantic"):
        validate_adjudication(index, disagreements, invalid_adjudication, corpus)

    malformed_adjudications = (
        {**adjudication, "adjudication_version": True},
        {**adjudication, "unexpected": True},
        {**adjudication, "scope_dispositions": []},
        {
            **adjudication,
            "decisions": [{**adjudication["decisions"][0], "rationale": True}],
        },
        {
            **adjudication,
            "decisions": [{**adjudication["decisions"][0], "rationale": "x" * 4097}],
        },
        {
            **adjudication,
            "decisions": [
                {
                    **adjudication["decisions"][0],
                    "disposition": "rerun_new_version",
                }
            ],
        },
    )
    for malformed in malformed_adjudications:
        with pytest.raises(Exception):
            validate_adjudication(index, disagreements, malformed, corpus)


def test_seal_binds_executed_attempt_stdout_and_full_preview_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    spec = ReviewerSpec("A", "codex", "model-a", sys.executable)
    monkeypatch.setenv("OPENAI_API_KEY", "credential-at-run-time")
    value = label(bundle)
    attempt = successful_attempt(bundle, spec, value)
    with pytest.raises(ReviewValidationError, match="provenance"):
        seal_review(bundle, replace(spec, model="model-b"), attempt, tmp_path / "wrong-model")
    with pytest.raises(ReviewValidationError, match="stdout"):
        seal_review(
            bundle,
            spec,
            replace(attempt, stdout=canonical_json_bytes(label(bundle, rationale="different"))),
            tmp_path / "wrong-stdout",
        )
    with pytest.raises(ReviewValidationError, match="executed"):
        seal_review(
            bundle, spec, replace(attempt, preview=replace(attempt.preview, execute=False)), tmp_path / "preview"
        )
    invalid_keys = tuple(sorted((*attempt.preview.environment_keys, "AWS_SECRET_ACCESS_KEY")))
    with pytest.raises(ReviewValidationError, match="environment keys"):
        seal_review(
            bundle,
            spec,
            replace(attempt, preview=replace(attempt.preview, environment_keys=invalid_keys)),
            tmp_path / "invalid-env",
        )
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "credential-after-run")
    seal = seal_review(bundle, spec, attempt, tmp_path / "changed-env")
    assert seal.output_path.is_file()


def test_atomic_report_requires_bound_manifest_two_seals_and_complete_adjudication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, _, blob_root = corpus_fixture(tmp_path)
    super_strata = tuple(SUPER_STRATA)
    entries = tuple(
        SampleEntry(
            f"{number:06d}.SH",
            f"合成公司{number}",
            "801780.SI",
            "银行",
            super_strata[(number - 1) // 6],
            "low" if (number - 1) % 6 < 3 else "high",
            Decimal(number),
            f"{number:064x}"[-64:],
        )
        for number in range(1, 37)
    )
    sample = replace(corpus.sample, entries=entries)
    relationships = tuple(RelationshipReport(entry.ts_code, corpus.relationship_reports[0].blob) for entry in entries)
    complete_corpus = replace(corpus, sample=sample, relationship_reports=relationships)
    manifest = frozen_corpus_manifest(tmp_path, complete_corpus, "complete-")
    bundle_a = create_review_bundle(tmp_path / "reviewer-a", complete_corpus, manifest, blob_root, "prompt")
    bundle_b = create_review_bundle(tmp_path / "reviewer-b", complete_corpus, manifest, blob_root, "prompt")
    spec_a = ReviewerSpec("A", "codex", "model-a", sys.executable)
    spec_b = ReviewerSpec("B", "agy", "model-b", sys.executable)
    seal_a = seal_review(bundle_a, spec_a, successful_attempt(bundle_a, spec_a, label(bundle_a)), tmp_path / "seals")
    seal_b = seal_review(bundle_b, spec_b, successful_attempt(bundle_b, spec_b, label(bundle_b)), tmp_path / "seals")
    index = build_review_index(seal_a, seal_b)
    adjudication = {
        "adjudication_version": "1",
        "parent_review_index_sha256": index.index_sha256,
        "decisions": [],
    }
    real_read = review_module._read_regular_bytes
    seal_reads: list[Path] = []

    def recording_read(path: Path, limit: int) -> tuple[bytes, int]:
        if path in {seal_a.output_path, seal_b.output_path}:
            seal_reads.append(path)
        return real_read(path, limit)

    monkeypatch.setattr(review_module, "_read_regular_bytes", recording_read)
    report = build_coverage_report(complete_corpus, manifest, index, adjudication)
    assert len(report.rows) == 8
    assert report.corpus_manifest_sha256 == index.corpus_manifest_sha256
    assert report.review_index_sha256 == index.index_sha256
    assert seal_reads.count(seal_a.output_path) == 1
    assert seal_reads.count(seal_b.output_path) == 1
    bundle_a.input_dir.chmod(0o755)
    injected = bundle_a.input_dir / "empty-injected-directory"
    injected.mkdir()
    injected.chmod(0o555)
    bundle_a.input_dir.chmod(0o555)
    with pytest.raises(ReviewValidationError, match="bundle changed"):
        build_coverage_report(complete_corpus, manifest, index, adjudication)
    with pytest.raises(ReviewValidationError, match="manifest"):
        build_coverage_report(replace(complete_corpus, technical_status="pending_retry"), manifest, index, adjudication)


def test_index_rejects_shared_cache_and_changed_corpus(tmp_path: Path) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle_a = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    bundle_b = create_review_bundle(tmp_path / "reviewer-b", corpus, manifest, blob_root, "prompt")
    spec_a = ReviewerSpec("A", "codex", "a", sys.executable)
    spec_b = ReviewerSpec("B", "agy", "b", sys.executable)
    seal_a = seal_review(
        bundle_a,
        spec_a,
        successful_attempt(bundle_a, spec_a, label(bundle_a)),
        tmp_path / "seals",
    )
    seal_b = seal_review(
        bundle_b,
        spec_b,
        successful_attempt(bundle_b, spec_b, label(bundle_b)),
        tmp_path / "seals",
    )
    with pytest.raises(Exception, match="workspaces and caches"):
        build_review_index(seal_a, replace(seal_b, cache_dir=seal_a.cache_dir))
    with pytest.raises(Exception, match="corpus"):
        build_review_index(seal_a, replace(seal_b, corpus_manifest_sha256="0" * 64))


def test_seal_path_is_absolute_bounded_and_rejects_symlink_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    spec = ReviewerSpec("A", "codex", "model", sys.executable)
    attempt = successful_attempt(bundle, spec, label(bundle))
    real_dir = tmp_path / "real-seals"
    real_dir.mkdir()
    linked_dir = tmp_path / "linked-seals"
    linked_dir.symlink_to(real_dir, target_is_directory=True)
    with pytest.raises(Exception, match="real directory"):
        seal_review(bundle, spec, attempt, linked_dir)
    race_dir = tmp_path / "race-seals"
    real_link = os.link

    def racing_link(source: str, target: Path, *, follow_symlinks: bool) -> None:
        Path(target).write_bytes(b"winner")
        real_link(source, target, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(review_module.os, "link", racing_link)
    with pytest.raises(Exception, match="already exists"):
        seal_review(bundle, spec, attempt, race_dir)
    assert (race_dir / "reviewer-a-labels.json").read_bytes() == b"winner"
    monkeypatch.setattr(review_module.os, "link", real_link)
    seal = seal_review(bundle, spec, attempt, real_dir)
    assert seal.output_path.is_absolute()
    oversized = b"{" + b"x" * review_module.STDOUT_LIMIT
    seal.output_path.chmod(0o644)
    seal.output_path.write_bytes(oversized)
    seal.output_path.chmod(0o444)
    forged = replace(
        seal,
        output_sha256=hashlib.sha256(oversized).hexdigest(),
        byte_count=len(oversized),
    )
    with pytest.raises(ReviewValidationError, match="size limit"):
        build_review_index(forged, replace(forged, reviewer="B", backend="agy", workspace=str(tmp_path / "other")))


def test_harmless_subprocess_nonzero_and_output_limit_are_technical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    spec = ReviewerSpec("A", "codex", "model", sys.executable, timeout_seconds=10)
    script = tmp_path / "helper.py"
    script.write_text("import sys; sys.stdout.write('x' * (4 * 1024 * 1024 + 1))", encoding="utf-8")
    monkeypatch.setattr(review_module, "_command", lambda *args: (sys.executable, str(script)))
    preview = run_reviewer(bundle, spec)
    assert isinstance(preview, InvocationPreview)
    authorization = ReviewerExecutionAuthorization.from_preview(preview)
    real_killpg = os.killpg
    signals: list[int] = []

    def recording_killpg(process_group: int, sent_signal: int) -> None:
        signals.append(sent_signal)
        real_killpg(process_group, sent_signal)

    monkeypatch.setattr(review_module.os, "killpg", recording_killpg)
    attempt = run_reviewer(bundle, spec, True, authorization)
    assert isinstance(attempt, ReviewAttempt) and attempt.error_code == "output_limit"
    assert len(attempt.stdout) == review_module.STDOUT_LIMIT and signals
    script.write_text("import sys; sys.stderr.write('x' * (1024 * 1024 + 1))", encoding="utf-8")
    attempt = run_reviewer(bundle, spec, True, authorization)
    assert isinstance(attempt, ReviewAttempt) and attempt.error_code == "output_limit"
    assert len(attempt.stderr) == review_module.STDERR_LIMIT
    script.write_text("raise SystemExit(7)", encoding="utf-8")
    attempt = run_reviewer(bundle, spec, True, authorization)
    assert isinstance(attempt, ReviewAttempt) and attempt.error_code == "nonzero_exit"


def test_harmless_subprocess_timeout_terminates_process_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    spec = ReviewerSpec("A", "codex", "model", sys.executable, timeout_seconds=0.1)
    script = tmp_path / "sleep.py"
    script.write_text("import time; time.sleep(30)", encoding="utf-8")
    monkeypatch.setattr(review_module, "_command", lambda *args: (sys.executable, str(script)))
    preview = run_reviewer(bundle, spec)
    assert isinstance(preview, InvocationPreview)
    authorization = ReviewerExecutionAuthorization.from_preview(preview)
    real_killpg = os.killpg
    signals: list[int] = []

    def recording_killpg(process_group: int, sent_signal: int) -> None:
        signals.append(sent_signal)
        real_killpg(process_group, sent_signal)

    monkeypatch.setattr(review_module.os, "killpg", recording_killpg)
    attempt = run_reviewer(bundle, spec, True, authorization)
    assert isinstance(attempt, ReviewAttempt) and attempt.error_code == "timeout"
    assert signals


def test_subprocess_value_errors_become_technical_and_started_process_is_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, manifest, blob_root = corpus_fixture(tmp_path)
    bundle = create_review_bundle(tmp_path / "reviewer-a", corpus, manifest, blob_root, "prompt")
    spec = ReviewerSpec("A", "codex", "model", sys.executable, timeout_seconds=1)
    preview = run_reviewer(bundle, spec)
    assert isinstance(preview, InvocationPreview)
    authorization = ReviewerExecutionAuthorization.from_preview(preview)
    real_popen = review_module.subprocess.Popen
    monkeypatch.setattr(review_module.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError()))
    attempt = run_reviewer(bundle, spec, True, authorization)
    assert isinstance(attempt, ReviewAttempt) and attempt.error_code == "spawn_error"

    script = tmp_path / "sleep-on-io-error.py"
    script.write_text("import time; time.sleep(30)", encoding="utf-8")
    monkeypatch.setattr(review_module.subprocess, "Popen", real_popen)
    monkeypatch.setattr(review_module, "_command", lambda *args: (sys.executable, str(script)))
    changed_preview = run_reviewer(bundle, spec)
    assert isinstance(changed_preview, InvocationPreview)
    authorization = ReviewerExecutionAuthorization.from_preview(changed_preview)
    started: list[object] = []

    def broken_reader(process: object, timeout: float) -> tuple[bytes, bytes, str | None]:
        started.append(process)
        raise ValueError

    monkeypatch.setattr(review_module, "_read_reviewer_output", broken_reader)
    attempt = run_reviewer(bundle, spec, True, authorization)
    assert isinstance(attempt, ReviewAttempt) and attempt.error_code == "io_error"
    assert started and started[0].poll() is not None  # type: ignore[attr-defined]
