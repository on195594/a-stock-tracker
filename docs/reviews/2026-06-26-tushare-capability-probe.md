# Tushare Capability Probe

Run date: 2026-06-26
Token configured: yes
Date range: 2025-06-26 -> 2026-06-26
Volume unit assumption: `hand`

## Decision

Write Gate: PASS
Capability Checks: PASS
Production Decision: DAILY_WRITES_ALLOWED
Index/Calendar Dependent Jobs: ALLOWED
Reason: daily close cross-check passed

| Check | Blocking | Status | Source | Rows | Latency ms | Error | Message |
|---|---|---|---|---:|---:|---|---|
| daily 600036 (600036.SH) | yes | ok | tushare.daily | 243 | 2231.1 |  |  |
| daily 000001 (000001.SZ) | yes | ok | tushare.daily | 243 | 922.0 |  |  |
| daily 002594 (002594.SZ) | yes | ok | tushare.daily | 243 | 1041.5 |  |  |
| index_daily 000300 (000300.SH) | no | ok | tushare.index_daily | 5215 | 4085.3 |  |  |
| trade_cal SSE | no | ok | tushare.trade_cal | 243 | 601.7 |  |  |

## Close Cross-Check

Close cross-check: PASS

| Code | Trade Date | Tushare Close | Reference Trade Date | Reference Close | Reference Source | Diff | Status |
|---|---|---:|---|---:|---|---:|---|
| 600036 | 2026-06-26 | 36.0000 | 2026-06-26 | 36.0000 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 000001 | 2026-06-26 | 10.2300 | 2026-06-26 | 10.2300 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 002594 | 2026-06-26 | 78.2000 | 2026-06-26 | 78.2000 | baostock.query_history_k_data_plus | 0.000% | PASS |

## Write Gate Result

PASS

Daily production writes require Write Gate PASS. Capability DEGRADED/BLOCKED means index/calendar-dependent jobs remain on hold.
