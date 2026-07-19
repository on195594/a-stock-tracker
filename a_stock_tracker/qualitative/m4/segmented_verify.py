"""Token-free offline verifier and data-gate reconstruction for M4 v1.3.1."""

from __future__ import annotations

import calendar
import re
import stat
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, Sequence

from a_stock_tracker.qualitative.audit import (
    FrameRow,
    PreregistrationRef,
    SAMPLING_SEED,
    SW2021_INDUSTRIES,
    canonical_json_bytes,
)
from a_stock_tracker.qualitative.audit import select_sample, validate_frame
from a_stock_tracker.qualitative.m4.segmented_core import (
    CODE_RE,
    DATE_EVIDENCE_SCHEMA,
    HASH_RE,
    LEDGER_SCHEMA,
    MANIFEST_SCHEMA,
    PROJECT_ROOT,
    PROTOCOL_HASH_RELATIVE_PATH,
    PROTOCOL_ID,
    PROTOCOL_RELATIVE_PATH,
    PROTOCOL_VERSION,
    RECEIPT_SCHEMA,
    STOP_SCHEMA,
    SUPERVISOR_COMMIT_SCHEMA,
    SUPERSEDES_SHA256,
    AttemptResult,
    Authorization,
    CallReceipt,
    DateEvidenceAuthorization,
    FrameAuthorization,
    JsonNumber,
    SegmentedRestCall,
    VerificationError,
    generator_references,
    load_authorization,
    parse_time,
    protocol_sha256,
    read_regular,
    require_safe_project_path,
    row_sha256,
    safe_project_relative,
    sha256_bytes,
    strict_json_loads,
)


MANIFEST_FIELDS = {
    "artifact_files",
    "attempt_id",
    "attempt_kind",
    "authorization_id",
    "authorization_relative_path",
    "authorization_sha256",
    "blob_refs",
    "capture_input_eligible",
    "complete",
    "date_evidence_ref",
    "date_selection_only",
    "disposition",
    "exclusion_counts",
    "exclusion_ledger_ref",
    "execution_deadline",
    "expected_ordinals",
    "failed_ordinal",
    "gate_results",
    "generator_files",
    "non_adoptable",
    "protocol_path",
    "protocol_sha256",
    "receipt_refs",
    "response_byte_total",
    "schema_version",
    "sealed_at",
    "stop_record_ref",
    "supervisor_commit_ref",
    "successful_ordinals",
    "supersedes_sha256",
    "terminal_failed_ordinals",
    "unattempted_ordinals",
}
RECEIPT_FIELDS = {
    "attempt_id",
    "authorization_id",
    "blob_relative_path",
    "blob_sha256",
    "byte_count",
    "call_id",
    "captured_at",
    "content_type",
    "error_code",
    "http_status",
    "ordinal",
    "provider_code",
    "request_description",
    "schema_version",
    "state",
    "status_category",
}
STOP_FIELDS = {"attempt_id", "authorization_id", "next_ordinal", "schema_version", "stop_code", "stopped_at"}
ARTIFACT_FIELDS = {"byte_count", "kind", "relative_path", "sha256"}
SUPERVISOR_COMMIT_REF_FIELDS = {*ARTIFACT_FIELDS, "nonce"}
SUPERVISOR_COMMIT_FIELDS = {
    "attempt_id",
    "authorization_id",
    "execution_deadline",
    "nonce",
    "outcome",
    "schema_version",
}
RECEIPT_REF_FIELDS = {"ordinal", "relative_path", "sealed_at", "sha256", "state"}
REASONS = (
    "delisting_consolidation",
    "exchange_out_of_scope_bse",
    "listing_age_below_36_months",
    "not_in_frame_eligible_membership",
    "not_in_frozen_sw2021_membership",
    "security_type_out_of_scope",
    "st_risk_warning",
)
SOURCE_KINDS = ("membership", "stock_basic", "stock_st", "daily")
RECEIPT_STATES = {"success_blob", "terminal_failure_blob", "terminal_failure_no_blob"}
TERMINAL_BLOB_ERRORS = {
    "http_error": "http",
    "provider_error": "provider",
    "malformed_json": "parse",
    "schema_error": "schema",
    "process_control_interrupt_after_blob": "process_control",
    "authorization_window_overrun_after_blob": "authorization",
}
TERMINAL_NO_BLOB_ERRORS = {
    "transport_error": "transport",
    "response_too_large": "size",
    "attempt_total_too_large": "size",
    "token_echo": "security",
    "process_control_interrupt_before_blob": "process_control",
    "authorization_window_overrun_before_blob": "authorization",
}
STOP_CODES = {
    "authorization_invalid",
    "authorization_not_yet_valid",
    "authorization_expired",
    "authorization_drift",
    "protocol_drift",
    "clock_invalid",
    "lock_lost",
    "output_drift",
    "credential_unavailable",
    "credential_invalid",
    "process_control_interrupt",
}


@dataclass(frozen=True, slots=True)
class ParsedResponse:
    fields: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]

    def maps(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(zip(self.fields, row, strict=True)) for row in self.rows)


@dataclass(frozen=True, slots=True)
class Reproduction:
    gates: Mapping[str, bool | None]
    ledger: Mapping[str, object] | None
    evidence: Mapping[str, object] | None


def _int(value: object, *, field: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or (minimum is not None and value < minimum):
        raise VerificationError(f"{field}_invalid")
    return value


def _path(value: object, *, field: str) -> str:
    return safe_project_relative(value, field=field)


def _mapping(value: object, fields: set[str], *, code: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise VerificationError(code)
    return value


def _ordinal_list(value: object) -> list[int]:
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in value
    ):
        raise VerificationError("ordinal_partition_invalid")
    return value


def _provider_int(value: object) -> int | None:
    if not isinstance(value, JsonNumber) or re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value.lexeme) is None:
        return None
    if len(value.lexeme.removeprefix("-")) > 20:
        return None
    return int(value.lexeme)


def _raw_provider_code(raw: bytes) -> int | None:
    try:
        value = strict_json_loads(raw)
    except VerificationError:
        return None
    return _provider_int(value.get("code")) if isinstance(value, dict) else None


