#!/usr/bin/env python3
"""
A股投研数据缓存管理器
用法：
  cache.py check <代码>                                  # 【推荐】一次性检查分析结论+基本面缓存状态
  cache.py get <代码>                                    # 获取基本面缓存数据
  cache.py set <代码> <名称> <行业> <JSON> [TTL]            # 写入基本面数据（TTL自动按行业推断）
  cache.py get-analysis <代码>                           # 获取今日分析结论缓存
  cache.py set-analysis <代码> [得分]                    # 从stdin写入今日分析结论（可选：同时写入得分）
  cache.py set-score <代码> <分数>                       # 写入今日综合得分（/80，向后兼容）
  cache.py set-score-breakdown <代码> '<JSON>'           # 写入今日各维度分项得分
  cache.py set-flag <代码> <yellow|red> <原因>            # 记录红黄线预警
  cache.py clear-flag <代码>                             # 清除指定股票所有预警标记
  cache.py add-holding <代码> <成本价> [股数] [备注]       # 添加/更新持仓记录
  cache.py close-holding <代码> <卖出价> [日期]           # 记录平仓（保留历史，用于评分验证）
  cache.py holdings                                     # 显示在仓持股 + 已平仓历史（含盈亏%）
  cache.py remove-holding <代码>                        # 彻底删除持仓记录（慎用）
  cache.py watchlist                                    # 显示所有有效缓存股票的关键指标摘要
  cache.py list                                         # 查看所有缓存（含过期）
  cache.py cleanup                                      # 清除所有过期缓存条目
  cache.py clear [代码]                                  # 清除全部或指定股票缓存

check 命令输出格式（供 SKILL.md 解析）：
  ANALYSIS_HIT   → 今日分析结论已缓存，直接输出结论，终止分析流程
  FUNDAMENTALS_HIT → 基本面数据有缓存，跳过基本面搜索，只查实时行情
  FULL_MISS      → 完全未命中，执行完整分析流程

TTL 按行业自动推断（set 命令未指定 TTL 时）：
  银行/保险/券商/公用事业/水电 → 72h（季报数据稳定）
  消费/白酒/食品/零售         → 12h（情绪驱动，变化快）
  其余行业                   → 24h（默认）
"""

import sqlite3
import json
import sys
import os
from datetime import datetime, timedelta
from typing import Any, Callable

DB_PATH = os.path.expanduser('~/a-stock-tracker/tracker.db')

# ② 行业 → TTL 映射（关键词匹配，越靠前优先级越高）
INDUSTRY_TTL_MAP = [
    # 168h：季报驱动、基本面变化慢（对齐 weekly cron 7天刷新周期）
    (['银行', '保险', '券商', '国有大行', '股份制银行', '城商行', '农商行',
      '水电', '公用事业', '电网', '水务', '燃气', '高速'], 168),
    # 48h：情绪/渠道敏感，但 daily 最多两天不刷新
    (['白酒', '消费', '食品', '零售', '饮料', '乳制品'], 48),
    # 168h：默认，对齐 weekly cron（行业未知时财务数据也是季度更新的）
]


