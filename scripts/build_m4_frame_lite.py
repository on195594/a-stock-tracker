#!/usr/bin/env python3
"""Build the M4 SW2021 frame and deterministic sample with a lightweight workflow."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import ssl
import sys
import time
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a_stock_tracker.qualitative.audit import INDUSTRY_TO_SUPER, SAMPLING_SEED, SUPER_STRATA, SW2021_INDUSTRIES  # noqa: E402


API_URL = "https://api.tushare.pro:443"
ARTIFACT_ROOT = PROJECT_ROOT / "artifacts" / "milestone-004" / "lite"
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MIN_INTERVAL_SECONDS = 1.3
REQUEST_TIMEOUT_SECONDS = 30.0
RUN_SCHEMA = "m4-frame-lite-run-v1"

CLASSIFY_FIELDS = (
    "index_code",
    "industry_name",
    "parent_code",
    "level",
    "industry_code",
    "is_pub",
    "src",
)
MEMBER_FIELDS = (
    "l1_code",
    "l1_name",
    "l2_code",
    "l2_name",
    "l3_code",
    "l3_name",
    "ts_code",
    "name",
    "in_date",
    "out_date",
    "is_new",
)
STOCK_FIELDS = ("ts_code", "name", "market", "exchange", "curr_type", "list_status", "list_date")
DAILY_FIELDS = ("ts_code", "trade_date", "total_mv")

FRAME_FIELDS = (
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
)
SAMPLE_FIELDS = (*FRAME_FIELDS, "sampling_hash")
EXCLUDED_FIELDS = ("ts_code", "name", "industry_code", "industry_name", "reason")


class LiteError(RuntimeError):
    """Raised when the lightweight workflow must stop without retrying."""


@dataclass(frozen=True, slots=True)
class CallSpec:
    """Describe one fixed Tushare request."""

    api_name: str
    params: Mapping[str, str]
    fields: tuple[str, ...]
    raw_name: str


@dataclass(frozen=True, slots=True)
class TransportResponse:
    """Contain the HTTP status and bounded response body."""

    status: int
    body: bytes


@dataclass(frozen=True, slots=True)
class ParsedResponse:
    """Contain a schema-checked provider response."""

    rows: tuple[Mapping[str, object], ...]
    provider_count: int | None
    count_mode: str


class Transport(Protocol):
    """Minimal injectable transport used by production and tests."""

    def send(self, spec: CallSpec, token: str) -> TransportResponse:
        """Send one request and return its HTTP response."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class TushareTransport:
    """Strict TLS transport pinned to the Tushare Pro endpoint."""

    def __init__(self, *, timeout_seconds: float = REQUEST_TIMEOUT_SECONDS) -> None:
        context = ssl.create_default_context()
        self._opener = build_opener(_NoRedirect(), HTTPSHandler(context=context))
        self._timeout_seconds = timeout_seconds

    def send(self, spec: CallSpec, token: str) -> TransportResponse:
        payload = json.dumps(
            {
                "api_name": spec.api_name,
                "token": token,
                "params": dict(spec.params),
                "fields": ",".join(spec.fields),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            API_URL,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                status = int(response.getcode())
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            try:
                body = exc.read(MAX_RESPONSE_BYTES + 1)
            except Exception:
                body = b""
            return TransportResponse(int(exc.code), body)
        except (URLError, TimeoutError, OSError) as exc:
            raise LiteError("transport failure") from exc
        return TransportResponse(status, body)


class _Runner:
    def __init__(
        self,
        run_root: Path,
        token: str,
        transport: Transport,
        summary: dict[str, object],
        *,
        min_interval_seconds: float,
        monotonic: Callable[[], float],
        sleeper: Callable[[float], None],
        wait_before_first: bool = False,
    ) -> None:
        self.run_root = run_root
        self.raw_root = run_root / "raw"
        self.token = token
        self.transport = transport
        self.summary = summary
        self.min_interval_seconds = min_interval_seconds
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.last_started: float | None = None
        self.wait_before_first = wait_before_first
        self.current_call: dict[str, object] | None = None

    def call(
        self,
        ordinal: int,
        spec: CallSpec,
        validator: Callable[[ParsedResponse], None],
    ) -> ParsedResponse:
        raw_path = self.raw_root / spec.raw_name
        if raw_path.exists() or raw_path.is_symlink():
            raise LiteError("raw response path already exists")
        if self.last_started is None and self.wait_before_first:
            self.sleeper(self.min_interval_seconds)
        elif self.last_started is not None:
            remaining = self.min_interval_seconds - (self.monotonic() - self.last_started)
            if remaining > 0:
                self.sleeper(remaining)
        self.last_started = self.monotonic()
        self.current_call = {"ordinal": ordinal, "api_name": spec.api_name}
        try:
            response = self.transport.send(spec, self.token)
        except LiteError:
            raise
        except Exception as exc:
            raise LiteError("transport failure") from exc
        if response.status != 200:
            raise LiteError(f"unexpected HTTP status {response.status}")
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise LiteError("response exceeds 32 MiB")
        if self.token.encode("utf-8") in response.body:
            raise LiteError("response contains credential echo")
        _write_create_only(raw_path, response.body)
        receipt: dict[str, object] = {
            "ordinal": ordinal,
            "api_name": spec.api_name,
            "params": dict(spec.params),
            "fields": list(spec.fields),
            "raw_path": str(raw_path.relative_to(self.run_root)),
            "sha256": _sha256(response.body),
            "byte_count": len(response.body),
            "status": "captured",
        }
        calls = self.summary.setdefault("calls", [])
        if not isinstance(calls, list):
            raise LiteError("run summary calls are malformed")
        calls.append(receipt)
        _write_summary(self.run_root, self.summary)
        parsed = _parse_response(response.body, spec.fields)
        validator(parsed)
        receipt["status"] = "pass"
        receipt["row_count"] = len(parsed.rows)
        receipt["provider_count"] = parsed.provider_count
        receipt["count_mode"] = parsed.count_mode
        if parsed.count_mode == "unknown_zero_sentinel":
            anomalies = self.summary.setdefault("response_anomalies", [])
            if not isinstance(anomalies, list):
                raise LiteError("run summary response_anomalies are malformed")
            anomalies.append(
                {
                    "ordinal": ordinal,
                    "api_name": spec.api_name,
                    "kind": "unknown_zero_count_sentinel",
                    "row_count": len(parsed.rows),
                }
            )
        api_rows = self.summary.setdefault("api_rows", {})
        if not isinstance(api_rows, dict):
            raise LiteError("run summary api_rows are malformed")
        api_rows[spec.api_name] = int(api_rows.get(spec.api_name, 0)) + len(parsed.rows)
        _write_summary(self.run_root, self.summary)
        return parsed


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_reason(error: BaseException, token: str) -> str:
    text = str(error) if isinstance(error, LiteError) else "unexpected failure"
    if token:
        text = text.replace(token, "<redacted>")
    return text[:240]


def _load_token(project_root: Path = PROJECT_ROOT) -> str:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    env_path = project_root / ".env"
    if not env_path.is_file() or env_path.is_symlink():
        raise LiteError("TUSHARE_TOKEN is not configured")
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LiteError("could not read local token configuration") from exc
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "TUSHARE_TOKEN":
            token = value.strip().strip('"').strip("'")
            if token:
                return token
    raise LiteError("TUSHARE_TOKEN is not configured")


def _parse_trade_date(value: str) -> date:
    if not re.fullmatch(r"\d{8}", value):
        raise LiteError("trade date must use YYYYMMDD")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise LiteError("trade date is invalid") from exc


def _date_value(value: object, label: str) -> date:
    text = str(value or "")
    if not re.fullmatch(r"\d{8}", text):
        raise LiteError(f"invalid date in {label}")
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise LiteError(f"invalid date in {label}") from exc


def _decimal_value(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise LiteError(f"invalid positive number in {label}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LiteError(f"invalid positive number in {label}") from exc
    if not number.is_finite() or number <= 0:
        raise LiteError(f"invalid positive number in {label}")
    return number


def _write_create_only(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise LiteError("create-only output already exists") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    _write_create_only(temporary, raw)
    try:
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _write_summary(run_root: Path, summary: Mapping[str, object]) -> None:
    _atomic_json(run_root / "run-summary.json", summary)


def _parse_response(raw: bytes, requested_fields: Sequence[str]) -> ParsedResponse:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiteError("response is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise LiteError("response root is not an object")
    code = value.get("code")
    if isinstance(code, bool) or not isinstance(code, int):
        raise LiteError("provider code is malformed")
    if code != 0:
        raise LiteError(f"provider code {code}")
    data = value.get("data")
    if not isinstance(data, dict):
        raise LiteError("response data is malformed")
    fields = data.get("fields")
    if fields != list(requested_fields):
        raise LiteError("response fields differ from request")
    items = data.get("items")
    if not isinstance(items, list):
        raise LiteError("response rows are malformed")
    has_more: bool | None = None
    if "has_more" in data:
        has_more = data["has_more"]
        if not isinstance(has_more, bool):
            raise LiteError("response has_more is malformed")
        if has_more:
            raise LiteError("response is paginated")
    count = data.get("count")
    if count is None:
        count_mode = "absent"
    elif isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise LiteError("response count is malformed")
    elif count == len(items):
        count_mode = "exact"
    elif count == 0 and items and has_more is False:
        count_mode = "unknown_zero_sentinel"
    else:
        raise LiteError("response count differs from rows")
    rows: list[Mapping[str, object]] = []
    for item in items:
        if not isinstance(item, list) or len(item) != len(fields):
            raise LiteError("response row width differs from fields")
        rows.append(dict(zip(fields, item, strict=True)))
    return ParsedResponse(tuple(rows), count, count_mode)


def _validate_classification(response: ParsedResponse) -> None:
    if len(response.rows) != len(SW2021_INDUSTRIES):
        raise LiteError("classification must contain exactly 31 rows")
    mapping: dict[str, str] = {}
    for row in response.rows:
        code = str(row.get("index_code") or "")
        name = str(row.get("industry_name") or "")
        if row.get("level") != "L1" or row.get("src") != "SW2021" or code in mapping:
            raise LiteError("classification structure drift")
        mapping[code] = name
    if mapping != dict(SW2021_INDUSTRIES):
        raise LiteError("classification differs from the SW2021 mapping")


def _is_standard_member_code(ts_code: str) -> bool:
    return re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", ts_code) is not None


def _is_retired_nonstandard_member(ts_code: str, name: object) -> bool:
    normalized_name = _normalize_name(name)
    return (
        not _is_standard_member_code(ts_code)
        and re.fullmatch(r"[A-Z0-9]{1,12}\.(SH|SZ|BJ)", ts_code) is not None
        and "退市" in normalized_name
    )


def _validate_member(response: ParsedResponse, code: str, name: str, trade_date: date) -> None:
    if not response.rows or len(response.rows) >= 2000:
        raise LiteError(f"member response size is invalid for {code}")
    seen: set[str] = set()
    for row in response.rows:
        ts_code = str(row.get("ts_code") or "")
        if (row.get("l1_code"), row.get("l1_name")) != (code, name):
            raise LiteError(f"member L1 drift for {code}")
        if row.get("is_new") != "Y" or row.get("out_date") not in (None, ""):
            raise LiteError(f"member current-state drift for {code}")
        if _date_value(row.get("in_date"), f"member/{code}/in_date") > trade_date:
            raise LiteError(f"member in_date is after trade date for {code}")
        if not _is_standard_member_code(ts_code) and not _is_retired_nonstandard_member(ts_code, row.get("name")):
            raise LiteError(f"member security code is malformed for {code}")
        if ts_code in seen:
            raise LiteError(f"duplicate member security for {code}")
        seen.add(ts_code)


def _validate_stock(response: ParsedResponse, exchange: str) -> None:
    suffix = "SH" if exchange == "SSE" else "SZ"
    seen: set[str] = set()
    for row in response.rows:
        ts_code = str(row.get("ts_code") or "")
        if not re.fullmatch(rf"\d{{6}}\.{suffix}", ts_code):
            raise LiteError(f"stock code/exchange drift for {exchange}")
        if row.get("exchange") != exchange or row.get("list_status") != "L":
            raise LiteError(f"stock listing-state drift for {exchange}")
        if ts_code in seen:
            raise LiteError(f"duplicate stock row for {exchange}")
        seen.add(ts_code)


def _validate_daily(response: ParsedResponse, trade_date: str) -> None:
    seen: set[str] = set()
    for row in response.rows:
        ts_code = str(row.get("ts_code") or "")
        if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", ts_code) or ts_code in seen:
            raise LiteError("daily security code is malformed or duplicated")
        seen.add(ts_code)
        if row.get("trade_date") != trade_date:
            raise LiteError("daily trade date drift")
        _decimal_value(row.get("total_mv"), f"daily/{ts_code}/total_mv")


def _classify_spec() -> CallSpec:
    return CallSpec(
        "index_classify",
        {"level": "L1", "src": "SW2021"},
        CLASSIFY_FIELDS,
        "001-index-classify.json",
    )


def _member_spec(code: str, ordinal: int) -> CallSpec:
    return CallSpec(
        "index_member_all",
        {"l1_code": code, "is_new": "Y"},
        MEMBER_FIELDS,
        f"{ordinal:03d}-index-member-{code}.json",
    )


def _stock_spec(exchange: str, ordinal: int) -> CallSpec:
    return CallSpec(
        "stock_basic",
        {"exchange": exchange, "list_status": "L"},
        STOCK_FIELDS,
        f"{ordinal:03d}-stock-basic-{exchange}.json",
    )


def _daily_spec(trade_date: str, ordinal: int) -> CallSpec:
    return CallSpec(
        "daily_basic",
        {"trade_date": trade_date},
        DAILY_FIELDS,
        f"{ordinal:03d}-daily-basic-{trade_date}.json",
    )


def _probe_specs(trade_date: str) -> tuple[CallSpec, ...]:
    first_code = next(iter(SW2021_INDUSTRIES))
    return (
        _classify_spec(),
        _member_spec(first_code, 2),
        _stock_spec("SSE", 3),
        _stock_spec("SZSE", 4),
        _daily_spec(trade_date, 5),
    )


def _validator_for(spec: CallSpec, trade_date: date) -> Callable[[ParsedResponse], None]:
    if spec.api_name == "index_classify":
        return _validate_classification
    if spec.api_name == "index_member_all":
        code = spec.params["l1_code"]
        return lambda response: _validate_member(response, code, SW2021_INDUSTRIES[code], trade_date)
    if spec.api_name == "stock_basic":
        return lambda response: _validate_stock(response, spec.params["exchange"])
    if spec.api_name == "daily_basic":
        return lambda response: _validate_daily(response, trade_date.strftime("%Y%m%d"))
    raise LiteError("unknown API specification")


def _new_run_root(artifact_root: Path, now: datetime) -> Path:
    artifact_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{now.astimezone(SHANGHAI).strftime('%Y%m%dT%H%M%S%f%z')}-{uuid.uuid4().hex[:8]}"
    run_root = artifact_root / run_id
    run_root.mkdir(mode=0o700)
    os.chmod(run_root, 0o700)
    raw_root = run_root / "raw"
    raw_root.mkdir(mode=0o700)
    os.chmod(raw_root, 0o700)
    return run_root


def _record_failure(
    run_root: Path,
    summary: dict[str, object],
    error: BaseException,
    token: str,
    current_call: Mapping[str, object] | None,
    phase: str,
) -> None:
    failed: dict[str, object] = {"phase": phase, "reason": _safe_reason(error, token)}
    if current_call:
        failed.update(current_call)
    summary["failed_call"] = failed
    summary["build_pass"] = False
    if phase == "probe":
        summary["probe_pass"] = False
    _write_summary(run_root, summary)


def run_probe(
    trade_date_text: str,
    *,
    token: str | None = None,
    transport: Transport | None = None,
    artifact_root: Path = ARTIFACT_ROOT,
    now: datetime | None = None,
    min_interval_seconds: float = MIN_INTERVAL_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> Path:
    """Execute the five-call probe in a new create-only run directory."""
    trade_date = _parse_trade_date(trade_date_text)
    credential = token if token is not None else _load_token()
    if not credential:
        raise LiteError("TUSHARE_TOKEN is not configured")
    captured_at = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    run_root = _new_run_root(artifact_root, captured_at)
    summary: dict[str, object] = {
        "schema_version": RUN_SCHEMA,
        "run_id": run_root.name,
        "trade_date": trade_date_text,
        "acquired_at": captured_at.isoformat(),
        "probe_pass": False,
        "build_pass": False,
        "failed_call": None,
        "calls": [],
        "api_rows": {},
        "response_anomalies": [],
    }
    _write_summary(run_root, summary)
    runner = _Runner(
        run_root,
        credential,
        transport or TushareTransport(),
        summary,
        min_interval_seconds=min_interval_seconds,
        monotonic=monotonic,
        sleeper=sleeper,
    )
    try:
        for ordinal, spec in enumerate(_probe_specs(trade_date_text), 1):
            runner.call(ordinal, spec, _validator_for(spec, trade_date))
        summary["probe_pass"] = True
        summary["failed_call"] = None
        _write_summary(run_root, summary)
    except Exception as exc:
        _record_failure(run_root, summary, exc, credential, runner.current_call, "probe")
        raise LiteError(f"probe failed: {_safe_reason(exc, credential)}") from None
    return run_root


def _read_summary(run_root: Path) -> dict[str, object]:
    path = run_root / "run-summary.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiteError("probe summary is unreadable") from exc
    if not isinstance(value, dict):
        raise LiteError("probe summary is malformed")
    return value


def _read_probe_response(
    run_root: Path,
    receipt: Mapping[str, object],
    spec: CallSpec,
    trade_date: date,
    token: str,
) -> ParsedResponse:
    if (
        receipt.get("api_name") != spec.api_name
        or receipt.get("params") != dict(spec.params)
        or receipt.get("fields") != list(spec.fields)
        or receipt.get("status") != "pass"
    ):
        raise LiteError("probe request metadata drift")
    relative = receipt.get("raw_path")
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise LiteError("probe raw path is invalid")
    path = run_root / relative
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LiteError("probe raw response is missing") from exc
    if len(raw) > MAX_RESPONSE_BYTES or receipt.get("sha256") != _sha256(raw):
        raise LiteError("probe raw response hash or size drift")
    if token.encode("utf-8") in raw:
        raise LiteError("probe raw response contains credential echo")
    parsed = _parse_response(raw, spec.fields)
    if receipt.get("provider_count") != parsed.provider_count or receipt.get("count_mode") != parsed.count_mode:
        raise LiteError("probe response count metadata drift")
    _validator_for(spec, trade_date)(parsed)
    return parsed


def _validate_reusable_probe(
    run_root: Path, summary: dict[str, object], token: str
) -> tuple[date, list[ParsedResponse]]:
    if summary.get("schema_version") != RUN_SCHEMA or summary.get("run_id") != run_root.name:
        raise LiteError("probe summary identity drift")
    if summary.get("probe_pass") is not True or summary.get("build_pass") is not False:
        raise LiteError("probe is not reusable")
    if summary.get("failed_call") is not None or summary.get("build_started_at") is not None:
        raise LiteError("failed or previously started probe is not reusable")
    trade_date_text = summary.get("trade_date")
    if not isinstance(trade_date_text, str):
        raise LiteError("probe trade date is missing")
    trade_date = _parse_trade_date(trade_date_text)
    calls = summary.get("calls")
    specs = _probe_specs(trade_date_text)
    if not isinstance(calls, list) or len(calls) != len(specs):
        raise LiteError("probe must contain exactly five successful calls")
    responses: list[ParsedResponse] = []
    for ordinal, (receipt, spec) in enumerate(zip(calls, specs, strict=True), 1):
        if not isinstance(receipt, dict) or receipt.get("ordinal") != ordinal:
            raise LiteError("probe call order drift")
        responses.append(_read_probe_response(run_root, receipt, spec, trade_date, token))
    return trade_date, responses


def _normalize_name(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _excluded_name(name: str) -> bool:
    folded = name.casefold()
    return name.startswith("退市") or name.endswith("退") or folded.startswith("st") or folded.startswith("*st")


def _third_anniversary(list_date: date) -> date:
    try:
        return list_date.replace(year=list_date.year + 3)
    except ValueError:
        return list_date.replace(year=list_date.year + 3, day=28)


def _board_allowed(ts_code: str, exchange: object, market: object) -> bool:
    code = ts_code.split(".", 1)[0]
    if exchange == "SSE" and market == "主板":
        return code.startswith(("600", "601", "603", "605"))
    if exchange == "SSE" and market == "科创板":
        return code.startswith("688")
    if exchange == "SZSE" and market == "主板":
        return code.startswith(("000", "001", "002", "003"))
    if exchange == "SZSE" and market == "创业板":
        return code.startswith(("300", "301"))
    return False


def _rows_by_code(response: ParsedResponse) -> dict[str, Mapping[str, object]]:
    return {str(row["ts_code"]): row for row in response.rows}


def _assemble_rows(
    trade_date: date,
    member_responses: Mapping[str, ParsedResponse],
    stock_responses: Sequence[ParsedResponse],
    daily_response: ParsedResponse,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    membership: dict[str, tuple[str, str, str]] = {}
    for industry_code, response in member_responses.items():
        industry_name = SW2021_INDUSTRIES[industry_code]
        for row in response.rows:
            ts_code = str(row["ts_code"])
            if ts_code in membership:
                raise LiteError(f"cross-industry duplicate member: {ts_code}")
            membership[ts_code] = (industry_code, industry_name, _normalize_name(row.get("name")))

    stocks: dict[str, Mapping[str, object]] = {}
    for response in stock_responses:
        for ts_code, row in _rows_by_code(response).items():
            if ts_code in stocks:
                raise LiteError(f"duplicate stock row across exchanges: {ts_code}")
            stocks[ts_code] = row
    daily = _rows_by_code(daily_response)

    candidates: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    for ts_code in sorted(membership):
        industry_code, industry_name, member_name = membership[ts_code]
        stock = stocks.get(ts_code)
        name = member_name
        reason: str | None = None
        if not _is_standard_member_code(ts_code):
            reason = "invalid_member_code"
        elif ts_code.endswith(".BJ"):
            reason = "bj_exchange"
        elif stock is None:
            reason = "missing_stock"
        else:
            name = _normalize_name(stock.get("name"))
            if stock.get("curr_type") != "CNY":
                reason = "non_cny"
            elif not _board_allowed(ts_code, stock.get("exchange"), stock.get("market")):
                reason = "unsupported_board"
            elif not name or _excluded_name(name):
                reason = "excluded_name"
            else:
                try:
                    listed = _date_value(stock.get("list_date"), f"stock/{ts_code}/list_date")
                except LiteError:
                    reason = "invalid_list_date"
                else:
                    if _third_anniversary(listed) > trade_date:
                        reason = "listed_less_than_3_years"
                    elif ts_code not in daily:
                        reason = "missing_daily"
                    else:
                        candidates.append(
                            {
                                "ts_code": ts_code,
                                "name": name,
                                "industry_code": industry_code,
                                "industry_name": industry_name,
                                "super_stratum": INDUSTRY_TO_SUPER[industry_name],
                                "market_cap_stratum": "",
                                "total_mv": str(_decimal_value(daily[ts_code]["total_mv"], f"daily/{ts_code}")),
                                "trade_date": trade_date.strftime("%Y%m%d"),
                                "exchange": str(stock["exchange"]),
                                "market": str(stock["market"]),
                                "list_date": str(stock["list_date"]),
                            }
                        )
        if reason is not None:
            excluded.append(
                {
                    "ts_code": ts_code,
                    "name": name,
                    "industry_code": industry_code,
                    "industry_name": industry_name,
                    "reason": reason,
                }
            )

    by_super: dict[str, list[dict[str, str]]] = {name: [] for name in SUPER_STRATA}
    for row in candidates:
        by_super[row["super_stratum"]].append(row)
    for rows in by_super.values():
        rows.sort(key=lambda row: (Decimal(row["total_mv"]), row["ts_code"]))
        low_count = (len(rows) + 1) // 2
        for index, row in enumerate(rows):
            row["market_cap_stratum"] = "low" if index < low_count else "high"

    sample: list[dict[str, str]] = []
    for super_name in SUPER_STRATA:
        for cap in ("low", "high"):
            cell = [row for row in by_super[super_name] if row["market_cap_stratum"] == cap]
            ranked = sorted(
                cell,
                key=lambda row: (
                    hashlib.sha256((SAMPLING_SEED + "\x1f" + row["ts_code"]).encode("utf-8")).hexdigest(),
                    row["ts_code"],
                ),
            )
            if len(ranked) < 3:
                raise LiteError(f"sampling cell {super_name}/{cap} has fewer than three stocks")
            for row in ranked[:3]:
                selected = dict(row)
                selected["sampling_hash"] = hashlib.sha256(
                    (SAMPLING_SEED + "\x1f" + row["ts_code"]).encode("utf-8")
                ).hexdigest()
                sample.append(selected)

    frame = sorted(candidates, key=lambda row: row["ts_code"])
    excluded.sort(key=lambda row: (row["reason"], row["ts_code"]))
    if not frame or len(sample) != 36 or len({row["ts_code"] for row in sample}) != 36:
        raise LiteError("frame/sample cardinality is invalid")
    return frame, sample, excluded


def _csv_bytes(fields: Sequence[str], rows: Sequence[Mapping[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _publish_derived(
    run_root: Path,
    frame: Sequence[Mapping[str, str]],
    sample: Sequence[Mapping[str, str]],
    excluded: Sequence[Mapping[str, str]],
) -> dict[str, str]:
    temporary = run_root / "derived.tmp"
    final = run_root / "derived"
    if temporary.exists() or temporary.is_symlink() or final.exists() or final.is_symlink():
        raise LiteError("derived output already exists")
    temporary.mkdir(mode=0o700)
    os.chmod(temporary, 0o700)
    outputs = {
        "frame.csv": _csv_bytes(FRAME_FIELDS, frame),
        "sample.csv": _csv_bytes(SAMPLE_FIELDS, sample),
        "excluded.csv": _csv_bytes(EXCLUDED_FIELDS, excluded),
    }
    try:
        for name, raw in outputs.items():
            _write_create_only(temporary / name, raw)
        os.replace(temporary, final)
    except Exception:
        if temporary.exists() and temporary.is_dir():
            shutil.rmtree(temporary)
        raise
    return {name: _sha256(raw) for name, raw in outputs.items()}


def run_build(
    run_root: Path,
    *,
    token: str | None = None,
    transport: Transport | None = None,
    now: datetime | None = None,
    min_interval_seconds: float = MIN_INTERVAL_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> Path:
    """Validate and reuse a successful probe, then fetch only the other 30 industries."""
    run_root = run_root.resolve()
    credential = token if token is not None else _load_token()
    if not credential:
        raise LiteError("TUSHARE_TOKEN is not configured")
    summary = _read_summary(run_root)
    try:
        trade_date, probe_responses = _validate_reusable_probe(run_root, summary, credential)
    except Exception as exc:
        raise LiteError(f"build rejected probe: {_safe_reason(exc, credential)}") from None
    summary["build_started_at"] = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI).isoformat()
    _write_summary(run_root, summary)
    runner = _Runner(
        run_root,
        credential,
        transport or TushareTransport(),
        summary,
        min_interval_seconds=min_interval_seconds,
        monotonic=monotonic,
        sleeper=sleeper,
        wait_before_first=True,
    )
    first_code = next(iter(SW2021_INDUSTRIES))
    member_responses: dict[str, ParsedResponse] = {first_code: probe_responses[1]}
    try:
        for ordinal, code in enumerate(tuple(SW2021_INDUSTRIES)[1:], 6):
            spec = _member_spec(code, ordinal)
            member_responses[code] = runner.call(ordinal, spec, _validator_for(spec, trade_date))
        runner.current_call = None
        frame, sample, excluded = _assemble_rows(
            trade_date,
            member_responses,
            (probe_responses[2], probe_responses[3]),
            probe_responses[4],
        )
        hashes = _publish_derived(run_root, frame, sample, excluded)
        summary["frame_rows"] = len(frame)
        summary["sample_rows"] = len(sample)
        summary["excluded_rows"] = len(excluded)
        summary["exclusion_counts"] = dict(sorted(Counter(row["reason"] for row in excluded).items()))
        summary["derived_sha256"] = hashes
        summary["build_pass"] = True
        summary["failed_call"] = None
        summary["completed_at"] = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI).isoformat()
        _write_summary(run_root, summary)
    except Exception as exc:
        temporary = run_root / "derived.tmp"
        if temporary.exists() and temporary.is_dir():
            shutil.rmtree(temporary)
        final = run_root / "derived"
        if final.exists() and final.is_dir():
            shutil.rmtree(final)
        _record_failure(run_root, summary, exc, credential, runner.current_call, "build")
        raise LiteError(f"build failed: {_safe_reason(exc, credential)}") from None
    return run_root / "derived"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe = subparsers.add_parser("probe", help="run the five-call capability/data probe")
    probe.add_argument("--trade-date", required=True, help="market-cap date in YYYYMMDD format")
    build = subparsers.add_parser("build", help="reuse a successful probe and build frame/sample")
    build.add_argument("--from-probe", required=True, type=Path, help="successful lite run directory")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the lightweight frame CLI without exposing credential material."""
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "probe":
            run_root = run_probe(args.trade_date)
            print(run_root)
        else:
            derived = run_build(args.from_probe)
            print(derived)
    except LiteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("error: unexpected failure", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
