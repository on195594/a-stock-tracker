#!/usr/bin/env python3
"""Export one provenance-bearing AKShare snapshot for the M4 sampling frame."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a_stock_tracker.qualitative.audit import (  # noqa: E402
    SW2021_INDUSTRIES,
    FrameRow,
    canonical_json_bytes,
    load_preregistration_ref,
    select_sample,
    validate_frame,
)

AUTHORIZED_FUNCTIONS = frozenset(
    {"index_component_sw", "stock_zh_a_spot_em", "stock_sse_summary", "stock_szse_summary"}
)
ALLOWED_HOSTS: Mapping[str, frozenset[str]] = {
    "index_component_sw": frozenset({"www.swsresearch.com"}),
    "stock_zh_a_spot_em": frozenset({"82.push2.eastmoney.com"}),
    "stock_sse_summary": frozenset({"query.sse.com.cn"}),
    "stock_szse_summary": frozenset({"www.szse.cn"}),
}
AUTHORIZATION_ID = "qualitative-v2-m4-akshare-frame-export-2026-07-15-01"
LEGACY_EXPORT_DISABLED = "legacy M4 AKShare exporter is disabled; use the authorized capture-first 'capture' command"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_RESPONSE_BYTES = 256 * 1024 * 1024
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "milestone-004" / "incoming"
PROTOCOL_PATH = (
    PROJECT_ROOT / "docs" / "plans" / "2026-07-15-milestone-004-evidence-feasibility-preregistration-v1.1.md"
)
PROTOCOL_HASH_PATH = PROJECT_ROOT / "reviews" / "milestone-004-preregistration-v1.1" / "preregistration.sha256"
_TRANSPORT_PATCH_LOCK = threading.Lock()


class ExportError(RuntimeError):
    """Fail-closed error for an AKShare sampling-frame export."""


@dataclass(frozen=True, slots=True)
class HttpCapture:
    request_url: str
    request_params: Mapping[str, str]
    response_url: str
    status_code: int
    content_type: str
    raw: bytes


@dataclass(frozen=True, slots=True)
class CallResult:
    function_name: str
    arguments: Mapping[str, str]
    dataframe: Any
    captures: tuple[HttpCapture, ...]


@dataclass(frozen=True, slots=True)
class ExportResult:
    package_dir: Path
    frame_path: Path
    provenance_path: Path
    frame_sha256: str
    sampling_date: date
    row_count: int


class Runner(Protocol):
    version: str

    def call(self, function_name: str, arguments: Mapping[str, str]) -> CallResult: ...


SzseSource = Callable[[date], CallResult]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _https_url(raw_url: str, allowed_hosts: frozenset[str]) -> str:
    parts = urlsplit(raw_url)
    host = (parts.hostname or "").lower()
    if host not in allowed_hosts or parts.username is not None or parts.password is not None:
        raise ExportError(f"AKShare request escaped its authorized host: {host or '<missing>'}")
    if parts.port not in (None, 80, 443):
        raise ExportError("AKShare request used a non-default port")
    return urlunsplit(("https", host, parts.path or "/", parts.query, ""))


def _safe_params(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ExportError("AKShare request params must be a mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        name = str(key)
        if re.search(r"token|secret|password|api[_-]?key|credential", name, re.IGNORECASE):
            raise ExportError("AKShare request unexpectedly contains a credential field")
        result[name] = str(item)
    return result


class _TransportGuard:
    """Temporarily guard AKShare's requests calls in this single-purpose process."""

    def __init__(
        self,
        function_name: str,
        capture_callback: Callable[[str, HttpCapture], None] | None = None,
    ) -> None:
        self._function_name = function_name
        self._capture_callback = capture_callback
        self._captures: list[HttpCapture] = []
        self._requests: Any = None
        self._original_get: Any = None
        self._original_session_get: Any = None

    @property
    def captures(self) -> tuple[HttpCapture, ...]:
        return tuple(self._captures)

    def __enter__(self) -> _TransportGuard:
        _TRANSPORT_PATCH_LOCK.acquire()
        try:
            import requests  # type: ignore[import-untyped]
        except BaseException:
            _TRANSPORT_PATCH_LOCK.release()
            raise

        self._requests = requests
        self._original_get = requests.get
        self._original_session_get = requests.sessions.Session.get

        def guarded_get(url: str, **kwargs: object) -> Any:
            return self._request(self._original_get, None, url, kwargs)

        def guarded_session_get(session: object, url: str, **kwargs: object) -> Any:
            return self._request(self._original_session_get, session, url, kwargs)

        try:
            requests.get = guarded_get
            requests.sessions.Session.get = guarded_session_get
        except BaseException:
            _TRANSPORT_PATCH_LOCK.release()
            raise
        return self

    def _request(self, getter: Any, session: object | None, url: str, kwargs: Mapping[str, object]) -> Any:
        allowed_hosts = ALLOWED_HOSTS[self._function_name]
        safe_url = _https_url(str(url), allowed_hosts)
        call_kwargs = dict(kwargs)
        request_params = _safe_params(call_kwargs.get("params"))
        call_kwargs["verify"] = True
        call_kwargs["allow_redirects"] = False
        call_kwargs["stream"] = True
        call_kwargs.setdefault("timeout", 30)
        try:
            response = getter(safe_url, **call_kwargs) if session is None else getter(session, safe_url, **call_kwargs)
        except (OSError, self._requests.RequestException) as exc:
            raise ExportError(f"AKShare {self._function_name} transport failure: {type(exc).__name__}") from exc
        status = int(getattr(response, "status_code", 0))
        response_url = _https_url(str(getattr(response, "url", safe_url)), allowed_hosts)
        if not 200 <= status < 300:
            response.close()
            raise ExportError(f"AKShare {self._function_name} HTTP failure: status={status}")
        chunks: list[bytes] = []
        byte_count = 0
        aggregate_before = sum(len(item.raw) for item in self._captures)
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                byte_count += len(chunk)
                if byte_count > MAX_RESPONSE_BYTES:
                    raise ExportError(f"AKShare {self._function_name} response exceeds 32 MiB")
                if aggregate_before + byte_count > MAX_TOTAL_RESPONSE_BYTES:
                    raise ExportError("AKShare export responses exceed the 256 MiB aggregate limit")
                chunks.append(bytes(chunk))
        except BaseException:
            response.close()
            raise
        raw = b"".join(chunks)
        response._content = raw
        response._content_consumed = True
        headers = getattr(response, "headers", {})
        content_type = str(headers.get("Content-Type", "application/octet-stream")).split(";", 1)[0].strip().lower()
        capture = HttpCapture(safe_url, request_params, response_url, status, content_type, raw)
        self._captures.append(capture)
        if self._capture_callback is not None:
            self._capture_callback(self._function_name, capture)
        return response

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._requests is not None:
            self._requests.get = self._original_get
            self._requests.sessions.Session.get = self._original_session_get
            _TRANSPORT_PATCH_LOCK.release()


