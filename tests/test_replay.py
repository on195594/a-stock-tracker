"""Offline-only replay contracts, using synthetic prices and mock acquisition."""

import json
import sys
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from a_stock_tracker import opportunities as swing
from a_stock_tracker import replay
from a_stock_tracker.automation import read_state, save_state
from a_stock_tracker.opportunity_demo import demo_packet
from a_stock_tracker.research import BudgetStop, ScreenError


@pytest.mark.parametrize("kind", ["observation", "weak", "wide"])
def test_shared_conditions_match_evaluate(kind):
    packet = demo_packet()
    if kind == "weak":
        packet["bars"] = deepcopy(packet["benchmark"])
    elif kind == "wide":
        packet["bars"][-1]["low"] = 1.0
    stock = swing.bars(packet["bars"], packet["sessions"])
    benchmark = swing.bars(packet["benchmark"], packet["sessions"])
    original = deepcopy((stock, benchmark))
    result = swing.price_conditions(stock, benchmark)
    evaluated = swing.evaluate(packet)
    assert evaluated["levels"]["trigger"] == result["trigger"]
    assert evaluated["levels"]["stop"] == result["stop"]
    assert evaluated["status"] == "reference"
    assert bool(result["reasons"]) == (kind != "observation")
    assert result["risk"] == pytest.approx(
        (result["ceiling"] - result["stop"]) / result["ceiling"] * 100
    )
    assert (stock, benchmark) == original


def test_price_rounding_keeps_existing_evaluate_behavior():
    packet = demo_packet()
    packet["bars"][-1]["high"] = 11.24
    result = swing.price_conditions(packet["bars"], packet["benchmark"])
    assert result["trigger"] == 11.25
    assert result["ceiling"] == 11.47
    assert swing.RULE == "swing-observation-v1"
    assert swing.evaluate(packet)["levels"]["trigger"] == 11.25


class SyntheticClient:
    def __init__(self, periods=150, codes=1):
        self.days = pd.bdate_range("20240101", periods=periods).strftime("%Y%m%d").tolist()
        self.codes = [f"{600001 + j:06}.SH" for j in range(codes)]
        self.calls = []
        self.failures = 0

    def __getattr__(self, endpoint):
        def request(**params):
            self.calls.append((endpoint, params))
            if self.failures:
                self.failures -= 1
                raise OSError("synthetic failure")
            if endpoint == "trade_cal":
                dates = pd.date_range(params["start_date"], params["end_date"]).strftime("%Y%m%d")
                return pd.DataFrame(
                    [
                        {"exchange": "SSE", "cal_date": d, "is_open": int(d in self.days)}
                        for d in dates
                    ]
                )
            if endpoint == "stock_basic":
                rows = (
                    [
                        {
                            "ts_code": c,
                            "name": "合成公司",
                            "market": "主板",
                            "list_date": "20000101",
                            "delist_date": "",
                        }
                        for c in self.codes
                    ]
                    if params["list_status"] == "L"
                    else []
                )
                return pd.DataFrame(rows, columns=params["fields"].split(","))
            if endpoint == "index_daily":
                return pd.DataFrame([self.price(d, "000300.SH", 0.005) for d in self.days])
            if endpoint == "daily":
                return pd.DataFrame([self.price(params["trade_date"], c, 0.02) for c in self.codes])
            if endpoint == "daily_basic":
                return pd.DataFrame(
                    [
                        {
                            "ts_code": c,
                            "trade_date": params["trade_date"],
                            "pb": 2.0,
                            "total_mv": 600000.0,
                        }
                        for c in self.codes
                    ]
                )
            raise AssertionError(endpoint)

        return request

    def price(self, day, code, step):
        i = self.days.index(day)
        close = round(10 + i * step, 2)
        return {
            "ts_code": code,
            "trade_date": day,
            "open": close,
            "close": close,
            "high": round(close + 0.02, 2),
            "low": round(close - 0.02, 2),
            "pre_close": round(10 + (i - 1) * step, 2),
            "vol": 10000.0,
        }