def parse_provider_response(raw: bytes, call: SegmentedRestCall) -> ParsedResponse:
    """Validate provider success structure while preserving exact numeric cells."""
    value = strict_json_loads(raw)
    if not isinstance(value, dict) or set(value) - {"code", "msg", "data", "request_id"}:
        raise VerificationError("provider_schema_invalid")
    provider_code = _provider_int(value.get("code"))
    message = value.get("msg")
    if provider_code is None or (message is not None and not isinstance(message, str)):
        raise VerificationError("provider_schema_invalid")
    if provider_code != 0 or message:
        raise VerificationError("provider_error")
    data = value.get("data")
    if (
        not isinstance(data, dict)
        or not {"fields", "items"} <= set(data)
        or set(data) - {"fields", "items", "count", "has_more"}
    ):
        raise VerificationError("provider_schema_invalid")
    if data.get("fields") != list(call.fields) or not isinstance(data.get("items"), list):
        raise VerificationError("provider_fields_invalid")
    if "has_more" in data and data.get("has_more") is not False and data.get("has_more") is not None:
        raise VerificationError("pagination_residue")
    rows: list[tuple[object, ...]] = []
    for item in data["items"]:
        if not isinstance(item, list) or len(item) != len(call.fields):
            raise VerificationError("provider_row_invalid")
        rows.append(tuple(item))
    if "count" in data:
        count = _provider_int(data.get("count"))
        if count is None or count < 0 or count != len(rows):
            raise VerificationError("provider_count_invalid")
    return ParsedResponse(call.fields, tuple(rows))


def _text(row: Mapping[str, object], name: str, *, nonempty: bool = False) -> str:
    value = row.get(name)
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise VerificationError(f"{name}_invalid")
    return value


def _compact_date(value: object, *, field: str) -> date:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None:
        raise VerificationError(f"{field}_invalid")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise VerificationError(f"{field}_invalid") from exc


def _ledger_entry(
    parsed: ParsedResponse,
    cells: tuple[object, ...],
    *,
    source_kind: str,
    source_ordinal: int,
    provider_ordinal: int,
    reason: str,
    ts_code: str,
    l1_code: str | None,
    l1_name: str | None,
    name: str | None,
) -> dict[str, object]:
    return {
        "canonical_row_sha256": row_sha256(parsed.fields, cells),
        "l1_code": l1_code,
        "l1_name": l1_name,
        "name": name,
        "provider_row_ordinal": provider_ordinal,
        "reason_code": reason,
        "source_kind": source_kind,
        "source_ordinal": source_ordinal,
        "ts_code": ts_code,
    }


def _classification(parsed: ParsedResponse) -> bool:
    mapping: dict[str, str] = {}
    if len(parsed.rows) != len(SW2021_INDUSTRIES):
        return False
    try:
        for row in parsed.maps():
            code = _text(row, "index_code", nonempty=True)
            name = _text(row, "industry_name", nonempty=True)
            if code in mapping or row.get("level") != "L1" or row.get("src") != "SW2021":
                return False
            mapping[code] = name
    except VerificationError:
        return False
    return mapping == dict(SW2021_INDUSTRIES)


def _membership(
    calls: Sequence[SegmentedRestCall], parsed: Mapping[int, ParsedResponse]
) -> tuple[
    bool, dict[str, tuple[str, str, str, ParsedResponse, tuple[object, ...], int, int]], list[dict[str, object]]
]:
    memberships: dict[str, tuple[str, str, str, ParsedResponse, tuple[object, ...], int, int]] = {}
    ledger: list[dict[str, object]] = []
    represented: set[str] = set()
    try:
        for call in calls[1:32]:
            response = parsed[call.ordinal]
            rows = response.maps()
            code_expected = dict(call.params)["l1_code"]
            name_expected = SW2021_INDUSTRIES[code_expected]
            if not 1 <= len(rows) < 2000:
                return False, {}, []
            for provider_ordinal, (row, cells) in enumerate(zip(rows, response.rows, strict=True), 1):
                code = _text(row, "ts_code", nonempty=True)
                name = _text(row, "name", nonempty=True)
                in_date = _compact_date(row.get("in_date"), field="in_date")
                if (
                    row.get("l1_code") != code_expected
                    or row.get("l1_name") != name_expected
                    or row.get("is_new") != "Y"
                    or (row.get("out_date") is not None and row.get("out_date") != "")
                    or CODE_RE.fullmatch(code) is None
                    or in_date > _compact_date(dict(calls[34].params)["trade_date"], field="trade_date")
                    or code in memberships
                ):
                    return False, {}, []
                memberships[code] = (
                    code_expected,
                    name_expected,
                    name,
                    response,
                    cells,
                    call.ordinal,
                    provider_ordinal,
                )
                if code.endswith(".BJ"):
                    ledger.append(
                        _ledger_entry(
                            response,
                            cells,
                            source_kind="membership",
                            source_ordinal=call.ordinal,
                            provider_ordinal=provider_ordinal,
                            reason="exchange_out_of_scope_bse",
                            ts_code=code,
                            l1_code=code_expected,
                            l1_name=name_expected,
                            name=name,
                        )
                    )
                else:
                    represented.add(code_expected)
    except (KeyError, VerificationError):
        return False, {}, []
    exchange_count = sum(code.endswith((".SH", ".SZ")) for code in memberships)
    passed = exchange_count >= 4000 and represented == set(SW2021_INDUSTRIES)
    return passed, memberships, ledger


def _anniversary(listed: date) -> date:
    year = listed.year + 3
    day = min(listed.day, calendar.monthrange(year, listed.month)[1])
    return date(year, listed.month, day)


def _security_reason(row: Mapping[str, object], code: str, trade_date: date, st_codes: set[str]) -> str | None:
    exchange = _text(row, "exchange")
    market = _text(row, "market")
    curr = _text(row, "curr_type")
    prefixes = {
        ("SSE", "主板"): {"600", "601", "603", "605"},
        ("SSE", "科创板"): {"688"},
        ("SZSE", "主板"): {"000", "001", "002", "003"},
        ("SZSE", "创业板"): {"300", "301"},
    }
    if curr != "CNY" or code[:3] not in prefixes.get((exchange, market), set()):
        return "security_type_out_of_scope"
    normalized = unicodedata.normalize("NFKC", _text(row, "name", nonempty=True)).strip()
    if normalized.startswith("退市") or normalized.endswith("退"):
        return "delisting_consolidation"
    folded = normalized.casefold()
    if folded.startswith("st") or folded.startswith("*st") or code in st_codes:
        return "st_risk_warning"
    if _anniversary(_compact_date(row.get("list_date"), field="list_date")) > trade_date:
        return "listing_age_below_36_months"
    return None


