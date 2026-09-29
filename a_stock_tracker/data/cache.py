#!/usr/bin/env python3
"""
通用 TuShare 数据链的 SQLite 缓存层。

保留的职责：
- stock_fundamentals 基本面缓存
- 通用行情和审计表 schema

旧 skill 时代的分析结论缓存、预警和持仓 CLI 已移除。
"""

import re as _re
import sqlite3
from datetime import datetime
from typing import Any

from a_stock_tracker.config import DB_PATH

_SAFE_IDENTIFIER_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
