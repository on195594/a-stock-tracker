#!/usr/bin/env python3
"""
A股基本面数据自动获取工具
用法：
  fetcher.py fetch <股票代码>   获取并缓存基本面数据
  fetcher.py check <股票代码>   验证缓存数据质量
  fetcher.py batch              批量更新 watchlist 所有股票
"""

import sys
import os
import logging
from typing import Any, Callable
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cache import (get_spot_em_snapshot, set_spot_em_snapshot,
                   get_fundamentals, set_fundamentals, list_codes)

logger = logging.getLogger(__name__)

API_TIMEOUT      = 10   # 每个 AKShare 调用的超时秒数
PB_TIMEOUT       = 30   # PB 历史分位计算允许更长时间（需拉历史 PB 序列）
SPOT_EM_TIMEOUT  = 180  # 全量快照允许3分钟

# 字段注册表：key → (显示标签, 数据来源层)
#   akshare   = 从 API 直接获取/计算
#   computed  = 从历史数据自行构造
#   web       = 需要 WebSearch 补充（fetcher 不负责）
FIELDS = {
    'pe_ttm':             ('PE_TTM（市盈率）',         'akshare'),
    'pb':                 ('PB（市净率）',               'akshare'),
    'roe_3y_avg':         ('ROE近3年均值(%)',            'akshare'),
    'net_profit_growth':  ('净利润增速近3年均值(%)',      'akshare'),
    'debt_ratio':         ('资产负债率(%)',               'akshare'),
    'dividend_yield':     ('股息率(%)',                   'akshare'),
    'pb_percentile_10y':  ('PB历史10年分位(%)',           'computed'),
    'float_to_total_ratio': ('流通/总市值比(%)',           'akshare'),
    'gross_margin':       ('毛利率(%)',                   'web'),
    'nim':                ('净息差（银行）',               'web'),
    'npl_ratio':          ('不良贷款率（银行）',           'web'),
    'provision_coverage': ('拨备覆盖率（银行）',           'web'),
}


# ─── 工具函数 ────────────────────────────────────────────────────────────────

def timed_call(fn: Callable[..., Any], *args: Any,
               timeout: int = API_TIMEOUT, **kwargs: Any) -> Any | str | tuple[str, str]:
    """在独立线程中执行 fn，超时返回 'TIMEOUT'，异常返回 ('ERROR', msg)"""
    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            return 'TIMEOUT'
        except Exception as e:
            return ('ERROR', str(e))


def timed_call_with_retry(fn: Callable[..., Any], *args: Any,
                          timeout: int = API_TIMEOUT, max_retries: int = 3,
                          **kwargs: Any) -> Any | str | tuple[str, str]:
    """带指数退避的重试版 timed_call，专用于已知不稳定的 API"""
    import time
    for attempt in range(max_retries):
        result = timed_call(fn, *args, timeout=timeout, **kwargs)
        if isinstance(result, (str, tuple)):
            if attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.info("  [retry] 第 %d 次失败，%ds 后重试...", attempt + 1, wait)
                time.sleep(wait)
            continue
        return result  # 成功则返回
    return result  # 所有重试耗尽，返回最后一次结果


def parse_float(val: Any, default: float | None = None) -> float | None:
    """将财务数据中的各种格式转为 float，无效返回 default"""
    if val is None:
        return default
    s = str(val).replace('%', '').strip()
    if s in ('--', '-', 'None', 'nan', 'False', 'True', ''):
        return default
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