@pytest.fixture
def no_wait(monkeypatch):
    sleeps = []
    monkeypatch.setattr(replay.time, "sleep", sleeps.append)
    return sleeps


def pull(root, client, budget=1000):
    return replay.fetch(root, client.days[0], client.days[-1], budget, client)


def test_fetch_hard_budget_counts_retries_and_resumes(tmp_path, no_wait):
    root = tmp_path / "cache"
    client = SyntheticClient(periods=3)
    client.failures = 1
    with pytest.raises(BudgetStop):
        pull(root, client, 2)
    assert len(client.calls) == 2
    assert (root / "cal.json").exists()
    assert no_wait == [0.5, 0.5]
    assert pull(root, client) == 8  # L/D/P, index, three daily, one snapshot.
    assert sum(ep == "trade_cal" for ep, _ in client.calls) == 2
    assert pull(root, client, 1) == 0
    assert all(p.suffix != ".pkl" for p in root.rglob("*"))
    assert read_state(root / "cal.json")["endpoint"] == "trade_cal"
    with pytest.raises(ScreenError, match="日期范围"):
        replay.fetch(root, "20240101", "20240110", 1, client)


def test_repeated_failure_never_exceeds_budget(tmp_path, no_wait):
    client = SyntheticClient(3)
    client.failures = 5
    with pytest.raises(BudgetStop):
        pull(tmp_path / "cache", client, 2)
    assert len(client.calls) == 2
    assert not (tmp_path / "cache/cal.json").exists()


@pytest.mark.parametrize(
    "bad",
    [
        "public",
        "symlink",
        "parent_symlink",
        "occupied",
        "service",
        "service_child",
        "research",
        "internal_symlink",
    ],
)
def test_private_root_rejects_unsafe_paths(tmp_path, monkeypatch, no_wait, bad):
    root = tmp_path / "cache"
    if bad == "public":
        root.mkdir(mode=0o755)
        root.chmod(0o755)
    elif bad in {"symlink", "parent_symlink"}:
        target = tmp_path / "target"
        target.mkdir(mode=0o700)
        root.symlink_to(target, target_is_directory=True)
        if bad == "parent_symlink":
            root = root / "child"
    elif bad == "occupied":
        root.mkdir(mode=0o700)
        (root / "unrelated").write_text("preserve")
    elif bad in {"service", "service_child"}:
        monkeypatch.setenv("RESEARCH_SERVICE_ROOT", str(root))
        if bad == "service_child":
            root = root / "child"
    elif bad == "research":
        monkeypatch.setattr(replay, "DATA_DIR", tmp_path)
        root = tmp_path / "research"
    elif bad == "internal_symlink":
        pull(root, SyntheticClient(3))
        (root / "daily/20240101.json").unlink()
        (root / "daily/20240101.json").symlink_to(root / "cal.json")
    client = SyntheticClient(3)
    with pytest.raises(ScreenError):
        pull(root, client)
    assert not client.calls


def test_corrupted_cache_is_not_skipped(tmp_path, no_wait):
    root = tmp_path / "cache"
    client = SyntheticClient(3)
    pull(root, client)
    path = root / "cal.json"
    path.write_text(path.read_text().replace("SSE", "XXX"))
    with pytest.raises(ScreenError, match="checksum"):
        pull(root, client)


