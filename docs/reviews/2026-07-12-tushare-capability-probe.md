# Tushare Capability Probe

Run date: 2026-07-12
Token configured: yes
Date range: 2025-07-12 -> 2026-07-12
Volume unit assumption: `hand`

## Decision

Write Gate: PASS
Capability Checks: PASS
Production Decision: DAILY_WRITES_ALLOWED
Index/Calendar Dependent Jobs: ALLOWED
Reason: daily close cross-check passed

| Check | Blocking | Status | Source | Rows | Latency ms | Error | Message |
|---|---|---|---|---:|---:|---|---|
| daily 600036 (600036.SH) | yes | ok | tushare.daily | 241 | 956.9 |  |  |
| daily 000001 (000001.SZ) | yes | ok | tushare.daily | 241 | 789.0 |  |  |
| daily 002594 (002594.SZ) | yes | ok | tushare.daily | 241 | 809.1 |  |  |
| index_daily 000300 (000300.SH) | no | ok | tushare.index_daily | 5946 | 2025.1 |  |  |
| trade_cal SSE | no | ok | tushare.trade_cal | 241 | 497.5 |  |  |

## Close Cross-Check

Close cross-check: PASS

| Code | Trade Date | Tushare Close | Reference Trade Date | Reference Close | Reference Source | Diff | Status |
|---|---|---:|---|---:|---|---:|---|
| 600036 | 2026-07-10 | 36.8800 | 2026-07-10 | 36.8800 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 000001 | 2026-07-10 | 10.4500 | 2026-07-10 | 10.4500 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 002594 | 2026-07-10 | 90.0000 | 2026-07-10 | 90.0000 | baostock.query_history_k_data_plus | 0.000% | PASS |

## Write Gate Result

PASS

Daily production writes require Write Gate PASS. Capability DEGRADED/BLOCKED means index/calendar-dependent jobs remain on hold.
