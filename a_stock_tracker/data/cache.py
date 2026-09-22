#!/usr/bin/env python3
"""
A 股选股管道的 SQLite 缓存层。

保留的职责：
- stock_fundamentals 基本面缓存
- 通用行情和审计表 schema

旧 skill 时代的分析结论缓存、预警和持仓 CLI 已移除。
"""

import json
import re as _re
import sqlite3
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
