"""Capture the authorized official source front doors for the M5 data Sprint."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from a_stock_tracker.qualitative.m5.pipeline import (
    MAX_JSON_BYTES,
    EXPECTED_SAMPLE_SHA256,
    _canonical_json,
    _read_regular_file,
)
from a_stock_tracker.qualitative.m5.data_auth import (
    MAX_RESPONSE_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    DataAuthorization,
    load_data_authorization,
    preflight_data_authorization,
)
from a_stock_tracker.paths import PROJECT_ROOT

SCHEMA_VERSION = "m5-source-frontdoors-v1"
FRONT_DOOR_PAGES: tuple[tuple[str, str], ...] = (
    ("CNINFO", "https://www.cninfo.com.cn/new/fulltextSearch"),
    ("SSE", "https://www.sse.com.cn/disclosure/listedinfo/announcement/"),
    ("SZSE", "https://www.szse.cn/disclosure/listed/bulletin/index.html"),
    ("CNIPA", "https://pss-system.cponline.cnipa.gov.cn/conventionalSearch"),
)
UI_ASSETS: tuple[tuple[str, str, str], ...] = (
    (
        "CNINFO",
        "https://static.cninfo.com.cn/new/js/app/disclosure/fulltextsearch/search_new.js?v=20260713100600",
        "cninfo-search-controller.js",
    ),
    (
        "SSE",
        "https://www.sse.com.cn/xhtml/home/2021public/querySearch/search_listedCompanyInfo_2021.js",
        "sse-search-controller.js",
    ),
)
_SOURCE_HOSTS = {
    "CNINFO": "cninfo.com.cn",
    "SSE": "sse.com.cn",
    "SZSE": "szse.cn",
    "CNIPA": "cponline.cnipa.gov.cn",
}
_MIME_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+*-]+/[A-Za-z0-9!#$&^_.+*-]+$")


class SourceFrontdoorError(ValueError):
    """The capture would violate the sealed D1 boundary."""


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Bounded response returned by an official-front-door transport."""

    requested_url: str
    final_url: str
    status_code: int | None
    content_type: str | None
    body: bytes
    http_attempts: int
    redirect_count: int
    error_class: str | None = None


Fetcher = Callable[[str], FetchResult]


def _canonical_bytes(value: object) -> bytes:
    return (_canonical_json(value) + "\n").encode("utf-8")


def _official_host(source: str, url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    root = _SOURCE_HOSTS.get(source)
    if parsed.scheme != "https" or root is None or not (host == root or host.endswith("." + root)):
        raise SourceFrontdoorError(f"non-official HTTPS URL for {source}")
    if parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443):
        raise SourceFrontdoorError(f"unsafe authority for {source}")
    return host