def _stocks(
    authorization: FrameAuthorization,
    parsed: Mapping[int, ParsedResponse],
    memberships: Mapping[str, tuple[str, str, str, ParsedResponse, tuple[object, ...], int, int]],
    ledger: list[dict[str, object]],
) -> tuple[bool, bool, dict[str, tuple[str, str, str]], list[dict[str, object]]]:
    exchange_members = {code for code in memberships if code.endswith((".SH", ".SZ"))}
    st_codes: set[str] = set()
    try:
        st_response = parsed[35]
        if len(st_response.rows) >= 1000:
            return False, False, {}, ledger
        for provider_ordinal, (row, cells) in enumerate(zip(st_response.maps(), st_response.rows, strict=True), 1):
            code = _text(row, "ts_code", nonempty=True)
            name = _text(row, "name", nonempty=True)
            if (
                CODE_RE.fullmatch(code) is None
                or code in st_codes
                or _compact_date(row.get("trade_date"), field="trade_date") != authorization.trade_date
                or not _text(row, "type", nonempty=True)
                or not _text(row, "type_name", nonempty=True)
            ):
                return False, False, {}, ledger
            st_codes.add(code)
            if code.endswith(".BJ"):
                reason = "exchange_out_of_scope_bse"
            elif code not in exchange_members:
                reason = "not_in_frozen_sw2021_membership"
            else:
                reason = "st_risk_warning"
            member = memberships.get(code)
            ledger.append(
                _ledger_entry(
                    st_response,
                    cells,
                    source_kind="stock_st",
                    source_ordinal=35,
                    provider_ordinal=provider_ordinal,
                    reason=reason,
                    ts_code=code,
                    l1_code=member[0] if member else None,
                    l1_name=member[1] if member else None,
                    name=name,
                )
            )
    except (KeyError, VerificationError):
        return False, False, {}, ledger

    stocks: dict[str, tuple[Mapping[str, object], ParsedResponse, tuple[object, ...], int, int]] = {}
    stock_structural = True
    try:
        for ordinal, exchange, suffix in ((33, "SSE", ".SH"), (34, "SZSE", ".SZ")):
            response = parsed[ordinal]
            if not 1 <= len(response.rows) < 6000:
                return False, True, {}, ledger
            for provider_ordinal, (row, cells) in enumerate(zip(response.maps(), response.rows, strict=True), 1):
                code = _text(row, "ts_code", nonempty=True)
                _text(row, "name", nonempty=True)
                _text(row, "market")
                _text(row, "exchange")
                _text(row, "curr_type")
                listed = _compact_date(row.get("list_date"), field="list_date")
                _anniversary(listed)
                if (
                    re.fullmatch(r"[0-9]{6}\.(SH|SZ)", code) is None
                    or not code.endswith(suffix)
                    or row.get("exchange") != exchange
                    or row.get("list_status") != "L"
                    or code in stocks
                ):
                    stock_structural = False
                    break
                stocks[code] = (row, response, cells, ordinal, provider_ordinal)
            if not stock_structural:
                break
    except (KeyError, VerificationError, ValueError, OverflowError):
        stock_structural = False
    if not stock_structural or set(stocks) & set(code for code in memberships if code.endswith(".BJ")):
        return False, True, {}, ledger

    eligible: dict[str, tuple[str, str, str]] = {}
    joins_ok = exchange_members <= set(stocks)
    for code, (stock_row, response, cells, ordinal, provider_ordinal) in stocks.items():
        member = memberships.get(code)
        if member is None:
            ledger.append(
                _ledger_entry(
                    response,
                    cells,
                    source_kind="stock_basic",
                    source_ordinal=ordinal,
                    provider_ordinal=provider_ordinal,
                    reason="not_in_frozen_sw2021_membership",
                    ts_code=code,
                    l1_code=None,
                    l1_name=None,
                    name=str(stock_row["name"]),
                )
            )
            continue
        try:
            stock_reason = _security_reason(stock_row, code, authorization.trade_date, st_codes)
        except (VerificationError, ValueError, OverflowError):
            return False, True, {}, ledger
        if stock_reason is None:
            eligible[code] = (member[0], member[1], str(stock_row["name"]))
            continue
        ledger.append(
            _ledger_entry(
                response,
                cells,
                source_kind="stock_basic",
                source_ordinal=ordinal,
                provider_ordinal=provider_ordinal,
                reason=stock_reason,
                ts_code=code,
                l1_code=member[0],
                l1_name=member[1],
                name=str(stock_row["name"]),
            )
        )
        ledger.append(
            _ledger_entry(
                member[3],
                member[4],
                source_kind="membership",
                source_ordinal=member[5],
                provider_ordinal=member[6],
                reason="not_in_frame_eligible_membership",
                ts_code=code,
                l1_code=member[0],
                l1_name=member[1],
                name=member[2],
            )
        )
    return joins_ok, True, eligible, ledger


def _daily(
    authorization: FrameAuthorization,
    parsed: Mapping[int, ParsedResponse],
    memberships: Mapping[str, tuple[str, str, str, ParsedResponse, tuple[object, ...], int, int]],
    eligible: Mapping[str, tuple[str, str, str]],
    ledger: list[dict[str, object]],
) -> tuple[bool, dict[str, Decimal], list[dict[str, object]]]:
    values: dict[str, Decimal] = {}
    exchange_members = {code for code in memberships if code.endswith((".SH", ".SZ"))}
    try:
        response = parsed[36]
        if not 4000 <= len(response.rows) < 6000:
            return False, {}, ledger
        for provider_ordinal, (row, cells) in enumerate(zip(response.maps(), response.rows, strict=True), 1):
            code = _text(row, "ts_code", nonempty=True)
            if (
                CODE_RE.fullmatch(code) is None
                or code in values
                or _compact_date(row.get("trade_date"), field="trade_date") != authorization.trade_date
            ):
                return False, {}, ledger
            number = row.get("total_mv")
            if not isinstance(number, JsonNumber):
                return False, {}, ledger
            try:
                amount = Decimal(number.lexeme)
            except InvalidOperation:
                return False, {}, ledger
            if not amount.is_finite() or amount <= 0:
                return False, {}, ledger
            values[code] = amount
            if code.endswith(".BJ"):
                reason = "exchange_out_of_scope_bse"
            elif code not in exchange_members:
                reason = "not_in_frozen_sw2021_membership"
            elif code not in eligible:
                reason = "not_in_frame_eligible_membership"
            else:
                continue
            ledger.append(
                _ledger_entry(
                    response,
                    cells,
                    source_kind="daily",
                    source_ordinal=36,
                    provider_ordinal=provider_ordinal,
                    reason=reason,
                    ts_code=code,
                    l1_code=None,
                    l1_name=None,
                    name=None,
                )
            )
    except (KeyError, VerificationError):
        return False, {}, ledger
    return exchange_members <= set(values), values, ledger


def _frame(
    authorization: FrameAuthorization,
    eligible: Mapping[str, tuple[str, str, str]],
    daily: Mapping[str, Decimal],
) -> tuple[bool, bool]:
    rows = [
        FrameRow(
            code,
            values[2],
            values[0],
            values[1],
            daily[code],
            authorization.trade_date,
            "SSE" if code.endswith(".SH") else "SZSE",
        )
        for code, values in eligible.items()
        if code in daily
    ]
    prereg = PreregistrationRef(
        PROTOCOL_ID,
        PROTOCOL_VERSION,
        PROTOCOL_RELATIVE_PATH,
        authorization.protocol_sha256,
        PROTOCOL_HASH_RELATIVE_PATH,
        SAMPLING_SEED,
        SUPERSEDES_SHA256,
    )
    try:
        validated = validate_frame(rows, authorization.trade_date, prereg)
        sample = select_sample(validated)
    except Exception:
        return False, False
    cells = {(item.super_stratum, item.market_cap_stratum) for item in sample.entries}
    return bool(rows), len(sample.entries) == 36 and len(cells) == 12


