from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Mapping

import pandas as pd
import pytest
import requests  # type: ignore[import-untyped]

from a_stock_tracker.qualitative.audit import SW2021_INDUSTRIES
from scripts.archive_m4 import export_m4_sampling_frame_akshare as exporter
from scripts.archive_m4.export_m4_sampling_frame_akshare import (
    AUTHORIZED_FUNCTIONS,
    CallResult,
    ExportError,
    HttpCapture,
    _TransportGuard,
    _https_url,
    export_sampling_frame,
)


class SyntheticRunner:
    version = "synthetic-1"

    def __init__(self, *, sse_date: str = "20260715", duplicate_membership: bool = False) -> None:
        self.sse_date = sse_date
        self.duplicate_membership = duplicate_membership
        self.calls: list[tuple[str, Mapping[str, str]]] = []
        self._stocks: dict[str, tuple[str, str]] = {}
        counter = 0
        for industry_code in SW2021_INDUSTRIES:
            for _ in range(140):
                counter += 1
                prefix = "6" if counter % 2 else ("3" if counter % 4 == 0 else "0")
                code = prefix + f"{counter:05d}"
                self._stocks[code] = (f"公司{counter}", industry_code)

    def _capture(self, function_name: str) -> tuple[HttpCapture, ...]:
        host = {
            "index_component_sw": "www.swsresearch.com",
            "stock_zh_a_spot_em": "82.push2.eastmoney.com",
            "stock_sse_summary": "query.sse.com.cn",
            "stock_szse_summary": "www.szse.cn",
        }[function_name]
        raw = json.dumps({"function": function_name}, separators=(",", ":")).encode()
        url = f"https://{host}/synthetic"
        return (HttpCapture(url, {}, url, 200, "application/json", raw),)

    def call(self, function_name: str, arguments: Mapping[str, str]) -> CallResult:
        assert function_name in AUTHORIZED_FUNCTIONS
        self.calls.append((function_name, dict(arguments)))
        if function_name == "stock_sse_summary":
            dataframe = pd.DataFrame(
                [
                    {"项目": "总市值", "股票": "100", "主板": "90", "科创板": "10"},
                    {"项目": "报告时间", "股票": self.sse_date, "主板": "", "科创板": ""},
                ]
            )
        elif function_name == "stock_szse_summary":
            dataframe = pd.DataFrame([{"证券类别": "A股", "数量": 2000, "成交金额": 1, "总市值": 100, "流通市值": 80}])
        elif function_name == "stock_zh_a_spot_em":
            dataframe = pd.DataFrame(
                [
                    {"代码": code, "名称": name, "总市值": 1_000_000 + index}
                    for index, (code, (name, _)) in enumerate(self._stocks.items())
                ]
            )
        else:
            symbol = arguments["symbol"] + ".SI"
            rows = [
                {"序号": index, "证券代码": code, "证券名称": name, "最新权重": 1, "计入日期": "20210101"}
                for index, (code, (name, industry)) in enumerate(self._stocks.items(), start=1)
                if industry == symbol
            ]
            if self.duplicate_membership and symbol == list(SW2021_INDUSTRIES)[1]:
                first_code, (first_name, _) = next(iter(self._stocks.items()))
                rows.append(
                    {"序号": 999, "证券代码": first_code, "证券名称": first_name, "最新权重": 1, "计入日期": "20210101"}
                )
            dataframe = pd.DataFrame(rows)
        return CallResult(function_name, dict(arguments), dataframe, self._capture(function_name))


def test_legacy_akshare_export_is_disabled_before_runner(tmp_path: Path) -> None:
    runner = SyntheticRunner()
    with pytest.raises(ExportError, match="legacy M4 AKShare exporter is disabled"):
        export_sampling_frame(
            output_root=tmp_path,
            now=datetime.fromisoformat("2026-07-15T18:00:00+08:00"),
            runner=runner,
        )
    assert runner.calls == []
    assert not any(tmp_path.iterdir())


