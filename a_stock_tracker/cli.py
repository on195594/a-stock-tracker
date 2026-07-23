"""
a-stock-tracker 主编排器

子命令：
  init            首次初始化，对 watchlist 每只股票执行 fetch，填充 stock_fundamentals
  daily           每日评分并写入 predictions 表（cron: 工作日 16:30）
  outcome-update  更新到期预测的实际收益（cron: 工作日 17:00）
  accuracy-report 输出 benchmark 相对命中率报告
  framework-b-cohort-freeze  显式冻结每周 Framework B report-only cohort
"""

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
import pandas as pd


@contextmanager
def provider_session(provider):
    if provider is not None and hasattr(provider, "__enter__"):
        with provider as p:
            yield p
    else:
        yield provider


import a_stock_tracker.config as config
from a_stock_tracker.data.cache import (
    get_db,
    get_fundamentals,
    insert_market_data_audit,
    latest_daily_close,
    latest_market_data_audit,
    load_daily_bars,
)
from a_stock_tracker.signals.entry_signal import (
    ENTRY_SIGNAL_VERSION,
    EntrySignalResult,
    REASON_MISSING_VOLUME,
    REASON_MIXED_SOURCE_VOLUME_UNSAFE,
    compute_entry_signal,
)
from a_stock_tracker.signals.l3_v2_pipeline import compute_l3_v2_from_daily_bars, now_isoformat as _l3v2_now
from a_stock_tracker.reporting.accuracy_report import build_accuracy_report
from a_stock_tracker.reporting.framework_b_cohort import freeze_weekly_cohort
from a_stock_tracker.data.market_data import (
    MarketDataCacheService,
    MarketDataCoverage,
    MarketDataProvider,
    SOURCE_STALE,
    get_default_market_data_provider,
    get_market_data_backfill_provider,
)
from a_stock_tracker.integrations.gemini_scorer import get_qualitative_score
from a_stock_tracker.qualitative.production import (
    ProductionQualitativeSelection,
    get_production_qualitative_selection,
)
from a_stock_tracker.scoring import (
    SUPPORTED_FRAMEWORKS,
    InsufficientDataError,
    UnsupportedFrameworkError,
    compute_daily_pb_percentile,
    score_stock,
)
from a_stock_tracker.paths import PROJECT_ROOT

FETCHER_STOCK_TIMEOUT_SECONDS = 420
_OUTCOME_WINDOWS: frozenset[str] = frozenset({"30d", "60d", "90d"})


def _load_dotenv() -> None:
    """从项目根目录 .env 加载环境变量（不覆盖已有变量，无 .env 文件时静默跳过）。"""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    with env_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

assert sqlite3.sqlite_version_info >= (3, 31, 0), (
    f"需要 SQLite ≥ 3.31.0（当前 {sqlite3.sqlite_version}），请升级系统 SQLite"
)