def _sort_ledger(entries: list[dict[str, object]]) -> None:
    rank = {kind: index for index, kind in enumerate(SOURCE_KINDS)}
    entries.sort(
        key=lambda item: (
            rank[str(item["source_kind"])],
            str(item["ts_code"]),
            str(item["l1_code"] or ""),
            str(item["canonical_row_sha256"]),
            _int(item["source_ordinal"], field="source_ordinal", minimum=1),
            _int(item["provider_row_ordinal"], field="provider_row_ordinal", minimum=1),
        )
    )


def _empty_gates(window: bool) -> dict[str, bool | None]:
    return {
        "authorization_window": window,
        "calendar": None,
        "classification": None,
        "daily": None,
        "date_derivation": None,
        "frame": None,
        "membership": None,
        "overall_pass": False,
        "sampling_cells": None,
        "stock_basic": None,
        "stock_st": None,
    }


def reproduce_frame(
    authorization: FrameAuthorization,
    parsed: Mapping[int, ParsedResponse],
    *,
    complete: bool,
    authorization_window: bool,
) -> Reproduction:
    entries: list[dict[str, object]] = []
    gates = _empty_gates(authorization_window)
    if complete:
        gates["classification"] = _classification(parsed[1]) if 1 in parsed else False
        membership_ok, memberships, entries = _membership(authorization.calls, parsed)
        gates["membership"] = membership_ok
        stock_basic_ok, stock_st_ok, eligible, entries = _stocks(authorization, parsed, memberships, entries)
        gates["stock_basic"] = stock_basic_ok
        gates["stock_st"] = stock_st_ok
        daily_ok, daily, entries = _daily(authorization, parsed, memberships, eligible, entries)
        gates["daily"] = daily_ok
        frame_ok, cells_ok = (
            _frame(authorization, eligible, daily)
            if all(gates[name] for name in ("classification", "membership", "stock_basic", "stock_st", "daily"))
            else (False, False)
        )
        gates["frame"] = frame_ok
        gates["sampling_cells"] = cells_ok
        gates["overall_pass"] = authorization_window and all(
            gates[name] is True
            for name in ("classification", "membership", "stock_basic", "stock_st", "daily", "frame", "sampling_cells")
        )
    _sort_ledger(entries)
    ledger = {
        "attempt_id": authorization.attempt_id,
        "authorization_id": authorization.authorization_id,
        "entries": entries,
        "mode": authorization.mode,
        "schema_version": LEDGER_SCHEMA,
    }
    return Reproduction(gates, ledger, None)


def _calendar_rows(
    authorization: DateEvidenceAuthorization, parsed: Mapping[int, ParsedResponse]
) -> tuple[bool, dict[str, dict[date, int]]]:
    expected_dates = {authorization.calendar_start + timedelta(days=offset) for offset in range(125)}
    calendars: dict[str, dict[date, int]] = {}
    try:
        for ordinal, exchange in ((1, "SSE"), (2, "SZSE")):
            result: dict[date, int] = {}
            response = parsed[ordinal]
            if len(response.rows) != 125:
                return False, {}
            for row in response.maps():
                selected = _compact_date(row.get("cal_date"), field="cal_date")
                opened = _provider_int(row.get("is_open"))
                pretrade = row.get("pretrade_date")
                if (
                    row.get("exchange") != exchange
                    or selected in result
                    or opened not in {0, 1}
                    or (
                        pretrade is not None
                        and (not isinstance(pretrade, str) or re.fullmatch(r"[0-9]{8}", pretrade) is None)
                    )
                ):
                    return False, {}
                if pretrade is not None:
                    _compact_date(pretrade, field="pretrade_date")
                result[selected] = int(opened)
            if set(result) != expected_dates:
                return False, {}
            calendars[exchange] = result
    except (KeyError, VerificationError):
        return False, {}
    return True, calendars


def reproduce_date(
    authorization: DateEvidenceAuthorization,
    parsed: Mapping[int, ParsedResponse],
    receipts: Sequence[CallReceipt],
    *,
    complete: bool,
    authorization_window: bool,
    sealed_at: datetime,
) -> Reproduction:
    gates = _empty_gates(authorization_window)
    gates.update(
        {
            name: None
            for name in ("classification", "membership", "stock_basic", "stock_st", "daily", "frame", "sampling_cells")
        }
    )
    evidence: dict[str, object] | None = None
    if complete:
        calendar_ok, calendars = _calendar_rows(authorization, parsed)
        gates["calendar"] = calendar_ok
        derived_ok = False
        next_open: date | None = None
        if calendar_ok and receipts and sealed_at >= max(receipt.captured_at for receipt in receipts):
            common = sorted(day for day in calendars["SSE"] if calendars["SSE"][day] == calendars["SZSE"][day] == 1)
            completed = [
                day
                for day in common
                if datetime.combine(day, datetime.min.time(), tzinfo=sealed_at.tzinfo).replace(hour=18) <= sealed_at
            ]
            derived = max(completed) if completed else None
            next_open = next((day for day in common if derived is not None and day > derived), None)
            derived_ok = derived == authorization.proposed_sampling_date and next_open is not None
        gates["date_derivation"] = derived_ok
        gates["overall_pass"] = calendar_ok and derived_ok and authorization_window
        if gates["overall_pass"] and next_open is not None:
            sources = []
            for exchange, receipt in zip(("SSE", "SZSE"), receipts, strict=True):
                sources.append(
                    {
                        "blob_relative_path": receipt.blob_relative_path,
                        "blob_sha256": receipt.blob_sha256,
                        "byte_count": receipt.byte_count,
                        "call_id": f"tushare:trade_cal:{exchange}",
                        "exchange": exchange,
                        "receipt_relative_path": receipt.receipt_relative_path,
                        "receipt_sha256": receipt.receipt_sha256,
                    }
                )
            evidence = {
                "attempt_id": authorization.attempt_id,
                "authorization_id": authorization.authorization_id,
                "calendar_end": authorization.calendar_end.isoformat(),
                "calendar_start": authorization.calendar_start.isoformat(),
                "date_selection_only": True,
                "next_common_open_date": next_open.isoformat(),
                "protocol_sha256": authorization.protocol_sha256,
                "sampling_date": authorization.proposed_sampling_date.isoformat(),
                "schema_version": DATE_EVIDENCE_SCHEMA,
                "sealed_at": sealed_at.isoformat(timespec="seconds"),
                "sources": sources,
                "valid_from": f"{authorization.proposed_sampling_date.isoformat()}T18:00:00+08:00",
                "valid_until": f"{next_open.isoformat()}T17:59:59+08:00",
            }
    return Reproduction(gates, None, evidence)


