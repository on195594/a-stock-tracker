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
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import akshare_provider
from cache import (get_recent_spot_em_snapshot, get_spot_em_snapshot, set_spot_em_snapshot,
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
    'bps':                ('每股净资产(元)',               'akshare'),
    'dividend_yield':     ('股息率(%)',                   'akshare'),
    'pb_percentile_10y':  ('PB历史10年分位(%)',           'computed'),
    'pb_hist_monthly':    ('PB月度历史序列(内部)',         'computed'),
    'float_to_total_ratio': ('流通/总市值比(%)',           'akshare'),
    'gross_margin':       ('毛利率(%)',                   'computed'),
    'report_period':      ('财报期',                       'akshare'),
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
    vals = [fv for v in series.tail(n) if (fv := parse_float(v)) is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


# ─── AKShare 数据获取函数（供 timed_call 包装）──────────────────────────────

def _fetch_info(code: str) -> dict | None:
    df = akshare_provider.stock_individual_info_em(code, API_TIMEOUT)
    if df is None or df.empty:
        return None
    return dict(zip(df['item'], df['value']))


def _fetch_financials(code: str) -> Any:
    return akshare_provider.stock_financial_abstract_ths(code)


def _fetch_dividends(code: str) -> Any:
    return akshare_provider.stock_history_dividend_detail(code)


def _fetch_fhps_detail(code: str) -> Any:
    return akshare_provider.stock_fhps_detail_em(code)


def _fetch_price_history(code: str, start_date: str, end_date: str) -> Any:
    return akshare_provider.stock_zh_a_hist(code, start_date, end_date)


def _fetch_spot_em() -> Any:
    return akshare_provider.stock_zh_a_spot_em()


_spot_em_failed_today: str | None = None


def _fetch_spot_em_safe(today: str) -> Any:
    """spot_em 带今日失败记忆：进程内只尝试一次，避免 N 只股票 N 次无效调用。"""
    global _spot_em_failed_today
    if _spot_em_failed_today == today:
        return ('ERROR', 'spot_em 今日已失败，跳过重复拉取')
    result = timed_call(_fetch_spot_em, timeout=SPOT_EM_TIMEOUT)
    if result is None:
        _spot_em_failed_today = today
        return ('ERROR', 'spot_em 返回空')
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


def _fetch_pb_hist_and_percentile(code: str) -> tuple[float | None, list[float] | None]:
    """调用 Baidu 估值接口，同时返回 (当前分位%, 月度历史序列)。

    分位基于最新月度 PB（非实时价），序列供 cmd_daily 计算实时分位用。
    序列约 731 个 float，序列化后约 6-8KB/股。
    """
    result = timed_call(
        akshare_provider.stock_zh_valuation_baidu,
        code, timeout=PB_TIMEOUT,
    )
    if isinstance(result, (str, tuple)) or result is None:
        return None, None
    df = result
    assert 'value' in df.columns, (
        f"stock_zh_valuation_baidu 列名变更，期望含 'value'，实际：{df.columns.tolist()}"
    )
    values = df['value'].dropna()
    if len(values) < 12:
        return None, None
    series = [round(float(v), 4) for v in values]
    current_pb = series[-1]
    pct = round(float((values < current_pb).sum() / len(values) * 100), 1)
    return pct, series


def _fetch_latest_close(code: str, today: str) -> tuple[float | None, str | None]:
    """spot_em 不可用时，退化为最近日线收盘价，供 PB 和股息率计算使用。"""
    end_date = today.replace("-", "")
    start_date = (datetime.fromisoformat(today) - timedelta(days=14)).strftime("%Y%m%d")
    result = timed_call(_fetch_price_history, code, start_date, end_date, timeout=API_TIMEOUT)
    if isinstance(result, str):
        return None, "日线价格API超时"
    if isinstance(result, tuple):
        return None, f"日线价格API失败: {result[1]}"
    if result is None or getattr(result, "empty", False):
        return None, "日线价格为空"

    close_col = "收盘" if "收盘" in result.columns else "close" if "close" in result.columns else None
    if close_col is None:
        return None, f"日线价格缺少收盘列: {result.columns.tolist()}"
    valid_closes = result[close_col].map(parse_float).dropna()
    if valid_closes.empty:
        return None, "日线收盘价无有效数据"
    close = valid_closes.iloc[-1]
    if close is None or close <= 0:
        return None, f"日线收盘价无效: {close}"
    return close, None


_FINANCIAL_INDUSTRY_SKIP = frozenset({'银行', '保险', '证券', '信托', '期货',
                                       '多元金融', '非银金融', '券商'})


def _compute_gross_margin(code: str, industry: str) -> float | None:
    """从新浪利润表计算近3年年报平均毛利率（金融行业返回 None）。

    金融行业的营业收入≠传统产品收入，毛利率无意义。
    建筑/钢铁/资源等非金融行业均正常计算（低毛利是行业特征，不跳过）。
    """
    if any(kw in industry for kw in _FINANCIAL_INDUSTRY_SKIP):
        return None
    result = timed_call(
        akshare_provider.stock_financial_report_sina,
        code,
        timeout=API_TIMEOUT,
    )
    if isinstance(result, (str, tuple)) or result is None:
        return None
    df = result
    required_cols = {'报告日', '营业收入', '营业成本'}
    if not required_cols.issubset(df.columns):
        return None
    annual = df[df['报告日'].astype(str).str.endswith('1231')].sort_values('报告日', ascending=False).head(3)
    rev = pd.to_numeric(annual['营业收入'], errors='coerce')
    cos = pd.to_numeric(annual['营业成本'], errors='coerce')
    valid_mask = rev > 0
    rev_v, cos_v = rev[valid_mask], cos[valid_mask]
    if len(rev_v) < 2:
        return None
    gm_series = (rev_v - cos_v) / rev_v * 100
    result_val = round(float(gm_series.mean()), 2)
    if not (-20 <= result_val <= 95):
        logger.warning(f"  ⚠️ {code} 毛利率 {result_val}% 超出合理范围，置 None")
        return None
    return result_val


def _detect_split_ratio(
    fhps_df: Any,
    latest_report_year: int | None,
) -> tuple[float, str | None]:
    """从 stock_fhps_detail_em 数据检测年报截止日后已实施的送转比例。

    触发条件：方案进度==实施分配 + 除权日在最新年报12-31之后 + 除权日<=今日 + 送转比例>0
    返回 (cumulative_split_ratio, latest_ex_date_str)。无送转时返回 (0.0, None)。
    """
    import pandas as pd
    if fhps_df is None or getattr(fhps_df, 'empty', True) or not latest_report_year:
        return 0.0, None
    ratio_col = '送转股份-送转总比例'
    date_col  = '除权除息日'
    prog_col  = '方案进度'
    if ratio_col not in fhps_df.columns or date_col not in fhps_df.columns:
        return 0.0, None
    df = fhps_df.copy()
    df['_ex_date'] = pd.to_datetime(df[date_col], errors='coerce')
    df['_ratio']   = pd.to_numeric(df[ratio_col], errors='coerce').fillna(0)
    cutoff = pd.Timestamp(f'{latest_report_year}-12-31')
    today  = pd.Timestamp.now().normalize()
    cond = (df['_ex_date'] > cutoff) & (df['_ex_date'] <= today) & (df['_ratio'] > 0)
    if prog_col in df.columns:
        cond = cond & (df[prog_col] == '实施分配')
    recent = df[cond]
    if recent.empty:
        return 0.0, None
    recent = recent.sort_values('_ex_date')
    latest_ex_date = recent.iloc[-1]['_ex_date'].strftime('%Y-%m-%d')
    factor = 1.0
    for _, row in recent.iterrows():
        factor *= (1.0 + float(row['_ratio']) / 10)
    return round(factor - 1.0, 6), latest_ex_date


# ─── 命令实现 ────────────────────────────────────────────────────────────────

def cmd_fetch(args: list[str]) -> None:
    if not args:
        print("错误：需要参数 <股票代码>", file=sys.stderr)
        sys.exit(1)
    code = args[0]

    print(f"[fetch] 开始获取 {code} 基本面数据...", flush=True)
    results: dict[str, Any]    = {}
    null_reasons: dict[str, str] = {}

    # ── Step 1：基本信息（名称 / 行业 / 当前价格）──
    # 东方财富接口可能不稳定；失败时用代码作为名称、行业置"未知"，继续抓财务数据
    print("  [1/7] 基本信息（名称/行业/价格）...", flush=True)
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
    print("  [2/7] 财务指标（同花顺年度）...", flush=True)
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
        if bps is not None:
            results['bps'] = bps
        else:
            null_reasons['bps'] = '每股净资产数据缺失'
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

    # ── Step 2.5：送转复权检测（修正 BPS 口径）──
    print("  [2.5/7] 送转复权检测...", flush=True)
    latest_report_year: int | None = None
    try:
        if results.get('report_period'):
            latest_report_year = int(str(results['report_period'])[:4])
    except (ValueError, TypeError):
        pass
    fhps_df = timed_call(_fetch_fhps_detail, code, timeout=API_TIMEOUT)
    if isinstance(fhps_df, (str, tuple)):
        logger.warning("  ⚠️ 送转数据获取失败，跳过复权检测")
    elif fhps_df is not None and bps is not None:
        split_ratio, split_ex_date = _detect_split_ratio(fhps_df, latest_report_year)
        if split_ratio > 0:
            bps_adj = round(bps / (1 + split_ratio), 4)
            logger.warning(
                "  ⚠️ 检测到送转（比例=%.4f，除权日=%s），BPS调整: %.4f→%.4f",
                split_ratio, split_ex_date, bps, bps_adj,
            )
            bps = bps_adj
            results['bps'] = bps_adj
        else:
            print("  ✅ 无送转，BPS无需调整")

    # ── Step 3：PE / PB / 最新价（stock_zh_a_spot_em 当日快照）──
    print("  [3/7] PE_TTM / PB / 最新价（spot_em 快照）...", flush=True)
    today    = datetime.now().strftime("%Y-%m-%d")
    snapshot = get_spot_em_snapshot(today)
    snapshot_source = today
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
                if not snapshot:
                    raise ValueError("spot_em 返回空快照")
                set_spot_em_snapshot(today, snapshot)
                logger.info("  [spot_em] 全量拉取完成，共 %d 只股票，已缓存至今日", len(snapshot))
            except Exception as e:
                logger.warning("  ⚠️ spot_em 数据解析失败: %s", e)
                for k in ('pe_ttm', 'pb'):
                    null_reasons[k] = f'spot_em 解析失败: {e}'
        if snapshot is None:
            recent_snapshot = get_recent_spot_em_snapshot(today, max_age_days=3)
            if recent_snapshot is not None:
                snapshot_source, snapshot = recent_snapshot
                logger.warning("  ⚠️ spot_em 使用最近缓存快照: %s", snapshot_source)
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
                null_reasons.pop('pe_ttm', None)
                print(f"  ✅ PE_TTM={results['pe_ttm']}")
            else:
                null_reasons['pe_ttm'] = f'spot_em PE无效(值={spot_pe})'
                logger.warning("  ⚠️ PE_TTM 无效: %s", null_reasons['pe_ttm'])
            if spot_pb and spot_pb > 0:
                results['pb'] = round(spot_pb, 2)
                null_reasons.pop('pb', None)
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
                null_reasons[k] = f'{snapshot_source} 快照中未找到 {code}'

    if current_price is None:
        latest_close, close_reason = _fetch_latest_close(code, today)
        if latest_close is not None:
            current_price = latest_close
            print(f"  ✅ 最新收盘价fallback={current_price}")
        else:
            logger.warning("  ⚠️ 最新收盘价fallback失败: %s", close_reason)
    if 'pb' not in results and current_price and bps and bps > 0:
        results['pb'] = round(current_price / bps, 2)
        null_reasons.pop('pb', None)
        print(f"  ✅ PB fallback={results['pb']}")

    # 跨源一致性校验：PE口径偏差超过25%时告警（可能是未处理的送转复权）
    _pe_from_market = results.get('pe_ttm')
    if (_pe_from_market and _pe_from_market > 0
            and eps and eps > 0 and current_price and current_price > 0):
        _pe_from_eps = current_price / eps
        _pe_deviation = abs(_pe_from_eps - _pe_from_market) / _pe_from_market
        if _pe_deviation > 0.25:
            logger.warning(
                "  ⚠️ PE口径偏差 %.0f%%：price/eps=%.2f vs spot_pe=%.2f，"
                "可能存在未处理送转复权，请检查 Step 2.5 日志",
                _pe_deviation * 100, _pe_from_eps, _pe_from_market,
            )

    # ── Step 4：股息率（分红历史 ÷ 当前价）──
    print("  [4/7] 计算股息率...", flush=True)
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
            null_reasons['dividend_yield'] = dy_reason or '未知原因'
            logger.warning("  ⚠️ 股息率无法计算: %s", dy_reason)

    # ── Step 4.5：毛利率（新浪利润表，近3年年报均值）──
    print("  [4.5/7] 计算毛利率（新浪利润表）...", flush=True)
    gm = _compute_gross_margin(code, industry)
    if gm is not None:
        results['gross_margin'] = gm
        print(f"  ✅ 毛利率={gm}%")
    else:
        null_reasons['gross_margin'] = '金融行业跳过或接口失败或数据不足'
        logger.warning("  ⚠️ 毛利率获取失败")

    # ── Step 5：PB 历史10年分位 + 月度序列（Baidu，允许30s）──
    print("  [5/7] PB 历史分位 + 月度序列...", flush=True)
    pct, series = _fetch_pb_hist_and_percentile(code)
    if pct is not None:
        assert series is not None
        results['pb_percentile_10y'] = pct
        results['pb_hist_monthly'] = series
        print(f"  ✅ PB历史10年分位={pct}%，序列 {len(series)} 个数据点")
    else:
        null_reasons['pb_percentile_10y'] = 'PB历史数据不足或接口失败'
        null_reasons['pb_hist_monthly'] = '同上'
        logger.warning("  ⚠️ PB历史分位获取失败")

    # ── Step 6：写入 cache ──
    fetched_count = sum(1 for k, v in results.items() if v is not None)
    if fetched_count == 0:
        raise RuntimeError(f"{code} 未获取到任何有效字段，跳过缓存写入")

    print("  [6/7] 写入缓存...", flush=True)
    cache_data = {k: results.get(k) for k in FIELDS if k in results or k in null_reasons}
    msg = set_fundamentals(code, name, industry, cache_data, merge=True)
    print(f"  ✅ {msg}")

    # ── 汇总 ──
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