class _SameOriginRedirectHandler(HTTPRedirectHandler):
    def __init__(self, original_url: str) -> None:
        super().__init__()
        self._original_host = _official_host(_source_for_url(original_url), original_url)
        self.redirect_count = 0

    def redirect_request(  # type: ignore[no-untyped-def]
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        parsed = urlsplit(newurl)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or host != self._original_host or parsed.port not in (None, 443):
            raise SourceFrontdoorError("official front door attempted a cross-origin or non-HTTPS redirect")
        self.redirect_count += 1
        if self.redirect_count > 5:
            raise SourceFrontdoorError("official front door exceeded the redirect limit")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _source_for_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    for source, root in _SOURCE_HOSTS.items():
        if host == root or host.endswith("." + root):
            return source
    raise SourceFrontdoorError("front-door URL is outside the approved official hosts")


def _content_type(headers: Mapping[str, str]) -> str | None:
    raw = headers.get("Content-Type") or headers.get("content-type")
    if raw is None:
        return None
    mime = raw.split(";", 1)[0].strip().lower()
    return mime if _MIME_RE.fullmatch(mime) else None


def fetch_official_frontdoor(url: str) -> FetchResult:
    """Fetch one approved front door with TLS verification and same-origin redirects."""
    source = _source_for_url(url)
    _official_host(source, url)
    redirect_handler = _SameOriginRedirectHandler(url)
    opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), redirect_handler)
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.1",
            "User-Agent": "a-stock-tracker-m5-data-readiness/1.0",
        },
    )
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise SourceFrontdoorError("official front-door response exceeds the authorized byte limit")
            final_url = response.geturl()
            _official_host(source, final_url)
            return FetchResult(
                requested_url=url,
                final_url=final_url,
                status_code=int(response.status),
                content_type=_content_type(cast(Mapping[str, str], response.headers)),
                body=body,
                http_attempts=1 + redirect_handler.redirect_count,
                redirect_count=redirect_handler.redirect_count,
            )
    except HTTPError as exc:
        body = exc.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            body = b""
        final_url = exc.geturl()
        _official_host(source, final_url)
        return FetchResult(
            requested_url=url,
            final_url=final_url,
            status_code=exc.code,
            content_type=_content_type(cast(Mapping[str, str], exc.headers)),
            body=body,
            http_attempts=1 + redirect_handler.redirect_count,
            redirect_count=redirect_handler.redirect_count,
            error_class=f"http_{exc.code}",
        )
    except (OSError, URLError, ssl.SSLError, SourceFrontdoorError) as exc:
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=None,
            content_type=None,
            body=b"",
            http_attempts=1 + redirect_handler.redirect_count,
            redirect_count=redirect_handler.redirect_count,
            error_class=type(exc).__name__,
        )


def _safe_attempt_root(authorization: DataAuthorization, current: datetime, *, stage: str) -> Path:
    if stage not in {"source-frontdoors", "source-ui-assets"}:
        raise SourceFrontdoorError("unknown source-capture stage")
    relative = Path(authorization.artifact_root)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != authorization.artifact_root:
        raise SourceFrontdoorError("authorization artifact root is unsafe")
    if current.utcoffset() != timedelta(hours=8):
        current = current.astimezone(authorization.not_before.tzinfo)
    attempt_root = PROJECT_ROOT / relative / stage / current.strftime("%Y%m%d")
    project = PROJECT_ROOT.resolve(strict=True)
    cursor = PROJECT_ROOT
    for part in attempt_root.relative_to(PROJECT_ROOT).parts:
        cursor /= part
        if cursor.is_symlink():
            raise SourceFrontdoorError("front-door artifact ancestry contains a symlink")
        if cursor.exists() and not cursor.is_dir():
            raise SourceFrontdoorError("front-door artifact ancestry contains a non-directory")
    if attempt_root.exists() or attempt_root.is_symlink():
        raise SourceFrontdoorError(f"{stage} capture already exists for this local date")
    if project not in attempt_root.resolve(strict=False).parents:
        raise SourceFrontdoorError("front-door artifact root escapes the project")
    return attempt_root


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


