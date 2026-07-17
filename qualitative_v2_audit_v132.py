"""Deterministic, offline primitives for the MILESTONE-004 evidence audit."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo


PROTOCOL_ID = "qualitative-v2-m4-prereg-v1.1"
PROTOCOL_VERSION = "1.1.0"
SAMPLING_SEED = "qualitative-v2-m4-prereg-v1"
SOURCES = ("CNINFO", "SSE", "SZSE", "CNIPA")
QUERIES = ("护城河", "壁垒", "专利", "特许经营", "独占", "授权", "核心技术")
Z_95 = 1.959963984540054

# The suffix is part of the official SW2021 identifier and is deliberately kept.
SW2021_INDUSTRIES: Mapping[str, str] = {
    "801010.SI": "农林牧渔",
    "801030.SI": "基础化工",
    "801040.SI": "钢铁",
    "801050.SI": "有色金属",
    "801080.SI": "电子",
    "801110.SI": "家用电器",
    "801120.SI": "食品饮料",
    "801130.SI": "纺织服饰",
    "801140.SI": "轻工制造",
    "801150.SI": "医药生物",
    "801160.SI": "公用事业",
    "801170.SI": "交通运输",
    "801180.SI": "房地产",
    "801200.SI": "商贸零售",
    "801210.SI": "社会服务",
    "801230.SI": "综合",
    "801710.SI": "建筑材料",
    "801720.SI": "建筑装饰",
    "801730.SI": "电力设备",
    "801740.SI": "国防军工",
    "801750.SI": "计算机",
    "801760.SI": "传媒",
    "801770.SI": "通信",
    "801780.SI": "银行",
    "801790.SI": "非银金融",
    "801880.SI": "汽车",
    "801890.SI": "机械设备",
    "801950.SI": "煤炭",
    "801960.SI": "石油石化",
    "801970.SI": "环保",
    "801980.SI": "美容护理",
}

SUPER_STRATA: Mapping[str, tuple[str, ...]] = {
    "金融地产": ("银行", "非银金融", "房地产"),
    "能源材料": ("石油石化", "煤炭", "有色金属", "钢铁", "基础化工"),
    "工业基础设施": ("建筑装饰", "建筑材料", "电力设备", "机械设备", "国防军工", "交通运输", "综合"),
    "科技通信": ("电子", "计算机", "传媒", "通信"),
    "消费": ("汽车", "家用电器", "轻工制造", "纺织服饰", "商贸零售", "社会服务", "食品饮料", "美容护理", "农林牧渔"),
    "医疗公用事业": ("医药生物", "公用事业", "环保"),
}
INDUSTRY_TO_SUPER = {industry: group for group, industries in SUPER_STRATA.items() for industry in industries}


class AttemptStatus(StrEnum):
    SUCCESS = "success"
    TECHNICAL_ERROR = "technical_error"
    PENDING_RETRY = "pending_retry"
    BLOCKED_TECHNICAL_ERROR = "blocked_technical_error"


class Decision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class AuditError(RuntimeError):
    """Base class for fail-closed audit errors."""


class AuditBlockedError(AuditError):
    """Raised when a protocol gate prevents downstream work."""


class FrameValidationError(AuditBlockedError):
    """Raised when the sampling frame violates its frozen contract."""


class HashDriftError(AuditBlockedError):
    """Raised when frozen bytes no longer match their manifest."""


class FrozenArtifactError(AuditBlockedError):
    """Raised when create-only state would be overwritten."""


class IsolationViolationError(AuditBlockedError):
    """Raised when independent reviewer boundaries overlap."""


class UnadjudicatedDisagreementError(AuditBlockedError):
    """Raised when review disagreements are not fully adjudicated."""


class TechnicalAttemptError(AuditBlockedError):
    """Raised when collection attempts are invalid or unresolved."""


def reject_v132_segmented_attempt(attempt_dir: str | Path) -> None:
    """Keep every legacy assembler/resume path closed to v1.3.2 attempts."""
    path = Path(attempt_dir)
    manifest = path / "attempt-manifest.json"
    version_root = ("artifacts", "milestone-004", "v1.3.2")
    parts = path.parts
    under_version_root = any(tuple(parts[index : index + 3]) == version_root for index in range(max(0, len(parts) - 2)))
    if manifest.exists() or manifest.is_symlink() or under_version_root:
        raise AuditBlockedError("v1.3.2 segmented attempt requires require_capture_input_eligible()")


@dataclass(frozen=True, slots=True)
class PreregistrationRef:
    protocol_id: str
    protocol_version: str
    protocol_path: str
    protocol_sha256: str
    hash_manifest_path: str
    seed: str = SAMPLING_SEED
    supersedes_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class FrameRow:
    ts_code: str
    name: str
    industry_code: str
    industry_name: str
    total_mv: Decimal
    trade_date: date | None = None
    exchange: str = ""


@dataclass(frozen=True, slots=True)
class StratifiedFrameRow:
    row: FrameRow
    super_stratum: str
    market_cap_stratum: str


@dataclass(frozen=True, slots=True)
class FrameValidationResult:
    rows: tuple[StratifiedFrameRow, ...]
    sampling_date: date
    prereg: PreregistrationRef


@dataclass(frozen=True, slots=True)
class PassOverRecord:
    rejected_ts_code: str
    failed_predicate: str
    evidence_sha256: str
    detected_at: datetime
    replacement_ts_code: str | None = None
    super_stratum: str | None = None
    market_cap_stratum: str | None = None
    sampling_hash: str | None = None


@dataclass(frozen=True, slots=True)
class SampleEntry:
    ts_code: str
    name: str
    industry_code: str
    industry_name: str
    super_stratum: str
    market_cap_stratum: str
    total_mv: Decimal
    sampling_hash: str


@dataclass(frozen=True, slots=True)
class SampleManifest:
    protocol_sha256: str
    sampling_date: date
    seed: str
    entries: tuple[SampleEntry, ...]
    pass_overs: tuple[PassOverRecord, ...] = ()
    supersedes: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    ts_code: str
    source: str
    query: str
    rank: int
    title: str
    url: str
    document_number: str | None = None
    raw_sha256: str | None = None
    publication_date: date | None = None
    default_order: str = "official_default"


@dataclass(frozen=True, slots=True)
class BlobRef:
    relative_path: str
    sha256: str
    byte_count: int
    mime_type: str
    detected_mime_type: str | None = None


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    operation_id: str
    attempted_at: datetime
    status: str
    sanitized_error_class: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateDocument:
    ts_code: str
    source: str
    query: str
    result_rank: int
    round_robin_ordinal: int
    title: str
    canonical_url: str
    blob: BlobRef
    document_number: str | None = None
    duplicate_of: str | None = None
    duplicate_rule: str | None = None

    @property
    def document_sha256(self) -> str:
        return self.blob.sha256


@dataclass(frozen=True, slots=True)
class DuplicateRecord:
    ts_code: str
    source: str
    query: str
    result_rank: int
    round_robin_ordinal: int
    duplicate_of_sha256: str
    matching_rule: str


@dataclass(frozen=True, slots=True)
class RelationshipReport:
    """Bind one relationship-report blob to the company it evidences."""

    ts_code: str
    blob: BlobRef


@dataclass(frozen=True, slots=True)
class CandidateCorpus:
    protocol_sha256: str
    sample: SampleManifest
    documents: tuple[CandidateDocument, ...]
    relationship_reports: tuple[RelationshipReport, ...]
    attempts: tuple[AttemptRecord, ...]
    technical_status: str
    duplicates: tuple[DuplicateRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class CoverageRow:
    layer_type: str
    layer: str
    denominator: int
    scorable: int
    insufficient: int
    proportion: float
    passed: bool
    wilson_lower: float
    wilson_upper: float
    wilson_lower_display: str
    wilson_upper_display: str
    required_action: str
    scope_disposition: str


@dataclass(frozen=True, slots=True)
class CoverageReport:
    rows: tuple[CoverageRow, ...]
    overall_passed: bool
    milestone_005_approval_blocked: bool
    corpus_manifest_sha256: str
    review_index_sha256: str
    reviewer_seal_sha256s: tuple[str, str]
    adjudication_sha256: str


_LINEAGE_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class ValidatedAuditLineage:
    """Opaque proof that coverage inputs passed the frozen review lineage gates."""

    corpus: CandidateCorpus
    corpus_manifest_sha256: str
    review_index_sha256: str
    reviewer_seal_sha256s: tuple[str, str]
    adjudication_sha256: str
    _final_labels_json: bytes
    _adjudication_json: bytes

    def __init__(
        self,
        corpus: CandidateCorpus,
        corpus_manifest_sha256: str,
        review_index_sha256: str,
        reviewer_seal_sha256s: tuple[str, str],
        adjudication_sha256: str,
        final_labels_json: bytes,
        adjudication_json: bytes,
        *,
        _token: object,
    ) -> None:
        if _token is not _LINEAGE_TOKEN:
            raise AuditBlockedError("validated audit lineage must be created by the review validation factory")
        object.__setattr__(self, "corpus", corpus)
        object.__setattr__(self, "corpus_manifest_sha256", corpus_manifest_sha256)
        object.__setattr__(self, "review_index_sha256", review_index_sha256)
        object.__setattr__(self, "reviewer_seal_sha256s", reviewer_seal_sha256s)
        object.__setattr__(self, "adjudication_sha256", adjudication_sha256)
        object.__setattr__(self, "_final_labels_json", final_labels_json)
        object.__setattr__(self, "_adjudication_json", adjudication_json)

    @property
    def final_labels(self) -> Mapping[str, Any]:
        """Return a detached copy of the validated company outcomes."""
        value = json.loads(self._final_labels_json)
        if not isinstance(value, dict):
            raise AuditBlockedError("validated final labels are not an object")
        return value

    @property
    def adjudication(self) -> Mapping[str, Any]:
        """Return a detached copy of the validated adjudication artifact."""
        value = json.loads(self._adjudication_json)
        if not isinstance(value, dict):
            raise AuditBlockedError("validated adjudication is not an object")
        return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    """Serialize protocol JSON as canonical UTF-8 with one trailing LF."""

    def convert(item: object) -> object:
        if is_dataclass(item) and not isinstance(item, type):
            return {field.name: convert(getattr(item, field.name)) for field in fields(item)}
        if isinstance(item, Mapping):
            return {str(key): convert(val) for key, val in item.items()}
        if isinstance(item, (tuple, list)):
            return [convert(val) for val in item]
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, Decimal):
            if not item.is_finite():
                raise ValueError("non-finite Decimal is forbidden")
            return str(item)
        if isinstance(item, Path):
            return str(item)
        if isinstance(item, StrEnum):
            return item.value
        return item

    return (
        json.dumps(convert(value), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _create_validated_audit_lineage(
    corpus: CandidateCorpus,
    final_labels: Mapping[str, bool | str | Mapping[str, Any]],
    adjudication: Mapping[str, Any],
    *,
    corpus_manifest_sha256: str,
    review_index_sha256: str,
    reviewer_seal_sha256s: tuple[str, str],
) -> ValidatedAuditLineage:
    """Create coverage inputs after the review module validates the full lineage."""
    if corpus.technical_status != "complete" or _attempt_status(corpus.attempts) != "complete":
        raise TechnicalAttemptError("coverage lineage requires a complete technical ledger")
    if corpus.protocol_sha256 != corpus.sample.protocol_sha256:
        raise HashDriftError("corpus and sample protocol hashes differ")
    sample_codes = {entry.ts_code for entry in corpus.sample.entries}
    if len(corpus.sample.entries) != 36 or len(sample_codes) != 36:
        raise AuditBlockedError("coverage lineage requires the complete 36-company sample")
    relationship_codes = [report.ts_code for report in corpus.relationship_reports]
    if len(relationship_codes) != len(set(relationship_codes)) or set(relationship_codes) != sample_codes:
        raise AuditBlockedError("coverage lineage requires one relationship report per sampled company")
    if any(
        not re.fullmatch(r"[0-9a-f]{64}", report.blob.sha256) or report.blob.byte_count <= 0
        for report in corpus.relationship_reports
    ):
        raise AuditBlockedError("coverage lineage contains an invalid relationship-report blob")
    if any(document.ts_code not in sample_codes for document in corpus.documents):
        raise AuditBlockedError("candidate corpus contains a document outside the frozen sample")
    if set(final_labels) != sample_codes:
        raise UnadjudicatedDisagreementError("final labels must cover exactly the frozen sample")
    if any(type(value) is not bool for value in final_labels.values()):
        raise UnadjudicatedDisagreementError("validated company outcomes must be booleans")
    if len(reviewer_seal_sha256s) != 2:
        raise AuditBlockedError("coverage lineage requires exactly two reviewer seals")
    hashes = (corpus_manifest_sha256, review_index_sha256, *reviewer_seal_sha256s)
    if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes):
        raise HashDriftError("coverage lineage contains an invalid frozen-artifact hash")
    if adjudication.get("parent_review_index_sha256") != review_index_sha256:
        raise UnadjudicatedDisagreementError("adjudication does not reference the validated review index")
    if not isinstance(adjudication.get("decisions"), list):
        raise UnadjudicatedDisagreementError("validated adjudication decisions must be an array")
    final_labels_json = canonical_json_bytes(final_labels)
    adjudication_json = canonical_json_bytes(adjudication)
    return ValidatedAuditLineage(
        corpus,
        corpus_manifest_sha256,
        review_index_sha256,
        reviewer_seal_sha256s,
        _sha256_bytes(adjudication_json),
        final_labels_json,
        adjudication_json,
        _token=_LINEAGE_TOKEN,
    )


def load_preregistration_ref(protocol_path: str | Path, hash_manifest_path: str | Path) -> PreregistrationRef:
    """Load and verify one protocol against an unambiguous SHA-256 manifest."""
    protocol = Path(protocol_path)
    manifest = Path(hash_manifest_path)
    raw = protocol.read_bytes()
    digest = _sha256_bytes(raw)
    matching: list[str] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})\s+\*?(.+)", line.strip())
        if match and (match.group(2) == str(protocol) or Path(match.group(2)).name == protocol.name):
            matching.append(match.group(1))
    if matching != [digest]:
        raise HashDriftError(f"protocol hash manifest does not uniquely match {protocol}")
    text = raw.decode("utf-8")
    protocol_id_match = re.search(r"^Protocol ID:\s*`([^`]+)`", text, re.MULTILINE)
    version_match = re.search(r"^Protocol version:\s*`([^`]+)`", text, re.MULTILINE)
    if not protocol_id_match or not version_match:
        raise AuditError("protocol header is incomplete")
    seed_match = re.search(r"^\| Seed \| `([^`]+)`", text, re.MULTILINE)
    supersedes_match = re.search(r"^Supersedes SHA-256:\s*`([0-9a-f]{64})`", text, re.MULTILINE)
    return PreregistrationRef(
        protocol_id=protocol_id_match.group(1),
        protocol_version=version_match.group(1),
        protocol_path=str(protocol),
        protocol_sha256=digest,
        hash_manifest_path=str(manifest),
        seed=seed_match.group(1) if seed_match else SAMPLING_SEED,
        supersedes_sha256=supersedes_match.group(1) if supersedes_match else None,
    )


def _validate_frozen_mapping() -> None:
    if len(SW2021_INDUSTRIES) != 31 or len(set(SW2021_INDUSTRIES.values())) != 31:
        raise FrameValidationError("the SW2021 code/name mapping must contain 31 unique pairs")
    flattened = [item for values in SUPER_STRATA.values() for item in values]
    if len(flattened) != 31 or set(flattened) != set(SW2021_INDUSTRIES.values()):
        raise FrameValidationError("the six super-strata must partition all 31 industries")


def validate_frame(rows: Iterable[FrameRow], sampling_date: date, prereg: PreregistrationRef) -> FrameValidationResult:
    """Validate and deterministically assign every frame row to a market-cap half."""
    _validate_frozen_mapping()
    materialized = tuple(rows)
    seen_codes: set[str] = set()
    by_super: dict[str, list[FrameRow]] = {name: [] for name in SUPER_STRATA}
    for row in materialized:
        if row.ts_code in seen_codes:
            raise FrameValidationError(f"duplicate ts_code: {row.ts_code}")
        seen_codes.add(row.ts_code)
        if not re.fullmatch(r"\d{6}\.(SH|SZ)", row.ts_code):
            raise FrameValidationError(f"invalid A-share ts_code: {row.ts_code}")
        expected_name = SW2021_INDUSTRIES.get(row.industry_code)
        if expected_name is None or expected_name != row.industry_name:
            raise FrameValidationError(f"unknown or crossed industry mapping: {row.industry_code}/{row.industry_name}")
        if not isinstance(row.total_mv, Decimal) or not row.total_mv.is_finite() or row.total_mv <= 0:
            raise FrameValidationError(f"invalid unrounded total_mv for {row.ts_code}")
        if row.trade_date is not None and row.trade_date != sampling_date:
            raise FrameValidationError(f"trade_date drift for {row.ts_code}")
        if row.exchange and row.exchange != ("SSE" if row.ts_code.endswith(".SH") else "SZSE"):
            raise FrameValidationError(f"exchange mismatch for {row.ts_code}")
        by_super[INDUSTRY_TO_SUPER[row.industry_name]].append(row)
    stratified: list[StratifiedFrameRow] = []
    for super_name, group_rows in by_super.items():
        ordered = sorted(group_rows, key=lambda item: (item.total_mv, item.ts_code))
        low_count = (len(ordered) + 1) // 2
        for index, row in enumerate(ordered):
            stratified.append(StratifiedFrameRow(row, super_name, "low" if index < low_count else "high"))
    return FrameValidationResult(tuple(sorted(stratified, key=lambda item: item.row.ts_code)), sampling_date, prereg)


def _sampling_hash(seed: str, ts_code: str) -> str:
    return _sha256_bytes((seed + "\x1f" + ts_code).encode("utf-8"))


def select_sample(frame: FrameValidationResult, pass_overs: Iterable[PassOverRecord] = ()) -> SampleManifest:
    """Select exactly three hash-ranked companies from each of twelve frozen cells."""
    pass_over_records = tuple(pass_overs)
    rejected = {item.rejected_ts_code for item in pass_over_records}
    if len(rejected) != len(pass_over_records):
        raise FrameValidationError("a company may be passed over only once")
    frame_codes = {item.row.ts_code for item in frame.rows}
    if not rejected <= frame_codes:
        raise FrameValidationError("pass-over references a company outside the frame")
    entries: list[SampleEntry] = []
    for super_name in SUPER_STRATA:
        for cap in ("low", "high"):
            cell = [item for item in frame.rows if item.super_stratum == super_name and item.market_cap_stratum == cap]
            ordered = sorted(
                cell, key=lambda item: (_sampling_hash(frame.prereg.seed, item.row.ts_code), item.row.ts_code)
            )
            cell_codes = {item.row.ts_code for item in cell}
            cell_records = [item for item in pass_over_records if item.rejected_ts_code in cell_codes]
            original_top = {item.row.ts_code for item in ordered[:3]}
            for record in cell_records:
                if record.rejected_ts_code not in original_top:
                    raise FrameValidationError("only an originally selected row may be passed over")
                if not record.failed_predicate or not re.fullmatch(r"[0-9a-f]{64}", record.evidence_sha256):
                    raise FrameValidationError("pass-over requires a failed predicate and evidence SHA-256")
                expected_hash = _sampling_hash(frame.prereg.seed, record.rejected_ts_code)
                if record.sampling_hash is not None and record.sampling_hash != expected_hash:
                    raise FrameValidationError("pass-over sampling hash drift")
                if record.super_stratum is not None and record.super_stratum != super_name:
                    raise FrameValidationError("pass-over super-stratum drift")
                if record.market_cap_stratum is not None and record.market_cap_stratum != cap:
                    raise FrameValidationError("pass-over market-cap stratum drift")
            selected = [item for item in ordered if item.row.ts_code not in rejected][:3]
            if len(selected) != 3:
                raise FrameValidationError(f"sampling cell {super_name}/{cap} contains fewer than three eligible rows")
            selected_codes = {item.row.ts_code for item in selected}
            for record in cell_records:
                if record.replacement_ts_code is None or record.replacement_ts_code not in selected_codes:
                    raise FrameValidationError("pass-over must record the deterministic replacement")
            for item in selected:
                row = item.row
                entries.append(
                    SampleEntry(
                        row.ts_code,
                        row.name,
                        row.industry_code,
                        row.industry_name,
                        super_name,
                        cap,
                        row.total_mv,
                        _sampling_hash(frame.prereg.seed, row.ts_code),
                    )
                )
    if len(entries) != 36 or len({item.ts_code for item in entries}) != 36:
        raise FrameValidationError("the frozen sample must contain 36 unique companies")
    for record in pass_over_records:
        if record.replacement_ts_code and record.replacement_ts_code not in {item.ts_code for item in entries}:
            raise FrameValidationError("documented pass-over replacement was not selected")
    return SampleManifest(
        protocol_sha256=frame.prereg.protocol_sha256,
        sampling_date=frame.sampling_date,
        seed=frame.prereg.seed,
        entries=tuple(entries),
        pass_overs=pass_over_records,
    )


_OFFICIAL_HOSTS: Mapping[str, tuple[str, ...]] = {
    "CNINFO": ("cninfo.com.cn",),
    "SSE": ("sse.com.cn",),
    "SZSE": ("szse.cn",),
    "EXCHANGE": ("sse.com.cn", "szse.cn"),
    "CNIPA": ("cnipa.gov.cn", "cponline.cnipa.gov.cn"),
}
_TRACKING_KEYS = {"spm", "timestamp", "_"}


def _normalize_path(path: str) -> str:
    segments: list[str] = []
    for raw_segment in path.split("/"):
        decoded = unquote(raw_segment)
        if decoded == ".":
            continue
        if decoded == "..":
            if segments:
                segments.pop()
            continue
        # Preserve path case while ensuring a stable uppercase percent encoding.
        segments.append(quote(decoded, safe="!$&'()*+,;=:@-._~"))
    normalized = "/".join(segments)
    if path.startswith("/") and not normalized.startswith("/"):
        normalized = "/" + normalized
    if not normalized:
        normalized = "/"
    if normalized != "/":
        normalized = normalized.rstrip("/")
    return normalized


def normalize_url(source: str, url: str) -> str:
    """Normalize an official URL without destroying source-specific identifiers."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise AuditBlockedError("only absolute HTTP(S) official URLs are allowed")
    source_name = source.upper()
    allowed = _OFFICIAL_HOSTS.get(source_name)
    if not allowed:
        raise AuditBlockedError(f"unknown official source: {source}")
    host = parsed.hostname.lower().rstrip(".")
    if not any(host == root or host.endswith("." + root) for root in allowed):
        raise AuditBlockedError(f"host is not official for {source}: {host}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise AuditBlockedError("invalid URL port") from exc
    if port in (None, 80, 443):
        netloc = host
    else:
        netloc = f"{host}:{port}"
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_KEYS and not key.lower().startswith("utm_")
    ]
    query = urlencode(sorted(query_items), doseq=True, quote_via=quote)
    return urlunsplit(("https", netloc, _normalize_path(parsed.path), query, ""))


