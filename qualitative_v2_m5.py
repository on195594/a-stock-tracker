"""Fixture-first, fail-closed orchestration for the M5 bounded shadow batch.

The module consumes an already-built static bundle.  It never retrieves
evidence, reads production state, or selects a provider.  This implementation
enables execution only for synthetic bundles and accepts injected transports,
so tests cannot accidentally reach Claude or Gemini.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import stat
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import IO, cast

from qualitative_v2_client import DEFAULT_GEMINI_MODEL, GeminiCallResult, ValidationStatus
from qualitative_v2_contract import DIMENSION_NAMES
from qualitative_v2_types import QualitativeContext
from qualitative_v2_validator import validate_context_dict, validate_model_output

M5_BUNDLE_VERSION = "m5-bundle-v1"
M5_PROVENANCE_VERSION = "m5-provenance-v1"
M5_SUPPORT_AUDIT_VERSION = "m5-support-audit-v1"
EXPECTED_SAMPLE_SHA256 = "b278a7b00b71fd54e34519dead098602a8748635f1350414e1b78538a3d7635d"
EXPECTED_COMPANY_COUNT = 36
EXPECTED_CELL_SIZE = 3
SUPER_STRATA = (
    "金融地产",
    "能源材料",
    "工业基础设施",
    "科技通信",
    "消费",
    "医疗公用事业",
)
CAP_STRATA = ("low", "high")
VALID_MODEL_STATUSES = frozenset({ValidationStatus.VALID_SCORED.value, ValidationStatus.VALID_INSUFFICIENT_DATA.value})
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
MAX_REASON_CHARS = 256
Z_95 = 1.959963984540054

_SAMPLE_FIELDS = (
    "ts_code",
    "name",
    "industry_code",
    "industry_name",
    "super_stratum",
    "market_cap_stratum",
    "total_mv",
    "trade_date",
    "exchange",
    "market",
    "list_date",
    "sampling_hash",
)
_COMPANY_FIELDS = frozenset(
    {
        "code",
        "name",
        "industry",
        "super_stratum",
        "market_cap_stratum",
        "context_path",
        "context_sha256",
        "evidence_provenance",
    }
)
_PROVENANCE_FIELDS = frozenset(
    {"evidence_id", "official_source", "source_scope", "document_path", "document_sha256", "locator"}
)
_MANIFEST_FIELDS = frozenset(
    {
        "bundle_version",
        "bundle_kind",
        "as_of_date",
        "sample_sha256",
        "provenance_schema_version",
        "source_scopes",
        "coverage_report",
        "source_approval_id",
        "excluded_layers",
        "companies",
    }
)
_COVERAGE_REPORT_FIELDS = frozenset(
    {
        "rows",
        "overall_passed",
        "milestone_005_approval_blocked",
        "corpus_manifest_sha256",
        "review_index_sha256",
        "reviewer_seal_sha256s",
        "adjudication_sha256",
    }
)
_COVERAGE_ROW_FIELDS = frozenset(
    {
        "layer_type",
        "layer",
        "denominator",
        "scorable",
        "insufficient",
        "proportion",
        "passed",
        "wilson_lower",
        "wilson_upper",
        "wilson_lower_display",
        "wilson_upper_display",
        "required_action",
        "scope_disposition",
    }
)


@dataclass(frozen=True)
class SampleCompany:
    """One immutable company/layer identity from the approved M4 sample."""

    code: str
    name: str
    industry: str
    super_stratum: str
    market_cap_stratum: str


@dataclass(frozen=True)
class BundleCompany:
    """A validated context bound to its M4 sample metadata."""

    sample: SampleCompany
    context: QualitativeContext
    context_sha256: str


@dataclass(frozen=True)
class LoadedBundle:
    """Validated static M5 bundle with independent sample and bundle hashes."""

    kind: str
    as_of_date: str
    sample_path: Path
    sample_sha256: str
    manifest_path: Path
    bundle_sha256: str
    companies: tuple[BundleCompany, ...]
    source_approval_id: str | None
    coverage_report_sha256: str | None


@dataclass(frozen=True)
class ModelResponse:
    """One no-retry Claude/fake scoring response before local validation."""

    raw_output: Mapping[str, object] | None
    failure_reason: str | None = None
    attempts: int = 1


@dataclass(frozen=True)
class SupportAuditResponse:
    """One no-retry support-audit response before local shape validation."""

    raw_output: Mapping[str, object] | None
    failure_reason: str | None = None
    attempts: int = 1


ReferenceClient = Callable[[QualitativeContext, str], ModelResponse]
GeminiClient = Callable[[QualitativeContext, str], GeminiCallResult]
SupportClient = Callable[[QualitativeContext, Mapping[str, object], str], SupportAuditResponse]


def _canonical_json(value: object) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _read_regular_file(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"missing input file: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"symlink input is forbidden: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"input is not a regular file: {path}")
    if metadata.st_size > maximum_bytes:
        raise ValueError(f"input exceeds {maximum_bytes} bytes: {path}")
    return path.read_bytes()


def _safe_relative_file(base: Path, raw_path: object, *, maximum_bytes: int) -> tuple[Path, bytes]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("bundle file path must be a non-empty relative string")
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe bundle path: {raw_path!r}")
    current = base
    for part in relative.parts:
        if part in ("", "."):
            raise ValueError(f"unsafe bundle path: {raw_path!r}")
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ValueError(f"missing bundle file: {raw_path!r}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"symlink bundle path is forbidden: {raw_path!r}")
    return current, _read_regular_file(current, maximum_bytes=maximum_bytes)


def _load_json_bytes(raw: bytes, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return cast(dict[str, object], value)


def _parse_as_of(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("bundle as_of_date must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("bundle as_of_date must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError("bundle as_of_date must use YYYY-MM-DD")
    return value


def _load_sample(sample_path: Path) -> tuple[str, tuple[SampleCompany, ...]]:
    raw = _read_regular_file(sample_path, maximum_bytes=MAX_JSON_BYTES)
    sample_sha256 = _sha256_bytes(raw)
    if sample_sha256 != EXPECTED_SAMPLE_SHA256:
        raise ValueError(f"sample SHA-256 mismatch: expected {EXPECTED_SAMPLE_SHA256}, got {sample_sha256}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("sample must be UTF-8 CSV") from exc
    reader = csv.DictReader(text.splitlines())
    if tuple(reader.fieldnames or ()) != _SAMPLE_FIELDS:
        raise ValueError("sample CSV field set/order does not match the M4 contract")
    rows = list(reader)
    if len(rows) != EXPECTED_COMPANY_COUNT:
        raise ValueError(f"sample must contain exactly {EXPECTED_COMPANY_COUNT} companies")

    companies: list[SampleCompany] = []
    seen_codes: set[str] = set()
    cells: Counter[tuple[str, str]] = Counter()
    for row in rows:
        code = row["ts_code"]
        if code in seen_codes:
            raise ValueError(f"duplicate company in sample: {code}")
        seen_codes.add(code)
        super_stratum = row["super_stratum"]
        cap = row["market_cap_stratum"]
        if super_stratum not in SUPER_STRATA or cap not in CAP_STRATA:
            raise ValueError(f"unknown sample layer for {code}")
        cells[(super_stratum, cap)] += 1
        companies.append(
            SampleCompany(
                code=code,
                name=row["name"],
                industry=row["industry_name"],
                super_stratum=super_stratum,
                market_cap_stratum=cap,
            )
        )
    expected_cells = {(super_stratum, cap) for super_stratum in SUPER_STRATA for cap in CAP_STRATA}
    if set(cells) != expected_cells or any(cells[cell] != EXPECTED_CELL_SIZE for cell in expected_cells):
        raise ValueError("sample must contain exactly three companies in each of the 12 registered cells")
    return sample_sha256, tuple(companies)


def _validate_coverage_report(raw: bytes) -> None:
    """Validate the actual frozen M4 result instead of a manifest assertion."""
    report = _load_json_bytes(raw, label="M4 coverage report")
    if set(report) != _COVERAGE_REPORT_FIELDS:
        raise ValueError("M4 coverage report has the wrong field set")
    if report["overall_passed"] is not True:
        raise ValueError("M4 coverage report did not pass overall")
    if report["milestone_005_approval_blocked"] is not False:
        raise ValueError("M4 coverage report still blocks M5 approval")

    lineage_hashes = (
        report["corpus_manifest_sha256"],
        report["review_index_sha256"],
        report["adjudication_sha256"],
    )
    seals = report["reviewer_seal_sha256s"]
    if not all(_is_sha256(value) for value in lineage_hashes) or not (
        isinstance(seals, list) and len(seals) == 2 and all(_is_sha256(value) for value in seals)
    ):
        raise ValueError("M4 coverage report has invalid lineage hashes")

    rows = report["rows"]
    if not isinstance(rows, list):
        raise ValueError("M4 coverage report rows must be a list")
    expected_layers = {
        *(("industry", layer, 6, 4) for layer in SUPER_STRATA),
        *(("market_cap", layer, 18, 12) for layer in CAP_STRATA),
    }
    seen_layers: set[tuple[str, str, int, int]] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != _COVERAGE_ROW_FIELDS:
            raise ValueError("M4 coverage report has an invalid layer row")
        layer_type = row["layer_type"]
        layer = row["layer"]
        denominator = row["denominator"]
        scorable = row["scorable"]
        insufficient = row["insufficient"]
        if not (
            isinstance(layer_type, str)
            and isinstance(layer, str)
            and isinstance(denominator, int)
            and not isinstance(denominator, bool)
            and isinstance(scorable, int)
            and not isinstance(scorable, bool)
            and isinstance(insufficient, int)
            and not isinstance(insufficient, bool)
        ):
            raise ValueError("M4 coverage report layer counts are invalid")
        threshold = 4 if layer_type == "industry" else 12
        identity = (layer_type, layer, denominator, threshold)
        if identity not in expected_layers or identity in seen_layers:
            raise ValueError("M4 coverage report layer set is invalid")
        seen_layers.add(identity)
        proportion = row["proportion"]
        lower = row["wilson_lower"]
        upper = row["wilson_upper"]
        expected_lower, expected_upper = _wilson_interval(scorable, denominator)
        if (
            scorable < threshold
            or scorable > denominator
            or insufficient != denominator - scorable
            or not isinstance(proportion, (int, float))
            or isinstance(proportion, bool)
            or float(proportion) != scorable / denominator
            or not isinstance(lower, (int, float))
            or isinstance(lower, bool)
            or not isinstance(upper, (int, float))
            or isinstance(upper, bool)
            or float(lower) != expected_lower
            or float(upper) != expected_upper
            or row["passed"] is not True
            or row["required_action"] != "none"
            or row["scope_disposition"] != "not_required"
            or row["wilson_lower_display"] != f"{expected_lower:.6f}"
            or row["wilson_upper_display"] != f"{expected_upper:.6f}"
        ):
            raise ValueError("M4 coverage report layer did not pass its frozen gate")
    if seen_layers != expected_layers:
        raise ValueError("M4 coverage report does not cover all eight frozen layers")


def _wilson_interval(scorable: int, denominator: int) -> tuple[float, float]:
    proportion = scorable / denominator
    z_squared = Z_95 * Z_95
    adjusted_denominator = 1.0 + z_squared / denominator
    center = (proportion + z_squared / (2.0 * denominator)) / adjusted_denominator
    half_width = (
        Z_95
        / adjusted_denominator
        * math.sqrt(proportion * (1.0 - proportion) / denominator + z_squared / (4.0 * denominator * denominator))
    )
    lower = 0.0 if scorable == 0 else max(0.0, center - half_width)
    upper = 1.0 if scorable == denominator else min(1.0, center + half_width)
    return lower, upper


def _validate_real_gate(manifest: Mapping[str, object], base: Path) -> tuple[str, str]:
    approval = manifest["source_approval_id"]
    if not isinstance(approval, str) or not approval.strip():
        raise ValueError("real bundle requires a non-empty source_approval_id")
    excluded = manifest["excluded_layers"]
    if excluded != []:
        raise ValueError("real bundle cannot proceed when any sample layer is excluded")
    coverage = manifest["coverage_report"]
    if not isinstance(coverage, dict) or set(coverage) != {"path", "sha256", "all_layers_approved"}:
        raise ValueError("real bundle requires the complete M4 coverage_report gate")
    if coverage["all_layers_approved"] is not True:
        raise ValueError("M4 coverage report has not approved every sample layer")
    expected_hash = coverage["sha256"]
    if not _is_sha256(expected_hash):
        raise ValueError("coverage_report.sha256 must be a lowercase SHA-256")
    _path, raw = _safe_relative_file(base, coverage["path"], maximum_bytes=MAX_DOCUMENT_BYTES)
    actual_hash = _sha256_bytes(raw)
    if actual_hash != expected_hash:
        raise ValueError("M4 coverage report hash drift")
    _validate_coverage_report(raw)
    return approval, actual_hash


def load_bundle(sample_path: str | os.PathLike[str], manifest_path: str | os.PathLike[str]) -> LoadedBundle:
    """Validate a fixed sample and static synthetic/real bundle without credentials or artifacts."""
    sample = Path(sample_path)
    manifest_file = Path(manifest_path)
    sample_sha256, sample_companies = _load_sample(sample)
    manifest_raw = _read_regular_file(manifest_file, maximum_bytes=MAX_JSON_BYTES)
    manifest_sha256 = _sha256_bytes(manifest_raw)
    manifest = _load_json_bytes(manifest_raw, label="bundle manifest")
    if set(manifest) != _MANIFEST_FIELDS:
        raise ValueError(f"bundle manifest has wrong field set: {sorted(manifest)}")
    if manifest["bundle_version"] != M5_BUNDLE_VERSION:
        raise ValueError("unsupported bundle_version")
    kind = manifest["bundle_kind"]
    if kind not in ("synthetic", "real"):
        raise ValueError("bundle_kind must be synthetic or real")
    if manifest["sample_sha256"] != sample_sha256:
        raise ValueError("bundle sample_sha256 does not match the validated sample")
    if manifest["provenance_schema_version"] != M5_PROVENANCE_VERSION:
        raise ValueError("unsupported provenance_schema_version")
    as_of_date = _parse_as_of(manifest["as_of_date"])
    scopes_raw = manifest["source_scopes"]
    if (
        not isinstance(scopes_raw, list)
        or not scopes_raw
        or not all(isinstance(item, str) and item.strip() for item in scopes_raw)
        or len(set(scopes_raw)) != len(scopes_raw)
    ):
        raise ValueError("source_scopes must be a non-empty unique string list")
    source_scopes = cast(list[str], scopes_raw)
    base = manifest_file.parent
    source_approval_id: str | None = None
    coverage_sha256: str | None = None
    if kind == "synthetic":
        if source_scopes != ["synthetic"]:
            raise ValueError("synthetic bundle source_scopes must be exactly ['synthetic']")
        if manifest["coverage_report"] is not None or manifest["source_approval_id"] is not None:
            raise ValueError("synthetic bundle must not claim real coverage or source approval")
        if manifest["excluded_layers"] != []:
            raise ValueError("synthetic bundle must not exclude sample layers")
    else:
        if "synthetic" in source_scopes:
            raise ValueError("real bundle may not use the synthetic source scope")
        source_approval_id, coverage_sha256 = _validate_real_gate(manifest, base)

    companies_raw = manifest["companies"]
    if not isinstance(companies_raw, list) or len(companies_raw) != EXPECTED_COMPANY_COUNT:
        raise ValueError(f"bundle must contain exactly {EXPECTED_COMPANY_COUNT} companies")
    sample_by_code = {company.code: company for company in sample_companies}
    loaded: list[BundleCompany] = []
    seen_codes: set[str] = set()
    for index, company_raw in enumerate(companies_raw):
        if not isinstance(company_raw, dict) or set(company_raw) != _COMPANY_FIELDS:
            raise ValueError(f"bundle company[{index}] has the wrong field set")
        code = company_raw["code"]
        if not isinstance(code, str) or code in seen_codes or code not in sample_by_code:
            raise ValueError(f"missing, duplicate, or unknown bundle company: {code!r}")
        seen_codes.add(code)
        sample_company = sample_by_code[code]
        expected_identity = {
            "name": sample_company.name,
            "industry": sample_company.industry,
            "super_stratum": sample_company.super_stratum,
            "market_cap_stratum": sample_company.market_cap_stratum,
        }
        for field, expected in expected_identity.items():
            if company_raw[field] != expected:
                raise ValueError(f"bundle company {code} has sample metadata drift in {field}")
        context_hash = company_raw["context_sha256"]
        if not _is_sha256(context_hash):
            raise ValueError(f"bundle company {code} has an invalid context_sha256")
        _context_path, context_raw = _safe_relative_file(
            base, company_raw["context_path"], maximum_bytes=MAX_JSON_BYTES
        )
        if _sha256_bytes(context_raw) != context_hash:
            raise ValueError(f"context hash drift for {code}")
        context_dict = _load_json_bytes(context_raw, label=f"context for {code}")
        validation = validate_context_dict(context_dict)
        if not validation.valid or validation.context is None:
            raise ValueError(f"invalid context for {code}: {validation.rejection_reason}")
        context = validation.context
        if (context.code, context.name, context.industry) != (
            sample_company.code,
            sample_company.name,
            sample_company.industry,
        ):
            raise ValueError(f"context identity drift for {code}")
        if context.as_of_date != as_of_date:
            raise ValueError(f"context as_of_date drift for {code}")

        provenance_raw = company_raw["evidence_provenance"]
        if not isinstance(provenance_raw, list):
            raise ValueError(f"evidence_provenance for {code} must be a list")
        evidence_ids = {item.evidence_id for item in context.evidence}
        provenance_ids: set[str] = set()
        for provenance in provenance_raw:
            if not isinstance(provenance, dict) or set(provenance) != _PROVENANCE_FIELDS:
                raise ValueError(f"provenance for {code} has the wrong field set")
            evidence_id = provenance["evidence_id"]
            if not isinstance(evidence_id, str) or evidence_id in provenance_ids:
                raise ValueError(f"duplicate or invalid provenance evidence_id for {code}")
            provenance_ids.add(evidence_id)
            if provenance["official_source"] is not True:
                raise ValueError(f"provenance for {code}/{evidence_id} is not an official source")
            if provenance["source_scope"] not in source_scopes:
                raise ValueError(f"provenance for {code}/{evidence_id} uses an unapproved source scope")
            if not isinstance(provenance["locator"], str) or not provenance["locator"].strip():
                raise ValueError(f"provenance for {code}/{evidence_id} has no locator")
            document_hash = provenance["document_sha256"]
            if not _is_sha256(document_hash):
                raise ValueError(f"provenance for {code}/{evidence_id} has an invalid document hash")
            _document_path, document_raw = _safe_relative_file(
                base, provenance["document_path"], maximum_bytes=MAX_DOCUMENT_BYTES
            )
            if _sha256_bytes(document_raw) != document_hash:
                raise ValueError(f"source document hash drift for {code}/{evidence_id}")
        if provenance_ids != evidence_ids:
            raise ValueError(f"provenance/evidence ID mismatch for {code}")
        loaded.append(BundleCompany(sample=sample_company, context=context, context_sha256=context_hash))

    if seen_codes != set(sample_by_code):
        raise ValueError("bundle company set does not exactly match the sample")
    if [company.sample.code for company in loaded] != [company.code for company in sample_companies]:
        raise ValueError("bundle company order must exactly match the sample")
    return LoadedBundle(
        kind=cast(str, kind),
        as_of_date=as_of_date,
        sample_path=sample.absolute(),
        sample_sha256=sample_sha256,
        manifest_path=manifest_file.absolute(),
        bundle_sha256=manifest_sha256,
        companies=tuple(loaded),
        source_approval_id=source_approval_id,
        coverage_report_sha256=coverage_sha256,
    )


def preview_bundle(sample_path: str | os.PathLike[str], manifest_path: str | os.PathLike[str]) -> dict[str, object]:
    """Return a credential-free aggregate preview and create no artifacts."""
    bundle = load_bundle(sample_path, manifest_path)
    cells = Counter((company.sample.super_stratum, company.sample.market_cap_stratum) for company in bundle.companies)
    return {
        "as_of_date": bundle.as_of_date,
        "bundle_kind": bundle.kind,
        "bundle_sha256": bundle.bundle_sha256,
        "company_count": len(bundle.companies),
        "sample_sha256": bundle.sample_sha256,
        "cells": [
            {"super_stratum": super_stratum, "market_cap_stratum": cap, "count": cells[(super_stratum, cap)]}
            for super_stratum in SUPER_STRATA
            for cap in CAP_STRATA
        ],
        "validated": True,
    }


def _validate_exact_claude_model(model: str) -> None:
    if (
        not model
        or len(model) > 128
        or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in model)
    ):
        raise ValueError("Claude model must be a bounded exact model identifier")
    if model.lower() in {"sonnet", "opus", "haiku"} or not any(char.isdigit() for char in model):
        raise ValueError("Claude moving aliases are forbidden; provide the full exact model ID")


def _assert_synthetic_execution(bundle: LoadedBundle) -> None:
    if bundle.kind != "synthetic":
        raise ValueError("real M5 execution is not authorized by the fixture-first implementation")


def _secure_create_jsonl(path: Path) -> IO[str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "w", encoding="utf-8")


def _write_jsonl(handle: IO[str], record: Mapping[str, object]) -> None:
    handle.write(_canonical_json(record) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def _atomic_summary(run_root: Path, summary: Mapping[str, object]) -> None:
    serialized = (_canonical_json(summary) + "\n").encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=".run-summary-", dir=run_root)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, run_root / "run-summary.json")
        os.chmod(run_root / "run-summary.json", 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _create_run_root(run_root: Path) -> None:
    for ancestor in (run_root.parent, *run_root.parent.parents):
        if ancestor.exists() and ancestor.is_symlink():
            raise ValueError("run-root ancestors may not be symlinks")
    run_root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if run_root.parent.is_symlink():
        raise ValueError("run-root parent may not be a symlink")
    try:
        run_root.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("run root already exists; M5 runs are never resumed or overwritten") from exc
    os.chmod(run_root, 0o700)


def _bounded_reason(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    cleaned = " ".join(value.split())
    for variable in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_API_KEY"):
        secret = os.environ.get(variable, "")
        if secret:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    return cleaned[:MAX_REASON_CHARS]


def _validate_scoring_response(
    context: QualitativeContext, response: ModelResponse
) -> tuple[str, dict[str, object] | None, str | None]:
    if response.attempts != 1:
        return "CALL_BOUND_VIOLATION", None, "Claude logical calls must have exactly one attempt"
    if response.raw_output is None:
        return "CALL_FAILED", None, _bounded_reason(response.failure_reason, "MODEL_CALL_FAILED")
    raw = dict(response.raw_output)
    validation = validate_model_output(raw, context=context)
    if not validation.valid:
        return "OUTPUT_INVALID", raw, _bounded_reason(validation.rejection_reason, "LOCAL_VALIDATION_FAILED")
    assert validation.result is not None
    status = (
        ValidationStatus.VALID_SCORED.value
        if validation.result.overall_status == "scored"
        else ValidationStatus.VALID_INSUFFICIENT_DATA.value
    )
    return status, raw, None


def _base_summary(bundle: LoadedBundle, run_root: Path, claude_model: str) -> dict[str, object]:
    return {
        "run_version": "m5-run-v1",
        "run_root": str(run_root),
        "sample_path": str(bundle.sample_path),
        "sample_sha256": bundle.sample_sha256,
        "bundle_path": str(bundle.manifest_path),
        "bundle_sha256": bundle.bundle_sha256,
        "bundle_kind": bundle.kind,
        "as_of_date": bundle.as_of_date,
        "company_count": len(bundle.companies),
        "claude_model": claude_model,
        "gemini_model": DEFAULT_GEMINI_MODEL,
        "claude_call_limit": 72,
        "gemini_logical_call_limit": 36,
        "gemini_http_attempt_limit": 108,
        "reference_attempted": 0,
        "reference_valid": 0,
        "gemini_attempted": 0,
        "gemini_http_attempts": 0,
        "support_attempted": 0,
        "not_attempted": [],
        "reference_artifact_sha256": None,
        "gemini_artifact_sha256": None,
        "support_artifact_sha256": None,
        "aggregate_report_sha256": None,
        "terminal_status": None,
        "state": "REFERENCE_RUNNING",
    }


def run_blind_review(
    sample_path: str | os.PathLike[str],
    manifest_path: str | os.PathLike[str],
    run_root: str | os.PathLike[str],
    *,
    claude_model: str,
    client: ReferenceClient,
) -> dict[str, object]:
    """Seal all 36 synthetic machine-blind references before any shadow stage."""
    _validate_exact_claude_model(claude_model)
    bundle = load_bundle(sample_path, manifest_path)
    _assert_synthetic_execution(bundle)
    root = Path(run_root)
    _create_run_root(root)
    root = root.resolve(strict=True)
    summary = _base_summary(bundle, root, claude_model)
    _atomic_summary(root, summary)
    failed = False
    with _secure_create_jsonl(root / "claude-reference.jsonl") as artifact:
        for company in bundle.companies:
            try:
                response = client(company.context, claude_model)
                status, raw_output, reason = _validate_scoring_response(company.context, response)
                attempts = response.attempts
            except Exception:  # transport details are deliberately not persisted
                status, raw_output, reason, attempts = "CALL_FAILED", None, "MODEL_CALL_FAILED", 1
            record = {
                "record_type": "machine_blind_reference",
                "code": company.sample.code,
                "input_hash": company.context.compute_input_hash(),
                "context_sha256": company.context_sha256,
                "model": claude_model,
                "attempts": attempts,
                "validation_status": status,
                "failure_reason": reason,
                "result": raw_output,
            }
            _write_jsonl(artifact, record)
            summary["reference_attempted"] = cast(int, summary["reference_attempted"]) + 1
            if status in VALID_MODEL_STATUSES:
                summary["reference_valid"] = cast(int, summary["reference_valid"]) + 1
            else:
                failed = True
                break
    summary["reference_artifact_sha256"] = _artifact_sha256(root / "claude-reference.jsonl")
    if failed:
        summary["state"] = "FAIL"
        summary["terminal_status"] = "FAIL"
        attempted = cast(int, summary["reference_attempted"])
        summary["not_attempted"] = [company.sample.code for company in bundle.companies[attempted:]]
    else:
        summary["state"] = "REFERENCE_COMPLETE"
    _atomic_summary(root, summary)
    if failed:
        _write_aggregate_report(root, summary, bundle)
    return summary


def _read_json_object(path: Path) -> dict[str, object]:
    return _load_json_bytes(_read_regular_file(path, maximum_bytes=MAX_JSON_BYTES), label=str(path))


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    raw = _read_regular_file(path, maximum_bytes=MAX_JSON_BYTES)
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"non-object JSONL record at {path}:{line_number}")
        records.append(cast(dict[str, object], value))
    return records


def _artifact_sha256(path: Path) -> str:
    return _sha256_bytes(_read_regular_file(path, maximum_bytes=MAX_JSON_BYTES))


def _verify_artifact_seal(summary: Mapping[str, object], key: str, path: Path, *, required: bool) -> None:
    expected = summary.get(key)
    exists = os.path.lexists(path)
    if expected is None and not exists and not required:
        return
    if not _is_sha256(expected) or not exists:
        raise ValueError(f"missing artifact seal: {path.name}")
    if _artifact_sha256(path) != expected:
        raise ValueError(f"sealed artifact hash drift: {path.name}")


def _load_run_bundle(run_root: Path, summary: Mapping[str, object]) -> LoadedBundle:
    recorded_root = summary.get("run_root")
    if not isinstance(recorded_root, str) or Path(recorded_root).resolve() != run_root.resolve():
        raise ValueError("run summary belongs to a different run root")
    bundle = load_bundle(cast(str, summary["sample_path"]), cast(str, summary["bundle_path"]))
    if bundle.sample_sha256 != summary["sample_sha256"] or bundle.bundle_sha256 != summary["bundle_sha256"]:
        raise ValueError("run input hash drift")
    return bundle


def _write_report_payload(run_root: Path, report: Mapping[str, object]) -> str:
    path = run_root / "aggregate-report.json"
    serialized = (_canonical_json(report) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)
    return _sha256_bytes(serialized)


def _artifact_hashes(summary: Mapping[str, object]) -> dict[str, object]:
    return {
        "claude_reference": summary.get("reference_artifact_sha256"),
        "gemini_shadow": summary.get("gemini_artifact_sha256"),
        "claude_support_audit": summary.get("support_artifact_sha256"),
    }


def _pre_call_failure_report(run_root: Path, summary: Mapping[str, object], failure_code: str) -> dict[str, object]:
    return {
        "report_version": "m5-report-v1",
        "run_root": str(run_root),
        "status": "FAIL",
        "sample_sha256": summary["sample_sha256"],
        "bundle_sha256": summary["bundle_sha256"],
        "claude_model": summary["claude_model"],
        "gemini_model": summary["gemini_model"],
        "artifact_sha256s": _artifact_hashes(summary),
        "failure_code": failure_code,
        "strict_gates": {},
        "selection_bias_risks": [],
        "limitations": [
            "input or artifact drift prevented evaluation",
            "no result authorizes M6, production cutover, or predictive-validity claims",
        ],
    }


def _finalize_pre_call_failure(run_root: Path, summary: dict[str, object], failure_code: str) -> None:
    """Make input/artifact drift terminal when a full report cannot be computed."""
    summary["state"] = "FAIL"
    summary["terminal_status"] = "FAIL"
    summary["failure_code"] = failure_code
    _atomic_summary(run_root, summary)
    if not os.path.lexists(run_root / "aggregate-report.json"):
        summary["aggregate_report_sha256"] = _write_report_payload(
            run_root, _pre_call_failure_report(run_root, summary, failure_code)
        )
        _atomic_summary(run_root, summary)


def _reference_index(run_root: Path, bundle: LoadedBundle) -> dict[str, dict[str, object]]:
    records = _read_jsonl(run_root / "claude-reference.jsonl")
    if len(records) != EXPECTED_COMPANY_COUNT:
        raise ValueError("all 36 machine-blind references must be sealed before Gemini")
    index: dict[str, dict[str, object]] = {}
    for record, company in zip(records, bundle.companies, strict=True):
        code = record.get("code")
        if code != company.sample.code or code in index:
            raise ValueError("reference order/company binding is invalid")
        if record.get("input_hash") != company.context.compute_input_hash():
            raise ValueError("reference input hash drift")
        if record.get("validation_status") not in VALID_MODEL_STATUSES:
            raise ValueError("all references must be valid before Gemini")
        result = record.get("result")
        if not isinstance(result, dict) or not validate_model_output(result, context=company.context).valid:
            raise ValueError("sealed reference failed local revalidation")
        index[cast(str, code)] = record
    return index


def _gemini_record(company: BundleCompany, response: GeminiCallResult, model: str) -> dict[str, object]:
    status = response.status.value
    raw_output = response.raw_output
    if response.attempts < 1 or response.attempts > 3:
        status = "CALL_BOUND_VIOLATION"
        raw_output = None
    elif status in VALID_MODEL_STATUSES:
        if raw_output is None or not validate_model_output(raw_output, context=company.context).valid:
            status = "OUTPUT_INVALID"
    return {
        "record_type": "gemini_shadow",
        "code": company.sample.code,
        "input_hash": company.context.compute_input_hash(),
        "context_sha256": company.context_sha256,
        "model": model,
        "attempts": response.attempts,
        "validation_status": status,
        "failure_reason": _bounded_reason(response.failure_reason, "LOCAL_VALIDATION_FAILED")
        if status not in VALID_MODEL_STATUSES
        else None,
        "result": raw_output,
    }


def _validate_support_output(
    company: BundleCompany, raw: Mapping[str, object] | None
) -> tuple[str, dict[str, object] | None, str | None]:
    if raw is None:
        return "CALL_FAILED", None, "SUPPORT_AUDIT_CALL_FAILED"
    value = dict(raw)
    required = {"schema_version", "code", "input_hash", "supported", "unsupported_facts"}
    if set(value) != required:
        return "OUTPUT_INVALID", value, "support audit has the wrong field set"
    facts = value["unsupported_facts"]
    if (
        value["schema_version"] != M5_SUPPORT_AUDIT_VERSION
        or value["code"] != company.sample.code
        or value["input_hash"] != company.context.compute_input_hash()
        or not isinstance(value["supported"], bool)
        or not isinstance(facts, list)
        or not all(isinstance(item, str) and item.strip() and len(item) <= 500 for item in facts)
        or value["supported"] != (len(facts) == 0)
    ):
        return "OUTPUT_INVALID", value, "support audit contract validation failed"
    return "VALID", value, None


def run_shadow(
    run_root: str | os.PathLike[str],
    *,
    gemini_client: GeminiClient,
    support_client: SupportClient,
) -> dict[str, object]:
    """Run serial synthetic Gemini shadow then revealed Claude support audit."""
    root = Path(run_root).resolve(strict=True)
    summary = _read_json_object(root / "run-summary.json")
    if summary.get("state") != "REFERENCE_COMPLETE" or summary.get("terminal_status") is not None:
        raise ValueError("run is not a fresh, complete reference stage and cannot be resumed")
    for artifact_name in ("gemini-shadow.jsonl", "claude-support-audit.jsonl", "aggregate-report.json"):
        if os.path.lexists(root / artifact_name):
            _finalize_pre_call_failure(root, summary, "UNEXPECTED_RUN_ARTIFACT")
            raise ValueError(f"unexpected pre-existing run artifact: {artifact_name}")
    try:
        bundle = _load_run_bundle(root, summary)
        _assert_synthetic_execution(bundle)
        _verify_artifact_seal(summary, "reference_artifact_sha256", root / "claude-reference.jsonl", required=True)
        _reference_index(root, bundle)
    except (OSError, ValueError):
        _finalize_pre_call_failure(root, summary, "INPUT_OR_REFERENCE_ARTIFACT_DRIFT")
        raise
    summary["state"] = "SHADOW_RUNNING"
    _atomic_summary(root, summary)

    gemini_failed = False
    with _secure_create_jsonl(root / "gemini-shadow.jsonl") as artifact:
        for company in bundle.companies:
            try:
                response = gemini_client(company.context, DEFAULT_GEMINI_MODEL)
            except Exception:
                response = GeminiCallResult(
                    status=ValidationStatus.API_SERVER_ERROR,
                    result=None,
                    raw_output=None,
                    failure_reason="MODEL_CALL_FAILED",
                    attempts=1,
                )
            record = _gemini_record(company, response, DEFAULT_GEMINI_MODEL)
            _write_jsonl(artifact, record)
            summary["gemini_attempted"] = cast(int, summary["gemini_attempted"]) + 1
            summary["gemini_http_attempts"] = cast(int, summary["gemini_http_attempts"]) + max(response.attempts, 0)
            if record["validation_status"] not in VALID_MODEL_STATUSES:
                gemini_failed = True
                break
    summary["gemini_artifact_sha256"] = _artifact_sha256(root / "gemini-shadow.jsonl")
    if gemini_failed:
        attempted = cast(int, summary["gemini_attempted"])
        summary["not_attempted"] = [company.sample.code for company in bundle.companies[attempted:]]
        summary["state"] = "FAIL"
        summary["terminal_status"] = "FAIL"
        _atomic_summary(root, summary)
        _write_aggregate_report(root, summary, bundle)
        return summary

    gemini_records = _read_jsonl(root / "gemini-shadow.jsonl")
    summary["state"] = "SUPPORT_RUNNING"
    _atomic_summary(root, summary)
    support_failed = False
    with _secure_create_jsonl(root / "claude-support-audit.jsonl") as artifact:
        for company, gemini_record in zip(bundle.companies, gemini_records, strict=True):
            gemini_result = gemini_record["result"]
            assert isinstance(gemini_result, dict)
            reason: str | None
            try:
                support_response = support_client(company.context, gemini_result, cast(str, summary["claude_model"]))
                if support_response.attempts != 1:
                    status, raw_output, reason = (
                        "CALL_BOUND_VIOLATION",
                        None,
                        "Claude support calls must have exactly one attempt",
                    )
                else:
                    status, raw_output, reason = _validate_support_output(company, support_response.raw_output)
                    if support_response.failure_reason and status != "VALID":
                        reason = _bounded_reason(support_response.failure_reason, cast(str, reason))
                attempts = support_response.attempts
            except Exception:
                status, raw_output, reason, attempts = "CALL_FAILED", None, "SUPPORT_AUDIT_CALL_FAILED", 1
            record = {
                "record_type": "revealed_evidence_support_audit",
                "code": company.sample.code,
                "input_hash": company.context.compute_input_hash(),
                "model": summary["claude_model"],
                "attempts": attempts,
                "validation_status": status,
                "failure_reason": reason,
                "result": raw_output,
            }
            _write_jsonl(artifact, record)
            summary["support_attempted"] = cast(int, summary["support_attempted"]) + 1
            if status != "VALID":
                support_failed = True
                break
    summary["support_artifact_sha256"] = _artifact_sha256(root / "claude-support-audit.jsonl")
    if support_failed:
        summary["state"] = "FAIL"
        summary["terminal_status"] = "FAIL"
    else:
        report = build_aggregate_report(root, summary, bundle)
        summary["state"] = report["status"]
        summary["terminal_status"] = report["status"]
    _atomic_summary(root, summary)
    _write_aggregate_report(root, summary, bundle)
    return summary


def _dimension_result(record: Mapping[str, object], dimension: str) -> Mapping[str, object] | None:
    result = record.get("result")
    if not isinstance(result, dict):
        return None
    dimensions = result.get("dimensions")
    if not isinstance(dimensions, dict):
        return None
    value = dimensions.get(dimension)
    return value if isinstance(value, dict) else None


def build_aggregate_report(run_root: Path, summary: Mapping[str, object], bundle: LoadedBundle) -> dict[str, object]:
    """Compute strict M5 gates, completeness, and stratified selection-risk flags."""
    reference_records = _read_jsonl(run_root / "claude-reference.jsonl")
    gemini_path = run_root / "gemini-shadow.jsonl"
    support_path = run_root / "claude-support-audit.jsonl"
    gemini_records = _read_jsonl(gemini_path) if gemini_path.exists() else []
    support_records = _read_jsonl(support_path) if support_path.exists() else []
    ref_by_code = {cast(str, record["code"]): record for record in reference_records}
    gemini_by_code = {cast(str, record["code"]): record for record in gemini_records}

    schema_total = len(reference_records) + len(gemini_records)
    schema_valid = sum(record.get("validation_status") in VALID_MODEL_STATUSES for record in reference_records)
    schema_valid += sum(record.get("validation_status") in VALID_MODEL_STATUSES for record in gemini_records)
    unsupported_facts = 0
    for record in support_records:
        result = record.get("result")
        if isinstance(result, dict) and isinstance(result.get("unsupported_facts"), list):
            unsupported_facts += len(result["unsupported_facts"])

    dimensions_report: dict[str, object] = {}
    all_complete = True
    all_agreement = True
    all_fail_closed = True
    selection_risks: list[dict[str, object]] = []
    for dimension in DIMENSION_NAMES:
        reference_scored = 0
        agreement_count = 0
        reference_insufficient = 0
        fail_closed_count = 0
        reference_scored_super: set[str] = set()
        scored_by_super: Counter[str] = Counter()
        total_by_super: Counter[str] = Counter()
        scored_by_cap: Counter[str] = Counter()
        total_by_cap: Counter[str] = Counter()
        gemini_scored_total = 0
        for company in bundle.companies:
            code = company.sample.code
            ref_dimension = _dimension_result(ref_by_code.get(code, {}), dimension)
            gemini_dimension = _dimension_result(gemini_by_code.get(code, {}), dimension)
            total_by_super[company.sample.super_stratum] += 1
            total_by_cap[company.sample.market_cap_stratum] += 1
            if gemini_dimension is not None and gemini_dimension.get("status") == "scored":
                gemini_scored_total += 1
                scored_by_super[company.sample.super_stratum] += 1
                scored_by_cap[company.sample.market_cap_stratum] += 1
            if ref_dimension is None:
                continue
            if ref_dimension.get("status") == "scored":
                reference_scored += 1
                reference_scored_super.add(company.sample.super_stratum)
                ref_score = ref_dimension.get("score")
                gemini_score = gemini_dimension.get("score") if gemini_dimension is not None else None
                if isinstance(ref_score, int) and isinstance(gemini_score, int) and abs(ref_score - gemini_score) <= 1:
                    agreement_count += 1
            elif ref_dimension.get("status") == "insufficient_data":
                reference_insufficient += 1
                if gemini_dimension is not None and gemini_dimension.get("status") == "insufficient_data":
                    fail_closed_count += 1
        required_agreement = math.ceil(0.9 * reference_scored)
        agreement_pass = agreement_count >= required_agreement
        fail_closed_pass = fail_closed_count == reference_insufficient
        scored_super_coverage = [name for name in SUPER_STRATA if name in reference_scored_super]
        complete = reference_scored >= 12 and len(scored_super_coverage) == len(SUPER_STRATA)
        all_complete = all_complete and complete
        all_agreement = all_agreement and agreement_pass
        all_fail_closed = all_fail_closed and fail_closed_pass
        overall_ratio = gemini_scored_total / EXPECTED_COMPANY_COUNT
        strata: dict[str, object] = {"super_stratum": {}, "market_cap_stratum": {}}
        for layer, scored, total in (
            *((name, scored_by_super[name], total_by_super[name]) for name in SUPER_STRATA),
            *((name, scored_by_cap[name], total_by_cap[name]) for name in CAP_STRATA),
        ):
            ratio = scored / total
            target = "market_cap_stratum" if layer in CAP_STRATA else "super_stratum"
            cast(dict[str, object], strata[target])[layer] = {
                "scored": scored,
                "insufficient_data": total - scored,
                "scored_ratio": ratio,
            }
            if overall_ratio - ratio > 0.20:
                selection_risks.append(
                    {"dimension": dimension, "layer_type": target, "layer": layer, "gap": overall_ratio - ratio}
                )
        dimensions_report[dimension] = {
            "reference_scored": reference_scored,
            "reference_insufficient_data": reference_insufficient,
            "agreement_within_one": agreement_count,
            "agreement_required": required_agreement,
            "blind_reference_agreement_pass": agreement_pass,
            "fail_closed_count": fail_closed_count,
            "fail_closed_pass": fail_closed_pass,
            "six_super_strata_covered": len(scored_super_coverage) == len(SUPER_STRATA),
            "non_provisional_sample_complete": complete,
            "gemini_scored_ratio": overall_ratio,
            "strata": strata,
        }

    all_calls_complete = (
        len(reference_records) == EXPECTED_COMPANY_COUNT
        and len(gemini_records) == EXPECTED_COMPANY_COUNT
        and len(support_records) == EXPECTED_COMPANY_COUNT
        and summary.get("support_attempted") == EXPECTED_COMPANY_COUNT
    )
    strict_gates = {
        "schema_validity_100_percent": schema_total == 72 and schema_valid == schema_total,
        "unknown_evidence_ids_accepted_zero": schema_valid == schema_total,
        "unsupported_fact_count_zero": unsupported_facts == 0,
        "reference_insufficient_fail_closed_100_percent": all_fail_closed,
        "blind_reference_agreement_90_percent_integer_gate": all_agreement,
    }
    if summary.get("terminal_status") == "FAIL" or not all_calls_complete or not all(strict_gates.values()):
        status = "FAIL"
    elif all_complete:
        status = "PASS"
    else:
        status = "PROVISIONAL"
    return {
        "report_version": "m5-report-v1",
        "run_root": str(run_root),
        "status": status,
        "sample_sha256": bundle.sample_sha256,
        "bundle_sha256": bundle.bundle_sha256,
        "claude_model": summary["claude_model"],
        "gemini_model": summary["gemini_model"],
        "artifact_sha256s": _artifact_hashes(summary),
        "strict_gates": strict_gates,
        "schema_valid": schema_valid,
        "schema_total": schema_total,
        "unsupported_fact_count": unsupported_facts,
        "dimensions": dimensions_report,
        "selection_bias_risks": selection_risks,
        "limitations": [
            "machine_blind_reference is a single Claude reviewer, not human review or independent investment judgment",
            "Claude and Gemini may share model bias",
            "no result authorizes M6, production cutover, or predictive-validity claims",
        ],
    }


def _write_aggregate_report(run_root: Path, summary: dict[str, object], bundle: LoadedBundle) -> dict[str, object]:
    report = build_aggregate_report(run_root, summary, bundle)
    summary["aggregate_report_sha256"] = _write_report_payload(run_root, report)
    _atomic_summary(run_root, summary)
    return report


def load_report(run_root: str | os.PathLike[str]) -> dict[str, object]:
    """Verify a report's run binding, artifact seals, digest, and recomputed content."""
    root = Path(run_root).resolve(strict=True)
    summary = _read_json_object(root / "run-summary.json")
    bundle = _load_run_bundle(root, summary)
    _verify_artifact_seal(summary, "reference_artifact_sha256", root / "claude-reference.jsonl", required=True)
    _verify_artifact_seal(summary, "gemini_artifact_sha256", root / "gemini-shadow.jsonl", required=False)
    _verify_artifact_seal(summary, "support_artifact_sha256", root / "claude-support-audit.jsonl", required=False)
    report_path = root / "aggregate-report.json"
    expected_report_hash = summary.get("aggregate_report_sha256")
    if not _is_sha256(expected_report_hash) or _artifact_sha256(report_path) != expected_report_hash:
        raise ValueError("aggregate report hash drift")
    report = _read_json_object(report_path)
    failure_code = summary.get("failure_code")
    if isinstance(failure_code, str):
        expected = _pre_call_failure_report(root, summary, failure_code)
    else:
        expected = build_aggregate_report(root, summary, bundle)
    if report != expected:
        raise ValueError("aggregate report does not match its sealed run artifacts")
    if summary.get("state") != report.get("status") or summary.get("terminal_status") != report.get("status"):
        raise ValueError("run summary terminal state does not match the aggregate report")
    return report