def _make_private_tree(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    cursor = path
    data_run_root = path.parents[1]
    while True:
        metadata = cursor.stat(follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode) or cursor.is_symlink():
            raise SourceFrontdoorError("front-door artifact tree is unsafe")
        cursor.chmod(0o700)
        if cursor == data_run_root:
            break
        cursor = cursor.parent


def _capture_resources(
    attempt_root: Path,
    resources: tuple[tuple[str, str, str], ...],
    fetcher: Fetcher,
) -> tuple[list[dict[str, object]], int]:
    records: list[dict[str, object]] = []
    total_attempts = 0
    for source, url, filename in resources:
        result = fetcher(url)
        try:
            final_source = _source_for_url(result.final_url)
        except SourceFrontdoorError as exc:
            raise SourceFrontdoorError("source transport returned mismatched source identity") from exc
        if result.requested_url != url or final_source != source:
            raise SourceFrontdoorError("source transport returned mismatched source identity")
        _official_host(source, result.final_url)
        if result.http_attempts < 1 or result.redirect_count < 0:
            raise SourceFrontdoorError("source transport returned invalid attempt counts")
        if len(result.body) > MAX_RESPONSE_BYTES:
            raise SourceFrontdoorError("source transport exceeded the response limit")
        total_attempts += result.http_attempts
        raw_path: str | None = None
        raw_sha256: str | None = None
        if result.body:
            _secure_write(attempt_root / filename, result.body)
            raw_path = filename
            raw_sha256 = hashlib.sha256(result.body).hexdigest()
        success = (
            result.error_class is None
            and result.status_code is not None
            and 200 <= result.status_code < 300
            and bool(result.body)
        )
        records.append(
            {
                "source": source,
                "requested_url": url,
                "final_url": result.final_url,
                "status_code": result.status_code,
                "content_type": result.content_type,
                "byte_count": len(result.body),
                "raw_path": raw_path,
                "raw_sha256": raw_sha256,
                "http_attempts": result.http_attempts,
                "redirect_count": result.redirect_count,
                "status": "success" if success else "technical_error",
                "sanitized_error_class": None if success else result.error_class or "invalid_response",
            }
        )
    return records, total_attempts


def capture_source_frontdoors(
    sample_path: str | os.PathLike[str],
    authorization_path: str | os.PathLike[str],
    checksum_path: str | os.PathLike[str],
    *,
    now: datetime | None = None,
    fetcher: Fetcher = fetch_official_frontdoor,
) -> dict[str, object]:
    """Capture four official front doors into one create-only, authorization-bound attempt."""
    sample = Path(sample_path)
    authorization = load_data_authorization(sample, authorization_path, checksum_path)
    current = now or datetime.now().astimezone()
    preflight_data_authorization(sample, authorization_path, checksum_path, now=current, require_active=True)
    attempt_root = _safe_attempt_root(authorization, current, stage="source-frontdoors")
    _make_private_tree(attempt_root)
    try:
        resources = tuple((source, url, f"{source.lower()}-front-door.bin") for source, url in FRONT_DOOR_PAGES)
        records, total_attempts = _capture_resources(attempt_root, resources, fetcher)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "authorization_id": authorization.authorization_id,
            "authorization_sha256": authorization.sha256,
            "data_run_id": authorization.data_run_id,
            "sample_sha256": EXPECTED_SAMPLE_SHA256,
            "captured_at": current.astimezone(authorization.not_before.tzinfo).isoformat(timespec="seconds"),
            "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "http_attempts": total_attempts,
            "search_http_budget_consumed": total_attempts,
            "pages": records,
        }
        manifest_raw = _canonical_bytes(payload)
        _secure_write(attempt_root / "manifest.json", manifest_raw)
        success_count = sum(record["status"] == "success" for record in records)
        return {
            "validated": True,
            "attempt_root": str(attempt_root.relative_to(PROJECT_ROOT)),
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "source_count": len(records),
            "success_count": success_count,
            "technical_error_count": len(records) - success_count,
            "http_attempts": total_attempts,
            "model_calls": 0,
            "database_reads": 0,
        }
    except BaseException:
        # Preserve completed raw captures, but never publish a partial manifest as a successful attempt.
        raise


