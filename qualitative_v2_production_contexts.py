"""Bounded CNINFO snippet collector for the qualitative-v2 production fast lane."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

from qualitative_v2_validator import validate_context_dict

AUTHORIZATION_ID = "qualitative-v2-prod-canary-20260719-01"
CNINFO_ENDPOINT = "https://www.cninfo.com.cn/new/fulltextSearch/full"
CNINFO_HOST = "www.cninfo.com.cn"
MAX_OPERATION_ATTEMPTS = 3
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_RETAINED_SNIPPETS = 3
CANARY_HTTP_LIMIT = 45
ALL_HTTP_LIMIT = 315
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_PERSISTENT_RE = re.compile(r"(合同期限|履行期限|有效期|持续|长期|[一二三四五六七八九十\d]+年|\d+个月)")

QUERY_SPECS: tuple[tuple[str, str, str, str, int], ...] = (
    ("moat", "核心技术", "competitive_moat", "max_age_365d", 365),
    ("market_pos", "市场占有率", "industry_position", "max_age_365d", 365),
    ("sentiment", "合同期限", "market_sentiment", "max_age_30d", 30),
)


class ContextCollectionError(ValueError):
    """The collection request or artifact would violate the approved boundary."""


@dataclass(frozen=True, slots=True)
class SnippetFetchResult:
    """One non-redirecting CNINFO HTTP attempt."""

    requested_url: str
    final_url: str
    status_code: int | None
    content_type: str | None
    body: bytes
    error_class: str | None = None


Fetcher = Callable[[str], SnippetFetchResult]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def fetch_cninfo_snippets(url: str) -> SnippetFetchResult:
    """Perform one bounded official CNINFO GET without following redirects."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != CNINFO_HOST or parsed.port not in (None, 443):
        raise ContextCollectionError("CNINFO request URL is outside the approved endpoint")
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect()
    )
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "a-stock-tracker-qualitative-v2-fast-lane/1.0",
        },
    )
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ContextCollectionError("CNINFO response exceeds the byte limit")
            return SnippetFetchResult(
                requested_url=url,
                final_url=response.geturl(),
                status_code=int(response.status),
                content_type=response.headers.get_content_type(),
                body=body,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            body = b""
        return SnippetFetchResult(url, exc.geturl(), exc.code, exc.headers.get_content_type(), body, f"http_{exc.code}")
    except (OSError, urllib.error.URLError, ssl.SSLError, ContextCollectionError) as exc:
        return SnippetFetchResult(url, url, None, None, b"", type(exc).__name__)


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


def create_context_run_root(project_root: Path, run_id: str) -> Path:
    """Create one private, project-contained, create-only context run root."""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id) is None or run_id in {".", ".."}:
        raise ContextCollectionError("run-id must be a bounded safe identifier")
    base = project_root / "artifacts" / "qualitative-v2-production-contexts"
    cursor = project_root
    for part in base.relative_to(project_root).parts:
        cursor /= part
        if cursor.is_symlink():
            raise ContextCollectionError("context artifact ancestry contains a symlink")
        if cursor.exists() and not cursor.is_dir():
            raise ContextCollectionError("context artifact ancestry contains a non-directory")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.chmod(0o700)
    root = base / run_id
    if root.exists() or root.is_symlink():
        raise ContextCollectionError("context run-id is create-only")
    root.mkdir(mode=0o700)
    (root / "raw").mkdir(mode=0o700)
    (root / "contexts").mkdir(mode=0o700)
    return root


