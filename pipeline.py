"""
a-stock-tracker 主编排器

子命令：
  init            首次初始化，对 watchlist 每只股票执行 fetch，填充 stock_fundamentals
  daily           每日评分并写入 predictions 表（cron: 工作日 16:30）
  outcome-update  更新到期预测的实际收益（cron: 工作日 17:00）
  accuracy-report 输出 benchmark 相对命中率报告
"""

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta


def _load_dotenv() -> None:
    """从项目根目录 .env 加载环境变量（不覆盖已有变量，无 .env 文件时静默跳过）。"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

import akshare as ak

import config
from lib.cache import get_db, get_fundamentals
from gemini_scorer import get_qualitative_score
from scorer import InsufficientDataError, UnsupportedFrameworkError, score_stock

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
    return hashlib.md5(
        json.dumps(weights["frameworks"], sort_keys=True).encode()
    ).hexdigest()[:8]


def _retry(fn, *args, retries: int = 3, **kwargs):
    """指数退避重试，失败返回 None。"""
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                logger.warning(f"重试 {attempt + 1}/{retries}，{wait}s 后重试：{e}")
                time.sleep(wait)
            else:
                logger.error(f"重试耗尽：{e}")
    return None


def _today() -> str:
    return date.today().isoformat()


def _add_days(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


# ──────────────────────────────────────────────
# init 命令
# ──────────────────────────────────────────────

def cmd_init() -> None:
    """对 WATCHLIST 每只股票执行首次 fetch，填充 stock_fundamentals 表。"""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
    import fetcher

    logger.info(f"=== pipeline init：共 {len(config.WATCHLIST)} 只股票 ===")
    for item in config.WATCHLIST:
        code = item["code"]
        logger.info(f"  fetch {code} {item['name']} ...")
        try:
            fetcher.cmd_fetch([code])
            logger.info(f"  ✓ {code}")
        except (Exception, SystemExit) as e:
            logger.error(f"  ✗ {code} fetch 失败：{e}")
    logger.info("init 完成")


# ──────────────────────────────────────────────
# weekly 命令（每周刷新基本面缓存，取代 daily 内的批量 fetch）
# ──────────────────────────────────────────────

def cmd_weekly() -> None:
    """每周刷新 WATCHLIST 所有股票的基本面缓存（财务/PE/分红），供 daily 评分使用。

    与 init 的区别：init 是首次建立缓存，weekly 是周期性刷新已有缓存。
    两者实现相同，分开命名以便 cron 管理。
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
    import fetcher

    today = _today()
    logger.info(f"=== pipeline weekly：批量刷新基本面数据（{today}）共 {len(config.WATCHLIST)} 只 ===")
    success = failed = 0
    for item in config.WATCHLIST:
        code = item["code"]
        logger.info(f"  fetch {code} {item['name']} ...")
        try:
            fetcher.cmd_fetch([code])
            logger.info(f"  ✓ {code}")
            success += 1
        except (Exception, SystemExit) as e:
            logger.error(f"  ✗ {code} fetch 失败：{e}")
            failed += 1
    logger.info(f"weekly 完成：成功 {success} 只，失败 {failed} 只")


# ──────────────────────────────────────────────
# daily 命令
# ──────────────────────────────────────────────