def test_run_uses_shared_formula_and_correct_forward_statistics(tmp_path, no_wait, monkeypatch):
    client = SyntheticClient()
    root = tmp_path / "cache"
    pull(root, client)
    actual = replay.price_conditions
    computed = []

    def checked(stock, benchmark):
        result = actual(stock, benchmark)
        computed.append(result)
        return result

    monkeypatch.setattr(replay, "price_conditions", checked)
    result = replay.run(root)
    assert len(computed) == 25  # [60, 150 - 5 - 60)
    assert result["stock_days_valid"] == 25
    assert result["pass_rates"] == dict.fromkeys(
        ("trend", "rs", "trend_and_rs", "observation"), 1.0
    )
    assert computed[0]["trigger"] == 11.23
    assert computed[0]["stop"] == 11.0
    assert result["observation_5d_outcome"] == {
        "triggered": 0,
        "invalidated_first": 0,
        "gap_above": 0,
        "expired": 3,
    }
    signals = result["observation_signal_close"]
    assert signals["n"] == 3
    assert signals["h20"]["mean"] == round(
        np.mean([0.4 / (10 + t * 0.02) for t in (60, 70, 80)]) * 100, 2
    )
    assert signals["h20"]["excess_mean"] == round(
        np.mean([0.4 / (10 + t * 0.02) - 0.1 / round(10 + t * 0.005, 2) for t in (60, 70, 80)])
        * 100,
        2,
    )
    assert signals["mae20_median"] == 0.0
    assert signals["stop_rule_20d_mean"] == signals["h20"]["mean"]
    assert result["triggered_entry_close"] == {"n": 0}
    json.dumps(result, allow_nan=False)
    out = tmp_path / "result.json"
    monkeypatch.setattr(sys, "argv", ["replay", "run", "--root", str(root), "--out", str(out)])
    assert replay.main() == 0
    assert json.loads(out.read_text()) == result
    with pytest.raises(ScreenError, match="已存在"):
        replay.main()


@pytest.mark.parametrize("outcome", ["triggered", "invalidated_first", "gap_above"])
def test_trigger_window_and_gap_stop(tmp_path, no_wait, outcome):
    client = SyntheticClient(90)
    root = tmp_path / "cache"
    pull(root, client)
    day = client.days[61]
    path = root / "daily" / f"{day}.json"
    state = read_state(path)
    row = dict(zip(state["columns"], state["data"][0], strict=True))
    if outcome == "triggered":
        row.update(open=11.25, close=11.25, high=11.3, vol=20000)
    elif outcome == "invalidated_first":
        row.update(low=10.0)
    else:
        row.update(close=12.0, high=12.0)
    state["data"][0] = [row[k] for k in state["columns"]]
    save_state(path, state)
    if outcome == "triggered":
        stop_path = root / "daily" / f"{client.days[62]}.json"
        state = read_state(stop_path)
        row = dict(zip(state["columns"], state["data"][0], strict=True))
        row.update(open=10.5, low=10.0, pre_close=11.25)
        state["data"][0] = [row[k] for k in state["columns"]]
        save_state(stop_path, state)
    result = replay.run(root, 20)
    assert result["observation_5d_outcome"][outcome] == 1
    if outcome == "triggered":
        assert result["triggered_entry_close"]["n"] == 1
        assert result["triggered_entry_close"]["stop_rule_20d_mean"] == round(
            (10.5 / 11.25 - 1) * 100, 2
        )


@pytest.mark.parametrize("damage", ["vol", "nan", "ohlc", "basis", "missing"])
def test_vectorized_evidence_validation_matches_bars(damage):
    packet = demo_packet()
    rows = packet["bars"]
    if damage == "vol":
        rows[0]["vol"] = 0
    elif damage == "nan":
        rows[5]["close"] = float("nan")
    elif damage == "ohlc":
        rows[5]["high"] = 1.0
    elif damage == "basis":
        rows[5]["pre_close"] = 1.0
    else:
        rows[5] = {k: float("nan") for k in replay.PRICE_FIELDS} | {"date": rows[5]["date"]}
    values = {k: np.array([[r[k]] for r in rows]) for k in replay.PRICE_FIELDS}
    assert not replay.valid_windows(values)[60, 0]
    with pytest.raises(ScreenError):
        swing.bars(rows, packet["sessions"])


@pytest.mark.parametrize("invalid", [0, True, "10000"])
def test_no_valid_windows_and_small_horizon(tmp_path, no_wait, invalid):
    client = SyntheticClient(90)
    root = tmp_path / "cache"
    pull(root, client)
    path = root / "daily" / f"{client.days[45]}.json"
    state = read_state(path)
    state["data"][0][state["columns"].index("vol")] = invalid
    save_state(path, state)
    result = replay.run(root, 5)
    assert result["stock_days_valid"] == 0
    assert result["pass_rates"]["observation"] is None
    assert result["observation_signal_close"] == {"n": 0}
    json.dumps(result, allow_nan=False)