def exclusion_counts(ledger: Mapping[str, object]) -> dict[str, object]:
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        raise VerificationError("ledger_invalid")
    sources = Counter(str(item["source_kind"]) for item in entries if isinstance(item, dict))
    reasons = Counter(str(item["reason_code"]) for item in entries if isinstance(item, dict))
    return {
        "by_reason": {reason: reasons[reason] for reason in REASONS},
        "by_source": {source: sources[source] for source in SOURCE_KINDS},
        "total": len(entries),
    }


def _authorization_for_manifest(value: Mapping[str, object]) -> Authorization:
    relative = _path(value.get("authorization_relative_path"), field="authorization_relative_path")
    path = PROJECT_ROOT / relative
    checksum = path.with_suffix(".sha256")
    authorization = load_authorization(path, checksum)
    if value.get("authorization_sha256") != authorization.sha256:
        raise VerificationError("authorization_drift")
    return authorization


def _receipt(
    attempt_dir: Path,
    record: object,
    authorization: Authorization,
    expected_call: SegmentedRestCall,
    expected_files: dict[str, dict[str, object]],
    *,
    require_read_only: bool,
) -> tuple[CallReceipt, ParsedResponse | None]:
    ref = _mapping(record, RECEIPT_REF_FIELDS, code="receipt_ref_invalid")
    if ref.get("ordinal") != expected_call.ordinal:
        raise VerificationError("receipt_ref_ordinal_invalid")
    relative = _path(ref.get("relative_path"), field="receipt_path")
    if relative != f"receipts/{expected_call.ordinal:04d}.json":
        raise VerificationError("receipt_path_invalid")
    raw = read_regular(attempt_dir / relative, maximum=2 * 1024 * 1024, require_read_only=require_read_only)
    if ref.get("sha256") != sha256_bytes(raw):
        raise VerificationError("receipt_hash_drift")
    value = strict_json_loads(raw, numbers=False)
    receipt = _mapping(value, RECEIPT_FIELDS, code="receipt_schema_invalid")
    if receipt.get("schema_version") != RECEIPT_SCHEMA or receipt.get("ordinal") != expected_call.ordinal:
        raise VerificationError("receipt_schema_invalid")
    if (
        receipt.get("authorization_id") != authorization.authorization_id
        or receipt.get("attempt_id") != authorization.attempt_id
    ):
        raise VerificationError("receipt_identity_invalid")
    state = receipt.get("state")
    if state not in RECEIPT_STATES or ref.get("state") != state or receipt.get("call_id") != expected_call.call_id:
        raise VerificationError("receipt_state_invalid")
    if receipt.get("request_description") != expected_call.request_description():
        raise VerificationError("request_description_invalid")
    captured = parse_time(receipt.get("captured_at"), field="captured_at")
    receipt_sealed_at = parse_time(ref.get("sealed_at"), field="receipt_sealed_at")
    if receipt_sealed_at < captured:
        raise VerificationError("receipt_sealed_at_invalid")
    if require_read_only:
        filesystem_sealed_at = datetime.fromtimestamp(
            (attempt_dir / relative).stat(follow_symlinks=False).st_mtime,
            receipt_sealed_at.tzinfo,
        ).replace(microsecond=0)
        if filesystem_sealed_at != receipt_sealed_at:
            raise VerificationError("receipt_seal_timestamp_drift")
    blob_relative = receipt.get("blob_relative_path")
    blob_sha = receipt.get("blob_sha256")
    byte_count = receipt.get("byte_count")
    http_status = receipt.get("http_status")
    provider_code = receipt.get("provider_code")
    if http_status is not None and (
        isinstance(http_status, bool) or not isinstance(http_status, int) or not 100 <= http_status <= 599
    ):
        raise VerificationError("receipt_http_status_invalid")
    if provider_code is not None and (isinstance(provider_code, bool) or not isinstance(provider_code, int)):
        raise VerificationError("receipt_provider_code_invalid")
    parsed: ParsedResponse | None = None
    if state in {"success_blob", "terminal_failure_blob"}:
        relative_blob = _path(blob_relative, field="blob_path")
        if not isinstance(blob_sha, str) or HASH_RE.fullmatch(blob_sha) is None:
            raise VerificationError("blob_hash_invalid")
        if relative_blob != f"blobs/{expected_call.ordinal:04d}-{blob_sha}.bin":
            raise VerificationError("blob_path_invalid")
        blob = read_regular(
            attempt_dir / relative_blob, maximum=authorization.response_byte_limit, require_read_only=require_read_only
        )
        if sha256_bytes(blob) != blob_sha or byte_count != len(blob):
            raise VerificationError("blob_hash_or_size_drift")
        if provider_code != _raw_provider_code(blob):
            raise VerificationError("receipt_provider_code_invalid")
        if not isinstance(receipt.get("content_type"), str) or not receipt.get("content_type"):
            raise VerificationError("blob_content_type_invalid")
        expected_files[relative_blob] = {
            "byte_count": len(blob),
            "kind": "blob",
            "relative_path": relative_blob,
            "sha256": blob_sha,
        }
        if state == "success_blob":
            if not (
                receipt.get("status_category") == "success"
                and receipt.get("error_code") is None
                and receipt.get("http_status") == 200
                and receipt.get("provider_code") == 0
            ):
                raise VerificationError("success_receipt_invalid")
            parsed = parse_provider_response(blob, expected_call)
        else:
            error_code = receipt.get("error_code")
            if not isinstance(error_code, str) or TERMINAL_BLOB_ERRORS.get(error_code) != receipt.get(
                "status_category"
            ):
                raise VerificationError("terminal_blob_receipt_invalid")
            if error_code == "http_error" and (http_status is None or http_status == 200):
                raise VerificationError("terminal_blob_receipt_invalid")
            if error_code == "provider_error" and http_status != 200:
                raise VerificationError("terminal_blob_receipt_invalid")
            if error_code in {"malformed_json", "schema_error"} and http_status != 200:
                raise VerificationError("terminal_blob_receipt_invalid")
            if error_code == "provider_error":
                try:
                    parse_provider_response(blob, expected_call)
                except VerificationError as exc:
                    if exc.code != "provider_error":
                        raise VerificationError("terminal_blob_classification_invalid") from exc
                else:
                    raise VerificationError("terminal_blob_classification_invalid")
            elif error_code == "malformed_json":
                try:
                    strict_json_loads(blob)
                except VerificationError as exc:
                    if exc.code != "json_invalid":
                        raise VerificationError("terminal_blob_classification_invalid") from exc
                else:
                    raise VerificationError("terminal_blob_classification_invalid")
            elif error_code == "schema_error":
                try:
                    parse_provider_response(blob, expected_call)
                except VerificationError as exc:
                    if exc.code in {"provider_error", "json_invalid"}:
                        raise VerificationError("terminal_blob_classification_invalid") from exc
                else:
                    raise VerificationError("terminal_blob_classification_invalid")
    else:
        if any(item is not None for item in (blob_relative, blob_sha, byte_count, receipt.get("content_type"))):
            raise VerificationError("no_blob_receipt_invalid")
        error_code = receipt.get("error_code")
        if not isinstance(error_code, str) or TERMINAL_NO_BLOB_ERRORS.get(error_code) != receipt.get("status_category"):
            raise VerificationError("no_blob_receipt_invalid")
    expected_files[relative] = {
        "byte_count": len(raw),
        "kind": "receipt",
        "relative_path": relative,
        "sha256": sha256_bytes(raw),
    }
    result = CallReceipt(
        expected_call.ordinal,
        expected_call.call_id,
        str(state),
        captured,
        str(receipt.get("status_category")),
        str(receipt["error_code"]) if receipt.get("error_code") is not None else None,
        int(byte_count) if isinstance(byte_count, int) and not isinstance(byte_count, bool) else None,
        str(blob_relative) if blob_relative is not None else None,
        str(blob_sha) if blob_sha is not None else None,
        relative,
        sha256_bytes(raw),
        receipt_sealed_at,
    )
    return result, parsed


