"""Synthetic-only rule, evidence, publication and page regressions."""

import asyncio
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import flet as ft
import pytest

from a_stock_tracker import app
from a_stock_tracker import opportunities as swing
from a_stock_tracker.auth import AuthError, create_demo_actor
from a_stock_tracker.automation import Budget, digest, save_state
from a_stock_tracker.opportunity_demo import demo_board, demo_packet
from a_stock_tracker.research import ScreenError
from tests.test_app import AppMockPage, app_controls, completed_owner_authorization


def test_conditions_are_computed_and_not_claimed_triggered():
    packet = demo_packet()
    row = swing.evaluate(packet)
    assert row["status"] == "reference"
    assert row["levels"]["trigger"] == 11.23
    assert row["levels"]["stop"] == 11.0
    assert row["levels"]["distance"] == pytest.approx((11.2 - 11.0) / 11.2 * 100)
    assert "ceiling" not in row["levels"] and "volume" not in row["levels"]
    assert row["as_of"] == packet["sessions"][-1]
    assert row["expires"] == packet["future_sessions"][-1]
    assert "观察线" in row["facts"]["condition"]
    assert "不是最大损失承诺" in row["facts"]["distance"]


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "duplicate",
        "nan",
        "bool",
        "ohlc",
        "corporate_action",
        "future_order",
        "st",
        "outside",
        "zero_volume",
        "s_star_st",
        "delisting",
    ],
)
def test_unsafe_evidence_cannot_produce_levels(damage):
    packet = demo_packet()
    if damage == "missing":
        packet["bars"].pop()
    elif damage == "duplicate":
        packet["bars"][-1]["date"] = packet["bars"][-2]["date"]
    elif damage == "nan":
        packet["bars"][-1]["close"] = float("nan")
    elif damage == "bool":
        packet["bars"][-1]["close"] = True
    elif damage == "ohlc":
        packet["bars"][-1]["high"] = 1
    elif damage == "corporate_action":
        packet["bars"][-1]["pre_close"] = 5
    elif damage == "future_order":
        packet["future_sessions"][0] = packet["sessions"][-1]
    elif damage in {"st", "s_star_st", "delisting"}:
        packet["name"] = {"st": "*ST示例", "s_star_st": "S*ST示例", "delisting": "示例退"}[damage]
    elif damage == "outside":
        packet["code"] = "300001.SZ"
    elif damage == "zero_volume":
        packet["bars"][-1]["vol"] = 0
    with pytest.raises(ScreenError):
        swing.evaluate(packet)


@pytest.mark.parametrize(
    "damage",
    [None, "calendar_gap", "calendar_duplicate", "identity", "price_identity", "latest_weekend"],
)
def test_collect_uses_explicit_pool_and_proven_calendar(damage, monkeypatch):
    import pandas as pd

    if damage == "latest_weekend":
        monkeypatch.setattr("a_stock_tracker.opportunity_demo.today", lambda: date(2026, 10, 10))
    packet = demo_packet()
    as_of = date.fromisoformat(packet["sessions"][-1])
    calls = []

    class Client:
        def trade_cal(self, **kwargs):
            calls.append("trade_cal")
            start = date.fromisoformat(
                kwargs["start_date"][:4]
                + "-"
                + kwargs["start_date"][4:6]
                + "-"
                + kwargs["start_date"][6:]
            )
            end = date.fromisoformat(
                kwargs["end_date"][:4]
                + "-"
                + kwargs["end_date"][4:6]
                + "-"
                + kwargs["end_date"][6:]
            )
            rows = [
                {
                    "exchange": "SSE",
                    "cal_date": (start + timedelta(days=i)).strftime("%Y%m%d"),
                    "is_open": int((start + timedelta(days=i)).weekday() < 5),
                }
                for i in range((end - start).days + 1)
            ]
            if damage == "calendar_gap":
                rows.pop()
            if damage == "calendar_duplicate":
                rows[-1] = rows[-2]
            return pd.DataFrame(rows)

        def stock_basic(self, **kwargs):
            calls.append("stock_basic")
            assert kwargs["ts_code"] == packet["code"]
            return pd.DataFrame(
                [
                    {
                        "ts_code": packet["code"],
                        "name": packet["name"],
                        "market": "创业板" if damage == "identity" else "主板",
                        "exchange": "SSE",
                        "list_status": "L",
                    }
                ]
            )

        def prices(self, code, rows):
            return pd.DataFrame(
                [
                    {
                        **row,
                        "ts_code": "wrong" if damage == "price_identity" else code,
                        "trade_date": row["date"].replace("-", ""),
                    }
                    for row in rows
                ]
            )

        def daily(self, **kwargs):
            calls.append("daily")
            assert kwargs["ts_code"] == packet["code"]
            return self.prices(packet["code"], packet["bars"])

        def index_daily(self, **kwargs):
            calls.append("index_daily")
            assert kwargs["ts_code"] == "000300.SH"
            return self.prices("000300.SH", packet["benchmark"])

    if damage == "identity":
        result = swing.collect(Client(), "mock", [packet["code"]], as_of)
        assert result == [{"code": packet["code"], "name": "", "collect_error": "ScreenError"}]
    elif damage == "latest_weekend":
        weekend = as_of + timedelta(days=2)
        result = swing.collect(Client(), "mock", [packet["code"]], weekend, latest=True)
        assert result[0]["sessions"][-1] == as_of.isoformat()
        assert swing.evaluate(result[0])["status"] == "reference"
    elif damage:
        with pytest.raises(ScreenError):
            swing.collect(Client(), "mock", [packet["code"]], as_of)
    else:
        result = swing.collect(Client(), "mock", [packet["code"]], as_of)
        assert swing.evaluate(result[0])["status"] == "reference"
        assert calls == ["trade_cal", "index_daily", "stock_basic", "daily"]


