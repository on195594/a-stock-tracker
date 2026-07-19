from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from a_stock_tracker.qualitative.audit import FrozenArtifactError, HashDriftError, canonical_json_bytes
from a_stock_tracker.qualitative.archive_m4.audit_artifacts import AuditStore, FrozenStage


def _review_index_payload(corpus: FrozenStage, reviewer_a: FrozenStage, reviewer_b: FrozenStage) -> dict[str, str]:
    return {
        "corpus_stage_sha256": corpus.sha256,
        "reviewer_a_seal_stage_sha256": reviewer_a.sha256,
        "reviewer_b_seal_stage_sha256": reviewer_b.sha256,
    }


def test_create_only_stage_chain_and_blob_verification(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nsynthetic")
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    blob = store.store_blob(source, "000001/doc.pdf", "application/pdf")
    frame = store.freeze_stage("frame", {"code_version": "one"})
    sample = store.freeze_stage("sample", {"selection": [1]}, frame)
    store.freeze_stage("corpus", {"blob": blob}, sample)
    verified = store.verify_chain()
    assert [item.stage for item in verified] == ["frame", "sample", "corpus"]
    assert blob.byte_count == len(source.read_bytes())
    assert blob.detected_mime_type == "application/pdf"


def test_freeze_rejects_overwrite_and_bad_order(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    frame = store.freeze_stage("frame", {"x": 1})
    with pytest.raises(FrozenArtifactError):
        store.freeze_stage("frame", {"x": 2})
    with pytest.raises(FrozenArtifactError):
        store.freeze_stage("corpus", {"x": 2}, frame)


def test_concurrent_different_payloads_freeze_only_one_stage(tmp_path: Path) -> None:
    blob_root = tmp_path / "blobs"
    manifest_root = tmp_path / "manifests"
    stores = (AuditStore(blob_root, manifest_root), AuditStore(blob_root, manifest_root))
    start = threading.Barrier(2)

    def freeze(store: AuditStore, value: int) -> str:
        start.wait()
        try:
            store.freeze_stage("frame", {"x": value})
        except FrozenArtifactError:
            return "blocked"
        return "frozen"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(freeze, stores, (1, 2)))

    assert sorted(results) == ["blocked", "frozen"]
    assert len(list(manifest_root.glob("00-frame-*.json"))) == 1


@pytest.mark.parametrize("unsafe_kind", ["symlink", "directory"])
def test_manifest_lock_must_be_a_safe_regular_file(tmp_path: Path, unsafe_kind: str) -> None:
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    lock_path = tmp_path / "manifests" / ".audit-store.lock"
    if unsafe_kind == "symlink":
        target = tmp_path / "outside-lock"
        target.write_text("unsafe", encoding="utf-8")
        lock_path.symlink_to(target)
    else:
        lock_path.mkdir()

    with pytest.raises(FrozenArtifactError, match="manifest lock"):
        store.freeze_stage("frame", {"x": 1})


def test_manifest_and_blob_tampering_propagate(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"local":true}', encoding="utf-8")
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    blob = store.store_blob(source, "doc.json", "application/json")
    frame = store.freeze_stage("frame", {"blob": blob})
    stored_blob = tmp_path / "blobs" / "doc.json"
    stored_blob.chmod(0o644)
    stored_blob.write_text("changed", encoding="utf-8")
    with pytest.raises(HashDriftError, match="drift"):
        store.verify_chain()
    assert frame.path.exists()


def test_path_traversal_and_symlink_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("safe", encoding="utf-8")
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    with pytest.raises(HashDriftError, match="unsafe"):
        store.store_blob(source, "../escape.txt", "text/plain")
    (tmp_path / "blobs" / "link").symlink_to(tmp_path)
    with pytest.raises(HashDriftError, match="symlink"):
        store.store_blob(source, "link/escape.txt", "text/plain")


def test_store_blob_does_not_replace_target_created_during_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("candidate", encoding="utf-8")
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    original_link = os.link

    def occupy_target_then_link(source_path: str | Path, target_path: str | Path) -> None:
        Path(target_path).write_bytes(b"concurrent-winner")
        original_link(source_path, target_path)

    monkeypatch.setattr("a_stock_tracker.qualitative.archive_m4.audit_artifacts.os.link", occupy_target_then_link)
    with pytest.raises(FrozenArtifactError, match="already exists"):
        store.store_blob(source, "race.txt", "text/plain")
    assert (tmp_path / "blobs" / "race.txt").read_bytes() == b"concurrent-winner"


def test_freeze_stage_does_not_replace_target_created_during_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    occupied_targets: list[Path] = []
    original_link = os.link

    def occupy_target_then_link(source_path: str | Path, target_path: str | Path) -> None:
        target = Path(target_path)
        target.write_bytes(b"concurrent-winner")
        occupied_targets.append(target)
        original_link(source_path, target)

    monkeypatch.setattr("a_stock_tracker.qualitative.archive_m4.audit_artifacts.os.link", occupy_target_then_link)
    with pytest.raises(FrozenArtifactError, match="already exists"):
        store.freeze_stage("frame", {"x": 1})
    assert len(occupied_targets) == 1
    assert occupied_targets[0].read_bytes() == b"concurrent-winner"


def test_parent_hash_drift_is_not_an_ignorable_boolean(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    frame = store.freeze_stage("frame", {"x": 1})
    sample = store.freeze_stage("sample", {"x": 2}, frame)
    sample.path.chmod(0o644)
    value = json.loads(sample.path.read_text(encoding="utf-8"))
    value["parent_sha256"] = "0" * 64
    sample.path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(HashDriftError):
        store.verify_chain()


def test_verify_chain_rejects_duplicate_stage_files(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    store.freeze_stage("frame", {"x": 1})
    duplicate = {
        "canonical_json": "UTF-8;LF;recursive-key-sort;compact;ensure_ascii=false;allow_nan=false",
        "parent_manifest": None,
        "parent_sha256": None,
        "payload": {"x": 2},
        "stage": "frame",
    }
    duplicate_bytes = canonical_json_bytes(duplicate)
    duplicate_sha256 = hashlib.sha256(duplicate_bytes).hexdigest()
    (tmp_path / "manifests" / f"00-frame-{duplicate_sha256}.json").write_bytes(duplicate_bytes)

    with pytest.raises(HashDriftError, match="duplicate stage: frame"):
        store.verify_chain()


@pytest.mark.parametrize("malformed", [{}, []])
def test_verify_chain_wraps_malformed_manifest_envelopes(tmp_path: Path, malformed: object) -> None:
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    (tmp_path / "manifests" / "00-malformed.json").write_text(json.dumps(malformed), encoding="utf-8")

    with pytest.raises(HashDriftError, match="invalid manifest envelope"):
        store.verify_chain()


@pytest.mark.parametrize("malformed", [{}, []])
def test_freeze_wraps_malformed_explicit_parent_envelopes(tmp_path: Path, malformed: object) -> None:
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    parent = tmp_path / "manifests" / "parent.data"
    parent.write_text(json.dumps(malformed), encoding="utf-8")

    with pytest.raises(FrozenArtifactError, match="invalid parent manifest"):
        store.freeze_stage("sample", {"x": 1}, parent)


def test_review_index_requires_both_reviewer_seals(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    frame = store.freeze_stage("frame", {"x": 1})
    sample = store.freeze_stage("sample", {"x": 2}, frame)
    corpus = store.freeze_stage("corpus", {"x": 3}, sample)
    reviewer_a = store.freeze_stage("reviewer-a-seal", {"reviewer": "a"}, corpus)

    with pytest.raises(FrozenArtifactError, match="both reviewer seals"):
        store.freeze_stage("review-index", {"x": 4}, reviewer_a)

    reviewer_b = store.freeze_stage("reviewer-b-seal", {"reviewer": "b"}, corpus)
    store.freeze_stage("review-index", _review_index_payload(corpus, reviewer_a, reviewer_b), reviewer_b)
    assert [stage.stage for stage in store.verify_chain()][-1] == "review-index"


def test_chain_rejects_index_if_non_parent_reviewer_seal_is_missing(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    frame = store.freeze_stage("frame", {"x": 1})
    sample = store.freeze_stage("sample", {"x": 2}, frame)
    corpus = store.freeze_stage("corpus", {"x": 3}, sample)
    reviewer_a = store.freeze_stage("reviewer-a-seal", {"reviewer": "a"}, corpus)
    reviewer_b = store.freeze_stage("reviewer-b-seal", {"reviewer": "b"}, corpus)
    store.freeze_stage("review-index", _review_index_payload(corpus, reviewer_a, reviewer_b), reviewer_b)

    reviewer_a.path.unlink()
    with pytest.raises(HashDriftError, match="without both reviewer seals"):
        store.verify_chain()


def test_review_index_rejects_incorrect_upstream_stage_hash(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    frame = store.freeze_stage("frame", {"x": 1})
    sample = store.freeze_stage("sample", {"x": 2}, frame)
    corpus = store.freeze_stage("corpus", {"x": 3}, sample)
    reviewer_a = store.freeze_stage("reviewer-a-seal", {"reviewer": "a"}, corpus)
    reviewer_b = store.freeze_stage("reviewer-b-seal", {"reviewer": "b"}, corpus)
    payload = _review_index_payload(corpus, reviewer_a, reviewer_b)
    payload["reviewer_a_seal_stage_sha256"] = "0" * 64

    with pytest.raises(FrozenArtifactError, match="reviewer_a_seal_stage_sha256"):
        store.freeze_stage("review-index", payload, reviewer_b)


def test_chain_rejects_tampered_review_index_stage_binding(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "blobs", tmp_path / "manifests")
    frame = store.freeze_stage("frame", {"x": 1})
    sample = store.freeze_stage("sample", {"x": 2}, frame)
    corpus = store.freeze_stage("corpus", {"x": 3}, sample)
    reviewer_a = store.freeze_stage("reviewer-a-seal", {"reviewer": "a"}, corpus)
    reviewer_b = store.freeze_stage("reviewer-b-seal", {"reviewer": "b"}, corpus)
    review_index = store.freeze_stage("review-index", _review_index_payload(corpus, reviewer_a, reviewer_b), reviewer_b)

    review_index.path.chmod(0o644)
    value = json.loads(review_index.path.read_text(encoding="utf-8"))
    value["payload"]["corpus_stage_sha256"] = "0" * 64
    tampered_bytes = canonical_json_bytes(value)
    tampered_sha256 = hashlib.sha256(tampered_bytes).hexdigest()
    tampered_path = review_index.path.with_name(f"04-review-index-{tampered_sha256}.json")
    review_index.path.write_bytes(tampered_bytes)
    review_index.path.rename(tampered_path)

    with pytest.raises(HashDriftError, match="corpus_stage_sha256"):
        store.verify_chain()