def _attempt_status(attempts: Sequence[AttemptRecord]) -> str:
    grouped: dict[str, list[AttemptRecord]] = {}
    for attempt in attempts:
        grouped.setdefault(attempt.operation_id, []).append(attempt)
    has_pending = False
    has_blocked = False
    for operation_id, records in grouped.items():
        ordered = sorted(records, key=lambda item: item.attempted_at)
        if any(item.attempted_at.tzinfo is None for item in ordered):
            raise TechnicalAttemptError(f"attempt timestamps must be timezone-aware for {operation_id}")
        if any(item.status not in {AttemptStatus.SUCCESS, AttemptStatus.TECHNICAL_ERROR} for item in ordered):
            raise TechnicalAttemptError(f"unknown attempt status for {operation_id}")
        if len(ordered) > 3:
            raise TechnicalAttemptError(f"more than three attempts for {operation_id}")
        shanghai = ZoneInfo("Asia/Shanghai")
        local_dates = [item.attempted_at.astimezone(shanghai).date() for item in ordered]
        if len(set(local_dates)) != len(local_dates):
            raise TechnicalAttemptError(f"attempt dates are not distinct for {operation_id}")
        if local_dates and local_dates != [local_dates[0] + timedelta(days=index) for index in range(len(local_dates))]:
            raise TechnicalAttemptError(f"attempts are not scheduled on D/D+1/D+2 for {operation_id}")
        successful = [index for index, item in enumerate(ordered) if item.status == AttemptStatus.SUCCESS]
        if len(successful) > 1 or (successful and successful[0] != len(ordered) - 1):
            raise TechnicalAttemptError(f"attempt ledger continues after success for {operation_id}")
        if successful:
            continue
        if len(ordered) < 3:
            has_pending = True
        else:
            has_blocked = True
    if has_blocked:
        return "blocked_technical_error"
    return "pending_retry" if has_pending else "complete"


