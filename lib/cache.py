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
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Any

DB_PATH = os.path.expanduser("~/a-stock-tracker/tracker.db")

# 行业 → TTL 映射（关键词匹配，越靠前优先级越高）
INDUSTRY_TTL_MAP = [
    # 168h：季报驱动、基本面变化慢（对齐 weekly cron 7 天刷新周期）
    (["银行", "保险", "券商", "国有大行", "股份制银行", "城商行", "农商行",
      "水电", "公用事业", "电网", "水务", "燃气", "高速"], 168),
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
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


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
        created_at      TEXT,
        UNIQUE(code, framework, score_date)
    )""")
    _ensure_columns(conn, "predictions", {
        "entry_signal": "INTEGER",
        "entry_signal_version": "TEXT",
    })
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

    # 兼容旧库：qualitative_scores 曾经用 code 单列主键，无法支持漂移检测。
    pk_cols = conn.execute(
        "SELECT COUNT(*) FROM pragma_table_info('qualitative_scores') WHERE pk > 0"
    ).fetchone()[0]
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
    rows = conn.execute(
        "SELECT code FROM stock_fundamentals ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_spot_em_snapshot(date_str: str) -> list | None:
    """查询当日全量行情快照，不存在返回 None。"""
    conn = get_db()
    row = conn.execute(
        "SELECT data FROM spot_em_snapshot WHERE snapshot_date=?", (date_str,)
    ).fetchone()
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
    stocks = conn.execute(
        "SELECT code, name, updated_at, ttl_hours FROM stock_fundamentals"
    ).fetchall()
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