class AkshareRunner:
    """Invoke only the four explicitly authorized AKShare functions."""

    def __init__(self, capture_callback: Callable[[str, HttpCapture], None] | None = None) -> None:
        import akshare

        self._module = akshare
        self.version = str(akshare.__version__)
        self._captured_bytes = 0
        self._capture_callback = capture_callback

    def call(self, function_name: str, arguments: Mapping[str, str]) -> CallResult:
        if function_name not in AUTHORIZED_FUNCTIONS:
            raise ExportError(f"AKShare function is not authorized: {function_name}")
        expected_arguments = {
            "index_component_sw": frozenset({"symbol"}),
            "stock_zh_a_spot_em": frozenset(),
            "stock_sse_summary": frozenset(),
            "stock_szse_summary": frozenset({"date"}),
        }[function_name]
        if set(arguments) != expected_arguments:
            raise ExportError(f"unexpected arguments for AKShare {function_name}")
        function = getattr(self._module, function_name)
        with _TransportGuard(function_name, self._capture_callback) as guard:
            try:
                dataframe = function(**dict(arguments))
            except ExportError:
                raise
            except Exception as exc:
                raise ExportError(f"AKShare {function_name} failed: {type(exc).__name__}") from exc
        if not guard.captures:
            raise ExportError(f"AKShare {function_name} made no captured HTTP request")
        captured_bytes = sum(len(item.raw) for item in guard.captures)
        if self._captured_bytes + captured_bytes > MAX_TOTAL_RESPONSE_BYTES:
            raise ExportError("AKShare export responses exceed the 256 MiB aggregate limit")
        self._captured_bytes += captured_bytes
        return CallResult(function_name, dict(arguments), dataframe, guard.captures)