def build_candidate_corpus(
    sample: SampleManifest,
    result_matrix: Mapping[tuple[str, str, str], Sequence[SearchResult]],
    blob_refs: Mapping[str, BlobRef],
    relationship_reports: Mapping[str, BlobRef],
    attempts: Sequence[AttemptRecord],
) -> CandidateCorpus:
    """Validate complete search matrices and select the deterministic candidate corpus."""
    results = tuple(item for values in result_matrix.values() for item in values)
    sample_codes = {entry.ts_code for entry in sample.entries}
    expected_matrix_keys = {
        (ts_code, source, query)
        for ts_code in sample_codes
        for source in (("CNINFO", "SSE", "CNIPA") if ts_code.endswith(".SH") else ("CNINFO", "SZSE", "CNIPA"))
        for query in QUERIES
    }
    if not all(isinstance(key, tuple) and len(key) == 3 for key in result_matrix):
        raise AuditBlockedError("search matrix keys must be (ts_code, source, query) tuples")
    if set(result_matrix) != expected_matrix_keys:
        raise AuditBlockedError("search matrix must explicitly contain every company/source/query group")
    by_key: dict[tuple[str, str, str], list[SearchResult]] = {}
    for result in results:
        expected_sources = {"CNINFO", "SSE", "CNIPA"} if result.ts_code.endswith(".SH") else {"CNINFO", "SZSE", "CNIPA"}
        if result.ts_code not in sample_codes or result.source not in expected_sources or result.query not in QUERIES:
            raise AuditBlockedError("search result lies outside the frozen sample/source/query matrix")
        if result.default_order != "official_default":
            raise AuditBlockedError("search result does not preserve the frozen official default order")
        if not 1 <= result.rank <= 20:
            raise AuditBlockedError("search result rank must be in 1..20")
        by_key.setdefault((result.ts_code, result.source, result.query), []).append(result)
    for ts_code in sample_codes:
        sources = ("CNINFO", "SSE", "CNIPA") if ts_code.endswith(".SH") else ("CNINFO", "SZSE", "CNIPA")
        for source in sources:
            for query in QUERIES:
                ranked = sorted(by_key.get((ts_code, source, query), []), key=lambda item: item.rank)
                if [item.rank for item in ranked] != list(range(1, len(ranked) + 1)):
                    raise AuditBlockedError(f"non-contiguous official ranking for {ts_code}/{source}/{query}")
    documents: list[CandidateDocument] = []
    duplicates: list[DuplicateRecord] = []
    for ts_code in sorted(sample_codes):
        seen_numbers: dict[str, CandidateDocument] = {}
        seen_urls: dict[str, CandidateDocument] = {}
        seen_hashes: dict[str, CandidateDocument] = {}
        ordinal = 0
        chosen = 0
        for rank in range(1, 21):
            sources = ("CNINFO", "SSE", "CNIPA") if ts_code.endswith(".SH") else ("CNINFO", "SZSE", "CNIPA")
            for source in sources:
                for query in QUERIES:
                    matches = [item for item in by_key.get((ts_code, source, query), ()) if item.rank == rank]
                    if not matches:
                        continue
                    result = matches[0]
                    ordinal += 1
                    normalized = normalize_url(source, result.url)
                    duplicate: CandidateDocument | None = None
                    rule: str | None = None
                    if result.document_number and result.document_number in seen_numbers:
                        duplicate, rule = seen_numbers[result.document_number], "document_number"
                    elif normalized in seen_urls:
                        duplicate, rule = seen_urls[normalized], "canonical_url"
                    blob = blob_refs.get(result.raw_sha256 or normalized)
                    if duplicate is None:
                        if blob is None:
                            raise AuditBlockedError(f"missing blob reference for selected result {ts_code}/{ordinal}")
                        if not re.fullmatch(r"[0-9a-f]{64}", blob.sha256) or blob.byte_count <= 0:
                            raise AuditBlockedError(f"invalid authoritative blob reference for {ts_code}/{ordinal}")
                        if result.raw_sha256 is not None and result.raw_sha256 != blob.sha256:
                            raise HashDriftError(f"result/blob hash drift for {ts_code}/{ordinal}")
                        if blob.sha256 in seen_hashes:
                            duplicate, rule = seen_hashes[blob.sha256], "raw_sha256"
                    if duplicate is not None:
                        duplicates.append(
                            DuplicateRecord(
                                ts_code,
                                source,
                                query,
                                rank,
                                ordinal,
                                duplicate.document_sha256,
                                rule or "unknown",
                            )
                        )
                        continue
                    assert blob is not None
                    document = CandidateDocument(
                        ts_code=ts_code,
                        source=source,
                        query=query,
                        result_rank=rank,
                        round_robin_ordinal=ordinal,
                        title=result.title,
                        canonical_url=normalized,
                        blob=blob,
                        document_number=result.document_number,
                    )
                    documents.append(document)
                    chosen += 1
                    if result.document_number:
                        seen_numbers[result.document_number] = document
                    seen_urls[normalized] = document
                    seen_hashes[blob.sha256] = document
                    if chosen == 20:
                        break
                if chosen == 20:
                    break
            if chosen == 20:
                break
    technical_status = _attempt_status(attempts)
    if not set(relationship_reports) <= sample_codes:
        raise AuditBlockedError("relationship report references a company outside the sample")
    if technical_status == "complete" and set(relationship_reports) != sample_codes:
        raise AuditBlockedError("a complete corpus requires exactly one relationship report per company")
    if any(
        not re.fullmatch(r"[0-9a-f]{64}", blob.sha256) or blob.byte_count <= 0 for blob in relationship_reports.values()
    ):
        raise AuditBlockedError("invalid relationship-report blob reference")
    relation_values = tuple(
        RelationshipReport(code, relationship_reports[code]) for code in sorted(relationship_reports)
    )
    return CandidateCorpus(
        protocol_sha256=sample.protocol_sha256,
        sample=sample,
        documents=tuple(documents),
        relationship_reports=relation_values,
        attempts=tuple(attempts),
        technical_status=technical_status,
        duplicates=tuple(duplicates),
    )