def cmd_daily() -> None:
    weights = _load_weights()
    weights_hash = _compute_weights_hash(weights)
    today = _today()
    db = get_db()

    # 启动检查：今日已有记录且 hash 不同 → 拒绝运行
    existing = db.execute(
        "SELECT DISTINCT weights_hash FROM predictions WHERE score_date = ?", (today,)
    ).fetchall()
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

    # 获取今日 spot_em 快照（用于提取 price_at_score）
    snapshot_data: dict = {}
    try:
        spot_df = _retry(ak.stock_zh_a_spot_em)
        if spot_df is None:
            raise RuntimeError("spot_em 重试耗尽")
        for _, row in spot_df.iterrows():
            code = str(row.get("代码", "")).strip()
            price = row.get("最新价")
            if code and price is not None:
                try:
                    snapshot_data[code] = float(price)
                except (ValueError, TypeError):
                    pass
        logger.info(f"spot_em 快照：{len(snapshot_data)} 只股票")
    except Exception as e:
        logger.warning(f"spot_em 快照获取失败，price_at_score 将为 NULL：{e}")

    skipped: list[str] = []
    written = 0

    for item in config.WATCHLIST:
        code = item["code"]
        name = item["name"]
        fundamentals = get_fundamentals(code)
        if not fundamentals:
            logger.warning(f"  跳过 {code}：无基本面缓存（请先运行 init）")
            skipped.append(code)
            continue

        data = dict(fundamentals.get("data", fundamentals))
        report_period = data.get("report_period")
        price_at_score = snapshot_data.get(code)

        # 注入 Gemini 定性评分（覆盖 phase1_fixed，失败自动 fallback）
        qual = get_qualitative_score(code, name)
        data["moat_fixed"] = qual["moat"]
        data["market_pos_fixed"] = qual["market_pos"]
        data["sentiment_fixed"] = qual["sentiment"]

        try:
            result = score_stock(code, "A", data, weights=weights)
        except InsufficientDataError as e:
            logger.warning(f"  跳过 {code}：{e}")
            skipped.append(code)
            continue
        except UnsupportedFrameworkError as e:
            logger.error(f"  错误 {code}：{e}")
            skipped.append(code)
            continue

        thresholds = weights.get("thresholds", {})
        threshold_adjusted = 0
        if thresholds.get("_adjusted"):
            threshold_adjusted = 1

        try:
            db.execute(
                """INSERT OR IGNORE INTO predictions
                   (code, name, framework, score_date, price_at_score,
                    quant_score, total_score, weights_hash, report_period,
                    threshold_adjusted, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    code, name, "A", today, price_at_score,
                    result["quant_score"], result["total_score"],
                    weights_hash, report_period,
                    threshold_adjusted, datetime.now().isoformat(),
                ),
            )
            db.commit()
            written += 1
            logger.info(
                f"  ✓ {code} {name}  总分={result['total_score']}  "
                f"data_quality={result['data_quality']}"
            )
        except Exception as e:
            logger.error(f"  写入 {code} 失败：{e}")

    log_line = (
        f"{today} daily 完成：写入 {written} 条，跳过 {len(skipped)} 条"
        + (f"（{skipped}）" if skipped else "")
    )
    logger.info(log_line)
    with open(os.path.join(config.LOG_DIR, "daily_log.txt"), "a", encoding="utf-8") as f:
        f.write(log_line + "\n")
    db.close()

    # Telegram 推送（≥55分触发，失败不阻断）
    try:
        import telegram_push
        buy_threshold = weights.get("thresholds", {}).get("buy_strong", 55)
        telegram_push.push_daily_signals(today, buy_threshold)
    except Exception as e:
        logger.warning(f"Telegram 推送失败（不影响 SQLite 数据）：{e}")

    # Sheets sync（独立后置步骤，失败不影响上方写入结果）
    try:
        import sheets_sync
        sheets_sync.sync_all()
    except Exception as e:
        logger.warning(f"Sheets sync 失败（不影响 SQLite 数据）：{e}")


# ──────────────────────────────────────────────
# outcome-update 命令
# ──────────────────────────────────────────────

def _get_index_price(db: sqlite3.Connection, symbol: str, target_date: str) -> float | None:
    """从 index_prices 表查收盘价，向前找最近 5 个交易日。"""
    for delta in range(6):
        d = _add_days(target_date, -delta)
        row = db.execute(
            "SELECT close FROM index_prices WHERE symbol=? AND date=?", (symbol, d)
        ).fetchone()
        if row:
            return row[0]
    return None


def _ensure_index_prices(db: sqlite3.Connection, earliest_score_date: str, today: str) -> None:
    """确保 index_prices 有从 earliest_score_date 到 today 的完整数据。"""
    latest_cached = db.execute(
        "SELECT MAX(date) FROM index_prices WHERE symbol='000300'"
    ).fetchone()[0]

    start_date = earliest_score_date
    if latest_cached and latest_cached >= earliest_score_date:
        # 增量更新：从 latest_cached 到 today
        start_date = latest_cached

    if start_date > today:
        return

    logger.info(f"拉取沪深300日线：{start_date} → {today}")
    df = _retry(
        ak.index_zh_a_hist,
        symbol="000300", period="daily",
        start_date=start_date.replace("-", ""),
        end_date=today.replace("-", ""),
    )

    if df is None:
        logger.warning("东方财富接口失败，尝试腾讯 fallback：ak.stock_zh_index_daily_tx")
        df = _retry(ak.stock_zh_index_daily_tx, symbol="sh000300")
        if df is None:
            logger.warning("沪深300历史数据拉取失败（两个接口均失败），benchmark 将为 NULL")
            return
        # 腾讯接口列名（已验证：date, open, close, high, low, amount）
        assert "date" in df.columns and "close" in df.columns, (
            f"ak.stock_zh_index_daily_tx 列名变更，当前列：{list(df.columns)}"
        )
        date_col, close_col = "date", "close"
        # 腾讯接口返回全量历史，过滤到所需范围（date 列为 datetime.date 对象）
        df = df[df["date"].astype(str) >= start_date]
    else:
        # 东方财富接口列名（中文）
        assert "日期" in df.columns and "收盘" in df.columns, (
            f"ak.index_zh_a_hist 列名变更，当前列：{list(df.columns)}"
        )
        date_col, close_col = "日期", "收盘"

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

    # 确保有足够的 index_prices 历史
    earliest = db.execute("SELECT MIN(score_date) FROM predictions").fetchone()[0]
    if earliest:
        _ensure_index_prices(db, earliest, today)

    # 今日 spot_em 快照（用于获取股票今日收盘价，仅 target_date==today 时需要）
    snapshot_data: dict = {}
    try:
        spot_df = _retry(ak.stock_zh_a_spot_em)
        if spot_df is not None:
            for _, row in spot_df.iterrows():
                code = str(row.get("代码", "")).strip()
                price = row.get("最新价")
                if code and price is not None:
                    try:
                        snapshot_data[code] = float(price)
                    except (ValueError, TypeError):
                        pass
        else:
            logger.warning("spot_em 快照失败，outcome-update 将跳过需要今日价格的记录")
    except Exception as e:
        logger.warning(f"spot_em 快照失败，outcome-update 将跳过需要今日价格的记录：{e}")

    updated = 0
    for window, days in [("30d", 30), ("60d", 60), ("90d", 90)]:
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

            # 获取股票到期价格（向前找 5 个交易日）
            outcome_price = None
            estimate_flag = 0
            exact_price = snapshot_data.get(code) if target_date == today else None

            if exact_price:
                outcome_price = exact_price
            else:
                # 尝试历史价格
                for delta in range(6):
                    d = _add_days(target_date, -delta)
                    if d > today:
                        continue
                    try:
                        hist = ak.stock_zh_a_hist(
                            symbol=code, period="daily",
                            start_date=d.replace("-", ""),
                            end_date=d.replace("-", ""),
                            adjust="",
                        )
                        if hist is not None and not hist.empty:
                            close_col = "收盘" if "收盘" in hist.columns else hist.columns[4]
                            outcome_price = float(hist[close_col].iloc[-1])
                            if delta > 0:
                                estimate_flag = 1
                            break
                    except Exception:
                        continue

            if outcome_price is None:
                logger.info(f"  {code} {window} 到期日 {target_date} 无可用价格（5日内），置 NULL")
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
    db.close()


# ──────────────────────────────────────────────
# accuracy-report 命令
# ──────────────────────────────────────────────

def cmd_accuracy_report() -> None:
    db = get_db()

    # 排除数量
    null_count = db.execute(
        "SELECT COUNT(*) FROM predictions WHERE outcome_30d IS NULL"
    ).fetchone()[0]
    total_closed = db.execute(
        "SELECT COUNT(*) FROM predictions WHERE outcome_30d IS NOT NULL"
    ).fetchone()[0]

    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("a-stock-tracker 准确率报告")
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 60)

    if total_closed < 100:
        lines.append(
            f"\n⚠️  样本不足（{total_closed} 条已结案记录）\n"
            "    结论仅供参考，请勿据此做交易决策。\n"
            "    建议积累至 100 条以上再解读命中率。"
        )

    lines.append(
        f"\n已排除 {null_count} 条 NULL outcome 记录（停牌/退市/数据缺失），详见 daily_log.txt"
    )
    lines.append(
        "\n⚠️  选择性偏差声明：watchlist 为手动维护的已知标的，"
        "命中率不代表框架泛化能力。"
    )
    lines.append("")

    rows = db.execute("""
        SELECT
            CASE
                WHEN total_score >= 65 THEN 'strong'
                WHEN total_score >= 55 THEN 'moderate'
                WHEN total_score >= 45 THEN 'light'
                ELSE 'no-action'
            END AS signal_tier,
            COUNT(*) AS predictions,
            ROUND(AVG(CASE WHEN outcome_30d > 0 THEN 1.0 ELSE 0.0 END), 3) AS hit_abs_30d,
            ROUND(AVG(CASE WHEN alpha_30d > 0 THEN 1.0 ELSE 0.0 END), 3) AS hit_vs300_30d,
            ROUND(AVG(CASE WHEN alpha_60d > 0 THEN 1.0 ELSE 0.0 END), 3) AS hit_vs300_60d,
            ROUND(AVG(CASE WHEN alpha_90d > 0 THEN 1.0 ELSE 0.0 END), 3) AS hit_vs300_90d,
            ROUND(AVG(alpha_30d), 2) AS avg_alpha_30d,
            ROUND(AVG(alpha_60d), 2) AS avg_alpha_60d,
            ROUND(AVG(alpha_90d), 2) AS avg_alpha_90d
        FROM predictions
        WHERE outcome_30d IS NOT NULL
        GROUP BY signal_tier
        ORDER BY CASE signal_tier
            WHEN 'strong'   THEN 1
            WHEN 'moderate' THEN 2
            WHEN 'light'    THEN 3
            ELSE 4
        END
    """).fetchall()

    if not rows:
        lines.append("暂无已结案记录（outcome_30d 全部为 NULL）。")
    else:
        header = f"{'信号层级':<10} {'条数':>5}  {'绝对30d':>8}  {'超额30d':>8}  {'超额60d':>8}  {'超额90d':>8}  {'α30d':>7}  {'α60d':>7}  {'α90d':>7}"
        lines.append(header)
        lines.append("-" * len(header))
        for row in rows:
            tier, cnt, h30, hb30, hb60, hb90, a30, a60, a90 = row
            lines.append(
                f"{tier:<10} {cnt:>5}  "
                f"{_fmt(h30):>8}  {_fmt(hb30):>8}  {_fmt(hb60):>8}  {_fmt(hb90):>8}  "
                f"{_fmt(a30):>7}  {_fmt(a60):>7}  {_fmt(a90):>7}"
            )

    lines.append("")
    lines.append("解读：hit_vs300 > 55% 才开始有意义；avg_alpha > 2% 且样本≥20 可认为有初步信号")
    lines.append("      不同 weights_hash 的记录代表不同实验，请分开解读")

    report = "\n".join(lines)
    print(report)

    report_path = os.path.join(os.path.dirname(__file__), "accuracy_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    logger.info(f"报告已保存到 {report_path}")
    db.close()


def _fmt(v) -> str:
    if v is None:
        return "N/A"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


# ──────────────────────────────────────────────
# remove 命令
# ──────────────────────────────────────────────

def cmd_remove(code: str) -> None:
    """从 DB 彻底删除一只股票的基本面缓存和所有预测记录。
    调用前请先手动从 config.py WATCHLIST 中删除该条目。
    """
    db = get_db()
    pred_rows = db.execute(
        "SELECT COUNT(*) FROM predictions WHERE code = ?", (code,)
    ).fetchone()[0]
    fund_rows = db.execute(
        "SELECT COUNT(*) FROM stock_fundamentals WHERE code = ?", (code,)
    ).fetchone()[0]

    if pred_rows == 0 and fund_rows == 0:
        logger.warning(f"数据库中未找到 {code}，无需清理")
        return

    db.execute("DELETE FROM predictions WHERE code = ?", (code,))
    db.execute("DELETE FROM stock_fundamentals WHERE code = ?", (code,))
    db.commit()
    db.close()
    logger.info(f"已删除 {code}：predictions {pred_rows} 条，stock_fundamentals {fund_rows} 条")


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
    p_remove = sub.add_parser("remove", help="从 DB 删除一只股票的所有数据（先从 config.py 移除）")
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
    elif args.cmd == "remove":
        cmd_remove(args.code)


if __name__ == "__main__":
    main()
