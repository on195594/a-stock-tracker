"""Explicit bounded acquisition and offline replay; never used by the Web."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import stat
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from a_stock_tracker.automation import read_state, save, save_state
from a_stock_tracker.opportunities import RULE, bars, price_conditions
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


def fetch(root: Path, start: str, end: str, request_budget: int, client: Any) -> int:
    """Each invocation has its own hard request cap, including up to three attempts."""
    compact_date(start)
    compact_date(end)
    if start > end or request_budget < 1:
        raise ValueError("日期范围或请求预算无效")
    used = 0
    manifest = {"owner": OWNER, "start": start, "end": end}
    with replay_root(root, create=True):
        marker = root / "replay.json"
        if marker.exists():
            if read_state(marker) != manifest:
                raise ScreenError("缓存归属或日期范围不符；请使用新目录")
        else:
            save_state(marker, manifest)

        def table(filename: str, endpoint: str, fields: str, **params: Any) -> pd.DataFrame:
            nonlocal used
            path = root / filename
            if path.exists():
                return read_table(path)
            for attempt in range(3):
                if used >= request_budget:
                    raise BudgetStop("请求预算已耗尽；已完成缓存可在另行授权后续拉")
                used += 1
                time.sleep(0.5)
                try:
                    frame = getattr(client, endpoint)(fields=fields, **params)
                    break
                except Exception as exc:
                    LOG.warning(
                        "%s 请求失败（%s），已用请求 %d", endpoint, type(exc).__name__, used
                    )
                    if attempt == 2:
                        raise ScreenError(f"{endpoint} 三次请求失败") from None
            if not set(fields.split(",")).issubset(frame.columns):
                raise ScreenError(f"{endpoint} 返回字段不完整")
            if frame.empty and not (endpoint == "stock_basic" and params["list_status"] != "L"):
                raise ScreenError(f"{endpoint} 返回空数据，未保存为成功缓存")
            if len(frame) >= 6000:
                raise ScreenError(f"{endpoint} 疑似达到接口截断上限")
            value = json.loads(
                frame.to_json(orient="split", force_ascii=False, double_precision=15)
            )
            value.pop("index")
            save_state(
                path, {**value, "endpoint": endpoint, "params": params, "fetched_at": now_iso()}
            )
            return frame

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
    LOG.info("拉取完成：请求 %d 次，交易日 %d 个", used, len(days))
    return used


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
    args = parser.parse_args()
    if args.command == "fetch":
        token = os.getenv("TUSHARE_TOKEN")
        if not token:
            parser.error("须显式提供环境变量 TUSHARE_TOKEN")
        import tushare as ts  # type: ignore[import-untyped]

        fetch(args.root, args.start, args.end, args.request_budget, ts.pro_api(token, timeout=30))
    else:
        isolated_path(args.out)
        if args.out.exists():
            raise ScreenError("输出文件已存在，请使用新文件名")
        if args.root == args.out or args.root in args.out.parents:
            raise ScreenError("输出文件须与原始缓存分离")
        save(args.out, run(args.root, args.horizon), immutable=True)
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