def load_prior_http_attempts(project_root: Path, *, authorization_id: str, scope: str) -> int:
    """Rebuild cumulative HTTP usage from every sealed create-only context run."""
    base = project_root / "artifacts" / "qualitative-v2-production-contexts"
    if not base.exists():
        return 0
    if base.is_symlink() or not base.is_dir():
        raise ContextCollectionError("context artifact base is unsafe")
    total = 0
    for root in sorted(base.iterdir()):
        if root.is_symlink() or not root.is_dir():
            raise ContextCollectionError("context artifact run root is unsafe")
        manifest_path = root / "manifest.json"
        checksum_path = root / "manifest.sha256"
        if manifest_path.is_symlink() or checksum_path.is_symlink() or not manifest_path.is_file():
            raise ContextCollectionError("prior context run is incomplete or unsafe")
        raw = manifest_path.read_bytes()
        try:
            manifest = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ContextCollectionError("prior context manifest is invalid") from exc
        if not isinstance(manifest, dict) or raw != _canonical_bytes(manifest):
            raise ContextCollectionError("prior context manifest is non-canonical")
        digest = hashlib.sha256(raw).hexdigest()
        if not checksum_path.is_file() or checksum_path.read_bytes() != f"{digest}  manifest.json\n".encode("ascii"):
            raise ContextCollectionError("prior context manifest checksum drift")
        if manifest.get("schema_version") != "qualitative-v2-production-contexts-v1":
            raise ContextCollectionError("prior context manifest schema drift")
        if manifest.get("authorization_id") != authorization_id:
            continue
        if manifest.get("scope") not in {"canary", "all"}:
            raise ContextCollectionError("prior context manifest scope drift")
        operations = manifest.get("operations")
        attempts = manifest.get("http_attempts")
        if not isinstance(operations, list) or isinstance(attempts, bool) or not isinstance(attempts, int):
            raise ContextCollectionError("prior context attempt accounting is invalid")
        rebuilt = 0
        for operation in operations:
            if not isinstance(operation, dict) or not isinstance(operation.get("attempts"), list):
                raise ContextCollectionError("prior context operation accounting is invalid")
            rebuilt += len(operation["attempts"])
        if rebuilt != attempts or attempts < 0:
            raise ContextCollectionError("prior context HTTP total drift")
        total += attempts
    return total


def _search_url(name: str, query: str, start: date, end: date) -> str:
    params = {
        # CNINFO full-text search indexes issuer names and disclosure text.
        # A space preserves this as one UI keyword expression; commas are
        # reserved by the official controller for separate history entries.
        "searchkey": f"{name} {query}",
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


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = html.unescape(_HTML_TAG_RE.sub("", value))
    return " ".join(text.split())


def _announcement_date(value: object) -> date:
    if isinstance(value, bool):
        raise ContextCollectionError("announcement time has invalid type")
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone(timedelta(hours=8))).date()
    if isinstance(value, str):
        normalized = value.strip().replace("/", "-")
        try:
            return date.fromisoformat(normalized[:10])
        except ValueError as exc:
            raise ContextCollectionError("announcement time is invalid") from exc
    raise ContextCollectionError("announcement time is missing")


def _parse_snippets(
    body: bytes,
    *,
    code: str,
    name: str,
    dimension: str,
    query: str,
    claim_category: str,
    freshness_policy: str,
    start: date,
    end: date,
    raw_sha256: str,
) -> list[dict[str, object]]:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextCollectionError("CNINFO response is not JSON") from exc
    if not isinstance(value, dict):
        raise ContextCollectionError("CNINFO response contract drift")
    announcements_raw = value.get("announcements")
    if announcements_raw is None:
        announcements_raw = []
    if not isinstance(announcements_raw, list):
        raise ContextCollectionError("CNINFO announcements contract drift")
    evidence: list[dict[str, object]] = []
    for item in cast(list[object], announcements_raw):
        if not isinstance(item, dict):
            raise ContextCollectionError("CNINFO announcement row is invalid")
        if str(item.get("secCode") or "").strip() != code or _clean_text(item.get("secName")) != name:
            continue
        title = _clean_text(item.get("announcementTitle"))
        snippet = _clean_text(item.get("announcementContent"))
        combined = f"{title}：{snippet}".strip("：")
        if query not in combined or not snippet:
            continue
        source_date = _announcement_date(item.get("announcementTime"))
        if not start <= source_date <= end:
            continue
        announcement_id = str(item.get("announcementId") or "").strip()
        if not announcement_id:
            raise ContextCollectionError("CNINFO announcement identity is missing")
        suffix = hashlib.sha256(f"{dimension}\n{announcement_id}\n{combined}".encode()).hexdigest()[:20]
        source = (
            f"CNINFO fulltextSearch/full;response_sha256={raw_sha256};"
            f"locator=announcementId:{announcement_id}/announcementContent"
        )
        row: dict[str, object] = {
            "evidence_id": f"disclosure.{dimension}.{suffix}",
            "evidence_type": "company_disclosure",
            "claim_category": claim_category,
            "allowed_dimensions": [dimension],
            "directness": "direct",
            "freshness_policy": freshness_policy,
            "value": combined[:4000],
            "unit": None,
            "source": source,
            "source_date": source_date.isoformat(),
            "freshness_status": "fresh",
        }
        if dimension == "sentiment":
            persistent = _PERSISTENT_RE.search(combined) is not None
            row["persistence_horizon"] = "multi_quarter" if persistent else "one_time"
            row["materiality"] = "major" if "重大" in combined else "moderate"
        evidence.append(row)
        if len(evidence) == MAX_RETAINED_SNIPPETS:
            break
    return evidence


