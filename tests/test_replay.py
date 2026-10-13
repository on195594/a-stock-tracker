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


class ForwardClient(SyntheticClient):
    def __init__(self, periods=150, codes=2):
        super().__init__(periods, codes)
        self.changes = {}
        self.empty_days = set()

    def __getattr__(self, endpoint):
        original = super().__getattr__(endpoint)

        def request(**params):
            frame = original(**params)
            if endpoint == "index_daily":
                frame = frame.loc[
                    frame.trade_date.between(params["start_date"], params["end_date"])
                ]
            if endpoint in {"daily", "index_daily"}:
                frame = frame.copy()
                for (day, code), changes in self.changes.items():
                    for field, value in changes.items():
                        frame.loc[(frame.trade_date == day) & (frame.ts_code == code), field] = (
                            value
                        )
                frame = frame.loc[~frame.trade_date.isin(self.empty_days)]
            return frame

        return request


def closed(day, hour=18):
    return replay.datetime.strptime(day, "%Y%m%d").replace(hour=hour, tzinfo=swing.SHANGHAI)


def forward_scan(root, client, at=60, budget=100):
    return replay.scan(root, client.days[at], budget, client, now=closed(client.days[at]))


def test_scan_settle_report_chain(tmp_path, no_wait, monkeypatch):
    client = ForwardClient()
    root = tmp_path / "forward"
    event = forward_scan(root, client)
    assert event["requests"] == 66
    assert event["valid_stock_days"] == 2
    assert len(event["observations"]) == len(event["baseline"]) == 2
    assert no_wait == [0.5] * 66
    path = root / "ledger/events.jsonl"
    prefix = path.read_bytes()
    header = replay.read_ledger(root)[0]
    assert header["rule"] == swing.RULE and header["rule_hash"] == replay.rule_hash()
    assert replay.settle(root, 100, client, now=closed(client.days[124])) == {
        "settled": 0,
        "requests": 1,
    }
    assert path.read_bytes() == prefix
    result = replay.settle(root, 100, client, now=closed(client.days[125]))
    assert result == {"settled": 1, "requests": 67}
    assert path.read_bytes().startswith(prefix)
    settled = replay.read_ledger(root)[-1]
    row = settled["observations"][0]
    assert row["outcome"] == "expired" and row["trigger_date"] is None
    for horizon in (10, 20, 40, 60):
        assert row["metrics"]["returns"][str(horizon)] == pytest.approx(horizon * 0.02 / 11.2 * 100)
    assert row["metrics"]["excess"]["20"] == pytest.approx((0.4 / 11.2 - 0.1 / 10.3) * 100)
    assert row["metrics"]["mae20"] == pytest.approx(0)
    assert row["metrics"]["stop20"] == row["metrics"]["returns"]["20"]
    assert replay.settle(root, 1, client, now=closed(client.days[-1])) == {
        "settled": 0,
        "requests": 0,
    }
    out = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["replay", "report", "--root", str(root), "--out", str(out)])
    assert replay.main() == 0
    report = json.loads(out.read_text())
    assert report["status"] == "insufficient"
    assert report["observations_deduplicated"] == report["observations_settled"] == 2
    assert report["status"] == "insufficient"
    json.dumps(report, allow_nan=False)


def test_scan_budget_resume_duplicate_and_rule_freeze(tmp_path, no_wait, monkeypatch):
    client = ForwardClient()
    root = tmp_path / "forward"
    client.failures = 1
    with pytest.raises(BudgetStop):
        forward_scan(root, client, budget=2)
    assert len(client.calls) == 2
    assert len(replay.read_ledger(root)) == 1
    event = forward_scan(root, client)
    assert event["requests"] == 65
    calls = len(client.calls)
    with pytest.raises(ScreenError, match="不可重复"):
        forward_scan(root, client)
    assert len(client.calls) == calls
    monkeypatch.setattr(replay, "rule_hash", lambda: "changed")
    with pytest.raises(ScreenError, match="规则已变化须新账本"):
        forward_scan(root, client, at=61)
    assert len(client.calls) == calls
    assert replay.settle(root, 100, client, now=closed(client.days[125]))["settled"] == 1


