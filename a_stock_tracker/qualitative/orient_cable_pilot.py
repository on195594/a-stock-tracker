"""Bounded official-document collector for the approved 603606 production pilot."""

from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import sqlite3
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import cast

from pypdf import PdfReader

from a_stock_tracker.qualitative.production import context_missing_score_dimensions
from a_stock_tracker.qualitative.production_contexts import ContextCollectionError, ProductionContextAuthorization
from a_stock_tracker.qualitative.validator import validate_context_dict

ORIENT_CABLE_CODE = "603606"
ORIENT_CABLE_NAME = "东方电缆"
ORIENT_CABLE_INDUSTRY = "电力设备"
ANNOUNCEMENT_PAGE = "https://www.orientcable.com/ttz2.html"
ANNOUNCEMENT_LIST = "https://www.orientcable.com/ajax.asp?p=ajax_down_list&tbn=05&pgn=ttz2&l=cn&a=1"
ANNOUNCEMENT_VIEW = "https://www.orientcable.com/ajax.asp?p=ajax_down_view&tbn=05&l=cn&x={download_id}"
ANNUAL_REPORT_URL = "https://static.cninfo.com.cn/finalpage/2026-03-28/1225048251.PDF"
ABOUT_PAGE = "https://www.orientcable.com/about.html"
NEWS_PAGE = "https://www.orientcable.com/news1.html"
CNINFO_ENDPOINT = "https://www.cninfo.com.cn/new/fulltextSearch/full"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_PDF_PAGES = 400
MAX_EXTRACTED_TEXT_CHARS = 4_000_000
_TAG_RE = re.compile(r"<[^>]+>")
_PERSISTENT_RE = re.compile(r"(合同期限|履行期限|有效期|持续|长期|在手订单|[一二三四五六七八九十\d]+年|\d+个月)")


@dataclass(frozen=True, slots=True)
class OfficialFetchResult:
    """One official-source HTTP attempt."""

    requested_url: str
    final_url: str
    status_code: int | None
    content_type: str | None
    body: bytes
    error_class: str | None = None


OfficialFetcher = Callable[[str, tuple[str, ...]], OfficialFetchResult]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def _validated_url(url: str, allowed_hosts: tuple[str, ...]) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ContextCollectionError("official source URL is outside the approved host boundary")
    return parsed


def fetch_official(url: str, allowed_hosts: tuple[str, ...]) -> OfficialFetchResult:
    """Fetch one bounded official resource without redirects."""
    _validated_url(url, allowed_hosts)
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect()
    )
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "text/html,application/json,application/pdf", "User-Agent": "a-stock-tracker-v2-pilot/1.0"},
    )
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ContextCollectionError("official response exceeds the byte limit")
            return OfficialFetchResult(
                url,
                response.geturl(),
                int(response.status),
                response.headers.get_content_type(),
                body,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            body = b""
        return OfficialFetchResult(
            url, exc.geturl(), exc.code, exc.headers.get_content_type(), body, f"http_{exc.code}"
        )
    except (OSError, urllib.error.URLError, ssl.SSLError, ContextCollectionError) as exc:
        return OfficialFetchResult(url, url, None, None, b"", type(exc).__name__)


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


def create_pilot_run_root(project_root: Path, run_id: str) -> Path:
    """Create a private create-only artifact root for the single-stock pilot."""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id) is None or run_id in {".", ".."}:
        raise ContextCollectionError("run-id must be a bounded safe identifier")
    base = project_root / "artifacts" / "qualitative-v2-orient-cable"
    cursor = project_root
    for part in base.relative_to(project_root).parts:
        cursor /= part
        if cursor.is_symlink():
            raise ContextCollectionError("pilot artifact ancestry contains a symlink")
        if cursor.exists() and not cursor.is_dir():
            raise ContextCollectionError("pilot artifact ancestry contains a non-directory")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.chmod(0o700)
    root = base / run_id
    if root.exists() or root.is_symlink():
        raise ContextCollectionError("pilot run-id is create-only")
    root.mkdir(mode=0o700)
    (root / "raw").mkdir(mode=0o700)
    (root / "receipts").mkdir(mode=0o700)
    (root / "contexts").mkdir(mode=0o700)
    return root


