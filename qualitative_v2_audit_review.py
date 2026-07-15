"""Isolated, opt-in reviewer execution and deterministic adjudication helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from qualitative_v2_audit import (
    AuditBlockedError,
    CandidateCorpus,
    FrozenArtifactError,
    IsolationViolationError,
    UnadjudicatedDisagreementError,
    canonical_json_bytes,
)


LABEL_SCHEMA_VERSION = "m4-review-label-v1"
STDOUT_LIMIT = 4 * 1024 * 1024
STDERR_LIMIT = 1 * 1024 * 1024
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
    relationship_sha256s: tuple[str, ...]


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


@dataclass(frozen=True, slots=True)
class InvocationPreview:
    reviewer: str
    backend: str
    model: str
    command: tuple[str, ...]
    cwd: str
    prompt_sha256: str
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
    corpus_manifest_sha256: str
    bundle_sha256: str
    output_path: Path
    output_sha256: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class ReviewIndex:
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


def _bundle_hash(input_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(input_dir.rglob("*")):
        if path.is_file():
            relative = path.relative_to(input_dir).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _link_or_copy_verified(source: Path, target: Path, expected_sha256: str) -> None:
    if source.is_symlink() or not source.is_file() or _sha256_file(source) != expected_sha256:
        raise ReviewValidationError(f"corpus source is absent, linked, or hash-drifted: {source}")
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)
    if _sha256_file(target) != expected_sha256:
        target.unlink(missing_ok=True)
        raise ReviewValidationError(f"corpus mirror hash mismatch: {target}")


def create_review_bundle(
    workspace: str | Path,
    corpus: CandidateCorpus,
    corpus_manifest_path: str | Path,
    blob_root: str | Path,
    instructions: str | bytes,
) -> ReviewBundle:
    """Create one immutable reviewer input and isolated writable namespaces."""
    root = Path(workspace)
    if root.exists() and any(root.iterdir()):
        raise FrozenArtifactError(f"review workspace is not empty: {root}")
    input_dir = root / "input"
    output_dir = root / "output"
    cache_dir = root / "cache"
    state_dir = root / "state"
    tmp_dir = root / "tmp"
    corpus_dir = input_dir / "corpus"
    for directory in (corpus_dir, output_dir, cache_dir, state_dir, tmp_dir):
        directory.mkdir(parents=True, exist_ok=True)
    manifest_source = Path(corpus_manifest_path)
    manifest_bytes = manifest_source.read_bytes()
    corpus_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    (input_dir / "corpus-manifest.json").write_bytes(manifest_bytes)
    instruction_bytes = instructions.encode("utf-8") if isinstance(instructions, str) else instructions
    (input_dir / "instructions.md").write_bytes(instruction_bytes)
    (input_dir / "labels.schema.json").write_bytes(canonical_json_bytes(labels_schema()))
    document_keys: list[tuple[str, int, str]] = []
    for document in sorted(corpus.documents, key=lambda item: (item.ts_code, item.round_robin_ordinal)):
        source = Path(blob_root) / document.blob.relative_path
        suffix = Path(document.blob.relative_path).suffix
        target = corpus_dir / f"{document.ts_code}-{document.round_robin_ordinal:03d}-{document.blob.sha256}{suffix}"
        _link_or_copy_verified(source, target, document.blob.sha256)
        document_keys.append((document.ts_code, document.round_robin_ordinal, document.blob.sha256))
    relationship_dir = corpus_dir / "relationships"
    relationship_dir.mkdir()
    for index, blob in enumerate(corpus.relationship_reports, start=1):
        source = Path(blob_root) / blob.relative_path
        suffix = Path(blob.relative_path).suffix
        target = relationship_dir / f"relationship-{index:03d}-{blob.sha256}{suffix}"
        _link_or_copy_verified(source, target, blob.sha256)
    for path in input_dir.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
        elif path.is_dir():
            path.chmod(0o555)
    bundle_sha256 = _bundle_hash(input_dir)
    return ReviewBundle(
        root,
        input_dir,
        output_dir,
        cache_dir,
        state_dir,
        tmp_dir,
        corpus.protocol_sha256,
        corpus_manifest_sha256,
        bundle_sha256,
        tuple(document_keys),
        tuple(sorted(blob.sha256 for blob in corpus.relationship_reports)),
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


_REMOVED_ENV_MARKERS = (
    "GEMINI",
    "GOOGLE_API",
    "TUSHARE",
    "TELEGRAM",
    "SHEETS",
    "GSPREAD",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)


def _isolated_env(bundle: ReviewBundle) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in _REMOVED_ENV_MARKERS)
    }
    env["TMPDIR"] = str(bundle.tmp_dir)
    env["XDG_CACHE_HOME"] = str(bundle.cache_dir)
    env["XDG_STATE_HOME"] = str(bundle.state_dir)
    return env


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


def validate_review_output(bundle: ReviewBundle, value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exact document coverage, field bounds, and semantic label rules."""
    if set(value) != {"schema_version", "protocol_sha256", "corpus_manifest_sha256", "document_evaluations"}:
        raise ReviewValidationError("review output has missing or unknown top-level fields")
    if value["schema_version"] != LABEL_SCHEMA_VERSION:
        raise ReviewValidationError("review schema version mismatch")
    if (
        value["protocol_sha256"] != bundle.protocol_sha256
        or value["corpus_manifest_sha256"] != bundle.corpus_manifest_sha256
    ):
        raise ReviewValidationError("review protocol/corpus hash mismatch")
    evaluations = value["document_evaluations"]
    if not isinstance(evaluations, list):
        raise ReviewValidationError("document_evaluations must be an array")
    expected = [(ts_code, ordinal, sha256) for ts_code, ordinal, sha256 in bundle.document_keys]
    observed: list[tuple[str, int, str]] = []
    ordinal_by_key = {(ts_code, sha256): ordinal for ts_code, ordinal, sha256 in expected}
    for evaluation in evaluations:
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
        observed.append((ts_code, ordinal_by_key[key], document_sha256))
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
            if reason == "core_active_patent" and (
                not evaluation["relationship_document_sha256"] or not evaluation["relationship_locator"]
            ):
                raise ReviewValidationError("accepted patent evidence requires relationship support")
            if (
                reason == "core_active_patent"
                and evaluation["relationship_document_sha256"] not in bundle.relationship_sha256s
            ):
                raise ReviewValidationError("accepted patent relationship document is outside the frozen corpus")
    if observed != expected or len(set(observed)) != len(observed):
        raise ReviewValidationError("review must cover every document exactly once in frozen order")
    return dict(value)