def test_settle_budget_is_hard_and_preserves_scan(tmp_path, no_wait):
    client = ForwardClient()
    root = tmp_path / "forward"
    forward_scan(root, client)
    prefix = (root / "ledger/events.jsonl").read_bytes()
    calls = len(client.calls)
    with pytest.raises(BudgetStop):
        replay.settle(root, 2, client, now=closed(client.days[125]))
    assert len(client.calls) - calls == 2
    assert (root / "ledger/events.jsonl").read_bytes() == prefix
    assert replay.settle(root, 65, client, now=closed(client.days[125])) == {
        "settled": 1,
        "requests": 65,
    }


@pytest.mark.parametrize(
    "damage", ["tamper", "unlink_middle", "previous", "partial", "missing", "truncate_tail"]
)
def test_ledger_tamper_and_break_refused_before_requests(tmp_path, no_wait, damage):
    client = ForwardClient()
    root = tmp_path / "forward"
    forward_scan(root, client)
    forward_scan(root, client, at=61)
    path = root / "ledger/events.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    if damage == "tamper":
        lines[1] = lines[1].replace(b'"valid_stock_days":2', b'"valid_stock_days":3')
    elif damage == "unlink_middle":
        del lines[1]
    elif damage == "previous":
        row = json.loads(lines[-1])
        row["previous"] = "1" * 64
        lines[-1] = json.dumps(row).encode() + b"\n"
    elif damage == "partial":
        lines[-1] = lines[-1][:-10]
    elif damage == "truncate_tail":
        lines.pop()
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"".join(lines))
    calls = len(client.calls)
    for action in (
        lambda: forward_scan(root, client, at=62),
        lambda: replay.settle(root, 100, client, now=closed(client.days[125])),
        lambda: replay.report(root),
    ):
        with pytest.raises(ScreenError, match="账本"):
            action()
    assert len(client.calls) == calls


@pytest.mark.parametrize("outcome", ["triggered", "invalidated_first", "gap_above", "data_gap"])
def test_forward_outcomes_stop_and_missing(tmp_path, no_wait, outcome):
    client = ForwardClient()
    root = tmp_path / "forward"
    forward_scan(root, client)
    day, code = client.days[61], client.codes[0]
    if outcome == "triggered":
        client.changes[(day, code)] = dict(open=11.25, close=11.25, high=11.3, vol=20000)
        client.changes[(client.days[62], code)] = dict(open=10.5, low=10.0, pre_close=11.25)
    elif outcome == "invalidated_first":
        # Invalidation wins even when the same daily bar also meets the trigger.
        client.changes[(day, code)] = dict(low=10.0, close=11.25, high=11.3, vol=20000)
    elif outcome == "gap_above":
        client.changes[(day, code)] = dict(close=12.0, high=12.0)
    else:
        client.empty_days.add(day)
    replay.settle(root, 100, client, now=closed(client.days[125]))
    result = replay.read_ledger(root)[-1]
    row = result["observations"][0]
    assert row["outcome"] == outcome
    if outcome == "triggered":
        assert row["trigger_date"] == day
        assert row["triggered_metrics"]["stop20"] == pytest.approx((10.5 / 11.25 - 1) * 100)
        assert row["triggered_metrics"]["stop_date"] == client.days[62]
        assert set(row["triggered_metrics"]["returns"]) == {"10", "20", "40", "60"}
    if outcome == "data_gap":
        for group in ("observations", "baseline"):
            metrics = result[group][0]["metrics"]
            assert metrics["returns"] == dict.fromkeys(("10", "20", "40", "60"))
            assert metrics["excess"]["20"] is metrics["mae20"] is metrics["stop20"] is None
            assert metrics["data_gaps"] == [day]
    json.dumps(result, allow_nan=False)