def test_legacy_akshare_cli_is_disabled_before_runner_construction(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(exporter, "AkshareRunner", lambda: (_ for _ in ()).throw(AssertionError("runner created")))
    assert exporter.main() == 2
    assert "legacy M4 AKShare exporter is disabled" in capsys.readouterr().err


def test_export_uses_direct_szse_source_without_calling_akshare_szse(tmp_path: Path) -> None:
    runner = SyntheticRunner()
    source_called = False

    def direct_source(source_date: object) -> CallResult:
        nonlocal source_called
        source_called = True
        raise AssertionError("legacy exporter must not call the direct source")

    with pytest.raises(ExportError, match="legacy M4 AKShare exporter is disabled"):
        export_sampling_frame(
            output_root=tmp_path,
            now=datetime.fromisoformat("2026-07-15T18:00:00+08:00"),
            runner=runner,
            szse_source=direct_source,  # type: ignore[arg-type]
        )
    assert runner.calls == []
    assert source_called is False


def test_source_date_drift_fails_without_publishing_package(tmp_path: Path) -> None:
    runner = SyntheticRunner(sse_date="20260714")
    with pytest.raises(ExportError, match="legacy M4 AKShare exporter is disabled"):
        export_sampling_frame(
            output_root=tmp_path,
            now=datetime.fromisoformat("2026-07-15T18:00:00+08:00"),
            runner=runner,
        )
    assert runner.calls == []
    assert not any(path.name.startswith("akshare-frame-") for path in tmp_path.iterdir())


def test_duplicate_industry_membership_fails_closed(tmp_path: Path) -> None:
    runner = SyntheticRunner(duplicate_membership=True)
    with pytest.raises(ExportError, match="legacy M4 AKShare exporter is disabled"):
        export_sampling_frame(
            output_root=tmp_path,
            now=datetime.fromisoformat("2026-07-15T18:00:00+08:00"),
            runner=runner,
        )
    assert runner.calls == []
    assert not any(tmp_path.iterdir())


def test_https_policy_rewrites_default_http_and_rejects_escape() -> None:
    assert _https_url("http://query.sse.com.cn/commonQuery.do#fragment", frozenset({"query.sse.com.cn"})) == (
        "https://query.sse.com.cn/commonQuery.do"
    )
    with pytest.raises(ExportError, match="escaped"):
        _https_url("https://evil.example/", frozenset({"query.sse.com.cn"}))


def test_export_rejects_pre_close_capture(tmp_path: Path) -> None:
    runner = SyntheticRunner()
    with pytest.raises(ExportError, match="legacy M4 AKShare exporter is disabled"):
        export_sampling_frame(
            output_root=tmp_path,
            now=datetime.fromisoformat("2026-07-15T15:59:59+08:00"),
            runner=runner,
        )
    assert runner.calls == []


def test_transport_guard_streams_with_tls_and_restores_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    class Response:
        status_code = 200
        url = "https://query.sse.com.cn/commonQuery.do"
        headers = {"Content-Type": "application/json; charset=utf-8"}
        _content = b""
        _content_consumed = False

        def iter_content(self, chunk_size: int) -> list[bytes]:
            assert chunk_size == 64 * 1024
            return [b'{"ok":', b"true}"]

        def close(self) -> None:
            observed["closed"] = True

    def fake_get(url: str, **kwargs: object) -> Response:
        observed["url"] = url
        observed["kwargs"] = kwargs
        return Response()

    monkeypatch.setattr(requests, "get", fake_get)
    with _TransportGuard("stock_sse_summary") as guard:
        response = requests.get("http://query.sse.com.cn/commonQuery.do", params={"sqlId": "x"})
        assert response._content == b'{"ok":true}'
    assert requests.get is fake_get
    assert observed["url"] == "https://query.sse.com.cn/commonQuery.do"
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["verify"] is True
    assert kwargs["allow_redirects"] is False
    assert kwargs["stream"] is True
    assert guard.captures[0].raw == b'{"ok":true}'


def test_transport_guard_stops_at_response_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = False

    class Response:
        status_code = 200
        url = "https://query.sse.com.cn/commonQuery.do"
        headers: dict[str, str] = {}

        def iter_content(self, chunk_size: int) -> list[bytes]:
            return [b"123", b"456"]

        def close(self) -> None:
            nonlocal closed
            closed = True

    def fake_get(url: str, **kwargs: object) -> Response:
        return Response()

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(exporter, "MAX_RESPONSE_BYTES", 4)
    with pytest.raises(ExportError, match="exceeds 32 MiB"):
        with _TransportGuard("stock_sse_summary"):
            requests.get("https://query.sse.com.cn/commonQuery.do")
    assert closed
    assert requests.get is fake_get