@pytest.mark.parametrize("kind", ["weak", "wide", "downtrend"])
def test_complete_prices_are_references_regardless_of_old_exclusions(kind):
    packet = demo_packet()
    if kind == "weak":
        packet["bars"] = deepcopy(packet["benchmark"])
    elif kind == "wide":
        packet["bars"][-5]["low"] = 8.0
    else:
        for i, row in enumerate(packet["bars"]):
            row.update(
                open=20 - i * 0.01,
                high=21 - i * 0.01,
                low=19 - i * 0.01,
                close=20 - i * 0.01,
                pre_close=20 - (i - 1) * 0.01,
            )
    row = swing.evaluate(packet)
    assert row["status"] == "reference"
    if kind == "wide":
        assert row["levels"]["distance"] > 8
    if kind == "downtrend":
        assert row["levels"]["uptrend"] is False


def test_publish_has_no_model_path_and_preserves_company_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "a_stock_tracker.disclosures.model_call",
        lambda *a: pytest.fail("price reference called model"),
    )
    packets = [{"code": "600002.SH", "name": "", "collect_error": "ScreenError"}, demo_packet()]
    result = swing.publish(tmp_path, packets, Budget(20, 600))
    assert result["rule"] == swing.REFERENCE_RULE
    assert result["model_calls"] == 0 and "model" not in result
    assert result["rows"][0]["status"] == "gap"
    assert "levels" not in result["rows"][0]
    assert result["rows"][1]["status"] == "reference"
    assert (tmp_path / "result.json").is_file()


def legacy_board():
    packet = demo_packet()
    c = swing.price_conditions(packet["bars"], packet["benchmark"])
    row = {
        "code": packet["code"],
        "name": packet["name"],
        "status": "observation",
        "as_of": packet["sessions"][-1],
        "expires": packet["future_sessions"][-1],
        "facts": {
            "trend": "合成历史趋势",
            "relative": "合成历史相对强弱",
            "limits": "历史资料范围有限",
            "condition": "历史区间 11.23–11.45 元；量能 15000 手",
            "invalidation": "历史失效价 11.00 元",
            "distance": "旧规则距离 3.93%",
        },
        "levels": {k: c[k] for k in ("trigger", "ceiling", "stop", "volume")},
        "explanation": {
            k: {
                "text": "合成历史说明：只引用当时日线中的资料，不表示现在仍然有效。",
                "refs": ["trend"],
            }
            for k in swing.SECTIONS
        },
        "review": {"supported": True, "reason": "synthetic"},
    }
    return {"rule": swing.RULE, "source": "合成历史资料", "rows": [row]}


def test_legacy_reader_keeps_explanation_validation():
    row = legacy_board()["rows"][0]
    swing.validate_explanation(row["explanation"], row["facts"])
    for field, value in (("text", "无依据价格12元"), ("refs", ["invented"])):
        report = deepcopy(row["explanation"])
        report["reason"][field] = value
        with pytest.raises(ScreenError):
            swing.validate_explanation(report, row["facts"])


