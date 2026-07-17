#!/usr/bin/env python3
"""Verify the acyclic M4 v1.3.2 candidate/review/attestation chain offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_PATH = ROOT / "reviews/milestone-004-preregistration-v1.3.2/candidate-manifest.json"
REVIEW_PATH = ROOT / "reviews/milestone-004-preregistration-v1.3.2/protocol-review.md"
ATTESTATION_PATH = ROOT / "reviews/milestone-004-preregistration-v1.3.2/freeze-attestation.json"
PROTOCOL_ID = "qualitative-v2-m4-prereg-v1.3.2"
HASH_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
CANDIDATE_FIELDS = {
    "artifacts",
    "author_role",
    "dependency_dag",
    "historical_baseline_commit",
    "protocol_id",
    "repair_plan_commit",
    "schema_version",
}
ARTIFACT_FIELDS = {"byte_count", "path", "role", "sha256"}
ATTESTATION_FIELDS = {
    "attested_at",
    "candidate_manifest",
    "claims",
    "freeze_outcome",
    "protocol_id",
    "review",
    "reviewed_commit",
    "schema_version",
}


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def safe_path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("path_invalid")
    relative = Path(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("path_invalid")
    resolved = ROOT / relative
    if resolved.is_symlink() or not resolved.is_file() or not resolved.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("path_unsafe")
    return resolved


def verify_hash_inventory(path: Path) -> None:
    seen: set[str] = set()
    for line in path.read_text(encoding="ascii").splitlines():
        if not re.fullmatch(r"[0-9a-f]{64}  [^\s]+", line):
            raise ValueError(f"inventory_line_invalid:{path}")
        expected, relative = line.split("  ", 1)
        if relative in seen or digest(safe_path(relative).read_bytes()) != expected:
            raise ValueError(f"inventory_drift:{relative}")
        seen.add(relative)


def verify_candidate() -> tuple[str, dict[str, Any]]:
    raw = CANDIDATE_PATH.read_bytes()
    value = json.loads(raw)
    if canonical(value) != raw or set(value) != CANDIDATE_FIELDS:
        raise ValueError("candidate_schema_or_canonical_invalid")
    if value["schema_version"] != "m4-v132-candidate-manifest-v1" or value["protocol_id"] != PROTOCOL_ID:
        raise ValueError("candidate_identity_invalid")
    if value["author_role"] != "author/fixer: codex-root":
        raise ValueError("candidate_author_role_invalid")
    if value["historical_baseline_commit"] != "0bb791653927118a8ef661b7275f2854cec5623e":
        raise ValueError("candidate_baseline_invalid")
    if value["repair_plan_commit"] != "a012005335c8497cba732aaf90f8a3f95087b419":
        raise ValueError("candidate_plan_commit_invalid")
    if value["dependency_dag"] != [
        "P",
        "K",
        "G->H(P)",
        "C->P,K,G",
        "M->P,K,G,C",
        "R->H(M),commit",
        "S->H(M),H(R),commit",
    ]:
        raise ValueError("candidate_dag_invalid")
    artifacts = value["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("candidate_artifacts_invalid")
    paths: list[str] = []
    roles: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != ARTIFACT_FIELDS:
            raise ValueError("candidate_artifact_schema_invalid")
        relative = item["path"]
        if not isinstance(relative, str) or relative == CANDIDATE_PATH.relative_to(ROOT).as_posix():
            raise ValueError("candidate_self_reference")
        file_path = safe_path(relative)
        file_raw = file_path.read_bytes()
        if item["byte_count"] != len(file_raw) or item["sha256"] != digest(file_raw):
            raise ValueError(f"candidate_artifact_drift:{relative}")
        if not isinstance(item["role"], str):
            raise ValueError("candidate_artifact_role_invalid")
        paths.append(relative)
        roles.add(item["role"])
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("candidate_artifact_order_invalid")
    required_roles = {
        "P",
        "K",
        "G",
        "C",
        "generator",
        "directed_test",
        "historical_inventory",
        "review_prompt",
        "repair_plan_review",
        "chain_verifier",
    }
    if not required_roles <= roles:
        raise ValueError("candidate_artifact_role_missing")
    verify_hash_inventory(ROOT / "reviews/milestone-004-preregistration-v1.3.2/preregistration.sha256")
    verify_hash_inventory(ROOT / "reviews/milestone-004-preregistration-v1.3.2/historical-v1.3.1-inventory.sha256")
    return digest(raw), value


def parse_review(raw: bytes) -> dict[str, str]:
    text = raw.decode("utf-8")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            fields[key] = value
    required = {
        "REVIEWER_IDENTITY",
        "REVIEWED_COMMIT",
        "CANDIDATE_MANIFEST_SHA256",
        "ISOLATION",
        "COMMAND_EVIDENCE",
        "P0",
        "P1",
        "P2",
        "P3",
        "VERDICT",
        "FREEZE",
    }
    if set(fields) != required:
        raise ValueError("review_shape_invalid")
    return fields


def verify_review(candidate_sha: str) -> tuple[str, str]:
    raw = REVIEW_PATH.read_bytes()
    fields = parse_review(raw)
    if (
        fields["CANDIDATE_MANIFEST_SHA256"] != candidate_sha
        or HASH_RE.fullmatch(fields["CANDIDATE_MANIFEST_SHA256"]) is None
    ):
        raise ValueError("review_candidate_invalid")
    if COMMIT_RE.fullmatch(fields["REVIEWED_COMMIT"]) is None:
        raise ValueError("review_commit_invalid")
    if fields["REVIEWER_IDENTITY"] == "author/fixer: codex-root" or fields["ISOLATION"] != "PASS":
        raise ValueError("review_isolation_invalid")
    if any(fields[level] != "NONE" for level in ("P0", "P1", "P2", "P3")):
        raise ValueError("review_findings_present")
    if fields["VERDICT"] != "PASS" or fields["FREEZE"] != "APPROVE_EXACT_BYTES":
        raise ValueError("review_not_strict_pass")
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{fields['REVIEWED_COMMIT']}^{{commit}}"], cwd=ROOT, check=False, capture_output=True
    )
    if result.returncode:
        raise ValueError("review_commit_missing")
    return digest(raw), fields["REVIEWED_COMMIT"]


def verify_attestation(candidate_sha: str, review_sha: str, reviewed_commit: str) -> None:
    raw = ATTESTATION_PATH.read_bytes()
    value = json.loads(raw)
    if canonical(value) != raw or set(value) != ATTESTATION_FIELDS:
        raise ValueError("attestation_schema_or_canonical_invalid")
    if value["schema_version"] != "m4-v132-freeze-attestation-v1" or value["protocol_id"] != PROTOCOL_ID:
        raise ValueError("attestation_identity_invalid")
    if value["candidate_manifest"] != {"path": CANDIDATE_PATH.relative_to(ROOT).as_posix(), "sha256": candidate_sha}:
        raise ValueError("attestation_candidate_invalid")
    if value["review"] != {"path": REVIEW_PATH.relative_to(ROOT).as_posix(), "sha256": review_sha, "verdict": "PASS"}:
        raise ValueError("attestation_review_invalid")
    if value["reviewed_commit"] != reviewed_commit or value["freeze_outcome"] != "FROZEN":
        raise ValueError("attestation_outcome_invalid")
    if value["claims"] != [
        "candidate bytes unchanged after review",
        "P0-P3 NONE",
        "strict PASS",
        "no authorization created",
    ]:
        raise ValueError("attestation_claims_invalid")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--attestation", action="store_true")
    args = parser.parse_args()
    candidate_sha, _candidate = verify_candidate()
    summary = f"PASS candidate={candidate_sha}"
    if args.review or args.attestation:
        review_sha, reviewed_commit = verify_review(candidate_sha)
        summary += f" review={review_sha} commit={reviewed_commit}"
    if args.attestation:
        verify_attestation(candidate_sha, review_sha, reviewed_commit)
        summary += " frozen=true"
    print(summary)


if __name__ == "__main__":
    main()
