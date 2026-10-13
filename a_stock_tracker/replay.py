"""Explicit bounded acquisition and offline replay; never used by the Web."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import inspect
import json
import logging
import math
import os
import re
import stat
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from a_stock_tracker.automation import digest, encoded, read_state, save, save_state
from a_stock_tracker.calendar import _atomic_write_bytes
from a_stock_tracker.opportunities import RULE, SHANGHAI, bars, price_conditions
from a_stock_tracker.paths import DATA_DIR, PROJECT_ROOT
from a_stock_tracker.research import BudgetStop, ScreenError, now_iso

LOG = logging.getLogger(__name__)
PRICE_FIELDS = ("open", "high", "low", "close", "pre_close", "vol")
PRICE_COLUMNS = "ts_code,trade_date," + ",".join(PRICE_FIELDS)
OWNER = "a-stock-tracker.replay-v1"


def safe_path(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ScreenError("请使用不含上级跳转的绝对路径")
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ScreenError("拒绝符号链接")


def isolated_path(path: Path) -> None:
    safe_path(path)
    forbidden = {PROJECT_ROOT / "data" / "research", DATA_DIR / "research"}
    configured = os.getenv("RESEARCH_SERVICE_ROOT")
    if configured:
        forbidden.add(Path(configured).expanduser().resolve())
    if any(path == p or p in path.parents or path in p.parents for p in forbidden):
        raise ScreenError("重放目录不得与研究目录重叠")
    if any((p / "request-service.json").exists() for p in (path, *path.parents)):
        raise ScreenError("重放目录不得位于研究服务内")


@contextmanager
def replay_root(root: Path, *, create: bool = False):
    isolated_path(root)
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not root.is_dir() or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise ScreenError("重放目录必须是私有目录（0700）")
    marker = root / "replay.json"
    lock = root / ".lock"
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ScreenError("重放缓存只允许普通文件和目录")
    if not marker.exists() and (not create or any(p != lock for p in root.iterdir())):
        raise ScreenError("首次重放目录必须为空")
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ScreenError("重放目录正在使用") from exc
        yield root


def compact_date(value: str) -> str:
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError("日期须为 YYYYMMDD")
    datetime.strptime(value, "%Y%m%d")
    return value


def read_table(path: Path) -> pd.DataFrame:
    safe_path(path)
    value = read_state(path)
    frame = pd.DataFrame(value["data"], columns=value["columns"])
    # Preserve bars() numeric semantics before concatenation can coerce bools to floats.
    for key in set(PRICE_FIELDS) & set(frame.columns):
        if frame[key].dtype.kind not in "iuf":
            frame[key] = frame[key].map(lambda v: v if type(v) in (int, float) else np.nan)
    return frame


def calendar_days(calendar: pd.DataFrame, start: str, end: str) -> list[str]:
    expected = pd.date_range(start, end).strftime("%Y%m%d").tolist()
    if (
        calendar.cal_date.duplicated().any()
        or sorted(calendar.cal_date) != expected
        or not calendar.exchange.eq("SSE").all()
        or not calendar.is_open.isin([0, 1]).all()
    ):
        raise ScreenError("日历缺失或冲突")
    days = sorted(calendar.loc[calendar.is_open == 1, "cal_date"].tolist())
    if not days:
        raise ScreenError("范围内没有交易日")
    return days


class RequestCache:
    """Share the request cap, pacing and verified JSON cache across acquisition modes."""

    def __init__(self, root: Path, request_budget: int, client: Any):
        if request_budget < 1:
            raise ValueError("请求预算须为正整数")
        self.root, self.budget, self.client = root, request_budget, client
        self.used = 0

    def table(
        self, filename: str, endpoint: str, fields: str, *, allow_empty: bool = False, **params: Any
    ) -> pd.DataFrame:
        path = self.root / filename
        safe_path(path)
        if path.exists():
            state = read_state(path)
            if state["endpoint"] != endpoint or state["params"] != params:
                raise ScreenError("缓存请求身份不符")
            frame = read_table(path)
            if not set(fields.split(",")).issubset(frame.columns):
                raise ScreenError("缓存字段不完整")
            if frame.empty and not allow_empty and endpoint != "stock_basic":
                raise ScreenError("缓存为空")
            return frame
        for attempt in range(3):
            if self.used >= self.budget:
                raise BudgetStop("请求预算已耗尽；已完成缓存可在另行授权后续拉")
            self.used += 1
            time.sleep(0.5)
            try:
                frame = getattr(self.client, endpoint)(fields=fields, **params)
                break
            except Exception as exc:
                LOG.warning(
                    "%s 请求失败（%s），已用请求 %d", endpoint, type(exc).__name__, self.used
                )
                if attempt == 2:
                    raise ScreenError(f"{endpoint} 三次请求失败") from None
        if not set(fields.split(",")).issubset(frame.columns):
            raise ScreenError(f"{endpoint} 返回字段不完整")
        if (
            frame.empty
            and not allow_empty
            and not (endpoint == "stock_basic" and params["list_status"] != "L")
        ):
            raise ScreenError(f"{endpoint} 返回空数据，未保存为成功缓存")
        if len(frame) >= 6000:
            raise ScreenError(f"{endpoint} 疑似达到接口截断上限")
        value = json.loads(frame.to_json(orient="split", force_ascii=False, double_precision=15))
        value.pop("index")
        save_state(path, {**value, "endpoint": endpoint, "params": params, "fetched_at": now_iso()})
        return read_table(path)


def fetch(root: Path, start: str, end: str, request_budget: int, client: Any) -> int:
    """Each invocation has its own hard request cap, including up to three attempts."""
    compact_date(start)
    compact_date(end)
    if start > end or request_budget < 1:
        raise ValueError("日期范围或请求预算无效")
    manifest = {"owner": OWNER, "start": start, "end": end}
    with replay_root(root, create=True):
        marker = root / "replay.json"
        if marker.exists():
            if read_state(marker) != manifest:
                raise ScreenError("缓存归属或日期范围不符；请使用新目录")
        else:
            save_state(marker, manifest)

        cache = RequestCache(root, request_budget, client)
        table = cache.table

        days = calendar_days(
            table(
                "cal.json",
                "trade_cal",
                "exchange,cal_date,is_open",
                exchange="SSE",
                start_date=start,
                end_date=end,
            ),
            start,
            end,
        )
        for status in "LDP":
            table(
                f"basic-{status}.json",
                "stock_basic",
                "ts_code,name,list_date,delist_date,market",
                list_status=status,
            )
        table(
            "hs300.json",
            "index_daily",
            PRICE_COLUMNS,
            ts_code="000300.SH",
            start_date=start,
            end_date=end,
        )
        for day in days:
            table(f"daily/{day}.json", "daily", PRICE_COLUMNS, trade_date=day)
        month_ends = {day[:6]: day for day in days}
        for day in month_ends.values():
            table(
                f"basic_daily/{day}.json",
                "daily_basic",
                "ts_code,trade_date,pb,total_mv",
                trade_date=day,
            )
    LOG.info("拉取完成：请求 %d 次，交易日 %d 个", cache.used, len(days))
    return cache.used


def valid_windows(values: dict[str, Any]) -> Any:
    """Vectorize bars() evidence checks, not the price-condition formulas."""
    o, h, low, c, p, v = (values[k] for k in PRICE_FIELDS)
    good = np.logical_and.reduce([np.isfinite(x) & (x > 0) for x in (o, h, low, c, p, v)])
    good &= (low <= np.minimum(o, c)) & (np.maximum(o, c) <= h)
    breaks = np.zeros_like(good, dtype=int)
    breaks[1:] = ~(np.abs(p[1:] - c[:-1]) <= 0.011)
    invalid = np.vstack([np.zeros(good.shape[1], dtype=int), np.cumsum(~good, axis=0)])
    broken = np.vstack([np.zeros(good.shape[1], dtype=int), np.cumsum(breaks, axis=0)])
    valid = np.zeros_like(good)
    valid[60:] = (invalid[61:] == invalid[:-61]) & (broken[61:] == broken[1:-60])
    return valid


def replay(
    daily: pd.DataFrame,
    basic: pd.DataFrame,
    benchmark: pd.DataFrame,
    days: list[str],
    horizon: int = 60,
) -> dict[str, Any]:
    """Replay shared conditions; retain the reference's sampling and return conventions."""
    if horizon < 1 or len(days) <= 65 + max(20, horizon):
        raise ScreenError("样本不足：需预留 61 日日线、5 日窗口与至少 20 日前瞻")
    if basic.ts_code.duplicated().any():
        raise ScreenError("证券身份重复或冲突")
    eligible = basic.loc[
        basic.ts_code.str.fullmatch(r"(?:60\d{4}\.SH|00\d{4}\.SZ)")
        & basic.market.eq("主板")
        & basic.name.str.len().between(1, 30)
        & ~basic.name.str.upper().str.contains("ST|退", na=True),
        "ts_code",
    ]
    daily = daily[daily.ts_code.isin(eligible)]
    if daily.empty or daily.duplicated(["trade_date", "ts_code"]).any():
        raise ScreenError("没有主板日线或存在重复股日")
    if not set(daily.trade_date).issubset(days):
        raise ScreenError("日线日期不在交易日历内")
    if benchmark.ts_code.ne("000300.SH").any() or benchmark.trade_date.duplicated().any():
        raise ScreenError("基准身份或日期冲突")
    benchmark = benchmark.set_index("trade_date").reindex(days)
    b_records = benchmark[list(PRICE_FIELDS)].to_dict("records")
    # Validate the entire benchmark once, including every adjacent price basis.
    bars([{**r, "date": d} for d, r in zip(days, b_records, strict=True)], days)
    values = {
        k: daily.pivot(index="trade_date", columns="ts_code", values=k)
        .reindex(days)
        .to_numpy(float)
        for k in PRICE_FIELDS
    }
    o, high, low, close, pre_close, vol = (values[k] for k in PRICE_FIELDS)
    b = benchmark.close.to_numpy(float)
    valid = valid_windows(values)
    t_count, n_codes = close.shape
    last = t_count - 5 - max(20, horizon)
    span = slice(60, last)
    trend, rs, obs = (np.zeros_like(valid) for _ in range(3))
    trigger, stop, ceiling, risk, volume = (np.full_like(close, np.nan) for _ in range(5))
    for j in range(n_codes):
        records = [
            dict(zip(PRICE_FIELDS, row, strict=True))
            for row in zip(*(values[k][:, j].tolist() for k in PRICE_FIELDS), strict=True)
        ]
        for t in (np.flatnonzero(valid[span, j]) + 60).tolist():
            result = price_conditions(records[t - 60 : t + 1], b_records[t - 60 : t + 1])
            trend[t, j] = result["close"] > result["ma20"] > result["ma60"]
            rs[t, j] = result["relative"] > 0
            obs[t, j] = not result["reasons"]
            for target, key in (
                (trigger, "trigger"),
                (stop, "stop"),
                (ceiling, "ceiling"),
                (risk, "risk"),
                (volume, "volume"),
            ):
                target[t, j] = result[key]
    momentum = trend & rs
    ratios = close / pre_close
    ratios[np.isnan(ratios)] = 1.0
    adjusted = np.cumprod(ratios, axis=0)
    adjusted[np.isnan(close)] = np.nan
    factor = adjusted / close
    horizons = sorted(set([h for h in (10, 20, 40, 60) if h <= horizon] + [horizon]))

    def event_stats(events: list[tuple[int, int, int]]) -> dict[str, Any]:
        if not events:
            return {"n": 0}
        entry, js, signal = np.array(events).T
        base = adjusted[entry, js]
        result: dict[str, Any] = {"n": len(events), "unique_stocks": len(set(js.tolist()))}
        returns = {}
        excess = {}
        for h in sorted(set(horizons + [20])):
            x = adjusted[entry + h, js] / base - 1
            ex = x - (b[entry + h] / b[entry] - 1)
            returns[h], excess[h] = x, ex
            if h in horizons:
                result[f"h{h}"] = {
                    "mean": pd.Series(x).mean() * 100,
                    "median": pd.Series(x).median() * 100,
                    "win": round(float((x > 0).mean()), 3),
                    "excess_mean": pd.Series(ex).mean() * 100,
                    "excess_win": round(float((ex > 0).mean()), 3),
                    "missing": int((~np.isfinite(x)).sum()),
                }
                for key in ("mean", "median", "excess_mean"):
                    result[f"h{h}"][key] = round(float(result[f"h{h}"][key]), 2)
        future = entry[:, None] + np.arange(1, 21)
        lows = low[future, js[:, None]] * factor[future, js[:, None]]
        mae = pd.DataFrame(lows).min(axis=1).to_numpy() / base - 1
        stop_adjusted = stop[signal, js] * factor[signal, js]
        hits = lows < stop_adjusted[:, None]
        first = hits.argmax(axis=1)
        opens = o[entry + first + 1, js] * factor[entry + first + 1, js]
        sr = np.where(hits.any(axis=1), np.minimum(opens, stop_adjusted) / base - 1, returns[20])
        result["mae20_median"] = round(float(pd.Series(mae).median() * 100), 2)
        result["stop_rule_20d_mean"] = round(float(pd.Series(sr).mean() * 100), 2)
        years = pd.DataFrame({"year": [days[t][:4] for t in signal], "excess": excess[20]})
        result["by_year_h20_excess"] = (
            years.groupby("year").excess.agg(["count", "mean"]).round(4).to_dict("index")
        )
        return result

    def dedupe(mask: Any) -> list[tuple[int, int, int]]:
        events = []
        next_day = np.zeros(n_codes, dtype=int)
        for t in range(60, last):
            for j in np.flatnonzero(mask[t] & (t >= next_day)).tolist():
                events.append((t, j, t))
                next_day[j] = t + 10
        return events

    signals = dedupe(obs)
    triggered = []
    outcomes = dict.fromkeys(("triggered", "invalidated_first", "gap_above", "expired"), 0)
    for t, j, _ in signals:
        state = "expired"
        for u in range(t + 1, t + 6):
            if low[u, j] < stop[t, j]:
                state = "invalidated_first"
                break
            if trigger[t, j] <= close[u, j] <= ceiling[t, j] and vol[u, j] >= volume[t, j]:
                state = "triggered"
                triggered.append((u, j, t))
                break
            if close[u, j] > ceiling[t, j]:
                state = "gap_above"
                break
        outcomes[state] += 1
    vt, vj = np.nonzero(valid[span])
    sampled = np.random.default_rng(0).choice(len(vt), size=min(20000, len(vt)), replace=False)
    baseline = [(int(vt[i]) + 60, int(vj[i]), int(vt[i]) + 60) for i in sampled]
    nv = int(valid[span].sum())
    distances = risk[span][momentum[span]]
    per_day = obs[span].sum(axis=1)
    per_momentum = momentum[span].sum(axis=1)
    return {
        "rule": RULE,
        "horizon": horizon,
        "dates": [days[0], days[-1]],
        "trading_days": t_count,
        "codes": n_codes,
        "stock_days_valid": nv,
        "pass_rates": {
            name: float(mask[span].sum() / nv) if nv else None
            for name, mask in (
                ("trend", trend),
                ("rs", rs),
                ("trend_and_rs", momentum),
                ("observation", obs),
            )
        },
        "per_day_observations": {
            "mean": float(per_day.mean()),
            "median": float(np.median(per_day)),
            "zero_days": float((per_day == 0).mean()),
        },
        "per_day_momentum": {
            "mean": float(per_momentum.mean()),
            "median": float(np.median(per_momentum)),
        },
        "risk_distance_among_momentum": {
            f"p{q}": float(np.percentile(distances, q)) if len(distances) else None
            for q in (10, 25, 50, 75, 90)
        },
        "risk_distance_share": {
            f"<={k}": float((distances <= k).mean()) if len(distances) else None
            for k in (8, 10, 12, 15)
        },
        "p_obs_given_momentum": float(obs[span].sum() / momentum[span].sum())
        if momentum[span].any()
        else None,
        "baseline_random_valid": event_stats(baseline),
        "momentum_trend_rs_signal_close": event_stats(dedupe(momentum)),
        "observation_signal_close": event_stats(signals),
        "observation_5d_outcome": outcomes,
        "triggered_entry_close": event_stats(triggered),
        "limits": "仅离线验证，不作为收益证明；当前名称筛查 ST，未含逐日风险名单、ROE、交易成本、T+1或涨停成交约束。收益按 pre_close 链计算；缺失远期收益不填零，胜率分母保留全部事件；停牌缺日的观察窗口沿用参考口径，可能记为过期。",
    }