def _verify_reference_chains(authorization: Authorization, visited: set[Path]) -> None:
    refs: list[Mapping[str, object]] = []
    if isinstance(authorization, DateEvidenceAuthorization):
        refs.append(authorization.capability_manifest_ref)
    elif authorization.mode == "capture":
        if authorization.capability_manifest_ref is None or authorization.date_evidence_ref is None:
            raise VerificationError("capture_reference_missing")
        refs.append(authorization.capability_manifest_ref)
    for ref in refs:
        relative = _path(ref.get("relative_path"), field="reference_path")
        manifest_path = PROJECT_ROOT / relative
        if manifest_path.name != "attempt-manifest.json":
            raise VerificationError("capability_reference_invalid")
        result = _verify_attempt(manifest_path.parent, visited=visited)
        if (
            result.attempt_kind != "capability"
            or not result.overall_pass
            or result.disposition != "ELIGIBLE_FOR_FULL_CAPTURE_AUTHORIZATION"
        ):
            raise VerificationError("capability_reference_invalid")
        if result.attempt_id != ref.get("attempt_id") or result.manifest_sha256 != ref.get("sha256"):
            raise VerificationError("capability_reference_invalid")
    if isinstance(authorization, FrameAuthorization) and authorization.mode == "capture":
        assert authorization.date_evidence_ref is not None
        ref = authorization.date_evidence_ref
        relative = _path(ref.get("relative_path"), field="date_evidence_path")
        evidence_path = PROJECT_ROOT / relative
        if evidence_path.name != "date-selection-evidence.json":
            raise VerificationError("date_evidence_reference_invalid")
        result = _verify_attempt(evidence_path.parent, visited=visited)
        evidence_raw = read_regular(evidence_path, maximum=2 * 1024 * 1024, require_read_only=True)
        evidence = strict_json_loads(evidence_raw, numbers=False)
        if (
            result.attempt_kind != "date_selection_evidence"
            or not result.overall_pass
            or result.attempt_id != ref.get("attempt_id")
            or sha256_bytes(evidence_raw) != ref.get("sha256")
            or not isinstance(evidence, dict)
            or evidence.get("authorization_id") != ref.get("authorization_id")
            or evidence.get("sampling_date") != authorization.trade_date.isoformat()
        ):
            raise VerificationError("date_evidence_reference_invalid")
        sealed = parse_time(evidence.get("sealed_at"), field="evidence_sealed_at")
        valid_from = parse_time(evidence.get("valid_from"), field="evidence_valid_from")
        valid_until = parse_time(evidence.get("valid_until"), field="evidence_valid_until")
        if authorization.not_before < max(sealed, valid_from) or authorization.not_after > valid_until:
            raise VerificationError("capture_evidence_window_invalid")


