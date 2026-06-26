# Tushare Capability Probe

Run date: 2026-06-26
Token configured: yes
Date range: 2025-06-26 -> 2026-06-26
Volume unit assumption: `hand`

## Decision

Write Gate: PASS
Capability Checks: DEGRADED
Production Decision: DAILY_WRITES_ALLOWED
Index/Calendar Dependent Jobs: HOLD
Reason: daily close cross-check passed

| Check | Blocking | Status | Source | Rows | Latency ms | Error | Message |
|---|---|---|---|---:|---:|---|---|
| daily 600036 (600036.SH) | yes | ok | tushare.daily | 242 | 1225.9 |  |  |
| daily 000001 (000001.SZ) | yes | ok | tushare.daily | 242 | 616.0 |  |  |
| daily 002594 (002594.SZ) | yes | ok | tushare.daily | 242 | 592.9 |  |  |
| index_daily 000300 (000300.SH) | no | failed | tushare.index_daily | 0 | 5903.7 | RATE_LIMITED | 抱歉，您访问接口(index_daily)频率超限(1次/分钟)，具体频次详情：https://tushare.pro/document/1?doc_id=108。 |
| trade_cal SSE | no | failed | tushare.trade_cal | 0 | 7280.3 | RATE_LIMITED | 抱歉，您访问接口(trade_cal)频率超限(1次/分钟)，具体频次详情：https://tushare.pro/document/1?doc_id=108。 |

## Close Cross-Check

Close cross-check: PASS

| Code | Trade Date | Tushare Close | Reference Trade Date | Reference Close | Reference Source | Diff | Status |
|---|---|---:|---|---:|---|---:|---|
| 600036 | 2026-06-25 | 36.2300 | 2026-06-25 | 36.2300 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 000001 | 2026-06-25 | 10.4200 | 2026-06-25 | 10.4200 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 002594 | 2026-06-25 | 82.2000 | 2026-06-25 | 82.2000 | baostock.query_history_k_data_plus | 0.000% | PASS |

## Write Gate Result

PASS

Daily production writes require Write Gate PASS. Capability DEGRADED/BLOCKED means index/calendar-dependent jobs remain on hold.