def run(root: Path, horizon: int = 60) -> dict[str, Any]:
    with replay_root(root):
        manifest = read_state(root / "replay.json")
        if manifest["owner"] != OWNER:
            raise ScreenError("重放缓存归属不符")
        days = calendar_days(read_table(root / "cal.json"), manifest["start"], manifest["end"])
        frames = []
        for day in days:
            frame = read_table(root / "daily" / f"{day}.json")
            if frame.empty or frame.trade_date.ne(day).any():
                raise ScreenError("日线缓存日期不符或为空")
            frames.append(frame)
        result = replay(
            pd.concat(frames, ignore_index=True),
            pd.concat([read_table(root / f"basic-{s}.json") for s in "LDP"], ignore_index=True),
            read_table(root / "hs300.json"),
            days,
            horizon,
        )
    # JSON null preserves unavailable statistics without nonstandard NaN literals.
    return json.loads(pd.Series(result).to_json(force_ascii=False, double_precision=15))


LEDGER_OWNER = {"owner": OWNER, "mode": "prospective-v1"}
FORWARD_CONTRACT = {
    "dedupe_sessions": 10,
    "window": 5,
    "horizons": [10, 20, 40, 60],
    "baseline_max": 500,
    "seed": 0,
    "order": "ts_code",
    "entry": "observation_close",
    "outcome_order": ["invalidated_first", "triggered", "gap_above", "expired"],
}


