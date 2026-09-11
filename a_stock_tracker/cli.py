"""
a-stock-tracker 主编排器

子命令：
  daily           每日评分并写入 predictions 表（cron: 工作日 17:30）
  remove          删除指定股票的缓存和预测记录
"""

import argparse
from contextlib import contextmanager
import hashlib
from importlib.metadata import version
import json
import logging
import os
import sqlite3
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
from a_stock_tracker.qualitative.production import (
    DIMENSION_NAMES,
    SCORE_RANGES,
    ProductionQualitativeSelection,
    get_production_qualitative_selection,
)
from a_stock_tracker.scoring import (
    SCORING_INPUT_VERSION,
    SUPPORTED_FRAMEWORKS,
    InsufficientDataError,
    UnsupportedFrameworkError,
    score_stock,
    validated_pb_percentile,
)
from a_stock_tracker.paths import PROJECT_ROOT


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


def _scoring_implementation() -> dict:
    """Conservative cohort boundary: input/scorer code or provider changes require new evidence."""
    # ponytail: full-file hashes also split comment-only changes; reviewed semantic hashes if deploy churn matters.
    files = ("cli.py", "scoring.py", "data/tushare_primary_materialization.py", "qualitative/production.py")
    return {
        "input_policy_version": SCORING_INPUT_VERSION,
        "a_stock_lib": version("a-stock-lib"),
        "source_sha256": {
            name: hashlib.sha256((PROJECT_ROOT / "a_stock_tracker" / name).read_bytes()).hexdigest() for name in files
        },
    }


def _compute_weights_hash(weights: dict) -> str:
    def scoring_values(value):
        if isinstance(value, dict):
            return {key: scoring_values(item) for key, item in value.items() if key != "note"}
        if isinstance(value, list):
            return [scoring_values(item) for item in value]
        return value

    payload = json.dumps(
        {"frameworks": scoring_values(weights["frameworks"]), "implementation": _scoring_implementation()},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _today() -> str:
    return date.today().isoformat()


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
                "保留今日历史记录，下一交易日自然启用新 cohort；不得删除历史记录后重跑。"
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


def _validate_daily_pb_percentile(data: dict, today: str, code: str) -> None:
    data["pb_percentile_10y"] = validated_pb_percentile(data, today)
    if data["pb_percentile_10y"] is None:
        logger.warning("%s 无合格当日十年PB分位；保持缺失，不用短历史或旧值补齐", code)


def _apply_qualitative_scores(db, code: str, name: str, data: dict) -> ProductionQualitativeSelection:
    # v2 仅消费本地预计算结果；缺失或失效时读取现有本地 v1 缓存。
    selection = get_production_qualitative_selection(
        db,
        code,
        name,
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


def _qualitative_as_of_dates(db, code: str, selection: ProductionQualitativeSelection) -> dict[str, str | None]:
    rows = {
        "v1": db.execute(
            "SELECT moat, market_pos, sentiment, scored_date FROM qualitative_scores WHERE code=? ORDER BY scored_date DESC LIMIT 1",
            (code,),
        ).fetchone(),
        "v2": db.execute(
            "SELECT moat, market_pos, sentiment, as_of_date FROM qualitative_scores_v2 WHERE code=? ORDER BY as_of_date DESC, scored_at DESC LIMIT 1",
            (code,),
        ).fetchone(),
    }
    legacy = rows["v1"]
    if legacy is not None and any(
        isinstance(legacy[index], bool)
        or not isinstance(legacy[index], int)
        or not SCORE_RANGES[dimension][0] <= legacy[index] <= SCORE_RANGES[dimension][1]
        for index, dimension in enumerate(DIMENSION_NAMES)
    ):
        rows["v1"] = None
    dates: dict[str, str | None] = {}
    for index, dimension in enumerate(DIMENSION_NAMES):
        row = rows.get(selection.sources[dimension])
        dates[dimension] = str(row[3]) if row is not None and row[index] == selection.scores[dimension] else None
    return dates


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
    raw_data = dict(fundamentals.get("data", fundamentals))
    data = dict(raw_data)
    report_period = data.get("report_period")
    price_at_score = score_prices.get(code)
    _validate_daily_pb_percentile(data, today, code)
    qualitative_selection = _apply_qualitative_scores(db, code, name, data)
    return {
        "code": code,
        "name": name,
        "data": data,
        "scoring_snapshot": {
            "implementation": _scoring_implementation(),
            "raw_fundamentals": raw_data,
            "fundamentals_updated_at": raw_data.get("_cache_meta", {}).get("updated_at"),
            "industry": raw_data.get("_cache_meta", {}).get("industry"),
            "scoring_inputs": data,
            "price_at_score": price_at_score,
            "qualitative_as_of": _qualitative_as_of_dates(db, code, qualitative_selection),
        },
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
                                qualitative_sources_json, qualitative_mode, scoring_snapshot_json, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            prep["code"], prep["name"], framework, today, prep["price_at_score"],
            result["quant_score"], result["total_score"], weights_hash, prep["report_period"],
            prep["threshold_adjusted"], prep["l3_v2_result"].signal,
            prep["l3_v2_result"].version, prep["l3_v2_result"].status,
            prep["l3_v2_result"].reason, prep["l3_v2_fetched_at"],
            prep["qualitative_snapshot_json"], prep["qualitative_sources_json"],
            prep["qualitative_mode"], prep["scoring_snapshot_json"], datetime.now().isoformat(),
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
            prep["scoring_snapshot_json"] = json.dumps(
                {**prep["scoring_snapshot"], "weights": weights["frameworks"][framework], "result": result},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
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


def _write_accuracy_report(strong_threshold: float = 44.0) -> None:
    """从 SQLite 自动刷新唯一的策略评估报告。"""
    db = get_db()
    try:
        report = build_accuracy_report(db, strong_threshold=strong_threshold)
    finally:
        db.close()

    report_path = Path(config.ACCURACY_REPORT_PATH)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = report_path.with_name(f"{report_path.name}.tmp")
    temporary_path.write_text(report + "\n", encoding="utf-8")
    temporary_path.replace(report_path)
    logger.info(f"策略评估报告已自动刷新：{report_path}")


def cmd_daily() -> None:
    weights = _load_weights()
    weights_hash = _compute_weights_hash(weights)
    buy_threshold = weights.get("thresholds", {}).get("buy_strong", 55)
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
        try:
            _write_accuracy_report(buy_threshold)
        except Exception as e:
            logger.warning(f"策略评估报告刷新失败（不影响 SQLite 数据）：{e}")
    _run_daily_post_steps(today, weights)


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
    sub.add_parser("daily", help="每日评分，写入 predictions 表（依赖 TuShare 物化缓存）")
    p_remove = sub.add_parser(
        "remove",
        help="删除一只股票在 predictions 和 stock_fundamentals 表中的记录（先从 a_stock_tracker/config.py 移除）",
    )
    p_remove.add_argument("code", help="股票代码，如 600036")

    args = parser.parse_args()

    if args.cmd == "daily":
        cmd_daily()
    elif args.cmd == "remove":
        cmd_remove(args.code)


def _alert_crash(cmd: str, exc: Exception) -> None:
    """main()未捕获异常时的最后一道告警：cron环境下日志没人主动看，
    不发Telegram就等同于2026-04那次"claude command not found"静默两个月的同类风险。
    使用标准库 HTTP 请求，不依赖额外模块。
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