def _records(result: CallResult, required_columns: Sequence[str]) -> list[dict[str, object]]:
    dataframe = result.dataframe
    columns = [str(item) for item in getattr(dataframe, "columns", [])]
    if len(columns) != len(set(columns)) or not set(required_columns) <= set(columns):
        raise ExportError(f"AKShare {result.function_name} output schema drift: {columns}")
    try:
        records = dataframe.to_dict(orient="records")
    except Exception as exc:
        raise ExportError(f"AKShare {result.function_name} did not return a DataFrame") from exc
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ExportError(f"AKShare {result.function_name} returned malformed records")
    return records


def _call(runner: Runner, function_name: str, arguments: Mapping[str, str]) -> CallResult:
    if function_name not in AUTHORIZED_FUNCTIONS:
        raise ExportError(f"AKShare function is not authorized: {function_name}")
    result = runner.call(function_name, arguments)
    if result.function_name != function_name or dict(result.arguments) != dict(arguments):
        raise ExportError(f"AKShare runner returned mismatched provenance for {function_name}")
    if not result.captures:
        raise ExportError(f"AKShare {function_name} returned no raw HTTP capture")
    return result


def _direct_szse_call(source: SzseSource, sampling_date: date) -> CallResult:
    result = source(sampling_date)
    expected_arguments = {"date": sampling_date.isoformat()}
    if result.function_name != "szse_https_market_overview" or dict(result.arguments) != expected_arguments:
        raise ExportError("direct SZSE source returned mismatched provenance")
    if not result.captures:
        raise ExportError("direct SZSE source returned no raw HTTPS capture")
    for capture in result.captures:
        request = urlsplit(capture.request_url)
        response = urlsplit(capture.response_url)
        if (
            request.scheme != "https"
            or response.scheme != "https"
            or request.hostname != "www.szse.cn"
            or response.hostname != "www.szse.cn"
        ):
            raise ExportError("direct SZSE capture escaped its approved HTTPS origin")
    return result