def capture_source_ui_assets(
    sample_path: str | os.PathLike[str],
    authorization_path: str | os.PathLike[str],
    checksum_path: str | os.PathLike[str],
    *,
    now: datetime | None = None,
    fetcher: Fetcher = fetch_official_frontdoor,
) -> dict[str, object]:
    """Capture the two query-controller scripts referenced by the successful official pages."""
    sample = Path(sample_path)
    authorization = load_data_authorization(sample, authorization_path, checksum_path)
    current = now or datetime.now().astimezone()
    preflight_data_authorization(sample, authorization_path, checksum_path, now=current, require_active=True)
    if current.utcoffset() != timedelta(hours=8):
        current = current.astimezone(authorization.not_before.tzinfo)
    frontdoor_manifest = (
        PROJECT_ROOT / authorization.artifact_root / "source-frontdoors" / current.strftime("%Y%m%d") / "manifest.json"
    )
    try:
        frontdoor_raw = _read_regular_file(frontdoor_manifest, maximum_bytes=MAX_JSON_BYTES)
    except (OSError, ValueError) as exc:
        raise SourceFrontdoorError("sealed front-door manifest is missing") from exc
    frontdoor_value = json.loads(frontdoor_raw)
    if (
        not isinstance(frontdoor_value, dict)
        or frontdoor_value.get("schema_version") != SCHEMA_VERSION
        or frontdoor_value.get("authorization_sha256") != authorization.sha256
        or frontdoor_raw != _canonical_bytes(frontdoor_value)
    ):
        raise SourceFrontdoorError("front-door manifest is non-canonical or authorization-drifted")
    pages = frontdoor_value.get("pages")
    if not isinstance(pages, list):
        raise SourceFrontdoorError("front-door manifest pages are invalid")
    successful_sources = {
        item.get("source") for item in pages if isinstance(item, dict) and item.get("status") == "success"
    }
    if not {"CNINFO", "SSE"} <= successful_sources:
        raise SourceFrontdoorError("required source pages were not successfully captured")
    pages_by_source = {
        item["source"]: item for item in pages if isinstance(item, dict) and isinstance(item.get("source"), str)
    }
    for source, asset_url, _filename in UI_ASSETS:
        page = pages_by_source.get(source)
        if not isinstance(page, dict):
            raise SourceFrontdoorError("UI asset has no sealed parent page")
        raw_path = page.get("raw_path")
        if not isinstance(raw_path, str) or Path(raw_path).name != raw_path:
            raise SourceFrontdoorError("front-door manifest has an unsafe raw path")
        try:
            page_raw = _read_regular_file(frontdoor_manifest.parent / raw_path, maximum_bytes=MAX_RESPONSE_BYTES)
        except ValueError as exc:
            raise SourceFrontdoorError("sealed front-door raw bytes are missing") from exc
        if len(page_raw) != page.get("byte_count") or hashlib.sha256(page_raw).hexdigest() != page.get("raw_sha256"):
            raise SourceFrontdoorError("sealed front-door raw bytes drifted")
        parsed_asset = urlsplit(asset_url)
        path_query = parsed_asset.path + ("?" + parsed_asset.query if parsed_asset.query else "")
        references = (asset_url.encode(), ("//" + parsed_asset.netloc + path_query).encode(), path_query.encode())
        if not any(reference in page_raw for reference in references):
            raise SourceFrontdoorError("UI asset is not referenced by its sealed official page")

    attempt_root = _safe_attempt_root(authorization, current, stage="source-ui-assets")
    _make_private_tree(attempt_root)
    records, total_attempts = _capture_resources(attempt_root, UI_ASSETS, fetcher)
    payload = {
        "schema_version": "m5-source-ui-assets-v1",
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": authorization.sha256,
        "data_run_id": authorization.data_run_id,
        "sample_sha256": EXPECTED_SAMPLE_SHA256,
        "captured_at": current.isoformat(timespec="seconds"),
        "parent_frontdoors_manifest_sha256": hashlib.sha256(frontdoor_raw).hexdigest(),
        "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "http_attempts": total_attempts,
        "search_http_budget_consumed": total_attempts,
        "assets": records,
    }
    manifest_raw = _canonical_bytes(payload)
    _secure_write(attempt_root / "manifest.json", manifest_raw)
    success_count = sum(record["status"] == "success" for record in records)
    return {
        "validated": True,
        "attempt_root": str(attempt_root.relative_to(PROJECT_ROOT)),
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "parent_frontdoors_manifest_sha256": hashlib.sha256(frontdoor_raw).hexdigest(),
        "asset_count": len(records),
        "success_count": success_count,
        "technical_error_count": len(records) - success_count,
        "http_attempts": total_attempts,
        "model_calls": 0,
        "database_reads": 0,
    }


__all__ = [
    "FRONT_DOOR_PAGES",
    "UI_ASSETS",
    "FetchResult",
    "SourceFrontdoorError",
    "capture_source_frontdoors",
    "capture_source_ui_assets",
    "fetch_official_frontdoor",
]