def get_industry_ttl(industry: str) -> int:
    """根据行业名称推断合适的 TTL（小时）"""
    for keywords, ttl in INDUSTRY_TTL_MAP:
        if any(kw in industry for kw in keywords):
            return ttl
    return 168


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS stock_fundamentals (
        code TEXT PRIMARY KEY,
        name TEXT,
        industry TEXT,
        data JSON,
        updated_at TEXT,
        ttl_hours INTEGER DEFAULT 168
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS analysis_results (
        code TEXT,
        date TEXT,
        name TEXT,
        result TEXT,
        created_at TEXT,
        score INTEGER,
        flags TEXT,
        score_breakdown TEXT,
        data_date TEXT,
        PRIMARY KEY (code, date)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS holdings (
        code TEXT PRIMARY KEY,
        name TEXT,
        cost_price REAL,
        shares INTEGER,
        buy_date TEXT,
        buy_score INTEGER,
        stop_loss_15 REAL,
        stop_loss_20 REAL,
        notes TEXT,
        updated_at TEXT,
        exit_price REAL,
        exit_date TEXT
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS spot_em_snapshot (
        snapshot_date TEXT PRIMARY KEY,
        data JSON,
        created_at TEXT
    )''')
    # 迁移：为旧表添加缺失字段
    for col_def in [
        'ALTER TABLE analysis_results ADD COLUMN name TEXT',
        'ALTER TABLE analysis_results ADD COLUMN score INTEGER',
        'ALTER TABLE analysis_results ADD COLUMN flags TEXT',
        'ALTER TABLE analysis_results ADD COLUMN score_breakdown TEXT',
        'ALTER TABLE analysis_results ADD COLUMN data_date TEXT',
        'ALTER TABLE holdings ADD COLUMN exit_price REAL',
        'ALTER TABLE holdings ADD COLUMN exit_date TEXT',
    ]:
        try:
            conn.execute(col_def)
            conn.commit()
        except Exception:
            pass
    conn.execute('''CREATE TABLE IF NOT EXISTS predictions (
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
        created_at      TEXT,
        UNIQUE(code, framework, score_date)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS index_prices (
        symbol  TEXT NOT NULL,
        date    TEXT NOT NULL,
        close   REAL NOT NULL,
        PRIMARY KEY (symbol, date)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS qualitative_scores (
        code        TEXT NOT NULL,
        moat        INTEGER NOT NULL,
        market_pos  INTEGER NOT NULL,
        sentiment   INTEGER NOT NULL,
        scored_date TEXT NOT NULL,
        PRIMARY KEY (code, scored_date)
    )''')
    # 迁移：旧表 PK=(code) 单列 → 新表 PK=(code, scored_date) 复合
    # 检测：旧表 pk 列数 = 1，新表 = 2；DROP 重建即可（旧数据无漂移比较价值）
    pk_cols = conn.execute(
        "SELECT COUNT(*) FROM pragma_table_info('qualitative_scores') WHERE pk > 0"
    ).fetchone()[0]
    if pk_cols == 1:
        conn.execute("DROP TABLE qualitative_scores")
        conn.execute('''CREATE TABLE qualitative_scores (
            code        TEXT NOT NULL,
            moat        INTEGER NOT NULL,
            market_pos  INTEGER NOT NULL,
            sentiment   INTEGER NOT NULL,
            scored_date TEXT NOT NULL,
            PRIMARY KEY (code, scored_date)
        )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS phase_milestones (
        phase        TEXT    NOT NULL,
        milestone    INTEGER NOT NULL,
        notified_at  TEXT    NOT NULL,
        PRIMARY KEY (phase, milestone)
    )''')
    conn.commit()
    return conn


def get_fundamentals(code: str) -> dict | None:
    """获取基本面缓存。未命中或过期返回 None；命中返回含 _cache_meta 的 dict。"""
    conn = get_db()
    row = conn.execute(
        'SELECT name, industry, data, updated_at, ttl_hours FROM stock_fundamentals WHERE code=?',
        (code,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    name, industry, data, updated_at, ttl_hours = row
    if is_expired(updated_at, ttl_hours):
        return None
    result = json.loads(data)
    result['_cache_meta'] = {
        'code': code, 'name': name, 'industry': industry,
        'updated_at': updated_at[:16], 'ttl_hours': ttl_hours
    }
    return result


def set_fundamentals(code: str, name: str, industry: str,
                     data_dict: dict, ttl: int | None = None,
                     merge: bool = False) -> str:
    """写入基本面缓存，ttl=None 时按行业自动推断。

    merge=True：新值为 None 的字段，保留旧缓存中的有效值（防止接口临时失败覆盖有效旧值）。
    返回状态消息。
    """
    ttl_hours = ttl if ttl is not None else get_industry_ttl(industry)
    conn = get_db()
    if merge:
        existing_row = conn.execute(
            'SELECT data FROM stock_fundamentals WHERE code=?', (code,)
        ).fetchone()
        if existing_row:
            old_data = json.loads(existing_row[0])
            data_dict = {
                k: (old_data[k] if v is None and old_data.get(k) is not None else v)
                for k, v in data_dict.items()
            }
    conn.execute(
        '''INSERT OR REPLACE INTO stock_fundamentals
           (code, name, industry, data, updated_at, ttl_hours)
           VALUES (?,?,?,?,?,?)''',
        (code, name, industry, json.dumps(data_dict, ensure_ascii=False),
         datetime.now().isoformat(), ttl_hours)
    )
    conn.commit()
    conn.close()
    return f"已缓存 {name}({code}) 行业:{industry} TTL:{ttl_hours}h"


def list_codes() -> list[str]:
    """返回所有基本面缓存中的股票代码（含过期），按更新时间倒序。"""
    conn = get_db()
    rows = conn.execute(
        'SELECT code FROM stock_fundamentals ORDER BY updated_at DESC'
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_spot_em_snapshot(date_str: str) -> list | None:
    """查询当日全量行情快照，不存在返回 None"""
    conn = get_db()
    row = conn.execute(
        'SELECT data FROM spot_em_snapshot WHERE snapshot_date=?', (date_str,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return json.loads(row[0])


def set_spot_em_snapshot(date_str: str, data: list) -> None:
    """写入当日全量行情快照（list of dict）"""
    conn = get_db()
    conn.execute(
        '''INSERT OR REPLACE INTO spot_em_snapshot (snapshot_date, data, created_at)
           VALUES (?,?,?)''',
        (date_str, json.dumps(data, ensure_ascii=False), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def is_expired(updated_at_str, ttl_hours):
    updated = datetime.fromisoformat(updated_at_str)
    return datetime.now() - updated > timedelta(hours=ttl_hours)


def cmd_check(args):
    """一次性检查分析结论+基本面缓存，输出状态码+内容"""
    if len(args) < 1:
        print("FULL_MISS")
        return
    code = args[0]
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()

    # 优先检查今日分析结论
    analysis_row = conn.execute(
        'SELECT result, created_at, name FROM analysis_results WHERE code=? AND date=?',
        (code, today)
    ).fetchone()
    if analysis_row:
        result, created_at, name = analysis_row
        name_str = f"({name})" if name else ""
        print(f"ANALYSIS_HIT {code}{name_str} [{created_at[:16]}]")
        print(result)
        conn.close()
        return

    # 再检查基本面缓存
    fund_row = conn.execute(
        'SELECT name, industry, data, updated_at, ttl_hours FROM stock_fundamentals WHERE code=?',
        (code,)
    ).fetchone()
    conn.close()

    if fund_row:
        name, industry, data, updated_at, ttl_hours = fund_row
        if not is_expired(updated_at, ttl_hours):
            result = json.loads(data)
            result['_cache_meta'] = {
                'code': code, 'name': name, 'industry': industry,
                'updated_at': updated_at[:16], 'ttl_hours': ttl_hours
            }
            print(f"FUNDAMENTALS_HIT {code}({name}) [{industry}] 更新:{updated_at[:16]}")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

    print("FULL_MISS")


def cmd_get(args):
    """获取基本面缓存，未命中或过期返回 CACHE_MISS"""
    if len(args) < 1:
        print("CACHE_MISS")
        return
    code = args[0]
    conn = get_db()
    row = conn.execute(
        'SELECT name, industry, data, updated_at, ttl_hours FROM stock_fundamentals WHERE code=?',
        (code,)
    ).fetchone()
    conn.close()
    if not row:
        print("CACHE_MISS")
        return
    name, industry, data, updated_at, ttl_hours = row
    if is_expired(updated_at, ttl_hours):
        print("CACHE_MISS")
        return
    result = json.loads(data)
    result['_cache_meta'] = {
        'code': code, 'name': name, 'industry': industry,
        'updated_at': updated_at[:16], 'ttl_hours': ttl_hours
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_set(args):
    """② 写入基本面数据。TTL 未指定时按行业自动推断。"""
    if len(args) < 4:
        print("错误：需要参数 <代码> <名称> <行业> <JSON数据> [TTL]", file=sys.stderr)
        sys.exit(1)
    code, name, industry, data_str = args[0], args[1], args[2], args[3]
    # TTL：显式指定 > 行业推断
    ttl_hours = int(args[4]) if len(args) > 4 else get_industry_ttl(industry)
    try:
        data = json.loads(data_str)
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}", file=sys.stderr)
        sys.exit(1)
    conn = get_db()
    conn.execute(
        '''INSERT OR REPLACE INTO stock_fundamentals
           (code, name, industry, data, updated_at, ttl_hours)
           VALUES (?,?,?,?,?,?)''',
        (code, name, industry, json.dumps(data, ensure_ascii=False),
         datetime.now().isoformat(), ttl_hours)
    )
    conn.commit()
    conn.close()
    print(f"已缓存 {name}({code}) 行业:{industry} TTL:{ttl_hours}h")


def cmd_get_analysis(args):
    """获取今日分析结论缓存，未命中返回 CACHE_MISS"""
    if len(args) < 1:
        print("CACHE_MISS")
        return
    code = args[0]
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    row = conn.execute(
        'SELECT result, created_at FROM analysis_results WHERE code=? AND date=?',
        (code, today)
    ).fetchone()
    conn.close()
    if not row:
        print("CACHE_MISS")
        return
    result, created_at = row
    print(f"[缓存命中 {created_at[:16]}]\n{result}")


def cmd_set_analysis(args):
    """从stdin读取分析结论并缓存（当日有效）。

    用法：
      python3 cache.py set-analysis <代码> [得分] << 'EOF'
      <分析文本>
      EOF

    参数：
      <代码>   股票代码（必填）
      [得分]   综合得分整数（可选）。若提供，result 与 score 一并写入，
               无需再单独调用 set-score。若不提供，score 保持 NULL。
    """
    if len(args) < 1:
        print("错误：需要参数 <代码>", file=sys.stderr)
        sys.exit(1)
    code = args[0]

    # 解析可选得分参数：args[1] 存在且为纯数字时视为得分
    score = None
    if len(args) > 1 and args[1].lstrip('-').isdigit():
        score = int(args[1])

    # 自动从基本面缓存查询股票名称
    conn_tmp = get_db()
    row = conn_tmp.execute(
        'SELECT name FROM stock_fundamentals WHERE code=?', (code,)
    ).fetchone()
    conn_tmp.close()
    name = row[0] if row else None

    result = sys.stdin.read().strip()
    if not result:
        print("错误：stdin为空", file=sys.stderr)
        sys.exit(1)
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    conn.execute(
        '''INSERT OR REPLACE INTO analysis_results (code, date, name, result, created_at, score)
           VALUES (?,?,?,?,?,?)''',
        (code, today, name, result, datetime.now().isoformat(), score)
    )
    conn.commit()
    conn.close()
    name_str = f"({name})" if name else ""
    score_str = f" 得分:{score}/80" if score is not None else ""
    print(f"分析结论已缓存：{code}{name_str} ({today}){score_str}")


def cmd_set_score(args):
    """③ 写入今日综合得分（/100）。需先执行 set-analysis。"""
    if len(args) < 2:
        print("错误：需要参数 <代码> <分数>", file=sys.stderr)
        sys.exit(1)
    code = args[0]
    try:
        score = int(args[1])
    except ValueError:
        print("错误：分数必须为整数", file=sys.stderr)
        sys.exit(1)
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    updated = conn.execute(
        'UPDATE analysis_results SET score=? WHERE code=? AND date=?',
        (score, code, today)
    ).rowcount
    conn.commit()
    conn.close()
    if updated:
        print(f"得分已记录：{code} → {score}/100")
    else:
        print(f"未找到今日分析记录，请先执行 set-analysis（代码：{code}）", file=sys.stderr)
        sys.exit(1)


def cmd_set_score_breakdown(args):
    """写入今日各维度分项得分（JSON格式）。需先执行 set-analysis。"""
    if len(args) < 2:
        print("错误：需要参数 <代码> '<JSON>'", file=sys.stderr)
        sys.exit(1)
    code = args[0]
    try:
        breakdown = json.loads(args[1])
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}", file=sys.stderr)
        sys.exit(1)
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    updated = conn.execute(
        'UPDATE analysis_results SET score_breakdown=? WHERE code=? AND date=?',
        (json.dumps(breakdown, ensure_ascii=False), code, today)
    ).rowcount
    conn.commit()
    conn.close()
    if updated:
        print(f"分项得分已记录：{code} → {breakdown}")
    else:
        print(f"未找到今日分析记录，请先执行 set-analysis（代码：{code}）", file=sys.stderr)
        sys.exit(1)


def cmd_add_holding(args):
    """添加或更新持仓记录，自动计算15%/20%止损价。"""
    if len(args) < 2:
        print("错误：需要参数 <代码> <成本价> [股数] [备注]", file=sys.stderr)
        sys.exit(1)
    code = args[0]
    try:
        cost_price = float(args[1])
    except ValueError:
        print("错误：成本价必须为数字", file=sys.stderr)
        sys.exit(1)
    shares = int(args[2]) if len(args) > 2 and args[2].isdigit() else None
    notes = args[3] if len(args) > 3 else (args[2] if len(args) > 2 and not args[2].isdigit() else None)

    stop_loss_15 = round(cost_price * 0.85, 3)
    stop_loss_20 = round(cost_price * 0.80, 3)
    today = datetime.now().strftime('%Y-%m-%d')

    # 查询今日分析得分
    conn = get_db()
    score_row = conn.execute(
        'SELECT score, name FROM analysis_results WHERE code=? ORDER BY date DESC LIMIT 1',
        (code,)
    ).fetchone()
    buy_score = score_row[0] if score_row else None
    # 查询股票名称
    name_row = conn.execute(
        'SELECT name FROM stock_fundamentals WHERE code=?', (code,)
    ).fetchone()
    name = name_row[0] if name_row else (score_row[1] if score_row and score_row[1] else None)

    conn.execute(
        '''INSERT OR REPLACE INTO holdings
           (code, name, cost_price, shares, buy_date, buy_score,
            stop_loss_15, stop_loss_20, notes, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (code, name, cost_price, shares, today, buy_score,
         stop_loss_15, stop_loss_20, notes, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

    name_str = f"({name})" if name else ""
    shares_str = f" {shares}股" if shares else ""
    score_str = f" 买入得分:{buy_score}/80" if buy_score else ""
    print(f"持仓已记录：{code}{name_str} 成本:{cost_price}{shares_str}{score_str}")
    print(f"  止损15%: {stop_loss_15}  止损20%: {stop_loss_20}")


def cmd_holdings():
    """显示持仓列表：在仓持股 + 已平仓历史（含盈亏%，用于验证评分准确性）"""
    conn = get_db()
    rows = conn.execute(
        '''SELECT code, name, cost_price, shares, buy_date, buy_score,
                  stop_loss_15, stop_loss_20, notes, exit_price, exit_date
           FROM holdings ORDER BY exit_date IS NULL DESC, buy_date DESC'''
    ).fetchall()
    conn.close()

    if not rows:
        print("暂无持仓记录")
        return

    open_rows   = [r for r in rows if r[10] is None]   # exit_date IS NULL
    closed_rows = [r for r in rows if r[10] is not None]

    if open_rows:
        print(f"\n{'─'*80}")
        print(f"  在仓持股（{len(open_rows)} 只）")
        print(f"{'─'*80}")
        print(f"  {'股票':<14} {'成本价':>7} {'股数':>6} {'止损15%':>8} {'止损20%':>8} {'得分':>4} {'买入日期':<11} 备注")
        print(f"  {'─'*74}")
        for r in open_rows:
            code, name, cost, shares, buy_date, score, sl15, sl20, notes, _, _ = r
            label = f"{name}({code})" if name else code
            shares_str = str(shares) if shares else "─"
            score_str  = str(score) if score else "─"
            notes_str  = notes or "─"
            print(f"  {label:<14} {cost:>7.3f} {shares_str:>6} {sl15:>8.3f} {sl20:>8.3f} {score_str:>4} {buy_date:<11} {notes_str}")

    if closed_rows:
        print(f"\n{'─'*80}")
        print(f"  已平仓历史（{len(closed_rows)} 只）")
        print(f"{'─'*80}")
        print(f"  {'股票':<14} {'成本价':>7} {'卖出价':>7} {'盈亏%':>7} {'得分':>4} {'买入':>11} {'卖出':>11} 备注")
        print(f"  {'─'*74}")
        for r in closed_rows:
            code, name, cost, shares, buy_date, score, _, _, notes, exit_price, exit_date = r
            label      = f"{name}({code})" if name else code
            score_str  = str(score) if score else "─"
            notes_str  = notes or "─"
            pnl_pct    = round((exit_price - cost) / cost * 100, 1)
            pnl_str    = f"{pnl_pct:+.1f}%"
            print(f"  {label:<14} {cost:>7.3f} {exit_price:>7.3f} {pnl_str:>7} {score_str:>4} {buy_date:>11} {exit_date:>11} {notes_str}")

        # 统计：平均盈亏、胜率，帮助验证评分系统有效性
        # r[9]=exit_price, r[2]=cost_price（与上方 SELECT 列顺序一致）
        pnl_list = [round((r[9] - r[2]) / r[2] * 100, 1) for r in closed_rows]
        win_rate = round(sum(1 for p in pnl_list if p > 0) / len(pnl_list) * 100)
        avg_pnl  = round(sum(pnl_list) / len(pnl_list), 1)
        print(f"\n  已平仓统计：胜率 {win_rate}% | 平均盈亏 {avg_pnl:+.1f}% | 共 {len(closed_rows)} 笔")


def cmd_close_holding(args):
    """记录平仓（保留历史用于评分验证）。用法：close-holding <代码> <卖出价> [日期]"""
    if len(args) < 2:
        print("错误：需要参数 <代码> <卖出价> [日期，默认今天]", file=sys.stderr)
        sys.exit(1)
    code = args[0]
    try:
        exit_price = float(args[1])
    except ValueError:
        print("错误：卖出价必须为数字", file=sys.stderr)
        sys.exit(1)
    exit_date = args[2] if len(args) > 2 else datetime.now().strftime('%Y-%m-%d')

    conn = get_db()
    row = conn.execute(
        'SELECT cost_price, name, buy_score FROM holdings WHERE code=? AND exit_date IS NULL',
        (code,)
    ).fetchone()
    if not row:
        print(f"错误：未找到 {code} 的在仓记录", file=sys.stderr)
        sys.exit(1)
    cost_price, name, buy_score = row

    conn.execute(
        'UPDATE holdings SET exit_price=?, exit_date=?, updated_at=? WHERE code=?',
        (exit_price, exit_date, datetime.now().isoformat(), code)
    )
    conn.commit()
    conn.close()

    pnl_pct   = round((exit_price - cost_price) / cost_price * 100, 1)
    name_str  = f"({name})" if name else ""
    score_str = f" 买入得分:{buy_score}/80" if buy_score else ""
    print(f"平仓已记录：{code}{name_str}{score_str}")
    print(f"  成本:{cost_price} → 卖出:{exit_price} | 盈亏:{pnl_pct:+.1f}%")


def cmd_remove_holding(args):
    """删除持仓记录"""
    if len(args) < 1:
        print("错误：需要参数 <代码>", file=sys.stderr)
        sys.exit(1)
    code = args[0]
    conn = get_db()
    deleted = conn.execute('DELETE FROM holdings WHERE code=?', (code,)).rowcount
    conn.commit()
    conn.close()
    if deleted:
        print(f"已移除持仓：{code}")
    else:
        print(f"未找到持仓记录：{code}", file=sys.stderr)
        sys.exit(1)


def cmd_set_flag(args):
    """⑤ 记录红黄线预警（追加，不覆盖历史）"""
    if len(args) < 3:
        print("错误：需要参数 <代码> <yellow|red> <原因>", file=sys.stderr)
        sys.exit(1)
    code, level, reason = args[0], args[1], ' '.join(args[2:])
    if level not in ('yellow', 'red'):
        print("错误：level 必须为 yellow 或 red", file=sys.stderr)
        sys.exit(1)
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    row = conn.execute(
        'SELECT flags FROM analysis_results WHERE code=? AND date=?',
        (code, today)
    ).fetchone()
    if not row:
        print(f"未找到今日分析记录，请先执行 set-analysis（代码：{code}）", file=sys.stderr)
        conn.close()
        sys.exit(1)
    existing = json.loads(row[0]) if row[0] else []
    existing.append({'level': level, 'reason': reason, 'date': today})
    conn.execute(
        'UPDATE analysis_results SET flags=? WHERE code=? AND date=?',
        (json.dumps(existing, ensure_ascii=False), code, today)
    )
    conn.commit()
    conn.close()
    icon = '🔴' if level == 'red' else '⚠️'
    print(f"预警已记录：{code} {icon} {reason}")


def cmd_clear_flag(args):
    """⑤ 清除指定股票今日所有预警标记"""
    if len(args) < 1:
        print("错误：需要参数 <代码>", file=sys.stderr)
        sys.exit(1)
    code = args[0]
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    conn.execute(
        'UPDATE analysis_results SET flags=NULL WHERE code=? AND date=?',
        (code, today)
    )
    conn.commit()
    conn.close()
    print(f"已清除 {code} 的今日预警标记")


def cmd_watchlist():
    """显示所有有效缓存股票的关键指标摘要表（按综合得分排序）"""
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    stocks = conn.execute(
        '''SELECT f.code, f.name, f.industry, f.data, f.updated_at, f.ttl_hours,
                  a.created_at as analysis_time, a.score, a.flags
           FROM stock_fundamentals f
           LEFT JOIN analysis_results a ON f.code = a.code AND a.date = ?
           ORDER BY a.score DESC NULLS LAST, a.created_at DESC NULLS LAST, f.updated_at DESC''',
        (today,)
    ).fetchall()
    conn.close()

    valid = [(r, json.loads(r[3])) for r in stocks if not is_expired(r[4], r[5])]
    if not valid:
        print("暂无有效缓存股票")
        return

    header = f"{'股票':<14} {'行业':<16} {'得分':>4} {'PE':>6} {'PB':>5} {'股息率':>7} {'ROE':>7}  预警  分析"
    print(header)
    print("─" * 75)
    for row, data in valid:
        code, name, industry = row[0], row[1], row[2]
        analysis_time, score, flags_raw = row[6], row[7], row[8]
        pe  = str(data.get('pe_ttm', '─'))
        pb  = str(data.get('pb', '─'))
        div = (str(data.get('dividend_yield', '─')) + '%') if data.get('dividend_yield') else '─'
        roe = (str(data.get('roe_3y_avg', '─')) + '%') if data.get('roe_3y_avg') else '─'
        score_str = str(score) if score is not None else '─'
        # 预警标记
        flags = json.loads(flags_raw) if flags_raw else []
        if any(f['level'] == 'red' for f in flags):
            flag_str = '🔴'
        elif any(f['level'] == 'yellow' for f in flags):
            flag_str = '⚠️'
        else:
            flag_str = '─'
        tag = "✅今日" if analysis_time else "─"
        label = f"{name}({code})"
        print(f"{label:<14} {industry:<16} {score_str:>4} {pe:>6} {pb:>5} {div:>7} {roe:>7}  {flag_str:<4}  {tag}")


def cmd_list():
    """列出所有缓存内容（含过期）"""
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    stocks = conn.execute(
        'SELECT code, name, industry, updated_at, ttl_hours FROM stock_fundamentals ORDER BY updated_at DESC'
    ).fetchall()
    analyses = conn.execute(
        '''SELECT a.code, a.date, a.created_at, COALESCE(a.name, f.name, a.code) as display_name,
                  a.score, a.flags, a.score_breakdown
           FROM analysis_results a
           LEFT JOIN stock_fundamentals f ON a.code = f.code
           ORDER BY a.created_at DESC LIMIT 20'''
    ).fetchall()
    conn.close()

    valid_count = sum(1 for s in stocks if not is_expired(s[3], s[4]))
    print(f"=== 基本面缓存 ({valid_count}有效 / {len(stocks)}条) ===")
    for s in stocks:
        code, name, industry, updated_at, ttl_hours = s
        expired = is_expired(updated_at, ttl_hours)
        status = "⚠️ 已过期" if expired else "✅ 有效"
        print(f"  {name}({code}) [{industry}] 更新:{updated_at[:16]} TTL:{ttl_hours}h [{status}]")

    today_count = sum(1 for a in analyses if a[1] == today)
    print(f"\n=== 分析结论缓存 ({today_count}今日有效 / 近{len(analyses)}条) ===")
    for a in analyses:
        code, date, created_at, display_name, score, flags_raw, breakdown_raw = a
        status = "✅ 今日有效" if date == today else f"⚠️ 过期({date})"
        score_str = f" 得分:{score}" if score is not None else ""
        breakdown_str = " 📊分项" if breakdown_raw else ""
        flags = json.loads(flags_raw) if flags_raw else []
        flag_icons = ''.join('🔴' if f['level'] == 'red' else '⚠️' for f in flags)
        print(f"  {display_name}({code}) [{status}]{score_str}{breakdown_str}{flag_icons} 创建:{created_at[:16]}")


def cmd_cleanup():
    """清除所有过期的缓存条目"""
    conn = get_db()
    stocks = conn.execute(
        'SELECT code, name, updated_at, ttl_hours FROM stock_fundamentals'
    ).fetchall()
    today = datetime.now().strftime('%Y-%m-%d')

    expired_names = [f"{s[1]}({s[0]})" for s in stocks if is_expired(s[2], s[3])]
    for s in stocks:
        if is_expired(s[2], s[3]):
            conn.execute('DELETE FROM stock_fundamentals WHERE code=?', (s[0],))

    old = conn.execute('DELETE FROM analysis_results WHERE date != ?', (today,))
    old_count = old.rowcount
    conn.commit()
    conn.close()

    if expired_names:
        print(f"已清除过期基本面缓存：{', '.join(expired_names)}")
    if old_count:
        print(f"已清除 {old_count} 条历史分析结论")
    if not expired_names and not old_count:
        print("无过期缓存，无需清理")


def cmd_clear(args):
    """清除缓存。不带参数=清全部，带代码=清指定股票"""
    conn = get_db()
    if args:
        code = args[0]
        conn.execute('DELETE FROM stock_fundamentals WHERE code=?', (code,))
        conn.execute('DELETE FROM analysis_results WHERE code=?', (code,))
        conn.commit()
        print(f"已清除 {code} 的所有缓存")
    else:
        conn.execute('DELETE FROM stock_fundamentals')
        conn.execute('DELETE FROM analysis_results')
        conn.commit()
        print("已清除全部缓存")
    conn.close()


# 供外部 import 的别名（fetcher.py 等可直接 from cache import check）
check = cmd_check

COMMANDS: dict[str, Callable[..., Any]] = {
    'check': cmd_check,
    'get': cmd_get,
    'set': cmd_set,
    'get-analysis': cmd_get_analysis,
    'set-analysis': cmd_set_analysis,
    'set-score': cmd_set_score,
    'set-score-breakdown': cmd_set_score_breakdown,
    'set-flag': cmd_set_flag,
    'clear-flag': cmd_clear_flag,
    'add-holding': cmd_add_holding,
    'close-holding': cmd_close_holding,
    'holdings': cmd_holdings,
    'remove-holding': cmd_remove_holding,
    'watchlist': cmd_watchlist,
    'list': cmd_list,
    'cleanup': cmd_cleanup,
    'clear': cmd_clear,
}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    remaining = sys.argv[2:]
    if cmd in ('list', 'watchlist', 'cleanup', 'holdings'):
        COMMANDS[cmd]()
    else:
        COMMANDS[cmd](remaining)
