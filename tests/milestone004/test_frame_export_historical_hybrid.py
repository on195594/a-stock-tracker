from __future__ import annotations

import hashlib
import io
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, cast
from urllib.request import Request

import pandas as pd
import pytest

from a_stock_tracker.qualitative.audit import SW2021_INDUSTRIES, canonical_json_bytes
from scripts.export_m4_sampling_frame import Transport
from scripts.export_m4_sampling_frame_akshare import CallResult, ExportError, HttpCapture
from scripts.export_m4_sampling_frame_historical_hybrid import export_historical_sampling_frame


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class SyntheticRunner:
    version = "synthetic-1"

    def __init__(self, *, sse_date: str = "20260715", post_date_member: bool = False) -> None:
        self.sse_date = sse_date
        self.post_date_member = post_date_member
        self.calls: list[tuple[str, Mapping[str, str]]] = []
        self.stocks: dict[str, tuple[str, str]] = {}
        counter = 0
        for industry_code in SW2021_INDUSTRIES:
            for _ in range(140):
                counter += 1
                prefix = "6" if counter % 2 else ("3" if counter % 4 == 0 else "0")
                self.stocks[prefix + f"{counter:05d}"] = (f"公司{counter}", industry_code)

    def call(self, function_name: str, arguments: Mapping[str, str]) -> CallResult:
        assert function_name in {"stock_sse_summary", "index_component_sw"}
        self.calls.append((function_name, dict(arguments)))
        if function_name == "stock_sse_summary":
            dataframe = pd.DataFrame(
                [
                    {"项目": "总市值", "股票": "100", "主板": "90", "科创板": "10"},
                    {"项目": "报告时间", "股票": self.sse_date, "主板": "", "科创板": ""},
                ]
            )
            host = "query.sse.com.cn"
        else:
            symbol = arguments["symbol"] + ".SI"
            rows = [
                {
                    "序号": index,
                    "证券代码": code,
                    "证券名称": name,
                    "最新权重": 1,
                    "计入日期": "20260716" if self.post_date_member and index == 1 else "20210101",
                }
                for index, (code, (name, industry)) in enumerate(self.stocks.items(), start=1)
                if industry == symbol
            ]
            dataframe = pd.DataFrame(rows)
            host = "www.swsresearch.com"
        raw = json.dumps({"function": function_name}, separators=(",", ":")).encode()
        url = f"https://{host}/synthetic"
        capture = HttpCapture(url, {}, url, 200, "application/json", raw)
        return CallResult(function_name, dict(arguments), dataframe, (capture,))


def _daily_transport(stocks: Mapping[str, tuple[str, str]], *, trade_date: str = "20260715") -> Transport:
    def transport(request: Request, timeout: float) -> bytes:
        assert timeout > 0
        assert request.data is not None
        payload: dict[str, Any] = json.loads(cast(bytes, request.data))
        assert payload["api_name"] == "daily_basic"
        assert payload["params"] == {"trade_date": "20260715"}
        assert payload["token"] == "test-secret"
        fields = ["ts_code", "trade_date", "total_mv"]
        items = [
            [f"{code}.{'SH' if code.startswith('6') else 'SZ'}", trade_date, f"{index}.123456"]
            for index, code in enumerate(stocks, start=1)
        ]
        return json.dumps(
            {
                "request_id": "synthetic",
                "code": 0,
                "msg": None,
                "data": {"fields": fields, "items": items, "count": 0, "has_more": False},
            },
            separators=(",", ":"),
        ).encode()

    return transport


