"""Synthetic-only rule, evidence, publication and page regressions."""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from a_stock_tracker import opportunities as swing
from a_stock_tracker.automation import Budget, digest, save_state
from a_stock_tracker.mcp_server import EXPIRED_FACTS
from a_stock_tracker.opportunity_demo import demo_board, demo_packet
from a_stock_tracker.research import ScreenError


def test_conditions_are_computed_and_not_claimed_triggered():
    packet = demo_packet()
    row = swing.evaluate(packet)
    assert row["status"] == "reference"
    assert row["levels"]["trigger"] == 11.23
    assert row["levels"]["stop"] == 11.0
    assert row["levels"]["distance"] == pytest.approx((11.2 - 11.0) / 11.2 * 100)
    assert set(row["levels"]) == {
        "close",
        "ma20",
        "ma60",
        "relative",
        "trigger",
        "stop",
        "distance",
        "uptrend",
    }
    assert set(swing.price_conditions(packet["bars"], packet["benchmark"])) == {
        "close",
        "ma20",
        "ma60",
        "relative",
        "trigger",
        "stop",
    }
    assert row["as_of"] == packet["sessions"][-1]
    assert row["expires"] == packet["future_sessions"][-1]
    assert "观察线" in row["facts"]["condition"]
    assert "不是最大损失承诺" in row["facts"]["distance"]
    assert row["adjustments"] == [] and "adjustment" not in row["facts"]


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "duplicate",
        "nan",
        "bool",
        "ohlc",
        "price_rise",
        "extreme_ratio",
        "repeated_actions",
        "benchmark_action",
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
    elif damage == "price_rise":
        packet["bars"][-1]["pre_close"] = round(packet["bars"][-2]["close"] * 1.1, 2)
    elif damage == "extreme_ratio":
        packet["bars"][-1]["pre_close"] = round(packet["bars"][-2]["close"] * 0.2, 2)
    elif damage == "repeated_actions":
        for i in (-30, -20, -10):
            packet["bars"][i]["pre_close"] = round(packet["bars"][i - 1]["close"] * 0.95, 2)
    elif damage == "benchmark_action":
        packet["benchmark"][-1]["pre_close"] = round(packet["benchmark"][-2]["close"] * 0.95, 2)
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


def test_ex_rights_break_is_forward_adjusted_and_disclosed():
    packet = demo_packet()
    raw = deepcopy(packet["bars"])
    previous = raw[-16]["close"]
    packet["bars"][-15]["pre_close"] = round(previous * 0.98, 2)
    factor = packet["bars"][-15]["pre_close"] / previous
    row = swing.evaluate(packet)
    assert row["status"] == "reference"
    assert row["adjustments"] == [{"date": packet["sessions"][-15], "factor": round(factor, 4)}]
    # History before the ex-date moves to the latest price basis; later bars stay raw.
    adjusted = [r["close"] * factor for r in raw[:-15]] + [r["close"] for r in raw[-15:]]
    highs = [r["high"] * factor for r in raw[-20:-15]] + [r["high"] for r in raw[-15:]]
    assert row["levels"]["close"] == raw[-1]["close"]
    assert row["levels"]["ma60"] == pytest.approx(sum(adjusted[-60:]) / 60)
    assert row["levels"]["trigger"] == round(max(highs) + 0.01, 2)
    assert (
        "前复权" in row["facts"]["adjustment"]
        and packet["sessions"][-15] in row["facts"]["adjustment"]
    )
    # The disclosure is not a level, so expiry never blanks it.
    assert "adjustment" not in EXPIRED_FACTS


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
        assert result[0]["code"] == packet["code"]
        assert result[0]["collect_error"]["category"] == "issuer_scope"
        assert result[0]["collect_error"]["stage"] == "issuer_collection"
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
    assert result["usage"]["requests"] == result["requests"]
    assert result["usage"]["request_limit"] == 20
    assert result["usage"]["stop_reason"] == "completed"


def legacy_board():
    packet = demo_packet()
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
        # Historical artifacts must not depend on the current pricing algorithm.
        "levels": {"trigger": 11.23, "ceiling": 11.45, "stop": 11.0, "volume": 15000},
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
    board = legacy_board() if legacy is True else demo_board()
    if legacy == "v1":
        # Unadjusted price-reference-v1 artifacts stay readable as published.
        board["rule"] = swing.REFERENCE_RULE_V1
    save_state(tmp_path / run / "result.json", board)
    save_state(tmp_path / "latest.json", {"run": run, "digest": digest(board)})
    return board


@pytest.mark.parametrize("legacy", [False, True, "v1"])
def test_board_integrity_and_expiry(tmp_path, legacy):
    board = saved_board(tmp_path, legacy)
    output = tmp_path / ("swing-" + "a" * 32) / "result.json"
    before = output.read_bytes()
    expiry = date.fromisoformat(board["rows"][0]["expires"])
    loaded = swing.load_board(tmp_path, on=expiry)
    assert loaded and not loaded["rows"][0]["expired"]
    loaded = swing.load_board(tmp_path, on=expiry + timedelta(days=1))
    assert loaded and loaded["rows"][0]["expired"]
    assert output.read_bytes() == before


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
        swing.load_board(root)
