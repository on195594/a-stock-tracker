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
import akshare as ak

import config
from lib.cache import get_db, get_fundamentals
from lib.data_quality import FieldStatus, evaluate_data_quality
from lib.entry_signal import ENTRY_SIGNAL_VERSION, EntrySignalResult, compute_entry_signal
from lib.framework_b_report import (
    POST_FIX_DATE,
    append_framework_b_dry_run,
    append_framework_b_quality_expansion,
    append_phase6_readiness,
)
from gemini_scorer import get_qualitative_score
from scorer import (
    SUPPORTED_FRAMEWORKS,
    InsufficientDataError,
    UnsupportedFrameworkError,
    compute_daily_pb_percentile,
    score_stock,
)


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


def _compute_daily_pb_percentile(price: float, data: dict) -> float | None:
    return compute_daily_pb_percentile(price, data)


def _normalize_entry_signal_bars(hist) -> object:
    """把 AKShare 日线中文列名归一化为 entry_signal 纯函数输入。"""
    if hist is None or getattr(hist, "empty", False):
        return hist
    rename_map = {"日期": "date", "收盘": "close", "成交量": "volume"}
    normalized = hist.rename(columns=rename_map)
    required = {"date", "close", "volume"}
    if not required.issubset(set(normalized.columns)):
        return normalized
    return normalized[["date", "close", "volume"]]


def _compute_stock_entry_signal(code: str, today: str) -> EntrySignalResult:
    """读取 120+ 交易日窗口并计算 L3；失败返回 NULL/v1，不阻断基础评分。"""
    start_date = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=220)).strftime("%Y%m%d")
    end_date = today.replace("-", "")
    try:
        hist = _retry(
            ak.stock_zh_a_hist,
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )
        return compute_entry_signal(_normalize_entry_signal_bars(hist))
    except Exception as e:
        logger.warning(f"  {code} L3 买点层计算失败：{e}")
        return EntrySignalResult(None, ENTRY_SIGNAL_VERSION, "L3_ERROR")


def _refresh_fundamentals(label: str) -> tuple[int, int]:
    """对 WATCHLIST 每只股票执行 fetch，刷新 stock_fundamentals 缓存。返回 (success, failed)。"""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
    import fetcher

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
    logger.info(f"{label} 完成：成功 {success} 只，失败 {failed} 只")
    return success, failed


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