def run_reviewer(
    bundle: ReviewBundle,
    spec: ReviewerSpec,
    execute: bool = False,
    authorization: ReviewerExecutionAuthorization | None = None,
) -> InvocationPreview | ReviewAttempt:
    """Preview by default; execute only with a narrowly matching authorization."""
    reviewer, backend = _validate_spec(spec)
    prompt = (bundle.input_dir / "instructions.md").read_bytes()
    env = _isolated_env(bundle)
    command = _command(bundle, spec, backend)
    preview = InvocationPreview(
        reviewer,
        backend,
        spec.model,
        command,
        str(bundle.workspace),
        hashlib.sha256(prompt).hexdigest(),
        tuple(sorted(env)),
        execute,
    )
    if not execute:
        return preview
    if authorization is None or (
        authorization.reviewer.upper(),
        authorization.protocol_sha256,
        authorization.corpus_manifest_sha256,
    ) != (reviewer, bundle.protocol_sha256, bundle.corpus_manifest_sha256):
        raise AuditBlockedError("review execution authorization does not match reviewer/protocol/corpus")
    process = subprocess.Popen(
        command,
        cwd=bundle.workspace,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=prompt, timeout=spec.timeout_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        return ReviewAttempt(
            reviewer,
            "technical_error",
            preview,
            process.returncode,
            stdout[:STDOUT_LIMIT],
            stderr[:STDERR_LIMIT],
            "timeout",
        )
    if len(stdout) > STDOUT_LIMIT or len(stderr) > STDERR_LIMIT:
        return ReviewAttempt(
            reviewer,
            "technical_error",
            preview,
            process.returncode,
            stdout[:STDOUT_LIMIT],
            stderr[:STDERR_LIMIT],
            "output_limit",
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
    if attempt.reviewer != reviewer or attempt.status != "success" or attempt.labels is None:
        raise ReviewValidationError("only a successful matching review attempt can be sealed")
    validated = validate_review_output(bundle, attempt.labels)
    raw = canonical_json_bytes(validated)
    target_dir = Path(seal_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"reviewer-{reviewer.lower()}-labels.json"
    if target.exists():
        raise FrozenArtifactError(f"review seal already exists: {target}")
    fd, temp_name = tempfile.mkstemp(prefix=f".reviewer-{reviewer.lower()}.", dir=target_dir)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            raise FrozenArtifactError(f"review seal already exists: {target}")
        os.replace(temp_name, target)
        target.chmod(0o444)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return ReviewSeal(
        reviewer,
        backend,
        spec.model,
        str(bundle.workspace.resolve()),
        str(bundle.cache_dir.resolve()),
        bundle.corpus_manifest_sha256,
        bundle.bundle_sha256,
        target,
        hashlib.sha256(raw).hexdigest(),
        len(raw),
    )


def build_review_index(seal_a: ReviewSeal, seal_b: ReviewSeal) -> ReviewIndex:
    """Link two independent, existing seals for the same corpus."""
    seals = {seal_a.reviewer.upper(): seal_a, seal_b.reviewer.upper(): seal_b}
    if set(seals) != {"A", "B"}:
        raise IsolationViolationError("review index requires exactly Reviewer A and Reviewer B")
    a, b = seals["A"], seals["B"]
    if a.backend != "codex" or b.backend != "agy":
        raise IsolationViolationError("review seals use the wrong backend binding")
    for seal in (a, b):
        if not seal.output_path.is_file() or _sha256_file(seal.output_path) != seal.output_sha256:
            raise ReviewValidationError("review seal is absent or modified")
    if a.corpus_manifest_sha256 != b.corpus_manifest_sha256 or a.bundle_sha256 != b.bundle_sha256:
        raise IsolationViolationError("reviewers did not receive the same frozen input")
    if a.workspace == b.workspace or a.cache_dir == b.cache_dir:
        raise IsolationViolationError("reviewer workspaces and caches must be distinct")
    payload = {
        "corpus_manifest_sha256": a.corpus_manifest_sha256,
        "reviewer_a_sha256": a.output_sha256,
        "reviewer_b_sha256": b.output_sha256,
    }
    return ReviewIndex(a.corpus_manifest_sha256, a, b, hashlib.sha256(canonical_json_bytes(payload)).hexdigest())


def _load_seal(seal: ReviewSeal) -> list[dict[str, Any]]:
    if _sha256_file(seal.output_path) != seal.output_sha256:
        raise ReviewValidationError("review seal changed after sealing")
    value = json.loads(seal.output_path.read_text(encoding="utf-8"))
    return value["document_evaluations"]


def compare_reviews(index: ReviewIndex) -> tuple[Disagreement, ...]:
    """Compare every label field and assign deterministic disagreement IDs."""
    evaluations_a = _load_seal(index.seal_a)
    evaluations_b = _load_seal(index.seal_b)
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


def validate_adjudication(
    index: ReviewIndex, disagreements: Sequence[Disagreement], adjudication: Mapping[str, Any]
) -> dict[str, Any]:
    """Require a full user disposition for every deterministic disagreement."""
    if (
        adjudication.get("adjudication_version") is None
        or adjudication.get("parent_review_index_sha256") != index.index_sha256
    ):
        raise UnadjudicatedDisagreementError("adjudication header does not reference this review index")
    decisions = adjudication.get("decisions")
    if not isinstance(decisions, list):
        raise UnadjudicatedDisagreementError("adjudication decisions must be an array")
    expected = {item.disagreement_id for item in disagreements}
    observed: set[str] = set()
    for item in decisions:
        if not isinstance(item, dict) or not isinstance(item.get("disagreement_id"), str):
            raise UnadjudicatedDisagreementError("invalid adjudication decision")
        disagreement_id = item["disagreement_id"]
        if disagreement_id not in expected or disagreement_id in observed:
            raise UnadjudicatedDisagreementError("adjudication references an unknown or duplicate disagreement")
        observed.add(disagreement_id)
        disposition = item.get("disposition")
        if disposition not in {"accept", "reject", "rerun_new_version"} or not item.get("rationale"):
            raise UnadjudicatedDisagreementError("adjudication requires a disposition and user rationale")
        if disposition in {"accept", "reject"}:
            final_label = item.get("final_label")
            if not isinstance(final_label, dict) or set(final_label) != set(LABEL_FIELDS):
                raise UnadjudicatedDisagreementError("accept/reject adjudication requires a complete final label")
            if final_label.get("decision") != disposition:
                raise UnadjudicatedDisagreementError("final label decision does not match disposition")
    if observed != expected:
        raise UnadjudicatedDisagreementError("not every disagreement has been adjudicated")
    # Detect seal modification once more at the adjudication boundary.
    _load_seal(index.seal_a)
    _load_seal(index.seal_b)
    return dict(adjudication)


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
    "compare_reviews",
    "create_review_bundle",
    "labels_schema",
    "prepare_review_bundle",
    "run_reviewer",
    "seal_review",
    "validate_adjudication",
    "validate_review_output",
]
