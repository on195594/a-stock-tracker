"""One-shot authorization and offline context derivation for the 603606 hybrid run."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from a_stock_tracker.qualitative.client import DEFAULT_GEMINI_MODEL
from a_stock_tracker.qualitative.orient_cable_pilot import _pdf_evidence, _pdf_pages
from a_stock_tracker.qualitative.production import context_missing_score_dimensions
from a_stock_tracker.qualitative.validator import validate_context_dict

AUTHORIZATION_FILENAME = "qualitative_v2_hybrid_authorizations.json"
AUTHORIZATION_CONFIG_PATH = Path("config/qualitative/hybrid_authorizations.json")
AUTHORIZATION_SCHEMA = "qualitative-v2-hybrid-authorizations-v1"
APPROVED_AUTHORIZATION_ID = "qualitative-v2-orient-cable-hybrid-20260719-01"
APPROVED_CONTEXT_HASH = "299efe53f541c8ca4f0165c4276607c00bcc5a4ada09466528456ba3b14d6b1f"
APPROVED_CONTEXT_RUN_ID = "orient-cable-hybrid-context-20260719-01"
APPROVED_SOURCE_RUN_ID = "orient-cable-context-20260719-04"
APPROVED_SOURCE_MANIFEST_HASH = "bee40eb10052338bf40f60b4f55b4de21960454cfc19b5134fe428f4091af430"
APPROVED_SOURCE_CONTEXT_HASH = "5430f1acfa77ffe2945aacca4e0a81425dc8f8fae210fbba7a715a2fb77376fa"
APPROVED_SOURCE_PDF_HASH = "b2ae50ca46f0c628879c6e20726d2ff6d84c8a2f10e5665adadfa63623bf8efc"


class HybridAuthorizationError(ValueError):
    """The requested hybrid run violates its one-shot authorization."""


@dataclass(frozen=True, slots=True)
class HybridAuthorization:
    authorization_id: str
    target_code: str
    context_input_hash: str
    context_run_id: str
    source_run_id: str
    source_manifest_sha256: str
    source_context_input_hash: str
    source_pdf_sha256: str
    model: str
    logical_call_limit: int
    http_attempt_limit: int
    source_http_attempt_limit: int
    pdf_download_limit: int


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")


def _secure_write(path: Path, raw: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _authorization_from_dict(value: object) -> HybridAuthorization:
    expected = {
        "authorization_id",
        "target_code",
        "context_input_hash",
        "context_run_id",
        "source_run_id",
        "source_manifest_sha256",
        "source_context_input_hash",
        "source_pdf_sha256",
        "model",
        "logical_call_limit",
        "http_attempt_limit",
        "source_http_attempt_limit",
        "pdf_download_limit",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise HybridAuthorizationError("active hybrid authorization contract drift")
    authorization = HybridAuthorization(**value)
    if authorization != HybridAuthorization(
        APPROVED_AUTHORIZATION_ID,
        "603606",
        APPROVED_CONTEXT_HASH,
        APPROVED_CONTEXT_RUN_ID,
        APPROVED_SOURCE_RUN_ID,
        APPROVED_SOURCE_MANIFEST_HASH,
        APPROVED_SOURCE_CONTEXT_HASH,
        APPROVED_SOURCE_PDF_HASH,
        DEFAULT_GEMINI_MODEL,
        1,
        3,
        0,
        0,
    ):
        raise HybridAuthorizationError("active hybrid authorization differs from the approved grant")
    return authorization


def load_active_hybrid_authorization(project_root: Path, authorization_id: str) -> HybridAuthorization:
    """Load the exact active grant and reject any retired or mismatched ID."""
    configured = project_root / AUTHORIZATION_CONFIG_PATH
    path = configured if configured.is_file() else project_root / AUTHORIZATION_FILENAME
    if path.is_symlink() or not path.is_file():
        raise HybridAuthorizationError("hybrid authorization ledger is missing or unsafe")
    try:
        ledger = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HybridAuthorizationError("hybrid authorization ledger is invalid") from exc
    if not isinstance(ledger, dict) or set(ledger) != {"schema_version", "active", "retired"}:
        raise HybridAuthorizationError("hybrid authorization ledger contract drift")
    if ledger.get("schema_version") != AUTHORIZATION_SCHEMA or not isinstance(ledger.get("retired"), list):
        raise HybridAuthorizationError("hybrid authorization ledger schema drift")
    retired = ledger["retired"]
    for entry in retired:
        if not isinstance(entry, dict) or not isinstance(entry.get("authorization_id"), str):
            raise HybridAuthorizationError("retired hybrid authorization entry is invalid")
        if entry["authorization_id"] == authorization_id:
            raise HybridAuthorizationError("hybrid authorization is retired and cannot be reused")
    active = ledger.get("active")
    if active is None:
        raise HybridAuthorizationError("no active hybrid authorization")
    authorization = _authorization_from_dict(active)
    if authorization.authorization_id != authorization_id:
        raise HybridAuthorizationError("authorization ID does not match the active hybrid grant")
    return authorization


def _safe_file(path: Path, *, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise HybridAuthorizationError(f"{label} is missing or unsafe")
    return path.read_bytes()


def _create_run_root(project_root: Path, run_id: str) -> Path:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id) is None or run_id in {".", ".."}:
        raise HybridAuthorizationError("hybrid context run ID is unsafe")
    base = project_root / "artifacts" / "qualitative-v2-orient-cable-hybrid"
    cursor = project_root
    for part in base.relative_to(project_root).parts:
        cursor /= part
        if cursor.is_symlink():
            raise HybridAuthorizationError("hybrid artifact ancestry contains a symlink")
        if cursor.exists() and not cursor.is_dir():
            raise HybridAuthorizationError("hybrid artifact ancestry contains a non-directory")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.chmod(0o700)
    root = base / run_id
    if root.exists() or root.is_symlink():
        raise HybridAuthorizationError("hybrid context run ID is create-only")
    root.mkdir(mode=0o700)
    (root / "contexts").mkdir(mode=0o700)
    return root


def build_authorized_hybrid_context(
    project_root: Path,
    authorization: HybridAuthorization,
) -> dict[str, object]:
    """Derive the approved context from sealed local inputs without network or database access."""
    if authorization.source_http_attempt_limit != 0 or authorization.pdf_download_limit != 0:
        raise HybridAuthorizationError("hybrid context derivation must be source-network free")
    source_root = project_root / "artifacts" / "qualitative-v2-orient-cable" / authorization.source_run_id
    if source_root.is_symlink() or not source_root.is_dir():
        raise HybridAuthorizationError("sealed source run is missing or unsafe")
    manifest_raw = _safe_file(source_root / "manifest.json", label="source manifest")
    manifest_hash = hashlib.sha256(manifest_raw).hexdigest()
    if manifest_hash != authorization.source_manifest_sha256:
        raise HybridAuthorizationError("source manifest hash drift")
    checksum = _safe_file(source_root / "manifest.sha256", label="source manifest checksum")
    if checksum != f"{manifest_hash}  manifest.json\n".encode("ascii"):
        raise HybridAuthorizationError("source manifest checksum drift")
    context_raw = _safe_file(source_root / "contexts" / "603606.json", label="source context")
    try:
        source_context = json.loads(context_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HybridAuthorizationError("source context is invalid") from exc
    if not isinstance(source_context, dict):
        raise HybridAuthorizationError("source context contract drift")
    source_validation = validate_context_dict(source_context)
    if not source_validation.valid or source_validation.context is None:
        raise HybridAuthorizationError(f"source context failed validation: {source_validation.rejection_reason}")
    if source_validation.context.compute_input_hash() != authorization.source_context_input_hash:
        raise HybridAuthorizationError("source context input hash drift")
    pdf_raw = _safe_file(source_root / "raw" / "01-annual-report.pdf", label="source PDF")
    pdf_hash = hashlib.sha256(pdf_raw).hexdigest()
    if pdf_hash != authorization.source_pdf_sha256:
        raise HybridAuthorizationError("source PDF hash drift")
    pages = _pdf_pages(pdf_raw)
    moat = _pdf_evidence(
        pages,
        raw_sha256=pdf_hash,
        dimension="moat",
        claim_category="competitive_moat",
        patterns=("拥有 500kV", "±535kV", "DNV 认证"),
    )
    market_pos = _pdf_evidence(
        pages,
        raw_sha256=pdf_hash,
        dimension="market_pos",
        claim_category="industry_position",
        patterns=("国内陆缆系统、海缆系统核心供应商", "最具竞争力企业 10 强"),
    )
    financial = [
        item
        for item in source_context["evidence"]
        if isinstance(item, dict) and item.get("claim_category") == "financial_performance"
    ]
    if len(financial) != 1 or moat is None or market_pos is None:
        raise HybridAuthorizationError("sealed inputs do not produce the approved hybrid evidence set")
    context = {
        "code": "603606",
        "name": "东方电缆",
        "industry": "电力设备",
        "as_of_date": "2026-07-19",
        "schema_version": "qualitative-score-v2",
        "rubric_version": "rubric-v1",
        "taxonomy_version": "taxonomy-v1",
        "evidence": [financial[0], moat, market_pos],
    }
    validation = validate_context_dict(context)
    if not validation.valid or validation.context is None:
        raise HybridAuthorizationError(f"derived hybrid context is invalid: {validation.rejection_reason}")
    input_hash = validation.context.compute_input_hash()
    if input_hash != authorization.context_input_hash:
        raise HybridAuthorizationError("derived hybrid context hash differs from the approved SHA")
    missing = list(context_missing_score_dimensions(validation.context))
    if missing != ["sentiment"]:
        raise HybridAuthorizationError("derived hybrid readiness differs from the approved scope")
    run_root = _create_run_root(project_root, authorization.context_run_id)
    _secure_write(run_root / "contexts" / "603606.json", _canonical_bytes(context))
    manifest = {
        "schema_version": "qualitative-v2-orient-cable-hybrid-context-v1",
        "authorization_id": authorization.authorization_id,
        "code": authorization.target_code,
        "context_input_hash": input_hash,
        "database_reads": 0,
        "database_writes": 0,
        "gemini_calls": 0,
        "missing_score_dimensions": missing,
        "model": authorization.model,
        "pdf_downloads": 0,
        "scoreable_dimensions": ["moat", "market_pos"],
        "source_context_input_hash": authorization.source_context_input_hash,
        "source_http_attempts": 0,
        "source_manifest_sha256": manifest_hash,
        "source_pdf_sha256": pdf_hash,
        "source_run_id": authorization.source_run_id,
    }
    manifest_bytes = _canonical_bytes(manifest)
    derived_manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    _secure_write(run_root / "manifest.json", manifest_bytes)
    _secure_write(run_root / "manifest.sha256", f"{derived_manifest_hash}  manifest.json\n".encode("ascii"))
    return {
        "authorization_id": authorization.authorization_id,
        "context_input_hash": input_hash,
        "contexts_path": str(run_root / "contexts"),
        "manifest_sha256": derived_manifest_hash,
        "missing_score_dimensions": missing,
        "scoreable_dimensions": ["moat", "market_pos"],
        "source_http_attempts": 0,
        "pdf_downloads": 0,
        "status": "READY_FOR_HYBRID_MODEL",
    }


__all__ = [
    "HybridAuthorization",
    "HybridAuthorizationError",
    "build_authorized_hybrid_context",
    "load_active_hybrid_authorization",
]