def _backfill_null_prices(db: sqlite3.Connection, today: str) -> int:
    """回填近 15 天内 price_at_score=NULL 的记录（不含今日）。

    只在今日价格抓取成功后调用，用腾讯历史日线补齐存量缺失。
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
    for code, score_date in rows:
        prefix = "sh" if code.startswith("6") else "sz"
        d_start = score_date.replace("-", "")
        d_end = (datetime.strptime(score_date, "%Y-%m-%d") + timedelta(days=3)).strftime("%Y%m%d")
        try:
            hist = _retry(ak.stock_zh_a_hist_tx, symbol=f"{prefix}{code}",
                          start_date=d_start, end_date=d_end)
            if hist is None or hist.empty:
                logger.debug(f"  回填 {code} {score_date}：无历史数据")
                continue
            price = None
            for _, row in hist.iterrows():
                if str(row["date"])[:10] == score_date:
                    price = float(row["close"])
                    break
            if price is None:
                logger.debug(f"  回填 {code} {score_date}：未找到当日收盘价")
                continue
            db.execute(
                "UPDATE predictions SET price_at_score=? WHERE code=? AND score_date=? AND price_at_score IS NULL",
                (price, code, score_date),
            )
            db.commit()
            updated += 1
            logger.info(f"  回填 ✓ {code} {score_date} price={price}")
        except Exception as e:
            logger.warning(f"  回填 {code} {score_date} 失败：{e}")
    if updated:
        logger.info(f"价格回填完成：更新 {updated} 条记录")
    return updated


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

    # 获取今日 price_at_score（腾讯日线逐股，取最近5日内最新收盘价）
    snapshot_data: dict = {}
    hist_start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y%m%d")
    hist_end = today.replace("-", "")
    for item in config.WATCHLIST:
        code = item["code"]
        prefix = "sh" if code.startswith("6") else "sz"
        try:
            hist = _retry(ak.stock_zh_a_hist_tx, symbol=f"{prefix}{code}",
                          start_date=hist_start, end_date=hist_end)
            if hist is not None and not hist.empty:
                snapshot_data[code] = float(hist.iloc[-1]["close"])
        except Exception as e:
            logger.debug(f"{code} 腾讯日线获取失败：{e}")
    if snapshot_data:
        logger.info(f"腾讯日线：获取到 {len(snapshot_data)} 只股票收盘价")
        _backfill_null_prices(db, today)
    else:
        logger.error("腾讯日线全部失败，今日评分中止（今日记录不写入，明日将写入明日数据）")
        db.close()
        return

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

        # 注入日度实时 PB 分位（股价变化→分位变化→评分每日变化）
        if price_at_score:
            daily_pct = _compute_daily_pb_percentile(price_at_score, data)
            if daily_pct is not None:
                data["pb_percentile_10y"] = daily_pct
                logger.debug(f"  {code} 实时PB分位={daily_pct}%（价={price_at_score}, bps={data.get('bps')}）")
            else:
                logger.debug(f"  {code} 无法计算实时PB分位（bps/hist缺失），使用缓存值")

        # 注入 Gemini 定性评分（覆盖 phase1_fixed，失败自动 fallback）
        qual = get_qualitative_score(code, name)
        data["moat_fixed"] = qual["moat"]
        data["market_pos_fixed"] = qual["market_pos"]
        data["sentiment_fixed"] = qual["sentiment"]

        threshold_adjusted = 0
        entry_signal_result = _compute_stock_entry_signal(code, today)

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

            try:
                cursor = db.execute(
                    """INSERT OR IGNORE INTO predictions
                       (code, name, framework, score_date, price_at_score,
                        quant_score, total_score, weights_hash, report_period,
                        threshold_adjusted, entry_signal, entry_signal_version, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        code, name, framework, today, price_at_score,
                        result["quant_score"], result["total_score"],
                        weights_hash, report_period,
                        threshold_adjusted, entry_signal_result.signal,
                        entry_signal_result.version, datetime.now().isoformat(),
                    ),
                )
                db.commit()
                if cursor.rowcount > 0:
                    written += 1
                logger.info(
                    f"  ✓ {code} {name} [{framework}]  总分={result['total_score']}  "
                    f"data_quality={result['data_quality']}"
                )
            except Exception as e:
                logger.error(f"  写入 {code}/{framework} 失败：{e}")

    log_line = (
        f"{today} daily 完成：写入 {written} 条，跳过 {len(skipped)} 条"
        + (f"（{skipped}）" if skipped else "")
    )
    logger.info(log_line)
    with open(os.path.join(config.LOG_DIR, "daily_log.txt"), "a", encoding="utf-8") as f:
        f.write(log_line + "\n")
    db.close()

    # Telegram 推送（阈值来自 weights.json，失败不阻断）
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
    """从 index_prices 表查收盘价，向前找最近 10 个自然日（覆盖黄金周 7 天停牌）。"""
    for delta in range(11):
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

    logger.info(f"拉取沪深300日线（腾讯）：{start_date} → {today}")
    df = _retry(ak.stock_zh_index_daily_tx, symbol="sh000300")
    if df is None:
        logger.warning("沪深300历史数据拉取失败（腾讯接口失败），benchmark 将为 NULL")
        return
    # 腾讯接口列名（已验证：date, open, close, high, low, amount）
    assert "date" in df.columns and "close" in df.columns, (
        f"ak.stock_zh_index_daily_tx 列名变更，当前列：{list(df.columns)}"
    )
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

    # 确保有足够的 index_prices 历史
    earliest = db.execute("SELECT MIN(score_date) FROM predictions").fetchone()[0]
    if earliest:
        _ensure_index_prices(db, earliest, today)

    # target_date==today 的记录走 per-stock ak.stock_zh_a_hist 逐日查询（见下方循环）
    snapshot_data: dict = {}

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

            # 获取股票到期价格（向前找 10 个自然日，覆盖黄金周 7 天停牌）
            outcome_price = None
            estimate_flag = 0
            exact_price = snapshot_data.get(code) if target_date == today else None

            if exact_price:
                outcome_price = exact_price
            else:
                # 尝试历史价格
                for delta in range(11):
                    d = _add_days(target_date, -delta)
                    if d > today:
                        continue
                    try:
                        hist = _retry(ak.stock_zh_a_hist,
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
    db.close()

    # Phase 4 里程碑检测（失败不阻断）
    try:
        _check_phase4_milestone()
    except Exception as e:
        logger.warning(f"Phase 4 里程碑检测失败（不影响数据）：{e}")

    # Sheets sync（独立后置步骤，失败不影响 SQLite 数据）
    try:
        import sheets_sync
        sheets_sync.sync_all()
    except Exception as e:
        logger.warning(f"Sheets sync 失败（不影响 SQLite 数据）：{e}")


# ──────────────────────────────────────────────
# accuracy-report 命令
# ──────────────────────────────────────────────

def cmd_accuracy_report() -> None:
    db = get_db()

    # 排除数量
    null_count = db.execute(
        "SELECT COUNT(*) FROM predictions WHERE outcome_30d IS NULL"
    ).fetchone()[0]
    framework_a_closed = db.execute(
        "SELECT COUNT(*) FROM predictions WHERE outcome_30d IS NOT NULL AND framework = 'A'"
    ).fetchone()[0]

    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("a-stock-tracker 准确率报告")
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 60)

    if framework_a_closed < 100:
        lines.append(
            f"\n⚠️  样本不足（Framework A {framework_a_closed} 条已结案记录）\n"
            "    结论仅供参考，请勿据此做交易决策。\n"
            "    建议 Framework A 积累至 100 条以上再解读命中率。"
        )

    lines.append(
        f"\n已排除 {null_count} 条 NULL outcome 记录（停牌/退市/数据缺失），详见 daily_log.txt"
    )
    lines.append(
        "\n⚠️  选择性偏差声明：watchlist 为手动维护的已知标的，"
        "命中率不代表框架泛化能力。"
    )
    lines.append("")

    # per-framework 记录摘要
    fw_rows = db.execute(
        """SELECT framework, COUNT(*), COUNT(outcome_30d),
                  ROUND(COUNT(CASE WHEN alpha_30d > 0 THEN 1 END) * 1.0
                        / NULLIF(COUNT(alpha_30d), 0), 3)
           FROM predictions
           GROUP BY framework ORDER BY framework"""
    ).fetchall()
    if fw_rows:
        lines.append("── 分 Framework 统计 ──")
        lines.append(f"{'Framework':<12} {'总记录':>6}  {'30d结案':>8}  {'超额命中30d':>12}")
        for fw, total, closed30, hr30 in fw_rows:
            lines.append(
                f"{fw:<12} {total:>6}  {closed30:>8}  {_fmt(hr30):>12}"
            )
        lines.append("")

    _w = _load_weights().get("thresholds", {})
    _strong = _w.get("buy_strong", 55)
    _moderate = _w.get("buy_moderate", 45)
    _light = _w.get("buy_light", 35)

    _tier_query = """
        SELECT COUNT(*),
               ROUND(COUNT(CASE WHEN outcome_30d > 0 THEN 1 END) * 1.0 / NULLIF(COUNT(outcome_30d), 0), 3),
               ROUND(COUNT(CASE WHEN alpha_30d  > 0 THEN 1 END) * 1.0 / NULLIF(COUNT(alpha_30d),  0), 3),
               ROUND(COUNT(CASE WHEN alpha_60d  > 0 THEN 1 END) * 1.0 / NULLIF(COUNT(alpha_60d),  0), 3),
               ROUND(COUNT(CASE WHEN alpha_90d  > 0 THEN 1 END) * 1.0 / NULLIF(COUNT(alpha_90d),  0), 3),
               ROUND(AVG(alpha_30d), 2),
               ROUND(AVG(alpha_60d), 2),
               ROUND(AVG(alpha_90d), 2)
        FROM predictions
        WHERE outcome_30d IS NOT NULL
          AND framework = 'A'
          AND total_score >= ? AND total_score < ?
    """
    rows = []
    for tier, lo, hi in [
        ("strong",    _strong,   9999),
        ("moderate",  _moderate, _strong),
        ("light",     _light,    _moderate),
        ("no-action", 0,         _light),
    ]:
        r = db.execute(_tier_query, (lo, hi)).fetchone()
        if r and r[0] > 0:
            rows.append((tier,) + r)

    if not rows:
        lines.append("暂无已结案记录（outcome_30d 全部为 NULL）。")
    else:
        _append_tier_rows(lines, rows)

    _append_post_fix_section(lines, db, _strong, _moderate, _light)

    # 五分位排名分析（单调性检验：分数越高超额收益是否越高）
    q_rows = db.execute(
        """SELECT quintile,
                  COUNT(*) as cnt,
                  ROUND(MIN(total_score), 1) as lo,
                  ROUND(MAX(total_score), 1) as hi,
                  ROUND(AVG(alpha_30d), 2) as avg_a30
           FROM (
               SELECT total_score, alpha_30d,
                      NTILE(5) OVER (ORDER BY total_score) as quintile
               FROM predictions
               WHERE outcome_30d IS NOT NULL AND framework = 'A'
           ) GROUP BY quintile ORDER BY quintile DESC"""
    ).fetchall()
    if q_rows and len(q_rows) >= 3:
        lines.append("")
        lines.append("── 五分位单调性检验（Framework A，分数最高→最低）──")
        lines.append(f"{'分位':>4}  {'样本':>5}  {'分数范围':>12}  {'α30d均值':>10}")
        for qnum, cnt, lo, hi, a30 in q_rows:
            label = {5: "Q5(高)", 4: "Q4", 3: "Q3", 2: "Q2", 1: "Q1(低)"}.get(qnum, f"Q{qnum}")
            lines.append(f"{label:>6}  {cnt:>5}  [{lo:>5} ~{hi:>5}]  {_fmt(a30):>10}")
        lines.append("  理想：Q5 alpha > Q4 > Q3 > ... > Q1（单调递减 = 框架有序预测力）")

    lines.append("")
    lines.append("解读：hit_vs300 > 55% 才开始有意义；avg_alpha > 2% 且样本≥20 可认为有初步信号")
    lines.append("      不同 weights_hash 的记录代表不同实验，请分开解读")

    # ── L3 买点层 ──
    lines.append("")
    lines.append("── L3 买点层 ──")
    l3_counts = db.execute(
        """SELECT
               COUNT(CASE WHEN entry_signal_version='v1' THEN 1 END) AS v1_count,
               COUNT(CASE WHEN entry_signal=1 THEN 1 END) AS pass_count,
               COUNT(CASE WHEN entry_signal=0 THEN 1 END) AS reject_count,
               COUNT(CASE WHEN entry_signal IS NULL THEN 1 END) AS null_count,
               COUNT(CASE WHEN entry_signal IS NULL AND entry_signal_version IS NULL THEN 1 END) AS pre_l3_count,
               COUNT(CASE WHEN entry_signal IS NULL AND entry_signal_version='v1' THEN 1 END) AS null_v1_count
           FROM predictions"""
    ).fetchone()
    v1_count, pass_count, reject_count, l3_null_count, pre_l3_count, null_v1_count = l3_counts
    strong_l3 = db.execute(
        """SELECT
               COUNT(CASE WHEN entry_signal=1 THEN 1 END) AS strong_pass,
               COUNT(CASE WHEN entry_signal=0 THEN 1 END) AS strong_reject
           FROM predictions
           WHERE framework='A' AND total_score >= ?""",
        (_strong,),
    ).fetchone()
    strong_pass, strong_reject = strong_l3
    l3_closed = db.execute(
        """SELECT
               COUNT(*) AS closed_count,
               ROUND(COUNT(CASE WHEN alpha_30d > 0 THEN 1 END) * 1.0 / NULLIF(COUNT(alpha_30d), 0), 3) AS hit_rate
           FROM predictions
           WHERE framework='A'
             AND entry_signal=1
             AND entry_signal_version='v1'
             AND outcome_30d IS NOT NULL"""
    ).fetchone()
    l3_closed_count, l3_hit_rate = l3_closed
    lines.append(f"v1 记录数：{v1_count}")
    lines.append(f"entry_signal=1：{pass_count}")
    lines.append(f"entry_signal=0：{reject_count}")
    lines.append(f"entry_signal=NULL：{l3_null_count}")
    lines.append(f"NULL/NULL pre-L3：{pre_l3_count}")
    lines.append(f"NULL/v1 不可计算：{null_v1_count}")
    lines.append(f"strong 候选 L3 通过：{strong_pass}")
    lines.append(f"strong 候选 L3 拒绝：{strong_reject}")
    lines.append(f"L3 30d 已结案：{l3_closed_count}")
    lines.append(f"L3 30d 命中率：{_fmt(l3_hit_rate)}")
    if l3_closed_count < 30:
        lines.append(f"L3 30d 样本不足（{l3_closed_count}/30），不得输出确定性结论")

    # ── Gemini 评分漂移检测 ──
    lines.append("")
    lines.append("── Gemini 评分稳定性 ──")
    drift_rows = db.execute(
        """SELECT q1.code, q1.moat AS first_moat, q2.moat AS latest_moat,
                  q1.sentiment AS first_sent, q2.sentiment AS latest_sent,
                  q1.scored_date AS first_date, q2.scored_date AS latest_date
           FROM qualitative_scores q1
           JOIN qualitative_scores q2 ON q1.code = q2.code
           WHERE q1.scored_date = (SELECT MIN(scored_date) FROM qualitative_scores WHERE code=q1.code)
             AND q2.scored_date = (SELECT MAX(scored_date) FROM qualitative_scores WHERE code=q1.code)
             AND q1.scored_date != q2.scored_date"""
    ).fetchall()
    stock_count = db.execute(
        "SELECT COUNT(DISTINCT code) FROM qualitative_scores"
    ).fetchone()[0]
    first_date_row = db.execute(
        "SELECT MIN(scored_date) FROM qualitative_scores"
    ).fetchone()[0]

    if not drift_rows:
        if first_date_row:
            next_reeval = (date.fromisoformat(first_date_row) + timedelta(days=30)).isoformat()
            lines.append(f"{stock_count} 只股已评，尚无重评数据")
            lines.append(f"预计首批重评：{next_reeval}（30天缓存到期）")
        else:
            lines.append("尚无 Gemini 评分记录")
    else:
        lines.append(f"{'股票':<8} {'首次日期':<12} {'最新日期':<12} {'moat变化':>8} {'sent变化':>8} 状态")
        lines.append("-" * 56)
        for code, fm, lm, fs, ls, fd, ld in drift_rows:
            moat_delta = lm - fm
            sent_delta = ls - fs
            flag = " ⚠️ low_confidence" if abs(moat_delta) > 2 or abs(sent_delta) > 2 else ""
            lines.append(
                f"{code:<8} {fd:<12} {ld:<12} {moat_delta:>+8} {sent_delta:>+8}{flag}"
            )

    data_quality_summary = _append_data_quality_audit(lines, db)
    weights = _load_weights()
    # ── Framework B 旧重启门槛进度 ──
    lines.append("")
    lines.append("── Framework B 旧重启门槛进度（A框生产化前置，不等同 report-only）──")
    current_hash = _compute_weights_hash(weights)
    closed_a = db.execute(
        "SELECT COUNT(*) FROM predictions WHERE framework='A' AND outcome_30d IS NOT NULL AND weights_hash=?",
        (current_hash,),
    ).fetchone()[0]
    threshold1_met = closed_a >= 100
    lines.append(f"门槛 1：A框 30d 结案 ≥ 100（当前权重）：当前 {closed_a} / 100  {'✅' if threshold1_met else '❌'}")

    threshold2_met = False
    if rows:
        for row in rows:
            tier, cnt, h30, hb30, hb60, hb90, a30, a60, a90 = row
            if hb30 is not None and hb30 > 0.55 and cnt >= 20:
                threshold2_met = True
                break
    lines.append(f"门槛 2：任一层级 hit_rate_vs_300 > 55%（≥20条）：{'✅ 已满足' if threshold2_met else '❌ 尚未满足'}")

    if threshold1_met and threshold2_met:
        lines.append("→ 两个门槛同时满足，才可讨论 Framework B 生产写入；report-only 不受此门槛阻断。")
    else:
        lines.append("→ 生产写入继续等待；report-only 研究可按后续小节推进。")

    framework_b_summary = append_framework_b_dry_run(lines, db, weights)
    framework_b_quality_summary = append_framework_b_quality_expansion(lines, db, weights)
    framework_b_summary_for_readiness = dict(framework_b_summary)
    for key in (
        "b_label_sample_count",
        "b_label_closed_count",
        "b_label_earliest_due",
        "b_label_overdue_count",
    ):
        if key in framework_b_quality_summary:
            framework_b_summary_for_readiness[key] = framework_b_quality_summary[key]
    append_phase6_readiness(lines, db, data_quality_summary, framework_b_summary_for_readiness)

    report = "\n".join(lines)
    print(report)

    report_path = config.ACCURACY_REPORT_PATH
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    logger.info(f"报告已保存到 {report_path}")
    db.close()


