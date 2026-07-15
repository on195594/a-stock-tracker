# Tushare Capability Probe

Run date: 2026-07-15
Token configured: yes
Date range: 2025-07-15 -> 2026-07-15
Volume unit assumption: `hand`

## Decision

Write Gate: PASS
Capability Checks: PASS
Production Decision: DAILY_WRITES_ALLOWED
Index/Calendar Dependent Jobs: ALLOWED
Reason: daily close cross-check passed

| Check | Blocking | Status | Source | Rows | Latency ms | Error | Message |
|---|---|---|---|---:|---:|---|---|
| daily 600036 (600036.SH) | yes | ok | tushare.daily | 242 | 971.4 |  |  |
| daily 000001 (000001.SZ) | yes | ok | tushare.daily | 242 | 711.3 |  |  |
| daily 002594 (002594.SZ) | yes | ok | tushare.daily | 242 | 778.8 |  |  |
| index_daily 000300 (000300.SH) | no | ok | tushare.index_daily | 5948 | 1773.4 |  |  |
| trade_cal SSE | no | ok | tushare.trade_cal | 243 | 871.1 |  |  |

## Close Cross-Check

Close cross-check: PASS

| Code | Trade Date | Tushare Close | Reference Trade Date | Reference Close | Reference Source | Diff | Status |
|---|---|---:|---|---:|---|---:|---|
| 600036 | 2026-07-14 | 37.1800 | 2026-07-14 | 37.1800 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 000001 | 2026-07-14 | 10.6900 | 2026-07-14 | 10.6900 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 002594 | 2026-07-14 | 90.1800 | 2026-07-14 | 90.1800 | baostock.query_history_k_data_plus | 0.000% | PASS |

## Write Gate Result

PASS

Daily production writes require Write Gate PASS. Capability DEGRADED/BLOCKED means index/calendar-dependent jobs remain on hold.
