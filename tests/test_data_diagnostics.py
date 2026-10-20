"""Data failures retain categories without provider-controlled content."""

import sys
from types import SimpleNamespace

import pandas as pd
import pytest
import tushare

from a_stock_tracker import automation as a
from a_stock_tracker.research import DataError, call_api, fetch_universe, frame_records


@pytest.mark.parametrize(
    "error,category", [(TimeoutError, "timeout"), (RuntimeError, "request_failed")]
)
def test_provider_secrets_never_reach_exception_or_saved_diagnostic(error, category):
    def fail(**kwargs):
        raise error("Authorization: other-secret; https://private.example/?token=synthetic")

    with pytest.raises(DataError) as caught:
        call_api(SimpleNamespace(stock_basic=fail), "stock_basic", "synthetic")
    assert caught.value.diagnostic == {
        "stage": "data_preparation",
        "endpoint": "stock_basic",
        "category": category,
    }
    assert "secret" not in str(caught.value) and "http" not in str(caught.value)


def test_missing_fields_empty_and_duplicate_are_distinct():
    with pytest.raises(DataError) as caught:
        frame_records(pd.DataFrame(), {"ts_code"}, "stock_basic")
    assert caught.value.diagnostic["category"] == "missing_fields"
    columns = ["ts_code", "name", "industry", "market", "exchange", "list_status"]
    for rows, category in [([], "empty_data"), ([["same"] * 6] * 2, "duplicate_security")]:
        client = SimpleNamespace(stock_basic=lambda **kw: pd.DataFrame(rows, columns=columns))
        with pytest.raises(DataError) as caught:
            fetch_universe(client, "synthetic", "2026-01-01")
        assert caught.value.diagnostic["category"] == category


def test_failure_before_freeze_saves_endpoint_and_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic")
    monkeypatch.setattr(tushare, "pro_api", lambda *args, **kw: None)
    root = tmp_path / "batch"
    monkeypatch.setattr(sys, "argv", ["automation", "--root", str(root)])

    def fail(client, *args, **kwargs):
        client.budget.used = 2
        raise DataError("stock_basic", "timeout", "not serialized")

    monkeypatch.setattr(a, "freeze", fail)
    with pytest.raises(DataError):
        a.main()
    saved = a.read_result(root / "invocation.json")
    assert saved["diagnostic"] == {
        "stage": "data_preparation",
        "endpoint": "stock_basic",
        "category": "timeout",
        "attempt": 2,
    }
    assert "not serialized" not in (root / "invocation.json").read_text()


def test_receiver_exposes_only_fixed_data_category(tmp_path, monkeypatch):
    import json

    from a_stock_tracker.research_service import ResearchStore
    from tests.test_research_service import payload

    store = ResearchStore(tmp_path / "service")
    request = payload("screen")
    directory = store.directory(request["id"])
    directory.mkdir(mode=0o700)
    batch = directory / "batch"
    batch.mkdir(mode=0o700)
    a.save(
        batch / "invocation.json",
        {
            "requests": 2,
            "diagnostic": {
                "stage": "data_preparation",
                "category": "timeout",
                "endpoint": "private-secret",
                "message": "provider-secret",
                "attempt": 2,
            },
        },
    )
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda *args, **kwargs: SimpleNamespace(pid=123456, wait=lambda **kwargs: 1),
    )
    monkeypatch.setattr("os.killpg", lambda *_: None)
    state = {"id": request["id"], "request": request, "status": "running", "limits": {"seconds": 1}}
    store.execute(directory, state, store.lease())
    saved = a.read_state(directory / "request.json")
    assert saved["status"] == "failed" and saved["diagnostic"]["category"] == "timeout"
    assert "数据准备超时" in saved["message"]
    assert "secret" not in json.dumps(saved)