def _fmt(v) -> str:
    if v is None:
        return "N/A"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def _tier_rows_for_period(
    db: sqlite3.Connection,
    start_date: str | None,
    strong: float,
    moderate: float,
    light: float,
) -> list[tuple]:
    date_filter = "AND score_date >= ?" if start_date else ""
    query = f"""
        SELECT COUNT(*),
               ROUND(COUNT(CASE WHEN outcome_30d > 0 THEN 1 END) * 1.0 / NULLIF(COUNT(outcome_30d), 0), 3),
               ROUND(COUNT(CASE WHEN alpha_30d  > 0 THEN 1 END) * 1.0 / NULLIF(COUNT(alpha_30d),  0), 3),
               ROUND(COUNT(CASE WHEN alpha_60d  > 0 THEN 1 END) * 1.0 / NULLIF(COUNT(alpha_60d),  0), 3),
               ROUND(COUNT(CASE WHEN alpha_90d  > 0 THEN 1 END) * 1.0 / NULLIF(COUNT(alpha_90d),  0), 3),
               ROUND(AVG(alpha_30d), 2),
               ROUND(AVG(alpha_60d), 2),
               ROUND(AVG(alpha_90d), 2)
        FROM predictions
        WHERE outcome_30d IS NOT NULL
          AND framework = 'A'
          {date_filter}
          AND total_score >= ? AND total_score < ?
    """
    rows = []
    params_prefix: tuple = (start_date,) if start_date else ()
    for tier, lo, hi in [
        ("strong", strong, 9999),
        ("moderate", moderate, strong),
        ("light", light, moderate),
        ("no-action", 0, light),
    ]:
        r = db.execute(query, params_prefix + (lo, hi)).fetchone()
        if r and r[0] > 0:
            rows.append((tier,) + r)
    return rows


