#!/usr/bin/env python3
"""
A 股选股管道的 SQLite 缓存层。

保留的职责：
- stock_fundamentals 基本面缓存
- spot_em_snapshot 当日行情快照缓存
- predictions / index_prices / qualitative_scores / phase_milestones schema

旧 skill 时代的分析结论缓存、预警和持仓 CLI 已移除。本项目只做选股信号验证，
不做持仓或账户管理。
"""

import json
import re as _re
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Any

from a_stock_tracker.config import DB_PATH

_SAFE_IDENTIFIER_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 行业 → TTL 映射（关键词匹配，越靠前优先级越高）
INDUSTRY_TTL_MAP = [
    # 168h：季报驱动、基本面变化慢（对齐 weekly cron 7 天刷新周期）
    (
        [
            "银行",
            "保险",
            "券商",
            "国有大行",
            "股份制银行",
            "城商行",
            "农商行",
            "水电",
            "公用事业",
            "电网",
            "水务",
            "燃气",
            "高速",
        ],
        168,
    ),
    # 48h：情绪/渠道敏感，但 daily 最多两天不刷新
    (["白酒", "消费", "食品", "零售", "饮料", "乳制品"], 48),
]


def get_industry_ttl(industry: str) -> int:
    """根据行业名称推断基本面缓存 TTL（小时）。"""
    for keywords, ttl in INDUSTRY_TTL_MAP:
        if any(kw in industry for kw in keywords):
            return ttl
    return 168


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if not _SAFE_IDENTIFIER_RE.match(name):
            raise ValueError(f"unsafe column name: {name!r}")
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def ensure_framework_b_cohort_schema(conn: sqlite3.Connection) -> None:
    """Create the explicit report-only Framework B cohort store.

    This is intentionally not called by get_db(): schema creation belongs to the
    explicit framework-b-cohort-freeze command, not accuracy-report.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS framework_b_label_cohorts (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            cohort_week             TEXT NOT NULL,
            label_date              TEXT NOT NULL,
            code                    TEXT NOT NULL,
            name                    TEXT,
            industry                TEXT,
            source_a_prediction_id  INTEGER NOT NULL REFERENCES predictions(id),
            source_a_score_date     TEXT NOT NULL,
            source_a_total_score    REAL,
            score_a                 REAL NOT NULL,
            score_b                 REAL NOT NULL,
            delta                   REAL NOT NULL,
            label                   TEXT NOT NULL,
            rule_name               TEXT NOT NULL,
            rule_snapshot_json      TEXT NOT NULL,
            threshold_snapshot_json TEXT NOT NULL,
            candidate_input_json    TEXT NOT NULL,
            score_snapshot_json     TEXT NOT NULL,
            weights_hash            TEXT NOT NULL,
            status                  TEXT NOT NULL DEFAULT 'active',
            created_at              TEXT NOT NULL,
            UNIQUE(cohort_week, code, rule_name, weights_hash),
            UNIQUE(source_a_prediction_id, weights_hash)
        )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_framework_b_cohort_status_week
           ON framework_b_label_cohorts(status, cohort_week, label_date)"""
    )
    conn.commit()
    # SQLite disables FK enforcement per connection by default. Enable it only
    # for the explicit cohort-write connection after schema DDL is committed.
    conn.execute("PRAGMA foreign_keys=ON")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS stock_fundamentals (
        code TEXT PRIMARY KEY,
        name TEXT,
        industry TEXT,
        data JSON,
        updated_at TEXT,
        ttl_hours INTEGER DEFAULT 168
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS spot_em_snapshot (
        snapshot_date TEXT PRIMARY KEY,
        data JSON,
        created_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS predictions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        code            TEXT    NOT NULL,
        name            TEXT,
        framework       TEXT    NOT NULL,
        score_date      TEXT    NOT NULL,
        price_at_score  REAL,
        quant_score     REAL,
        total_score     REAL,
        weights_hash    TEXT,
        report_period   TEXT,
        outcome_30d     REAL,
        outcome_60d     REAL,
        outcome_90d     REAL,
        benchmark_30d   REAL,
        benchmark_60d   REAL,
        benchmark_90d   REAL,
        alpha_30d       REAL GENERATED ALWAYS AS (outcome_30d - benchmark_30d) VIRTUAL,
        alpha_60d       REAL GENERATED ALWAYS AS (outcome_60d - benchmark_60d) VIRTUAL,
        alpha_90d       REAL GENERATED ALWAYS AS (outcome_90d - benchmark_90d) VIRTUAL,
        estimate_flag   INTEGER DEFAULT 0,
        threshold_adjusted INTEGER DEFAULT 0,
        entry_signal    INTEGER,
        entry_signal_version TEXT,
        entry_signal_status TEXT,
        entry_signal_reason TEXT,
        entry_signal_source TEXT,
        entry_signal_fetched_at TEXT,
        l3_v2_signal    INTEGER,
        l3_v2_version   TEXT,
        l3_v2_status    TEXT,
        l3_v2_reason    TEXT,
        l3_v2_fetched_at TEXT,
        qualitative_snapshot_json TEXT,
        qualitative_sources_json TEXT,
        qualitative_mode TEXT,
        created_at      TEXT,
        UNIQUE(code, framework, score_date)
    )""")
    _ensure_columns(
        conn,
        "predictions",
        {
            "entry_signal": "INTEGER",
            "entry_signal_version": "TEXT",
            "entry_signal_status": "TEXT",
            "entry_signal_reason": "TEXT",
            "entry_signal_source": "TEXT",
            "entry_signal_fetched_at": "TEXT",
            "l3_v2_signal": "INTEGER",
            "l3_v2_version": "TEXT",
            "l3_v2_status": "TEXT",
            "l3_v2_reason": "TEXT",
            "l3_v2_fetched_at": "TEXT",
            "qualitative_snapshot_json": "TEXT",
            "qualitative_sources_json": "TEXT",
            "qualitative_mode": "TEXT",
        },
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_bars (
        code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        open REAL,
        high REAL,
        low REAL,
        close REAL NOT NULL,
        volume REAL,
        source TEXT NOT NULL,
        adjusted TEXT NOT NULL DEFAULT 'none',
        volume_unit TEXT NOT NULL DEFAULT 'unknown',
        fetched_at TEXT NOT NULL,
        quality_status TEXT NOT NULL,
        error_code TEXT,
        PRIMARY KEY (code, trade_date, adjusted)
    )""")
    _ensure_columns(
        conn,
        "daily_bars",
        {
            "volume_unit": "TEXT NOT NULL DEFAULT 'unknown'",
        },
    )
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_daily_bars_code_date
        ON daily_bars(code, trade_date DESC)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS market_data_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT NOT NULL,
        code TEXT NOT NULL,
        purpose TEXT NOT NULL,
        source TEXT NOT NULL,
        status TEXT NOT NULL,
        fallback_source TEXT,
        fallback_reason TEXT,
        error_code TEXT,
        error_message TEXT,
        fetched_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_market_data_audit_run_purpose
        ON market_data_audit(run_date, purpose, status)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS index_prices (
        symbol  TEXT NOT NULL,
        date    TEXT NOT NULL,
        close   REAL NOT NULL,
        PRIMARY KEY (symbol, date)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS qualitative_scores (
        code        TEXT NOT NULL,
        moat        INTEGER NOT NULL,
        market_pos  INTEGER NOT NULL,
        sentiment   INTEGER NOT NULL,
        scored_date TEXT NOT NULL,
        PRIMARY KEY (code, scored_date)
    )""")

    # v2 与 legacy qualitative_scores 物理隔离。context/result 原文用于每次读取时
    # 重新执行合同校验，避免只信任冗余分数字段或曾经通过的写入。
    conn.execute("""CREATE TABLE IF NOT EXISTS qualitative_scores_v2 (
        code          TEXT NOT NULL,
        name          TEXT NOT NULL,
        as_of_date    TEXT NOT NULL,
        input_hash    TEXT NOT NULL,
        model         TEXT NOT NULL,
        overall_status TEXT NOT NULL CHECK (overall_status IN ('scored', 'insufficient_data')),
        moat          INTEGER,
        market_pos    INTEGER,
        sentiment     INTEGER,
        context_json  TEXT NOT NULL,
        result_json   TEXT NOT NULL,
        scored_at     TEXT NOT NULL,
        PRIMARY KEY (code, as_of_date, input_hash, model)
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_qualitative_scores_v2_latest
        ON qualitative_scores_v2(code, as_of_date DESC, scored_at DESC)""")

    # 兼容旧库：qualitative_scores 曾经用 code 单列主键，无法支持漂移检测。
    pk_cols = conn.execute("SELECT COUNT(*) FROM pragma_table_info('qualitative_scores') WHERE pk > 0").fetchone()[0]
    if pk_cols == 1:
        conn.execute("DROP TABLE qualitative_scores")
        conn.execute("""CREATE TABLE qualitative_scores (
            code        TEXT NOT NULL,
            moat        INTEGER NOT NULL,
            market_pos  INTEGER NOT NULL,
            sentiment   INTEGER NOT NULL,
            scored_date TEXT NOT NULL,
            PRIMARY KEY (code, scored_date)
        )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS phase_milestones (
        phase        TEXT    NOT NULL,
        milestone    INTEGER NOT NULL,
        notified_at  TEXT    NOT NULL,
        PRIMARY KEY (phase, milestone)
    )""")
    conn.commit()
    return conn


def upsert_daily_bars(
    conn: sqlite3.Connection,
    code: str,
    bars: Any,
    source: str,
    adjusted: str = "none",
    volume_unit: str = "unknown",
    quality_status: str = "ok",
    fetched_at: str | None = None,
    error_code: str | None = None,
) -> int:
    """Upsert standardized date/open/high/low/close/volume bars."""
    if bars is None or getattr(bars, "empty", False):
        return 0
    fetched_at = fetched_at or datetime.now().isoformat()
    inserted = 0
    for _, row in bars.iterrows():
        trade_date = str(row["date"])[:10]
        cur = conn.execute(
            """INSERT INTO daily_bars
               (code, trade_date, open, high, low, close, volume, source,
                adjusted, volume_unit, fetched_at, quality_status, error_code)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(code, trade_date, adjusted) DO UPDATE SET
                 open=excluded.open,
                 high=excluded.high,
                 low=excluded.low,
                 close=excluded.close,
                 volume=excluded.volume,
                 source=excluded.source,
                 volume_unit=excluded.volume_unit,
                 fetched_at=excluded.fetched_at,
                 quality_status=excluded.quality_status,
                 error_code=excluded.error_code""",
            (
                code,
                trade_date,
                _optional_float(row, "open"),
                _optional_float(row, "high"),
                _optional_float(row, "low"),
                float(row["close"]),
                _optional_float(row, "volume"),
                source,
                adjusted,
                volume_unit,
                fetched_at,
                quality_status,
                error_code,
            ),
        )
        inserted += cur.rowcount
    return inserted


def _optional_float(row: Any, key: str) -> float | None:
    try:
        value = row[key]
    except Exception:
        return None
    if value is None:
        return None
    return float(value)


def load_daily_bars(
    conn: sqlite3.Connection,
    code: str,
    end_date: str,
    limit: int,
    adjusted: str = "none",
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT trade_date, close, volume, source, adjusted, volume_unit, fetched_at, quality_status
           FROM daily_bars
           WHERE code=? AND adjusted=? AND trade_date <= ?
           ORDER BY trade_date DESC
           LIMIT ?""",
        (code, adjusted, end_date, limit),
    ).fetchall()
    return [
        {
            "date": trade_date,
            "close": close,
            "volume": volume,
            "source": source,
            "adjusted": adjusted,
            "volume_unit": volume_unit,
            "fetched_at": fetched_at,
            "quality_status": quality_status,
        }
        for trade_date, close, volume, source, adjusted, volume_unit, fetched_at, quality_status in reversed(rows)
    ]


def latest_daily_close(
    conn: sqlite3.Connection,
    code: str,
    score_date: str,
    max_freshness_days: int = 5,
    adjusted: str = "none",
) -> tuple[float, int] | None:
    row = conn.execute(
        """SELECT trade_date, close FROM daily_bars
           WHERE code=? AND adjusted=? AND trade_date <= ?
           ORDER BY trade_date DESC
           LIMIT 1""",
        (code, adjusted, score_date),
    ).fetchone()
    if not row:
        return None
    trade_date, close = row
    freshness = (datetime.fromisoformat(score_date) - datetime.fromisoformat(trade_date)).days
    if freshness > max_freshness_days:
        return None
    return float(close), freshness


def insert_market_data_audit(
    conn: sqlite3.Connection,
    result: Any,
    purpose: str,
    code: str,
    run_date: str,
) -> None:
    conn.execute(
        """INSERT INTO market_data_audit
           (run_date, code, purpose, source, status, fallback_source,
            fallback_reason, error_code, error_message, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_date,
            code,
            purpose,
            getattr(result, "source", "unknown"),
            getattr(result, "status", "failed"),
            getattr(result, "fallback_source", None),
            getattr(result, "fallback_reason", None),
            getattr(result, "error_code", None),
            getattr(result, "error_message", None),
            getattr(result, "fetched_at", datetime.now().isoformat()),
        ),
    )


def latest_market_data_audit(
    conn: sqlite3.Connection,
    purpose: str,
    code: str,
    run_date: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT source, status, fallback_source, fallback_reason, error_code,
                  error_message, fetched_at
           FROM market_data_audit
           WHERE purpose=? AND code=? AND run_date=?
           ORDER BY id DESC
           LIMIT 1""",
        (purpose, code, run_date),
    ).fetchone()
    if not row:
        return None
    source, status, fallback_source, fallback_reason, error_code, error_message, fetched_at = row
    return {
        "source": source,
        "status": status,
        "fallback_source": fallback_source,
        "fallback_reason": fallback_reason,
        "error_code": error_code,
        "error_message": error_message,
        "fetched_at": fetched_at,
    }


def is_expired(updated_at_str: str, ttl_hours: int) -> bool:
    updated = datetime.fromisoformat(updated_at_str)
    return datetime.now() - updated > timedelta(hours=ttl_hours)


def get_fundamentals(code: str) -> dict | None:
    """获取基本面缓存。未命中或过期返回 None。"""
    conn = get_db()
    row = conn.execute(
        "SELECT name, industry, data, updated_at, ttl_hours FROM stock_fundamentals WHERE code=?",
        (code,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    name, industry, data, updated_at, ttl_hours = row
    if is_expired(updated_at, ttl_hours):
        return None
    result = json.loads(data)
    result["_cache_meta"] = {
        "code": code,
        "name": name,
        "industry": industry,
        "updated_at": updated_at[:16],
        "ttl_hours": ttl_hours,
    }
    return result


def set_fundamentals(
    code: str,
    name: str,
    industry: str,
    data_dict: dict,
    ttl: int | None = None,
    merge: bool = False,
) -> str:
    """写入基本面缓存。

    merge=True 时会在旧缓存上增量覆盖：新值为 None 的字段保留旧有效值，
    本次未返回的旧字段也继续保留，避免接口临时失败覆盖已有可用数据。
    """
    conn = get_db()
    if merge:
        existing_row = conn.execute(
            "SELECT name, industry, data FROM stock_fundamentals WHERE code=?", (code,)
        ).fetchone()
        if existing_row:
            old_name, old_industry, old_raw_data = existing_row
            old_data = json.loads(old_raw_data)
            merged = dict(old_data)
            for key, value in data_dict.items():
                if value is None and old_data.get(key) is not None:
                    continue
                merged[key] = value
            data_dict = merged
            if name == code and old_name:
                name = old_name
            if industry == "未知" and old_industry:
                industry = old_industry
    ttl_hours = ttl if ttl is not None else get_industry_ttl(industry)
    conn.execute(
        """INSERT OR REPLACE INTO stock_fundamentals
           (code, name, industry, data, updated_at, ttl_hours)
           VALUES (?,?,?,?,?,?)""",
        (
            code,
            name,
            industry,
            json.dumps(data_dict, ensure_ascii=False),
            datetime.now().isoformat(),
            ttl_hours,
        ),
    )
    conn.commit()
    conn.close()
    return f"已缓存 {name}({code}) 行业:{industry} TTL:{ttl_hours}h"


def list_codes() -> list[str]:
    """返回所有基本面缓存中的股票代码（含过期），按更新时间倒序。"""
    conn = get_db()
    rows = conn.execute("SELECT code FROM stock_fundamentals ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_spot_em_snapshot(date_str: str) -> list | None:
    """查询当日全量行情快照，不存在返回 None。"""
    conn = get_db()
    row = conn.execute("SELECT data FROM spot_em_snapshot WHERE snapshot_date=?", (date_str,)).fetchone()
    conn.close()
    return None if row is None else json.loads(row[0])


def get_recent_spot_em_snapshot(date_str: str, max_age_days: int = 3) -> tuple[str, list] | None:
    """查询 date_str 前 max_age_days 天内最近一份行情快照。"""
    cutoff = (datetime.fromisoformat(date_str) - timedelta(days=max_age_days)).date().isoformat()
    conn = get_db()
    row = conn.execute(
        """SELECT snapshot_date, data
           FROM spot_em_snapshot
           WHERE snapshot_date < ? AND snapshot_date >= ?
           ORDER BY snapshot_date DESC
           LIMIT 1""",
        (date_str, cutoff),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    snapshot_date, data = row
    return snapshot_date, json.loads(data)


def set_spot_em_snapshot(date_str: str, data: list) -> None:
    """写入当日全量行情快照。"""
    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO spot_em_snapshot (snapshot_date, data, created_at)
           VALUES (?,?,?)""",
        (date_str, json.dumps(data, ensure_ascii=False), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def cmd_get(args: list[str]) -> None:
    if len(args) < 1:
        print("CACHE_MISS")
        return
    result = get_fundamentals(args[0])
    if result is None:
        print("CACHE_MISS")
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_set(args: list[str]) -> None:
    if len(args) < 4:
        print("错误：需要参数 <代码> <名称> <行业> <JSON数据> [TTL]", file=sys.stderr)
        sys.exit(1)
    code, name, industry, data_str = args[0], args[1], args[2], args[3]
    ttl_hours = int(args[4]) if len(args) > 4 else get_industry_ttl(industry)
    try:
        data = json.loads(data_str)
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}", file=sys.stderr)
        sys.exit(1)
    print(set_fundamentals(code, name, industry, data, ttl=ttl_hours))


def cmd_list() -> None:
    conn = get_db()
    stocks = conn.execute(
        "SELECT code, name, industry, updated_at, ttl_hours FROM stock_fundamentals ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()

    valid_count = sum(1 for s in stocks if not is_expired(s[3], s[4]))
    print(f"=== 基本面缓存 ({valid_count}有效 / {len(stocks)}条) ===")
    for code, name, industry, updated_at, ttl_hours in stocks:
        status = "已过期" if is_expired(updated_at, ttl_hours) else "有效"
        print(f"  {name}({code}) [{industry}] 更新:{updated_at[:16]} TTL:{ttl_hours}h [{status}]")


def cmd_cleanup() -> None:
    conn = get_db()
    stocks = conn.execute("SELECT code, name, updated_at, ttl_hours FROM stock_fundamentals").fetchall()
    expired_names = [f"{s[1]}({s[0]})" for s in stocks if is_expired(s[2], s[3])]
    for code, _, updated_at, ttl_hours in stocks:
        if is_expired(updated_at, ttl_hours):
            conn.execute("DELETE FROM stock_fundamentals WHERE code=?", (code,))
    conn.commit()
    conn.close()

    if expired_names:
        print(f"已清除过期基本面缓存：{', '.join(expired_names)}")
    else:
        print("无过期缓存，无需清理")


def cmd_clear(args: list[str]) -> None:
    conn = get_db()
    if args:
        code = args[0]
        conn.execute("DELETE FROM stock_fundamentals WHERE code=?", (code,))
        conn.commit()
        print(f"已清除 {code} 的基本面缓存")
    else:
        conn.execute("DELETE FROM stock_fundamentals")
        conn.commit()
        print("已清除全部基本面缓存")
    conn.close()


COMMANDS: dict[str, Any] = {
    "get": cmd_get,
    "set": cmd_set,
    "list": cmd_list,
    "cleanup": cmd_cleanup,
    "clear": cmd_clear,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    remaining = sys.argv[2:]
    if cmd in ("list", "cleanup"):
        COMMANDS[cmd]()
    else:
        COMMANDS[cmd](remaining)
