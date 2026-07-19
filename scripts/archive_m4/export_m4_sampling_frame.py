#!/usr/bin/env python3
"""Export one provenance-bearing Tushare snapshot for the M4 sampling frame."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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

API_URL = "https://api.tushare.pro"
AUTHORIZED_APIS = frozenset({"stock_basic", "trade_cal", "daily_basic", "index_classify", "index_member_all"})
AUTHORIZATION_ID = "qualitative-v2-m4-tushare-frame-export-2026-07-15-01"
LEGACY_EXPORT_DISABLED = "legacy M4 exporter is disabled; use the authorized capture-first 'capture' command"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "milestone-004" / "incoming"
PROTOCOL_PATH = (
    PROJECT_ROOT / "docs" / "plans" / "2026-07-15-milestone-004-evidence-feasibility-preregistration-v1.1.md"
)
PROTOCOL_HASH_PATH = PROJECT_ROOT / "reviews" / "milestone-004-preregistration-v1.1" / "preregistration.sha256"

Transport = Callable[[Request, float], bytes]


class ExportError(RuntimeError):
    """Fail-closed error for a sampling-frame export."""


@dataclass(frozen=True, slots=True)
class ApiSnapshot:
    api_name: str
    params: Mapping[str, str]
    requested_fields: tuple[str, ...]
    response_fields: tuple[str, ...]
    rows: tuple[Mapping[str, object], ...]
    raw: bytes
    response_count: int | None = None
    has_more: bool | None = None


@dataclass(frozen=True, slots=True)
class ExportResult:
    package_dir: Path
    frame_path: Path
    provenance_path: Path
    frame_sha256: str
    sampling_date: date
    row_count: int


class _RejectRedirects(HTTPRedirectHandler):
    """Turn every redirect into a visible HTTP failure for fixed-origin capture."""

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


def _default_transport(request: Request, timeout: float) -> bytes:
    try:
        opener = build_opener(_RejectRedirects())
        with opener.open(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS origin
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise ExportError(f"Tushare HTTP failure: status={exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ExportError(f"Tushare transport failure: {type(exc).__name__}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ExportError("Tushare response exceeds the 32 MiB export limit")
    return raw


class TushareApiClient:
    """Small allowlisted client that preserves exact response bytes and numeric lexemes."""

    def __init__(
        self,
        token: str,
        *,
        transport: Transport = _default_transport,
        min_interval_seconds: float = 1.3,
        timeout_seconds: float = 30.0,
        capture_callback: Callable[[bytes], None] | None = None,
    ) -> None:
        if not token or any(character.isspace() for character in token):
            raise ExportError("TUSHARE_TOKEN is missing or malformed")
        if min_interval_seconds < 0 or timeout_seconds <= 0:
            raise ExportError("invalid Tushare client timing configuration")
        self._token = token
        self._transport = transport
        self._min_interval_seconds = min_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._capture_callback = capture_callback
        self._last_call_started: float | None = None
        self.snapshots: list[ApiSnapshot] = []

    def call(self, api_name: str, params: Mapping[str, str], fields: Sequence[str]) -> ApiSnapshot:
        """Call exactly one authorized API and retain its raw response in memory."""
        if api_name not in AUTHORIZED_APIS:
            raise ExportError(f"Tushare API is not authorized for this export: {api_name}")
        if not fields or any(not re.fullmatch(r"[a-z][a-z0-9_]*", field) for field in fields):
            raise ExportError(f"invalid requested fields for {api_name}")
        now = time.monotonic()
        if self._last_call_started is not None:
            remaining = self._min_interval_seconds - (now - self._last_call_started)
            if remaining > 0:
                time.sleep(remaining)
        self._last_call_started = time.monotonic()
        payload = {
            "api_name": api_name,
            "token": self._token,
            "params": dict(params),
            "fields": ",".join(fields),
        }
        request = Request(
            API_URL,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "a-stock-tracker-m4-frame-export/1"},
            method="POST",
        )
        raw = self._transport(request, self._timeout_seconds)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ExportError(f"Tushare {api_name} response exceeds the 32 MiB export limit")
        if self._capture_callback is not None:
            self._capture_callback(raw)
        try:
            value = json.loads(raw.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExportError(f"Tushare {api_name} returned invalid UTF-8 JSON") from exc
        allowed_envelope_fields = {"request_id", "code", "msg", "detail", "data"}
        if (
            not isinstance(value, dict)
            or not {"code", "msg", "data"} <= set(value)
            or not set(value) <= allowed_envelope_fields
            or ("request_id" in value and not isinstance(value["request_id"], str))
            or ("detail" in value and value["detail"] is not None and not isinstance(value["detail"], str))
        ):
            shape = (
                sorted((str(key), type(item).__name__) for key, item in value.items())
                if isinstance(value, dict)
                else [("<root>", type(value).__name__)]
            )
            raise ExportError(f"Tushare {api_name} returned an unexpected envelope shape: {shape}")
        if value["code"] != 0:
            provider_message = " | ".join(
                str(item) for item in (value.get("msg"), value.get("detail")) if item not in (None, "")
            )
            message = provider_message.replace(self._token, "[REDACTED]").replace("\n", " ")[:240]
            if not message:
                message = "unknown provider error"
            raise ExportError(f"Tushare {api_name} rejected the request: {message}")
        data = value["data"]
        allowed_data_fields = {"fields", "items", "count", "has_more"}
        if not isinstance(data, dict) or not {"fields", "items"} <= set(data) or not set(data) <= allowed_data_fields:
            shape = (
                sorted((str(key), type(item).__name__) for key, item in data.items())
                if isinstance(data, dict)
                else [("<data>", type(data).__name__)]
            )
            raise ExportError(f"Tushare {api_name} returned an invalid data object shape: {shape}")
        response_fields = data["fields"]
        items = data["items"]
        if (
            not isinstance(response_fields, list)
            or not all(isinstance(field, str) for field in response_fields)
            or len(response_fields) != len(set(response_fields))
            or not isinstance(items, list)
        ):
            raise ExportError(f"Tushare {api_name} returned invalid fields/items")
        count = data.get("count")
        has_more = data.get("has_more")
        if count is not None and (
            isinstance(count, bool) or not isinstance(count, int) or count < 0 or (count != 0 and count < len(items))
        ):
            raise ExportError(
                f"Tushare {api_name} returned an invalid pagination count: count={count!r} rows={len(items)}"
            )
        if has_more is not None and not isinstance(has_more, bool):
            raise ExportError(f"Tushare {api_name} returned an invalid pagination flag")
        if has_more is True:
            raise ExportError(f"Tushare {api_name} response is truncated")
        missing = set(fields) - set(response_fields)
        if missing:
            raise ExportError(f"Tushare {api_name} omitted requested fields: {sorted(missing)}")
        rows: list[Mapping[str, object]] = []
        for item in items:
            if not isinstance(item, list) or len(item) != len(response_fields):
                raise ExportError(f"Tushare {api_name} returned a malformed row")
            rows.append(dict(zip(response_fields, item, strict=True)))
        snapshot = ApiSnapshot(
            api_name,
            dict(params),
            tuple(fields),
            tuple(response_fields),
            tuple(rows),
            raw,
            count,
            has_more,
        )
        self.snapshots.append(snapshot)
        return snapshot


def _load_token() -> str:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file() or env_path.is_symlink():
        raise ExportError("TUSHARE_TOKEN is not configured")
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "TUSHARE_TOKEN":
                token = value.strip().strip('"').strip("'")
                if token:
                    return token
    except OSError as exc:
        raise ExportError("could not read local token configuration") from exc
    raise ExportError("TUSHARE_TOKEN is not configured")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _decimal(value: object, *, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ExportError(f"missing or invalid decimal for {label}")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExportError(f"missing or invalid decimal for {label}") from exc
    if not number.is_finite() or number <= 0:
        raise ExportError(f"non-positive or non-finite decimal for {label}")
    return number


def _date_yyyymmdd(value: object, *, label: str) -> date:
    text = str(value or "")
    if not re.fullmatch(r"\d{8}", text):
        raise ExportError(f"invalid date for {label}")
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise ExportError(f"invalid date for {label}") from exc


def _rows_by_code(snapshot: ApiSnapshot, code_field: str) -> dict[str, Mapping[str, object]]:
    indexed: dict[str, Mapping[str, object]] = {}
    for row in snapshot.rows:
        code = str(row.get(code_field) or "")
        if not code or code in indexed:
            raise ExportError(f"duplicate or missing {code_field} in {snapshot.api_name}")
        indexed[code] = row
    return indexed


def _calendar_dates(snapshot: ApiSnapshot, expected_exchange: str) -> set[date]:
    result: set[date] = set()
    for row in snapshot.rows:
        if str(row.get("exchange")) != expected_exchange:
            raise ExportError(f"trade_cal exchange drift: expected {expected_exchange}")
        if str(row.get("is_open")) == "1":
            result.add(_date_yyyymmdd(row.get("cal_date"), label=f"trade_cal/{expected_exchange}"))
    return result


def _render_csv(fieldnames: Sequence[str], rows: Sequence[Mapping[str, object]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


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


def _snapshot_filename(index: int, snapshot: ApiSnapshot) -> str:
    qualifier = ""
    for key in ("exchange", "l1_code", "trade_date"):
        if snapshot.params.get(key):
            qualifier = "-" + re.sub(r"[^a-zA-Z0-9]+", "-", snapshot.params[key]).strip("-").lower()
            break
    return f"{index:03d}-{snapshot.api_name}{qualifier}.json"


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


def export_sampling_frame(
    *,
    token: str,
    output_root: Path,
    now: datetime,
    transport: Transport = _default_transport,
    min_interval_seconds: float = 1.3,
    minimum_market_rows: int = 4000,
) -> ExportResult:
    """Run the bounded export and atomically publish a read-only static package."""
    raise ExportError(LEGACY_EXPORT_DISABLED)
    if now.tzinfo is None:
        raise ExportError("export timestamp must be timezone-aware")
    if minimum_market_rows < 36:
        raise ExportError("minimum market row guard may not be below the frozen sample size")
    shanghai_now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output_root.is_symlink() or not output_root.is_dir():
        raise ExportError("output root must be a real directory")
    temp_dir = Path(tempfile.mkdtemp(prefix=".m4-frame-export-", dir=output_root))
    try:
        raw_dir = temp_dir / "raw"
        raw_dir.mkdir(mode=0o700)
        client = TushareApiClient(
            token,
            transport=transport,
            min_interval_seconds=min_interval_seconds,
        )
        calendar_start = (shanghai_now.date() - timedelta(days=14)).strftime("%Y%m%d")
        calendar_end = shanghai_now.date().strftime("%Y%m%d")
        calendar_fields = ("exchange", "cal_date", "is_open", "pretrade_date")
        sse_calendar = client.call(
            "trade_cal", {"exchange": "SSE", "start_date": calendar_start, "end_date": calendar_end}, calendar_fields
        )
        szse_calendar = client.call(
            "trade_cal", {"exchange": "SZSE", "start_date": calendar_start, "end_date": calendar_end}, calendar_fields
        )
        classify_fields = ("index_code", "industry_name", "parent_code", "level", "industry_code", "is_pub", "src")
        classification = client.call("index_classify", {"level": "L1", "src": "SW2021"}, classify_fields)
        provider_mapping = {
            str(row.get("index_code")): str(row.get("industry_name"))
            for row in classification.rows
            if str(row.get("level")) == "L1" and str(row.get("src")) == "SW2021"
        }
        if provider_mapping != dict(SW2021_INDUSTRIES):
            raise ExportError("Tushare SW2021 L1 mapping differs from the frozen protocol mapping")

        member_fields = (
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
        memberships: dict[str, set[tuple[str, str]]] = {}
        member_names: dict[str, set[str]] = {}
        for industry_code, industry_name in SW2021_INDUSTRIES.items():
            snapshot = client.call("index_member_all", {"l1_code": industry_code, "is_new": "Y"}, member_fields)
            if len(snapshot.rows) >= 2000:
                raise ExportError(f"index_member_all may be truncated for {industry_code}")
            for row in snapshot.rows:
                if str(row.get("is_new")) != "Y" or row.get("out_date") not in (None, ""):
                    continue
                if (str(row.get("l1_code")), str(row.get("l1_name"))) != (industry_code, industry_name):
                    raise ExportError(f"index_member_all L1 drift for {industry_code}")
                ts_code = str(row.get("ts_code") or "")
                if not re.fullmatch(r"\d{6}\.(SH|SZ)", ts_code):
                    continue
                memberships.setdefault(ts_code, set()).add((industry_code, industry_name))
                name = str(row.get("name") or "").strip()
                if not name:
                    raise ExportError(f"index_member_all returned an empty company name: {ts_code}")
                member_names.setdefault(ts_code, set()).add(name)

        eligible_codes = {
            ts_code
            for ts_code, membership in memberships.items()
            if len(membership) == 1 and len(member_names.get(ts_code, set())) == 1
        }
        if len(eligible_codes) < minimum_market_rows:
            raise ExportError("index_member_all did not return a plausible full current SSE/SZSE market")

        common_dates = sorted(
            _calendar_dates(sse_calendar, "SSE") & _calendar_dates(szse_calendar, "SZSE"), reverse=True
        )
        if not common_dates:
            raise ExportError("no common SSE/SZSE trading date was returned")
        selected_date: date | None = None
        selected_daily: dict[str, Mapping[str, object]] = {}
        incomplete_dates: list[Mapping[str, object]] = []
        daily_fields = ("ts_code", "trade_date", "total_mv")
        for candidate in common_dates:
            daily_snapshot = client.call("daily_basic", {"trade_date": candidate.strftime("%Y%m%d")}, daily_fields)
            daily_rows = _rows_by_code(daily_snapshot, "ts_code")
            valid_codes: set[str] = set()
            for ts_code in eligible_codes:
                daily_row = daily_rows.get(ts_code)
                if daily_row is None:
                    continue
                if _date_yyyymmdd(daily_row.get("trade_date"), label=f"daily_basic/{ts_code}") != candidate:
                    raise ExportError(f"daily_basic date drift for {ts_code}")
                try:
                    _decimal(daily_row.get("total_mv"), label=f"daily_basic/total_mv/{ts_code}")
                except ExportError:
                    continue
                valid_codes.add(ts_code)
            if valid_codes == eligible_codes and len(valid_codes) >= 36:
                selected_date = candidate
                selected_daily = daily_rows
                break
            incomplete_dates.append(
                {
                    "trade_date": candidate.isoformat(),
                    "eligible_count": len(eligible_codes),
                    "valid_daily_basic_count": len(valid_codes),
                    "missing_or_invalid_count": len(eligible_codes - valid_codes),
                }
            )
        if selected_date is None:
            raise ExportError("no complete common trading date has daily_basic coverage for the eligible frame")

        frame_rows: list[FrameRow] = []
        csv_rows: list[Mapping[str, object]] = []
        exclusions: list[Mapping[str, object]] = []
        for ts_code in sorted(eligible_codes):
            industry_code, industry_name = next(iter(memberships[ts_code]))
            total_mv = _decimal(selected_daily[ts_code].get("total_mv"), label=f"daily_basic/total_mv/{ts_code}")
            exchange = "SSE" if ts_code.endswith(".SH") else "SZSE"
            name = next(iter(member_names[ts_code]))
            frame_rows.append(FrameRow(ts_code, name, industry_code, industry_name, total_mv, selected_date, exchange))
            csv_rows.append(
                {
                    "ts_code": ts_code,
                    "name": name,
                    "industry_code": industry_code,
                    "industry_name": industry_name,
                    "total_mv": str(total_mv),
                    "trade_date": selected_date.isoformat(),
                    "exchange": exchange,
                }
            )
        for ts_code, daily_row in sorted(selected_daily.items()):
            if not re.fullmatch(r"\d{6}\.(SH|SZ)", ts_code) or ts_code in eligible_codes:
                continue
            membership = memberships.get(ts_code, set())
            names = member_names.get(ts_code, set())
            reason = "missing_current_sw2021_membership" if not membership else "ambiguous_current_sw2021_membership"
            exclusions.append({"ts_code": ts_code, "name": sorted(names)[0] if names else "", "reason": reason})

        prereg = load_preregistration_ref(PROTOCOL_PATH, PROTOCOL_HASH_PATH)
        validated = validate_frame(frame_rows, selected_date, prereg)
        sample = select_sample(validated)
        if len(sample.entries) != 36:
            raise ExportError("validated frame did not produce the frozen 36-company sample")

        frame_raw = _render_csv(
            ("ts_code", "name", "industry_code", "industry_name", "total_mv", "trade_date", "exchange"), csv_rows
        )
        excluded_raw = _render_csv(("ts_code", "name", "reason"), exclusions)
        frame_path = temp_dir / "frame.csv"
        excluded_path = temp_dir / "excluded.csv"
        _write_new(frame_path, frame_raw)
        _write_new(excluded_path, excluded_raw)

        raw_records: list[Mapping[str, object]] = []
        for index, snapshot in enumerate(client.snapshots, start=1):
            filename = _snapshot_filename(index, snapshot)
            path = raw_dir / filename
            _write_new(path, snapshot.raw)
            raw_records.append(
                {
                    "api_name": snapshot.api_name,
                    "params": dict(snapshot.params),
                    "requested_fields": list(snapshot.requested_fields),
                    "relative_path": f"raw/{filename}",
                    "byte_count": len(snapshot.raw),
                    "sha256": _sha256(snapshot.raw),
                    "row_count": len(snapshot.rows),
                    "response_count": snapshot.response_count,
                    "has_more": snapshot.has_more,
                }
            )

        script_raw = Path(__file__).read_bytes()
        provenance = {
            "authorization_id": AUTHORIZATION_ID,
            "dataset_id": f"tushare-sw2021-frame-{selected_date.isoformat()}-{_sha256(frame_raw)[:16]}",
            "publisher": "Tushare Pro",
            "provider_endpoint": API_URL,
            "acquired_at": shanghai_now.isoformat(),
            "sampling_trade_date": selected_date.isoformat(),
            "protocol_id": prereg.protocol_id,
            "protocol_sha256": prereg.protocol_sha256,
            "generator": {
                "relative_path": str(Path(__file__).resolve().relative_to(PROJECT_ROOT)),
                "sha256": _sha256(script_raw),
            },
            "authorized_apis": sorted(AUTHORIZED_APIS),
            "scope": "All SSE/SZSE securities with exactly one current SW2021 L1 membership, one current member name, and complete positive daily_basic total_mv on the latest complete common trading date.",
            "eligibility_rule": "SSE/SZSE ts_code + exactly one is_new=Y SW2021 L1 membership/name + positive daily_basic total_mv; completeness requires daily_basic coverage of every eligible current SW2021 member",
            "total_mv_unit": "万元 (Tushare daily_basic provider-native value)",
            "field_mapping": {
                "ts_code": "index_member_all.ts_code joined to daily_basic.ts_code",
                "name": "index_member_all.name",
                "industry_code": "index_member_all.l1_code",
                "industry_name": "index_member_all.l1_name",
                "total_mv": "daily_basic.total_mv",
                "trade_date": "daily_basic.trade_date",
                "exchange": "derived from index_member_all.ts_code official .SH/.SZ suffix",
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
            "incomplete_newer_common_dates": incomplete_dates,
            "raw_responses": raw_records,
            "secret_handling": "TUSHARE_TOKEN used only in HTTPS request bodies; absent from package and provenance.",
        }
        provenance_raw = canonical_json_bytes(provenance)
        provenance_path = temp_dir / "frame-provenance.json"
        _write_new(provenance_path, provenance_raw)

        token_bytes = token.encode("utf-8")
        for path in temp_dir.rglob("*"):
            if path.is_file() and token_bytes in path.read_bytes():
                raise ExportError(f"secret leakage detected in generated package: {path.name}")

        checksum_rows: list[str] = []
        for path in sorted(item for item in temp_dir.rglob("*") if item.is_file()):
            relative = path.relative_to(temp_dir).as_posix()
            checksum_rows.append(f"{_sha256(path.read_bytes())}  {relative}")
        _write_new(temp_dir / "SHA256SUMS", ("\n".join(checksum_rows) + "\n").encode("utf-8"))
        final_dir = output_root / f"tushare-frame-{selected_date.isoformat()}-{_sha256(frame_raw)[:16]}"
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
            selected_date,
            len(csv_rows),
        )
    except BaseException:
        _remove_tree(temp_dir)
        raise


def main() -> int:
    print(f"M4_FRAME_EXPORT_BLOCKED: {LEGACY_EXPORT_DISABLED}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
