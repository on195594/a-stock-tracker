"""Create-only storage and hash-chain verification for MILESTONE-004."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from qualitative_v2_audit import BlobRef, FrozenArtifactError, HashDriftError, canonical_json_bytes


DEFAULT_BLOB_ROOT = Path("artifacts/milestone-004/v1.1")
DEFAULT_MANIFEST_ROOT = Path("reviews/milestone-004-audit-v1.1")


@dataclass(frozen=True, slots=True)
class FrozenStage:
    stage: str
    path: Path
    sha256: str
    parent_sha256: str | None


class AuditStore:
    """Manage local blobs and versioned, forward-only manifest stages."""

    _STAGE_RANK = {
        "frame": 0,
        "sample": 1,
        "corpus": 2,
        "reviewer-a-seal": 3,
        "reviewer-b-seal": 3,
        "review-index": 4,
        "adjudication": 5,
        "report": 6,
    }

    def __init__(
        self, blob_root: str | Path = DEFAULT_BLOB_ROOT, manifest_root: str | Path = DEFAULT_MANIFEST_ROOT
    ) -> None:
        self.blob_root = Path(blob_root)
        self.manifest_root = Path(manifest_root)
        self.blob_root.mkdir(parents=True, exist_ok=True)
        self.manifest_root.mkdir(parents=True, exist_ok=True)
        self._assert_directory(self.blob_root)
        self._assert_directory(self.manifest_root)

    @staticmethod
    def _assert_directory(path: Path) -> None:
        if path.is_symlink() or not path.is_dir():
            raise HashDriftError(f"audit root is not a real directory: {path}")

    def _safe_blob_path(self, relative_path: str | Path, *, require_exists: bool = False) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts or relative in (Path(""), Path(".")):
            raise HashDriftError(f"unsafe blob path: {relative_path}")
        candidate = self.blob_root / relative
        root_resolved = self.blob_root.resolve(strict=True)
        cursor = self.blob_root
        for part in relative.parts[:-1]:
            cursor /= part
            if cursor.is_symlink():
                raise HashDriftError(f"symlink in blob path: {relative_path}")
            if cursor.exists() and not cursor.is_dir():
                raise HashDriftError(f"non-directory in blob path: {relative_path}")
            if require_exists and not cursor.exists():
                raise HashDriftError(f"missing blob directory: {relative_path}")
            cursor.mkdir(exist_ok=True)
        if candidate.is_symlink():
            raise HashDriftError(f"blob may not be a symlink: {relative_path}")
        try:
            candidate.parent.resolve(strict=True).relative_to(root_resolved)
        except (OSError, ValueError) as exc:
            raise HashDriftError(f"blob path escapes root: {relative_path}") from exc
        if require_exists and (not candidate.exists() or not candidate.is_file()):
            raise HashDriftError(f"missing blob: {relative_path}")
        return candidate

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _detect_mime(path: Path) -> str:
        with path.open("rb") as stream:
            prefix = stream.read(512).lstrip()
        if prefix.startswith(b"%PDF-"):
            return "application/pdf"
        if prefix.startswith((b"{", b"[")):
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            else:
                return "application/json"
        if prefix.lower().startswith((b"<!doctype html", b"<html")):
            return "text/html"
        guessed, _ = mimetypes.guess_type(path.name)
        return guessed or "application/octet-stream"

    def store_blob(self, source_path: str | Path, relative_path: str | Path, mime_type: str) -> BlobRef:
        """Copy a local file into create-only blob storage and return its verified reference."""
        source = Path(source_path)
        if source.is_symlink() or not source.is_file():
            raise HashDriftError(f"source blob must be a regular file: {source}")
        target = self._safe_blob_path(relative_path)
        if target.exists():
            raise FrozenArtifactError(f"blob target already exists: {target}")
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as output, source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if target.exists():
                raise FrozenArtifactError(f"blob target already exists: {target}")
            os.replace(temp_name, target)
            target.chmod(0o444)
        finally:
            Path(temp_name).unlink(missing_ok=True)
        sha256, byte_count = self._hash_file(target)
        detected = self._detect_mime(target)
        return BlobRef(str(Path(relative_path)), sha256, byte_count, mime_type, detected)

    def verify_blob(self, ref: BlobRef | Mapping[str, Any]) -> BlobRef:
        """Fail closed on path, size, hash, or detected-MIME drift."""
        blob = (
            ref
            if isinstance(ref, BlobRef)
            else BlobRef(
                relative_path=str(ref["relative_path"]),
                sha256=str(ref["sha256"]),
                byte_count=int(ref["byte_count"]),
                mime_type=str(ref["mime_type"]),
                detected_mime_type=str(ref["detected_mime_type"])
                if ref.get("detected_mime_type") is not None
                else None,
            )
        )
        path = self._safe_blob_path(blob.relative_path, require_exists=True)
        sha256, byte_count = self._hash_file(path)
        if sha256 != blob.sha256 or byte_count != blob.byte_count:
            raise HashDriftError(f"blob size/hash drift: {blob.relative_path}")
        detected = self._detect_mime(path)
        if blob.detected_mime_type is not None and detected != blob.detected_mime_type:
            raise HashDriftError(f"blob MIME drift: {blob.relative_path}")
        return blob

    @staticmethod
    def _manifest_sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _frozen_stages(self) -> list[FrozenStage]:
        stages: list[FrozenStage] = []
        for path in sorted(self.manifest_root.glob("*.json")):
            if path.is_symlink():
                raise HashDriftError(f"manifest may not be a symlink: {path}")
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HashDriftError(f"invalid manifest JSON: {path}") from exc
            stages.append(FrozenStage(str(data["stage"]), path, self._manifest_sha(path), data.get("parent_sha256")))
        return stages

    def _validate_stage_order(self, stage: str, parent: Path | None) -> None:
        if stage not in self._STAGE_RANK:
            raise FrozenArtifactError(f"unknown audit stage: {stage}")
        stages = self._frozen_stages()
        same_stage = [item for item in stages if item.stage == stage]
        if same_stage:
            raise FrozenArtifactError(f"stage is create-only and already frozen: {stage}")
        rank = self._STAGE_RANK[stage]
        if rank == 0:
            if parent is not None or stages:
                raise FrozenArtifactError("frame must be the first local stage")
            return
        if (
            parent is None
            or parent.is_symlink()
            or not parent.is_file()
            or parent.parent.resolve() != self.manifest_root.resolve()
        ):
            raise FrozenArtifactError(f"stage {stage} requires a local parent manifest")
        parent_data = json.loads(parent.read_text(encoding="utf-8"))
        parent_rank = self._STAGE_RANK.get(str(parent_data.get("stage")), -1)
        if stage in {"reviewer-a-seal", "reviewer-b-seal"}:
            if parent_rank not in {2, 3}:
                raise FrozenArtifactError("reviewer seals must follow corpus or the peer seal")
        elif rank != parent_rank + 1:
            raise FrozenArtifactError(f"invalid stage transition: {parent_data.get('stage')} -> {stage}")

    def freeze_stage(
        self, stage: str, payload: object, parent_manifest: str | Path | FrozenStage | None = None
    ) -> FrozenStage:
        """Atomically freeze one canonical manifest without overwriting prior state."""
        parent_path = (
            parent_manifest.path
            if isinstance(parent_manifest, FrozenStage)
            else Path(parent_manifest)
            if parent_manifest
            else None
        )
        self._validate_stage_order(stage, parent_path)
        parent_sha = self._manifest_sha(parent_path) if parent_path else None
        envelope = {
            "canonical_json": "UTF-8;LF;recursive-key-sort;compact;ensure_ascii=false;allow_nan=false",
            "parent_manifest": parent_path.name if parent_path else None,
            "parent_sha256": parent_sha,
            "payload": payload,
            "stage": stage,
        }
        raw = canonical_json_bytes(envelope)
        sha256 = hashlib.sha256(raw).hexdigest()
        target = self.manifest_root / f"{self._STAGE_RANK[stage]:02d}-{stage}-{sha256}.json"
        if target.exists():
            raise FrozenArtifactError(f"manifest already exists: {target}")
        fd, temp_name = tempfile.mkstemp(prefix=f".{stage}.", dir=self.manifest_root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists():
                raise FrozenArtifactError(f"manifest already exists: {target}")
            os.replace(temp_name, target)
            target.chmod(0o444)
        finally:
            Path(temp_name).unlink(missing_ok=True)
        return FrozenStage(stage, target, sha256, parent_sha)

    @staticmethod
    def _blob_dicts(value: object) -> Iterator[Mapping[str, Any]]:
        if isinstance(value, Mapping):
            keys = set(value)
            if {"relative_path", "sha256", "byte_count", "mime_type"} <= keys:
                yield value
            for item in value.values():
                yield from AuditStore._blob_dicts(item)
        elif isinstance(value, list):
            for item in value:
                yield from AuditStore._blob_dicts(item)

    def verify_chain(self) -> tuple[FrozenStage, ...]:
        """Verify canonical bytes, every parent edge, and every referenced blob."""
        stages = self._frozen_stages()
        by_name = {item.path.name: item for item in stages}
        for item in stages:
            raw = item.path.read_bytes()
            if not item.path.stem.endswith(item.sha256):
                raise HashDriftError(f"manifest content hash drift: {item.path}")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise HashDriftError(f"invalid manifest: {item.path}") from exc
            if canonical_json_bytes(data) != raw:
                raise HashDriftError(f"non-canonical or modified manifest: {item.path}")
            parent_name = data.get("parent_manifest")
            parent_sha = data.get("parent_sha256")
            if parent_name is None:
                if parent_sha is not None or item.stage != "frame":
                    raise HashDriftError(f"invalid root manifest: {item.path}")
            else:
                parent = by_name.get(str(parent_name))
                if parent is None or parent.sha256 != parent_sha:
                    raise HashDriftError(f"parent hash drift: {item.path}")
                parent_rank = self._STAGE_RANK.get(parent.stage, -1)
                rank = self._STAGE_RANK.get(item.stage, -1)
                if item.stage in {"reviewer-a-seal", "reviewer-b-seal"}:
                    valid_transition = parent_rank in {2, 3}
                else:
                    valid_transition = rank == parent_rank + 1
                if not valid_transition:
                    raise HashDriftError(f"invalid stage transition: {parent.stage} -> {item.stage}")
            for blob in self._blob_dicts(data.get("payload")):
                self.verify_blob(blob)
        return tuple(stages)


__all__ = ["AuditStore", "DEFAULT_BLOB_ROOT", "DEFAULT_MANIFEST_ROOT", "FrozenStage"]