os.makedirs(config.LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(config.LOG_DIR, "pipeline.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────


def _load_weights() -> dict:
    with open(config.WEIGHTS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _compute_weights_hash(weights: dict) -> str:
    return hashlib.md5(json.dumps(weights["frameworks"], sort_keys=True).encode()).hexdigest()[:8]


def _today() -> str:
    return date.today().isoformat()


def _add_days(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def _compute_daily_pb_percentile(price: float, data: dict) -> float | None:
    return compute_daily_pb_percentile(price, data)


def _compute_stock_entry_signal(db: sqlite3.Connection, code: str, today: str) -> EntrySignalResult:
    """从本地 daily_bars 读取 120 日窗口并计算 L3；信号阶段不发网络请求。"""
    rows = load_daily_bars(db, code, today, 120)
    if len(rows) < 120:
        audit = latest_market_data_audit(db, "l3_bars", code, today)
        if audit and audit["status"] == "failed":
            return EntrySignalResult(
                None,
                ENTRY_SIGNAL_VERSION,
                audit["error_code"] or "FETCH_FAILED",
                "unavailable",
                source=audit["source"],
                fetched_at=audit["fetched_at"],
            )
        if audit and audit["fallback_reason"]:
            return EntrySignalResult(
                None,
                ENTRY_SIGNAL_VERSION,
                audit["fallback_reason"],
                "insufficient",
                source=audit["source"],
                fetched_at=audit["fetched_at"],
            )
        return EntrySignalResult(None, ENTRY_SIGNAL_VERSION, "INSUFFICIENT_WINDOW", "insufficient")
    latest_trade_date = str(rows[-1]["date"])[:10]
    freshness_days = (date.fromisoformat(today) - date.fromisoformat(latest_trade_date)).days
    if freshness_days > 5:
        return EntrySignalResult(
            None,
            ENTRY_SIGNAL_VERSION,
            SOURCE_STALE,
            "unavailable",
            source=rows[-1].get("source"),
            fetched_at=rows[-1].get("fetched_at"),
        )
    sources = {row["source"] for row in rows}
    if len(sources) > 1:
        return EntrySignalResult(
            None,
            ENTRY_SIGNAL_VERSION,
            REASON_MIXED_SOURCE_VOLUME_UNSAFE,
            "insufficient",
            source="mixed",
            fetched_at=rows[-1].get("fetched_at"),
        )
    adjusted_values = {row.get("adjusted") for row in rows}
    volume_units = {row.get("volume_unit") for row in rows}
    if (
        len(adjusted_values) > 1
        or len(volume_units) > 1
        or not volume_units
        or None in volume_units
        or "unknown" in volume_units
    ):
        return EntrySignalResult(
            None,
            ENTRY_SIGNAL_VERSION,
            REASON_MIXED_SOURCE_VOLUME_UNSAFE,
            "insufficient",
            source=rows[-1].get("source"),
            fetched_at=rows[-1].get("fetched_at"),
        )
    if any(row["volume"] is None for row in rows):
        return EntrySignalResult(None, ENTRY_SIGNAL_VERSION, REASON_MISSING_VOLUME, "insufficient")
    bars = pd.DataFrame(rows)[["date", "close", "volume"]]
    try:
        result = compute_entry_signal(bars)
        return EntrySignalResult(
            result.signal,
            result.version,
            result.reason,
            result.status,
            source=rows[-1].get("source"),
            fetched_at=rows[-1].get("fetched_at"),
        )
    except Exception as e:
        logger.warning(f"  {code} L3 买点层计算失败：{e}")
        return EntrySignalResult(None, ENTRY_SIGNAL_VERSION, "L3_ERROR", "unavailable")


def _get_score_price(
    db: sqlite3.Connection,
    provider: MarketDataProvider,
    code: str,
    today: str,
) -> float | None:
    cached = latest_daily_close(db, code, today, max_freshness_days=5)
    if cached:
        return cached[0]
    result = provider.fetch_score_price(code, today)
    insert_market_data_audit(db, result, "score_price", code, today)
    db.commit()
    if result.status == "failed" or result.value is None:
        logger.warning(f"  {code} price_at_score 获取失败：{result.error_code or 'UNKNOWN'}")
        return None
    return result.value


def _log_l3_coverage(db: sqlite3.Connection, today: str, strong_threshold: float) -> None:
    row = db.execute(
        """SELECT
               COUNT(CASE WHEN entry_signal_version='v1' THEN 1 END),
               COUNT(CASE WHEN entry_signal IN (0, 1) AND entry_signal_version='v1' THEN 1 END),
               COUNT(CASE WHEN entry_signal IS NULL AND entry_signal_version='v1' THEN 1 END),
               COUNT(CASE WHEN framework='A' AND total_score >= ?
                           AND entry_signal IS NULL AND entry_signal_version='v1' THEN 1 END)
           FROM predictions
           WHERE score_date=?""",
        (strong_threshold, today),
    ).fetchone()
    total, computable, unavailable, strong_unavailable = row
    coverage = (computable / total * 100) if total else 0
    reason_rows = db.execute(
        """SELECT COALESCE(entry_signal_reason, 'UNKNOWN'), COUNT(*)
           FROM predictions
           WHERE score_date=?
             AND entry_signal IS NULL
             AND entry_signal_version='v1'
           GROUP BY COALESCE(entry_signal_reason, 'UNKNOWN')
           ORDER BY COUNT(*) DESC, 1""",
        (today,),
    ).fetchall()
    reasons = ", ".join(f"{reason}={count}" for reason, count in reason_rows) or "none"
    logger.info(
        "L3 覆盖率：%s/%s = %.1f%%；不可计算：%s；strong 候选中 L3 不可计算：%s；原因：%s",
        computable,
        total,
        coverage,
        unavailable,
        strong_unavailable,
        reasons,
    )


def _refresh_fundamentals(label: str) -> tuple[int, int]:
    """对 WATCHLIST 每只股票执行 fetch，刷新 stock_fundamentals 缓存。返回 (success, failed)。"""
    success = failed = 0
    for item in config.WATCHLIST:
        code = item["code"]
        logger.info(f"  fetch {code} {item['name']} ...")
        result = _run_fetcher_process(code)
        if result == 0:
            logger.info(f"  ✓ {code}")
            success += 1
        else:
            logger.error(f"  ✗ {code} fetch 失败：exit={result}")
            failed += 1
    logger.info(f"{label} 完成：成功 {success} 只，失败 {failed} 只")
    return success, failed


def _run_fetcher_process(code: str, timeout: int = FETCHER_STOCK_TIMEOUT_SECONDS) -> int | str:
    """用独立进程刷新单只股票，避免底层 SDK 卡死拖住整轮 weekly。"""
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "a_stock_tracker.data.fetcher", "fetch", code],
            cwd=PROJECT_ROOT,
            timeout=timeout,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.stderr:
            for line in completed.stderr.strip().splitlines():
                logger.warning("fetcher[%s] %s", code, line)
    except subprocess.TimeoutExpired as e:
        logger.error("  ✗ %s fetch 超过 %ss，已终止子进程", code, timeout)
        stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else e.stderr
        if stderr:
            for line in stderr.strip().splitlines():
                logger.warning("fetcher[%s] %s", code, line)
        return "TIMEOUT"
    return completed.returncode


# ──────────────────────────────────────────────
# init 命令
# ──────────────────────────────────────────────


def cmd_init() -> None:
    """对 WATCHLIST 每只股票执行首次 fetch，填充 stock_fundamentals 表。"""
    logger.info(f"=== pipeline init：共 {len(config.WATCHLIST)} 只股票 ===")
    _refresh_fundamentals("init")


# ──────────────────────────────────────────────
# weekly 命令（每周刷新基本面缓存，取代 daily 内的批量 fetch）
# ──────────────────────────────────────────────


def cmd_weekly() -> None:
    """每周刷新 WATCHLIST 所有股票的基本面缓存（财务/PE/分红），供 daily 评分使用。"""
    logger.info(f"=== pipeline weekly：批量刷新基本面数据（{_today()}）共 {len(config.WATCHLIST)} 只 ===")
    _refresh_fundamentals("weekly")


# ──────────────────────────────────────────────
# daily 命令
# ──────────────────────────────────────────────


def _backfill_null_prices(db: sqlite3.Connection, today: str, provider: MarketDataProvider | None = None) -> int:
    """回填近 15 天内 price_at_score=NULL 的记录（不含今日）。

    只在今日价格抓取成功后调用，用历史日线数据（如 Tushare/BaoStock）补齐存量缺失。
    不修改 total_score / weights_hash，仅补 price_at_score。
    """
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=15)).strftime("%Y-%m-%d")
    rows = db.execute(
        """SELECT DISTINCT code, score_date FROM predictions
           WHERE price_at_score IS NULL AND score_date >= ? AND score_date < ?
           ORDER BY score_date""",
        (cutoff, today),
    ).fetchall()
    if not rows:
        return 0

    updated = 0
    provider = provider or get_default_market_data_provider()
    for code, score_date in rows:
        # Check database cache first to avoid redundant API calls
        cached = latest_daily_close(db, code, score_date, max_freshness_days=0)
        if cached:
            db.execute(
                "UPDATE predictions SET price_at_score=? WHERE code=? AND score_date=? AND price_at_score IS NULL",
                (cached[0], code, score_date),
            )
            db.commit()
            updated += 1
            logger.info(f"  回填 ✓ {code} {score_date} price={cached[0]} (来自缓存)")
            continue

        result = provider.fetch_score_price(code, score_date)
        insert_market_data_audit(db, result, "score_price", code, today)
        if result.value is None:
            logger.debug(f"  回填 {code} {score_date}：无可用价格")
            continue
        db.execute(
            "UPDATE predictions SET price_at_score=? WHERE code=? AND score_date=? AND price_at_score IS NULL",
            (result.value, code, score_date),
        )
        db.commit()
        updated += 1
        logger.info(f"  回填 ✓ {code} {score_date} price={result.value}")
    if updated:
        logger.info(f"价格回填完成：更新 {updated} 条记录")
    return updated


def _ensure_no_weights_hash_conflict(db, today: str, weights_hash: str) -> None:
    # 启动检查：今日已有记录且 hash 不同 → 拒绝运行
    existing = db.execute("SELECT DISTINCT weights_hash FROM predictions WHERE score_date = ?", (today,)).fetchall()
    if existing:
        existing_hashes = {r[0] for r in existing}
        if weights_hash not in existing_hashes:
            logger.error(
                f"冲突：今日 {today} 已有 weights_hash={existing_hashes}，"
                f"当前 hash={weights_hash}。\n"
                f"请手动删除今日记录后重跑：\n"
                f"  DELETE FROM predictions WHERE score_date='{today}';"
            )
            sys.exit(1)


def _collect_score_prices(db, provider, today: str) -> dict[str, float]:
    score_prices: dict = {}
    for item in config.WATCHLIST:
        code = item["code"]
        price = _get_score_price(db, provider, code, today)
        if price is not None:
            score_prices[code] = price
    return score_prices


def _inject_daily_pb_percentile(data: dict, price_at_score: float | None, code: str) -> None:
    # 注入日度实时 PB 分位（股价变化→分位变化→评分每日变化）
    if price_at_score:
        daily_pct = _compute_daily_pb_percentile(price_at_score, data)
        if daily_pct is not None:
            data["pb_percentile_10y"] = daily_pct
            logger.debug(f"  {code} 实时PB分位={daily_pct}%（价={price_at_score}, bps={data.get('bps')}）")
        else:
            logger.debug(f"  {code} 无法计算实时PB分位（bps/hist缺失），使用缓存值")


def _apply_qualitative_scores(db, code: str, name: str, data: dict) -> ProductionQualitativeSelection:
    # v2 仅消费预先验证并写入独立表的结果；off/非 canary/缺数/损坏
    # 均逐股回退既有 v1，不在 daily 内新增外部调用类型。
    selection = get_production_qualitative_selection(
        db,
        code,
        name,
        canary_codes=config.QUALITATIVE_V2_CANARY_CODES | config.QUALITATIVE_V2_PILOT_CODES,
        legacy_getter=get_qualitative_score,
    )
    data["moat_fixed"] = selection.scores["moat"]
    data["market_pos_fixed"] = selection.scores["market_pos"]
    data["sentiment_fixed"] = selection.scores["sentiment"]
    return selection


def _qualitative_snapshot_fields(selection: ProductionQualitativeSelection) -> dict[str, str]:
    return {
        "qualitative_snapshot_json": json.dumps(
            selection.scores,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        "qualitative_sources_json": json.dumps(
            selection.sources,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        "qualitative_mode": selection.mode,
    }


def _prepare_stock_scoring_input(
    db, item: dict, today: str, weights_hash: str, score_prices: dict[str, float], skipped: list[str]
) -> dict | None:
    code = item["code"]
    name = item["name"]
    fundamentals = get_fundamentals(code)
    if not fundamentals:
        logger.warning(f"  跳过 {code}：无基本面缓存（请先运行 init）")
        skipped.append(code)
        return None
    placeholders = ",".join("?" * len(SUPPORTED_FRAMEWORKS))
    written_today = db.execute(
        f"SELECT COUNT(DISTINCT framework) FROM predictions WHERE code=? AND score_date=? AND weights_hash=? AND framework IN ({placeholders})",
        (code, today, weights_hash, *sorted(SUPPORTED_FRAMEWORKS)),
    ).fetchone()[0]
    if written_today == len(SUPPORTED_FRAMEWORKS):
        logger.info(f"  检查点跳过 {code}：今日 {written_today}/{len(SUPPORTED_FRAMEWORKS)} 框架已完整写入")
        return None
    data = dict(fundamentals.get("data", fundamentals))
    report_period = data.get("report_period")
    price_at_score = score_prices.get(code)
    _inject_daily_pb_percentile(data, price_at_score, code)
    qualitative_selection = _apply_qualitative_scores(db, code, name, data)
    return {
        "code": code,
        "name": name,
        "data": data,
        "report_period": report_period,
        "price_at_score": price_at_score,
        "threshold_adjusted": 0,
        "entry_signal_result": _compute_stock_entry_signal(db, code, today),
        "l3_v2_result": compute_l3_v2_from_daily_bars(db, code, today),
        "l3_v2_fetched_at": _l3v2_now(),
        **_qualitative_snapshot_fields(qualitative_selection),
    }


# fmt: off
def _upsert_one_framework_prediction(
    db, prep: dict, today: str, weights_hash: str, framework: str, result: dict
) -> bool:
    cursor = db.execute(
        """INSERT OR IGNORE INTO predictions
                               (code, name, framework, score_date, price_at_score,
                                quant_score, total_score, weights_hash, report_period,
                                threshold_adjusted, entry_signal, entry_signal_version,
                                entry_signal_status, entry_signal_reason, entry_signal_source,
                                entry_signal_fetched_at,
                                l3_v2_signal, l3_v2_version, l3_v2_status, l3_v2_reason,
                                l3_v2_fetched_at, qualitative_snapshot_json,
                                qualitative_sources_json, qualitative_mode, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            prep["code"], prep["name"], framework, today, prep["price_at_score"],
            result["quant_score"], result["total_score"], weights_hash, prep["report_period"],
            prep["threshold_adjusted"], prep["entry_signal_result"].signal,
            prep["entry_signal_result"].version, prep["entry_signal_result"].status,
            prep["entry_signal_result"].reason, prep["entry_signal_result"].source,
            prep["entry_signal_result"].fetched_at, prep["l3_v2_result"].signal,
            prep["l3_v2_result"].version, prep["l3_v2_result"].status,
            prep["l3_v2_result"].reason, prep["l3_v2_fetched_at"],
            prep["qualitative_snapshot_json"], prep["qualitative_sources_json"],
            prep["qualitative_mode"], datetime.now().isoformat(),
        ),
    )
    if cursor.rowcount == 0:
        db.execute(
            """UPDATE predictions
                                   SET entry_signal=?,
                                       entry_signal_version=?,
                                       entry_signal_status=?,
                                       entry_signal_reason=?,
                                       entry_signal_source=?,
                                       entry_signal_fetched_at=?,
                                       l3_v2_signal=?,
                                       l3_v2_version=?,
                                       l3_v2_status=?,
                                       l3_v2_reason=?,
                                       l3_v2_fetched_at=?
                                   WHERE code=? AND framework=? AND score_date=?""",
            (
                prep["entry_signal_result"].signal, prep["entry_signal_result"].version,
                prep["entry_signal_result"].status, prep["entry_signal_result"].reason,
                prep["entry_signal_result"].source, prep["entry_signal_result"].fetched_at,
                prep["l3_v2_result"].signal, prep["l3_v2_result"].version,
                prep["l3_v2_result"].status, prep["l3_v2_result"].reason,
                prep["l3_v2_fetched_at"], prep["code"], framework, today,
            ),
        )
    return cursor.rowcount > 0
# fmt: on


def _write_stock_predictions(db, prep: dict, today: str, weights_hash: str, weights: dict, skipped: list[str]) -> int:
    code, name, data = prep["code"], prep["name"], prep["data"]
    stock_written = 0
    savepoint_created = False
    try:
        db.execute("SAVEPOINT sp_stock")
        savepoint_created = True
        for framework in sorted(SUPPORTED_FRAMEWORKS):
            try:
                result = score_stock(code, framework, data, weights=weights)
            except InsufficientDataError as e:
                logger.warning(f"  跳过 {code}/{framework}：{e}")
                skipped.append(f"{code}/{framework}")
                continue
            except UnsupportedFrameworkError as e:
                logger.error(f"  错误 {code}/{framework}：{e}")
                skipped.append(f"{code}/{framework}")
                continue
            if _upsert_one_framework_prediction(db, prep, today, weights_hash, framework, result):
                stock_written += 1
            logger.info(
                f"  ✓ {code} {name} [{framework}]  总分={result['total_score']}  data_quality={result['data_quality']}"
            )
        db.execute("RELEASE SAVEPOINT sp_stock")
        return stock_written
    except Exception as e:
        logger.error(f"  {code} 写入异常，回滚本股全部框架: {e}")
        if savepoint_created:
            db.execute("ROLLBACK TO SAVEPOINT sp_stock")
            db.execute("RELEASE SAVEPOINT sp_stock")
        return 0


def _run_daily_post_steps(today: str, weights: dict) -> None:
    # Telegram 推送（阈值来自 weights.json，失败不阻断）
    try:
        import a_stock_tracker.reporting.telegram_push as telegram_push

        buy_threshold = weights.get("thresholds", {}).get("buy_strong", 55)
        telegram_push.push_daily_signals(today, buy_threshold)
    except Exception as e:
        logger.warning(f"Telegram 推送失败（不影响 SQLite 数据）：{e}")

    # Sheets sync（独立后置步骤，失败不影响上方写入结果）
    try:
        import a_stock_tracker.reporting.sheets_sync as sheets_sync

        sheets_sync.sync_all()
    except Exception as e:
        logger.warning(f"Sheets sync 失败（不影响 SQLite 数据）：{e}")


def cmd_daily() -> None:
    weights = _load_weights()
    weights_hash = _compute_weights_hash(weights)
    today = _today()
    db = get_db()
    try:
        provider = get_default_market_data_provider()
        with provider_session(provider):
            market_data_cache = MarketDataCacheService(db, provider)
            _ensure_no_weights_hash_conflict(db, today, weights_hash)
            codes = [item["code"] for item in config.WATCHLIST]
            coverage = market_data_cache.refresh_daily_bars(codes, today, 120)
            logger.info(
                "L3 行情刷新：ok=%s degraded=%s failed=%s total=%s",
                coverage.ok,
                coverage.degraded,
                coverage.failed,
                coverage.total,
            )
            score_prices = _collect_score_prices(db, provider, today)
            if score_prices:
                logger.info(f"price_at_score：获取到 {len(score_prices)} 只股票收盘价")
                _backfill_null_prices(db, today, provider)
            else:
                logger.error("price_at_score 全部失败，今日评分中止（今日记录不写入，明日将写入明日数据）")
                return
            skipped: list[str] = []
            written = 0
            for item in config.WATCHLIST:
                prep = _prepare_stock_scoring_input(db, item, today, weights_hash, score_prices, skipped)
                if prep is None:
                    continue
                written += _write_stock_predictions(db, prep, today, weights_hash, weights, skipped)
            log_line = f"{today} daily 完成：写入 {written} 条，跳过 {len(skipped)} 条" + (
                f"（{skipped}）" if skipped else ""
            )
            logger.info(log_line)
            _log_l3_coverage(db, today, weights.get("thresholds", {}).get("buy_strong", 55))
            with open(os.path.join(config.LOG_DIR, "daily_log.txt"), "a", encoding="utf-8") as f:
                f.write(log_line + "\n")
    finally:
        db.close()
    _run_daily_post_steps(today, weights)


# ──────────────────────────────────────────────
# outcome-update 命令
# ──────────────────────────────────────────────


def _get_index_price(db: sqlite3.Connection, symbol: str, target_date: str) -> float | None:
    """从 index_prices 表查收盘价，向前找最近 10 个自然日（覆盖黄金周 7 天停牌）。"""
    for delta in range(11):
        d = _add_days(target_date, -delta)
        row = db.execute("SELECT close FROM index_prices WHERE symbol=? AND date=?", (symbol, d)).fetchone()
        if row:
            return row[0]
    return None


def _ensure_index_prices(
    db: sqlite3.Connection,
    earliest_score_date: str,
    today: str,
    provider: MarketDataProvider | None = None,
) -> None:
    """确保 index_prices 有从 earliest_score_date 到 today 的完整数据。"""
    latest_cached = db.execute("SELECT MAX(date) FROM index_prices WHERE symbol='000300'").fetchone()[0]

    start_date = earliest_score_date
    if latest_cached and latest_cached >= earliest_score_date:
        # 增量更新：从 latest_cached 到 today
        start_date = latest_cached

    if start_date > today:
        return

    logger.info(f"拉取沪深300日线：{start_date} → {today}")
    provider = provider or get_default_market_data_provider()
    result = provider.fetch_index_bars("000300")
    insert_market_data_audit(db, result, "benchmark_price", "000300", today)
    if result.value is None:
        logger.warning("沪深300历史数据拉取失败，benchmark 将为 NULL")
        return
    df = result.value
    date_col, close_col = "date", "close"
    # 腾讯接口返回全量历史，过滤到所需范围（date 列为 datetime.date 对象）
    df = df[df["date"].astype(str) >= start_date]

    inserted = 0
    for _, row in df.iterrows():
        d = str(row[date_col])[:10]
        c = float(row[close_col])
        cur = db.execute(
            "INSERT OR IGNORE INTO index_prices (symbol, date, close) VALUES (?, ?, ?)",
            ("000300", d, c),
        )
        inserted += cur.rowcount
    db.commit()
    logger.info(f"index_prices 写入 {inserted} 条")


def cmd_outcome_update() -> None:
    today = _today()
    db = get_db()
    try:
        provider = get_default_market_data_provider()
        with provider_session(provider):
            # 确保有足够的 index_prices 历史
            earliest = db.execute("SELECT MIN(score_date) FROM predictions").fetchone()[0]
            if earliest:
                _ensure_index_prices(db, earliest, today, provider)

            updated = 0
            for window, days in [("30d", 30), ("60d", 60), ("90d", 90)]:
                if window not in _OUTCOME_WINDOWS:
                    raise ValueError(f"未知 window: {window!r}")
                outcome_col = f"outcome_{window}"
                benchmark_col = f"benchmark_{window}"

                rows = db.execute(
                    f"""SELECT id, code, score_date, price_at_score
                        FROM predictions
                        WHERE {outcome_col} IS NULL
                          AND price_at_score IS NOT NULL
                          AND date(score_date, '+{days} days') <= ?""",
                    (today,),
                ).fetchall()

                for row_id, code, score_date, price_at_score in rows:
                    target_date = _add_days(score_date, days)

                    # 获取股票到期价格（向前找 10 个自然日，覆盖黄金周 7 天停牌）
                    outcome_price = None
                    estimate_flag = 0
                    result = provider.fetch_outcome_price(code, target_date)
                    insert_market_data_audit(db, result, "outcome_price", code, today)
                    if result.value is not None:
                        outcome_price = result.value
                        if result.freshness_days and result.freshness_days > 0:
                            estimate_flag = 1

                    if outcome_price is None:
                        logger.info(f"  {code} {window} 到期日 {target_date} 无可用价格（10日内），置 NULL")
                        continue

                    outcome_val = (outcome_price / price_at_score - 1) * 100

                    # 获取 benchmark
                    benchmark_val = None
                    score_index_price = _get_index_price(db, "000300", score_date)
                    target_index_price = _get_index_price(db, "000300", target_date)
                    if score_index_price and target_index_price:
                        benchmark_val = (target_index_price / score_index_price - 1) * 100

                    try:
                        db.execute(
                            f"""UPDATE predictions
                                SET {outcome_col} = ?,
                                    {benchmark_col} = ?,
                                    estimate_flag = CASE WHEN ? = 1 THEN 1 ELSE estimate_flag END
                                WHERE id = ?""",
                            (outcome_val, benchmark_val, estimate_flag, row_id),
                        )
                        updated += 1
                    except Exception as e:
                        logger.error(f"  写入 {code} {window} outcome 失败：{e}")

            db.commit()
            logger.info(f"outcome-update 完成：更新 {updated} 条")
    finally:
        db.close()

    # Phase 4 里程碑检测（失败不阻断）
    try:
        _check_phase4_milestone()
    except Exception as e:
        logger.warning(f"Phase 4 里程碑检测失败（不影响数据）：{e}")

    # Sheets sync（独立后置步骤，失败不影响 SQLite 数据）
    try:
        import a_stock_tracker.reporting.sheets_sync as sheets_sync

        sheets_sync.sync_all()
    except Exception as e:
        logger.warning(f"Sheets sync 失败（不影响 SQLite 数据）：{e}")


# ──────────────────────────────────────────────
# market-data-backfill 命令
# ──────────────────────────────────────────────


def _validate_l3_bar_windows(db: sqlite3.Connection, start: str) -> list[tuple]:
    return db.execute(
        """SELECT code,
                  COUNT(*) AS bars,
                  MIN(trade_date),
                  MAX(trade_date),
                  COUNT(DISTINCT source) AS sources,
                  COUNT(DISTINCT adjusted) AS adjustments,
                  COUNT(DISTINCT volume_unit) AS volume_units
           FROM daily_bars
           WHERE trade_date >= ?
             AND adjusted='none'
           GROUP BY code
           ORDER BY bars, code""",
        (start,),
    ).fetchall()


def _recompute_existing_l3_metadata(
    db: sqlite3.Connection,
    start: str,
    end: str,
    refreshed_codes: set[str] | None = None,
) -> int:
    rows = db.execute(
        """SELECT DISTINCT code, score_date
           FROM predictions
           WHERE score_date BETWEEN ? AND ?
             AND entry_signal_version='v1'
           ORDER BY score_date, code""",
        (start, end),
    ).fetchall()
    updated = 0
    for code, score_date in rows:
        if refreshed_codes is not None and code not in refreshed_codes:
            continue
        result = _compute_stock_entry_signal(db, code, score_date)
        cur = db.execute(
            """UPDATE predictions
               SET entry_signal=?,
                   entry_signal_version=?,
                   entry_signal_status=?,
                   entry_signal_reason=?,
                   entry_signal_source=?,
                   entry_signal_fetched_at=?
               WHERE code=? AND score_date=? AND entry_signal_version='v1'""",
            (
                result.signal,
                result.version,
                result.status,
                result.reason,
                result.source,
                result.fetched_at,
                code,
                score_date,
            ),
        )
        updated += cur.rowcount
    db.commit()
    return updated


def _backfill_fetch_start(start: str) -> str:
    return (date.fromisoformat(start) - timedelta(days=240)).isoformat()


def _refresh_daily_bars_range(
    db: sqlite3.Connection,
    provider: MarketDataProvider,
    codes: list[str],
    start: str,
    end: str,
) -> MarketDataCoverage:
    results = {}
    fetch_start = _backfill_fetch_start(start)
    for code in codes:
        result = provider.fetch_daily_bars_range(code, fetch_start, end)
        results[code] = result
        insert_market_data_audit(db, result, "l3_bars", code, end)
        if result.status != "failed" and result.value is not None:
            from a_stock_tracker.data.cache import upsert_daily_bars

            upsert_daily_bars(
                db,
                code,
                result.value,
                result.source,
                adjusted=result.adjusted,
                volume_unit=result.volume_unit,
                quality_status=result.status,
                fetched_at=result.fetched_at,
                error_code=result.error_code,
            )
    db.commit()
    ok = sum(1 for r in results.values() if r.status == "ok")
    degraded = sum(1 for r in results.values() if r.status == "degraded")
    failed = sum(1 for r in results.values() if r.status == "failed")
    return MarketDataCoverage(len(results), ok, degraded, failed, results)


def cmd_market_data_backfill(start: str, end: str, provider: MarketDataProvider | None = None) -> None:
    db = get_db()
    try:
        provider = provider or get_market_data_backfill_provider()
        with provider_session(provider):
            codes = [item["code"] for item in config.WATCHLIST]
            coverage = _refresh_daily_bars_range(db, provider, codes, start, end)
            logger.info(
                "market-data-backfill 行情刷新：ok=%s degraded=%s failed=%s total=%s",
                coverage.ok,
                coverage.degraded,
                coverage.failed,
                coverage.total,
            )
            refreshed_codes = {
                code
                for code, result in coverage.by_code.items()
                if result.status != "failed" and result.value is not None
            }
            updated_l3 = _recompute_existing_l3_metadata(db, start, end, refreshed_codes)
            logger.info("market-data-backfill L3 metadata 重算：更新 %s 条 prediction", updated_l3)
            for row in _validate_l3_bar_windows(db, start):
                code, bars, min_date, max_date, sources, adjustments, volume_units = row
                logger.info(
                    "daily_bars window %s bars=%s range=%s..%s sources=%s adjusted=%s volume_units=%s",
                    code,
                    bars,
                    min_date,
                    max_date,
                    sources,
                    adjustments,
                    volume_units,
                )
    finally:
        db.close()


# ──────────────────────────────────────────────
# accuracy-report 命令
# ──────────────────────────────────────────────


def cmd_accuracy_report() -> None:
    db = get_db()
    try:
        weights = _load_weights()
        weights_hash = _compute_weights_hash(weights)
        report = build_accuracy_report(db, weights, weights_hash)
        print(report)

        report_path = config.ACCURACY_REPORT_PATH
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        logger.info(f"报告已保存到 {report_path}")
    finally:
        db.close()


def cmd_framework_b_cohort_freeze(*, dry_run: bool, label_date: str | None = None) -> None:
    """Explicitly freeze one weekly report-only cohort; never writes predictions."""
    effective_date = label_date or _today()
    if dry_run:
        db_path = Path(config.DB_PATH).resolve()
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        db = get_db()
    try:
        result = freeze_weekly_cohort(db, _load_weights(), label_date=effective_date, dry_run=dry_run)
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    finally:
        db.close()


# ──────────────────────────────────────────────
# Phase 4 里程碑检测
# ──────────────────────────────────────────────

_PHASE4_POST_FIX_DATE = "2026-05-15"  # gross_margin + pb_percentile 修复日
_PHASE4_MILESTONES = [25, 50, 75, 100]


def _check_phase4_milestone() -> None:
    """统计 post-fix 30d 结案记录数，到达里程碑节点时发送 Telegram 通知。

    状态持久化在 phase_milestones 表，重复运行不重复推送。
    """
    db = get_db()
    try:
        count = db.execute(
            """SELECT COUNT(*) FROM predictions
               WHERE framework='A'
                 AND score_date >= ?
                 AND outcome_30d IS NOT NULL""",
            (_PHASE4_POST_FIX_DATE,),
        ).fetchone()[0]

        notified = {
            row[0] for row in db.execute("SELECT milestone FROM phase_milestones WHERE phase='phase4_30d'").fetchall()
        }

        for milestone in _PHASE4_MILESTONES:
            if count >= milestone and milestone not in notified:
                _send_phase4_notification(db, count, milestone)
                db.execute(
                    "INSERT OR IGNORE INTO phase_milestones(phase, milestone, notified_at) VALUES(?,?,?)",
                    ("phase4_30d", milestone, datetime.now().isoformat()),
                )
                db.commit()
                logger.info(f"Phase 4 里程碑 {milestone} 已通知")
    finally:
        db.close()


def _send_phase4_notification(db, count: int, milestone: int) -> None:
    """构造并发送 Phase 4 里程碑 Telegram 消息。"""
    import json as _json
    import urllib.request

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("Telegram 未配置，跳过 Phase 4 里程碑推送")
        return

    if milestone < 100:
        # 进度更新
        remaining = 100 - count
        lines = [
            f"Phase 4 进度 {count}/100 条",
            f"post-fix 30d 结案记录已达 {milestone} 条里程碑",
            f"距目标还差 {remaining} 条，预计约 {remaining} 个交易日",
            "",
            "系统正常运行中，无需操作。",
        ]
    else:
        # 100 条达成：附简要 hit_rate 摘要
        rows = db.execute(
            """SELECT
                 AVG(CASE WHEN alpha_30d > 0 THEN 1.0 ELSE 0.0 END) hit_rate,
                 COUNT(*) n,
                 AVG(outcome_30d) avg_ret,
                 AVG(alpha_30d)   avg_alpha
               FROM predictions
               WHERE framework='A'
                 AND score_date >= ?
                 AND outcome_30d IS NOT NULL
                 AND benchmark_30d IS NOT NULL""",
            (_PHASE4_POST_FIX_DATE,),
        ).fetchone()
        hit_rate, n, avg_ret, avg_alpha = rows
        hit_pct = f"{hit_rate * 100:.1f}%" if hit_rate is not None else "N/A"
        avg_ret_s = f"{avg_ret:+.2f}%" if avg_ret is not None else "N/A"
        avg_alpha_s = f"{avg_alpha:+.2f}%" if avg_alpha is not None else "N/A"

        lines = [
            "Phase 4 里程碑达成",
            f"post-fix 30d 结案记录已达 {count} 条",
            "",
            f"跑赢沪深300胜率：{hit_pct}（样本 {n} 条）",
            f"平均收益：{avg_ret_s}，平均 alpha：{avg_alpha_s}",
            "",
            "建议操作：",
            "  运行 python pipeline.py accuracy-report 查看完整报告",
            "  若 strong 层级 hit_rate_vs_300 > 55%，可进入后续 Framework B / 权重评估讨论",
        ]

    text = "\n".join(lines)
    payload = _json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:
        logger.warning(f"Phase 4 里程碑 Telegram 推送失败：{e}")


# ──────────────────────────────────────────────
# remove 命令
# ──────────────────────────────────────────────


def cmd_remove(code: str) -> None:
    """从 DB 彻底删除一只股票的基本面缓存和所有预测记录。
    调用前请先手动从 a_stock_tracker/config.py 的 WATCHLIST 中删除该条目。
    """
    db = get_db()
    try:
        pred_rows = db.execute("SELECT COUNT(*) FROM predictions WHERE code = ?", (code,)).fetchone()[0]
        fund_rows = db.execute("SELECT COUNT(*) FROM stock_fundamentals WHERE code = ?", (code,)).fetchone()[0]

        if pred_rows == 0 and fund_rows == 0:
            logger.warning(f"数据库中未找到 {code}，无需清理")
            return

        db.execute("DELETE FROM predictions WHERE code = ?", (code,))
        db.execute("DELETE FROM stock_fundamentals WHERE code = ?", (code,))
        db.commit()
        logger.info(f"已删除 {code}：predictions {pred_rows} 条，stock_fundamentals {fund_rows} 条")
    finally:
        db.close()


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="a-stock-tracker 管道")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="首次初始化，预填 watchlist 基本面缓存")
    sub.add_parser("weekly", help="每周刷新基本面缓存（cron: 每周六 10:00）")
    sub.add_parser("daily", help="每日评分，写入 predictions 表（依赖 weekly 缓存）")
    sub.add_parser("outcome-update", help="更新到期预测的实际收益")
    p_backfill = sub.add_parser("market-data-backfill", help="预热行情日线并重算已有 L3 metadata")
    p_backfill.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    p_backfill.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    sub.add_parser("accuracy-report", help="输出命中率报告")
    p_cohort = sub.add_parser("framework-b-cohort-freeze", help="显式冻结 Framework B 每周研究 cohort")
    p_cohort.add_argument("--dry-run", action="store_true", help="只显示候选计数，不建表、不写数据库")
    p_cohort.add_argument("--label-date", help="冻结日期 YYYY-MM-DD，默认今天")
    sub.add_parser("phase-check", help="手动触发 Phase 4 里程碑检测（自动在 outcome-update 后运行）")
    p_remove = sub.add_parser(
        "remove",
        help="删除一只股票在 predictions 和 stock_fundamentals 表中的记录（先从 a_stock_tracker/config.py 移除）",
    )
    p_remove.add_argument("code", help="股票代码，如 600036")

    args = parser.parse_args()

    if args.cmd == "init":
        cmd_init()
    elif args.cmd == "weekly":
        cmd_weekly()
    elif args.cmd == "daily":
        cmd_daily()
    elif args.cmd == "outcome-update":
        cmd_outcome_update()
    elif args.cmd == "market-data-backfill":
        cmd_market_data_backfill(args.start, args.end)
    elif args.cmd == "accuracy-report":
        cmd_accuracy_report()
    elif args.cmd == "framework-b-cohort-freeze":
        cmd_framework_b_cohort_freeze(dry_run=args.dry_run, label_date=args.label_date)
    elif args.cmd == "phase-check":
        _check_phase4_milestone()
    elif args.cmd == "remove":
        cmd_remove(args.code)


def _alert_crash(cmd: str, exc: Exception) -> None:
    """main()未捕获异常时的最后一道告警：cron环境下日志没人主动看，
    不发Telegram就等同于2026-04那次"claude command not found"静默两个月的同类风险。
    跟现有_send_phase4_notification同样的直连curl方式，不依赖额外模块。
    """
    import json as _json
    import urllib.request

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("Telegram 未配置，崩溃告警跳过发送（仍会在日志里报错）")
        return
    text = f"🚨 a-stock-tracker pipeline.py {cmd} 崩溃\n{type(exc).__name__}: {exc}\n详见 logs/ 下对应日志"
    payload = _json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as send_exc:
        logger.warning(f"崩溃告警本身也发送失败：{send_exc}")


def run() -> None:
    """Run the CLI with the historical crash-alert behavior."""
    try:
        main()
    except Exception as exc:
        logger.exception("pipeline.py 未捕获异常")
        _alert_crash(" ".join(sys.argv[1:]) or "(无子命令)", exc)
        raise


if __name__ == "__main__":
    run()
