from __future__ import annotations

import io
from datetime import date
from typing import Any

import pandas as pd
import pytest

from scripts.export_m4_sampling_frame_akshare import ExportError
from scripts import export_m4_sampling_frame_akshare_szse_https as exporter
from scripts.export_m4_sampling_frame_akshare_szse_https import SZSE_ENDPOINT, SzseHttpsSource


def _xlsx() -> bytes:
    output = io.BytesIO()
    pd.DataFrame(
        [
            {"证券类别": "A股", "数量": "2,800", "成交金额": "1", "总市值": "30,000", "流通市值": "20,000"},
            {"证券类别": "基金", "数量": "1,000", "成交金额": "2", "总市值": "3,000", "流通市值": "3,000"},
        ]
    ).to_excel(output, index=False, engine="openpyxl")
    return output.getvalue()


class FakeResponse:
    def __init__(self, *, url: str = SZSE_ENDPOINT, raw: bytes | None = None) -> None:
        self.status_code = 200
        self.url = url
        self.headers = {"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        self.raw = raw or _xlsx()
        self.closed = False

    def iter_content(self, chunk_size: int) -> list[bytes]:
        assert chunk_size == 64 * 1024
        return [self.raw[:100], self.raw[100:]]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.observed: dict[str, Any] = {}
        self.closed = False

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.observed = {"url": url, **kwargs}
        return self.response

    def close(self) -> None:
        self.closed = True


def test_direct_szse_source_captures_date_bound_https_xlsx() -> None:
    response = FakeResponse()
    session = FakeSession(response)
    with pytest.raises(ExportError, match="legacy M4 SZSE exporter is disabled"):
        SzseHttpsSource(session_factory=lambda: session)(date(2026, 7, 15))
    assert session.observed == {}
    assert not response.closed and not session.closed


def test_direct_szse_source_rejects_cross_domain_response() -> None:
    response = FakeResponse(url="https://evil.example/report.xlsx")
    session = FakeSession(response)
    with pytest.raises(ExportError, match="legacy M4 SZSE exporter is disabled"):
        SzseHttpsSource(session_factory=lambda: session)(date(2026, 7, 15))
    assert session.observed == {}


def test_direct_szse_source_rejects_non_xlsx_bytes() -> None:
    response = FakeResponse(raw=b"<html>error</html>")
    session = FakeSession(response)
    with pytest.raises(ExportError, match="legacy M4 SZSE exporter is disabled"):
        SzseHttpsSource(session_factory=lambda: session)(date(2026, 7, 15))
    assert session.observed == {}


def test_legacy_szse_cli_is_disabled_before_session_construction(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        exporter,
        "SzseHttpsSource",
        lambda: (_ for _ in ()).throw(AssertionError("session source created")),
    )
    assert exporter.main() == 2
    assert "legacy M4 SZSE exporter is disabled" in capsys.readouterr().err