def rule_hash() -> str:
    # Literal parameters are in the source; include any referenced module constants too.
    constants = {
        name: price_conditions.__globals__[name]
        for name in price_conditions.__code__.co_names
        if name in price_conditions.__globals__
        and isinstance(price_conditions.__globals__[name], (str, int, float, tuple, list, dict))
    }
    return digest(
        {"rule": RULE, "source": inspect.getsource(price_conditions), "constants": constants}
    )


def read_ledger(root: Path) -> list[dict[str, Any]]:
    path = root / "ledger/events.jsonl"
    safe_path(path)
    if not path.is_file():
        raise ScreenError("账本缺失，拒绝重新初始化")
    payload = path.read_bytes()
    if not payload or not payload.endswith(b"\n"):
        raise ScreenError("账本断链或末行不完整")
    events: list[dict[str, Any]] = []
    previous = "0" * 64
    scans: set[str] = set()
    settled: set[str] = set()
    try:
        for line in payload.splitlines():
            record = json.loads(line)
            sha = record.pop("hash")
            if record["previous"] != previous or digest(record) != sha:
                raise ScreenError("账本哈希不符或断链")
            event = record["event"]
            if not events:
                if event["type"] != "rule" or event["rule"] != RULE:
                    raise ScreenError("账本首行规则身份不符")
            elif event["type"] == "scan":
                day = compact_date(event["as_of"])
                if day in scans:
                    raise ScreenError("账本扫描日期重复")
                scans.add(day)
            elif event["type"] == "settle":
                day = event["as_of"]
                if day not in scans or day in settled:
                    raise ScreenError("账本结算引用无效或重复")
                settled.add(day)
            else:
                raise ScreenError("账本事件类型无效")
            events.append(event)
            previous = sha
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ScreenError("账本格式无效或断链") from exc
    if read_state(root / "replay.json").get("head") != previous:
        raise ScreenError("账本末端锚点不符，可能被截断或写入未完成")
    return events


