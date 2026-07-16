#!/usr/bin/env python3
"""Read-only offline L3 v2 backtest.

This script intentionally avoids importing production pipeline, Telegram,
Gemini, or Google Sheets modules. It reads tracker.db in SQLite read-only mode
and writes only explicit file artifacts outside --dry-run.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.l3_v2 import (  # noqa: E402,F401 - compatibility re-exports
    FREEFALL_THRESHOLD,
    OVERSOLD_UPPER_THRESHOLD,
    OVERSOLD_VOLUME_RATIO,
    V2_OVERSOLD_VERSION,
    V2_VERSION,
    DataContractState,
    DailyBar,
    PricePanel,
    SignalResult,
    compute_l3_v2_candidate,
    compute_l3_v2_oversold,
)
from lib.l3_v2_qfq_cache import (  # noqa: E402
    CacheInvalidError,
    CacheMissingError,
    FixedIntervalRateLimiter,
    build_cache_frame,
    derive_qfq_ohlcv,
    read_cache,
)


DEFAULT_REPORT = Path("docs/reviews/2026-07-08-l3-v2-backtest-report.md")
DEFAULT_ARTIFACTS_DIR = Path("docs/reviews/l3-v2-backtest-artifacts")
MARKET_SYMBOL = "000300"
MIN_PRELOAD_TRADING_DAYS = 120


@dataclass(frozen=True)
class BacktestConfig:
    db_path: Path
    start: date
    end: date
    preload_trading_days: int
    output: Path | None
    artifacts_dir: Path | None
    dry_run: bool
    codes: tuple[str, ...] | None
    qfq_cache_dir: Path | None
    no_external_fetch: bool
    allow_tushare_fetch: bool
    buy_strong_threshold: float
    buy_strong_source: str
    in_sample_start: date
    in_sample_end: date
    out_of_sample_start: date
    commission_bps: float
    slippage_bps: float
    stamp_tax_bps: float
    algorithm: str = "no-tech-gate"


@dataclass(frozen=True)
class Prediction:
    code: str
    name: str | None
    score_date: date
    total_score: float | None
    outcome_30d: float | None
    benchmark_30d: float | None
    entry_signal: int | None
    entry_signal_status: str | None
    entry_signal_reason: str | None


@dataclass(frozen=True)
class MarketState:
    state: str
    reason: str | None
    close: float | None
    ma20: float | None
    ma20_5d_ago: float | None


@dataclass
class BacktestResult:
    config: BacktestConfig
    preload_start: date
    readonly_ok: bool
    predictions_count: int
    buy_strong_count: int
    stock_count: int
    signals: list[dict[str, Any]]
    raw_events: list[dict[str, Any]]
    dedup_events: list[dict[str, Any]]
    metrics: dict[str, Any]
    decision: str
    decision_reasons: list[str]
    coverage: dict[str, Any]


def parse_args() -> BacktestConfig:
    parser = argparse.ArgumentParser(description="Run a read-only offline L3 v2 backtest.")
    parser.add_argument("--db", default="tracker.db", help="Path to tracker.db.")
    parser.add_argument("--start", required=True, type=parse_date, help="Inclusive score_date start, YYYY-MM-DD.")
    parser.add_argument("--end", required=True, type=parse_date, help="Inclusive score_date end, YYYY-MM-DD.")
    parser.add_argument("--output", type=Path, default=None, help="Markdown report path.")
    parser.add_argument("--artifacts-dir", type=Path, default=None, help="Directory for CSV/JSON artifacts.")
    parser.add_argument("--dry-run", action="store_true", help="Print summary only; do not write artifacts/report.")
    parser.add_argument(
        "--codes", nargs="*", default=None, help="Optional prediction code subset; comma or space separated."
    )
    parser.add_argument(
        "--qfq-cache-dir",
        type=Path,
        default=None,
        help="Validated raw qfq cache directory; readable with --no-external-fetch.",
    )
    parser.add_argument("--no-external-fetch", action="store_true", help="Disable external qfq fetch.")
    parser.add_argument(
        "--allow-tushare-fetch", action="store_true", help="Allow Tushare daily + adj_factor qfq fetch."
    )
    parser.add_argument("--preload-trading-days", type=int, default=120, help="Trading days to preload before start.")
    parser.add_argument("--buy-strong-threshold", type=float, default=None, help="Override buy_strong threshold.")
    parser.add_argument("--in-sample-start", type=parse_date, default=parse_date("2025-01-01"))
    parser.add_argument("--in-sample-end", type=parse_date, default=parse_date("2025-12-31"))
    parser.add_argument("--out-of-sample-start", type=parse_date, default=parse_date("2026-01-01"))
    parser.add_argument("--commission-bps", type=float, default=2.5)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--stamp-tax-bps", type=float, default=5.0)
    parser.add_argument(
        "--algorithm",
        default="no-tech-gate",
        choices=["no-tech-gate", "oversold"],
        help="v2 candidate algorithm variant.",
    )
    args = parser.parse_args()

    if args.start > args.end:
        parser.error("--start must be <= --end")
    if args.preload_trading_days < MIN_PRELOAD_TRADING_DAYS:
        parser.error(f"--preload-trading-days must be >= {MIN_PRELOAD_TRADING_DAYS}")
    if args.no_external_fetch and args.allow_tushare_fetch:
        parser.error("--no-external-fetch and --allow-tushare-fetch are mutually exclusive")

    threshold, source = load_buy_strong_threshold(args.buy_strong_threshold)
    return BacktestConfig(
        db_path=Path(args.db),
        start=args.start,
        end=args.end,
        preload_trading_days=args.preload_trading_days,
        output=args.output,
        artifacts_dir=args.artifacts_dir,
        dry_run=args.dry_run,
        codes=parse_codes(args.codes),
        qfq_cache_dir=args.qfq_cache_dir,
        no_external_fetch=args.no_external_fetch,
        allow_tushare_fetch=args.allow_tushare_fetch,
        buy_strong_threshold=threshold,
        buy_strong_source=source,
        in_sample_start=args.in_sample_start,
        in_sample_end=args.in_sample_end,
        out_of_sample_start=args.out_of_sample_start,
        commission_bps=args.commission_bps,
        slippage_bps=args.slippage_bps,
        stamp_tax_bps=args.stamp_tax_bps,
        algorithm=args.algorithm,
    )


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_codes(values: list[str] | None) -> tuple[str, ...] | None:
    if not values:
        return None
    codes: list[str] = []
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if item:
                codes.append(item)
    return tuple(dict.fromkeys(codes)) or None


def load_buy_strong_threshold(override: float | None) -> tuple[float, str]:
    if override is not None:
        return override, "cli"
    weights_path = Path("weights.json")
    try:
        data = json.loads(weights_path.read_text(encoding="utf-8"))
        return float(data["thresholds"]["buy_strong"]), "weights.json:thresholds.buy_strong"
    except Exception:
        return 44.0, "default:44"


def open_readonly_db(path: Path) -> sqlite3.Connection:
    absolute = path.expanduser().resolve()
    uri = f"file:{quote(str(absolute))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    query_only = conn.execute("PRAGMA query_only").fetchone()[0]
    if int(query_only) != 1:
        raise RuntimeError("SQLite query_only could not be enabled")
    return conn


def main() -> int:
    config = parse_args()
    result = run_backtest(config)
    if not config.dry_run:
        if config.artifacts_dir:
            write_artifacts(result, config.artifacts_dir)
        if config.output:
            write_report(result, config.output)
    print_summary(result)
    return 0


def run_backtest(config: BacktestConfig) -> BacktestResult:
    conn = open_readonly_db(config.db_path)
    readonly_ok = int(conn.execute("PRAGMA query_only").fetchone()[0]) == 1
    preload_start = compute_preload_start(conn, config.start, config.preload_trading_days)
    predictions = load_predictions(conn, config)
    codes = tuple(sorted({prediction.code for prediction in predictions}))
    local_panels = load_local_panels(conn, codes, preload_start, config.end)
    index_rows = load_index_rows(conn, config.end)
    trading_dates = tuple(row_date for row_date, _ in index_rows if config.start <= row_date <= config.end)
    qfq_panels: dict[str, PricePanel] = {}
    qfq_contracts: dict[str, DataContractState] = {}

    if config.qfq_cache_dir is not None and codes:
        qfq_panels, qfq_contracts = load_cached_qfq_panels(config.qfq_cache_dir, codes, preload_start, config.end)

    missing_codes = tuple(code for code in codes if code not in qfq_panels)
    if config.allow_tushare_fetch and missing_codes:
        fetched_panels, fetched_contracts = fetch_tushare_qfq_panels(
            missing_codes,
            preload_start,
            config.end,
            limiter_cache_dir=config.qfq_cache_dir or Path("data/qfq_cache"),
        )
        qfq_panels.update(fetched_panels)
        qfq_contracts.update(fetched_contracts)

    signals: list[dict[str, Any]] = []
    for prediction in predictions:
        none_panel = local_panels.get(prediction.code)
        qfq_panel = qfq_panels.get(prediction.code)
        qfq_contract = qfq_contracts.get(prediction.code)
        market_state = compute_market_state(index_rows, prediction.score_date)

        none_bars = tuple(bar for bar in (none_panel.bars if none_panel else ()) if bar.date <= prediction.score_date)
        qfq_bars = tuple(bar for bar in (qfq_panel.bars if qfq_panel else ()) if bar.date <= prediction.score_date)

        v1 = compute_l3_v1_replay(none_bars)
        v2_panel, v2_contract = choose_v2_panel(
            none_panel=none_panel,
            qfq_panel=replace_panel_bars(qfq_panel, qfq_bars) if qfq_panel else None,
            qfq_contract=qfq_contract,
            prediction_date=prediction.score_date,
        )
        if v2_panel and v2_panel.adjusted == "none":
            v2_panel = replace_panel_bars(v2_panel, none_bars)
        if config.algorithm == "oversold":
            v2 = compute_l3_v2_oversold(v2_panel, v2_contract)
        else:
            v2 = compute_l3_v2_candidate(v2_panel, v2_contract)
        signals.append(build_signal_row(config, prediction, v1, v2, v2_panel, v2_contract, market_state, preload_start))

    raw_events = build_raw_events(config, signals)
    dedup_events = build_dedup_events(raw_events, trading_dates)
    metrics = build_metrics(config, signals, raw_events, dedup_events)
    coverage = build_coverage(config, signals, local_panels, qfq_panels, qfq_contracts)
    decision, decision_reasons = decide(config, metrics, coverage, signals)
    return BacktestResult(
        config=config,
        preload_start=preload_start,
        readonly_ok=readonly_ok,
        predictions_count=len(predictions),
        buy_strong_count=sum(1 for row in signals if row["is_buy_strong_candidate"]),
        stock_count=len(codes),
        signals=signals,
        raw_events=raw_events,
        dedup_events=dedup_events,
        metrics=metrics,
        decision=decision,
        decision_reasons=decision_reasons,
        coverage=coverage,
    )


def compute_preload_start(conn: sqlite3.Connection, start: date, preload_days: int) -> date:
    rows = conn.execute(
        """SELECT DISTINCT trade_date
           FROM daily_bars
           WHERE adjusted='none' AND trade_date <= ?
           ORDER BY trade_date DESC
           LIMIT ?""",
        (start.isoformat(), preload_days + 1),
    ).fetchall()
    if not rows:
        return start
    return parse_date(rows[-1]["trade_date"])


def load_predictions(conn: sqlite3.Connection, config: BacktestConfig) -> list[Prediction]:
    sql = """SELECT code, name, score_date, total_score, outcome_30d, benchmark_30d,
                    entry_signal, entry_signal_status, entry_signal_reason
             FROM predictions
             WHERE framework='A' AND score_date BETWEEN ? AND ?"""
    params: list[Any] = [config.start.isoformat(), config.end.isoformat()]
    if config.codes:
        placeholders = ",".join("?" for _ in config.codes)
        sql += f" AND code IN ({placeholders})"
        params.extend(config.codes)
    sql += " ORDER BY score_date, code"
    rows = conn.execute(sql, params).fetchall()
    return [
        Prediction(
            code=row["code"],
            name=row["name"],
            score_date=parse_date(row["score_date"]),
            total_score=optional_float(row["total_score"]),
            outcome_30d=optional_float(row["outcome_30d"]),
            benchmark_30d=optional_float(row["benchmark_30d"]),
            entry_signal=row["entry_signal"],
            entry_signal_status=row["entry_signal_status"],
            entry_signal_reason=row["entry_signal_reason"],
        )
        for row in rows
    ]


def load_local_panels(
    conn: sqlite3.Connection,
    codes: tuple[str, ...],
    preload_start: date,
    end: date,
) -> dict[str, PricePanel]:
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    rows = conn.execute(
        f"""SELECT code, trade_date, open, high, low, close, volume, source, adjusted,
                   volume_unit, fetched_at, quality_status
            FROM daily_bars
            WHERE adjusted='none'
              AND trade_date BETWEEN ? AND ?
              AND code IN ({placeholders})
            ORDER BY code, trade_date""",
        [preload_start.isoformat(), end.isoformat(), *codes],
    ).fetchall()
    grouped: dict[str, list[DailyBar]] = defaultdict(list)
    for row in rows:
        grouped[row["code"]].append(
            DailyBar(
                date=parse_date(row["trade_date"]),
                open=optional_float(row["open"]),
                high=optional_float(row["high"]),
                low=optional_float(row["low"]),
                close=float(row["close"]),
                volume=optional_float(row["volume"]),
                source=row["source"],
                adjusted=row["adjusted"] or "none",
                volume_unit=row["volume_unit"] or "unknown",
                fetched_at=row["fetched_at"],
                quality_status=row["quality_status"],
            )
        )
    panels: dict[str, PricePanel] = {}
    for code, bars in grouped.items():
        source = single_or_mixed([bar.source for bar in bars])
        volume_unit = single_or_mixed([bar.volume_unit for bar in bars])
        stale_reason = None
        limitation = None
        if any(bar.quality_status not in (None, "ok") for bar in bars):
            limitation = "QUALITY_STATUS_NOT_OK"
        panels[code] = PricePanel(
            code=code,
            adjusted="none",
            bars=tuple(bars),
            source=source,
            volume_unit=volume_unit,
            preload_start=preload_start,
            stale_reason=stale_reason,
            limitation=limitation,
        )
    return panels


def load_index_rows(conn: sqlite3.Connection, end: date) -> tuple[tuple[date, float], ...]:
    rows = conn.execute(
        """SELECT date, close
           FROM index_prices
           WHERE symbol=? AND date <= ?
           ORDER BY date""",
        (MARKET_SYMBOL, end.isoformat()),
    ).fetchall()
    return tuple((parse_date(row["date"]), float(row["close"])) for row in rows)


def load_cached_qfq_panels(
    cache_dir: Path,
    codes: tuple[str, ...],
    preload_start: date,
    end: date,
) -> tuple[dict[str, PricePanel], dict[str, DataContractState]]:
    panels: dict[str, PricePanel] = {}
    contracts: dict[str, DataContractState] = {}
    for code in codes:
        try:
            raw, _metadata = read_cache(cache_dir, code, preload_start, end)
            derived = derive_qfq_ohlcv(raw)
            bars = tuple(
                DailyBar(
                    date=datetime.strptime(str(row["trade_date"]), "%Y%m%d").date(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["vol"]),
                    source=str(row["source"]),
                    adjusted="qfq",
                    volume_unit="hand",
                    fetched_at=str(row["fetched_at"]),
                    quality_status="ok",
                )
                for _, row in derived.iterrows()
            )
        except CacheMissingError:
            contracts[code] = qfq_failure_contract("QFQ_CACHE_MISSING")
            continue
        except CacheInvalidError as exc:
            contracts[code] = qfq_failure_contract(f"QFQ_CACHE_INVALID:{exc.reason}")
            continue
        panels[code] = PricePanel(
            code=code,
            adjusted="qfq",
            bars=bars,
            source="tushare.daily+adj_factor",
            volume_unit="hand",
            preload_start=preload_start,
            stale_reason=None,
            limitation=None,
        )
        contracts[code] = DataContractState(
            adjusted="qfq",
            source="tushare.daily+adj_factor",
            volume_unit="hand",
            is_stale=False,
            stale_reason=None,
            unavailable_reason=None,
            alignment_reason=None,
        )
    return panels, contracts


def qfq_failure_contract(reason: str) -> DataContractState:
    return DataContractState(
        adjusted="qfq",
        source="tushare.daily+adj_factor",
        volume_unit="hand",
        is_stale=False,
        stale_reason=None,
        unavailable_reason=reason,
        alignment_reason="QFQ_UNAVAILABLE",
    )


def fetch_tushare_qfq_panels(
    codes: tuple[str, ...],
    preload_start: date,
    end: date,
    limiter_cache_dir: Path = Path("data/qfq_cache"),
) -> tuple[dict[str, PricePanel], dict[str, DataContractState]]:
    token = get_tushare_token()
    panels: dict[str, PricePanel] = {}
    contracts: dict[str, DataContractState] = {}
    if not token:
        for code in codes:
            contracts[code] = DataContractState(
                adjusted="qfq",
                source="tushare.daily+adj_factor",
                volume_unit="hand",
                is_stale=False,
                stale_reason=None,
                unavailable_reason="TUSHARE_TOKEN_MISSING",
                alignment_reason="QFQ_UNAVAILABLE",
            )
        return panels, contracts

    try:
        import tushare as ts  # type: ignore[import-not-found]
    except Exception as exc:
        for code in codes:
            contracts[code] = DataContractState(
                adjusted="qfq",
                source="tushare.daily+adj_factor",
                volume_unit="hand",
                is_stale=False,
                stale_reason=None,
                unavailable_reason=f"TUSHARE_IMPORT_FAILED:{exc}",
                alignment_reason="QFQ_UNAVAILABLE",
            )
        return panels, contracts

    pro = ts.pro_api(token)
    limiter = FixedIntervalRateLimiter(limiter_cache_dir)
    start_s = preload_start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")
    for code in codes:
        ts_code = to_tushare_code(code)
        try:
            daily = pro.daily(ts_code=ts_code, start_date=start_s, end_date=end_s)
            limiter.acquire()
            factors = pro.adj_factor(ts_code=ts_code, start_date=start_s, end_date=end_s)
        except Exception as exc:
            reason = classify_tushare_failure(exc)
            contracts[code] = DataContractState(
                adjusted="qfq",
                source="tushare.daily+adj_factor",
                volume_unit="hand",
                is_stale=False,
                stale_reason=None,
                unavailable_reason=reason,
                alignment_reason="QFQ_UNAVAILABLE",
            )
            continue

        bars, reason = derive_qfq_bars_from_tushare(code, daily, factors, preload_start)
        if reason:
            contracts[code] = DataContractState(
                adjusted="qfq",
                source="tushare.daily+adj_factor",
                volume_unit="hand",
                is_stale=False,
                stale_reason=None,
                unavailable_reason=reason,
                alignment_reason="QFQ_ALIGNMENT_FAILED",
            )
            continue
        panels[code] = PricePanel(
            code=code,
            adjusted="qfq",
            bars=tuple(bars),
            source="tushare.daily+adj_factor",
            volume_unit="hand",
            preload_start=preload_start,
            stale_reason=None,
            limitation=None,
        )
        contracts[code] = DataContractState(
            adjusted="qfq",
            source="tushare.daily+adj_factor",
            volume_unit="hand",
            is_stale=False,
            stale_reason=None,
            unavailable_reason=None,
            alignment_reason=None,
        )
    return panels, contracts


def get_tushare_token() -> str | None:
    token = os.environ.get("TUSHARE_TOKEN")
    if token:
        return token
    env_path = Path(".env")
    if not env_path.exists():
        return None
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "TUSHARE_TOKEN":
                return value.strip().strip('"').strip("'") or None
    except OSError:
        return None
    return None


def classify_tushare_failure(exc: Exception) -> str:
    message = str(exc).replace("\n", " ").strip()
    if "频率超限" in message or "rate limit" in message.lower():
        return f"TUSHARE_RATE_LIMIT:{message}"
    return f"TUSHARE_FETCH_FAILED:{message}"


def to_tushare_code(code: str) -> str:
    suffix = "SH" if code.startswith(("5", "6", "9")) else "SZ"
    return f"{code}.{suffix}"


def derive_qfq_bars_from_tushare(
    code: str, daily: Any, factors: Any, preload_start: date
) -> tuple[list[DailyBar], str | None]:
    try:
        raw = build_cache_frame(code, daily, factors)
        derived = derive_qfq_ohlcv(raw)
    except CacheInvalidError as exc:
        return [], f"QFQ_ALIGNMENT_FAILED:{exc.reason}"
    bars = [
        DailyBar(
            date=datetime.strptime(str(row["trade_date"]), "%Y%m%d").date(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["vol"]),
            source="tushare.daily+adj_factor",
            adjusted="qfq",
            volume_unit="hand",
            fetched_at=str(row["fetched_at"]),
            quality_status="ok",
        )
        for _, row in derived.iterrows()
    ]
    return bars, None


def compute_l3_v1_replay(bars: tuple[DailyBar, ...]) -> SignalResult:
    if len(bars) < 120:
        return SignalResult(None, "v1-replay", "unavailable", "INSUFFICIENT_WINDOW", empty_metrics())
    if any(bar.volume is None for bar in bars[-20:]):
        return SignalResult(None, "v1-replay", "unavailable", "MISSING_VOLUME", empty_metrics())
    closes = [bar.close for bar in bars]
    volumes = [float(bar.volume) for bar in bars]
    latest_close = closes[-1]
    ma60 = mean(closes[-60:])
    ma120 = mean(closes[-120:])
    vol5 = mean(volumes[-5:])
    vol20 = mean(volumes[-20:])
    metrics = {
        "close": latest_close,
        "ma60": ma60,
        "ma120": ma120,
        "ma60_5d_ago": None,
        "vol5": vol5,
        "vol20": vol20,
        "high_20d_prev": None,
    }
    if latest_close <= ma60:
        return SignalResult(0, "v1-replay", "reject", "BELOW_MA60", metrics)
    if latest_close <= ma120:
        return SignalResult(0, "v1-replay", "reject", "BELOW_MA120", metrics)
    if vol5 <= vol20:
        return SignalResult(0, "v1-replay", "reject", "LOW_VOLUME", metrics)
    return SignalResult(1, "v1-replay", "pass", "PASS", metrics)


def compute_market_state(index_rows: tuple[tuple[date, float], ...], score_date: date) -> MarketState:
    rows = [(row_date, close) for row_date, close in index_rows if row_date <= score_date]
    if len(rows) < 25:
        return MarketState("market_unknown", "MARKET_WINDOW_INSUFFICIENT", None, None, None)
    latest_date, latest_close = rows[-1]
    if latest_date != score_date:
        return MarketState("market_unknown", "MARKET_STALE", latest_close, None, None)
    closes = [close for _, close in rows]
    ma20 = mean(closes[-20:])
    ma20_5d_ago = mean(closes[-25:-5])
    if latest_close > ma20 and ma20 >= ma20_5d_ago:
        return MarketState("market_bullish", None, latest_close, ma20, ma20_5d_ago)
    return MarketState("market_weak", None, latest_close, ma20, ma20_5d_ago)


def choose_v2_panel(
    none_panel: PricePanel | None,
    qfq_panel: PricePanel | None,
    qfq_contract: DataContractState | None,
    prediction_date: date,
) -> tuple[PricePanel | None, DataContractState]:
    if qfq_panel is not None and qfq_contract is not None and not qfq_contract.unavailable_reason:
        return qfq_panel, qfq_contract
    if none_panel is not None:
        return none_panel, DataContractState(
            adjusted="none",
            source=none_panel.source,
            volume_unit=none_panel.volume_unit,
            is_stale=False,
            stale_reason=None,
            unavailable_reason=qfq_contract.unavailable_reason if qfq_contract else "QFQ_UNAVAILABLE",
            alignment_reason=qfq_contract.alignment_reason if qfq_contract else "QFQ_UNAVAILABLE",
        )
    return None, DataContractState(
        adjusted="none",
        source="none",
        volume_unit="unknown",
        is_stale=False,
        stale_reason=None,
        unavailable_reason=f"PRICE_PANEL_UNAVAILABLE:{prediction_date.isoformat()}",
        alignment_reason=qfq_contract.alignment_reason if qfq_contract else "QFQ_UNAVAILABLE",
    )


def replace_panel_bars(panel: PricePanel | None, bars: tuple[DailyBar, ...]) -> PricePanel | None:
    if panel is None:
        return None
    return PricePanel(
        code=panel.code,
        adjusted=panel.adjusted,
        bars=bars,
        source=panel.source,
        volume_unit=panel.volume_unit,
        preload_start=panel.preload_start,
        stale_reason=panel.stale_reason,
        limitation=panel.limitation,
    )


def build_signal_row(
    config: BacktestConfig,
    prediction: Prediction,
    v1: SignalResult,
    v2: SignalResult,
    panel: PricePanel | None,
    contract: DataContractState,
    market: MarketState,
    preload_start: date,
) -> dict[str, Any]:
    alpha = None
    net_alpha = None
    if prediction.outcome_30d is not None and prediction.benchmark_30d is not None:
        alpha = prediction.outcome_30d - prediction.benchmark_30d
        net_alpha = alpha - round_trip_cost_bps(config) / 100.0
    return {
        "score_date": prediction.score_date.isoformat(),
        "code": prediction.code,
        "name": prediction.name,
        "total_score": prediction.total_score,
        "is_buy_strong_candidate": is_buy_strong(prediction.total_score, config.buy_strong_threshold),
        "buy_strong_threshold": config.buy_strong_threshold,
        "outcome_30d": prediction.outcome_30d,
        "benchmark_30d": prediction.benchmark_30d,
        "alpha_30d": alpha,
        "net_alpha_30d": net_alpha,
        "production_entry_signal": prediction.entry_signal,
        "production_entry_signal_status": prediction.entry_signal_status,
        "production_entry_signal_reason": prediction.entry_signal_reason,
        "v1_signal": v1.signal,
        "v1_status": v1.status,
        "v1_reason": v1.reason,
        "v2_signal": v2.signal,
        "v2_status": v2.status,
        "v2_reason": v2.reason,
        "v2_adjusted": contract.adjusted,
        "source": contract.source,
        "volume_unit": contract.volume_unit,
        "stale_reason": contract.stale_reason,
        "unavailable_reason": contract.unavailable_reason,
        "preload_start": preload_start.isoformat(),
        "alignment_reason": contract.alignment_reason,
        "close": v2.metrics.get("close"),
        "ma60": v2.metrics.get("ma60"),
        "ma120": v2.metrics.get("ma120"),
        "ma60_5d_ago": v2.metrics.get("ma60_5d_ago"),
        "vol5": v2.metrics.get("vol5"),
        "vol20": v2.metrics.get("vol20"),
        "high_20d_prev": v2.metrics.get("high_20d_prev"),
        "market_state": market.state,
        "market_reason": market.reason,
        "market_close": market.close,
        "market_ma20": market.ma20,
        "market_ma20_5d_ago": market.ma20_5d_ago,
        "panel_bar_count": len(panel.bars) if panel else 0,
    }


def build_raw_events(config: BacktestConfig, signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in signals:
        base = {
            "score_date": row["score_date"],
            "code": row["code"],
            "name": row["name"],
            "total_score": row["total_score"],
            "is_buy_strong_candidate": row["is_buy_strong_candidate"],
            "outcome_30d": row["outcome_30d"],
            "benchmark_30d": row["benchmark_30d"],
            "alpha_30d": row["alpha_30d"],
            "net_alpha_30d": row["net_alpha_30d"],
            "round_trip_cost_bps": round_trip_cost_bps(config),
        }
        events.append({**base, "strategy": "v1", "state": row["v1_status"], "reason": row["v1_reason"]})
        events.append({**base, "strategy": "v2", "state": row["v2_status"], "reason": row["v2_reason"]})
    return events


def build_dedup_events(raw_events: list[dict[str, Any]], trading_dates: tuple[date, ...]) -> list[dict[str, Any]]:
    eligible = [event for event in raw_events if is_pass_event(event)]
    eligible.sort(key=lambda event: (event["strategy"], event["code"], event["score_date"], event["state"]))
    last_counted: dict[tuple[str, str], date] = {}
    counted: list[dict[str, Any]] = []
    for event in eligible:
        key = (event["strategy"], event["code"])
        event_date = parse_date(event["score_date"])
        previous = last_counted.get(key)
        if previous is not None and trading_day_distance(previous, event_date, trading_dates) <= 20:
            continue
        last_counted[key] = event_date
        counted.append(event)
    counted.sort(key=lambda event: (event["score_date"], event["strategy"], event["code"], event["state"]))
    return counted


def is_pass_event(event: dict[str, Any]) -> bool:
    if event["strategy"] == "v1":
        return event["state"] == "pass"
    return event["state"] in {"pass_strong", "pass_weak"}


def trading_day_distance(previous: date, current: date, trading_dates: tuple[date, ...]) -> int:
    date_index = {trade_date: index for index, trade_date in enumerate(trading_dates)}
    if previous in date_index and current in date_index:
        return date_index[current] - date_index[previous]
    return (current - previous).days


def build_metrics(
    config: BacktestConfig,
    signals: list[dict[str, Any]],
    raw_events: list[dict[str, Any]],
    dedup_events: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "costs": {
            "commission_bps": config.commission_bps,
            "slippage_bps": config.slippage_bps,
            "stamp_tax_bps": config.stamp_tax_bps,
            "round_trip_cost_bps": round_trip_cost_bps(config),
        },
        "raw_daily": {},
        "dedup_20d": {},
        "is_oos": {},
        "signal_distribution": {
            "all": distribution(signals, only_buy_strong=False),
            "buy_strong": distribution(signals, only_buy_strong=True),
        },
    }
    for scope_name, only_buy_strong in (("all", False), ("buy_strong", True)):
        metrics["raw_daily"][scope_name] = grouped_event_metrics(raw_events, only_buy_strong=only_buy_strong)
        metrics["dedup_20d"][scope_name] = grouped_event_metrics(dedup_events, only_buy_strong=only_buy_strong)
    for bucket, predicate in (
        ("in_sample", lambda row: config.in_sample_start <= parse_date(row["score_date"]) <= config.in_sample_end),
        ("out_of_sample", lambda row: parse_date(row["score_date"]) >= config.out_of_sample_start),
    ):
        metrics["is_oos"][bucket] = {
            "raw_buy_strong": grouped_event_metrics(
                [event for event in raw_events if predicate(event)], only_buy_strong=True
            ),
            "dedup_buy_strong": grouped_event_metrics(
                [event for event in dedup_events if predicate(event)], only_buy_strong=True
            ),
        }
    return metrics


def grouped_event_metrics(events: list[dict[str, Any]], only_buy_strong: bool) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if only_buy_strong and not event["is_buy_strong_candidate"]:
            continue
        grouped[f"{event['strategy']}:{event['state']}"].append(event)
    return {key: summarize_events(value) for key, value in sorted(grouped.items())}


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [event for event in events if event["outcome_30d"] is not None and event["benchmark_30d"] is not None]
    alphas = [float(event["alpha_30d"]) for event in settled]
    net_alphas = [float(event["net_alpha_30d"]) for event in settled]
    returns = [float(event["outcome_30d"]) for event in settled]
    benchmarks = [float(event["benchmark_30d"]) for event in settled]
    return {
        "n": len(events),
        "settled_n": len(settled),
        "unsettled_n": len(events) - len(settled),
        "avg_return_30d": safe_mean(returns),
        "avg_benchmark_30d": safe_mean(benchmarks),
        "avg_alpha_30d": safe_mean(alphas),
        "avg_net_alpha_30d": safe_mean(net_alphas),
        "hit_rate": safe_hit_rate(alphas),
        "median_alpha_30d": safe_median(alphas),
        "worst_trade_alpha_30d": min(alphas) if alphas else None,
        "max_drawdown_proxy": max_drawdown_proxy(net_alphas),
        "sharpe_proxy": sharpe_proxy(net_alphas),
    }


def distribution(signals: list[dict[str, Any]], only_buy_strong: bool) -> dict[str, dict[str, int]]:
    rows = [row for row in signals if not only_buy_strong or row["is_buy_strong_candidate"]]
    return {
        "v1": dict(Counter(row["v1_status"] for row in rows)),
        "v2": dict(Counter(row["v2_status"] for row in rows)),
    }


def build_coverage(
    config: BacktestConfig,
    signals: list[dict[str, Any]],
    local_panels: dict[str, PricePanel],
    qfq_panels: dict[str, PricePanel],
    qfq_contracts: dict[str, DataContractState],
) -> dict[str, Any]:
    local_bar_counts = [len(panel.bars) for panel in local_panels.values()]
    qfq_bar_counts = [len(panel.bars) for panel in qfq_panels.values()]
    qfq_reason_counts = Counter(qfq_coverage_reason(contract) for contract in qfq_contracts.values())
    strong_rows = [row for row in signals if row["is_buy_strong_candidate"]]
    strong_qfq_issue_rows = [
        row
        for row in strong_rows
        if row["v2_adjusted"] != "qfq" or row["alignment_reason"] or row["unavailable_reason"]
    ]
    contract_counts = Counter(
        (row["source"], row["v2_adjusted"], row["volume_unit"], row["alignment_reason"] or "OK") for row in signals
    )
    high_dividend_rows = [
        {
            "code": row["code"],
            "score_date": row["score_date"],
            "v2_adjusted": row["v2_adjusted"],
            "v2_status": row["v2_status"],
            "v2_reason": row["v2_reason"],
            "close": row["close"],
            "ma60": row["ma60"],
            "ma120": row["ma120"],
            "alignment_reason": row["alignment_reason"],
        }
        for row in signals
        if row["code"] in {"600900"}
    ][:20]
    return {
        "local_panel_count": len(local_panels),
        "local_bar_min": min(local_bar_counts) if local_bar_counts else 0,
        "local_bar_max": max(local_bar_counts) if local_bar_counts else 0,
        "qfq_panel_count": len(qfq_panels),
        "qfq_bar_min": min(qfq_bar_counts) if qfq_bar_counts else 0,
        "qfq_bar_max": max(qfq_bar_counts) if qfq_bar_counts else 0,
        "qfq_reason_counts": dict(qfq_reason_counts),
        "buy_strong_qfq_issue_count": len(strong_qfq_issue_rows),
        "contract_counts": {"|".join(map(str, key)): value for key, value in contract_counts.items()},
        "high_dividend_examples": high_dividend_rows,
        "external_fetch_mode": (
            "allow_tushare_fetch"
            if config.allow_tushare_fetch
            else "qfq_file_cache"
            if config.qfq_cache_dir is not None
            else "no_external_fetch"
        ),
    }


def qfq_coverage_reason(contract: DataContractState) -> str:
    reason = contract.unavailable_reason or contract.alignment_reason
    if not reason:
        return "OK"
    return reason.split(":", 1)[0]


def decide(
    config: BacktestConfig,
    metrics: dict[str, Any],
    coverage: dict[str, Any],
    signals: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if config.preload_trading_days < MIN_PRELOAD_TRADING_DAYS:
        return "NEED_SPEC_FIX", ["preload_trading_days below 120"]
    if coverage["qfq_panel_count"] == 0:
        return "NEED_QFQ", ["No qfq panels were available; v2 pass_strong cannot be validated."]
    if coverage["buy_strong_qfq_issue_count"] > 0:
        return "NEED_QFQ", ["qfq unavailable/alignment issue affects buy_strong candidate rows."]
    buy_strong_dist = metrics["signal_distribution"]["buy_strong"]["v2"]
    if buy_strong_dist.get("pass_strong", 0) == 0:
        return "NEED_SPEC_FIX", ["No buy_strong v2 pass_strong samples after qfq filtering."]

    dedup_buy = metrics["dedup_20d"]["buy_strong"]
    v2_strong = dedup_buy.get("v2:pass_strong", {})
    v1_pass = dedup_buy.get("v1:pass", {})
    oos_raw = metrics["is_oos"]["out_of_sample"]["raw_buy_strong"]
    oos_dedup = metrics["is_oos"]["out_of_sample"]["dedup_buy_strong"]
    oos_dedup_v2 = oos_dedup.get("v2:pass_strong", {})
    oos_raw_v2 = oos_raw.get("v2:pass_strong", {})
    oos_raw_v1 = oos_raw.get("v1:pass", {})
    if v2_strong.get("settled_n", 0) < 20:
        reasons.append("v2 pass_strong settled dedup sample is below 20.")
    if oos_dedup_v2.get("n", 0) == 0:
        reasons.append("OOS v2 pass_strong sample is empty.")
    # Alpha gate uses raw OOS (settled_n typically 10x larger than dedup, avoids small-sample noise)
    v2_alpha = oos_raw_v2.get("avg_net_alpha_30d") if oos_raw_v2.get("settled_n", 0) >= 20 else None
    v1_alpha = oos_raw_v1.get("avg_net_alpha_30d") if oos_raw_v1.get("settled_n", 0) >= 20 else None
    v2_hit = v2_strong.get("hit_rate")
    v1_hit = v1_pass.get("hit_rate")
    if v2_alpha is None or v1_alpha is None or v2_alpha <= v1_alpha:
        reasons.append("v2 pass_strong raw OOS net alpha does not exceed v1 pass.")
    if v2_hit is None or v1_hit is None or v2_hit <= v1_hit:
        reasons.append("v2 pass_strong hit rate does not exceed v1 pass.")
    if reasons:
        return "NEED_SPEC_FIX", reasons
    return "GO_TDD", ["qfq coverage and buy_strong pass_strong metrics satisfy initial gate."]


def write_artifacts(result: BacktestResult, artifacts_dir: Path) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    write_csv(artifacts_dir / "signals_daily.csv", result.signals)
    write_csv(artifacts_dir / "events_raw_daily.csv", result.raw_events)
    write_csv(artifacts_dir / "events_dedup_20d.csv", result.dedup_events)
    (artifacts_dir / "metrics_summary.json").write_text(
        json.dumps(result.metrics, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_report(result: BacktestResult, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_report(result), encoding="utf-8")


def render_report(result: BacktestResult) -> str:
    config = result.config
    lines = [
        "# L3 v2 Offline Backtest Report",
        "",
        "## 1. Run metadata",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- DB path: {config.db_path}",
        f"- SQLite query_only: {result.readonly_ok}",
        f"- Score window: {config.start} to {config.end}",
        f"- Preload trading days: {config.preload_trading_days}",
        f"- Effective preload start: {result.preload_start}",
        f"- External fetch mode: {result.coverage['external_fetch_mode']}",
        f"- buy_strong threshold: {config.buy_strong_threshold} ({config.buy_strong_source})",
        f"- IS split: {config.in_sample_start} to {config.in_sample_end}",
        f"- OOS split: {config.out_of_sample_start} to {config.end}",
        f"- Cost bps: commission={config.commission_bps}, slippage={config.slippage_bps}, stamp_tax={config.stamp_tax_bps}, round_trip={round_trip_cost_bps(config)}",
        "",
        "## 2. Data coverage",
        f"- Framework A prediction rows: {result.predictions_count}",
        f"- buy_strong rows: {result.buy_strong_count}",
        f"- Stock count: {result.stock_count}",
        f"- Local none panels: {result.coverage['local_panel_count']} (bars min/max {result.coverage['local_bar_min']}/{result.coverage['local_bar_max']})",
        f"- qfq panels: {result.coverage['qfq_panel_count']} (bars min/max {result.coverage['qfq_bar_min']}/{result.coverage['qfq_bar_max']})",
        f"- qfq reason counts: `{json.dumps(result.coverage['qfq_reason_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- buy_strong qfq issue rows: {result.coverage['buy_strong_qfq_issue_count']}",
        f"- Data contract counts: `{json.dumps(result.coverage['contract_counts'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## 3. Signal distribution",
        "All Framework A sample:",
        render_distribution(result.metrics["signal_distribution"]["all"]),
        "",
        "buy_strong subset:",
        render_distribution(result.metrics["signal_distribution"]["buy_strong"]),
        "",
        "High-dividend qfq vs none examples (600900):",
        render_table(
            result.coverage["high_dividend_examples"],
            [
                "score_date",
                "code",
                "v2_adjusted",
                "v2_status",
                "v2_reason",
                "close",
                "ma60",
                "ma120",
                "alignment_reason",
            ],
        ),
        "",
        "## 4. Raw daily metrics",
        render_metrics_block(result.metrics["raw_daily"]),
        "",
        "## 5. Dedup 20d metrics",
        "This report's dedup hit rate depends on a 20-trading-day same-stock cooldown. Do not use it as a rollout claim unless production push implements an equivalent cooldown.",
        render_metrics_block(result.metrics["dedup_20d"]),
        "",
        "## 6. Cost-adjusted metrics",
        f"- Default round-trip cost: {round_trip_cost_bps(config)} bps, subtracted from alpha as {round_trip_cost_bps(config) / 100.0:.4f} percentage points.",
        render_cost_sensitivity(result),
        "",
        "## 7. IS/OOS diagnostics",
        render_is_oos(result.metrics["is_oos"]),
        "",
        "## 8. Decision gate",
        f"- Decision: **{result.decision}**",
        *[f"- Reason: {reason}" for reason in result.decision_reasons],
        "",
        "Allowed decisions are GO_TDD / NEED_QFQ / NEED_SPEC_FIX / STOP. GO_TDD is not production approval.",
        "",
        "## 9. Known limitations",
        "- v2 thresholds are fixed candidate values; ATR/beta/industry adaptive thresholds are not implemented here.",
        "- MaxDD and Sharpe are proxy metrics over realized event alpha, not portfolio path risk.",
        "- Dedup uses score-date trading rows for each code; future production needs an explicit push cooldown if v2 is promoted.",
        "- With no qfq coverage, pass_strong validation is intentionally blocked and the decision must remain NEED_QFQ.",
        "",
    ]
    return "\n".join(lines)


def render_distribution(distribution_data: dict[str, dict[str, int]]) -> str:
    return "\n".join(
        [
            f"- v1: `{json.dumps(distribution_data.get('v1', {}), ensure_ascii=False, sort_keys=True)}`",
            f"- v2: `{json.dumps(distribution_data.get('v2', {}), ensure_ascii=False, sort_keys=True)}`",
        ]
    )


def render_metrics_block(block: dict[str, Any]) -> str:
    parts: list[str] = []
    for scope, metrics in block.items():
        parts.append(f"### {scope}")
        rows = []
        for key, value in metrics.items():
            rows.append(
                {
                    "state": key,
                    "n": value.get("n"),
                    "settled": value.get("settled_n"),
                    "avg_alpha": fmt(value.get("avg_alpha_30d")),
                    "avg_net_alpha": fmt(value.get("avg_net_alpha_30d")),
                    "hit_rate": fmt(value.get("hit_rate")),
                    "median_alpha": fmt(value.get("median_alpha_30d")),
                    "worst": fmt(value.get("worst_trade_alpha_30d")),
                    "maxdd_proxy": fmt(value.get("max_drawdown_proxy")),
                }
            )
        parts.append(
            render_table(
                rows,
                [
                    "state",
                    "n",
                    "settled",
                    "avg_alpha",
                    "avg_net_alpha",
                    "hit_rate",
                    "median_alpha",
                    "worst",
                    "maxdd_proxy",
                ],
            )
        )
    return "\n".join(parts)


def render_is_oos(block: dict[str, Any]) -> str:
    parts: list[str] = []
    for bucket, data in block.items():
        parts.append(f"### {bucket}")
        for scope, metrics in data.items():
            parts.append(f"{scope}:")
            rows = []
            for key, value in metrics.items():
                rows.append(
                    {
                        "state": key,
                        "n": value.get("n"),
                        "settled": value.get("settled_n"),
                        "avg_net_alpha": fmt(value.get("avg_net_alpha_30d")),
                        "hit_rate": fmt(value.get("hit_rate")),
                    }
                )
            parts.append(render_table(rows, ["state", "n", "settled", "avg_net_alpha", "hit_rate"]))
    return "\n".join(parts)


def render_cost_sensitivity(result: BacktestResult) -> str:
    base = round_trip_cost_bps(result.config)
    rows = []
    raw_buy = result.metrics["raw_daily"]["buy_strong"]
    for delta in (-10.0, 0.0, 10.0):
        cost = max(0.0, base + delta)
        for state in ("v1:pass", "v2:pass_strong", "v2:pass_weak"):
            avg_alpha = raw_buy.get(state, {}).get("avg_alpha_30d")
            rows.append(
                {
                    "state": state,
                    "round_trip_bps": cost,
                    "avg_net_alpha": fmt(avg_alpha - cost / 100.0 if avg_alpha is not None else None),
                }
            )
    return render_table(rows, ["state", "round_trip_bps", "avg_net_alpha"])


def render_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(str(fmt(row.get(column))) for column in columns) + " |")
    return "\n".join([header, separator, *body])


def print_summary(result: BacktestResult) -> None:
    print(f"readonly_ok={result.readonly_ok}")
    print(f"predictions={result.predictions_count} buy_strong={result.buy_strong_count} stocks={result.stock_count}")
    print(f"preload_start={result.preload_start}")
    print(f"qfq_panels={result.coverage['qfq_panel_count']} qfq_reasons={result.coverage['qfq_reason_counts']}")
    print(f"decision={result.decision}")
    for reason in result.decision_reasons:
        print(f"decision_reason={reason}")
    if result.config.dry_run:
        print("dry_run=true; no report/artifacts written")
    else:
        if result.config.output:
            print(f"report={result.config.output}")
        if result.config.artifacts_dir:
            print(f"artifacts_dir={result.config.artifacts_dir}")


def round_trip_cost_bps(config: BacktestConfig) -> float:
    return config.commission_bps * 2.0 + config.slippage_bps * 2.0 + config.stamp_tax_bps


def optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def is_buy_strong(total_score: float | None, threshold: float) -> bool:
    return total_score is not None and total_score >= threshold


def empty_metrics() -> dict[str, float | str | None]:
    return {
        "close": None,
        "ma60": None,
        "ma120": None,
        "ma60_5d_ago": None,
        "vol5": None,
        "vol20": None,
        "high_20d_prev": None,
    }


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def safe_mean(values: list[float]) -> float | None:
    return mean(values) if values else None


def safe_median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def safe_hit_rate(values: list[float]) -> float | None:
    return sum(1 for value in values if value > 0) / len(values) if values else None


def sharpe_proxy(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    stddev = statistics.stdev(values)
    if stddev == 0:
        return None
    return (statistics.mean(values) / stddev) * math.sqrt(len(values))


def max_drawdown_proxy(values: list[float]) -> float | None:
    if not values:
        return None
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        worst = min(worst, cumulative - peak)
    return worst


def single_or_mixed(values: list[str]) -> str:
    unique = sorted({value or "unknown" for value in values})
    if len(unique) == 1:
        return unique[0]
    return "mixed:" + ",".join(unique)


def fmt(value: Any) -> Any:
    if isinstance(value, float):
        return f"{value:.4f}"
    if value is None:
        return ""
    return value


if __name__ == "__main__":
    raise SystemExit(main())