def _decimal(value: object, *, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ExportError(f"missing or invalid decimal for {label}")
    text = str(value).strip()
    if text in {"", "-", "nan", "None"}:
        raise ExportError(f"missing or invalid decimal for {label}")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ExportError(f"missing or invalid decimal for {label}") from exc
    if not number.is_finite() or number <= 0:
        raise ExportError(f"non-positive or non-finite decimal for {label}")
    return number


def _source_date(value: object) -> date:
    text = str(value).strip()
    matched = re.search(r"(20\d{2})[-/]?(\d{2})[-/]?(\d{2})", text)
    if matched is None:
        raise ExportError("SSE summary omitted a parseable report date")
    try:
        return date(*(int(item) for item in matched.groups()))
    except ValueError as exc:
        raise ExportError("SSE summary returned an invalid report date") from exc


def _render_csv(fieldnames: Sequence[str], rows: Sequence[Mapping[str, object]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _render_dataframe(result: CallResult) -> bytes:
    columns = [str(item) for item in result.dataframe.columns]
    return _render_csv(columns, _records(result, columns))


def _write_new(path: Path, raw: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _seal_tree(path: Path) -> None:
    for child in path.rglob("*"):
        if child.is_file():
            child.chmod(0o444)
    for child in sorted((item for item in path.rglob("*") if item.is_dir()), reverse=True):
        child.chmod(0o555)
    path.chmod(0o555)


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in path.rglob("*"):
        try:
            child.chmod(0o755 if child.is_dir() else 0o600)
        except OSError:
            pass
    path.chmod(0o755)
    shutil.rmtree(path)


def _summary_date(result: CallResult) -> date:
    rows = _records(result, ("项目", "股票"))
    matches = [row for row in rows if str(row.get("项目", "")).strip() == "报告时间"]
    if len(matches) != 1:
        raise ExportError("SSE summary must contain exactly one report-time row")
    return _source_date(matches[0]["股票"])


def _validate_exchange_summaries(sse: CallResult, szse: CallResult) -> None:
    sse_rows = _records(sse, ("项目", "股票"))
    sse_market_values = [row["股票"] for row in sse_rows if str(row.get("项目", "")).strip() == "总市值"]
    if len(sse_market_values) != 1:
        raise ExportError("SSE summary omitted total market value")
    _decimal(sse_market_values[0], label="SSE summary total_mv")
    szse_rows = _records(szse, ("证券类别", "数量", "总市值"))
    a_share_rows = [row for row in szse_rows if "A股" in str(row.get("证券类别", ""))]
    if not a_share_rows:
        raise ExportError("SZSE summary omitted A-share integrity rows")
    for row in a_share_rows:
        _decimal(row["数量"], label="SZSE summary A-share count")
        _decimal(row["总市值"], label="SZSE summary A-share total_mv")


def export_sampling_frame(
    *,
    output_root: Path,
    now: datetime,
    runner: Runner | None = None,
    minimum_market_rows: int = 4000,
    szse_source: SzseSource | None = None,
    authorization_id: str = AUTHORIZATION_ID,
    authorized_functions: frozenset[str] = AUTHORIZED_FUNCTIONS,
    package_label: str = "akshare",
    generator_paths: Sequence[Path] | None = None,
) -> ExportResult:
    """Run the bounded export and atomically publish a read-only static package."""
    raise ExportError(LEGACY_EXPORT_DISABLED)
    if now.tzinfo is None:
        raise ExportError("export timestamp must be timezone-aware")
    if minimum_market_rows < 36:
        raise ExportError("minimum market row guard may not be below the frozen sample size")
    if not re.fullmatch(r"[a-z0-9-]+", package_label):
        raise ExportError("package label must contain only lowercase ASCII letters, digits, and hyphens")
    if not authorization_id:
        raise ExportError("authorization ID is required")
    expected_functions = AUTHORIZED_FUNCTIONS if szse_source is None else AUTHORIZED_FUNCTIONS - {"stock_szse_summary"}
    if authorized_functions != expected_functions:
        raise ExportError("authorized AKShare function set does not match the selected SZSE source")
    shanghai_now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    if shanghai_now.timetz().replace(tzinfo=None) < time(16, 0):
        raise ExportError("AKShare spot snapshot may be exported only after 16:00 Asia/Shanghai")
    active_runner = runner or AkshareRunner()
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output_root.is_symlink() or not output_root.is_dir():
        raise ExportError("output root must be a real directory")
    temp_dir = Path(tempfile.mkdtemp(prefix=".m4-akshare-frame-", dir=output_root))
    try:
        raw_dir = temp_dir / "raw-http"
        output_dir = temp_dir / "function-outputs"
        raw_dir.mkdir(mode=0o700)
        output_dir.mkdir(mode=0o700)
        calls: list[CallResult] = []

        sse = _call(active_runner, "stock_sse_summary", {})
        calls.append(sse)
        sampling_date = _summary_date(sse)
        if sampling_date != shanghai_now.date():
            raise ExportError(
                f"source-date drift: SSE={sampling_date.isoformat()} acquisition={shanghai_now.date().isoformat()}"
            )
        szse = (
            _call(active_runner, "stock_szse_summary", {"date": sampling_date.strftime("%Y%m%d")})
            if szse_source is None
            else _direct_szse_call(szse_source, sampling_date)
        )
        calls.append(szse)
        _validate_exchange_summaries(sse, szse)
        spot = _call(active_runner, "stock_zh_a_spot_em", {})
        calls.append(spot)

        spot_rows: dict[str, tuple[str, Decimal, str]] = {}
        for row in _records(spot, ("代码", "名称", "总市值")):
            code = str(row["代码"]).strip().zfill(6)
            if not re.fullmatch(r"[036]\d{5}", code):
                continue
            ts_code = f"{code}.SH" if code.startswith("6") else f"{code}.SZ"
            if ts_code in spot_rows:
                raise ExportError(f"Eastmoney spot returned duplicate code: {ts_code}")
            name = str(row["名称"]).strip()
            if not name:
                raise ExportError(f"Eastmoney spot returned empty name: {ts_code}")
            spot_rows[ts_code] = (
                name,
                _decimal(row["总市值"], label=f"Eastmoney total_mv/{ts_code}"),
                "SSE" if ts_code.endswith(".SH") else "SZSE",
            )
        if len(spot_rows) < minimum_market_rows:
            raise ExportError("Eastmoney spot did not return a plausible full SSE/SZSE A-share market")

        memberships: dict[str, tuple[str, str, str]] = {}
        absent_from_spot: list[Mapping[str, object]] = []
        for industry_code, industry_name in SW2021_INDUSTRIES.items():
            call = _call(active_runner, "index_component_sw", {"symbol": industry_code.removesuffix(".SI")})
            calls.append(call)
            rows = _records(call, ("证券代码", "证券名称", "计入日期"))
            if not rows:
                raise ExportError(f"SWS component source returned no rows for {industry_code}")
            for row in rows:
                code = str(row["证券代码"]).strip().zfill(6)
                if not re.fullmatch(r"[036]\d{5}", code):
                    continue
                ts_code = f"{code}.SH" if code.startswith("6") else f"{code}.SZ"
                member_name = str(row["证券名称"]).strip()
                if not member_name:
                    raise ExportError(f"SWS component source returned empty name: {ts_code}")
                if ts_code in memberships:
                    previous = memberships[ts_code]
                    raise ExportError(
                        f"SWS component membership is not unique: {ts_code} in {previous[0]} and {industry_code}"
                    )
                memberships[ts_code] = (industry_code, industry_name, member_name)
                if ts_code not in spot_rows:
                    absent_from_spot.append(
                        {"ts_code": ts_code, "name": member_name, "reason": "not_in_same_day_eastmoney_spot"}
                    )

        frame_rows: list[FrameRow] = []
        csv_rows: list[Mapping[str, object]] = []
        exclusions = list(absent_from_spot)
        for ts_code, (name, total_mv, exchange) in sorted(spot_rows.items()):
            membership = memberships.get(ts_code)
            if membership is None:
                exclusions.append({"ts_code": ts_code, "name": name, "reason": "missing_sw2021_l1_membership"})
                continue
            industry_code, industry_name, member_name = membership
            if name != member_name:
                raise ExportError(f"company-name drift between SWS and Eastmoney: {ts_code}")
            frame_rows.append(FrameRow(ts_code, name, industry_code, industry_name, total_mv, sampling_date, exchange))
            csv_rows.append(
                {
                    "ts_code": ts_code,
                    "name": name,
                    "industry_code": industry_code,
                    "industry_name": industry_name,
                    "total_mv": str(total_mv),
                    "trade_date": sampling_date.isoformat(),
                    "exchange": exchange,
                }
            )
        if len(frame_rows) < minimum_market_rows:
            raise ExportError("same-date AKShare source intersection did not yield a plausible full frame")

        prereg = load_preregistration_ref(PROTOCOL_PATH, PROTOCOL_HASH_PATH)
        validated = validate_frame(frame_rows, sampling_date, prereg)
        sample = select_sample(validated)
        if len(sample.entries) != 36:
            raise ExportError("validated frame did not produce the frozen 36-company sample")

        frame_raw = _render_csv(
            ("ts_code", "name", "industry_code", "industry_name", "total_mv", "trade_date", "exchange"),
            csv_rows,
        )
        excluded_raw = _render_csv(("ts_code", "name", "reason"), exclusions)
        _write_new(temp_dir / "frame.csv", frame_raw)
        _write_new(temp_dir / "excluded.csv", excluded_raw)

        raw_records: list[Mapping[str, object]] = []
        output_records: list[Mapping[str, object]] = []
        capture_index = 0
        for call_index, call in enumerate(calls, start=1):
            qualifier = call.arguments.get("symbol") or call.arguments.get("date") or "all"
            stem = f"{call_index:03d}-{call.function_name}-{qualifier}"
            output_raw = _render_dataframe(call)
            output_path = output_dir / f"{stem}.csv"
            _write_new(output_path, output_raw)
            output_records.append(
                {
                    "function_name": call.function_name,
                    "arguments": dict(call.arguments),
                    "relative_path": output_path.relative_to(temp_dir).as_posix(),
                    "byte_count": len(output_raw),
                    "sha256": _sha256(output_raw),
                    "row_count": len(call.dataframe.index),
                }
            )
            for capture in call.captures:
                capture_index += 1
                if "json" in capture.content_type:
                    suffix = ".json"
                elif "spreadsheet" in capture.content_type or "excel" in capture.content_type:
                    suffix = ".xlsx"
                else:
                    suffix = ".bin"
                capture_path = raw_dir / f"{capture_index:03d}-{call.function_name}{suffix}"
                _write_new(capture_path, capture.raw)
                raw_records.append(
                    {
                        "function_name": call.function_name,
                        "request_url": capture.request_url,
                        "request_params": dict(capture.request_params),
                        "response_url": capture.response_url,
                        "status_code": capture.status_code,
                        "content_type": capture.content_type,
                        "relative_path": capture_path.relative_to(temp_dir).as_posix(),
                        "byte_count": len(capture.raw),
                        "sha256": _sha256(capture.raw),
                    }
                )

        generators = tuple(generator_paths or (Path(__file__),))
        generator_records = []
        for generator_path in generators:
            if generator_path.is_symlink():
                raise ExportError("generator path must be a real project file")
            resolved = generator_path.resolve(strict=True)
            if PROJECT_ROOT not in resolved.parents or not resolved.is_file():
                raise ExportError("generator path must be a real project file")
            generator_raw = resolved.read_bytes()
            generator_records.append(
                {
                    "relative_path": str(resolved.relative_to(PROJECT_ROOT)),
                    "sha256": _sha256(generator_raw),
                }
            )
        provenance = {
            "authorization_id": authorization_id,
            "dataset_id": f"{package_label}-sw2021-frame-{sampling_date.isoformat()}-{_sha256(frame_raw)[:16]}",
            "publishers": ["申万宏源研究", "东方财富", "上海证券交易所", "深圳证券交易所"],
            "adapter": {"name": "AKShare", "version": active_runner.version},
            "acquired_at": shanghai_now.isoformat(),
            "sampling_trade_date": sampling_date.isoformat(),
            "protocol_id": prereg.protocol_id,
            "protocol_sha256": prereg.protocol_sha256,
            "generators": generator_records,
            "authorized_akshare_functions": sorted(authorized_functions),
            "szse_source": "AKShare stock_szse_summary" if szse_source is None else "direct official HTTPS interface",
            "network_policy": "Exact per-function host allowlist; HTTPS forced; TLS verification enabled; redirects disabled.",
            "scope": "Same-day SSE/SZSE A shares present in Eastmoney spot and exactly one current SWS SW2021 L1 component list.",
            "total_mv_unit": "人民币元 (AKShare stock_zh_a_spot_em/东方财富 provider-native 总市值)",
            "field_mapping": {
                "ts_code": "stock_zh_a_spot_em.代码 with official .SH/.SZ suffix",
                "name": "exact match of stock_zh_a_spot_em.名称 and index_component_sw.证券名称",
                "industry_code": "queried frozen SW2021 index code plus .SI",
                "industry_name": "frozen protocol mapping for queried SW2021 code",
                "total_mv": "stock_zh_a_spot_em.总市值 (unrounded provider value)",
                "trade_date": "stock_sse_summary 报告时间; required to equal acquisition date; SZSE source queried same date",
                "exchange": "derived from six-digit official security code",
            },
            "integrity_gates": {
                "acquired_after_1600_asia_shanghai": True,
                "sse_report_date_equals_acquisition_date": True,
                "szse_requested_date": sampling_date.strftime("%Y%m%d"),
                "all_31_frozen_sw2021_queries_nonempty": True,
                "unique_sw2021_l1_membership": True,
                "exact_cross-source_company_name": True,
                "minimum_intersection_rows": minimum_market_rows,
            },
            "frame": {
                "relative_path": "frame.csv",
                "format": "text/csv; charset=utf-8; header=present",
                "byte_count": len(frame_raw),
                "sha256": _sha256(frame_raw),
                "row_count": len(csv_rows),
            },
            "exclusions": {
                "relative_path": "excluded.csv",
                "byte_count": len(excluded_raw),
                "sha256": _sha256(excluded_raw),
                "row_count": len(exclusions),
                "reason_counts": {
                    reason: sum(item["reason"] == reason for item in exclusions)
                    for reason in sorted({str(item["reason"]) for item in exclusions})
                },
            },
            "function_outputs": output_records,
            "raw_http_responses": raw_records,
        }
        provenance_raw = canonical_json_bytes(provenance)
        _write_new(temp_dir / "frame-provenance.json", provenance_raw)

        checksum_rows = []
        for path in sorted(item for item in temp_dir.rglob("*") if item.is_file()):
            checksum_rows.append(f"{_sha256(path.read_bytes())}  {path.relative_to(temp_dir).as_posix()}")
        _write_new(temp_dir / "SHA256SUMS", ("\n".join(checksum_rows) + "\n").encode("utf-8"))
        final_dir = output_root / f"{package_label}-frame-{sampling_date.isoformat()}-{_sha256(frame_raw)[:16]}"
        if final_dir.exists():
            raise ExportError(f"static frame package already exists: {final_dir}")
        _seal_tree(temp_dir)
        os.rename(temp_dir, final_dir)
        descriptor = os.open(output_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return ExportResult(
            final_dir,
            final_dir / "frame.csv",
            final_dir / "frame-provenance.json",
            _sha256(frame_raw),
            sampling_date,
            len(csv_rows),
        )
    except BaseException:
        _remove_tree(temp_dir)
        raise


def main() -> int:
    print(f"M4_AKSHARE_FRAME_EXPORT_BLOCKED: {LEGACY_EXPORT_DISABLED}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