def append_event(root: Path, event: dict[str, Any]) -> None:
    path = root / "ledger/events.jsonl"
    prefix = path.read_bytes() if path.exists() else b""
    previous = json.loads(prefix.splitlines()[-1])["hash"] if prefix else "0" * 64
    record = {"previous": previous, "event": event}
    sha = digest(record)
    # Replace atomically with the exact existing prefix plus one immutable event.
    _atomic_write_bytes(path, prefix + encoded({**record, "hash": sha}) + b"\n")
    # A separate head detects tail truncation; interrupted two-file publication fails closed.
    save_state(root / "replay.json", {**LEDGER_OWNER, "head": sha})


@contextmanager
def ledger_root(root: Path, *, create: bool = False):
    with replay_root(root, create=create):
        marker = root / "replay.json"
        if not marker.exists():
            save_state(marker, LEDGER_OWNER)
            append_event(
                root,
                {
                    "type": "rule",
                    "rule": RULE,
                    "rule_hash": rule_hash(),
                    "contract": FORWARD_CONTRACT,
                    "recorded_at": now_iso(),
                },
            )
        elif {k: v for k, v in read_state(marker).items() if k != "head"} != LEDGER_OWNER:
            raise ScreenError("前瞻账本不可与历史重放缓存混用")
        yield read_ledger(root)


