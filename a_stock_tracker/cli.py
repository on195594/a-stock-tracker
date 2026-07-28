"""
a-stock-tracker 主编排器

子命令：
  init            首次初始化，对 watchlist 每只股票执行 fetch，填充 stock_fundamentals
  daily           每日评分并写入 predictions 表（cron: 工作日 16:30）
  outcome-update  更新到期预测的实际收益（cron: 工作日 17:00）
  accuracy-report 输出 benchmark 相对命中率报告
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
)
from a_stock_tracker.signals.l3_v2_pipeline import compute_l3_v2_from_daily_bars, now_isoformat as _l3v2_now
from a_stock_tracker.reporting.accuracy_report import build_accuracy_report
from a_stock_tracker.data.market_data import (
    MarketDataProvider,
    get_default_market_data_provider,
)
from a_stock_tracker.data.outcome_shadow import (
    build_shadow_candidate,
    inspect_frozen_cohort,
    write_shadow_report,
)
from a_stock_tracker.data.outcome_shadow_migration import (
    apply_shadow_import,
    inspect_shadow_import,
    revert_shadow_import,
)
from a_stock_tracker.qualitative.contract import DIMENSION_NAMES, SCORE_RANGES
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


_LOCAL_QUALITATIVE_FALLBACK = {"moat": 5, "market_pos": 2, "sentiment": 3}


def _load_local_qualitative_scores(db: sqlite3.Connection, code: str, _name: str) -> dict[str, int]:
    """Read the newest existing local v1 score without TTL or external calls."""
    row = db.execute(
        """SELECT moat, market_pos, sentiment
           FROM qualitative_scores
           WHERE code=?
           ORDER BY scored_date DESC
           LIMIT 1""",
        (code,),
    ).fetchone()
    if row is None:
        return dict(_LOCAL_QUALITATIVE_FALLBACK)
    scores = dict(zip(DIMENSION_NAMES, row, strict=True))
    for dimension, score in scores.items():
        minimum, maximum = SCORE_RANGES[dimension]
        if isinstance(score, bool) or not isinstance(score, int) or not minimum <= score <= maximum:
            logger.warning("%s 本地定性缓存越界，使用固定 fallback", code)
            return dict(_LOCAL_QUALITATIVE_FALLBACK)
    return scores


def _get_score_price(
    db: sqlite3.Connection,
    provider: MarketDataProvider,
    code: str,
    today: str,
) -> float | None:
    for adjusted in ("qfq", "none"):
        cached = latest_daily_close(db, code, today, max_freshness_days=5, adjusted=adjusted)
        if cached:
            return cached[0]
    result = provider.fetch_score_price(code, today)
    insert_market_data_audit(db, result, "score_price", code, today)
    db.commit()
    if result.status == "failed" or result.value is None:
        logger.warning(f"  {code} price_at_score 获取失败：{result.error_code or 'UNKNOWN'}")
        return None
    return result.value


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

    只在今日价格抓取成功后调用，用 TuShare 历史日线数据补齐存量缺失。
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
    # v2 仅消费本地预计算结果；缺失或失效时读取现有本地 v1 缓存。
    selection = get_production_qualitative_selection(
        db,
        code,
        name,
        canary_codes=config.QUALITATIVE_V2_CANARY_CODES | config.QUALITATIVE_V2_PILOT_CODES,
        legacy_getter=lambda selected_code, selected_name: _load_local_qualitative_scores(
            db, selected_code, selected_name
        ),
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
                                threshold_adjusted, l3_v2_signal, l3_v2_version, l3_v2_status, l3_v2_reason,
                                l3_v2_fetched_at, qualitative_snapshot_json,
                                qualitative_sources_json, qualitative_mode, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            prep["code"], prep["name"], framework, today, prep["price_at_score"],
            result["quant_score"], result["total_score"], weights_hash, prep["report_period"],
            prep["threshold_adjusted"], prep["l3_v2_result"].signal,
            prep["l3_v2_result"].version, prep["l3_v2_result"].status,
            prep["l3_v2_result"].reason, prep["l3_v2_fetched_at"],
            prep["qualitative_snapshot_json"], prep["qualitative_sources_json"],
            prep["qualitative_mode"], datetime.now().isoformat(),
        ),
    )
    if cursor.rowcount == 0:
        db.execute(
            """UPDATE predictions
                                   SET l3_v2_signal=?,
                                       l3_v2_version=?,
                                       l3_v2_status=?,
                                       l3_v2_reason=?,
                                       l3_v2_fetched_at=?
                                   WHERE code=? AND framework=? AND score_date=?""",
            (
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


def cmd_daily() -> None:
    weights = _load_weights()
    weights_hash = _compute_weights_hash(weights)
    today = _today()
    db = get_db()
    try:
        provider = get_default_market_data_provider()
        with provider_session(provider):
            _ensure_no_weights_hash_conflict(db, today, weights_hash)
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


# ──────────────────────────────────────────────
# accuracy-report 命令
# ──────────────────────────────────────────────


def cmd_accuracy_report() -> None:
    db = get_db()
    try:
        weights = _load_weights()
        report = build_accuracy_report(db, weights)
        print(report)

        report_path = config.ACCURACY_REPORT_PATH
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        logger.info(f"报告已保存到 {report_path}")
    finally:
        db.close()


def cmd_outcome_shadow_build(
    *,
    source_db: str,
    as_of_date: str,
    dry_run: bool,
    candidate_db: str | None = None,
    benchmark_snapshot: str | None = None,
) -> None:
    """Inspect or build an explicit candidate-only historical outcome shadow."""
    if dry_run:
        if candidate_db or benchmark_snapshot:
            raise ValueError("candidate_db and benchmark_snapshot are not accepted with --dry-run")
        inspection = inspect_frozen_cohort(source_db, as_of_date)
        print(json.dumps(asdict(inspection), ensure_ascii=False, sort_keys=True, default=str))
        return
    if not candidate_db or not benchmark_snapshot:
        raise ValueError("candidate_db and benchmark_snapshot are required without --dry-run")
    build = build_shadow_candidate(source_db, candidate_db, benchmark_snapshot, as_of_date=as_of_date)
    print(json.dumps(asdict(build), ensure_ascii=False, sort_keys=True, default=str))


def cmd_outcome_shadow_report(*, candidate_db: str, run_id: str, output: str) -> None:
    """Generate a DB-read-only outcome shadow reconciliation report."""
    result = write_shadow_report(candidate_db, run_id, output)
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, default=str))


def cmd_outcome_shadow_import(
    *,
    production_db: str,
    candidate_db: str,
    expected_run: str,
    expected_manifest: str,
    mode: str,
    backup_db: str | None = None,
    evidence_json: str | None = None,
) -> None:
    """Inspect or explicitly apply one additive outcome-shadow migration."""
    if mode == "inspect":
        if backup_db or evidence_json:
            raise ValueError("backup_db and evidence_json are not accepted in inspect mode")
        result = inspect_shadow_import(production_db, candidate_db, expected_run, expected_manifest)
    elif mode == "apply":
        if not backup_db or not evidence_json:
            raise ValueError("backup_db and evidence_json are required in apply mode")
        result = apply_shadow_import(
            production_db,
            candidate_db,
            expected_run,
            expected_manifest,
            backup_db,
            evidence_json,
        )
    else:
        raise ValueError(f"unsupported import mode: {mode}")
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, default=str))


def cmd_outcome_shadow_revert(
    *,
    production_db: str,
    expected_run: str,
    expected_manifest: str,
    evidence_json: str | None,
    mode: str,
) -> None:
    """Inspect or explicitly apply a bounded drop-only shadow rollback."""
    if mode not in {"inspect", "apply"}:
        raise ValueError(f"unsupported revert mode: {mode}")
    if mode == "apply" and not evidence_json:
        raise ValueError("evidence_json is required in apply mode")
    result = revert_shadow_import(
        production_db,
        expected_run,
        expected_manifest,
        evidence_json,
        apply=mode == "apply",
    )
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, default=str))


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
            "  继续完成 QFQ 总收益、截面 IC、spread 与回撤评估",
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
    sub.add_parser("accuracy-report", help="输出命中率报告")
    p_shadow = sub.add_parser("outcome-shadow-build", help="[冻结研究] 只读检查或构建隔离历史 outcome shadow")
    p_shadow.add_argument("--source-db", required=True, help="显式源 SQLite 路径")
    p_shadow.add_argument("--as-of-date", required=True, help="冻结日期 YYYY-MM-DD")
    p_shadow.add_argument("--dry-run", action="store_true", help="只读检查 cohort，不创建文件或 schema")
    p_shadow.add_argument("--candidate-db", help="新建隔离候选 SQLite 路径")
    p_shadow.add_argument("--benchmark-snapshot", help="TuShare index_daily 标准化 JSON 快照")
    p_shadow_report = sub.add_parser("outcome-shadow-report", help="[冻结研究] 生成只读 shadow 差异报告")
    p_shadow_report.add_argument("--candidate-db", required=True, help="隔离候选 SQLite 路径")
    p_shadow_report.add_argument("--run-id", required=True, help="immutable shadow run ID")
    p_shadow_report.add_argument("--output", required=True, help="新建 .json 报告路径；同时生成同名 .md")
    p_shadow_import = sub.add_parser(
        "outcome-shadow-import", help="[冻结研究] 检查或原子导入一个历史 outcome shadow run"
    )
    p_shadow_import.add_argument("--production-db", required=True, help="显式生产 SQLite 路径")
    p_shadow_import.add_argument("--candidate-db", required=True, help="只读候选 SQLite 路径")
    p_shadow_import.add_argument("--expected-run", required=True, help="预期 immutable run ID")
    p_shadow_import.add_argument("--expected-manifest", required=True, help="预期 manifest hash")
    p_shadow_import.add_argument("--mode", required=True, choices=("inspect", "apply"))
    p_shadow_import.add_argument("--backup-db", help="apply 必填：新建 online backup 路径")
    p_shadow_import.add_argument("--evidence-json", help="apply 必填：新建证据 JSON 路径")
    p_shadow_revert = sub.add_parser("outcome-shadow-revert", help="[冻结研究] 检查或执行受限 drop-only shadow 回滚")
    p_shadow_revert.add_argument("--production-db", required=True, help="显式生产 SQLite 路径")
    p_shadow_revert.add_argument("--expected-run", required=True, help="预期 immutable run ID")
    p_shadow_revert.add_argument("--expected-manifest", required=True, help="预期 manifest hash")
    p_shadow_revert.add_argument("--evidence-json", help="apply 必填：新建证据 JSON 路径")
    p_shadow_revert.add_argument("--mode", required=True, choices=("inspect", "apply"))
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
    elif args.cmd == "accuracy-report":
        cmd_accuracy_report()
    elif args.cmd == "outcome-shadow-build":
        cmd_outcome_shadow_build(
            source_db=args.source_db,
            as_of_date=args.as_of_date,
            dry_run=args.dry_run,
            candidate_db=args.candidate_db,
            benchmark_snapshot=args.benchmark_snapshot,
        )
    elif args.cmd == "outcome-shadow-report":
        cmd_outcome_shadow_report(candidate_db=args.candidate_db, run_id=args.run_id, output=args.output)
    elif args.cmd == "outcome-shadow-import":
        cmd_outcome_shadow_import(
            production_db=args.production_db,
            candidate_db=args.candidate_db,
            expected_run=args.expected_run,
            expected_manifest=args.expected_manifest,
            mode=args.mode,
            backup_db=args.backup_db,
            evidence_json=args.evidence_json,
        )
    elif args.cmd == "outcome-shadow-revert":
        cmd_outcome_shadow_revert(
            production_db=args.production_db,
            expected_run=args.expected_run,
            expected_manifest=args.expected_manifest,
            evidence_json=args.evidence_json,
            mode=args.mode,
        )
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