def _verify_attempt(
    attempt_dir: Path,
    *,
    candidate_raw: bytes | None = None,
    require_sealed: bool = True,
    visited: set[Path] | None = None,
) -> AttemptResult:
    visited = set() if visited is None else visited
    resolved = attempt_dir.resolve()
    if resolved in visited:
        raise VerificationError("reference_cycle")
    visited.add(resolved)
    try:
        if attempt_dir.is_symlink() or not attempt_dir.is_dir():
            raise VerificationError("attempt_directory_invalid")
        require_safe_project_path(attempt_dir)
        if require_sealed:
            external_state = (
                attempt_dir.parent / f".{attempt_dir.name}.lock",
                attempt_dir.parent / f".{attempt_dir.name}.phase-journal.json",
                attempt_dir.parent / f".{attempt_dir.name}.publication-tmp",
            )
            if any(path.exists() or path.is_symlink() for path in external_state):
                raise VerificationError("stale_external_attempt_state")
        if require_sealed and stat.S_IMODE(attempt_dir.stat(follow_symlinks=False).st_mode) != 0o555:
            raise VerificationError("attempt_directory_permissions_invalid")
        manifest_path = attempt_dir / "attempt-manifest.json"
        manifest_raw = (
            candidate_raw
            if candidate_raw is not None
            else read_regular(manifest_path, maximum=8 * 1024 * 1024, require_read_only=require_sealed)
        )
        value = strict_json_loads(manifest_raw, numbers=False)
        manifest = _mapping(value, MANIFEST_FIELDS, code="manifest_schema_invalid")
        if canonical_json_bytes(manifest) != manifest_raw or manifest.get("schema_version") != MANIFEST_SCHEMA:
            raise VerificationError("manifest_not_canonical")
        authorization = _authorization_for_manifest(manifest)
        if (
            manifest.get("authorization_id") != authorization.authorization_id
            or manifest.get("attempt_id") != authorization.attempt_id
        ):
            raise VerificationError("manifest_identity_invalid")
        expected_kind = (
            authorization.purpose if isinstance(authorization, DateEvidenceAuthorization) else authorization.mode
        )
        if manifest.get("attempt_kind") != expected_kind:
            raise VerificationError("manifest_kind_invalid")
        if (
            manifest.get("protocol_path") != PROTOCOL_RELATIVE_PATH
            or manifest.get("protocol_sha256") != protocol_sha256()
            or manifest.get("supersedes_sha256") != SUPERSEDES_SHA256
            or manifest.get("generator_files") != generator_references()
        ):
            raise VerificationError("provenance_drift")
        expected_dir = PROJECT_ROOT / authorization.attempt_output_dir
        if attempt_dir.resolve() != expected_dir.resolve() or attempt_dir.name != authorization.attempt_id:
            raise VerificationError("attempt_path_invalid")
        _verify_reference_chains(authorization, visited)

        matrix = authorization.calls
        expected_ordinals = list(range(1, len(matrix) + 1))
        successful = _ordinal_list(manifest.get("successful_ordinals"))
        terminal = _ordinal_list(manifest.get("terminal_failed_ordinals"))
        unattempted = _ordinal_list(manifest.get("unattempted_ordinals"))
        if manifest.get("expected_ordinals") != expected_ordinals or len(terminal) > 1:
            raise VerificationError("ordinal_partition_invalid")
        if sorted(successful + terminal + unattempted) != expected_ordinals or len(
            set(successful + terminal + unattempted)
        ) != len(matrix):
            raise VerificationError("ordinal_partition_invalid")
        if any(items != sorted(items) for items in (successful, terminal, unattempted)):
            raise VerificationError("ordinal_partition_invalid")
        if manifest.get("failed_ordinal") != (terminal[0] if terminal else None):
            raise VerificationError("ordinal_partition_invalid")
        attempted = sorted(successful + terminal)
        if attempted != list(range(1, len(attempted) + 1)) or (terminal and terminal[0] != attempted[-1]):
            raise VerificationError("ordinal_sequence_invalid")

        receipt_refs = manifest.get("receipt_refs")
        if not isinstance(receipt_refs, list) or len(receipt_refs) != len(attempted):
            raise VerificationError("receipt_refs_invalid")
        expected_files: dict[str, dict[str, object]] = {}
        receipts: list[CallReceipt] = []
        parsed: dict[int, ParsedResponse] = {}
        for ordinal, record in zip(attempted, receipt_refs, strict=True):
            receipt, response = _receipt(
                attempt_dir,
                record,
                authorization,
                matrix[ordinal - 1],
                expected_files,
                require_read_only=require_sealed,
            )
            if (ordinal in successful) != (receipt.state == "success_blob"):
                raise VerificationError("receipt_partition_mismatch")
            if ordinal in terminal and receipt.state == "success_blob":
                raise VerificationError("receipt_partition_mismatch")
            receipts.append(receipt)
            if response is not None:
                parsed[ordinal] = response
        complete = successful == expected_ordinals and not terminal and not unattempted
        if manifest.get("complete") is not complete:
            raise VerificationError("complete_invalid")
        receipt_seal_times = [receipt.sealed_at or receipt.captured_at for receipt in receipts]
        if receipt_seal_times != sorted(receipt_seal_times):
            raise VerificationError("receipt_chronology_invalid")
        blob_refs = manifest.get("blob_refs")
        expected_blob_refs = [
            {
                "byte_count": receipt.byte_count,
                "ordinal": receipt.ordinal,
                "relative_path": receipt.blob_relative_path,
                "sealed_at": (receipt.sealed_at or receipt.captured_at).isoformat(timespec="seconds"),
                "sha256": receipt.blob_sha256,
            }
            for receipt in receipts
            if receipt.blob_relative_path is not None
        ]
        if blob_refs != expected_blob_refs:
            raise VerificationError("blob_refs_invalid")
        response_total = sum(receipt.byte_count or 0 for receipt in receipts if receipt.blob_relative_path is not None)
        if manifest.get("response_byte_total") != response_total or response_total > authorization.attempt_byte_limit:
            raise VerificationError("response_total_invalid")

        stop_ref = manifest.get("stop_record_ref")
        if stop_ref is not None:
            artifact_ref = _mapping(stop_ref, ARTIFACT_FIELDS, code="stop_ref_invalid")
            relative = _path(artifact_ref.get("relative_path"), field="stop_path")
            if relative != "stop-record.json" or artifact_ref.get("kind") != "stop_record" or terminal:
                raise VerificationError("stop_ref_invalid")
            raw = read_regular(attempt_dir / relative, maximum=1_000_000, require_read_only=require_sealed)
            stop = strict_json_loads(raw, numbers=False)
            stop_value = _mapping(stop, STOP_FIELDS, code="stop_schema_invalid")
            if canonical_json_bytes(stop_value) != raw or stop_value.get("schema_version") != STOP_SCHEMA:
                raise VerificationError("stop_schema_invalid")
            if (
                stop_value.get("authorization_id") != authorization.authorization_id
                or stop_value.get("attempt_id") != authorization.attempt_id
            ):
                raise VerificationError("stop_identity_invalid")
            stop_code = stop_value.get("stop_code")
            if not isinstance(stop_code, str) or stop_code not in STOP_CODES:
                raise VerificationError("stop_code_invalid")
            if stop_value.get("next_ordinal") != (unattempted[0] if unattempted else None):
                raise VerificationError("stop_next_ordinal_invalid")
            if artifact_ref.get("sha256") != sha256_bytes(raw) or artifact_ref.get("byte_count") != len(raw):
                raise VerificationError("stop_hash_invalid")
            expected_files[relative] = dict(artifact_ref)
        elif unattempted and not terminal:
            raise VerificationError("stop_missing")

        sealed_at = parse_time(manifest.get("sealed_at"), field="manifest_sealed_at")
        deadline = parse_time(manifest.get("execution_deadline"), field="execution_deadline")
        if deadline != authorization.not_after:
            raise VerificationError("execution_deadline_invalid")
        if any(receipt.captured_at < authorization.not_before for receipt in receipts):
            raise VerificationError("receipt_before_authorization_window")
        latest_support_time = max(receipt_seal_times, default=sealed_at)
        authorization_window = all(item <= deadline for item in receipt_seal_times) and sealed_at <= deadline
        if stop_ref is not None:
            stop_time = parse_time(stop_value.get("stopped_at"), field="stopped_at")
            latest_support_time = max(latest_support_time, stop_time)
            if stop_code == "authorization_not_yet_valid" and stop_time >= authorization.not_before:
                raise VerificationError("stop_time_invalid")
            if stop_code == "authorization_expired" and stop_time < deadline:
                raise VerificationError("stop_time_invalid")
            if stop_code in {"authorization_not_yet_valid", "authorization_expired"} or stop_time > deadline:
                authorization_window = False
        if sealed_at < latest_support_time:
            raise VerificationError("manifest_sealed_at_invalid")

        if isinstance(authorization, FrameAuthorization):
            reproduction = reproduce_frame(
                authorization, parsed, complete=complete, authorization_window=authorization_window
            )
        else:
            reproduction = reproduce_date(
                authorization,
                parsed,
                receipts,
                complete=complete,
                authorization_window=authorization_window,
                sealed_at=sealed_at,
            )
        if manifest.get("gate_results") != reproduction.gates:
            raise VerificationError("gate_results_invalid")
        overall_pass = reproduction.gates["overall_pass"] is True

        commit_ref_value = _mapping(
            manifest.get("supervisor_commit_ref"),
            SUPERVISOR_COMMIT_REF_FIELDS,
            code="supervisor_commit_ref_invalid",
        )
        nonce = commit_ref_value.get("nonce")
        if not isinstance(nonce, str) or HASH_RE.fullmatch(nonce) is None:
            raise VerificationError("supervisor_commit_ref_invalid")
        commit_value = {
            "attempt_id": authorization.attempt_id,
            "authorization_id": authorization.authorization_id,
            "execution_deadline": manifest.get("execution_deadline"),
            "nonce": nonce,
            "outcome": "pass" if overall_pass else "fail",
            "schema_version": SUPERVISOR_COMMIT_SCHEMA,
        }
        commit_raw = canonical_json_bytes(commit_value)
        commit_artifact = {key: commit_ref_value[key] for key in ARTIFACT_FIELDS}
        if (
            commit_artifact.get("relative_path") != "supervisor-commit.json"
            or commit_artifact.get("kind") != "supervisor_commit"
            or commit_artifact.get("byte_count") != len(commit_raw)
            or commit_artifact.get("sha256") != sha256_bytes(commit_raw)
        ):
            raise VerificationError("supervisor_commit_ref_invalid")
        commit_path = attempt_dir / "supervisor-commit.json"
        if require_sealed:
            actual_commit = read_regular(commit_path, maximum=4096, require_read_only=True)
            parsed_commit = strict_json_loads(actual_commit, numbers=False)
            if (
                actual_commit != commit_raw
                or _mapping(parsed_commit, SUPERVISOR_COMMIT_FIELDS, code="supervisor_commit_invalid") != commit_value
            ):
                raise VerificationError("supervisor_commit_invalid")
        elif commit_path.exists() or commit_path.is_symlink():
            raise VerificationError("premature_supervisor_commit")
        expected_files["supervisor-commit.json"] = commit_artifact

        ledger_ref = manifest.get("exclusion_ledger_ref")
        counts = manifest.get("exclusion_counts")
        if reproduction.ledger is not None:
            ref = _mapping(ledger_ref, ARTIFACT_FIELDS, code="ledger_ref_invalid")
            if ref.get("relative_path") != "exclusion-ledger.json" or ref.get("kind") != "exclusion_ledger":
                raise VerificationError("ledger_ref_invalid")
            raw = read_regular(
                attempt_dir / "exclusion-ledger.json", maximum=32 * 1024 * 1024, require_read_only=require_sealed
            )
            expected_raw = canonical_json_bytes(reproduction.ledger)
            if raw != expected_raw or ref.get("sha256") != sha256_bytes(raw) or ref.get("byte_count") != len(raw):
                raise VerificationError("ledger_drift")
            if counts != exclusion_counts(reproduction.ledger):
                raise VerificationError("exclusion_counts_invalid")
            expected_files["exclusion-ledger.json"] = dict(ref)
        elif ledger_ref is not None or counts is not None:
            raise VerificationError("date_ledger_must_be_null")

        evidence_ref = manifest.get("date_evidence_ref")
        if reproduction.evidence is not None:
            ref = _mapping(evidence_ref, ARTIFACT_FIELDS, code="date_evidence_ref_invalid")
            if ref.get("relative_path") != "date-selection-evidence.json" or ref.get("kind") != "date_evidence":
                raise VerificationError("date_evidence_ref_invalid")
            raw = read_regular(
                attempt_dir / "date-selection-evidence.json", maximum=2 * 1024 * 1024, require_read_only=require_sealed
            )
            expected_raw = canonical_json_bytes(reproduction.evidence)
            if raw != expected_raw or ref.get("sha256") != sha256_bytes(raw) or ref.get("byte_count") != len(raw):
                raise VerificationError("date_evidence_drift")
            expected_files["date-selection-evidence.json"] = dict(ref)
        elif evidence_ref is not None:
            raise VerificationError("failed_date_evidence_ref_not_null")

        expected_disposition = (
            ("ELIGIBLE_FOR_FULL_CAPTURE_AUTHORIZATION" if overall_pass else "NO_QUALIFIED_FRAME_SOURCE")
            if expected_kind == "capability"
            else ("FRAME_CAPTURE_ELIGIBLE" if overall_pass else "CAPTURE_FAILED_CLOSED")
            if expected_kind == "capture"
            else ("DATE_EVIDENCE_VALID" if overall_pass else "DATE_EVIDENCE_FAILED")
        )
        eligible = expected_kind == "capture" and overall_pass
        if (
            manifest.get("disposition") != expected_disposition
            or manifest.get("capture_input_eligible") is not eligible
            or manifest.get("non_adoptable") is not (not eligible)
            or manifest.get("date_selection_only") is not (expected_kind == "date_selection_evidence")
        ):
            raise VerificationError("manifest_outcome_invalid")

        artifact_files = manifest.get("artifact_files")
        if not isinstance(artifact_files, list) or artifact_files != [
            expected_files[key] for key in sorted(expected_files)
        ]:
            raise VerificationError("artifact_closed_set_manifest_invalid")
        actual: set[str] = set()
        actual_directories: set[str] = set()
        for path in attempt_dir.rglob("*"):
            if path.is_symlink():
                raise VerificationError("attempt_contains_symlink")
            if path.is_dir():
                actual_directories.add(path.relative_to(attempt_dir).as_posix())
                if require_sealed and stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) != 0o555:
                    raise VerificationError("directory_permissions_invalid")
            elif path.is_file():
                actual.add(path.relative_to(attempt_dir).as_posix())
        expected_actual = set(expected_files) | ({"attempt-manifest.json"} if manifest_path.exists() else set())
        if not require_sealed:
            expected_actual.discard("supervisor-commit.json")
        if actual != expected_actual:
            raise VerificationError("artifact_closed_set_invalid")
        expected_directories = {
            str(Path(relative).parent) for relative in expected_actual if str(Path(relative).parent) != "."
        }
        if actual_directories != expected_directories:
            raise VerificationError("artifact_directory_set_invalid")
        return AttemptResult(
            attempt_dir,
            manifest_path,
            sha256_bytes(manifest_raw),
            authorization.authorization_id,
            authorization.attempt_id,
            expected_kind,
            complete,
            overall_pass,
            expected_disposition,
            eligible,
            tuple(receipts),
        )
    finally:
        visited.discard(resolved)


def verify_segmented_rest_attempt(attempt_dir: str | Path) -> AttemptResult:
    """Reproduce a sealed v1.3.1 attempt without token, database, or network access."""
    return _verify_attempt(Path(attempt_dir))


def verify_manifest_candidate(attempt_dir: Path, raw: bytes) -> AttemptResult:
    """Verify exact candidate bytes before their create-only publication."""
    return _verify_attempt(attempt_dir, candidate_raw=raw, require_sealed=False)


__all__ = ["verify_segmented_rest_attempt"]