def test_forward_split_uses_pre_close_chain_for_stock_and_index(tmp_path, no_wait):
    client = ForwardClient()
    root = tmp_path / "forward"
    forward_scan(root, client)
    for day in client.days[70:]:
        for code, step in [(client.codes[0], 0.02), ("000300.SH", 0.005)]:
            prices = client.price(day, code, step)
            client.changes[(day, code)] = {
                k: prices[k] / 2 for k in replay.PRICE_FIELDS if k != "vol"
            }
    replay.settle(root, 100, client, now=closed(client.days[125]))
    metrics = replay.read_ledger(root)[-1]["observations"][0]["metrics"]
    assert metrics["returns"]["20"] == pytest.approx(0.4 / 11.2 * 100)
    assert metrics["excess"]["20"] == pytest.approx((0.4 / 11.2 - 0.1 / 10.3) * 100)
    assert metrics["mae20"] == pytest.approx(0)


def test_scan_close_calendar_and_sampling(tmp_path, no_wait):
    client = ForwardClient(codes=502)
    day = client.days[60]
    with pytest.raises(ScreenError, match="18:00"):
        replay.scan(tmp_path / "early", day, 100, client, now=closed(day, 17))
    assert not client.calls
    first = forward_scan(tmp_path / "first", client)
    second = forward_scan(tmp_path / "second", client)
    assert first["baseline"] == second["baseline"]
    assert len(first["baseline"]) == 500
    assert len({r["code"] for r in first["baseline"]}) == 500
    assert len(first["observations"]) == 502
    weekend = "20240330"
    with pytest.raises(ScreenError, match="不是交易日"):
        replay.scan(tmp_path / "weekend", weekend, 100, client, now=closed(weekend))
    historical = replay.scan(tmp_path / "historical", day, 100, client, now=closed(client.days[-1]))
    assert not historical["prospective"]
    assert replay.report(tmp_path / "historical")["observations_deduplicated"] == 0


def promotion_events(values=(1, 1, 1, 1, 1, 1, 1), *, baseline=-6, mae=-5):
    events = [{"type": "rule", "rule": swing.RULE, "rule_hash": "synthetic"}]
    for month, value in enumerate(values, 1):
        day = f"2024{month:02}15"
        sessions = pd.bdate_range(end=day, periods=61).strftime("%Y%m%d").tolist()
        samples = [{"code": f"{600000 + i}.SH"} for i in range(50)]
        events.append(
            {
                "type": "scan",
                "as_of": day,
                "prospective": True,
                "sessions": sessions,
                "observations": samples,
            }
        )
        events.append(
            {
                "type": "settle",
                "as_of": day,
                "observations": [
                    {"code": sample["code"], "metrics": {"excess": {"20": value}, "mae20": mae}}
                    for sample in samples
                ],
                "baseline": [{"metrics": {"mae20": baseline}}],
            }
        )
    return events


@pytest.mark.parametrize(
    "case, status",
    [
        ("short", "insufficient"),
        ("pass", "pass"),
        ("negative", "fail"),
        ("ci", "fail"),
        ("missing", "insufficient"),
    ],
)
def test_promotion_gates(case, status):
    events = promotion_events()
    if case == "short":
        events = events[:-2]
    elif case == "negative":
        events = promotion_events((-1,) * 7)
    elif case == "ci":
        events = promotion_events((1, 1, 1, 1, 1, 1, -5))
    elif case == "missing":
        events = promotion_events((None,) * 7)
    result = replay.promotion_report(events)
    assert result["status"] == status
    if case == "pass":
        assert result["forward_months"] == 6 and result["observations_deduplicated"] == 350
        assert result["excess20_ci95"] == [1, 1]
        assert result["positive_month_fraction"] == 1
    json.dumps(result, allow_nan=False)


