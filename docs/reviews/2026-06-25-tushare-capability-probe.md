# Tushare Capability Probe

Run date: 2026-06-25
Token configured: yes
Date range: 2025-06-25 -> 2026-06-25
Volume unit assumption: `hand`

| Check | Status | Source | Rows | Latency ms | Error | Message |
|---|---|---|---:|---:|---|---|
| daily 600036 (600036.SH) | ok | tushare.daily | 243 | 1056.3 |  |  |
| daily 000001 (000001.SZ) | ok | tushare.daily | 243 | 842.5 |  |  |
| daily 002594 (002594.SZ) | ok | tushare.daily | 243 | 529.3 |  |  |
| index_daily 000300 (000300.SH) | failed | tushare.index_daily | 0 | 5967.0 | RATE_LIMITED | 抱歉，您访问接口(index_daily)频率超限(1次/分钟)，具体频次详情：https://tushare.pro/document/1?doc_id=108。 |
| trade_cal SSE | failed | tushare.trade_cal | 0 | 5090.5 | RATE_LIMITED | 抱歉，您访问接口(trade_cal)频率超限(1次/分钟)，具体频次详情：https://tushare.pro/document/1?doc_id=108。 |

## Close Cross-Check

Close cross-check: MANUAL_REQUIRED

| Code | Trade Date | Tushare Close | Reference Trade Date | Reference Close | Reference Source | Diff | Status |
|---|---|---:|---|---:|---|---:|---|
| 600036 | 2026-06-25 | 36.2300 | 2026-06-24 | 36.7600 | baostock.query_history_k_data_plus |  | DATE_MISMATCH |
| 000001 | 2026-06-25 | 10.4200 | 2026-06-24 | 10.5100 | baostock.query_history_k_data_plus |  | DATE_MISMATCH |
| 002594 | 2026-06-25 | 82.2000 | 2026-06-24 | 83.3000 | baostock.query_history_k_data_plus |  | DATE_MISMATCH |

## Result

FAIL

Close-price cross-check must be PASS before enabling production writes.