def load_prior_source_attempts(project_root: Path) -> int:
    """Count receipts from earlier pilot roots, including the first pre-receipt failed run."""
    base = project_root / "artifacts" / "qualitative-v2-orient-cable"
    if not base.exists():
        return 0
    if base.is_symlink() or not base.is_dir():
        raise ContextCollectionError("pilot artifact base is unsafe")
    total = 0
    for root in sorted(base.iterdir()):
        if root.is_symlink() or not root.is_dir():
            raise ContextCollectionError("pilot artifact run root is unsafe")
        receipts = root / "receipts"
        if receipts.is_dir() and not receipts.is_symlink():
            receipt_paths = sorted(receipts.glob("*.json"))
            for receipt in receipt_paths:
                if receipt.is_symlink() or not receipt.is_file():
                    raise ContextCollectionError("pilot attempt receipt is unsafe")
                try:
                    payload = json.loads(receipt.read_bytes())
                except (OSError, json.JSONDecodeError) as exc:
                    raise ContextCollectionError("pilot attempt receipt is invalid") from exc
                if not isinstance(payload, dict) or payload.get("attempt") != int(receipt.stem):
                    raise ContextCollectionError("pilot attempt receipt identity drift")
            total += len(receipt_paths)
            continue
        raw = root / "raw"
        if not raw.is_dir() or raw.is_symlink():
            raise ContextCollectionError("legacy pilot run is incomplete or unsafe")
        legacy_paths = sorted(raw.iterdir())
        if any(path.is_symlink() or not path.is_file() for path in legacy_paths):
            raise ContextCollectionError("legacy pilot raw artifact is unsafe")
        total += len(legacy_paths)
    return total


def load_prior_pdf_attempts(project_root: Path) -> int:
    """Count prior official PDF attempts from durable receipts."""
    base = project_root / "artifacts" / "qualitative-v2-orient-cable"
    if not base.exists():
        return 0
    if base.is_symlink() or not base.is_dir():
        raise ContextCollectionError("pilot artifact base is unsafe")
    total = 0
    for receipt in sorted(base.glob("*/receipts/*.json")):
        if receipt.is_symlink() or not receipt.is_file():
            raise ContextCollectionError("pilot PDF receipt is unsafe")
        try:
            payload = json.loads(receipt.read_bytes())
        except (OSError, json.JSONDecodeError) as exc:
            raise ContextCollectionError("pilot PDF receipt is invalid") from exc
        if not isinstance(payload, dict):
            raise ContextCollectionError("pilot PDF receipt contract drift")
        if payload.get("label") == "annual-report":
            total += 1
    return total


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((" ".join("".join(self._text).split()), self._href))
            self._href = None
            self._text = []


def _annual_report_url(raw_html: bytes, base_url: str, allowed_hosts: tuple[str, ...]) -> str:
    try:
        text = raw_html.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_html.decode("gb18030")
    parser = _LinkParser()
    parser.feed(text)
    candidates: list[str] = []
    for label, href in parser.links:
        normalized = label.replace(" ", "")
        if "2025" in normalized and "年度报告" in normalized and "摘要" not in normalized:
            candidate = urllib.parse.urljoin(base_url, href)
            _validated_url(candidate, allowed_hosts)
            candidates.append(candidate)
    if len(set(candidates)) != 1:
        raise ContextCollectionError(f"expected one official 2025 annual report link, found {len(set(candidates))}")
    return candidates[0]


def _annual_download_id(raw_html: bytes) -> str:
    try:
        text = raw_html.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_html.decode("gb18030")
    normalized = re.sub(r"\s+", "", text)
    report = re.search(r"2025年?年度报告(?!摘要)", normalized)
    if report is None:
        raise ContextCollectionError("official announcement list has no 2025 annual report")
    prefix = normalized[max(0, report.start() - 1000) : report.start()]
    identifiers = re.findall(r"download\(['\"]?([A-Za-z0-9_-]+)", prefix)
    if not identifiers:
        suffix = normalized[report.end() : report.end() + 1000]
        identifiers = re.findall(r"download\(['\"]?([A-Za-z0-9_-]+)", suffix)
    if not identifiers:
        raise ContextCollectionError("official annual report download identity is missing")
    return identifiers[-1]