def closed_cutoff(now: datetime) -> str:
    now = now.astimezone(SHANGHAI)
    day = now.date() if now.hour >= 18 else now.date() - timedelta(days=1)
    return day.strftime("%Y%m%d")


def cached_calendar(cache: RequestCache, start: str, end: str) -> list[str]:
    return calendar_days(
        cache.table(
            f"calendar/{start}-{end}.json",
            "trade_cal",
            "exchange,cal_date,is_open",
            exchange="SSE",
            start_date=start,
            end_date=end,
        ),
        start,
        end,
    )


def daily_table(cache: RequestCache, day: str, *, allow_empty: bool = False) -> pd.DataFrame:
    frame = cache.table(
        f"daily/{day}.json", "daily", PRICE_COLUMNS, allow_empty=allow_empty, trade_date=day
    )
    if frame.trade_date.ne(day).any() or frame.ts_code.duplicated().any():
        raise ScreenError("日线身份或日期冲突")
    return frame


def benchmark_table(
    cache: RequestCache, start: str, end: str, *, allow_empty: bool = False
) -> pd.DataFrame:
    frame = cache.table(
        f"benchmark/{start}-{end}.json",
        "index_daily",
        PRICE_COLUMNS,
        allow_empty=allow_empty,
        ts_code="000300.SH",
        start_date=start,
        end_date=end,
    )
    if (
        frame.ts_code.ne("000300.SH").any()
        or frame.trade_date.duplicated().any()
        or not frame.trade_date.between(start, end).all()
    ):
        raise ScreenError("基准身份或日期冲突")
    return frame


def dated_bars(frame: pd.DataFrame, days: list[str]) -> list[dict[str, float]]:
    rows = frame.set_index("trade_date").reindex(days).reset_index().to_dict("records")
    return bars([{**row, "date": row["trade_date"]} for row in rows], days)