def avg_of(series: Any, n: int) -> float | None:
    """取 series 最后 n 行，计算非 None 值的均值，保留2位小数"""
    vals = [parse_float(v) for v in series.tail(n) if parse_float(v) is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


# ─── AKShare 数据获取函数（供 timed_call 包装）──────────────────────────────

def _fetch_info(code: str) -> dict | None:
    import akshare as ak
    df = ak.stock_individual_info_em(symbol=code, timeout=API_TIMEOUT)
    if df is None or df.empty:
        return None
    return dict(zip(df['item'], df['value']))


def _fetch_financials(code: str) -> Any:
    import akshare as ak
    return ak.stock_financial_abstract_ths(symbol=code, indicator='按年度')


def _fetch_dividends(code: str) -> Any:
    import akshare as ak
    return ak.stock_history_dividend_detail(symbol=code, indicator='分红')


def _fetch_price_history(code: str, start_date: str, end_date: str) -> Any:
    import akshare as ak
    return ak.stock_zh_a_hist(
        symbol=code, period='daily',
        start_date=start_date, end_date=end_date, adjust=''
    )


def _fetch_spot_em() -> Any:
    import akshare as ak
    return ak.stock_zh_a_spot_em()


_spot_em_failed_today: str | None = None


def _fetch_spot_em_safe(today: str) -> Any:
    """spot_em 带今日失败记忆：进程内只尝试一次，避免 N 只股票 N 次无效调用。"""
    global _spot_em_failed_today
    if _spot_em_failed_today == today:
        return None
    result = timed_call(_fetch_spot_em, timeout=SPOT_EM_TIMEOUT)
    if isinstance(result, (str, tuple)):  # 'TIMEOUT' or ('ERROR', msg)
        _spot_em_failed_today = today
    return result


# ─── 计算辅助函数 ────────────────────────────────────────────────────────────

def compute_dividend_yield(div_df: Any,
                           current_price: float | None) -> tuple[float | None, str | None]:
    """从分红历史估算股息率。

    注意：AKShare 的 '派息' 字段单位是 元/10股（中国惯例），需除以10换算每股。
    仅使用"实施"状态（排除"预案"），以除权除息日为时间基准取近12个月。
    若近12个月内无实施分红，返回 (None, '超过12个月无分红') 而非用历史数据估算。
    返回值：成功时返回 float，失败时返回 (None, reason_str)。
    """
    import pandas as pd
    if div_df is None or div_df.empty or not current_price or current_price <= 0:
        return None, '无分红数据或价格不可用'
    div_df = div_df.copy()

    # 只取已实施的分红
    if '进度' in div_df.columns:
        div_df = div_df[div_df['进度'] == '实施']
    if div_df.empty:
        return None, '无已实施分红记录'

    # 以除权除息日为准（比公告日期更准确），不可用时回退到公告日期
    date_col = '除权除息日' if '除权除息日' in div_df.columns else '公告日期'
    div_df['_date'] = pd.to_datetime(div_df[date_col], errors='coerce')
    div_df = div_df.dropna(subset=['_date'])
    if div_df.empty:
        return None, '分红日期字段无法解析'

    # 取近12个月（366天）内的实施分红；超过12个月则视为不分红，返回 null
    cutoff = datetime.now() - timedelta(days=366)
    recent = div_df[div_df['_date'] >= cutoff]
    if recent.empty:
        last_date = div_df['_date'].max().strftime('%Y-%m-%d')
        return None, f'超过12个月无分红（最近一次：{last_date}）'

    total_per_10 = pd.to_numeric(recent['派息'], errors='coerce').dropna().sum()
    if total_per_10 > 0:
        per_share = float(total_per_10) / 10   # 元/10股 → 元/股
        return round(per_share / float(current_price) * 100, 2), None
    return None, '派息为0'


def _fetch_pb_percentile(code: str) -> float | None:
    """获取当前 PB 在过去 10 年历史分布中的百分位（0.0–100.0）。
    使用 ak.stock_zh_valuation_baidu，约 731 行月度数据。
    数据不足 12 个月时返回 None。负 PB（资不抵债）正常参与计算。
    """
    import akshare as ak
    result = timed_call(
        ak.stock_zh_valuation_baidu,
        code, timeout=PB_TIMEOUT,
        indicator='市净率', period='近十年',
    )
    if isinstance(result, (str, tuple)) or result is None:
        return None
    df = result
    assert 'value' in df.columns, f"stock_zh_valuation_baidu 列名变更，期望含 'value'，实际：{df.columns.tolist()}"
    values = df['value'].dropna()
    if len(values) < 12:
        return None
    current_pb = float(values.iloc[-1])
    pct = float((values < current_pb).sum() / len(values) * 100)
    return round(pct, 1)


# ─── 命令实现 ────────────────────────────────────────────────────────────────

def cmd_fetch(args: list[str]) -> None:
    if not args:
        print("错误：需要参数 <股票代码>", file=sys.stderr)
        sys.exit(1)
    code = args[0]

    print(f"[fetch] 开始获取 {code} 基本面数据...", flush=True)
    results    = {}
    null_reasons = {}

    # ── Step 1：基本信息（名称 / 行业 / 当前价格）──
    # 东方财富接口可能不稳定；失败时用代码作为名称、行业置"未知"，继续抓财务数据
    print("  [1/6] 基本信息（名称/行业/价格）...", flush=True)
    info = timed_call(_fetch_info, code, timeout=15)
    if isinstance(info, str) or isinstance(info, tuple) or not info:
        err = info[1] if isinstance(info, tuple) else (info or '接口超时/无数据')
        logger.warning("  ⚠️ 基本信息获取失败，使用降级默认值：%s", err)
        name = code
        industry = '未知'
        current_price = None
    else:
        name          = info.get('股票简称', code)
        industry      = info.get('行业', '未知')
        current_price = parse_float(info.get('最新'))
        print(f"  ✅ {name}({code}) | 行业: {industry} | 当前价: {current_price}")

    # ── Step 2：主要财务指标（ROE / 增速 / 负债率 / EPS / BPS）──
    print("  [2/6] 财务指标（同花顺年度）...", flush=True)
    fin_df = timed_call_with_retry(_fetch_financials, code, timeout=API_TIMEOUT)
    if isinstance(fin_df, str):   # 'TIMEOUT'
        reason = '财务API超时'
        for k in ('roe_3y_avg', 'net_profit_growth', 'debt_ratio'):
            null_reasons[k] = reason
        eps = bps = None
        logger.warning("  ⚠️ 财务指标 获取失败（同花顺限速，已重试 3 次）")
    elif fin_df is None or isinstance(fin_df, tuple):
        reason = fin_df[1] if isinstance(fin_df, tuple) else '财务API失败'
        for k in ('roe_3y_avg', 'net_profit_growth', 'debt_ratio'):
            null_reasons[k] = reason
        eps = bps = None
        logger.warning("  ⚠️ 财务指标 获取失败（同花顺限速，已重试 3 次）")
    else:
        results['roe_3y_avg']        = avg_of(fin_df['净资产收益率'], 3)
        results['net_profit_growth'] = avg_of(fin_df['净利润同比增长率'], 3)
        results['debt_ratio']        = parse_float(fin_df['资产负债率'].iloc[-1])
        eps = parse_float(fin_df['基本每股收益'].iloc[-1])
        bps = parse_float(fin_df['每股净资产'].iloc[-1])
        # 记录最新财报所属期（供 predictions.report_period 使用）
        try:
            report_period_raw = fin_df.sort_values('报告期', ascending=False).iloc[0]['报告期']
            results['report_period'] = str(report_period_raw)[:10]
        except Exception:
            pass

        for k in ('roe_3y_avg', 'net_profit_growth', 'debt_ratio'):
            if results.get(k) is None:
                null_reasons[k] = '数据含缺失值'
        print(f"  ✅ ROE3y={results.get('roe_3y_avg')}% | "
              f"净利增速={results.get('net_profit_growth')}% | "
              f"负债率={results.get('debt_ratio')}% | EPS={eps} | BPS={bps}")

    # ── Step 3：PE / PB / 最新价（stock_zh_a_spot_em 当日快照）──
    print("  [3/6] PE_TTM / PB / 最新价（spot_em 快照）...", flush=True)
    today    = datetime.now().strftime("%Y-%m-%d")
    snapshot = get_spot_em_snapshot(today)
    if snapshot is None:
        spot_result = _fetch_spot_em_safe(today)
        if isinstance(spot_result, str) or isinstance(spot_result, tuple):
            # 'TIMEOUT' or ('ERROR', msg)
            err = spot_result[1] if isinstance(spot_result, tuple) else spot_result
            logger.warning("  ⚠️ spot_em 全量拉取失败: %s", err)
            for k in ('pe_ttm', 'pb'):
                null_reasons[k] = f'spot_em 拉取失败: {err}'
        else:
            try:
                snapshot = spot_result.to_dict('records')
                set_spot_em_snapshot(today, snapshot)
                logger.info("  [spot_em] 全量拉取完成，共 %d 只股票，已缓存至今日", len(snapshot))
            except Exception as e:
                logger.warning("  ⚠️ spot_em 数据解析失败: %s", e)
                for k in ('pe_ttm', 'pb'):
                    null_reasons[k] = f'spot_em 解析失败: {e}'
    else:
        logger.info("  [spot_em] 命中今日快照，跳过全量拉取")

    if snapshot is not None:
        row = next((r for r in snapshot if r['代码'] == code), None)
        if row:
            spot_pe    = parse_float(row.get('市盈率-动态'))
            spot_pb    = parse_float(row.get('市净率'))
            spot_price = parse_float(row.get('最新价'))
            if spot_pe and spot_pe > 0:
                results['pe_ttm'] = round(spot_pe, 2)
                print(f"  ✅ PE_TTM={results['pe_ttm']}")
            else:
                null_reasons['pe_ttm'] = f'spot_em PE无效(值={spot_pe})'
                logger.warning("  ⚠️ PE_TTM 无效: %s", null_reasons['pe_ttm'])
            if spot_pb and spot_pb > 0:
                results['pb'] = round(spot_pb, 2)
                print(f"  ✅ PB={results['pb']}")
            else:
                null_reasons['pb'] = f'spot_em PB无效(值={spot_pb})'
                logger.warning("  ⚠️ PB 无效: %s", null_reasons['pb'])
            if spot_price and spot_price > 0:
                current_price = spot_price
                print(f"  ✅ 最新价={current_price}")
            total_cap   = parse_float(row.get('总市值'))
            float_cap   = parse_float(row.get('流通市值'))
            if total_cap and total_cap > 0 and float_cap is not None:
                results['float_to_total_ratio'] = round(float_cap / total_cap * 100, 1)
                print(f"  ✅ 流通/总市值比={results['float_to_total_ratio']}%")
            else:
                null_reasons['float_to_total_ratio'] = 'spot_em 市值字段缺失'
                logger.warning("  ⚠️ 流通/总市值比 无法计算")
        else:
            logger.warning("  ⚠️ 未在快照中找到 %s", code)
            for k in ('pe_ttm', 'pb', 'float_to_total_ratio'):
                null_reasons[k] = f'快照中未找到 {code}'

    # ── Step 4：股息率（分红历史 ÷ 当前价）──
    print("  [4/6] 计算股息率...", flush=True)
    div_df = timed_call(_fetch_dividends, code, timeout=API_TIMEOUT)
    if isinstance(div_df, str):   # 'TIMEOUT'
        null_reasons['dividend_yield'] = '分红API超时'
        logger.warning("  ⚠️ 超时")
    elif div_df is None or isinstance(div_df, tuple):
        null_reasons['dividend_yield'] = '无分红数据'
        logger.warning("  ⚠️ 无数据")
    else:
        dy, dy_reason = compute_dividend_yield(div_df, current_price)
        if dy is not None:
            results['dividend_yield'] = dy
            print(f"  ✅ 股息率={dy}%")
        else:
            null_reasons['dividend_yield'] = dy_reason
            logger.warning("  ⚠️ 股息率无法计算: %s", dy_reason)

    # ── Step 5：PB 历史10年分位（调 Baidu 估值接口，允许30s）──
    print("  [5/6] 计算 PB 历史10年分位...", flush=True)
    pct = _fetch_pb_percentile(code)
    if pct is not None:
        results['pb_percentile_10y'] = pct
        print(f"  ✅ PB历史10年分位={pct}%")
    else:
        null_reasons['pb_percentile_10y'] = 'PB历史数据不足（<12个月）或接口失败'
        logger.warning("  ⚠️ PB历史分位获取失败，跳过")

    # ── Step 6：写入 cache ──
    print("  [6/6] 写入缓存...", flush=True)
    cache_data = {k: results.get(k) for k in FIELDS if k in results or k in null_reasons}
    msg = set_fundamentals(code, name, industry, cache_data)
    print(f"  ✅ {msg}")

    # ── 汇总 ──
    fetched_count = sum(1 for k, v in results.items() if v is not None)
    print(f"\n=== {name}({code}) 完成 | "
          f"获取: {fetched_count}字段 | null: {len(null_reasons)}字段 | "
          f"{datetime.now().strftime('%H:%M:%S')} ===")
    if null_reasons:
        print("null 字段原因：")
        for k, reason in null_reasons.items():
            label = FIELDS.get(k, (k,))[0]
            print(f"  - {label}: {reason}")


def cmd_check(args: list[str]) -> None:
    if not args:
        print("错误：需要参数 <股票代码>", file=sys.stderr)
        sys.exit(1)
    code = args[0]

    data = get_fundamentals(code)
    if data is None:
        print(f"未找到 {code} 的缓存数据，请先执行: fetcher.py fetch {code}")
        return

    meta     = data.pop('_cache_meta', {})
    name     = meta.get('name', code)
    industry = meta.get('industry', '未知')
    updated  = meta.get('updated_at', '未知')
    ttl      = meta.get('ttl_hours', '?')

    src_icon = {'akshare': '📊 AKShare', 'computed': '🔢 自动计算', 'web': '🔍 需WebSearch'}

    print(f"\n=== {name}({code}) 数据质量报告 ===")
    print(f"行业: {industry} | 更新: {updated} | TTL: {ttl}h\n")
    print(f"  {'字段':<24} {'值':>10}  {'来源':<16} 状态")
    print("  " + "─" * 62)

    core_total = core_fetched = 0
    for key, (label, src) in FIELDS.items():
        val     = data.get(key)
        icon    = src_icon.get(src, src)
        val_str = str(val) if val is not None else '─'

        if src in ('akshare', 'computed'):
            core_total += 1
            if val is not None:
                core_fetched += 1
                status = '✅'
            else:
                status = '⚠️ null'
        else:
            status = '─ (web补充)' if val is None else '✅'

        print(f"  {label:<24} {val_str:>10}  {icon:<16} {status}")

    print("  " + "─" * 62)
    score = round(core_fetched / core_total * 100) if core_total else 0
    web_filled = sum(1 for k, (_, s) in FIELDS.items() if s == 'web' and data.get(k) is not None)
    web_total  = sum(1 for _, (_, s) in FIELDS.items() if s == 'web')
    print(f"\n  核心字段: {core_fetched}/{core_total} | "
          f"Web补充: {web_filled}/{web_total} | "
          f"数据完整性: {score}%")

    missing_core = [FIELDS[k][0] for k, (_, s) in FIELDS.items()
                    if s in ('akshare', 'computed') and data.get(k) is None]
    if missing_core:
        print(f"  待补充: {', '.join(missing_core)}")


def cmd_batch(args: list[str]) -> None:
    codes = list_codes()
    # 去重保序（list_codes 已按更新时间排序，保序去重防万一）
    seen, unique = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    if not unique:
        print("watchlist 为空")
        return

    print(f"=== 批量更新 {len(unique)} 支股票 ===\n")
    summary = []
    for i, code in enumerate(unique, 1):
        print(f"[{i}/{len(unique)}] ── {code} ──")
        try:
            cmd_fetch([code])
            summary.append((code, '✅'))
        except (SystemExit, BaseException) as e:
            summary.append((code, f'❌ {e}'))
        print()

    print("=== 批量汇总 ===")
    for code, status in summary:
        print(f"  {code}: {status}")
    ok = sum(1 for _, s in summary if s == '✅')
    print(f"完成: {ok}/{len(summary)}")


COMMANDS = {
    'fetch': cmd_fetch,
    'check': cmd_check,
    'batch': cmd_batch,
}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0)
    COMMANDS[sys.argv[1]](sys.argv[2:])
