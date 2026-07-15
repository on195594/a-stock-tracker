"""Isolated, opt-in reviewer execution and deterministic adjudication helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Mapping, Sequence, cast

from qualitative_v2_audit import (
    AuditBlockedError,
    CandidateCorpus,
    CoverageReport,
    FrozenArtifactError,
    IsolationViolationError,
    UnadjudicatedDisagreementError,
    ValidatedAuditLineage,
    _build_coverage_report_from_lineage,
    _create_validated_audit_lineage,
    canonical_json_bytes,
)


LABEL_SCHEMA_VERSION = "m4-review-label-v1"
STDOUT_LIMIT = 4 * 1024 * 1024
STDERR_LIMIT = 1 * 1024 * 1024
MANIFEST_LIMIT = 16 * 1024 * 1024
ACCEPT_REASONS = {"exclusive_right", "concession", "core_active_patent", "exclusive_contract"}
REJECT_REASONS = {
    "count_only",
    "pending",
    "no_restriction_mechanism",
    "not_core_linked",
    "assertion_or_rank",
    "financial_only",
    "not_effective_as_of",
    "entity_out_of_scope",
    "relationship_unproven",
    "outside_inference_required",
}
LABEL_FIELDS = (
    "document_sha256",
    "ts_code",
    "decision",
    "reason_code",
    "locator",
    "quoted_text",
    "subject_entity",
    "effective_until",
    "effectiveness_basis",
    "relationship_document_sha256",
    "relationship_locator",
    "rationale",
)


class ReviewValidationError(AuditBlockedError):
    """Raised when a reviewer output cannot be sealed."""


@dataclass(frozen=True, slots=True)
class ReviewBundle:
    workspace: Path
    input_dir: Path
    output_dir: Path
    cache_dir: Path
    state_dir: Path
    tmp_dir: Path
    protocol_sha256: str
    corpus_manifest_sha256: str
    bundle_sha256: str
    document_keys: tuple[tuple[str, int, str], ...]
    relationship_sha256_by_ts_code: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ReviewerSpec:
    reviewer: str
    backend: str
    model: str
    executable: str
    timeout_seconds: float = 540.0


@dataclass(frozen=True, slots=True)
class ReviewerExecutionAuthorization:
    protocol_sha256: str
    corpus_manifest_sha256: str
    reviewer: str
    bundle_sha256: str
    prompt_sha256: str
    model: str
    backend: str
    command_sha256: str
    environment_keys: tuple[str, ...]

    @classmethod
    def from_preview(cls, preview: InvocationPreview) -> ReviewerExecutionAuthorization:
        """Bind authorization to one exact reviewer invocation preview."""
        return cls(
            preview.protocol_sha256,
            preview.corpus_manifest_sha256,
            preview.reviewer,
            preview.bundle_sha256,
            preview.prompt_sha256,
            preview.model,
            preview.backend,
            hashlib.sha256(canonical_json_bytes(preview.command)).hexdigest(),
            preview.environment_keys,
        )


@dataclass(frozen=True, slots=True)
class InvocationPreview:
    reviewer: str
    backend: str
    model: str
    command: tuple[str, ...]
    cwd: str
    prompt_sha256: str
    protocol_sha256: str
    corpus_manifest_sha256: str
    bundle_sha256: str
    environment_keys: tuple[str, ...]
    execute: bool = False


@dataclass(frozen=True, slots=True)
class ReviewAttempt:
    reviewer: str
    status: str
    preview: InvocationPreview
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    error_code: str | None
    labels: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ReviewSeal:
    reviewer: str
    backend: str
    model: str
    workspace: str
    cache_dir: str
    protocol_sha256: str
    corpus_manifest_sha256: str
    bundle_sha256: str
    prompt_sha256: str
    command_sha256: str
    document_keys: tuple[tuple[str, int, str], ...]
    relationship_sha256_by_ts_code: tuple[tuple[str, str], ...]
    output_path: Path
    output_sha256: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class ReviewIndex:
    protocol_sha256: str
    corpus_manifest_sha256: str
    seal_a: ReviewSeal
    seal_b: ReviewSeal
    index_sha256: str


@dataclass(frozen=True, slots=True)
class Disagreement:
    disagreement_id: str
    document_sha256: str
    ts_code: str
    fields: tuple[str, ...]
    reviewer_a: Mapping[str, Any]
    reviewer_b: Mapping[str, Any]


def labels_schema() -> dict[str, Any]:
    """Return the frozen reviewer output schema written into every bundle."""
    properties: dict[str, Any] = {field: {"type": ["string", "null"]} for field in LABEL_FIELDS}
    properties["decision"] = {"enum": ["accept", "reject"]}
    properties["reason_code"] = {"enum": sorted(ACCEPT_REASONS | REJECT_REASONS)}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "protocol_sha256", "corpus_manifest_sha256", "document_evaluations"],
        "properties": {
            "schema_version": {"const": LABEL_SCHEMA_VERSION},
            "protocol_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "corpus_manifest_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "document_evaluations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(LABEL_FIELDS),
                    "properties": properties,
                },
            },
        },
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_regular_bytes(path: Path, limit: int) -> tuple[bytes, int]:
    """Read one regular file once, without following symlinks and with a hard cap."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = -1
    try:
        fd = os.open(path, flags)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ReviewValidationError(f"not a regular file: {path}")
        if metadata.st_size > limit:
            raise ReviewValidationError(f"file exceeds frozen size limit: {path}")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ReviewValidationError(f"file exceeds frozen size limit: {path}")
        return raw, metadata.st_mode
    except OSError as exc:
        raise ReviewValidationError(f"cannot safely read frozen file: {path}") from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _bundle_hash(input_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(input_dir.rglob("*")):
        if path.is_symlink():
            raise ReviewValidationError(f"review input may not contain symlinks: {path}")
        relative = path.relative_to(input_dir).as_posix().encode("utf-8")
        if path.is_dir():
            kind = b"D"
        elif path.is_file():
            kind = b"F"
        else:
            raise ReviewValidationError(f"review input contains a special file: {path}")
        digest.update(kind)
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        if path.is_file():
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _safe_blob_source(blob_root: Path, relative_path: str) -> Path:
    if blob_root.is_symlink() or not blob_root.is_dir():
        raise ReviewValidationError(f"blob root must be a real directory: {blob_root}")
    relative = Path(relative_path)
    if relative.is_absolute() or relative in {Path(""), Path(".")} or ".." in relative.parts:
        raise ReviewValidationError(f"unsafe corpus blob path: {relative_path}")
    cursor = blob_root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ReviewValidationError(f"corpus blob path contains a symlink: {relative_path}")
    try:
        cursor.resolve(strict=True).relative_to(blob_root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ReviewValidationError(f"corpus blob path escapes its root: {relative_path}") from exc
    return cursor


def _link_or_copy_verified(source: Path, target: Path, expected_sha256: str, expected_size: int) -> None:
    if (
        source.is_symlink()
        or not source.is_file()
        or source.stat().st_size != expected_size
        or _sha256_file(source) != expected_sha256
    ):
        raise ReviewValidationError(f"corpus source is absent, linked, or hash-drifted: {source}")
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)
    if target.stat().st_size != expected_size or _sha256_file(target) != expected_sha256:
        target.unlink(missing_ok=True)
        raise ReviewValidationError(f"corpus mirror hash mismatch: {target}")


def _validate_corpus_manifest(corpus: CandidateCorpus, manifest_path: Path) -> bytes:
    manifest_bytes, manifest_mode = _read_regular_bytes(manifest_path, MANIFEST_LIMIT)
    try:
        value = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewValidationError("corpus manifest is not valid UTF-8 JSON") from exc
    if canonical_json_bytes(value) != manifest_bytes:
        raise ReviewValidationError("corpus manifest is not canonical JSON")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if not manifest_path.stem.endswith(manifest_sha256) or manifest_mode & 0o222:
        raise ReviewValidationError("corpus manifest is not a create-only frozen artifact")
    expected_keys = {"canonical_json", "parent_manifest", "parent_sha256", "payload", "stage"}
    if not isinstance(value, dict) or set(value) != expected_keys or value["stage"] != "corpus":
        raise ReviewValidationError("corpus manifest is not a frozen corpus-stage envelope")
    if value["canonical_json"] != "UTF-8;LF;recursive-key-sort;compact;ensure_ascii=false;allow_nan=false":
        raise ReviewValidationError("corpus manifest declares the wrong canonical JSON contract")
    parent_name = value["parent_manifest"]
    parent_sha256 = value["parent_sha256"]
    if (
        not isinstance(parent_name, str)
        or Path(parent_name).name != parent_name
        or not isinstance(parent_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", parent_sha256)
    ):
        raise ReviewValidationError("corpus manifest has an invalid parent reference")
    parent_path = manifest_path.parent / parent_name
    parent_raw, parent_mode = _read_regular_bytes(parent_path, MANIFEST_LIMIT)
    if parent_mode & 0o222 or hashlib.sha256(parent_raw).hexdigest() != parent_sha256:
        raise ReviewValidationError("corpus manifest parent is absent or hash-drifted")
    if canonical_json_bytes(value["payload"]) != canonical_json_bytes(corpus):
        raise ReviewValidationError("corpus manifest payload does not match the supplied CandidateCorpus")
    return manifest_bytes


def _verify_bundle(bundle: ReviewBundle) -> None:
    if bundle.input_dir.is_symlink() or not bundle.input_dir.is_dir():
        raise ReviewValidationError("review input directory is absent or linked")
    for path in (bundle.input_dir, *bundle.input_dir.rglob("*")):
        if path.is_symlink() or path.stat().st_mode & 0o222:
            raise ReviewValidationError(f"review input is linked or writable: {path}")
    if _bundle_hash(bundle.input_dir) != bundle.bundle_sha256:
        raise ReviewValidationError("review input bundle changed after creation")


def create_review_bundle(
    workspace: str | Path,
    corpus: CandidateCorpus,
    corpus_manifest_path: str | Path,
    blob_root: str | Path,
    instructions: str | bytes,
) -> ReviewBundle:
    """Atomically publish one immutable input and isolated writable namespaces."""
    root = Path(workspace).absolute()
    manifest_source = Path(corpus_manifest_path)
    manifest_bytes = _validate_corpus_manifest(corpus, manifest_source)
    corpus_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    instruction_bytes = instructions.encode("utf-8") if isinstance(instructions, str) else instructions
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise FrozenArtifactError(f"review workspace must be a real directory path: {root}")
    existed_empty = root.exists()
    original_mode = root.stat().st_mode & 0o777 if existed_empty else 0o755
    if existed_empty and any(root.iterdir()):
        raise FrozenArtifactError(f"review workspace is not empty: {root}")
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.parent.is_symlink() or root.parent.resolve(strict=True) != root.parent:
        raise FrozenArtifactError(f"review workspace parent may not traverse symlinks: {root.parent}")
    if existed_empty:
        root.rmdir()
    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    published = False
    document_keys: list[tuple[str, int, str]] = []
    try:
        input_dir = staging / "input"
        output_dir = staging / "output"
        cache_dir = staging / "cache"
        state_dir = staging / "state"
        tmp_dir = staging / "tmp"
        corpus_dir = input_dir / "corpus"
        for directory in (corpus_dir, output_dir, cache_dir, state_dir, tmp_dir):
            directory.mkdir(parents=True, exist_ok=True)
        (input_dir / "corpus-manifest.json").write_bytes(manifest_bytes)
        (input_dir / "instructions.md").write_bytes(instruction_bytes)
        (input_dir / "labels.schema.json").write_bytes(canonical_json_bytes(labels_schema()))
        for document in sorted(corpus.documents, key=lambda item: (item.ts_code, item.round_robin_ordinal)):
            source = _safe_blob_source(Path(blob_root), document.blob.relative_path)
            suffix = Path(document.blob.relative_path).suffix
            target = corpus_dir / (
                f"{document.ts_code}-{document.round_robin_ordinal:03d}-{document.blob.sha256}{suffix}"
            )
            _link_or_copy_verified(source, target, document.blob.sha256, document.blob.byte_count)
            document_keys.append((document.ts_code, document.round_robin_ordinal, document.blob.sha256))
        relationship_dir = corpus_dir / "relationships"
        relationship_dir.mkdir()
        for index, relationship in enumerate(corpus.relationship_reports, start=1):
            blob = relationship.blob
            source = _safe_blob_source(Path(blob_root), blob.relative_path)
            suffix = Path(blob.relative_path).suffix
            target = relationship_dir / f"relationship-{index:03d}-{blob.sha256}{suffix}"
            _link_or_copy_verified(source, target, blob.sha256, blob.byte_count)
        for path in input_dir.rglob("*"):
            path.chmod(0o444 if path.is_file() else 0o555)
        input_dir.chmod(0o555)
        bundle_sha256 = _bundle_hash(input_dir)
        os.replace(staging, root)
        published = True
    except BaseException:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)
            if existed_empty and not root.exists():
                root.mkdir(mode=original_mode)
        raise
    return ReviewBundle(
        root,
        root / "input",
        root / "output",
        root / "cache",
        root / "state",
        root / "tmp",
        corpus.protocol_sha256,
        corpus_manifest_sha256,
        bundle_sha256,
        tuple(document_keys),
        tuple((item.ts_code, item.blob.sha256) for item in corpus.relationship_reports),
    )


prepare_review_bundle = create_review_bundle


def _validate_spec(spec: ReviewerSpec) -> tuple[str, str]:
    reviewer = spec.reviewer.upper()
    backend = spec.backend.lower()
    if (reviewer, backend) not in {("A", "codex"), ("B", "agy")}:
        raise IsolationViolationError("Reviewer A must use Codex and Reviewer B must use AGY")
    if not spec.model or not spec.executable or not 0 < spec.timeout_seconds <= 540:
        raise ReviewValidationError("reviewer/model/executable and a timeout <= 540 seconds are required")
    return reviewer, backend


def _command(bundle: ReviewBundle, spec: ReviewerSpec, backend: str) -> tuple[str, ...]:
    schema_path = str(bundle.input_dir / "labels.schema.json")
    if backend == "codex":
        return (
            spec.executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--ask-for-approval",
            "never",
            "--output-schema",
            schema_path,
            "--model",
            spec.model,
            "--config",
            "project_doc_max_bytes=0",
            "-",
        )
    return (
        spec.executable,
        "--new-project",
        "--mode",
        "plan",
        "--sandbox",
        "--print-timeout",
        "9m",
        "--model",
        spec.model,
        "--log-file",
        str(bundle.state_dir / "agy-review.log"),
        "--print",
    )


_PRESERVED_ENV_KEYS = (
    "PATH",
    "LANG",
    "LANGUAGE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)
_BACKEND_ENV_KEYS = {
    "codex": ("CODEX_HOME", "OPENAI_API_KEY"),
    "agy": ("ANTHROPIC_API_KEY",),
}


def _validate_writable_namespaces(bundle: ReviewBundle, *, create_home: bool = True) -> Path:
    workspace = bundle.workspace
    if (
        not workspace.is_absolute()
        or workspace.is_symlink()
        or not workspace.is_dir()
        or workspace.resolve(strict=True) != workspace
    ):
        raise IsolationViolationError("reviewer workspace is absent, linked, or non-canonical")
    namespaces = (bundle.output_dir, bundle.cache_dir, bundle.state_dir, bundle.tmp_dir)
    expected = tuple(workspace / name for name in ("output", "cache", "state", "tmp"))
    if namespaces != expected or len(set(namespaces)) != len(namespaces):
        raise IsolationViolationError("reviewer writable namespaces must use distinct frozen workspace paths")
    for path in namespaces:
        if (
            not path.is_absolute()
            or path.parent != workspace
            or path.is_symlink()
            or not path.is_dir()
            or path.resolve(strict=True) != path
        ):
            raise IsolationViolationError(f"reviewer writable namespace escaped its workspace: {path}")
    isolated_home = bundle.state_dir / "home"
    if os.path.lexists(isolated_home):
        if isolated_home.is_symlink() or not isolated_home.is_dir():
            raise IsolationViolationError("reviewer HOME must be a real directory")
    else:
        if not create_home:
            raise IsolationViolationError("reviewer HOME disappeared after sealing")
        isolated_home.mkdir(mode=0o700)
    isolated_home.chmod(0o700)
    return isolated_home


def _isolated_env(bundle: ReviewBundle, backend: str) -> dict[str, str]:
    allowed = set(_PRESERVED_ENV_KEYS) | set(_BACKEND_ENV_KEYS[backend])
    allowed.update(key for key in os.environ if key.startswith("LC_"))
    env = {key: value for key, value in os.environ.items() if key in allowed}
    isolated_home = _validate_writable_namespaces(bundle)
    env["HOME"] = str(isolated_home)
    env["TMPDIR"] = str(bundle.tmp_dir)
    env["XDG_CACHE_HOME"] = str(bundle.cache_dir)
    env["XDG_STATE_HOME"] = str(bundle.state_dir)
    return env


def _validate_environment_keys(backend: str, environment_keys: Sequence[str]) -> tuple[str, ...]:
    keys = tuple(environment_keys)
    required = {"HOME", "TMPDIR", "XDG_CACHE_HOME", "XDG_STATE_HOME"}
    allowed = set(_PRESERVED_ENV_KEYS) | set(_BACKEND_ENV_KEYS[backend]) | required
    if (
        keys != tuple(sorted(set(keys)))
        or not required <= set(keys)
        or any(key not in allowed and not key.startswith("LC_") for key in keys)
    ):
        raise ReviewValidationError("review attempt environment keys violate the backend allowlist")
    return keys


def _parse_one_json(raw: bytes) -> Mapping[str, Any]:
    try:
        text = raw.decode("utf-8")
        decoder = json.JSONDecoder()
        value, end = decoder.raw_decode(text.lstrip())
        consumed = len(text) - len(text.lstrip()) + end
        if text[consumed:].strip() or not isinstance(value, dict):
            raise ValueError("review output is not exactly one JSON object")
        return value
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReviewValidationError("review output is not exactly one UTF-8 JSON object") from exc


def _validate_evaluation(
    evaluation: object,
    document_keys: Sequence[tuple[str, int, str]],
    relationship_sha256_by_ts_code: Mapping[str, str],
) -> tuple[str, int, str]:
    expected = list(document_keys)
    ordinal_by_key = {(ts_code, sha256): ordinal for ts_code, ordinal, sha256 in expected}
    if not isinstance(evaluation, dict) or set(evaluation) != set(LABEL_FIELDS):
        raise ReviewValidationError("every evaluation must contain exactly the frozen label fields")
    if any(value is not None and not isinstance(value, str) for value in evaluation.values()):
        raise ReviewValidationError("label fields must be strings or null")
    ts_code = evaluation["ts_code"]
    document_sha256 = evaluation["document_sha256"]
    if not isinstance(ts_code, str) or not isinstance(document_sha256, str):
        raise ReviewValidationError("document identity fields must be strings")
    if not re.fullmatch(r"[0-9a-f]{64}", document_sha256):
        raise ReviewValidationError("document SHA-256 is malformed")
    key = (ts_code, document_sha256)
    if key not in ordinal_by_key:
        raise ReviewValidationError("review contains an unknown document")
    decision = evaluation["decision"]
    reason = evaluation["reason_code"]
    if decision == "accept" and reason not in ACCEPT_REASONS:
        raise ReviewValidationError("accept label uses a non-accept reason")
    if decision == "reject" and reason not in REJECT_REASONS:
        raise ReviewValidationError("reject label uses a non-reject reason")
    if decision not in {"accept", "reject"}:
        raise ReviewValidationError("unknown decision")
    for field, limit in (
        ("subject_entity", 512),
        ("locator", 2048),
        ("quoted_text", 2048),
        ("effectiveness_basis", 2048),
        ("relationship_locator", 2048),
        ("effective_until", 512),
        ("rationale", 4096),
    ):
        field_value = evaluation[field]
        if field_value is not None and (not isinstance(field_value, str) or len(field_value) > limit):
            raise ReviewValidationError(f"{field} exceeds its schema bound")
    relationship_sha = evaluation["relationship_document_sha256"]
    if relationship_sha is not None and not re.fullmatch(r"[0-9a-f]{64}", relationship_sha):
        raise ReviewValidationError("relationship document SHA-256 is malformed")
    if decision == "accept":
        if not evaluation["subject_entity"] or not evaluation["effectiveness_basis"]:
            raise ReviewValidationError("accepted evidence requires subject and effectiveness basis")
        if reason == "core_active_patent" and (not relationship_sha or not evaluation["relationship_locator"]):
            raise ReviewValidationError("accepted patent evidence requires relationship support")
        if reason == "core_active_patent" and relationship_sha != relationship_sha256_by_ts_code.get(ts_code):
            raise ReviewValidationError("accepted patent relationship document does not belong to the company")
    return ts_code, ordinal_by_key[key], document_sha256


def _validate_review_value(
    protocol_sha256: str,
    corpus_manifest_sha256: str,
    document_keys: Sequence[tuple[str, int, str]],
    relationship_sha256_by_ts_code: Mapping[str, str],
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if set(value) != {"schema_version", "protocol_sha256", "corpus_manifest_sha256", "document_evaluations"}:
        raise ReviewValidationError("review output has missing or unknown top-level fields")
    if value["schema_version"] != LABEL_SCHEMA_VERSION:
        raise ReviewValidationError("review schema version mismatch")
    if value["protocol_sha256"] != protocol_sha256 or value["corpus_manifest_sha256"] != corpus_manifest_sha256:
        raise ReviewValidationError("review protocol/corpus hash mismatch")
    evaluations = value["document_evaluations"]
    if not isinstance(evaluations, list):
        raise ReviewValidationError("document_evaluations must be an array")
    expected = list(document_keys)
    observed = [_validate_evaluation(item, expected, relationship_sha256_by_ts_code) for item in evaluations]
    if observed != expected or len(set(observed)) != len(observed):
        raise ReviewValidationError("review must cover every document exactly once in frozen order")
    return dict(value)


def validate_review_output(bundle: ReviewBundle, value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exact document coverage, field bounds, and semantic label rules."""
    return _validate_review_value(
        bundle.protocol_sha256,
        bundle.corpus_manifest_sha256,
        bundle.document_keys,
        dict(bundle.relationship_sha256_by_ts_code),
        value,
    )


def _read_frozen_prompt(bundle: ReviewBundle) -> bytes:
    prompt, mode = _read_regular_bytes(bundle.input_dir / "instructions.md", MANIFEST_LIMIT)
    if mode & 0o222:
        raise ReviewValidationError("reviewer prompt is writable")
    return prompt


def _invocation_preview(
    bundle: ReviewBundle,
    spec: ReviewerSpec,
    backend: str,
    execute: bool,
    prompt: bytes,
    environment_keys: Sequence[str],
) -> InvocationPreview:
    command = _command(bundle, spec, backend)
    validated_environment_keys = _validate_environment_keys(backend, environment_keys)
    return InvocationPreview(
        spec.reviewer.upper(),
        backend,
        spec.model,
        command,
        str(bundle.workspace),
        hashlib.sha256(prompt).hexdigest(),
        bundle.protocol_sha256,
        bundle.corpus_manifest_sha256,
        bundle.bundle_sha256,
        validated_environment_keys,
        execute,
    )


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            process.kill()
        except OSError:
            pass
    if process.poll() is not None:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired as exc:
            raise ReviewValidationError("reviewer process could not be reaped") from exc


def _read_reviewer_output(process: subprocess.Popen[bytes], timeout_seconds: float) -> tuple[bytes, bytes, str | None]:
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("reviewer subprocess pipes were not created")

    streams = {process.stdout: (bytearray(), STDOUT_LIMIT), process.stderr: (bytearray(), STDERR_LIMIT)}
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout_seconds
    error_code: str | None = None
    try:
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                error_code = "timeout"
                _terminate_process_group(process)
                break
            for key, _ in selector.select(remaining):
                stream = cast(IO[bytes], key.fileobj)
                buffer, limit = streams[stream]
                try:
                    chunk = os.read(stream.fileno(), min(65536, limit - len(buffer) + 1))
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                remaining_capacity = limit - len(buffer)
                buffer.extend(chunk[:remaining_capacity])
                if len(chunk) > remaining_capacity:
                    error_code = "output_limit"
                    _terminate_process_group(process)
                    break
            if error_code is not None:
                break
        if error_code is None:
            remaining = deadline - time.monotonic()
            try:
                process.wait(timeout=max(0.0, remaining))
            except subprocess.TimeoutExpired:
                error_code = "timeout"
                _terminate_process_group(process)
    except OSError:
        error_code = "io_error"
        _terminate_process_group(process)
    finally:
        if process.poll() is None:
            _terminate_process_group(process)
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return bytes(streams[process.stdout][0]), bytes(streams[process.stderr][0]), error_code


def run_reviewer(
    bundle: ReviewBundle,
    spec: ReviewerSpec,
    execute: bool = False,
    authorization: ReviewerExecutionAuthorization | None = None,
) -> InvocationPreview | ReviewAttempt:
    """Preview by default; execute only with a narrowly matching authorization."""
    reviewer, backend = _validate_spec(spec)
    _verify_bundle(bundle)
    prompt = _read_frozen_prompt(bundle)
    env = _isolated_env(bundle, backend)
    preview = _invocation_preview(bundle, spec, backend, execute, prompt, tuple(sorted(env)))
    if not execute:
        return preview
    if authorization is None or (
        authorization.reviewer.upper(),
        authorization.protocol_sha256,
        authorization.corpus_manifest_sha256,
        authorization.bundle_sha256,
        authorization.prompt_sha256,
        authorization.model,
        authorization.backend,
        authorization.command_sha256,
        authorization.environment_keys,
    ) != (
        reviewer,
        bundle.protocol_sha256,
        bundle.corpus_manifest_sha256,
        preview.bundle_sha256,
        preview.prompt_sha256,
        preview.model,
        preview.backend,
        hashlib.sha256(canonical_json_bytes(preview.command)).hexdigest(),
        preview.environment_keys,
    ):
        raise AuditBlockedError("review execution authorization does not match reviewer/protocol/corpus")
    process: subprocess.Popen[bytes] | None = None
    try:
        with tempfile.TemporaryFile(dir=bundle.tmp_dir) as prompt_stream:
            prompt_stream.write(prompt)
            prompt_stream.seek(0)
            process = subprocess.Popen(
                preview.command,
                cwd=bundle.workspace,
                env=env,
                stdin=prompt_stream,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
            )
            stdout, stderr, read_error = _read_reviewer_output(process, spec.timeout_seconds)
    except (OSError, ValueError, subprocess.SubprocessError):
        if process is not None and process.poll() is None:
            try:
                _terminate_process_group(process)
            except ReviewValidationError:
                return ReviewAttempt(reviewer, "technical_error", preview, None, b"", b"", "process_cleanup_error")
        error_code = "spawn_error" if process is None else "io_error"
        return ReviewAttempt(reviewer, "technical_error", preview, None, b"", b"", error_code)
    except ReviewValidationError:
        if process is not None and process.poll() is None:
            try:
                _terminate_process_group(process)
            except ReviewValidationError:
                pass
        return ReviewAttempt(reviewer, "technical_error", preview, None, b"", b"", "process_cleanup_error")
    assert process is not None
    if read_error is not None:
        return ReviewAttempt(
            reviewer,
            "technical_error",
            preview,
            process.returncode,
            stdout,
            stderr,
            read_error,
        )
    if process.returncode != 0:
        return ReviewAttempt(reviewer, "technical_error", preview, process.returncode, stdout, stderr, "nonzero_exit")
    try:
        labels = validate_review_output(bundle, _parse_one_json(stdout))
    except ReviewValidationError:
        return ReviewAttempt(
            reviewer, "technical_error", preview, process.returncode, stdout, stderr, "invalid_json_or_schema"
        )
    return ReviewAttempt(reviewer, "success", preview, process.returncode, stdout, stderr, None, labels)


def seal_review(bundle: ReviewBundle, spec: ReviewerSpec, attempt: ReviewAttempt, seal_dir: str | Path) -> ReviewSeal:
    """Atomically seal only a schema-valid successful reviewer result."""
    reviewer, backend = _validate_spec(spec)
    _verify_bundle(bundle)
    if attempt.reviewer != reviewer or attempt.status != "success" or attempt.labels is None:
        raise ReviewValidationError("only a successful matching review attempt can be sealed")
    if not attempt.preview.execute or attempt.exit_code != 0 or attempt.error_code is not None:
        raise ReviewValidationError("only an executed, zero-exit review attempt can be sealed")
    expected_preview = _invocation_preview(
        bundle,
        spec,
        backend,
        attempt.preview.execute,
        _read_frozen_prompt(bundle),
        attempt.preview.environment_keys,
    )
    if attempt.preview != expected_preview:
        raise ReviewValidationError("review attempt provenance does not match the supplied bundle and spec")
    validated = validate_review_output(bundle, attempt.labels)
    if validated != validate_review_output(bundle, _parse_one_json(attempt.stdout)):
        raise ReviewValidationError("review attempt labels do not match its captured stdout")
    raw = canonical_json_bytes(validated)
    target_dir = Path(seal_dir)
    if target_dir.is_symlink() or (target_dir.exists() and not target_dir.is_dir()):
        raise FrozenArtifactError(f"review seal directory must be a real directory: {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)
    resolved_dir = target_dir.resolve(strict=True)
    if resolved_dir != target_dir.absolute():
        raise FrozenArtifactError(f"review seal directory may not traverse symlinks: {target_dir}")
    target = resolved_dir / f"reviewer-{reviewer.lower()}-labels.json"
    fd, temp_name = tempfile.mkstemp(prefix=f".reviewer-{reviewer.lower()}.", dir=resolved_dir)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temp_name).chmod(0o444)
        try:
            os.link(temp_name, target, follow_symlinks=False)
        except FileExistsError:
            raise FrozenArtifactError(f"review seal already exists: {target}")
        directory_fd = os.open(
            resolved_dir,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return ReviewSeal(
        reviewer,
        backend,
        spec.model,
        str(bundle.workspace.resolve()),
        str(bundle.cache_dir.resolve()),
        bundle.protocol_sha256,
        bundle.corpus_manifest_sha256,
        bundle.bundle_sha256,
        attempt.preview.prompt_sha256,
        hashlib.sha256(canonical_json_bytes(attempt.preview.command)).hexdigest(),
        bundle.document_keys,
        bundle.relationship_sha256_by_ts_code,
        target,
        hashlib.sha256(raw).hexdigest(),
        len(raw),
    )


@dataclass(frozen=True, slots=True)
class _SealSnapshot:
    seal: ReviewSeal
    value: Mapping[str, Any]
    evaluations: tuple[dict[str, Any], ...]


def _validate_seal(seal: ReviewSeal) -> _SealSnapshot:
    if not seal.output_path.is_absolute():
        raise ReviewValidationError("review seal path must be absolute")
    raw, mode = _read_regular_bytes(seal.output_path, STDOUT_LIMIT)
    if mode & 0o222 or len(raw) != seal.byte_count or hashlib.sha256(raw).hexdigest() != seal.output_sha256:
        raise ReviewValidationError("review seal is absent, writable, oversized, or modified")
    value = _parse_one_json(raw)
    if canonical_json_bytes(value) != raw:
        raise ReviewValidationError("review seal is not canonical JSON")
    validated = _validate_review_value(
        seal.protocol_sha256,
        seal.corpus_manifest_sha256,
        seal.document_keys,
        dict(seal.relationship_sha256_by_ts_code),
        value,
    )
    evaluations = validated["document_evaluations"]
    assert isinstance(evaluations, list)
    return _SealSnapshot(seal, validated, tuple(evaluations))


def _verify_reviewer_workspace(seal: ReviewSeal) -> None:
    workspace = Path(seal.workspace)
    if (
        not workspace.is_absolute()
        or workspace.is_symlink()
        or not workspace.is_dir()
        or workspace.resolve(strict=True) != workspace
    ):
        raise ReviewValidationError("reviewer workspace is absent, linked, or non-canonical")
    expected_names = {"input", "output", "cache", "state", "tmp"}
    if {path.name for path in workspace.iterdir()} != expected_names:
        raise ReviewValidationError("reviewer workspace layout changed after sealing")
    directories = {name: workspace / name for name in expected_names}
    if any(path.is_symlink() or not path.is_dir() for path in directories.values()):
        raise ReviewValidationError("reviewer workspace contains an absent or linked namespace")
    if Path(seal.cache_dir) != directories["cache"]:
        raise ReviewValidationError("reviewer cache provenance does not match its workspace")
    bundle = ReviewBundle(
        workspace,
        directories["input"],
        directories["output"],
        directories["cache"],
        directories["state"],
        directories["tmp"],
        seal.protocol_sha256,
        seal.corpus_manifest_sha256,
        seal.bundle_sha256,
        seal.document_keys,
        seal.relationship_sha256_by_ts_code,
    )
    _validate_writable_namespaces(bundle, create_home=False)
    _verify_bundle(bundle)
    manifest_raw, _ = _read_regular_bytes(bundle.input_dir / "corpus-manifest.json", MANIFEST_LIMIT)
    prompt_raw, _ = _read_regular_bytes(bundle.input_dir / "instructions.md", MANIFEST_LIMIT)
    if hashlib.sha256(manifest_raw).hexdigest() != seal.corpus_manifest_sha256:
        raise ReviewValidationError("reviewer corpus manifest changed after sealing")
    if hashlib.sha256(prompt_raw).hexdigest() != seal.prompt_sha256:
        raise ReviewValidationError("reviewer prompt changed after sealing")


def _build_review_index_from_snapshots(a_snapshot: _SealSnapshot, b_snapshot: _SealSnapshot) -> ReviewIndex:
    seals = {a_snapshot.seal.reviewer.upper(): a_snapshot, b_snapshot.seal.reviewer.upper(): b_snapshot}
    if set(seals) != {"A", "B"}:
        raise IsolationViolationError("review index requires exactly Reviewer A and Reviewer B")
    a_snapshot, b_snapshot = seals["A"], seals["B"]
    a, b = a_snapshot.seal, b_snapshot.seal
    if a.backend != "codex" or b.backend != "agy":
        raise IsolationViolationError("review seals use the wrong backend binding")
    if (
        a.protocol_sha256 != b.protocol_sha256
        or a.corpus_manifest_sha256 != b.corpus_manifest_sha256
        or a.bundle_sha256 != b.bundle_sha256
        or a.prompt_sha256 != b.prompt_sha256
        or a.document_keys != b.document_keys
        or a.relationship_sha256_by_ts_code != b.relationship_sha256_by_ts_code
    ):
        raise IsolationViolationError("reviewers did not receive the same frozen input")
    if a.workspace == b.workspace or a.cache_dir == b.cache_dir:
        raise IsolationViolationError("reviewer workspaces and caches must be distinct")
    payload = {
        "protocol_sha256": a.protocol_sha256,
        "corpus_manifest_sha256": a.corpus_manifest_sha256,
        "reviewer_a": {
            "backend": a.backend,
            "model": a.model,
            "command_sha256": a.command_sha256,
            "output_sha256": a.output_sha256,
            "prompt_sha256": a.prompt_sha256,
        },
        "reviewer_b": {
            "backend": b.backend,
            "model": b.model,
            "command_sha256": b.command_sha256,
            "output_sha256": b.output_sha256,
            "prompt_sha256": b.prompt_sha256,
        },
    }
    return ReviewIndex(
        a.protocol_sha256,
        a.corpus_manifest_sha256,
        a,
        b,
        hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
    )


def build_review_index(seal_a: ReviewSeal, seal_b: ReviewSeal) -> ReviewIndex:
    """Link two independent, existing seals for the same corpus."""
    return _build_review_index_from_snapshots(_validate_seal(seal_a), _validate_seal(seal_b))


def _load_index_snapshots(index: ReviewIndex, *, verify_workspaces: bool) -> tuple[_SealSnapshot, _SealSnapshot]:
    a_snapshot = _validate_seal(index.seal_a)
    b_snapshot = _validate_seal(index.seal_b)
    rebuilt = _build_review_index_from_snapshots(a_snapshot, b_snapshot)
    if rebuilt != index:
        raise ReviewValidationError("review index metadata or hash is not canonical")
    if verify_workspaces:
        _verify_reviewer_workspace(rebuilt.seal_a)
        _verify_reviewer_workspace(rebuilt.seal_b)
    return a_snapshot, b_snapshot


def _compare_snapshots(
    index: ReviewIndex, a_snapshot: _SealSnapshot, b_snapshot: _SealSnapshot
) -> tuple[Disagreement, ...]:
    evaluations_a = a_snapshot.evaluations
    evaluations_b = b_snapshot.evaluations
    by_a = {(item["ts_code"], item["document_sha256"]): item for item in evaluations_a}
    by_b = {(item["ts_code"], item["document_sha256"]): item for item in evaluations_b}
    if set(by_a) != set(by_b):
        raise ReviewValidationError("sealed reviews cover different document sets")
    disagreements: list[Disagreement] = []
    for key in sorted(by_a):
        a, b = by_a[key], by_b[key]
        changed = tuple(field for field in LABEL_FIELDS if a.get(field) != b.get(field))
        if changed:
            identity = {
                "review_index_sha256": index.index_sha256,
                "ts_code": key[0],
                "document_sha256": key[1],
                "fields": changed,
                "reviewer_a": a,
                "reviewer_b": b,
            }
            disagreement_id = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
            disagreements.append(Disagreement(disagreement_id, key[1], key[0], changed, a, b))
    return tuple(disagreements)


def compare_reviews(index: ReviewIndex) -> tuple[Disagreement, ...]:
    """Compare every label field and assign deterministic disagreement IDs."""
    snapshots = _load_index_snapshots(index, verify_workspaces=False)
    return _compare_snapshots(index, *snapshots)


def _validate_adjudication_value(
    index: ReviewIndex,
    disagreements: Sequence[Disagreement],
    adjudication: Mapping[str, Any],
    corpus: CandidateCorpus,
) -> dict[str, Any]:
    """Require a full user disposition for every deterministic disagreement."""
    required_top = {"adjudication_version", "parent_review_index_sha256", "decisions"}
    if not required_top <= set(adjudication) or not set(adjudication) <= required_top | {"scope_dispositions"}:
        raise UnadjudicatedDisagreementError("adjudication has missing or unknown top-level fields")
    version = adjudication.get("adjudication_version")
    if (
        not isinstance(version, str)
        or not version.strip()
        or len(version) > 128
        or adjudication.get("parent_review_index_sha256") != index.index_sha256
    ):
        raise UnadjudicatedDisagreementError("adjudication header does not reference this review index")
    if "scope_dispositions" in adjudication and not isinstance(adjudication["scope_dispositions"], dict):
        raise UnadjudicatedDisagreementError("scope_dispositions must be an object")
    decisions = adjudication.get("decisions")
    if not isinstance(decisions, list):
        raise UnadjudicatedDisagreementError("adjudication decisions must be an array")
    expected = {item.disagreement_id for item in disagreements}
    document_keys = tuple(
        (item.ts_code, item.round_robin_ordinal, item.document_sha256)
        for item in sorted(corpus.documents, key=lambda item: (item.ts_code, item.round_robin_ordinal))
    )
    relationships = {item.ts_code: item.blob.sha256 for item in corpus.relationship_reports}
    disagreement_by_id = {item.disagreement_id: item for item in disagreements}
    observed: set[str] = set()
    for item in decisions:
        if not isinstance(item, dict) or not isinstance(item.get("disagreement_id"), str):
            raise UnadjudicatedDisagreementError("invalid adjudication decision")
        disagreement_id = item["disagreement_id"]
        if disagreement_id not in expected or disagreement_id in observed:
            raise UnadjudicatedDisagreementError("adjudication references an unknown or duplicate disagreement")
        observed.add(disagreement_id)
        disposition = item.get("disposition")
        rationale = item.get("rationale")
        if (
            disposition not in {"accept", "reject", "rerun_new_version"}
            or not isinstance(rationale, str)
            or not rationale.strip()
            or len(rationale) > 4096
        ):
            raise UnadjudicatedDisagreementError("adjudication requires a disposition and user rationale")
        expected_fields = {"disagreement_id", "disposition", "rationale"}
        if disposition in {"accept", "reject"}:
            expected_fields.add("final_label")
        if set(item) != expected_fields:
            raise UnadjudicatedDisagreementError("adjudication decision fields do not match its disposition")
        if disposition in {"accept", "reject"}:
            final_label = item.get("final_label")
            if not isinstance(final_label, dict) or set(final_label) != set(LABEL_FIELDS):
                raise UnadjudicatedDisagreementError("accept/reject adjudication requires a complete final label")
            if final_label.get("decision") != disposition:
                raise UnadjudicatedDisagreementError("final label decision does not match disposition")
            disagreement = disagreement_by_id[disagreement_id]
            try:
                identity = _validate_evaluation(final_label, document_keys, relationships)
            except ReviewValidationError as exc:
                raise UnadjudicatedDisagreementError("adjudicated final label fails semantic validation") from exc
            if (identity[0], identity[2]) != (disagreement.ts_code, disagreement.document_sha256):
                raise UnadjudicatedDisagreementError("final label belongs to a different disagreement document")
    if observed != expected:
        raise UnadjudicatedDisagreementError("not every disagreement has been adjudicated")
    return dict(adjudication)


def validate_adjudication(
    index: ReviewIndex,
    disagreements: Sequence[Disagreement],
    adjudication: Mapping[str, Any],
    corpus: CandidateCorpus,
) -> dict[str, Any]:
    """Validate adjudication against one single-read snapshot of both seals."""
    snapshots = _load_index_snapshots(index, verify_workspaces=False)
    if tuple(disagreements) != _compare_snapshots(index, *snapshots):
        raise UnadjudicatedDisagreementError("disagreement set does not match the frozen reviewer seals")
    return _validate_adjudication_value(index, disagreements, adjudication, corpus)


def _build_validated_lineage_transaction(
    corpus: CandidateCorpus,
    corpus_manifest_path: str | Path,
    index: ReviewIndex,
    adjudication: Mapping[str, Any],
) -> ValidatedAuditLineage:
    manifest_bytes = _validate_corpus_manifest(corpus, Path(corpus_manifest_path))
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if manifest_sha256 != index.corpus_manifest_sha256 or corpus.protocol_sha256 != index.protocol_sha256:
        raise ReviewValidationError("corpus manifest/protocol does not match the review index")
    a_snapshot, b_snapshot = _load_index_snapshots(index, verify_workspaces=True)
    expected_document_keys = tuple(
        (item.ts_code, item.round_robin_ordinal, item.document_sha256)
        for item in sorted(corpus.documents, key=lambda item: (item.ts_code, item.round_robin_ordinal))
    )
    expected_relationships = tuple((item.ts_code, item.blob.sha256) for item in corpus.relationship_reports)
    if (
        index.seal_a.document_keys != expected_document_keys
        or index.seal_a.relationship_sha256_by_ts_code != expected_relationships
    ):
        raise ReviewValidationError("review index document metadata does not match the CandidateCorpus")
    disagreements = _compare_snapshots(index, a_snapshot, b_snapshot)
    validated_adjudication = _validate_adjudication_value(index, disagreements, adjudication, corpus)
    decisions = {item["disagreement_id"]: item for item in validated_adjudication["decisions"]}
    disagreement_by_document = {(item.ts_code, item.document_sha256): item for item in disagreements}
    evaluations_a = a_snapshot.evaluations
    evaluations_b = b_snapshot.evaluations
    by_b = {(item["ts_code"], item["document_sha256"]): item for item in evaluations_b}
    final_evaluations: list[Mapping[str, Any]] = []
    for evaluation_a in evaluations_a:
        key = (evaluation_a["ts_code"], evaluation_a["document_sha256"])
        disagreement = disagreement_by_document.get(key)
        if disagreement is None:
            if by_b.get(key) != evaluation_a:
                raise ReviewValidationError("review comparison omitted a changed document")
            final_evaluations.append(evaluation_a)
            continue
        decision = decisions[disagreement.disagreement_id]
        if decision["disposition"] == "rerun_new_version":
            raise UnadjudicatedDisagreementError("rerun_new_version cannot produce final audit lineage")
        final_evaluations.append(decision["final_label"])
    expected_documents = {(item.ts_code, item.document_sha256) for item in corpus.documents}
    observed_documents = {(item["ts_code"], item["document_sha256"]) for item in final_evaluations}
    if observed_documents != expected_documents or len(final_evaluations) != len(expected_documents):
        raise ReviewValidationError("final review labels do not cover the CandidateCorpus exactly once")
    sample_codes = {item.ts_code for item in corpus.sample.entries}
    final_labels = {
        ts_code: any(item["ts_code"] == ts_code and item["decision"] == "accept" for item in final_evaluations)
        for ts_code in sorted(sample_codes)
    }
    return _create_validated_audit_lineage(
        corpus,
        final_labels,
        validated_adjudication,
        corpus_manifest_sha256=manifest_sha256,
        review_index_sha256=index.index_sha256,
        reviewer_seal_sha256s=(index.seal_a.output_sha256, index.seal_b.output_sha256),
    )


def build_coverage_report(
    corpus: CandidateCorpus,
    corpus_manifest_path: str | Path,
    index: ReviewIndex,
    adjudication: Mapping[str, Any],
) -> CoverageReport:
    """Atomically revalidate frozen review inputs and build the coverage report."""
    lineage = _build_validated_lineage_transaction(corpus, corpus_manifest_path, index, adjudication)
    return _build_coverage_report_from_lineage(lineage)


__all__ = [
    "ACCEPT_REASONS",
    "Disagreement",
    "InvocationPreview",
    "LABEL_FIELDS",
    "LABEL_SCHEMA_VERSION",
    "REJECT_REASONS",
    "ReviewAttempt",
    "ReviewBundle",
    "ReviewIndex",
    "ReviewSeal",
    "ReviewValidationError",
    "ReviewerExecutionAuthorization",
    "ReviewerSpec",
    "build_review_index",
    "build_coverage_report",
    "compare_reviews",
    "create_review_bundle",
    "labels_schema",
    "prepare_review_bundle",
    "run_reviewer",
    "seal_review",
    "validate_adjudication",
    "validate_review_output",
]
