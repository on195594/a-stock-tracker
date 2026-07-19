from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import cast
from urllib.request import Request

import pytest

from a_stock_tracker.qualitative.audit import SUPER_STRATA, SW2021_INDUSTRIES
from scripts.archive_m4 import export_m4_sampling_frame as exporter
from scripts.archive_m4.export_m4_sampling_frame import ExportError, TushareApiClient, export_sampling_frame


def _response(fields: list[str], items: list[list[object]]) -> bytes:
    return json.dumps(
        {"request_id": "synthetic-request", "code": 0, "msg": None, "data": {"fields": fields, "items": items}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _fixture_rows() -> tuple[list[list[object]], dict[str, list[list[object]]], list[list[object]]]:
    code_by_name = {name: code for code, name in SW2021_INDUSTRIES.items()}
    stocks: list[list[object]] = []
    members: dict[str, list[list[object]]] = {code: [] for code in SW2021_INDUSTRIES}
    daily: list[list[object]] = []
    counter = 0
    for industries in SUPER_STRATA.values():
        industry_name = industries[0]
        industry_code = code_by_name[industry_name]
        for position in range(6):
            counter += 1
            suffix = "SH" if counter % 2 else "SZ"
            exchange = "SSE" if suffix == "SH" else "SZSE"
            ts_code = f"{counter:06d}.{suffix}"
            name = f"公司{counter}"
            stocks.append([ts_code, name, exchange, "L", "20200101", None])
            members[industry_code].append(
                [industry_code, industry_name, "x.SI", "二级", "y.SI", "三级", ts_code, name, "20210101", None, "Y"]
            )
            daily.append([ts_code, "20260714", f"{position + 1}.{counter:06d}"])
    return stocks, members, daily


def _transport(request: Request, timeout: float) -> bytes:
    assert timeout > 0
    assert request.data is not None
    payload = json.loads(cast(bytes, request.data))
    assert payload["token"] == "test-secret"
    api_name = payload["api_name"]
    params = payload["params"]
    stocks, members, daily = _fixture_rows()
    if api_name == "stock_basic":
        return _response(["ts_code", "name", "exchange", "list_status", "list_date", "delist_date"], stocks)
    if api_name == "trade_cal":
        return _response(
            ["exchange", "cal_date", "is_open", "pretrade_date"],
            [[params["exchange"], "20260714", "1", "20260713"]],
        )
    if api_name == "index_classify":
        return _response(
            ["index_code", "industry_name", "parent_code", "level", "industry_code", "is_pub", "src"],
            [
                [code, name, "0", "L1", code.removesuffix(".SI"), "1", "SW2021"]
                for code, name in SW2021_INDUSTRIES.items()
            ],
        )
    if api_name == "index_member_all":
        return _response(
            [
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
            ],
            members[str(params["l1_code"])],
        )
    if api_name == "daily_basic":
        return _response(["ts_code", "trade_date", "total_mv"], daily)
    raise AssertionError(f"unexpected API: {api_name}")


def test_legacy_tushare_export_is_disabled_before_transport(tmp_path: Path) -> None:
    called = False

    def forbidden_transport(request: Request, timeout: float) -> bytes:
        nonlocal called
        called = True
        raise AssertionError("legacy exporter must not reach transport")

    with pytest.raises(ExportError, match="legacy M4 exporter is disabled"):
        export_sampling_frame(
            token="test-secret",
            output_root=tmp_path,
            now=datetime.fromisoformat("2026-07-15T18:00:00+08:00"),
            transport=forbidden_transport,
        )
    assert called is False
    assert not any(tmp_path.iterdir())


def test_legacy_tushare_cli_is_disabled_before_token_load(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(exporter, "_load_token", lambda: (_ for _ in ()).throw(AssertionError("token read")))
    assert exporter.main() == 2
    assert "legacy M4 exporter is disabled" in capsys.readouterr().err


def test_tushare_client_rejects_non_authorized_api() -> None:
    client = TushareApiClient("secret", transport=_transport, min_interval_seconds=0)
    with pytest.raises(ExportError, match="not authorized"):
        client.call("daily", {}, ("ts_code",))


def test_tushare_client_redacts_token_from_provider_error() -> None:
    def rejected(request: Request, timeout: float) -> bytes:
        return json.dumps(
            {"request_id": "r", "code": 2002, "msg": "denied", "detail": "token=test-secret", "data": None}
        ).encode()

    client = TushareApiClient("test-secret", transport=rejected, min_interval_seconds=0)
    with pytest.raises(ExportError) as captured:
        client.call("stock_basic", {}, ("ts_code",))
    assert "test-secret" not in str(captured.value)
    assert "[REDACTED]" in str(captured.value)


def test_tushare_client_accepts_complete_pagination_metadata() -> None:
    def complete(request: Request, timeout: float) -> bytes:
        return json.dumps(
            {
                "request_id": "r",
                "code": 0,
                "msg": None,
                "data": {"fields": ["ts_code"], "items": [["000001.SZ"]], "count": 1, "has_more": False},
            }
        ).encode()

    snapshot = TushareApiClient("secret", transport=complete, min_interval_seconds=0).call(
        "stock_basic", {}, ("ts_code",)
    )
    assert len(snapshot.rows) == 1
    assert snapshot.response_count == 1
    assert snapshot.has_more is False


def test_tushare_client_accepts_larger_total_count_when_page_is_complete() -> None:
    def complete(request: Request, timeout: float) -> bytes:
        return json.dumps(
            {
                "request_id": "r",
                "code": 0,
                "msg": None,
                "data": {"fields": ["ts_code"], "items": [["000001.SZ"]], "count": 2, "has_more": False},
            }
        ).encode()

    snapshot = TushareApiClient("secret", transport=complete, min_interval_seconds=0).call(
        "stock_basic", {}, ("ts_code",)
    )
    assert len(snapshot.rows) == 1
    assert snapshot.response_count == 2
    assert snapshot.has_more is False


def test_tushare_client_accepts_zero_unknown_count_when_page_is_complete() -> None:
    def complete(request: Request, timeout: float) -> bytes:
        return json.dumps(
            {
                "request_id": "r",
                "code": 0,
                "msg": None,
                "data": {"fields": ["ts_code"], "items": [["000001.SZ"]], "count": 0, "has_more": False},
            }
        ).encode()

    snapshot = TushareApiClient("secret", transport=complete, min_interval_seconds=0).call(
        "stock_basic", {}, ("ts_code",)
    )
    assert len(snapshot.rows) == 1
    assert snapshot.response_count == 0
    assert snapshot.has_more is False


def test_tushare_client_rejects_truncated_pagination_metadata() -> None:
    def truncated(request: Request, timeout: float) -> bytes:
        return json.dumps(
            {
                "request_id": "r",
                "code": 0,
                "msg": None,
                "data": {"fields": ["ts_code"], "items": [["000001.SZ"]], "count": 1, "has_more": True},
            }
        ).encode()

    client = TushareApiClient("secret", transport=truncated, min_interval_seconds=0)
    with pytest.raises(ExportError, match="truncated"):
        client.call("stock_basic", {}, ("ts_code",))


def test_tushare_client_rejects_nonzero_count_smaller_than_page() -> None:
    def invalid(request: Request, timeout: float) -> bytes:
        return json.dumps(
            {
                "request_id": "r",
                "code": 0,
                "msg": None,
                "data": {
                    "fields": ["ts_code"],
                    "items": [["000001.SZ"], ["000002.SZ"]],
                    "count": 1,
                    "has_more": False,
                },
            }
        ).encode()

    client = TushareApiClient("secret", transport=invalid, min_interval_seconds=0)
    with pytest.raises(ExportError, match="invalid pagination count"):
        client.call("stock_basic", {}, ("ts_code",))