def wilson_interval(x: int, n: int) -> tuple[float, float]:
    """Calculate the protocol's binary64 Wilson score interval."""
    if n <= 0 or x < 0 or x > n:
        raise ValueError("Wilson inputs require 0 <= x <= n and n > 0")
    proportion = x / n
    z_squared = Z_95 * Z_95
    denominator = 1.0 + z_squared / n
    center = (proportion + z_squared / (2.0 * n)) / denominator
    half_width = Z_95 / denominator * math.sqrt(proportion * (1.0 - proportion) / n + z_squared / (4.0 * n * n))
    lower = 0.0 if x == 0 else max(0.0, center - half_width)
    upper = 1.0 if x == n else min(1.0, center + half_width)
    return lower, upper


def _display_six(value: float) -> str:
    return format(Decimal.from_float(value).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN), "f")


def _build_coverage_report_from_lineage(lineage: ValidatedAuditLineage) -> CoverageReport:
    """Build coverage only from inputs sealed by the full review-lineage validator."""
    if not isinstance(lineage, ValidatedAuditLineage):
        raise AuditBlockedError("coverage reporting requires validated audit lineage")
    sample = lineage.corpus.sample
    final_labels = lineage.final_labels
    adjudication = lineage.adjudication
    if lineage.corpus.technical_status != "complete" or _attempt_status(lineage.corpus.attempts) != "complete":
        raise TechnicalAttemptError("coverage reporting requires a complete technical ledger")
    if adjudication.get("parent_review_index_sha256") != lineage.review_index_sha256:
        raise UnadjudicatedDisagreementError("adjudication lineage changed before reporting")
    if _sha256_bytes(canonical_json_bytes(adjudication)) != lineage.adjudication_sha256:
        raise HashDriftError("adjudication changed after lineage validation")
    if len(sample.entries) != 36:
        raise AuditBlockedError("coverage requires the complete 36-company sample")
    sample_codes = {entry.ts_code for entry in sample.entries}
    if set(final_labels) != sample_codes:
        raise UnadjudicatedDisagreementError("final labels must cover exactly the frozen sample")
    decisions = adjudication.get("decisions", ())
    if isinstance(decisions, Sequence) and any(
        isinstance(item, Mapping) and item.get("disposition") == "rerun_new_version" for item in decisions
    ):
        raise UnadjudicatedDisagreementError("rerun_new_version requires a new audit version before reporting")

    def covered(ts_code: str) -> bool:
        if ts_code not in final_labels:
            raise UnadjudicatedDisagreementError(f"missing final company label: {ts_code}")
        label = final_labels[ts_code]
        if isinstance(label, Mapping):
            value = label.get("outcome", label.get("decision"))
        else:
            value = label
        if value in (True, "covered", "scorable", "accept"):
            return True
        if value in (False, "insufficient", "reject"):
            return False
        raise AuditBlockedError(f"unknown final company outcome for {ts_code}")

    rows: list[CoverageRow] = []
    groups: list[tuple[str, str, tuple[SampleEntry, ...], int]] = []
    for super_name in SUPER_STRATA:
        groups.append(
            ("industry", super_name, tuple(item for item in sample.entries if item.super_stratum == super_name), 4)
        )
    for cap in ("low", "high"):
        groups.append(("market_cap", cap, tuple(item for item in sample.entries if item.market_cap_stratum == cap), 12))
    dispositions = adjudication.get("scope_dispositions", {})
    if not isinstance(dispositions, Mapping):
        raise AuditBlockedError("scope_dispositions must be an object")
    known_layers = set(SUPER_STRATA) | {"low", "high"}
    if not set(dispositions) <= known_layers:
        raise AuditBlockedError("scope disposition references an unknown layer")
    for layer_type, layer, entries, threshold in groups:
        denominator = 6 if layer_type == "industry" else 18
        if len(entries) != denominator:
            raise AuditBlockedError(f"fixed denominator mismatch for {layer_type}/{layer}")
        scorable = sum(covered(item.ts_code) for item in entries)
        lower, upper = wilson_interval(scorable, denominator)
        passed = scorable >= threshold
        disposition = str(dispositions.get(layer, "not_required" if passed else "pending_user_decision"))
        allowed_dispositions = (
            {"not_required"} if passed else {"pending_user_decision", "excluded", "re_audit_new_version"}
        )
        if disposition not in allowed_dispositions:
            raise AuditBlockedError(f"invalid scope disposition for {layer}")
        rows.append(
            CoverageRow(
                layer_type,
                layer,
                denominator,
                scorable,
                denominator - scorable,
                scorable / denominator,
                passed,
                lower,
                upper,
                _display_six(lower),
                _display_six(upper),
                "none" if passed else "exclude_layer_or_reaudit",
                disposition,
            )
        )
    overall = all(row.passed for row in rows)
    blocked = any(
        row.scope_disposition in {"pending_user_decision", "re_audit_new_version"} for row in rows if not row.passed
    )
    return CoverageReport(
        tuple(rows),
        overall,
        blocked,
        lineage.corpus_manifest_sha256,
        lineage.review_index_sha256,
        lineage.reviewer_seal_sha256s,
        lineage.adjudication_sha256,
    )


__all__ = [
    "AttemptRecord",
    "AttemptStatus",
    "AuditBlockedError",
    "AuditError",
    "BlobRef",
    "CandidateCorpus",
    "CandidateDocument",
    "CoverageReport",
    "CoverageRow",
    "Decision",
    "DuplicateRecord",
    "FrameRow",
    "FrameValidationError",
    "FrameValidationResult",
    "FrozenArtifactError",
    "HashDriftError",
    "IsolationViolationError",
    "PassOverRecord",
    "PreregistrationRef",
    "QUERIES",
    "SAMPLING_SEED",
    "SOURCES",
    "SUPER_STRATA",
    "SW2021_INDUSTRIES",
    "SampleEntry",
    "SampleManifest",
    "SearchResult",
    "RelationshipReport",
    "TechnicalAttemptError",
    "UnadjudicatedDisagreementError",
    "build_candidate_corpus",
    "canonical_json_bytes",
    "load_preregistration_ref",
    "normalize_url",
    "reject_v132_segmented_attempt",
    "select_sample",
    "validate_frame",
    "wilson_interval",
]
