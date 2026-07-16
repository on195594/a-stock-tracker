from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.request import Request

import pandas as pd
import pytest

from qualitative_v2_audit import SW2021_INDUSTRIES, canonical_json_bytes
from scripts.export_m4_sampling_frame_akshare import ExportError
from scripts.export_m4_sampling_frame_historical_hybrid import (
    AUTHORIZATION_SCHEMA_VERSION,
    CALL_ID_DAILY_BASIC,
    CALL_ID_SSE_SUMMARY,
    FROZEN_CALL_IDS,
    CaptureBlockedError,
    CapturedResponse,
    HistoricalCaptureBackend,
    assemble_historical_sampling_frame,
    capture_historical_sources,
    recover_incomplete_capture,
)

CAPTURE_TIME = datetime.fromisoformat("2026-07-16T10:00:00+08:00")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _make_writable(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        child.chmod(0o755 if child.is_dir() else 0o644)
    path.chmod(0o755)


def _make_szse_package(root: Path) -> Path:
    package = root / "manual-szse"
    package.mkdir()
    dataframe = pd.DataFrame(
        [
            ["主板A股", 1494, 1, 100, 80],
            ["创业板A股", 1399, 1, 100, 80],
            ["主板B股", 38, None, None, None],
        ],
        columns=["证券类别", "数量", "成交金额", "总市值", "流通市值"],
    )
    output = io.BytesIO()
    dataframe.to_excel(output, index=False)
    xlsx = output.getvalue()
    (package / "ShowReport.xlsx").write_bytes(xlsx)
    provenance = {
        "acquisition": {
            "attempts": 1,
            "final_origin": "https://www.szse.cn",
            "redirect_policy": "reject_cross_domain",
            "tool": "synthetic browser",
            "tool_image": "synthetic@sha256:" + "0" * 64,
            "transport": "HTTPS with browser certificate validation",
        },
        "files": [
            {
                "acquired_at": "2026-07-16T09:00:00+08:00",
                "byte_count": len(xlsx),
                "filename": "ShowReport.xlsx",
                "format": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "row_count": 3,
                "sha256": _sha256(xlsx),
            }
        ],
        "publisher": "深圳证券交易所",
        "request": {
            "method": "browser navigation/download",
            "parameters": {
                "CATALOGID": "1803_sczm",
                "SHOWTYPE": "xlsx",
                "TABKEY": "tab1",
                "txtQueryDate": "2026-07-15",
            },
            "url": "https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1803_sczm"
            "&TABKEY=tab1&txtQueryDate=2026-07-15",
        },
        "schema_version": "m4-manual-szse-capture-v1",
        "source_date": "2026-07-15",
        "source_date_basis": "date-bound official SZSE request parameter",
        "validation": {
            "cross_domain_redirect_observed": False,
            "normalized_table_sha256": _sha256(dataframe.to_csv(index=False, lineterminator="\n").encode("utf-8")),
            "raw_files_semantically_equal": True,
            "xlsx_magic_valid": True,
            "xlsx_zip_integrity_valid": True,
        },
    }
    provenance_raw = canonical_json_bytes(provenance)
    (package / "provenance.json").write_bytes(provenance_raw)
    (package / "SHA256SUMS").write_text(
        f"{_sha256(xlsx)}  ShowReport.xlsx\n{_sha256(provenance_raw)}  provenance.json\n",
        encoding="utf-8",
    )
    return package


def _make_authorization(
    root: Path,
    attempt_id: str,
    *,
    not_before: str = "2026-07-16T00:00:00+08:00",
    not_after: str = "2026-07-16T23:59:59+08:00",
) -> tuple[Path, Path]:
    auth = root / f"authorization-{attempt_id}.json"
    raw = canonical_json_bytes(
        {
            "schema_version": AUTHORIZATION_SCHEMA_VERSION,
            "authorization_id": f"synthetic-auth-{attempt_id}",
            "attempt_id": attempt_id,
            "sampling_date": "2026-07-15",
            "not_before": not_before,
            "not_after": not_after,
            "allowed_calls": list(FROZEN_CALL_IDS),
        }
    )
    auth.write_bytes(raw)
    checksum = root / f"authorization-{attempt_id}.sha256"
    checksum.write_text(f"{_sha256(raw)}  {auth.name}\n", encoding="ascii")
    return auth, checksum


def _stocks() -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    counter = 0
    for industry_code in SW2021_INDUSTRIES:
        for _ in range(130):
            counter += 1
            code = str((600000 if counter % 2 else 300000) + counter)
            result[code] = (f"公司{counter}", industry_code)
    return result


class SyntheticBackend:
    def __init__(
        self,
        *,
        fail_call: str | None = None,
        malformed_call: str | None = None,
        echoed_token: str | None = None,
        response_host: str | None = None,
    ) -> None:
        self.stocks = _stocks()
        self.fail_call = fail_call
        self.malformed_call = malformed_call
        self.echoed_token = echoed_token
        self.response_host = response_host
        self.calls: list[str] = []

    def capture(self, call_id: str, request: Mapping[str, object]) -> CapturedResponse:
        self.calls.append(call_id)
        if call_id == self.fail_call:
            raise ExportError("synthetic mid-capture failure")
        if call_id == self.malformed_call:
            rows_raw = b"not-json"
        elif call_id == CALL_ID_DAILY_BASIC:
            items = [
                [f"{code}.{'SH' if code.startswith('6') else 'SZ'}", "20260715", f"{index}.123456"]
                for index, code in enumerate(self.stocks, start=1)
            ]
            rows_raw = json.dumps(
                {
                    "request_id": "synthetic",
                    "code": 0,
                    "msg": None,
                    "data": {
                        "fields": ["ts_code", "trade_date", "total_mv"],
                        "items": items,
                        "count": 0,
                        "has_more": False,
                    },
                },
                separators=(",", ":"),
            ).encode()
        elif call_id == CALL_ID_SSE_SUMMARY:
            values = [["1", "1", "1"] for _ in range(10)]
            values[1] = ["123456", "120000", "3456"]
            values[6] = ["20260715", "", ""]
            rows_raw = canonical_json_bytes({"result": {str(index): row for index, row in enumerate(values)}})
        else:
            symbol = call_id.rsplit(":", 1)[-1] + ".SI"
            rows_raw = canonical_json_bytes(
                {
                    "data": {
                        "results": [
                            {
                                "stockcode": code,
                                "stockname": name,
                                "beginningdate": "20210101",
                            }
                            for code, (name, industry) in self.stocks.items()
                            if industry == symbol
                        ]
                    }
                }
            )
        if call_id == CALL_ID_DAILY_BASIC:
            if self.echoed_token:
                rows_raw += self.echoed_token.encode()
            return CapturedResponse(
                "https://api.tushare.pro",
                f"https://{self.response_host or 'api.tushare.pro'}",
                200,
                "application/json",
                rows_raw,
            )
        if call_id == CALL_ID_SSE_SUMMARY:
            host = "query.sse.com.cn"
            path = "/commonQuery.do"
            params = (
                ("sqlId", "COMMON_SSE_SJ_GPSJ_GPSJZM_TJSJ_L"),
                ("PRODUCT_NAME", "股票,主板,科创板"),
                ("type", "inParams"),
            )
        else:
            host = "www.swsresearch.com"
            path = "/institute-sw/api/index_publish/details/component_stocks/"
            params = (
                ("swindexcode", call_id.rsplit(":", 1)[-1]),
                ("page", "1"),
                ("page_size", "10000"),
            )
        request_url = "https://" + host + path + "?" + "&".join(f"{key}={value}" for key, value in params)
        response_host = self.response_host or host
        return CapturedResponse(
            request_url,
            "https://" + response_host + path + "?" + "&".join(f"{key}={value}" for key, value in params),
            200,
            "application/json",
            rows_raw,
            params,
        )


def _capture(tmp_path: Path, attempt_id: str, backend: SyntheticBackend):
    auth, checksum = _make_authorization(tmp_path, attempt_id)
    return capture_historical_sources(
        capture_root=tmp_path / "captures",
        attempt_id=attempt_id,
        now=datetime.fromisoformat("2026-07-16T10:00:00+08:00"),
        szse_package=_make_szse_package(tmp_path)
        if not (tmp_path / "manual-szse").exists()
        else tmp_path / "manual-szse",
        authorization_path=auth,
        authorization_hash_path=checksum,
        backend=backend,
        token="test-secret",
        clock=lambda: CAPTURE_TIME,
    )


def test_complete_capture_and_offline_assembly_are_read_only_and_deterministic(tmp_path: Path) -> None:
    result = _capture(tmp_path, "attempt-one", SyntheticBackend())
    try:
        assert result.complete is True
        assert result.captured_call_ids == FROZEN_CALL_IDS
        assert stat.S_IMODE(result.attempt_dir.stat().st_mode) == 0o555
        assert len(list((result.attempt_dir / "receipts").glob("*.json"))) == 33
        assembly = assemble_historical_sampling_frame(
            attempt_dir=result.attempt_dir,
            output_root=tmp_path / "assembled-one",
        )
        second = assemble_historical_sampling_frame(
            attempt_dir=result.attempt_dir,
            output_root=tmp_path / "assembled-two",
        )
        assert assembly.row_count == 31 * 130
        assert assembly.sample_count == 36
        assert assembly.frame_sha256 == second.frame_sha256
        assert assembly.frame_path.read_bytes() == second.frame_path.read_bytes()
        assert sorted(path.name for path in assembly.package_dir.iterdir()) == [
            "SHA256SUMS",
            "excluded.csv",
            "frame-provenance.json",
            "frame.csv",
            "sample-manifest.json",
        ]
        assert stat.S_IMODE(assembly.frame_path.stat().st_mode) == 0o444
    finally:
        _make_writable(result.attempt_dir)
        for root in (tmp_path / "assembled-one", tmp_path / "assembled-two"):
            for package in root.glob("historical-frame-*"):
                _make_writable(package)


def test_mid_capture_failure_is_sealed_incomplete_and_resume_fetches_only_missing(tmp_path: Path) -> None:
    failed_call = FROZEN_CALL_IDS[10]
    with pytest.raises(CaptureBlockedError, match="mid-capture") as raised:
        _capture(tmp_path, "attempt-failed", SyntheticBackend(fail_call=failed_call))
    incomplete = raised.value.result
    assert incomplete is not None
    assert incomplete.complete is False
    backend = SyntheticBackend()
    auth, checksum = _make_authorization(tmp_path, "attempt-resumed")
    resumed = capture_historical_sources(
        capture_root=tmp_path / "captures",
        attempt_id="attempt-resumed",
        now=datetime.fromisoformat("2026-07-16T11:00:00+08:00"),
        szse_package=tmp_path / "manual-szse",
        authorization_path=auth,
        authorization_hash_path=checksum,
        backend=backend,
        token="test-secret",
        resume_attempt=incomplete.attempt_dir,
        clock=lambda: datetime.fromisoformat("2026-07-16T11:00:00+08:00"),
    )
    try:
        assert resumed.complete
        assert backend.calls == list(FROZEN_CALL_IDS[10:])
        manifest = json.loads(resumed.manifest_path.read_bytes())
        assert manifest["parent_manifest_sha256"] == incomplete.manifest_sha256
        assert manifest["resume_lineage"][0]["attempt_id"] == "attempt-failed"
    finally:
        _make_writable(incomplete.attempt_dir)
        _make_writable(resumed.attempt_dir)


def test_raw_only_revalidation_without_authorization_never_calls_backend(tmp_path: Path) -> None:
    result = _capture(tmp_path, "attempt-raw-only", SyntheticBackend(malformed_call=FROZEN_CALL_IDS[-1]))
    backend = SyntheticBackend()
    with pytest.raises(ExportError, match="require a new valid authorization"):
        capture_historical_sources(
            capture_root=tmp_path / "captures",
            attempt_id="attempt-no-auth",
            now=datetime.fromisoformat("2026-07-16T11:00:00+08:00"),
            szse_package=tmp_path / "manual-szse",
            backend=backend,
            resume_attempt=result.attempt_dir,
            clock=lambda: datetime.fromisoformat("2026-07-16T11:00:00+08:00"),
        )
    assert backend.calls == []
    _make_writable(result.attempt_dir)


@pytest.mark.parametrize("case", ["token", "redirect"])
def test_token_echo_and_cross_domain_redirect_fail_before_publication(tmp_path: Path, case: str) -> None:
    backend = (
        SyntheticBackend(echoed_token="test-secret")
        if case == "token"
        else SyntheticBackend(response_host="evil.example")
    )
    with pytest.raises(CaptureBlockedError):
        _capture(tmp_path, f"attempt-{case}", backend)
    incomplete = next((tmp_path / "captures").glob(f"capture-attempt-{case}-incomplete"))
    assert not list((incomplete / "blobs").glob("*.blob"))
    _make_writable(incomplete)


def test_authorization_matrix_expiry_and_lock_fail_closed(tmp_path: Path) -> None:
    szse = _make_szse_package(tmp_path)
    auth, checksum = _make_authorization(tmp_path, "attempt-expired")
    with pytest.raises(ExportError, match="not currently valid"):
        capture_historical_sources(
            capture_root=tmp_path / "captures",
            attempt_id="attempt-expired",
            now=datetime.fromisoformat("2026-07-17T10:00:00+08:00"),
            szse_package=szse,
            authorization_path=auth,
            authorization_hash_path=checksum,
            backend=SyntheticBackend(),
        )
    capture_root = tmp_path / "captures"
    capture_root.mkdir(exist_ok=True)
    descriptor = os.open(capture_root / ".capture.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(ExportError, match="locked"):
            capture_historical_sources(
                capture_root=capture_root,
                attempt_id="attempt-locked",
                now=datetime.fromisoformat("2026-07-16T10:00:00+08:00"),
                szse_package=szse,
            )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_recover_preserves_orphan_blob_but_never_marks_complete(tmp_path: Path) -> None:
    capture_root = tmp_path / "captures"
    staging = capture_root / ".in-progress-crashed"
    blobs = staging / "blobs"
    blobs.mkdir(parents=True)
    orphan = b"exact orphan response"
    (blobs / f"{_sha256(orphan)}.blob").write_bytes(orphan)
    result = recover_incomplete_capture(
        capture_root=capture_root,
        attempt_id="crashed",
        szse_package=_make_szse_package(tmp_path),
    )
    try:
        assert result.complete is False
        manifest = json.loads(result.manifest_path.read_bytes())
        assert manifest["recovered_staging"] is True
        assert manifest["orphan_files"][0]["sha256"] == _sha256(orphan)
        with pytest.raises(ExportError, match="complete capture"):
            assemble_historical_sampling_frame(
                attempt_dir=result.attempt_dir,
                output_root=tmp_path / "assembled",
            )
    finally:
        _make_writable(result.attempt_dir)


def test_manifest_blob_and_static_tampering_are_detected(tmp_path: Path) -> None:
    result = _capture(tmp_path, "attempt-tamper", SyntheticBackend())
    _make_writable(result.attempt_dir)
    blob = next((result.attempt_dir / "blobs").glob("*.blob"))
    blob.write_bytes(b"tampered")
    with pytest.raises(ExportError, match="blob hash"):
        assemble_historical_sampling_frame(
            attempt_dir=result.attempt_dir,
            output_root=tmp_path / "assembled",
        )


def test_capture_does_not_persist_request_body_or_secret(tmp_path: Path) -> None:
    result = _capture(tmp_path, "attempt-secret", SyntheticBackend())
    try:
        assert not any(b"test-secret" in path.read_bytes() for path in result.attempt_dir.rglob("*") if path.is_file())
        receipt = json.loads((result.attempt_dir / "receipts" / "tushare_daily_basic_20260715.json").read_bytes())
        assert set(receipt["request"]) == {"arguments", "method", "query", "url"}
        assert "headers" not in receipt["request"] and "body" not in receipt["request"]
    finally:
        _make_writable(result.attempt_dir)


def test_tushare_raw_is_published_before_live_schema_parser_failure(tmp_path: Path) -> None:
    class FailAfterTushareRunner:
        version = "synthetic-fail"

        def call(self, function_name: str, arguments: Mapping[str, str]) -> Any:
            raise ExportError("stop after Tushare raw capture")

    items = [[f"{index:06d}.SH", "20260715", "1.5"] for index in range(1, 5526)]
    exact_raw = json.dumps(
        {
            "request_id": "synthetic",
            "code": 0,
            "msg": None,
            "extra": "live client rejects this envelope field",
            "data": {
                "fields": ["ts_code", "trade_date", "total_mv"],
                "items": items,
                "count": 0,
                "has_more": False,
            },
        },
        separators=(",", ":"),
    ).encode()

    def transport(request: Request, timeout: float) -> bytes:
        assert request.full_url == "https://api.tushare.pro"
        assert timeout > 0
        return exact_raw

    auth, checksum = _make_authorization(tmp_path, "attempt-raw-first")
    with pytest.raises(CaptureBlockedError) as raised:
        capture_historical_sources(
            capture_root=tmp_path / "captures",
            attempt_id="attempt-raw-first",
            now=datetime.fromisoformat("2026-07-16T10:00:00+08:00"),
            szse_package=_make_szse_package(tmp_path),
            authorization_path=auth,
            authorization_hash_path=checksum,
            token="test-secret",
            runner=FailAfterTushareRunner(),
            tushare_transport=transport,
            min_interval_seconds=0,
            clock=lambda: CAPTURE_TIME,
        )
    result = raised.value.result
    assert result is not None
    try:
        receipt_path = result.attempt_dir / "receipts" / "tushare_daily_basic_20260715.json"
        receipt = json.loads(receipt_path.read_bytes())
        blob_path = result.attempt_dir / receipt["response"]["blob_relative_path"]
        assert blob_path.read_bytes() == exact_raw
        assert receipt["response"]["sha256"] == _sha256(exact_raw)
        assert receipt["parsing"]["status"] == "parsed"
    finally:
        _make_writable(result.attempt_dir)


@pytest.mark.parametrize(
    ("process_exception", "exception_type"),
    [(KeyboardInterrupt(), KeyboardInterrupt), (SystemExit(7), SystemExit)],
)
def test_process_control_exception_stops_after_prepublished_response(
    tmp_path: Path,
    process_exception: BaseException,
    exception_type: type[BaseException],
) -> None:
    class NeverRunner:
        version = "never-called"

        def call(self, function_name: str, arguments: Mapping[str, str]) -> Any:
            raise AssertionError("process cancellation must stop before AKShare")

    class CancelAfterPrepublication(HistoricalCaptureBackend):
        def __init__(self) -> None:
            super().__init__(
                token="test-secret",
                runner=NeverRunner(),
                tushare_transport=lambda request, timeout: b"",
                min_interval_seconds=0,
            )
            self.calls: list[str] = []

        def capture(self, call_id: str, request: Mapping[str, object]) -> CapturedResponse:
            self.calls.append(call_id)
            self._active_call_id = call_id
            self._active_request = request
            raw = json.dumps(
                {
                    "request_id": "cancel",
                    "code": 0,
                    "msg": None,
                    "data": {
                        "fields": ["ts_code", "trade_date", "total_mv"],
                        "items": [["600001.SH", "20260715", "1.5"]],
                        "count": 0,
                        "has_more": False,
                    },
                },
                separators=(",", ":"),
            ).encode()
            self._publish_early(
                CapturedResponse(
                    "https://api.tushare.pro",
                    "https://api.tushare.pro",
                    200,
                    "application/json",
                    raw,
                )
            )
            raise process_exception

    attempt_id = f"attempt-cancel-{exception_type.__name__.lower()}"
    auth, checksum = _make_authorization(tmp_path, attempt_id)
    backend = CancelAfterPrepublication()
    with pytest.raises(exception_type):
        capture_historical_sources(
            capture_root=tmp_path / "captures",
            attempt_id=attempt_id,
            now=CAPTURE_TIME,
            szse_package=_make_szse_package(tmp_path)
            if not (tmp_path / "manual-szse").exists()
            else tmp_path / "manual-szse",
            authorization_path=auth,
            authorization_hash_path=checksum,
            backend=backend,
            token="test-secret",
            clock=lambda: CAPTURE_TIME,
        )
    assert backend.calls == [CALL_ID_DAILY_BASIC]
    incomplete = tmp_path / "captures" / f"capture-{attempt_id}-incomplete"
    try:
        assert (incomplete / "receipts" / "tushare_daily_basic_20260715.json").is_file()
        manifest = json.loads((incomplete / "incomplete-capture-manifest.json").read_bytes())
        assert manifest["failure_class"] == exception_type.__name__
        assert [item["call_id"] for item in manifest["calls"]] == [CALL_ID_DAILY_BASIC]
        assert CALL_ID_DAILY_BASIC not in manifest["missing_call_ids"]
        assert {path.relative_to(incomplete).as_posix() for path in incomplete.rglob("*") if path.is_file()} == {
            "incomplete-capture-manifest.json",
            manifest["calls"][0]["blob_relative_path"],
            manifest["calls"][0]["receipt_relative_path"],
        }
    finally:
        _make_writable(incomplete)


def test_authorization_is_rechecked_and_receipt_uses_actual_call_clock(tmp_path: Path) -> None:
    attempt_id = "attempt-expires-mid-capture"
    auth, checksum = _make_authorization(
        tmp_path,
        attempt_id,
        not_after="2026-07-16T10:01:30+08:00",
    )
    times = iter(
        [
            datetime.fromisoformat("2026-07-16T10:00:00+08:00"),
            datetime.fromisoformat("2026-07-16T10:01:00+08:00"),
            datetime.fromisoformat("2026-07-16T10:02:00+08:00"),
        ]
    )
    backend = SyntheticBackend()
    with pytest.raises(CaptureBlockedError, match="authorization expired") as raised:
        capture_historical_sources(
            capture_root=tmp_path / "captures",
            attempt_id=attempt_id,
            now=CAPTURE_TIME,
            szse_package=_make_szse_package(tmp_path),
            authorization_path=auth,
            authorization_hash_path=checksum,
            backend=backend,
            token="test-secret",
            clock=lambda: next(times),
        )
    assert backend.calls == [CALL_ID_DAILY_BASIC]
    result = raised.value.result
    assert result is not None
    try:
        receipt = json.loads((result.attempt_dir / "receipts" / "tushare_daily_basic_20260715.json").read_bytes())
        assert receipt["response"]["captured_at"] == "2026-07-16T10:01:00+08:00"
        assert CALL_ID_SSE_SUMMARY in result.missing_call_ids
    finally:
        _make_writable(result.attempt_dir)
