from __future__ import annotations

import json
from pathlib import Path

import pytest

from qualitative_v2_audit import FrozenArtifactError, HashDriftError
from qualitative_v2_audit_artifacts import AuditStore


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
