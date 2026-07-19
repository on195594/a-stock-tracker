"""Create-only storage and hash-chain verification for MILESTONE-004."""

from __future__ import annotations

import fcntl
import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from a_stock_tracker.qualitative.audit import BlobRef, FrozenArtifactError, HashDriftError, canonical_json_bytes


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

    _CANONICAL_JSON = "UTF-8;LF;recursive-key-sort;compact;ensure_ascii=false;allow_nan=false"
    _ENVELOPE_FIELDS = frozenset({"canonical_json", "parent_manifest", "parent_sha256", "payload", "stage"})
    _LOCK_FILENAME = ".audit-store.lock"
    _REVIEW_INDEX_BINDINGS = {
        "corpus": "corpus_stage_sha256",
        "reviewer-a-seal": "reviewer_a_seal_stage_sha256",
        "reviewer-b-seal": "reviewer_b_seal_stage_sha256",
    }
    _REVIEWER_SEAL_STAGES = frozenset({"reviewer-a-seal", "reviewer-b-seal"})

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
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as output, source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
                os.fchmod(output.fileno(), 0o444)
                os.fsync(output.fileno())
            self._publish_create_only(temp_path, target)
        finally:
            temp_path.unlink(missing_ok=True)
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

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _publish_create_only(cls, temp_path: Path, target: Path) -> None:
        """Atomically publish a completed file without replacing an existing target."""
        try:
            os.link(temp_path, target)
        except FileExistsError as exc:
            raise FrozenArtifactError(f"artifact target already exists: {target}") from exc
        temp_path.unlink()
        cls._fsync_directory(target.parent)

    def _acquire_manifest_lock(self) -> int:
        lock_path = self.manifest_root / self._LOCK_FILENAME
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise FrozenArtifactError(f"manifest lock is not a safe regular file: {lock_path}") from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise FrozenArtifactError(f"manifest lock is not a safe regular file: {lock_path}")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            current = os.stat(lock_path, follow_symlinks=False)
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise FrozenArtifactError(f"manifest lock changed while acquiring it: {lock_path}")
        except OSError as exc:
            os.close(descriptor)
            raise FrozenArtifactError(f"could not acquire manifest lock: {lock_path}") from exc
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    @staticmethod
    def _release_manifest_lock(descriptor: int) -> None:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    @classmethod
    def _envelope_error(cls, value: object) -> str | None:
        if not isinstance(value, Mapping):
            return "manifest envelope must be a JSON object"
        if set(value) != cls._ENVELOPE_FIELDS:
            return "manifest envelope has missing or unknown fields"
        if value["canonical_json"] != cls._CANONICAL_JSON:
            return "manifest canonical JSON declaration is invalid"
        stage = value["stage"]
        if not isinstance(stage, str) or stage not in cls._STAGE_RANK:
            return "manifest stage is unknown or invalid"
        parent_name = value["parent_manifest"]
        parent_sha256 = value["parent_sha256"]
        if parent_name is not None and (
            not isinstance(parent_name, str)
            or not parent_name
            or parent_name != Path(parent_name).name
            or "\\" in parent_name
            or not parent_name.endswith(".json")
        ):
            return "manifest parent filename is invalid"
        if parent_sha256 is not None and (
            not isinstance(parent_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", parent_sha256) is None
        ):
            return "manifest parent SHA-256 is invalid"
        if (parent_name is None) != (parent_sha256 is None):
            return "manifest parent filename and SHA-256 must both be null or both be strings"
        return None

    @classmethod
    def _load_manifest_envelope(cls, path: Path) -> Mapping[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        error = cls._envelope_error(value)
        if error is not None:
            raise ValueError(error)
        assert isinstance(value, Mapping)
        return value

    def _frozen_stages(self) -> list[FrozenStage]:
        stages: list[FrozenStage] = []
        try:
            paths = sorted(self.manifest_root.glob("*.json"))
        except OSError as exc:
            raise HashDriftError(f"could not enumerate manifests: {self.manifest_root}") from exc
        for path in paths:
            if path.is_symlink():
                raise HashDriftError(f"manifest may not be a symlink: {path}")
            try:
                data = self._load_manifest_envelope(path)
                sha256 = self._manifest_sha(path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise HashDriftError(f"invalid manifest envelope: {path}") from exc
            stage = data["stage"]
            parent_sha256 = data["parent_sha256"]
            assert isinstance(stage, str)
            assert parent_sha256 is None or isinstance(parent_sha256, str)
            stages.append(FrozenStage(stage, path, sha256, parent_sha256))
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
        try:
            invalid_parent = (
                parent is None
                or parent.is_symlink()
                or not parent.is_file()
                or parent.parent.resolve() != self.manifest_root.resolve()
            )
        except OSError as exc:
            raise FrozenArtifactError(f"could not inspect parent manifest for stage {stage}") from exc
        if invalid_parent:
            raise FrozenArtifactError(f"stage {stage} requires a local parent manifest")
        assert parent is not None
        try:
            parent_data = self._load_manifest_envelope(parent)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise FrozenArtifactError(f"invalid parent manifest for stage {stage}: {parent}") from exc
        parent_stage = parent_data["stage"]
        assert isinstance(parent_stage, str)
        parent_rank = self._STAGE_RANK[parent_stage]
        if stage == "review-index" and not self._REVIEWER_SEAL_STAGES <= {item.stage for item in stages}:
            raise FrozenArtifactError("review-index requires both reviewer seals")
        if stage in {"reviewer-a-seal", "reviewer-b-seal"}:
            if parent_rank not in {2, 3}:
                raise FrozenArtifactError("reviewer seals must follow corpus or the peer seal")
        elif rank != parent_rank + 1:
            raise FrozenArtifactError(f"invalid stage transition: {parent_stage} -> {stage}")

    @classmethod
    def _review_index_binding_error(cls, payload: object, stages: list[FrozenStage]) -> str | None:
        if not isinstance(payload, Mapping):
            return "review-index payload must be an object with upstream stage hashes"
        for stage_name, payload_key in cls._REVIEW_INDEX_BINDINGS.items():
            matches = [item for item in stages if item.stage == stage_name]
            if len(matches) != 1:
                return f"review-index requires exactly one {stage_name} stage"
            if payload.get(payload_key) != matches[0].sha256:
                return f"review-index {payload_key} does not match frozen {stage_name} stage"
        return None

    def freeze_stage(
        self, stage: str, payload: object, parent_manifest: str | Path | FrozenStage | None = None
    ) -> FrozenStage:
        """Freeze one canonical manifest under a process-safe create-only transaction."""
        lock_descriptor = self._acquire_manifest_lock()
        try:
            return self._freeze_stage_locked(stage, payload, parent_manifest)
        finally:
            self._release_manifest_lock(lock_descriptor)

    def _freeze_stage_locked(
        self, stage: str, payload: object, parent_manifest: str | Path | FrozenStage | None
    ) -> FrozenStage:
        parent_path = (
            parent_manifest.path
            if isinstance(parent_manifest, FrozenStage)
            else Path(parent_manifest)
            if parent_manifest
            else None
        )
        self._validate_stage_order(stage, parent_path)
        if stage == "review-index":
            stages = list(self.verify_chain())
            binding_error = self._review_index_binding_error(payload, stages)
            if binding_error is not None:
                raise FrozenArtifactError(binding_error)
        try:
            parent_sha = self._manifest_sha(parent_path) if parent_path else None
        except OSError as exc:
            raise FrozenArtifactError(f"could not hash parent manifest for stage {stage}") from exc
        envelope = {
            "canonical_json": self._CANONICAL_JSON,
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
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o444)
                os.fsync(stream.fileno())
            self._publish_create_only(temp_path, target)
        finally:
            temp_path.unlink(missing_ok=True)
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
        seen_stages: set[str] = set()
        for item in stages:
            if item.stage in seen_stages:
                raise HashDriftError(f"audit chain contains duplicate stage: {item.stage}")
            seen_stages.add(item.stage)
        stage_names = {item.stage for item in stages}
        if "review-index" in stage_names and not self._REVIEWER_SEAL_STAGES <= stage_names:
            raise HashDriftError("review-index exists without both reviewer seals")
        by_name = {item.path.name: item for item in stages}
        envelope_by_path: dict[Path, Mapping[str, Any]] = {}
        for item in stages:
            try:
                raw = item.path.read_bytes()
                raw_sha256 = hashlib.sha256(raw).hexdigest()
                if raw_sha256 != item.sha256 or not item.path.stem.endswith(raw_sha256):
                    raise HashDriftError(f"manifest content hash drift: {item.path}")
                data = json.loads(raw)
                envelope_error = self._envelope_error(data)
                if envelope_error is not None:
                    raise HashDriftError(f"invalid manifest envelope: {item.path}: {envelope_error}")
                assert isinstance(data, Mapping)
                if canonical_json_bytes(data) != raw:
                    raise HashDriftError(f"non-canonical or modified manifest: {item.path}")
            except HashDriftError:
                raise
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise HashDriftError(f"could not validate manifest: {item.path}") from exc
            envelope_by_path[item.path] = data
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
                try:
                    self.verify_blob(blob)
                except HashDriftError:
                    raise
                except (OSError, KeyError, TypeError, ValueError) as exc:
                    raise HashDriftError(f"invalid blob reference in manifest: {item.path}") from exc
        review_indexes = [item for item in stages if item.stage == "review-index"]
        if review_indexes:
            review_index_data = envelope_by_path[review_indexes[0].path]
            binding_error = self._review_index_binding_error(review_index_data.get("payload"), stages)
            if binding_error is not None:
                raise HashDriftError(binding_error)
        return tuple(stages)


__all__ = ["AuditStore", "DEFAULT_BLOB_ROOT", "DEFAULT_MANIFEST_ROOT", "FrozenStage"]