def test_month_cluster_ci_and_ten_session_deduplication():
    events = promotion_events((1, 2, 3, 4, 5, 6, 7))
    result = replay.promotion_report(events)
    se = np.std([1, 2, 3, 4, 5, 6, 7], ddof=1) / np.sqrt(7)
    assert result["excess20_ci95"] == pytest.approx([4 - 1.96 * se, 4 + 1.96 * se])
    extra = deepcopy(events[1])
    extra["as_of"] = "20240126"  # Nine sessions since January 15: exclude.
    extra["sessions"] = pd.bdate_range(end=extra["as_of"], periods=61).strftime("%Y%m%d").tolist()
    events.append(extra)
    assert replay.promotion_report(events)["observations_deduplicated"] == 350
    extra["as_of"] = "20240129"  # Ten sessions: include, but still unsettled.
    extra["sessions"] = pd.bdate_range(end=extra["as_of"], periods=61).strftime("%Y%m%d").tolist()
    result = replay.promotion_report(events)
    assert result["observations_deduplicated"] == 400
    assert result["observations_settled"] == result["excess20_count"] == 350


def test_missing_later_bar_keeps_only_complete_horizons(tmp_path, no_wait):
    client = ForwardClient()
    root = tmp_path / "forward"
    forward_scan(root, client)
    day = client.days[75]
    client.changes[(day, client.codes[0])] = {"vol": 0}
    replay.settle(root, 100, client, now=closed(client.days[125]))
    row = replay.read_ledger(root)[-1]["observations"][0]
    assert row["outcome"] == "expired"
    assert row["metrics"]["returns"]["10"] is not None
    assert row["metrics"]["returns"]["20"] is None
    assert row["metrics"]["excess"]["20"] is None
    assert row["metrics"]["mae20"] is None
    assert row["metrics"]["data_gaps"] == [day]


def test_ledger_directory_sync_failure_does_not_publish_success(tmp_path, no_wait, monkeypatch):
    client = ForwardClient()
    root = tmp_path / "forward"
    event = forward_scan(root, client)
    path = root / "ledger/events.jsonl"
    prefix = path.read_bytes()
    original = replay.os.fsync
    failed = False

    def fail_once(fd):
        nonlocal failed
        if not failed and replay.stat.S_ISDIR(replay.os.fstat(fd).st_mode):
            failed = True
            raise OSError("synthetic directory fsync failure")
        original(fd)

    monkeypatch.setattr(replay.os, "fsync", fail_once)
    with replay.ledger_root(root), pytest.raises(OSError, match="fsync"):
        replay.append_event(root, {**event, "as_of": client.days[61]})
    assert path.read_bytes() == prefix
    assert len(replay.read_ledger(root)) == 2


def test_interrupted_head_publication_fails_closed(tmp_path, no_wait, monkeypatch):
    client = ForwardClient()
    root = tmp_path / "forward"
    event = forward_scan(root, client)
    original = replay.save_state

    def fail_marker(path, value):
        if path.name == "replay.json":
            raise OSError("synthetic anchor failure")
        original(path, value)

    monkeypatch.setattr(replay, "save_state", fail_marker)
    with replay.ledger_root(root), pytest.raises(OSError, match="anchor"):
        replay.append_event(root, {**event, "as_of": client.days[61]})
    with pytest.raises(ScreenError, match="末端锚点"):
        replay.report(root)


def test_cached_request_identity_mismatch_refused(tmp_path, no_wait):
    client = ForwardClient()
    root = tmp_path / "forward"
    with pytest.raises(BudgetStop):
        forward_scan(root, client, budget=6)
    path = root / "daily" / f"{client.days[0]}.json"
    cached = read_state(path)
    cached["params"]["trade_date"] = client.days[1]
    save_state(path, cached)
    calls = len(client.calls)
    with pytest.raises(ScreenError, match="缓存请求身份"):
        forward_scan(root, client)
    assert len(client.calls) == calls


@pytest.mark.parametrize("invalid", [True, "10000", None])
def test_scan_rejects_invalid_numeric_evidence_on_first_fetch(tmp_path, no_wait, invalid):
    client = ForwardClient()
    price = client.price

    def malformed(day, code, step):
        row = price(day, code, step)
        if code != "000300.SH" and day == client.days[0]:
            row["vol"] = invalid
        return row

    client.price = malformed
    event = forward_scan(tmp_path / "forward", client)
    assert event["valid_stock_days"] == 0
    assert event["observations"] == event["baseline"] == []
    assert len(event["gaps"]) == 2