def _fundamental_evidence(row: sqlite3.Row | tuple[object, ...] | None, as_of_date: date) -> list[dict[str, object]]:
    if row is None:
        return []
    raw_data = row[0]
    if not isinstance(raw_data, str):
        return []
    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    roe = data.get("roe_3y_avg")
    report_period = data.get("report_period")
    if isinstance(roe, bool) or not isinstance(roe, (int, float)) or not isinstance(report_period, str):
        return []
    try:
        source_date = date.fromisoformat(f"{report_period}-12-31" if len(report_period) == 4 else report_period[:10])
    except ValueError:
        return []
    if source_date > as_of_date or (as_of_date - source_date).days > 550:
        return []
    subset = {"report_period": report_period, "roe_3y_avg": float(roe)}
    digest = hashlib.sha256(_canonical_bytes(subset)).hexdigest()
    return [
        {
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
    ]


def collect_production_contexts(
    conn: sqlite3.Connection,
    companies: Mapping[str, tuple[str, str]],
    *,
    scope: str,
    run_root: Path,
    as_of_date: date,
    prior_http_attempts: int = 0,
    fetcher: Fetcher = fetch_cninfo_snippets,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Collect exactly three CNINFO queries per company and seal valid contexts."""
    if scope not in {"canary", "all"}:
        raise ContextCollectionError("scope must be canary or all")
    expected_count = 5 if scope == "canary" else 35
    if len(companies) != expected_count:
        raise ContextCollectionError(f"{scope} scope must contain exactly {expected_count} companies")
    http_limit = CANARY_HTTP_LIMIT if scope == "canary" else ALL_HTTP_LIMIT
    if isinstance(prior_http_attempts, bool) or not 0 <= prior_http_attempts < http_limit:
        raise ContextCollectionError("no authorized CNINFO HTTP attempts remain")
    placeholders = ",".join("?" for _ in companies)
    fundamentals = {
        str(code): (data,)
        for code, data in conn.execute(
            f"SELECT code, data FROM stock_fundamentals WHERE code IN ({placeholders})", tuple(sorted(companies))
        ).fetchall()
    }
    records: list[dict[str, object]] = []
    context_hashes: dict[str, str] = {}
    total_attempts = 0
    for code in sorted(companies):
        name, industry = companies[code]
        evidence = _fundamental_evidence(fundamentals.get(code), as_of_date)
        for dimension, query, claim_category, freshness_policy, window_days in QUERY_SPECS:
            start = as_of_date - timedelta(days=window_days)
            url = _search_url(name, query, start, as_of_date)
            operation_record: dict[str, object] = {
                "code": code,
                "dimension": dimension,
                "query": query,
                "requested_url": url,
                "attempts": [],
                "status": "technical_error",
                "evidence_count": 0,
            }
            for attempt in range(1, MAX_OPERATION_ATTEMPTS + 1):
                if prior_http_attempts + total_attempts >= http_limit:
                    raise ContextCollectionError("CNINFO HTTP attempt budget exhausted")
                result = fetcher(url)
                total_attempts += 1
                if result.requested_url != url or result.final_url != url:
                    raise ContextCollectionError("CNINFO transport URL identity drift or redirect")
                raw_path: str | None = None
                raw_sha256: str | None = None
                if result.body:
                    raw_sha256 = hashlib.sha256(result.body).hexdigest()
                    raw_path = f"raw/{code}-{dimension}-{attempt}.json"
                    _secure_write(run_root / raw_path, result.body)
                attempt_row = {
                    "attempt": attempt,
                    "status_code": result.status_code,
                    "content_type": result.content_type,
                    "byte_count": len(result.body),
                    "raw_path": raw_path,
                    "raw_sha256": raw_sha256,
                    "error_class": result.error_class,
                }
                cast(list[object], operation_record["attempts"]).append(attempt_row)
                success = result.status_code == 200 and result.error_class is None and bool(result.body)
                if success and raw_sha256 is not None:
                    try:
                        snippets = _parse_snippets(
                            result.body,
                            code=code,
                            name=name,
                            dimension=dimension,
                            query=query,
                            claim_category=claim_category,
                            freshness_policy=freshness_policy,
                            start=start,
                            end=as_of_date,
                            raw_sha256=raw_sha256,
                        )
                    except ContextCollectionError:
                        operation_record["status"] = "contract_error"
                    else:
                        evidence.extend(snippets)
                        operation_record["status"] = "success"
                        operation_record["evidence_count"] = len(snippets)
                    break
                retryable = (
                    result.status_code in {408, 429}
                    or (result.status_code is not None and result.status_code >= 500)
                    or result.status_code is None
                )
                if not retryable:
                    break
                if attempt < MAX_OPERATION_ATTEMPTS:
                    sleep(float(attempt))
            records.append(operation_record)
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
            raise ContextCollectionError(f"built context is invalid for {code}: {validation.rejection_reason}")
        context_hashes[code] = validation.context.compute_input_hash()
        _secure_write(run_root / "contexts" / f"{code}.json", _canonical_bytes(context))
    manifest: dict[str, object] = {
        "schema_version": "qualitative-v2-production-contexts-v1",
        "authorization_id": AUTHORIZATION_ID,
        "scope": scope,
        "as_of_date": as_of_date.isoformat(),
        "source": "CNINFO fulltextSearch/full",
        "query_count": len(companies) * len(QUERY_SPECS),
        "http_attempt_limit": http_limit,
        "http_attempts": total_attempts,
        "prior_http_attempts": prior_http_attempts,
        "cumulative_http_attempts": prior_http_attempts + total_attempts,
        "database_reads": 1,
        "database_writes": 0,
        "model_calls": 0,
        "context_hashes": context_hashes,
        "operations": records,
    }
    manifest_raw = _canonical_bytes(manifest)
    manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    _secure_write(run_root / "manifest.json", manifest_raw)
    _secure_write(run_root / "manifest.sha256", f"{manifest_sha256}  manifest.json\n".encode("ascii"))
    return {
        "authorization_id": AUTHORIZATION_ID,
        "contexts": len(context_hashes),
        "contexts_path": str(run_root / "contexts"),
        "http_attempt_limit": http_limit,
        "http_attempts": total_attempts,
        "cumulative_http_attempts": prior_http_attempts + total_attempts,
        "manifest_sha256": manifest_sha256,
        "queries": len(records),
        "queries_with_evidence": sum(cast(int, record["evidence_count"]) > 0 for record in records),
        "status": "COMPLETE",
    }


__all__ = [
    "AUTHORIZATION_ID",
    "ContextCollectionError",
    "SnippetFetchResult",
    "collect_production_contexts",
    "create_context_run_root",
    "fetch_cninfo_snippets",
    "load_prior_http_attempts",
]
