"""Offline three-axis source-capability probe for the M5 v1.2 redesign."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from qualitative_v2_m5 import (
    MAX_JSON_BYTES,
    EXPECTED_SAMPLE_SHA256,
    _canonical_json,
    _load_json_bytes,
    _read_regular_file,
    _safe_relative_file,
)
from qualitative_v2_m5_data_auth import MAX_RESPONSE_BYTES, DataAuthorization, load_data_authorization
from qualitative_v2_m5_source_frontdoors import FRONT_DOOR_PAGES, UI_ASSETS

PROJECT_ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = "m5-source-capability-probe-v1.2"
REPORT_FILENAME = "report.json"
SOURCE_ORDER = ("CNINFO", "SSE", "SZSE", "CNIPA")

_FRONTDOOR_FIELDS = {
    "schema_version",
    "authorization_id",
    "authorization_sha256",
    "data_run_id",
    "sample_sha256",
    "captured_at",
    "request_timeout_seconds",
    "max_response_bytes",
    "http_attempts",
    "search_http_budget_consumed",
    "pages",
}
_RESOURCE_FIELDS = {
    "source",
    "requested_url",
    "final_url",
    "status_code",
    "content_type",
    "byte_count",
    "raw_path",
    "raw_sha256",
    "http_attempts",
    "redirect_count",
    "status",
    "sanitized_error_class",
}
_ASSET_FIELDS = {
    "schema_version",
    "authorization_id",
    "authorization_sha256",
    "data_run_id",
    "sample_sha256",
    "captured_at",
    "parent_frontdoors_manifest_sha256",
    "request_timeout_seconds",
    "max_response_bytes",
    "http_attempts",
    "search_http_budget_consumed",
    "assets",
}


class CapabilityProbeError(ValueError):
    """A sealed input or requested report violates the v1.2 probe contract."""


def _canonical_bytes(value: object) -> bytes:
    return (_canonical_json(value) + "\n").encode("utf-8")


def _manifest(path: Path, *, label: str, fields: set[str]) -> tuple[dict[str, object], bytes]:
    try:
        raw = _read_regular_file(path, maximum_bytes=MAX_JSON_BYTES)
        value = _load_json_bytes(raw, label=label)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CapabilityProbeError(f"{label} is missing or invalid") from exc
    if set(value) != fields or raw != _canonical_bytes(value):
        raise CapabilityProbeError(f"{label} is non-canonical or has contract drift")
    return value, raw


def _identity(value: dict[str, object], authorization: DataAuthorization, *, label: str) -> None:
    if (
        value.get("authorization_id") != authorization.authorization_id
        or value.get("authorization_sha256") != authorization.sha256
        or value.get("data_run_id") != authorization.data_run_id
        or value.get("sample_sha256") != EXPECTED_SAMPLE_SHA256
    ):
        raise CapabilityProbeError(f"{label} identity does not match the sealed authorization")


def _resource_rows(value: object, *, expected_sources: tuple[str, ...], label: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) != len(expected_sources):
        raise CapabilityProbeError(f"{label} must contain the exact source rows")
    rows: list[dict[str, object]] = []
    for expected_source, raw_row in zip(expected_sources, value, strict=True):
        if not isinstance(raw_row, dict) or set(raw_row) != _RESOURCE_FIELDS:
            raise CapabilityProbeError(f"{label} row contract drift")
        row = cast(dict[str, object], raw_row)
        if row["source"] != expected_source or row["status"] not in {"success", "technical_error"}:
            raise CapabilityProbeError(f"{label} source order or status drift")
        if (
            isinstance(row["http_attempts"], bool)
            or not isinstance(row["http_attempts"], int)
            or row["http_attempts"] < 1
        ):
            raise CapabilityProbeError(f"{label} has invalid HTTP attempt count")
        if (
            isinstance(row["redirect_count"], bool)
            or not isinstance(row["redirect_count"], int)
            or row["redirect_count"] < 0
            or row["http_attempts"] != 1 + row["redirect_count"]
        ):
            raise CapabilityProbeError(f"{label} has invalid redirect accounting")
        if row["status"] == "success" and (
            row["sanitized_error_class"] is not None
            or isinstance(row["status_code"], bool)
            or not isinstance(row["status_code"], int)
            or not 200 <= row["status_code"] < 300
        ):
            raise CapabilityProbeError(f"{label} has inconsistent success metadata")
        if row["status"] == "technical_error" and not isinstance(row["sanitized_error_class"], str):
            raise CapabilityProbeError(f"{label} has inconsistent technical-error metadata")
        rows.append(row)
    return rows


def _same_origin(requested_url: object, final_url: object, *, label: str) -> None:
    if not isinstance(requested_url, str) or not isinstance(final_url, str):
        raise CapabilityProbeError(f"{label} URL is invalid")
    requested = urlsplit(requested_url)
    final = urlsplit(final_url)
    try:
        requested_port = requested.port
        final_port = final.port
    except ValueError as exc:
        raise CapabilityProbeError(f"{label} URL authority is invalid") from exc
    if (
        requested.scheme != "https"
        or final.scheme != "https"
        or requested.hostname is None
        or final.hostname != requested.hostname
        or requested_port not in (None, 443)
        or final_port not in (None, 443)
        or requested.username is not None
        or requested.password is not None
        or final.username is not None
        or final.password is not None
    ):
        raise CapabilityProbeError(f"{label} URL is not verified same-origin HTTPS")


def _verified_raw(manifest_path: Path, row: dict[str, object], *, required: bool) -> bytes | None:
    raw_path = row["raw_path"]
    raw_sha256 = row["raw_sha256"]
    byte_count = row["byte_count"]
    if raw_path is None and raw_sha256 is None and byte_count == 0 and not required:
        return None
    if (
        not isinstance(raw_path, str)
        or not isinstance(raw_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", raw_sha256) is None
        or isinstance(byte_count, bool)
        or not isinstance(byte_count, int)
        or byte_count <= 0
    ):
        raise CapabilityProbeError("source raw-byte metadata is incomplete")
    try:
        _path, raw = _safe_relative_file(manifest_path.parent, raw_path, maximum_bytes=MAX_RESPONSE_BYTES)
    except ValueError as exc:
        raise CapabilityProbeError("source raw bytes are missing or unsafe") from exc
    if len(raw) != byte_count or hashlib.sha256(raw).hexdigest() != raw_sha256:
        raise CapabilityProbeError("source raw bytes have hash or size drift")
    return raw


def _frontdoors(
    path: Path,
    authorization: DataAuthorization,
) -> tuple[dict[str, object], bytes, dict[str, dict[str, object]]]:
    value, raw = _manifest(path, label="front-door manifest", fields=_FRONTDOOR_FIELDS)
    if value["schema_version"] != "m5-source-frontdoors-v1":
        raise CapabilityProbeError("front-door manifest version drift")
    _identity(value, authorization, label="front-door manifest")
    rows = _resource_rows(value["pages"], expected_sources=SOURCE_ORDER, label="front-door manifest")
    expected_urls = dict(FRONT_DOOR_PAGES)
    by_source: dict[str, dict[str, object]] = {}
    for row in rows:
        source = cast(str, row["source"])
        if row["requested_url"] != expected_urls[source]:
            raise CapabilityProbeError("front-door requested URL drift")
        _same_origin(row["requested_url"], row["final_url"], label="front-door")
        required = row["status"] == "success"
        body = _verified_raw(path, row, required=required)
        if required and (row["status_code"] != 200 or body is None):
            raise CapabilityProbeError("successful front door lacks a valid HTTP-200 body")
        by_source[source] = row
    return value, raw, by_source


def _assets(
    path: Path,
    authorization: DataAuthorization,
    *,
    frontdoor_sha256: str,
) -> tuple[dict[str, object], bytes, dict[str, bytes]]:
    value, raw = _manifest(path, label="UI-asset manifest", fields=_ASSET_FIELDS)
    if value["schema_version"] != "m5-source-ui-assets-v1":
        raise CapabilityProbeError("UI-asset manifest version drift")
    _identity(value, authorization, label="UI-asset manifest")
    if value["parent_frontdoors_manifest_sha256"] != frontdoor_sha256:
        raise CapabilityProbeError("UI-asset manifest parent hash drift")
    expected_sources = tuple(source for source, _url, _filename in UI_ASSETS)
    rows = _resource_rows(value["assets"], expected_sources=expected_sources, label="UI-asset manifest")
    expected = {source: (url, filename) for source, url, filename in UI_ASSETS}
    by_source: dict[str, bytes] = {}
    for row in rows:
        source = cast(str, row["source"])
        url, filename = expected[source]
        if row["requested_url"] != url or row["raw_path"] != filename:
            raise CapabilityProbeError("UI-asset URL or filename drift")
        _same_origin(row["requested_url"], row["final_url"], label="UI-asset")
        body = _verified_raw(path, row, required=row["status"] == "success")
        if row["status"] == "success":
            if row["status_code"] != 200 or body is None:
                raise CapabilityProbeError("successful UI asset lacks a valid HTTP-200 body")
            by_source[source] = body
    return value, raw, by_source


def _utf8_controller(raw: bytes, *, source: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CapabilityProbeError(f"{source} controller is not UTF-8") from exc


def _fulltext_axis(source: str, controllers: dict[str, bytes]) -> tuple[str, list[str], str]:
    raw = controllers.get(source)
    if raw is None:
        return "not_evaluated", [], "no_verified_query_controller"
    text = _utf8_controller(raw, source=source)
    digest = hashlib.sha256(raw).hexdigest()
    if source == "CNINFO":
        signatures = {
            "title_plus_fulltext_label": r"key\s*:\s*['\"]标题\+全文['\"]",
            "fulltext_endpoint": r"/fulltextSearch/full",
            "fulltext_boolean": r"isfulltext\s*:\s*self\.searchType\s*==\s*0\s*\?\s*false\s*:\s*true",
            "result_page_size_20": r"pageSize\s*:\s*20",
        }
        matched = [name for name, pattern in signatures.items() if re.search(pattern, text)]
        if len(matched) == len(signatures):
            return "demonstrated", matched, f"controller_sha256:{digest}"
        return "not_demonstrated", matched, f"controller_sha256:{digest}"
    if source == "SSE":
        signatures = {
            "title_request_parameter": r"TITLE\s*:\s*['\"]{2}",
            "official_query_controller": r"queryCompanyBulletinNew\.do",
            "main_body_marker_removed": r"replace\(['\"]hasDelMain['\"],\s*['\"]{2}\)",
        }
        matched = [name for name, pattern in signatures.items() if re.search(pattern, text)]
        fulltext_flag = re.search(r"isfulltext|full.?text", text, flags=re.IGNORECASE)
        if len(matched) == len(signatures) and fulltext_flag is None:
            return "not_demonstrated", matched, f"controller_sha256:{digest}"
        return "not_evaluated", matched, f"controller_sha256:{digest}"
    return "not_evaluated", [], "no_source_specific_semantic_contract"


def capability_report(
    sample_path: str | os.PathLike[str],
    authorization_path: str | os.PathLike[str],
    checksum_path: str | os.PathLike[str],
    frontdoor_manifest_path: str | os.PathLike[str],
    ui_asset_manifest_path: str | os.PathLike[str],
) -> dict[str, object]:
    """Build a deterministic report that never conflates transport, semantics, and evidence quality."""
    sample = Path(sample_path)
    authorization = load_data_authorization(sample, authorization_path, checksum_path)
    frontdoor_path = Path(frontdoor_manifest_path)
    ui_asset_path = Path(ui_asset_manifest_path)
    frontdoor_value, frontdoor_raw, frontdoor_rows = _frontdoors(frontdoor_path, authorization)
    frontdoor_sha256 = hashlib.sha256(frontdoor_raw).hexdigest()
    asset_value, asset_raw, controllers = _assets(
        ui_asset_path,
        authorization,
        frontdoor_sha256=frontdoor_sha256,
    )

    source_rows: list[dict[str, object]] = []
    viable_fulltext_sources: list[str] = []
    degraded_sources: list[str] = []
    for source in SOURCE_ORDER:
        frontdoor = frontdoor_rows[source]
        transport_status = "available" if frontdoor["status"] == "success" else "technical_error"
        fulltext_status, signals, semantic_basis = _fulltext_axis(source, controllers)
        eligible = transport_status == "available" and fulltext_status == "demonstrated"
        if eligible:
            viable_fulltext_sources.append(source)
        else:
            degraded_sources.append(source)
        source_rows.append(
            {
                "source": source,
                "transport": {
                    "status": transport_status,
                    "status_code": frontdoor["status_code"],
                    "sanitized_error_class": frontdoor["sanitized_error_class"],
                    "frontdoor_raw_sha256": frontdoor["raw_sha256"],
                },
                "full_text_semantics": {
                    "status": fulltext_status,
                    "matched_signals": signals,
                    "basis": semantic_basis,
                },
                "evidence_quality": {
                    "status": "not_evaluated",
                    "reason": "no_candidate_documents_in_capability_inputs",
                },
                "candidate_fulltext_collection_eligible": eligible,
            }
        )

    minimum_route = bool(viable_fulltext_sources)
    observed_at = max(cast(str, frontdoor_value["captured_at"]), cast(str, asset_value["captured_at"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": authorization.sha256,
        "data_run_id": authorization.data_run_id,
        "sample_sha256": EXPECTED_SAMPLE_SHA256,
        "observed_at": observed_at,
        "inputs": {
            "frontdoor_manifest_sha256": frontdoor_sha256,
            "ui_asset_manifest_sha256": hashlib.sha256(asset_raw).hexdigest(),
        },
        "sources": source_rows,
        "routing": {
            "viable_fulltext_sources": viable_fulltext_sources,
            "source_local_degradations": degraded_sources,
            "minimum_viable_official_fulltext_route": minimum_route,
            "single_source_failure_global_block": False,
            "global_capability_blocked": not minimum_route,
        },
        "candidate_policy": {
            "transport_gate": "source_local",
            "full_text_gate": "source_local_demonstrated_only",
            "evidence_quality_gate": "separate_bounded_pilot_required",
            "failed_source_behavior": "exclude_from_candidate_route_and_report_degradation",
            "silent_title_only_fallback": False,
            "requires_user_approved_collection_protocol": True,
        },
        "lifecycle": {
            "current_v1_1_run_resumable": False,
            "collection_protocol_frozen": False,
            "protocol_freeze_ready": False,
            "next_action": "authorize_bounded_evidence_quality_pilot",
        },
        "network_calls_performed": 0,
        "database_reads_performed": 0,
        "credential_variables_read": 0,
        "reviewer_calls_performed": 0,
        "model_calls_performed": 0,
    }


def _safe_output(authorization: DataAuthorization, output_path: str | os.PathLike[str]) -> Path:
    relative = Path(authorization.artifact_root)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != authorization.artifact_root:
        raise CapabilityProbeError("authorization artifact root is unsafe")
    expected = PROJECT_ROOT / relative / "capability-probe-v1.2" / REPORT_FILENAME
    output = Path(output_path).absolute()
    if output != expected:
        raise CapabilityProbeError("capability report must use the authorization-bound output path")
    cursor = PROJECT_ROOT
    for part in expected.relative_to(PROJECT_ROOT).parts:
        cursor /= part
        if cursor.is_symlink():
            raise CapabilityProbeError("capability-report ancestry contains a symlink")
        if cursor.exists() and not cursor.is_dir() and cursor != expected:
            raise CapabilityProbeError("capability-report ancestry contains a non-directory")
    if output.exists() or output.is_symlink():
        raise CapabilityProbeError("capability report already exists")
    return output


def seal_capability_report(
    sample_path: str | os.PathLike[str],
    authorization_path: str | os.PathLike[str],
    checksum_path: str | os.PathLike[str],
    frontdoor_manifest_path: str | os.PathLike[str],
    ui_asset_manifest_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
) -> dict[str, object]:
    """Publish one create-only private capability report after full offline validation."""
    sample = Path(sample_path)
    authorization = load_data_authorization(sample, authorization_path, checksum_path)
    output = _safe_output(authorization, output_path)
    report = capability_report(
        sample,
        authorization_path,
        checksum_path,
        frontdoor_manifest_path,
        ui_asset_manifest_path,
    )
    raw = _canonical_bytes(report)
    output.parent.mkdir(parents=True, exist_ok=False, mode=0o700)
    output.parent.chmod(0o700)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            output.unlink()
        except OSError:
            pass
        raise
    return {
        "validated": True,
        "output_path": str(output.relative_to(PROJECT_ROOT)),
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        "minimum_viable_official_fulltext_route": cast(dict[str, object], report["routing"])[
            "minimum_viable_official_fulltext_route"
        ],
        "collection_protocol_frozen": False,
        "network_calls": 0,
        "database_reads": 0,
        "model_calls": 0,
    }


__all__ = [
    "CapabilityProbeError",
    "REPORT_FILENAME",
    "SCHEMA_VERSION",
    "capability_report",
    "seal_capability_report",
]