def saved_board(tmp_path, legacy=False):
    run = "swing-" + "a" * 32
    (tmp_path / run).mkdir()
    board = legacy_board() if legacy else demo_board()
    save_state(tmp_path / run / "result.json", board)
    save_state(tmp_path / "latest.json", {"run": run, "digest": digest(board)})
    return board


@pytest.mark.parametrize("legacy", [False, True])
def test_board_integrity_expiry_and_actor(tmp_path, legacy):
    board = saved_board(tmp_path, legacy)
    output = tmp_path / ("swing-" + "a" * 32) / "result.json"
    before = output.read_bytes()
    actor = create_demo_actor()
    expiry = date.fromisoformat(board["rows"][0]["expires"])
    loaded = swing.load_board(actor, tmp_path, on=expiry)
    assert loaded and not loaded["rows"][0]["expired"]
    loaded = swing.load_board(actor, tmp_path, on=expiry + timedelta(days=1))
    assert loaded and loaded["rows"][0]["expired"]
    assert output.read_bytes() == before
    actor.revoke()
    with pytest.raises(AuthError):
        swing.load_board(actor, tmp_path)


@pytest.mark.parametrize("damage", ["hash", "status", "symlink", "traversal"])
def test_invalid_board_artifacts_refused(tmp_path, damage):
    board = saved_board(tmp_path)
    run = "swing-" + "a" * 32
    output = tmp_path / run / "result.json"
    root = tmp_path
    if damage == "hash":
        output.write_text(output.read_text().replace("演示公司", "篡改公司"))
    elif damage == "status":
        board["rows"][0]["status"] = "buy"
        save_state(output, board)
        save_state(tmp_path / "latest.json", {"run": run, "digest": digest(board)})
    elif damage == "symlink":
        root = tmp_path / "alias"
        root.symlink_to(tmp_path, target_is_directory=True)
    else:
        save_state(tmp_path / "latest.json", {"run": "../secret", "digest": "x"})
    with pytest.raises(ScreenError):
        swing.load_board(create_demo_actor(), root)


def test_default_page_is_opportunities_and_stale_click_is_inert(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "demo")

    async def run():
        page = AppMockPage()
        page.route = "/"
        await app.build_app()(page)
        button = next(
            c
            for c in app_controls(page)
            if isinstance(c, ft.Button) and getattr(c.content, "value", c.content) == "查看价格参考"
        )
        assert page.navigation_bar is None
        await button.on_click(None)
        assert page.route == "/opportunity/600001.SH"
        assert any(
            isinstance(c, ft.Text) and c.value == "观察线与失效线" for c in app_controls(page)
        )
        page.route = "/settings"  # Browser changes the URL before dispatching the event.
        await page.on_route_change(SimpleNamespace(route="/settings"))
        await button.on_click(None)
        assert page.route == "/settings"
        await page.on_close(None)

    asyncio.run(run())


def test_late_private_board_cannot_overwrite_navigation(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "production")
    monkeypatch.setattr(app, "GITHUB_ALLOWED_USER_ID", "12345678")
    started, release = Event(), Event()

    def delayed_board(actor, root):
        started.set()
        assert release.wait(5)
        return demo_board()

    monkeypatch.setattr(app, "OPPORTUNITY_ROOT", Path("/synthetic"))
    monkeypatch.setattr(app, "load_board", delayed_board)

    async def run():
        page = AppMockPage(auth=await completed_owner_authorization("12345678"))
        await app.build_app()(page)
        pending = asyncio.create_task(
            page.on_login(
                ft.LoginEvent(name="login", control=None, error=None, error_description=None)
            )
        )
        try:
            assert await asyncio.to_thread(started.wait, 3)
            page.route = "/settings"
            await page.on_route_change(SimpleNamespace(route="/settings"))
        finally:
            release.set()
            await pending
        assert not any(
            isinstance(c, ft.Text) and "演示公司" in (c.value or "") for c in app_controls(page)
        )
        assert page.route == "/settings"
        await page.on_close(None)

    asyncio.run(run())


def test_disconnected_opportunity_callback_is_inert(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "demo")

    async def run():
        page = AppMockPage()
        page.route = "/"
        await app.build_app()(page)
        button = next(
            c
            for c in app_controls(page)
            if isinstance(c, ft.Button) and getattr(c.content, "value", c.content) == "查看价格参考"
        )
        await page.on_disconnect(None)
        await button.on_click(None)
        assert page.route == "/"
        await page.on_close(None)

    asyncio.run(run())