def _append_tier_rows(lines: list[str], rows: list[tuple]) -> None:
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


def _append_post_fix_section(
    lines: list[str],
    db: sqlite3.Connection,
    strong: float,
    moderate: float,
    light: float,
) -> None:
    post_fix_closed = db.execute(
        """SELECT COUNT(*) FROM predictions
           WHERE framework='A' AND score_date >= ? AND outcome_30d IS NOT NULL""",
        (POST_FIX_DATE,),
    ).fetchone()[0]
    pre_fix_closed = db.execute(
        """SELECT COUNT(*) FROM predictions
           WHERE framework='A' AND score_date < ? AND outcome_30d IS NOT NULL""",
        (POST_FIX_DATE,),
    ).fetchone()[0]

    lines.append("")
    lines.append(f"── Post-fix 样本专区（Framework A，score_date >= {POST_FIX_DATE}）──")
    lines.append(f"pre-fix 30d 结案：{pre_fix_closed}")
    lines.append(f"post-fix 30d 结案：{post_fix_closed}")
    if post_fix_closed < 100:
        lines.append(f"post-fix 样本不足（{post_fix_closed}/100），不得与 pre-fix 混合下结论")

    rows = _tier_rows_for_period(db, POST_FIX_DATE, strong, moderate, light)
    if rows:
        _append_tier_rows(lines, rows)
    else:
        lines.append("post-fix 暂无已结案分层样本。")