def scan(
    root: Path, as_of: str, request_budget: int, client: Any, *, now: datetime | None = None
) -> dict[str, Any]:
    compact_date(as_of)
    now = now or datetime.now(SHANGHAI)
    if as_of > closed_cutoff(now):
        raise ScreenError("as_of 必须是已收盘交易日；当日须上海时间18:00后")
    with ledger_root(root, create=True) as events:
        if events[0]["rule_hash"] != rule_hash() or events[0]["contract"] != FORWARD_CONTRACT:
            raise ScreenError("规则已变化须新账本")
        previous = [event["as_of"] for event in events if event["type"] == "scan"]
        if as_of in previous:
            raise ScreenError("同一 as_of 不可重复 scan")
        cache = RequestCache(root, request_budget, client)
        start = (datetime.strptime(as_of, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
        calendar = cached_calendar(cache, start, closed_cutoff(now))
        history = [day for day in calendar if day <= as_of]
        if not history or history[-1] != as_of or len(history) < 61:
            raise ScreenError("as_of 不是交易日或不足61个交易日")
        days = history[-61:]
        basic = pd.concat(
            [
                cache.table(
                    f"securities/{as_of}-{status}.json",
                    "stock_basic",
                    "ts_code,name,list_date,delist_date,market",
                    list_status=status,
                )
                for status in "LDP"
            ],
            ignore_index=True,
        )
        if basic.ts_code.duplicated().any():
            raise ScreenError("证券身份重复或冲突")
        eligible = basic.loc[
            basic.ts_code.str.fullmatch(r"(?:60\d{4}\.SH|00\d{4}\.SZ)")
            & basic.market.eq("主板")
            & basic.name.str.len().between(1, 30)
            & ~basic.name.str.upper().str.contains("ST|退", na=True)
            & basic.list_date.str.fullmatch(r"\d{8}")
            & (basic.list_date <= days[0])
            & (basic.delist_date.fillna("").eq("") | (basic.delist_date > as_of))
        ].sort_values("ts_code")
        benchmark = dated_bars(benchmark_table(cache, days[0], as_of), days)
        daily = pd.concat([daily_table(cache, day) for day in days], ignore_index=True)
        grouped = {code: frame for code, frame in daily.groupby("ts_code")}
        valid, observations, gaps = [], [], []
        for issuer in eligible.to_dict("records"):
            code = issuer["ts_code"]
            try:
                if code not in grouped:
                    raise ScreenError("缺少证券日线")
                stock = dated_bars(grouped[code], days)
                conditions = price_conditions(stock, benchmark)
            except ScreenError as exc:
                gaps.append({"code": code, "reason": str(exc)})
                continue
            sample = {"code": code, "name": issuer["name"], "conditions": conditions}
            valid.append(sample)
            if not conditions["reasons"]:
                observations.append(sample)
        seed = int(hashlib.sha256(f"{FORWARD_CONTRACT['seed']}:{as_of}".encode()).hexdigest(), 16)
        selected = np.random.default_rng(seed).choice(
            len(valid), size=min(500, len(valid)), replace=False
        )
        event = {
            "type": "scan",
            "as_of": as_of,
            "recorded_at": now.isoformat(),
            "sessions": days,
            "requests": cache.used,
            "valid_stock_days": len(valid),
            "observations": observations,
            "baseline": [valid[i] for i in sorted(selected.tolist())],
            "gaps": gaps,
            # Historical backfills remain auditable but cannot count toward promotion.
            "prospective": as_of == calendar[-1],
        }
        append_event(root, event)
        LOG.info(
            "扫描完成：%s，观察 %d，基线 %d，请求 %d",
            as_of,
            len(observations),
            len(selected),
            cache.used,
        )
        return event


def forward_rows(frame: pd.DataFrame, days: list[str]) -> list[dict[str, float] | None]:
    indexed = frame.set_index("trade_date")
    result: list[dict[str, float] | None] = []
    for day in days:
        try:
            row = indexed.loc[day].to_dict()
            # Validate individual bars; forward returns intentionally allow corporate actions.
            result.append(bars([{**row, "date": day}], [day])[0])
        except (KeyError, ScreenError):
            result.append(None)
    return result


def forward_metrics(
    stock: list[dict[str, float] | None],
    benchmark: list[dict[str, float] | None],
    days: list[str],
    close: float,
    stop: float,
) -> dict[str, Any]:
    value, index_value = 1.0, 1.0
    returns, excess = {}, {}
    lows = []
    gaps = []
    stop_result = None
    stop_day = None
    for i, (row, index) in enumerate(zip(stock, benchmark, strict=True), 1):
        if row is None:
            value = math.nan
            gaps.append(days[i - 1])
        else:
            factor = value / row["pre_close"]
            value *= row["close"] / row["pre_close"]
            if i <= 20:
                low = row["low"] * factor
                lows.append(low)
                if stop_day is None and low < stop / close:
                    stop_day = days[i - 1]
                    stop_result = (min(row["open"] * factor, stop / close) - 1) * 100
        if index is None:
            index_value = math.nan
            if days[i - 1] not in gaps:
                gaps.append(days[i - 1])
        else:
            index_value *= index["close"] / index["pre_close"]
        if i in (10, 20, 40, 60):
            returns[str(i)] = (value - 1) * 100 if math.isfinite(value) else None
            excess[str(i)] = (
                (value - index_value) * 100 if math.isfinite(value - index_value) else None
            )
    complete20 = len(stock) >= 20 and all(row is not None for row in stock[:20])
    return {
        "returns": returns,
        "excess": excess,
        "mae20": (min(1.0, *lows) - 1) * 100 if complete20 else None,
        "stop20": (stop_result if stop_day else returns.get("20")) if complete20 else None,
        "stop_date": stop_day if complete20 else None,
        "data_gaps": sorted(set(gaps)),
    }


def settle_sample(
    sample: dict[str, Any],
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    days: list[str],
    *,
    observation: bool,
) -> dict[str, Any]:
    code, conditions = sample["code"], sample["conditions"]
    stock = forward_rows(daily[daily.ts_code == code], days)
    index = forward_rows(benchmark, days)
    result = {
        "code": code,
        "metrics": forward_metrics(
            stock[:60], index[:60], days[:60], conditions["close"], conditions["stop"]
        ),
    }
    if not observation:
        return result
    outcome, triggered, previous = "expired", None, conditions["close"]
    for i, row in enumerate(stock[:5]):
        if row is None or abs(row["pre_close"] - previous) > 0.011:
            outcome = "data_gap"
            break
        if row["low"] < conditions["stop"]:
            outcome = "invalidated_first"
            break
        if (
            conditions["trigger"] <= row["close"] <= conditions["ceiling"]
            and row["vol"] >= conditions["volume"]
        ):
            outcome, triggered = "triggered", i
            break
        if row["close"] > conditions["ceiling"]:
            outcome = "gap_above"
            break
        previous = row["close"]
    result.update(
        outcome=outcome,
        trigger_date=days[triggered] if triggered is not None else None,
        triggered_metrics=None,
    )
    if triggered is not None:
        entry = stock[triggered]
        assert entry is not None
        begin = triggered + 1
        result["triggered_metrics"] = forward_metrics(
            stock[begin : begin + 60],
            index[begin : begin + 60],
            days[begin : begin + 60],
            entry["close"],
            conditions["stop"],
        )
    return result


def settle(
    root: Path, request_budget: int, client: Any, *, now: datetime | None = None
) -> dict[str, int]:
    now = now or datetime.now(SHANGHAI)
    cutoff = closed_cutoff(now)
    with ledger_root(root) as events:
        cache = RequestCache(root, request_budget, client)
        done = {event["as_of"] for event in events if event["type"] == "settle"}
        pending = [
            event for event in events if event["type"] == "scan" and event["as_of"] not in done
        ]
        completed = 0
        if pending and min(event["as_of"] for event in pending) < cutoff:
            start = min(event["as_of"] for event in pending)
            calendar = cached_calendar(cache, start, cutoff)
            for event in pending:
                future = [day for day in calendar if day > event["as_of"]]
                if len(future) < 65:
                    continue
                days = future[:65]
                daily = pd.concat(
                    [daily_table(cache, day, allow_empty=True) for day in days], ignore_index=True
                )
                grouped = {code: frame for code, frame in daily.groupby("ts_code")}
                benchmark = benchmark_table(cache, days[0], days[-1], allow_empty=True)
                results = {
                    group: [
                        settle_sample(
                            sample,
                            grouped.get(sample["code"], daily.iloc[:0]),
                            benchmark,
                            days,
                            observation=group == "observations",
                        )
                        for sample in event[group]
                    ]
                    for group in ("observations", "baseline")
                }
                append_event(
                    root,
                    {
                        "type": "settle",
                        "as_of": event["as_of"],
                        "through": days[-1],
                        "recorded_at": now.isoformat(),
                        "sessions": days,
                        "requests": cache.used,
                        **results,
                    },
                )
                completed += 1
        LOG.info("结算完成：%d 个扫描日，请求 %d", completed, cache.used)
        return {"settled": completed, "requests": cache.used}


def promotion_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    scans = sorted(
        [e for e in events if e["type"] == "scan" and e["prospective"]], key=lambda e: e["as_of"]
    )
    settlements = {e["as_of"]: e for e in events if e["type"] == "settle"}
    last: dict[str, str] = {}
    observations: list[tuple[str, dict[str, Any] | None]] = []
    baseline: list[float] = []
    for event in scans:
        day = event["as_of"]
        result = settlements.get(day, {})
        by_code = {row["code"]: row for row in result.get("observations", [])}
        for sample in event["observations"]:
            code = sample["code"]
            recent = [d for d in event["sessions"] if last.get(code, "") < d <= day]
            if code in last and len(recent) < 10:
                continue
            last[code] = day
            observations.append((day[:6], by_code.get(code)))
        baseline.extend(
            row["metrics"]["mae20"]
            for row in result.get("baseline", [])
            if row["metrics"]["mae20"] is not None
        )
    months: dict[str, list[float]] = {}
    maes = []
    settled_count = 0
    for month, row in observations:
        if row is None:
            continue
        settled_count += 1
        value = row["metrics"]["excess"]["20"]
        if value is not None:
            months.setdefault(month, []).append(value)
        if row["metrics"]["mae20"] is not None:
            maes.append(row["metrics"]["mae20"])
    values = [v for group in months.values() for v in group]
    mean = float(np.mean(values)) if values else None
    ci = None
    if len(months) >= 2 and mean is not None:
        # Cluster-robust standard error of the observation-weighted mean (month clusters).
        g, n = len(months), len(values)
        se = (
            math.sqrt(
                g / (g - 1) * sum(sum(v - mean for v in group) ** 2 for group in months.values())
            )
            / n
        )
        # 90% two-sided interval: its lower bound > 0 equals a one-sided 95% test.
        ci = [mean - 1.645 * se, mean + 1.645 * se]
    span = 0
    if scans:
        first, end = (datetime.strptime(e["as_of"], "%Y%m%d") for e in (scans[0], scans[-1]))
        span = (end.year - first.year) * 12 + end.month - first.month - (end.day < first.day)
    positive = sum(np.mean(v) > 0 for v in months.values()) / len(months) if months else None
    mae = float(np.median(maes)) if maes else None
    baseline_mae = float(np.median(baseline)) if baseline else None
    # Data sufficiency is a precondition, not a gate: short or thin history cannot be judged.
    sufficient = span >= 3 and len(values) >= 150
    if not sufficient or ci is None:
        status = "insufficient"
    else:
        status = "pass" if ci[0] > 0 else "fail"
    return {
        "rule": events[0]["rule"],
        "rule_hash": events[0]["rule_hash"],
        "status": status,
        "forward_months": span,
        "scan_days": len(scans),
        "observations_deduplicated": len(observations),
        "observations_settled": settled_count,
        "excess20_count": len(values),
        "excess20_mean": mean,
        "excess20_ci95": ci,
        "positive_month_fraction": positive,
        "monthly": {
            month: {"count": len(v), "excess20_mean": float(np.mean(v))}
            for month, v in months.items()
        },
        "mae20_median": mae,
        "mae20_count": len(maes),
        "baseline_mae20_median": baseline_mae,
        "baseline_mae20_count": len(baseline),
        "limits": "单位为百分比；正态近似90%双侧区间，月聚类标准误。未结算及缺失值不填零；历史回填不计晋升。通过仅支持进一步讨论，不自动进入首页，不作为收益证明。",
    }


def report(root: Path) -> dict[str, Any]:
    with ledger_root(root) as events:
        return promotion_report(events)


def main() -> int:
    parser = argparse.ArgumentParser(description="离线重放验证；真实拉取须逐次授权，示例不构成授权")
    sub = parser.add_subparsers(dest="command", required=True)
    pull = sub.add_parser("fetch", help="显式预算拉取，已校验的缓存跳过")
    pull.add_argument("--root", type=Path, required=True)
    pull.add_argument("--start", type=compact_date, required=True)
    pull.add_argument("--end", type=compact_date, required=True)
    pull.add_argument("--request-budget", type=int, required=True)
    offline = sub.add_parser("run", help="完全离线重放并输出 JSON")
    offline.add_argument("--root", type=Path, required=True)
    offline.add_argument("--horizon", type=int, default=60)
    offline.add_argument("--out", type=Path, required=True)
    for command in ("scan", "settle", "report"):
        child = sub.add_parser(
            command,
            help={
                "scan": "逐次授权的前瞻扫描",
                "settle": "逐次授权的到期结算",
                "report": "离线晋升门槛报告",
            }[command],
        )
        child.add_argument("--root", type=Path, required=True)
        if command == "report":
            child.add_argument("--out", type=Path, required=True)
        else:
            child.add_argument("--request-budget", type=int, required=True)
        if command == "scan":
            child.add_argument("--as-of", type=compact_date, required=True)
    args = parser.parse_args()
    if args.command in {"fetch", "scan", "settle"}:
        token = os.getenv("TUSHARE_TOKEN")
        if not token:
            parser.error("须显式提供环境变量 TUSHARE_TOKEN")
        import tushare as ts  # type: ignore[import-untyped]

        client = ts.pro_api(token, timeout=30)
        if args.command == "fetch":
            fetch(args.root, args.start, args.end, args.request_budget, client)
        elif args.command == "scan":
            scan(args.root, args.as_of, args.request_budget, client)
        else:
            settle(args.root, args.request_budget, client)
    else:
        isolated_path(args.out)
        if args.out.exists():
            raise ScreenError("输出文件已存在，请使用新文件名")
        if args.root == args.out or args.root in args.out.parents:
            raise ScreenError("输出文件须与原始缓存分离")
        save(
            args.out,
            report(args.root) if args.command == "report" else run(args.root, args.horizon),
            immutable=True,
        )
        LOG.info("离线结果已写入 %s", args.out)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.umask(0o077)
    try:
        raise SystemExit(main())
    except (BudgetStop, ScreenError, OSError, ValueError, KeyError, TypeError) as exc:
        LOG.error("重放失败（%s）：%s", type(exc).__name__, exc)
        raise SystemExit(1) from None
