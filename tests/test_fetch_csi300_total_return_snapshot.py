from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from scripts import fetch_csi300_total_return_snapshot as snapshot


class FakeApi:
    def __init__(self, total_closes: list[float], price_closes: list[float]) -> None:
        self.frames = {
            snapshot.TOTAL_RETURN_SYMBOL: _frame(total_closes),
            snapshot.PRICE_SYMBOL: _frame(price_closes),
        }
        self.calls: list[dict[str, str]] = []

    def index_daily(self, **kwargs: str) -> pd.DataFrame:
        self.calls.append(kwargs)
        return self.frames[kwargs["ts_code"]]


def _frame(closes: list[float]) -> pd.DataFrame:
    dates = ["20260701", "20260702", "20260703"]
    return pd.DataFrame({"trade_date": dates[: len(closes)], "close": closes})


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    total_closes: list[float],
    price_closes: list[float],
) -> tuple[int, Path, FakeApi]:
    output = tmp_path / "snapshot.json"
    api = FakeApi(total_closes, price_closes)
    monkeypatch.setattr(snapshot, "_create_api", lambda: api)
    exit_code = snapshot.main(
        [
            "--start-date",
            "2026-07-01",
            "--end-date",
            "2026-07-03",
            "--output",
            str(output),
        ]
    )
    return exit_code, output, api


def test_writes_contract_compliant_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exit_code, output, api = _run(monkeypatch, tmp_path, [200.0, 202.0, 206.0], [100.0, 101.0, 102.0])

    assert exit_code == 0
    payload: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["source"] == "tushare.index_daily"
    assert payload["symbol"] == "H00300.CSI"
    assert datetime.fromisoformat(payload["fetched_at"])
    assert payload["rows"] == [
        {"trade_date": "2026-07-01", "close": 200.0},
        {"trade_date": "2026-07-02", "close": 202.0},
        {"trade_date": "2026-07-03", "close": 206.0},
    ]
    assert api.calls == [
        {"ts_code": "H00300.CSI", "start_date": "20260701", "end_date": "20260703"},
        {"ts_code": "000300.SH", "start_date": "20260701", "end_date": "20260703"},
    ]


def test_ratio_drop_beyond_tolerance_fails_without_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exit_code, output, _ = _run(monkeypatch, tmp_path, [200.0, 199.0, 202.0], [100.0, 100.0, 100.0])

    assert exit_code != 0
    assert not output.exists()


def test_ratio_rounding_noise_does_not_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    noise_drop = 0.0002 / 10_000
    exit_code, output, _ = _run(
        monkeypatch,
        tmp_path,
        [200.0, 200.0 * (1.0 - noise_drop), 200.1],
        [100.0, 100.0, 100.0],
    )

    assert exit_code == 0
    assert output.exists()


def test_interval_return_gate_fails_without_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exit_code, output, _ = _run(
        monkeypatch,
        tmp_path,
        [200.0, 200.0005, 199.9995],
        [100.0, 100.0, 100.0],
    )

    assert exit_code != 0
    assert not output.exists()


def test_fetch_failure_returns_nonzero_without_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    output = tmp_path / "snapshot.json"

    class FailingApi:
        def index_daily(self, **kwargs: str) -> pd.DataFrame:
            raise RuntimeError("offline fake failure")

    monkeypatch.setattr(snapshot, "_create_api", lambda: FailingApi())
    exit_code = snapshot.main(
        [
            "--start-date",
            "2026-07-01",
            "--end-date",
            "2026-07-03",
            "--output",
            str(output),
        ]
    )

    assert exit_code != 0
    assert not output.exists()