def _latest_prediction_audit_row(db: sqlite3.Connection, code: str) -> tuple | None:
    return db.execute(
        """SELECT price_at_score, report_period
           FROM predictions
           WHERE code=? AND framework='A'
           ORDER BY score_date DESC, id DESC
           LIMIT 1""",
        (code,),
    ).fetchone()


def _latest_qualitative_row(db: sqlite3.Connection, code: str) -> tuple | None:
    return db.execute(
        """SELECT scored_date FROM qualitative_scores
           WHERE code=? ORDER BY scored_date DESC LIMIT 1""",
        (code,),
    ).fetchone()


def _append_data_quality_audit(lines: list[str], db: sqlite3.Connection) -> dict[str, int]:
    audited = missing_cache = acceptable = pb_ready = gemini_cached = 0
    financial_gross_margin_na = 0
    cache_report_period_missing = 0
    prediction_report_period_missing = 0
    prediction_report_period_history_locked = 0
    prediction_report_period_new_record_pending = 0
    prediction_report_period_no_a_record = 0
    problem_rows: list[str] = []

    for item in config.WATCHLIST:
        code = item["code"]
        fundamentals = get_fundamentals(code)
        if not fundamentals:
            missing_cache += 1
            problem_rows.append(f"{code} {item['name']}: cache_missing_or_expired")
            continue

        audited += 1
        data = dict(fundamentals.get("data", fundamentals))
        meta = fundamentals.get("_cache_meta", {})
        industry = str(meta.get("industry") or data.get("industry") or "")
        industry_context = f"{industry} {item['name']}"
        cache_report_period = data.get("report_period")
        latest = _latest_prediction_audit_row(db, code)
        prediction_report_period = latest[1] if latest else None
        if latest:
            data["price_at_score"] = latest[0]
            data["report_period"] = cache_report_period or prediction_report_period
        result = evaluate_data_quality(code, data, industry=industry_context)
        if result.is_acceptable:
            acceptable += 1
        if _latest_qualitative_row(db, code):
            gemini_cached += 1

        status_by_name = {field.name: field.status for field in result.fields}
        if status_by_name.get("pb_percentile_10y") == FieldStatus.OK:
            pb_ready += 1
        if status_by_name.get("gross_margin") == FieldStatus.NOT_APPLICABLE:
            financial_gross_margin_na += 1

        issues = list(result.missing_required)
        stale_fields = [field.name for field in result.fields if field.status == FieldStatus.STALE]
        issues.extend(f"stale:{name}" for name in stale_fields)
        if not cache_report_period:
            cache_report_period_missing += 1
            issues.append("missing:cache_report_period")
        if not prediction_report_period:
            prediction_report_period_missing += 1
            if latest and cache_report_period:
                prediction_report_period_history_locked += 1
                issues.append("missing:prediction_report_period(history_locked_cache_ready)")
            elif latest:
                prediction_report_period_new_record_pending += 1
                issues.append("missing:prediction_report_period(new_record_pending_cache_missing)")
            else:
                prediction_report_period_no_a_record += 1
                issues.append("missing:prediction_report_period(no_a_record)")
        if data.get("price_at_score") is None:
            issues.append("missing:price_at_score")
        if not _latest_qualitative_row(db, code):
            issues.append("gemini:no_cache")
        if issues:
            problem_rows.append(f"{code} {item['name']}: {', '.join(issues)}")

    total = len(config.WATCHLIST)
    lines.append("")
    lines.append("── 数据质量审计（当前 watchlist）──")
    lines.append(f"watchlist 股票数：{total}")
    lines.append(f"基本面缓存可用：{audited}/{total}")
    lines.append(f"基本面缓存缺失或过期：{missing_cache}")
    lines.append(f"required 字段可接受：{acceptable}/{audited if audited else 0}")
    lines.append(f"金融行业 gross_margin 不适用：{financial_gross_margin_na}")
    lines.append(f"PB 日度可计算：{pb_ready}/{audited if audited else 0}")
    lines.append(f"cache report_period 缺失：{cache_report_period_missing}/{audited if audited else 0}")
    lines.append(f"prediction report_period 缺失：{prediction_report_period_missing}/{audited if audited else 0}")
    if prediction_report_period_missing:
        lines.append(
            "prediction report_period 缺失拆分："
            f"历史记录不可回填={prediction_report_period_history_locked}, "
            f"新记录待补齐={prediction_report_period_new_record_pending}, "
            f"无A记录={prediction_report_period_no_a_record}"
        )
        if prediction_report_period_history_locked:
            lines.append("  - 历史记录不可回填：缓存已有 report_period，但最新 prediction 已写入为空；本报告不改写历史 predictions。")
        if prediction_report_period_new_record_pending:
            lines.append("  - 新记录待补齐：缓存仍缺 report_period，需先运行 weekly/fetch 成功后未来 daily 才能写入。")
        if prediction_report_period_no_a_record:
            lines.append("  - 无A记录：尚无可审计的 Framework A prediction。")
    lines.append(f"Gemini 缓存存在：{gemini_cached}/{total}")
    if problem_rows:
        lines.append("需处理样本（最多 12 条）：")
        lines.extend(f"  - {row}" for row in problem_rows[:12])
        if len(problem_rows) > 12:
            lines.append(f"  - ... 另 {len(problem_rows) - 12} 条")
    else:
        lines.append("未发现数据质量问题。")
    return {
        "watchlist_total": total,
        "audited": audited,
        "missing_cache": missing_cache,
        "acceptable": acceptable,
        "pb_ready": pb_ready,
        "financial_gross_margin_na": financial_gross_margin_na,
        "cache_report_period_missing": cache_report_period_missing,
        "prediction_report_period_missing": prediction_report_period_missing,
        "prediction_report_period_history_locked": prediction_report_period_history_locked,
        "prediction_report_period_new_record_pending": prediction_report_period_new_record_pending,
        "prediction_report_period_no_a_record": prediction_report_period_no_a_record,
        "gemini_cached": gemini_cached,
    }



# ──────────────────────────────────────────────
# Phase 4 里程碑检测
# ──────────────────────────────────────────────

_PHASE4_POST_FIX_DATE = "2026-05-15"   # gross_margin + pb_percentile 修复日
_PHASE4_MILESTONES = [25, 50, 75, 100]


def _check_phase4_milestone() -> None:
    """统计 post-fix 30d 结案记录数，到达里程碑节点时发送 Telegram 通知。

    状态持久化在 phase_milestones 表，重复运行不重复推送。
    """
    db = get_db()

    count = db.execute(
        """SELECT COUNT(*) FROM predictions
           WHERE framework='A'
             AND score_date >= ?
             AND outcome_30d IS NOT NULL""",
        (_PHASE4_POST_FIX_DATE,),
    ).fetchone()[0]

    notified = {
        row[0]
        for row in db.execute(
            "SELECT milestone FROM phase_milestones WHERE phase='phase4_30d'"
        ).fetchall()
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
    sub.add_parser("phase-check", help="手动触发 Phase 4 里程碑检测（自动在 outcome-update 后运行）")
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
    elif args.cmd == "phase-check":
        _check_phase4_milestone()
    elif args.cmd == "remove":
        cmd_remove(args.code)


if __name__ == "__main__":
    main()