def _insufficient_output(context: QualitativeContext) -> dict[str, object]:
    dimension: dict[str, object] = {
        "status": "insufficient_data",
        "score": None,
        "confidence": "low",
        "evidence_ids": [],
        "rationale": "The synthetic fixture supplies no qualifying evidence for this dimension.",
    }
    return {
        "schema_version": context.schema_version,
        "overall_status": "insufficient_data",
        "as_of_date": context.as_of_date,
        "dimensions": {name: dict(dimension) for name in DIMENSION_NAMES},
    }


def synthetic_reference_client(context: QualitativeContext, model: str) -> ModelResponse:
    """Deterministic credential-free Claude fake for synthetic fixture runs."""
    _validate_exact_claude_model(model)
    return ModelResponse(raw_output=_insufficient_output(context))


def synthetic_gemini_client(context: QualitativeContext, model: str) -> GeminiCallResult:
    """Deterministic credential-free Gemini fake for synthetic fixture runs."""
    if model != DEFAULT_GEMINI_MODEL:
        raise ValueError("M5 Gemini model is fixed")
    raw = _insufficient_output(context)
    validation = validate_model_output(raw, context=context)
    assert validation.valid and validation.result is not None
    return GeminiCallResult(
        status=ValidationStatus.VALID_INSUFFICIENT_DATA,
        result=validation.result,
        raw_output=raw,
        failure_reason=None,
        attempts=1,
    )


def synthetic_support_client(
    context: QualitativeContext, gemini_output: Mapping[str, object], model: str
) -> SupportAuditResponse:
    """Deterministic credential-free revealed support-audit fake."""
    _validate_exact_claude_model(model)
    if not validate_model_output(gemini_output, context=context).valid:
        return SupportAuditResponse(raw_output=None, failure_reason="INVALID_GEMINI_OUTPUT")
    return SupportAuditResponse(
        raw_output={
            "schema_version": M5_SUPPORT_AUDIT_VERSION,
            "code": context.code,
            "input_hash": context.compute_input_hash(),
            "supported": True,
            "unsupported_facts": [],
        }
    )


__all__ = [
    "EXPECTED_SAMPLE_SHA256",
    "LoadedBundle",
    "ModelResponse",
    "SupportAuditResponse",
    "build_aggregate_report",
    "load_bundle",
    "load_report",
    "preview_bundle",
    "run_blind_review",
    "run_shadow",
    "synthetic_gemini_client",
    "synthetic_reference_client",
    "synthetic_support_client",
]
