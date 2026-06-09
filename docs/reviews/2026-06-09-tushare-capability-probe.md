# Tushare Capability Probe

Run date: 2026-06-09
Token configured: yes
Date range: 2025-06-09 -> 2026-06-09
Volume unit assumption: `hand`

| Check | Status | Source | Rows | Latency ms | Error | Message |
|---|---|---|---:|---:|---|---|
| daily 600036 (600036.SH) | ok | tushare.daily | 243 | 2008.2 |  |  |
| daily 000001 (000001.SZ) | ok | tushare.daily | 243 | 542.7 |  |  |
| daily 002594 (002594.SZ) | ok | tushare.daily | 243 | 579.0 |  |  |
| index_daily 000300 (000300.SH) | failed | tushare.index_daily | 0 | 663.5 | RATE_LIMITED | 抱歉，您访问接口(index_daily)频率超限(1次/小时)，具体频次详情：https://tushare.pro/document/1?doc_id=108。 |
| trade_cal SSE | failed | tushare.trade_cal | 0 | 526.9 | RATE_LIMITED | 抱歉，您访问接口(trade_cal)频率超限(1次/小时)，具体频次详情：https://tushare.pro/document/1?doc_id=108。 |

## Close Cross-Check

Close cross-check: PASS

| Code | Trade Date | Tushare Close | Reference Close | Reference Source | Diff | Status |
|---|---|---:|---:|---|---:|---|
| 600036 | 2026-06-08 | 38.5000 | 38.5400 | akshare.stock_zh_a_hist | 0.104% | PASS |
| 000001 | 2026-06-08 | 11.0300 | 11.0300 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 002594 | 2026-06-08 | 91.1900 | 91.1900 | baostock.query_history_k_data_plus | 0.000% | PASS |

## Result

FAIL

Close-price cross-check must be PASS before enabling production writes.
