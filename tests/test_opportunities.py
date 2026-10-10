"""Synthetic-only rule, evidence, publication and page regressions."""

import asyncio
from copy import deepcopy
from datetime import date, timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import flet as ft
import pytest

from a_stock_tracker import app
from a_stock_tracker import opportunities as swing
from a_stock_tracker.auth import AuthError, create_demo_actor
from a_stock_tracker.automation import Budget, digest, save_state
from a_stock_tracker.opportunity_demo import demo_board, demo_packet
from a_stock_tracker.opportunity_view import opportunity_view
from a_stock_tracker.research import ScreenError
from tests.test_app import AppMockPage


def test_conditions_are_computed_and_not_claimed_triggered():
    packet = demo_packet()
    row = swing.evaluate(packet)
    assert row["status"] == "pending_review"
    assert row["levels"] == {"trigger": 11.23, "ceiling": 11.45, "stop": 11.0, "volume": 15000.0}
    assert row["as_of"] == packet["sessions"][-1]
    assert row["expires"] == packet["future_sessions"][-1]
    assert "不代表可以买入" in row["facts"]["condition"]
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
    "damage", [None, "calendar_gap", "calendar_duplicate", "identity", "price_identity"]
)
def test_collect_uses_explicit_pool_and_proven_calendar(damage):
    import pandas as pd

    packet = demo_packet()
    as_of = date.fromisoformat(packet["sessions"][-1])
    calls = []

    class Client:
        def trade_cal(self, **kwargs):
            calls.append("trade_cal")
            start = as_of - timedelta(days=240)
            rows = [
                {
                    "exchange": "SSE",
                    "cal_date": (start + timedelta(days=i)).strftime("%Y%m%d"),
                    "is_open": int((start + timedelta(days=i)).weekday() < 5),
                }
                for i in range(271)
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
    elif damage:
        with pytest.raises(ScreenError):
            swing.collect(Client(), "mock", [packet["code"]], as_of)
    else:
        result = swing.collect(Client(), "mock", [packet["code"]], as_of)
        assert swing.evaluate(result[0])["status"] == "pending_review"
        assert calls == ["trade_cal", "index_daily", "stock_basic", "daily"]


def test_flat_or_weak_stock_is_excluded():
    packet = demo_packet()
    packet["bars"] = deepcopy(packet["benchmark"])
    assert "近二十个交易日未跑赢沪深三百" in swing.evaluate(packet)["reasons"]


def test_model_cannot_invent_price_or_reference():
    row = demo_board()["rows"][0]
    swing.validate_explanation(row["explanation"], row["facts"])
    for field, value in (
        ("text", "建议在12元买入，这个价格很值得考虑，可以带来确定性的上涨机会"),
        ("refs", ["invented"]),
    ):
        report = deepcopy(row["explanation"])
        report["reason"][field] = value
        with pytest.raises(ScreenError):
            swing.validate_explanation(report, row["facts"])


@pytest.mark.parametrize(
    ("approved", "collect_failed"), [(True, False), (False, False), ("true", False), (True, True)]
)
def test_publish_requires_second_model_review(tmp_path, monkeypatch, approved, collect_failed):
    calls = []

    def model_call(directory, label, prompt, model, state, budget):
        calls.append(label)
        state["model_calls"] += 1
        return (
            demo_board()["rows"][0]["explanation"]
            if label.endswith("explain")
            else {"supported": approved, "reason": "checked"}
        )

    monkeypatch.setattr(swing, "model_call", model_call)
    packets = [demo_packet()]
    if collect_failed:
        packets.insert(0, {"code": "600002.SH", "name": "", "collect_error": "ScreenError"})
    result = swing.publish(
        tmp_path, packets, {"provider": "mock", "model": "mock"}, Budget(20, 600)
    )
    assert len(calls) == 2
    if collect_failed:
        assert result["rows"][0]["status"] == "gap"
        assert "levels" not in result["rows"][0]
    row = result["rows"][-1]
    assert row["status"] == ("observation" if approved is True else "gap")
    assert ("levels" in row) is (approved is True)
    assert (tmp_path / "result.json").is_file()


def saved_board(tmp_path):
    run = "swing-" + "a" * 32
    (tmp_path / run).mkdir()
    board = demo_board()
    board["rule"] = swing.RULE
    board["rows"][0]["review"] = {"supported": True, "reason": "synthetic"}
    save_state(tmp_path / run / "result.json", board)
    save_state(tmp_path / "latest.json", {"run": run, "digest": digest(board)})
    return board


def test_board_integrity_expiry_and_actor(tmp_path):
    board = saved_board(tmp_path)
    actor = create_demo_actor()
    expiry = date.fromisoformat(board["rows"][0]["expires"])
    loaded = swing.load_board(actor, tmp_path, on=expiry)
    assert loaded and not loaded["rows"][0]["expired"]
    loaded = swing.load_board(actor, tmp_path, on=expiry + timedelta(days=1))
    assert loaded and loaded["rows"][0]["expired"]
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


def test_expired_detail_hides_actionable_levels():
    board = demo_board()
    board["rows"][0]["expired"] = True
    view = opportunity_view(board, "600001.SH", lambda _: None, None, None)
    visible = " ".join(c.value for c in view.controls if isinstance(c, ft.Text))
    assert "观察已过期" in visible

    def texts(control):
        yield getattr(control, "value", "") or ""
        for child in getattr(control, "controls", None) or []:
            yield from texts(child)

    visible = " ".join(texts(view))
    assert "11.23" not in visible and "11.00" not in visible


def test_default_page_is_opportunities_and_stale_click_is_inert(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "demo")

    async def run():
        page = AppMockPage()
        page.route = "/"
        await app.build_app()(page)
        content = page.controls[0].controls[0].controls[0].content.controls[1]
        button = next(
            c
            for c in content.content.controls
            if isinstance(c, ft.Button) and str(c.content).startswith("查看条件")
        )
        assert not page.navigation_bar.visible
        await button.on_click(None)
        assert page.route == "/opportunity/600001.SH"
        assert any(
            isinstance(c, ft.Text) and c.value == "什么时候放弃" for c in content.content.controls
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

    monkeypatch.setattr(app, "load_board", delayed_board)

    async def run():
        page = AppMockPage(auth=MagicMock(user={"id": "12345678"}))
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
        content = page.controls[0].controls[0].controls[0].content.controls[1]
        assert not any(
            isinstance(c, ft.Text) and "演示公司" in c.value for c in content.content.controls
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
        content = page.controls[0].controls[0].controls[0].content.controls[1]
        button = next(c for c in content.content.controls if isinstance(c, ft.Button))
        await page.on_disconnect(None)
        await button.on_click(None)
        assert page.route == "/"
        await page.on_close(None)

    asyncio.run(run())