def _download_url(raw_json: bytes, base_url: str, allowed_hosts: tuple[str, ...]) -> str:
    try:
        value = json.loads(raw_json)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextCollectionError("official download response is not JSON") from exc
    if (
        not isinstance(value, dict)
        or value.get("err") not in {"", "0", 0, None}
        or not isinstance(value.get("pUrl"), str)
    ):
        raise ContextCollectionError("official download response contract drift")
    url = urllib.parse.urljoin(base_url, cast(str, value["pUrl"]))
    _validated_url(url, allowed_hosts)
    return url


def _clean_html(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError:
        value = raw.decode("gb18030")
    return " ".join(html.unescape(_TAG_RE.sub(" ", value)).split())


def _excerpt(text: str, patterns: tuple[str, ...], *, radius: int = 350) -> str | None:
    positions = [text.find(pattern) for pattern in patterns if pattern in text]
    if not positions:
        return None
    start = max(0, min(positions) - radius)
    end = min(len(text), min(positions) + radius)
    return text[start:end]


def _pdf_pages(raw: bytes) -> list[str]:
    try:
        reader = PdfReader(io.BytesIO(raw), strict=True)
    except Exception as exc:
        raise ContextCollectionError("official annual report PDF is invalid") from exc
    if not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
        raise ContextCollectionError("official annual report PDF page count is out of bounds")
    pages: list[str] = []
    total = 0
    for page in reader.pages:
        try:
            text = " ".join((page.extract_text() or "").split())
        except Exception as exc:
            raise ContextCollectionError("official annual report PDF text extraction failed") from exc
        total += len(text)
        if total > MAX_EXTRACTED_TEXT_CHARS:
            raise ContextCollectionError("official annual report extracted text exceeds the limit")
        pages.append(text)
    return pages


def _pdf_evidence(
    pages: list[str],
    *,
    raw_sha256: str,
    dimension: str,
    claim_category: str,
    patterns: tuple[str, ...],
) -> dict[str, object] | None:
    for page_number, page in enumerate(pages, start=1):
        excerpt = _excerpt(page, patterns)
        if excerpt:
            suffix = hashlib.sha256(f"{dimension}\n{page_number}\n{excerpt}".encode()).hexdigest()[:20]
            return {
                "evidence_id": f"disclosure.{dimension}.{suffix}",
                "evidence_type": "company_disclosure",
                "claim_category": claim_category,
                "allowed_dimensions": [dimension],
                "directness": "direct",
                "freshness_policy": "max_age_365d",
                "value": excerpt,
                "unit": None,
                "source": (
                    f"official 2025 annual report summary;document_sha256={raw_sha256};locator=pdf_page:{page_number}"
                ),
                "source_date": "2026-03-28",
                "freshness_status": "fresh",
            }
    return None


def _fundamental_evidence(conn: sqlite3.Connection, as_of_date: date) -> dict[str, object] | None:
    row = conn.execute("SELECT data FROM stock_fundamentals WHERE code=?", (ORIENT_CABLE_CODE,)).fetchone()
    if row is None or not isinstance(row[0], str):
        return None
    try:
        data = json.loads(row[0])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    roe = data.get("roe_3y_avg")
    report_period = data.get("report_period")
    if isinstance(roe, bool) or not isinstance(roe, (int, float)) or not isinstance(report_period, str):
        return None
    try:
        source_date = date.fromisoformat(f"{report_period}-12-31" if len(report_period) == 4 else report_period[:10])
    except ValueError:
        return None
    if source_date > as_of_date or (as_of_date - source_date).days > 550:
        return None
    subset = {"report_period": report_period, "roe_3y_avg": float(roe)}
    digest = hashlib.sha256(_canonical_bytes(subset)).hexdigest()
    return {
        "evidence_id": "fundamentals.roe_3y_avg",
        "evidence_type": "financial_metric",
        "claim_category": "financial_performance",
        "allowed_dimensions": ["moat"],
        "directness": "supporting",
        "freshness_policy": "max_age_550d",
        "value": float(roe),
        "unit": "percent",
        "source": f"local read-only stock_fundamentals;row_sha256={digest};locator=data.roe_3y_avg",
        "source_date": source_date.isoformat(),
        "freshness_status": "fresh",
    }


def _cninfo_url(keyword: str, as_of_date: date) -> str:
    params = {
        "searchkey": f"{ORIENT_CABLE_NAME} {keyword}",
        "sdate": (as_of_date - timedelta(days=30)).isoformat(),
        "edate": as_of_date.isoformat(),
        "isfulltext": "true",
        "sortName": "nothing",
        "sortType": "desc",
        "pageNum": "1",
        "pageSize": "20",
        "type": "shj",
    }
    return f"{CNINFO_ENDPOINT}?{urllib.parse.urlencode(params)}"


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


def _sentiment_from_cninfo(
    raw: bytes,
    *,
    raw_sha256: str,
    as_of_date: date,
    keyword: str,
) -> dict[str, object] | None:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextCollectionError("CNINFO sentiment response is not JSON") from exc
    if not isinstance(payload, dict):
        raise ContextCollectionError("CNINFO sentiment response contract drift")
    announcements = payload.get("announcements")
    if announcements is None:
        announcements = []
    if not isinstance(announcements, list):
        raise ContextCollectionError("CNINFO sentiment announcements contract drift")
    for item in cast(list[object], announcements):
        if not isinstance(item, dict):
            raise ContextCollectionError("CNINFO sentiment announcement row is invalid")
        if str(item.get("secCode") or "").strip() != ORIENT_CABLE_CODE:
            continue
        title = " ".join(html.unescape(_TAG_RE.sub("", str(item.get("announcementTitle") or ""))).split())
        content = " ".join(html.unescape(_TAG_RE.sub("", str(item.get("announcementContent") or ""))).split())
        combined = f"{title}：{content}".strip("：")
        if not content or keyword not in combined or _PERSISTENT_RE.search(combined) is None:
            continue
        try:
            source_date = _announcement_date(item.get("announcementTime"))
        except ContextCollectionError:
            continue
        if not as_of_date - timedelta(days=30) <= source_date <= as_of_date:
            continue
        announcement_id = str(item.get("announcementId") or "").strip()
        if not announcement_id:
            continue
        suffix = hashlib.sha256(f"{announcement_id}\n{combined}".encode()).hexdigest()[:20]
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


class _AttemptLedger:
    def __init__(
        self,
        authorization: ProductionContextAuthorization,
        run_root: Path,
        fetcher: OfficialFetcher,
        prior_http_attempts: int,
        prior_pdf_attempts: int,
    ) -> None:
        self.authorization = authorization
        self.run_root = run_root
        self.fetcher = fetcher
        self.prior_http_attempts = prior_http_attempts
        self.prior_pdf_attempts = prior_pdf_attempts
        self.operations: list[dict[str, object]] = []
        self.pdf_downloads = 0

    def get(self, url: str, *, label: str, expect_pdf: bool = False) -> tuple[bytes, str]:
        if self.prior_http_attempts + len(self.operations) >= self.authorization.http_attempt_limit:
            raise ContextCollectionError("official source HTTP attempt budget exhausted")
        if expect_pdf and self.prior_pdf_attempts + self.pdf_downloads >= self.authorization.pdf_download_limit:
            raise ContextCollectionError("official PDF download budget exhausted")
        _validated_url(url, self.authorization.allowed_hosts)
        result = self.fetcher(url, self.authorization.allowed_hosts)
        if expect_pdf:
            self.pdf_downloads += 1
        sequence = len(self.operations) + 1
        digest = hashlib.sha256(result.body).hexdigest() if result.body else None
        suffix = ".pdf" if expect_pdf else ".bin"
        raw_path = f"raw/{sequence:02d}-{label}{suffix}" if result.body else None
        if raw_path is not None:
            _secure_write(self.run_root / raw_path, result.body)
        operation: dict[str, object] = {
            "attempt": sequence,
            "byte_count": len(result.body),
            "content_type": result.content_type,
            "error_class": result.error_class,
            "final_url": result.final_url,
            "label": label,
            "pdf_download": expect_pdf,
            "raw_path": raw_path,
            "raw_sha256": digest,
            "requested_url": url,
            "status_code": result.status_code,
        }
        self.operations.append(operation)
        receipt = {
            "attempt": self.prior_http_attempts + sequence,
            "authorization_id": self.authorization.authorization_id,
            "byte_count": len(result.body),
            "error_class": result.error_class,
            "final_url": result.final_url,
            "label": label,
            "pdf_download": expect_pdf,
            "raw_sha256": digest,
            "status_code": result.status_code,
        }
        _secure_write(
            self.run_root / "receipts" / f"{self.prior_http_attempts + sequence:03d}.json",
            _canonical_bytes(receipt),
        )
        if result.requested_url != url or result.final_url != url:
            raise ContextCollectionError("official transport URL identity drift or redirect")
        if result.status_code != 200 or result.error_class is not None or not result.body or digest is None:
            raise ContextCollectionError(f"official source request failed: {label}")
        return result.body, digest


def collect_orient_cable_context(
    conn: sqlite3.Connection,
    *,
    authorization: ProductionContextAuthorization,
    run_root: Path,
    as_of_date: date,
    prior_http_attempts: int = 0,
    prior_pdf_attempts: int = 0,
    fetcher: OfficialFetcher = fetch_official,
) -> dict[str, object]:
    """Collect one sealed 603606 context under the exact approved pilot grant."""
    if (
        authorization.scope != "orient-cable"
        or authorization.target_codes != (ORIENT_CABLE_CODE,)
        or authorization.as_of_date != as_of_date.isoformat()
    ):
        raise ContextCollectionError("orient-cable collector authorization drift")
    if isinstance(prior_http_attempts, bool) or not 0 <= prior_http_attempts < authorization.http_attempt_limit:
        raise ContextCollectionError("no authorized official source attempts remain")
    if isinstance(prior_pdf_attempts, bool) or not 0 <= prior_pdf_attempts < authorization.pdf_download_limit:
        raise ContextCollectionError("no authorized official PDF attempts remain")
    ledger = _AttemptLedger(authorization, run_root, fetcher, prior_http_attempts, prior_pdf_attempts)
    evidence: list[dict[str, object]] = []
    fundamental = _fundamental_evidence(conn, as_of_date)
    if fundamental is not None:
        evidence.append(fundamental)

    # Runs 01/02 already sealed the official ttz2 -> ajax_down_list ->
    # ajax_down_view lookup. Reuse the official CNINFO report-summary URL
    # recorded during discovery rather than spend three more attempts.
    _validated_url(ANNUAL_REPORT_URL, authorization.allowed_hosts)
    annual_pdf, annual_sha = ledger.get(ANNUAL_REPORT_URL, label="annual-report", expect_pdf=True)
    pages = _pdf_pages(annual_pdf)
    moat = _pdf_evidence(
        pages,
        raw_sha256=annual_sha,
        dimension="moat",
        claim_category="competitive_moat",
        patterns=("拥有 500kV", "±535kV", "DNV 认证"),
    )
    market_pos = _pdf_evidence(
        pages,
        raw_sha256=annual_sha,
        dimension="market_pos",
        claim_category="industry_position",
        patterns=("国内陆缆系统、海缆系统核心供应商", "最具竞争力企业 10 强"),
    )

    # The profile page is retained as a transport-capability observation only.
    # It has no independently verifiable publication date, so it cannot supply
    # freshness-bound score evidence.
    ledger.get(ABOUT_PAGE, label="company-about")
    if moat is not None:
        evidence.append(moat)
    if market_pos is not None:
        evidence.append(market_pos)

    news_html, _ = ledger.get(NEWS_PAGE, label="company-news-index")
    news_text = _clean_html(news_html)
    recent_news_dates = sorted(
        {
            match.group(0)
            for match in re.finditer(r"2026[.-](?:0[6-7])[.-](?:0[1-9]|[12]\d|3[01])", news_text)
            if (as_of_date - timedelta(days=30)).isoformat()
            <= match.group(0).replace(".", "-")
            <= as_of_date.isoformat()
        }
    )

    sentiment = None
    for keyword in ("合同期限", "中标", "在手订单"):
        body, raw_sha = ledger.get(_cninfo_url(keyword, as_of_date), label=f"cninfo-{keyword}")
        candidate = _sentiment_from_cninfo(
            body,
            raw_sha256=raw_sha,
            as_of_date=as_of_date,
            keyword=keyword,
        )
        if sentiment is None and candidate is not None:
            sentiment = candidate
    if sentiment is not None:
        evidence.append(sentiment)

    context: dict[str, object] = {
        "code": ORIENT_CABLE_CODE,
        "name": ORIENT_CABLE_NAME,
        "industry": ORIENT_CABLE_INDUSTRY,
        "as_of_date": as_of_date.isoformat(),
        "schema_version": "qualitative-score-v2",
        "rubric_version": "rubric-v1",
        "taxonomy_version": "taxonomy-v1",
        "evidence": evidence,
    }
    validation = validate_context_dict(context)
    if not validation.valid or validation.context is None:
        raise ContextCollectionError(f"built orient-cable context is invalid: {validation.rejection_reason}")
    missing = list(context_missing_score_dimensions(validation.context))
    scoreable = [dimension for dimension in ("moat", "market_pos", "sentiment") if dimension not in missing]
    input_hash = validation.context.compute_input_hash()
    _secure_write(run_root / "contexts" / f"{ORIENT_CABLE_CODE}.json", _canonical_bytes(context))
    manifest: dict[str, object] = {
        "schema_version": "qualitative-v2-orient-cable-pilot-v1",
        "authorization_id": authorization.authorization_id,
        "as_of_date": as_of_date.isoformat(),
        "code": ORIENT_CABLE_CODE,
        "context_input_hash": input_hash,
        "database_reads": 1,
        "database_writes": 0,
        "gemini_calls": 0,
        "http_attempt_limit": authorization.http_attempt_limit,
        "http_attempts": len(ledger.operations),
        "prior_http_attempts": prior_http_attempts,
        "cumulative_http_attempts": prior_http_attempts + len(ledger.operations),
        "missing_score_dimensions": missing,
        "scoreable_dimensions": scoreable,
        "hybrid_ready": bool(scoreable),
        "model_call_limit": authorization.gemini_logical_call_limit,
        "operations": ledger.operations,
        "pdf_download_limit": authorization.pdf_download_limit,
        "pdf_downloads": ledger.pdf_downloads,
        "prior_pdf_downloads": prior_pdf_attempts,
        "cumulative_pdf_downloads": prior_pdf_attempts + ledger.pdf_downloads,
        "recent_company_news_dates": recent_news_dates,
        "score_ready": not missing,
    }
    manifest_raw = _canonical_bytes(manifest)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    _secure_write(run_root / "manifest.json", manifest_raw)
    _secure_write(run_root / "manifest.sha256", f"{manifest_sha}  manifest.json\n".encode("ascii"))
    return {
        "authorization_id": authorization.authorization_id,
        "code": ORIENT_CABLE_CODE,
        "contexts_path": str(run_root / "contexts"),
        "http_attempts": len(ledger.operations),
        "cumulative_http_attempts": prior_http_attempts + len(ledger.operations),
        "manifest_sha256": manifest_sha,
        "missing_score_dimensions": missing,
        "scoreable_dimensions": scoreable,
        "hybrid_ready": bool(scoreable),
        "pdf_downloads": ledger.pdf_downloads,
        "cumulative_pdf_downloads": prior_pdf_attempts + ledger.pdf_downloads,
        "score_ready": not missing,
        "status": (
            "READY_FOR_MODEL" if not missing else "READY_FOR_HYBRID_MODEL" if scoreable else "INSUFFICIENT_EVIDENCE"
        ),
    }


__all__ = [
    "OfficialFetchResult",
    "collect_orient_cable_context",
    "create_pilot_run_root",
    "fetch_official",
    "load_prior_source_attempts",
    "load_prior_pdf_attempts",
]
