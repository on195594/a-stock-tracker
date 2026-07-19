"""Offline, create-only construction of a validated real M5 bundle."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from a_stock_tracker.qualitative.m5.pipeline import (
    MAX_DOCUMENT_BYTES,
    MAX_JSON_BYTES,
    M5_BUNDLE_VERSION,
    M5_PROVENANCE_VERSION,
    _canonical_json,
    _is_sha256,
    _load_json_bytes,
    _load_sample,
    _read_regular_file,
    _safe_relative_file,
    _sha256_bytes,
    _validate_coverage_report,
    load_bundle,
    preview_bundle,
)

M5_REAL_BUNDLE_INPUT_VERSION = "m5-real-bundle-input-v1"

_INPUT_FIELDS = frozenset(
    {
        "input_version",
        "as_of_date",
        "source_approval_id",
        "source_scopes",
        "coverage_report_path",
        "companies",
    }
)
_INPUT_COMPANY_FIELDS = frozenset({"code", "context_path", "evidence_provenance"})
_PROVENANCE_FIELDS = frozenset(
    {"evidence_id", "official_source", "source_scope", "document_path", "document_sha256", "locator"}
)


def _secure_write(path: Path, raw: bytes) -> None:
    """Create one private regular file inside the unpublished temporary root."""
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


def _require_private_tree(root: Path) -> None:
    """Normalize and verify private modes before publishing the bundle."""
    for path in (root, *sorted(root.rglob("*"))):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"bundle output contains a symlink: {path}")
        if stat.S_ISDIR(metadata.st_mode):
            path.chmod(0o700)
        elif stat.S_ISREG(metadata.st_mode):
            path.chmod(0o600)
        else:
            raise ValueError(f"bundle output contains a non-regular entry: {path}")


def _validate_header(value: Mapping[str, object]) -> tuple[str, str, list[str]]:
    if set(value) != _INPUT_FIELDS:
        raise ValueError("real bundle input has the wrong field set")
    if value["input_version"] != M5_REAL_BUNDLE_INPUT_VERSION:
        raise ValueError("unsupported real bundle input_version")
    as_of_date = value["as_of_date"]
    approval = value["source_approval_id"]
    scopes = value["source_scopes"]
    if not isinstance(as_of_date, str):
        raise ValueError("real bundle input as_of_date must be a string")
    if not isinstance(approval, str) or not approval.strip():
        raise ValueError("real bundle input requires source_approval_id")
    if (
        not isinstance(scopes, list)
        or not scopes
        or not all(isinstance(item, str) and item.strip() and item != "synthetic" for item in scopes)
        or len(set(scopes)) != len(scopes)
    ):
        raise ValueError("real bundle input requires unique non-synthetic source_scopes")
    return as_of_date, approval, cast(list[str], scopes)


def _copy_document(
    *,
    input_base: Path,
    output_documents: Path,
    provenance: Mapping[str, object],
    copied_documents: dict[str, str],
) -> str:
    expected_hash = provenance["document_sha256"]
    if not _is_sha256(expected_hash):
        raise ValueError("provenance document_sha256 must be a lowercase SHA-256")
    _source_path, raw = _safe_relative_file(
        input_base,
        provenance["document_path"],
        maximum_bytes=MAX_DOCUMENT_BYTES,
    )
    actual_hash = _sha256_bytes(raw)
    if actual_hash != expected_hash:
        raise ValueError(f"source document hash drift: {provenance['document_path']!r}")
    if actual_hash not in copied_documents:
        destination = output_documents / actual_hash
        _secure_write(destination, raw)
        copied_documents[actual_hash] = f"documents/{actual_hash}"
    return copied_documents[actual_hash]


def build_real_bundle(
    sample_path: str | os.PathLike[str],
    input_manifest_path: str | os.PathLike[str],
    output_root: str | os.PathLike[str],
) -> dict[str, object]:
    """Build and self-validate one private real bundle without network or credentials."""
    sample = Path(sample_path)
    input_manifest = Path(input_manifest_path)
    output = Path(output_root)
    if output.exists() or output.is_symlink():
        raise ValueError(f"bundle output root already exists: {output}")

    sample_sha256, sample_companies = _load_sample(sample)
    input_raw = _read_regular_file(input_manifest, maximum_bytes=MAX_JSON_BYTES)
    value = _load_json_bytes(input_raw, label="real bundle input")
    as_of_date, approval, source_scopes = _validate_header(value)
    input_base = input_manifest.parent

    _coverage_path, coverage_raw = _safe_relative_file(
        input_base,
        value["coverage_report_path"],
        maximum_bytes=MAX_DOCUMENT_BYTES,
    )
    _validate_coverage_report(coverage_raw)
    coverage_sha256 = _sha256_bytes(coverage_raw)

    companies_raw = value["companies"]
    if not isinstance(companies_raw, list) or len(companies_raw) != len(sample_companies):
        raise ValueError("real bundle input must contain exactly the fixed sample companies")
    by_code: dict[str, dict[str, object]] = {}
    for company in companies_raw:
        if not isinstance(company, dict) or set(company) != _INPUT_COMPANY_FIELDS:
            raise ValueError("real bundle input company has the wrong field set")
        code = company["code"]
        if not isinstance(code, str) or code in by_code:
            raise ValueError(f"duplicate or invalid real bundle company: {code!r}")
        by_code[code] = cast(dict[str, object], company)
    sample_codes = {company.code for company in sample_companies}
    if set(by_code) != sample_codes:
        raise ValueError("real bundle input company set differs from the fixed sample")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        assert temporary is not None
        temporary.chmod(0o700)
        contexts_dir = temporary / "contexts"
        documents_dir = temporary / "documents"
        contexts_dir.mkdir(mode=0o700)
        documents_dir.mkdir(mode=0o700)
        _secure_write(temporary / "coverage-report.json", coverage_raw)

        manifest_companies: list[dict[str, object]] = []
        copied_documents: dict[str, str] = {}
        for index, sample_company in enumerate(sample_companies):
            company = by_code[sample_company.code]
            _context_source, context_raw = _safe_relative_file(
                input_base,
                company["context_path"],
                maximum_bytes=MAX_JSON_BYTES,
            )
            context_name = f"{index:02d}-{sample_company.code}.json"
            _secure_write(contexts_dir / context_name, context_raw)

            provenance_raw = company["evidence_provenance"]
            if not isinstance(provenance_raw, list):
                raise ValueError(f"evidence_provenance for {sample_company.code} must be a list")
            final_provenance: list[dict[str, object]] = []
            for item in provenance_raw:
                if not isinstance(item, dict) or set(item) != _PROVENANCE_FIELDS:
                    raise ValueError(f"provenance for {sample_company.code} has the wrong field set")
                copied_path = _copy_document(
                    input_base=input_base,
                    output_documents=documents_dir,
                    provenance=item,
                    copied_documents=copied_documents,
                )
                final_item = dict(item)
                final_item["document_path"] = copied_path
                final_provenance.append(final_item)
            manifest_companies.append(
                {
                    "code": sample_company.code,
                    "name": sample_company.name,
                    "industry": sample_company.industry,
                    "super_stratum": sample_company.super_stratum,
                    "market_cap_stratum": sample_company.market_cap_stratum,
                    "context_path": f"contexts/{context_name}",
                    "context_sha256": _sha256_bytes(context_raw),
                    "evidence_provenance": final_provenance,
                }
            )

        manifest = {
            "bundle_version": M5_BUNDLE_VERSION,
            "bundle_kind": "real",
            "as_of_date": as_of_date,
            "sample_sha256": sample_sha256,
            "provenance_schema_version": M5_PROVENANCE_VERSION,
            "source_scopes": source_scopes,
            "coverage_report": {
                "path": "coverage-report.json",
                "sha256": coverage_sha256,
                "all_layers_approved": True,
            },
            "source_approval_id": approval,
            "excluded_layers": [],
            "companies": manifest_companies,
        }
        manifest_raw = (_canonical_json(manifest) + "\n").encode("utf-8")
        _secure_write(temporary / "bundle-manifest.json", manifest_raw)
        _require_private_tree(temporary)

        loaded = load_bundle(sample, temporary / "bundle-manifest.json")
        if loaded.coverage_report_sha256 != coverage_sha256 or loaded.source_approval_id != approval:
            raise ValueError("self-validated bundle lost its coverage or source approval binding")
        preview = preview_bundle(sample, temporary / "bundle-manifest.json")
        if output.exists() or output.is_symlink():
            raise ValueError(f"bundle output root appeared during construction: {output}")
        os.replace(temporary, output)
        temporary = None
        return preview
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)


__all__ = ["M5_REAL_BUNDLE_INPUT_VERSION", "build_real_bundle"]
