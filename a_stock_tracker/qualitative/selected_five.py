"""One-shot official-document collection for the authorized five-stock canary batch."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

from a_stock_tracker.qualitative.client import DEFAULT_GEMINI_MODEL
from a_stock_tracker.qualitative.orient_cable_pilot import OfficialFetchResult, _pdf_pages, fetch_official
from a_stock_tracker.qualitative.production import context_missing_score_dimensions
from a_stock_tracker.qualitative.production_contexts import ContextCollectionError, _fundamental_evidence
from a_stock_tracker.qualitative.validator import validate_context_dict

AUTHORIZATION_FILENAME = "qualitative_v2_selected_five_authorizations.json"
AUTHORIZATION_CONFIG_PATH = Path("config/qualitative/selected_five_authorizations.json")
AUTHORIZATION_SCHEMA = "qualitative-v2-selected-five-authorizations-v1"
APPROVED_AUTHORIZATION_ID = "qualitative-v2-selected-five-20260719-01"
APPROVED_CODES = ("000963", "002050", "600036", "600900", "601088")
APPROVED_HOSTS = (
    "www.cninfo.com.cn",
    "static.cninfo.com.cn",
    "www.hkexnews.hk",
    "www1.hkexnews.hk",
    "web-cn-oss.huadongpharm.com",
    "www.shenhuachina.com",
)
CNINFO_ENDPOINT = "https://www.cninfo.com.cn/new/fulltextSearch/full"
STATIC_CNINFO_ROOT = "https://static.cninfo.com.cn/"
MAX_SOURCE_HTTP_ATTEMPTS = 60
MAX_PDF_DOWNLOADS = 8
MAX_RESPONSE_RETRIES = 3
_TAG_RE = re.compile(r"<[^>]+>")
_PERSISTENT_RE = re.compile(r"(合同期限|履行期限|有效期|持续|长期|在手订单|[一二三四五六七八九十\d]+年|\d+个月)")


@dataclass(frozen=True, slots=True)
class SelectedFiveAuthorization:
    """Exact one-shot source and model grant for the selected five stocks."""

    authorization_id: str
    target_codes: tuple[str, ...]
    as_of_date: str
    allowed_hosts: tuple[str, ...]
    source_http_attempt_limit: int
    pdf_download_limit: int
    model: str
    gemini_logical_call_limit: int
    gemini_http_attempt_limit: int


COMPANIES: dict[str, tuple[str, str]] = {
    "000963": ("华东医药", "医药制造"),
    "002050": ("三花智控", "家电零部件"),
    "600036": ("招商银行", "银行"),
    "600900": ("长江电力", "电力"),
    "601088": ("中国神华", "煤炭"),
}

ANNUAL_REPORTS: dict[str, tuple[str, date]] = {
    "000963": (
        "https://web-cn-oss.huadongpharm.com/huadong/Reports/2025/"
        "2025%E5%8D%8E%E4%B8%9C%E5%8C%BB%E8%8D%AF%EF%BC%9A2025%E5%B9%B4%E5%B9%B4%E5%BA%A6%E6%8A%A5%E5%91%8A.pdf",
        date(2026, 4, 24),
    ),
    "002050": ("https://static.cninfo.com.cn/finalpage/2026-03-24/1225026522.PDF", date(2026, 3, 24)),
    "600036": (
        "https://www.hkexnews.hk/listedco/listconews/sehk/2026/0327/2026032701767.pdf",
        date(2026, 3, 28),
    ),
    "600900": ("https://static.cninfo.com.cn/finalpage/2026-04-30/1225262036.PDF", date(2026, 4, 30)),
    "601088": (
        "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0330/2026033003748.pdf",
        date(2026, 3, 31),
    ),
}

# Company-specific phrases avoid promoting generic industry text as direct evidence.
EVIDENCE_PATTERNS: dict[str, dict[str, tuple[str, ...]]] = {
    "000963": {
        "moat": ("工业微生物", "研发平台", "核心技术平台"),
        "market_pos": ("市场占有率", "行业领先", "国内领先"),
    },
    "002050": {
        "moat": ("电子膨胀阀", "热管理", "核心技术"),
        "market_pos": ("全球领先", "市场占有率", "行业龙头"),
    },
    "600036": {
        "moat": (
            "wealth management and asset management sector continued",
            "CMB TREE Asset Allocation Service System",
        ),
        "market_pos": ("scale of direct bill discounting business ranked the second in the market",),
    },
    "600900": {
        "moat": ("六座梯级电站", "梯级联合调度", "水电资源"),
        "market_pos": ("装机容量", "世界最大", "行业领先"),
    },
    "601088": {
        "moat": ("advantages of integrated coal-power-transportation-chemical operations",),
        "market_pos": ("top coal port in China in terms of throughput for seven consecutive years",),
    },
}


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


def _authorization_from_dict(value: object) -> SelectedFiveAuthorization:
    fields = {
        "authorization_id",
        "target_codes",
        "as_of_date",
        "allowed_hosts",
        "source_http_attempt_limit",
        "pdf_download_limit",
        "model",
        "gemini_logical_call_limit",
        "gemini_http_attempt_limit",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ContextCollectionError("selected-five active authorization contract drift")
    authorization = SelectedFiveAuthorization(
        authorization_id=cast(str, value["authorization_id"]),
        target_codes=tuple(cast(list[str], value["target_codes"])),
        as_of_date=cast(str, value["as_of_date"]),
        allowed_hosts=tuple(cast(list[str], value["allowed_hosts"])),
        source_http_attempt_limit=cast(int, value["source_http_attempt_limit"]),
        pdf_download_limit=cast(int, value["pdf_download_limit"]),
        model=cast(str, value["model"]),
        gemini_logical_call_limit=cast(int, value["gemini_logical_call_limit"]),
        gemini_http_attempt_limit=cast(int, value["gemini_http_attempt_limit"]),
    )
    expected = SelectedFiveAuthorization(
        APPROVED_AUTHORIZATION_ID,
        APPROVED_CODES,
        "2026-07-19",
        APPROVED_HOSTS,
        MAX_SOURCE_HTTP_ATTEMPTS,
        MAX_PDF_DOWNLOADS,
        DEFAULT_GEMINI_MODEL,
        5,
        15,
    )
    if authorization != expected:
        raise ContextCollectionError("selected-five authorization differs from the direct user grant")
    return authorization


def load_active_authorization(project_root: Path, authorization_id: str) -> SelectedFiveAuthorization:
    """Load the exact active grant and reject any retired use."""
    configured = project_root / AUTHORIZATION_CONFIG_PATH
    path = configured if configured.is_file() else project_root / AUTHORIZATION_FILENAME
    if path.is_symlink() or not path.is_file():
        raise ContextCollectionError("selected-five authorization ledger is missing or unsafe")
    try:
        ledger = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextCollectionError("selected-five authorization ledger is invalid") from exc
    if not isinstance(ledger, dict) or set(ledger) != {"schema_version", "active", "retired"}:
        raise ContextCollectionError("selected-five authorization ledger contract drift")
    if ledger.get("schema_version") != AUTHORIZATION_SCHEMA or not isinstance(ledger.get("retired"), list):
        raise ContextCollectionError("selected-five authorization ledger schema drift")
    for item in cast(list[object], ledger["retired"]):
        if not isinstance(item, dict) or not isinstance(item.get("authorization_id"), str):
            raise ContextCollectionError("selected-five retired authorization entry is invalid")
        if item["authorization_id"] == authorization_id:
            raise ContextCollectionError("selected-five authorization is retired and cannot be reused")
    if ledger.get("active") is None:
        raise ContextCollectionError("no active selected-five authorization")
    authorization = _authorization_from_dict(ledger["active"])
    if authorization.authorization_id != authorization_id:
        raise ContextCollectionError("authorization ID does not match the selected-five grant")
    return authorization


def create_run_root(project_root: Path, run_id: str) -> Path:
    """Create a private, create-only selected-five source root."""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id) is None or run_id in {".", ".."}:
        raise ContextCollectionError("selected-five run ID is unsafe")
    base = project_root / "artifacts" / "qualitative-v2-selected-five"
    cursor = project_root
    for part in base.relative_to(project_root).parts:
        cursor /= part
        if cursor.is_symlink():
            raise ContextCollectionError("selected-five artifact ancestry contains a symlink")
        if cursor.exists() and not cursor.is_dir():
            raise ContextCollectionError("selected-five artifact ancestry contains a non-directory")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.chmod(0o700)
    root = base / run_id
    if root.exists() or root.is_symlink():
        raise ContextCollectionError("selected-five run ID is create-only")
    root.mkdir(mode=0o700)
    for child in ("raw", "receipts", "contexts"):
        (root / child).mkdir(mode=0o700)
    return root


def _clean(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(html.unescape(_TAG_RE.sub("", value)).split())


def _announcement_date(value: object) -> date:
    if isinstance(value, bool):
        raise ContextCollectionError("CNINFO announcement time has invalid type")
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone(timedelta(hours=8))).date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip().replace("/", "-")[:10])
        except ValueError as exc:
            raise ContextCollectionError("CNINFO announcement time is invalid") from exc
    raise ContextCollectionError("CNINFO announcement time is missing")


def _search_url(name: str, keyword: str, start: date, end: date) -> str:
    params = {
        "searchkey": f"{name} {keyword}",
        "sdate": start.isoformat(),
        "edate": end.isoformat(),
        "isfulltext": "true",
        "sortName": "nothing",
        "sortType": "desc",
        "pageNum": "1",
        "pageSize": "20",
        "type": "shj",
    }
    return f"{CNINFO_ENDPOINT}?{urllib.parse.urlencode(params)}"


class _Ledger:
    def __init__(
        self,
        authorization: SelectedFiveAuthorization,
        root: Path,
        fetcher: Callable[[str, tuple[str, ...]], OfficialFetchResult],
        prior_http_attempts: int,
        prior_pdf_downloads: int,
    ) -> None:
        self.authorization = authorization
        self.root = root
        self.fetcher = fetcher
        self.prior_http_attempts = prior_http_attempts
        self.prior_pdf_downloads = prior_pdf_downloads
        self.operations: list[dict[str, object]] = []
        self.pdf_downloads = 0

    def get(self, url: str, *, label: str, expect_pdf: bool = False) -> tuple[bytes, str]:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in self.authorization.allowed_hosts:
            raise ContextCollectionError("selected-five URL is outside the approved hosts")
        if expect_pdf and self.prior_pdf_downloads + self.pdf_downloads >= self.authorization.pdf_download_limit:
            raise ContextCollectionError("selected-five PDF budget exhausted")
        for local_attempt in range(1, MAX_RESPONSE_RETRIES + 1):
            if self.prior_http_attempts + len(self.operations) >= self.authorization.source_http_attempt_limit:
                raise ContextCollectionError("selected-five source HTTP budget exhausted")
            result = self.fetcher(url, self.authorization.allowed_hosts)
            sequence = len(self.operations) + 1
            if expect_pdf:
                self.pdf_downloads += 1
            digest = hashlib.sha256(result.body).hexdigest() if result.body else None
            suffix = ".pdf" if expect_pdf else ".json"
            raw_path = f"raw/{sequence:03d}-{label}{suffix}" if result.body else None
            if raw_path is not None:
                _secure_write(self.root / raw_path, result.body)
            operation = {
                "attempt": self.prior_http_attempts + sequence,
                "authorization_id": self.authorization.authorization_id,
                "byte_count": len(result.body),
                "content_type": result.content_type,
                "error_class": result.error_class,
                "final_url": result.final_url,
                "label": label,
                "local_attempt": local_attempt,
                "pdf_download": expect_pdf,
                "raw_path": raw_path,
                "raw_sha256": digest,
                "requested_url": url,
                "status_code": result.status_code,
            }
            self.operations.append(operation)
            _secure_write(
                self.root / "receipts" / f"{self.prior_http_attempts + sequence:03d}.json",
                _canonical_bytes(operation),
            )
            if result.requested_url != url or result.final_url != url:
                raise ContextCollectionError("selected-five transport URL drift or redirect")
            if result.status_code == 200 and result.error_class is None and result.body and digest:
                return result.body, digest
            retryable = (
                result.status_code is None
                or result.status_code in {408, 429}
                or (result.status_code is not None and result.status_code >= 500)
            )
            if expect_pdf or not retryable:
                break
        raise ContextCollectionError(f"selected-five official request failed: {label}")


def _payload(raw: bytes, *, label: str) -> list[dict[str, object]]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextCollectionError(f"{label} response is not JSON") from exc
    if not isinstance(value, dict):
        raise ContextCollectionError(f"{label} response contract drift")
    announcements = value.get("announcements")
    if announcements is None:
        announcements = []
    if not isinstance(announcements, list):
        raise ContextCollectionError(f"{label} response contract drift")
    return [cast(dict[str, object], item) for item in announcements if isinstance(item, dict)]


def _excerpt(pages: list[str], patterns: tuple[str, ...]) -> tuple[int, str] | None:
    matches: list[tuple[int, int, str]] = []
    for page_number, page in enumerate(pages, start=1):
        for pattern in patterns:
            start = page.find(pattern)
            if start < 0:
                continue
            left = max(0, start - 300)
            right = min(len(page), start + len(pattern) + 500)
            text = page[left:right]
            # Very short table-of-contents hits are not evidence.
            if len(text) >= 120:
                matches.append((page_number, start, text))
    if not matches:
        return None
    page_number, _position, text = matches[0]
    return page_number, text


def _annual_evidence(
    pages: list[str],
    *,
    raw_sha256: str,
    source_date: date,
    code: str,
    dimension: str,
) -> dict[str, object] | None:
    located = _excerpt(pages, EVIDENCE_PATTERNS[code][dimension])
    if located is None:
        return None
    page_number, excerpt = located
    suffix = hashlib.sha256(f"{code}\n{dimension}\n{page_number}\n{excerpt}".encode()).hexdigest()[:20]
    return {
        "evidence_id": f"disclosure.{dimension}.{suffix}",
        "evidence_type": "company_disclosure",
        "claim_category": "competitive_moat" if dimension == "moat" else "industry_position",
        "allowed_dimensions": [dimension],
        "directness": "direct",
        "freshness_policy": "max_age_365d",
        "value": excerpt,
        "unit": None,
        "source": f"official company report;document_sha256={raw_sha256};locator=pdf_page:{page_number}",
        "source_date": source_date.isoformat(),
        "freshness_status": "fresh",
    }


def _sentiment_evidence(
    raw: bytes,
    *,
    raw_sha256: str,
    code: str,
    name: str,
    as_of_date: date,
    keyword: str,
) -> dict[str, object] | None:
    for item in _payload(raw, label="sentiment search"):
        if str(item.get("secCode") or "").strip() != code or _clean(item.get("secName")) != name:
            continue
        title = _clean(item.get("announcementTitle"))
        content = _clean(item.get("announcementContent"))
        combined = f"{title}：{content}".strip("：")
        if not content or keyword not in combined or _PERSISTENT_RE.search(combined) is None:
            continue
        source_date = _announcement_date(item.get("announcementTime"))
        if not as_of_date - timedelta(days=30) <= source_date <= as_of_date:
            continue
        announcement_id = str(item.get("announcementId") or "").strip()
        if not announcement_id:
            continue
        suffix = hashlib.sha256(f"{code}\n{announcement_id}\n{combined}".encode()).hexdigest()[:20]
        return {
            "evidence_id": f"disclosure.sentiment.{suffix}",
            "evidence_type": "company_disclosure",
            "claim_category": "market_sentiment",
            "allowed_dimensions": ["sentiment"],
            "directness": "direct",
            "freshness_policy": "max_age_30d",
            "value": combined[:4000],
            "unit": None,
            "source": (
                f"CNINFO fulltextSearch/full;response_sha256={raw_sha256};"
                f"locator=announcementId:{announcement_id}/announcementContent"
            ),
            "source_date": source_date.isoformat(),
            "freshness_status": "fresh",
            "persistence_horizon": "multi_quarter",
            "materiality": "major" if "重大" in combined else "moderate",
        }
    return None


def collect_selected_five(
    conn: sqlite3.Connection,
    *,
    authorization: SelectedFiveAuthorization,
    run_root: Path,
    as_of_date: date,
    prior_http_attempts: int = 0,
    prior_pdf_downloads: int = 0,
    fetcher: Callable[[str, tuple[str, ...]], OfficialFetchResult] = fetch_official,
    prior_responses: Mapping[str, tuple[bytes, str]] | None = None,
) -> dict[str, object]:
    """Collect and seal exactly five official-document contexts."""
    if authorization.target_codes != APPROVED_CODES or authorization.as_of_date != as_of_date.isoformat():
        raise ContextCollectionError("selected-five collection authorization drift")
    placeholders = ",".join("?" for _ in APPROVED_CODES)
    rows = conn.execute(
        f"SELECT code, data FROM stock_fundamentals WHERE code IN ({placeholders})", APPROVED_CODES
    ).fetchall()
    fundamentals = {str(code): (data,) for code, data in rows}
    if not 0 <= prior_http_attempts <= authorization.source_http_attempt_limit:
        raise ContextCollectionError("selected-five source HTTP accounting is invalid")
    if not 0 <= prior_pdf_downloads <= authorization.pdf_download_limit:
        raise ContextCollectionError("selected-five PDF accounting is invalid")
    ledger = _Ledger(authorization, run_root, fetcher, prior_http_attempts, prior_pdf_downloads)
    context_hashes: dict[str, str] = {}
    readiness: dict[str, dict[str, object]] = {}
    reused_inputs: list[dict[str, str]] = []

    def get(url: str, *, label: str, expect_pdf: bool = False) -> tuple[bytes, str]:
        prior = (prior_responses or {}).get(url)
        if prior is not None:
            raw, digest = prior
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ContextCollectionError("selected-five reused source hash drift")
            reused_inputs.append({"label": label, "raw_sha256": digest, "requested_url": url})
            return raw, digest
        return ledger.get(url, label=label, expect_pdf=expect_pdf)

    for code in APPROVED_CODES:
        name, industry = COMPANIES[code]
        evidence = _fundamental_evidence(fundamentals.get(code), as_of_date)
        pdf_url, report_date = ANNUAL_REPORTS[code]
        annual_pdf, annual_sha = get(pdf_url, label=f"{code}-annual-report", expect_pdf=True)
        pages = _pdf_pages(annual_pdf)
        for dimension in ("moat", "market_pos"):
            item = _annual_evidence(
                pages,
                raw_sha256=annual_sha,
                source_date=report_date,
                code=code,
                dimension=dimension,
            )
            if item is not None:
                evidence.append(item)
        sentiment = None
        for keyword in ("合同期限", "中标", "在手订单"):
            body, digest = get(
                _search_url(name, keyword, as_of_date - timedelta(days=30), as_of_date),
                label=f"{code}-sentiment-{keyword}",
            )
            candidate = _sentiment_evidence(
                body,
                raw_sha256=digest,
                code=code,
                name=name,
                as_of_date=as_of_date,
                keyword=keyword,
            )
            if sentiment is None and candidate is not None:
                sentiment = candidate
        if sentiment is not None:
            evidence.append(sentiment)
        context: dict[str, object] = {
            "code": code,
            "name": name,
            "industry": industry,
            "as_of_date": as_of_date.isoformat(),
            "schema_version": "qualitative-score-v2",
            "rubric_version": "rubric-v1",
            "taxonomy_version": "taxonomy-v1",
            "evidence": evidence,
        }
        validation = validate_context_dict(context)
        if not validation.valid or validation.context is None:
            raise ContextCollectionError(f"selected-five context is invalid for {code}: {validation.rejection_reason}")
        missing = list(context_missing_score_dimensions(validation.context))
        scoreable = [dimension for dimension in ("moat", "market_pos", "sentiment") if dimension not in missing]
        if not scoreable:
            raise ContextCollectionError(f"selected-five context has no scoreable dimension for {code}")
        context_hashes[code] = validation.context.compute_input_hash()
        readiness[code] = {"missing": missing, "scoreable": scoreable}
        _secure_write(run_root / "contexts" / f"{code}.json", _canonical_bytes(context))
    manifest = {
        "schema_version": "qualitative-v2-selected-five-contexts-v1",
        "authorization_id": authorization.authorization_id,
        "as_of_date": as_of_date.isoformat(),
        "context_hashes": context_hashes,
        "database_reads": 1,
        "database_writes": 0,
        "gemini_calls": 0,
        "http_attempt_limit": authorization.source_http_attempt_limit,
        "http_attempts": len(ledger.operations),
        "prior_http_attempts": prior_http_attempts,
        "cumulative_http_attempts": prior_http_attempts + len(ledger.operations),
        "model": authorization.model,
        "operations": ledger.operations,
        "pdf_download_limit": authorization.pdf_download_limit,
        "pdf_downloads": ledger.pdf_downloads,
        "prior_pdf_downloads": prior_pdf_downloads,
        "cumulative_pdf_downloads": prior_pdf_downloads + ledger.pdf_downloads,
        "readiness": readiness,
        "reused_inputs": reused_inputs,
        "target_codes": list(authorization.target_codes),
    }
    manifest_raw = _canonical_bytes(manifest)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    _secure_write(run_root / "manifest.json", manifest_raw)
    _secure_write(run_root / "manifest.sha256", f"{manifest_sha}  manifest.json\n".encode("ascii"))
    return {
        "authorization_id": authorization.authorization_id,
        "contexts_path": str(run_root / "contexts"),
        "context_hashes": context_hashes,
        "http_attempts": len(ledger.operations),
        "cumulative_http_attempts": prior_http_attempts + len(ledger.operations),
        "manifest_sha256": manifest_sha,
        "pdf_downloads": ledger.pdf_downloads,
        "cumulative_pdf_downloads": prior_pdf_downloads + ledger.pdf_downloads,
        "readiness": readiness,
        "status": "READY_FOR_HYBRID_MODEL",
    }


def load_prior_usage(project_root: Path) -> tuple[int, int]:
    """Rebuild source and PDF usage from durable selected-five receipts."""
    base = project_root / "artifacts" / "qualitative-v2-selected-five"
    if not base.exists():
        return 0, 0
    if base.is_symlink() or not base.is_dir():
        raise ContextCollectionError("selected-five artifact base is unsafe")
    attempts = 0
    pdf_downloads = 0
    for receipt in sorted(base.glob("*/receipts/*.json")):
        if receipt.is_symlink() or not receipt.is_file():
            raise ContextCollectionError("selected-five receipt is unsafe")
        try:
            value = json.loads(receipt.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContextCollectionError("selected-five receipt is invalid") from exc
        if not isinstance(value, dict) or not isinstance(value.get("pdf_download"), bool):
            raise ContextCollectionError("selected-five receipt contract drift")
        attempts += 1
        pdf_downloads += int(value["pdf_download"])
    return attempts, pdf_downloads


def load_prior_responses(project_root: Path) -> dict[str, tuple[bytes, str]]:
    """Load hash-verified successful response bytes for source-network-free reuse."""
    base = project_root / "artifacts" / "qualitative-v2-selected-five"
    if not base.exists():
        return {}
    responses: dict[str, tuple[bytes, str]] = {}
    for receipt in sorted(base.glob("*/receipts/*.json")):
        if receipt.is_symlink() or not receipt.is_file():
            raise ContextCollectionError("selected-five receipt is unsafe")
        try:
            value = json.loads(receipt.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContextCollectionError("selected-five receipt is invalid") from exc
        if not isinstance(value, dict) or value.get("status_code") != 200 or value.get("error_class") is not None:
            continue
        url = value.get("requested_url")
        raw_path = value.get("raw_path")
        digest = value.get("raw_sha256")
        if not all(isinstance(item, str) and item for item in (url, raw_path, digest)):
            raise ContextCollectionError("selected-five successful receipt is incomplete")
        source = receipt.parents[1] / cast(str, raw_path)
        if source.is_symlink() or not source.is_file():
            raise ContextCollectionError("selected-five prior raw response is missing or unsafe")
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ContextCollectionError("selected-five prior raw response hash drift")
        responses[cast(str, url)] = (raw, cast(str, digest))
    return responses


def validate_context_manifest(
    project_root: Path,
    authorization: SelectedFiveAuthorization,
    contexts_directory: Path,
) -> dict[str, str]:
    """Bind model execution to the sealed source manifest and exact context hashes."""
    if contexts_directory.is_symlink() or not contexts_directory.is_dir():
        raise ContextCollectionError("selected-five contexts directory is missing or unsafe")
    resolved_contexts = contexts_directory.resolve(strict=True)
    root = resolved_contexts.parent
    sealed_base = (project_root / "artifacts" / "qualitative-v2-selected-five").resolve(strict=True)
    try:
        root.relative_to(sealed_base)
    except ValueError as exc:
        raise ContextCollectionError("selected-five contexts are outside the sealed artifact root") from exc
    manifest_path = root / "manifest.json"
    checksum_path = root / "manifest.sha256"
    if any(path.is_symlink() or not path.is_file() for path in (manifest_path, checksum_path)):
        raise ContextCollectionError("selected-five manifest is missing or unsafe")
    raw = manifest_path.read_bytes()
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextCollectionError("selected-five manifest is invalid") from exc
    if raw != _canonical_bytes(manifest):
        raise ContextCollectionError("selected-five manifest is non-canonical")
    digest = hashlib.sha256(raw).hexdigest()
    if checksum_path.read_bytes() != f"{digest}  manifest.json\n".encode("ascii"):
        raise ContextCollectionError("selected-five manifest checksum drift")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "qualitative-v2-selected-five-contexts-v1"
        or manifest.get("authorization_id") != authorization.authorization_id
        or manifest.get("target_codes") != list(authorization.target_codes)
        or manifest.get("model") != authorization.model
    ):
        raise ContextCollectionError("selected-five manifest binding drift")
    hashes = manifest.get("context_hashes")
    if not isinstance(hashes, dict) or set(hashes) != set(authorization.target_codes):
        raise ContextCollectionError("selected-five context hash set drift")
    result: dict[str, str] = {}
    for code in authorization.target_codes:
        path = resolved_contexts / f"{code}.json"
        if path.is_symlink() or not path.is_file():
            raise ContextCollectionError("selected-five context is missing or unsafe")
        validation = validate_context_dict(json.loads(path.read_bytes()))
        if not validation.valid or validation.context is None:
            raise ContextCollectionError(f"selected-five context failed validation for {code}")
        computed = validation.context.compute_input_hash()
        if hashes.get(code) != computed:
            raise ContextCollectionError(f"selected-five context hash drift for {code}")
        result[code] = computed
    return result


__all__ = [
    "SelectedFiveAuthorization",
    "collect_selected_five",
    "create_run_root",
    "load_active_authorization",
    "load_prior_usage",
    "load_prior_responses",
    "validate_context_manifest",
]