def _make_szse_package(root: Path, *, source_date: str = "2026-07-15") -> Path:
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
    file_record = {
        "acquired_at": "2026-07-16T09:00:00+08:00",
        "byte_count": len(xlsx),
        "filename": "ShowReport.xlsx",
        "format": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "row_count": 3,
        "sha256": _sha256(xlsx),
    }
    provenance = {
        "acquisition": {
            "attempts": 1,
            "final_origin": "https://www.szse.cn",
            "redirect_policy": "reject_cross_domain",
            "tool": "synthetic browser",
            "tool_image": "synthetic@sha256:" + "0" * 64,
            "transport": "HTTPS with browser certificate validation",
        },
        "files": [file_record],
        "publisher": "深圳证券交易所",
        "request": {
            "method": "browser navigation/download",
            "parameters": {
                "CATALOGID": "1803_sczm",
                "SHOWTYPE": "xlsx",
                "TABKEY": "tab1",
                "txtQueryDate": source_date,
            },
            "url": "https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1803_sczm"
            f"&TABKEY=tab1&txtQueryDate={source_date}",
        },
        "schema_version": "m4-manual-szse-capture-v1",
        "source_date": source_date,
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
    sums = f"{_sha256(xlsx)}  ShowReport.xlsx\n{_sha256(provenance_raw)}  provenance.json\n"
    (package / "SHA256SUMS").write_text(sums, encoding="utf-8")
    return package


def test_historical_hybrid_export_is_complete_hashed_and_read_only(tmp_path: Path) -> None:
    runner = SyntheticRunner()
    szse_package = _make_szse_package(tmp_path)
    transport_called = False

    def forbidden_transport(request: Request, timeout: float) -> bytes:
        nonlocal transport_called
        transport_called = True
        raise AssertionError("legacy exporter must not reach transport")

    with pytest.raises(ExportError, match="legacy historical M4 exporter is disabled"):
        export_historical_sampling_frame(
            token="test-secret",
            output_root=tmp_path / "out",
            now=datetime.fromisoformat("2026-07-16T10:00:00+08:00"),
            sampling_date=date(2026, 7, 15),
            szse_package=szse_package,
            runner=runner,
            tushare_transport=forbidden_transport,
        )
    assert runner.calls == []
    assert transport_called is False
    assert not (tmp_path / "out").exists()


def test_historical_hybrid_rejects_sw_entry_after_sampling_date(tmp_path: Path) -> None:
    runner = SyntheticRunner(post_date_member=True)
    with pytest.raises(ExportError, match="legacy historical M4 exporter is disabled"):
        export_historical_sampling_frame(
            token="test-secret",
            output_root=tmp_path / "out",
            now=datetime.fromisoformat("2026-07-16T10:00:00+08:00"),
            sampling_date=date(2026, 7, 15),
            szse_package=_make_szse_package(tmp_path),
            runner=runner,
            tushare_transport=_daily_transport(runner.stocks),
            min_interval_seconds=0,
        )
    assert not list((tmp_path / "out").glob("historical-hybrid-frame-*"))
    assert runner.calls == []


def test_historical_hybrid_rejects_daily_date_drift(tmp_path: Path) -> None:
    runner = SyntheticRunner()
    with pytest.raises(ExportError, match="legacy historical M4 exporter is disabled"):
        export_historical_sampling_frame(
            token="test-secret",
            output_root=tmp_path / "out",
            now=datetime.fromisoformat("2026-07-16T10:00:00+08:00"),
            sampling_date=date(2026, 7, 15),
            szse_package=_make_szse_package(tmp_path),
            runner=runner,
            tushare_transport=_daily_transport(runner.stocks, trade_date="20260714"),
            min_interval_seconds=0,
        )
    assert runner.calls == []


def test_historical_hybrid_rejects_tampered_static_szse_package(tmp_path: Path) -> None:
    runner = SyntheticRunner()
    package = _make_szse_package(tmp_path)
    (package / "ShowReport.xlsx").write_bytes(b"tampered")
    with pytest.raises(ExportError, match="legacy historical M4 exporter is disabled"):
        export_historical_sampling_frame(
            token="test-secret",
            output_root=tmp_path / "out",
            now=datetime.fromisoformat("2026-07-16T10:00:00+08:00"),
            sampling_date=date(2026, 7, 15),
            szse_package=package,
            runner=runner,
            tushare_transport=_daily_transport(runner.stocks),
            min_interval_seconds=0,
        )
    assert runner.calls == []
