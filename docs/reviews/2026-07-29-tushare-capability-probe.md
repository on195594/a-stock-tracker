# Tushare Capability Probe

Run date: 2026-07-29
Token configured: yes
Date range: 2025-07-29 -> 2026-07-29
Volume unit assumption: `hand`

## Decision

Write Gate: PASS
Capability Checks: PASS
Production Decision: DAILY_WRITES_ALLOWED
Index/Calendar Dependent Jobs: ALLOWED
Reason: daily close cross-check passed

| Check | Blocking | Status | Source | Rows | Latency ms | Error | Message |
|---|---|---|---|---:|---:|---|---|
| daily 600036 (600036.SH) | yes | ok | tushare.daily | 243 | 895.0 |  |  |
| daily 000786 (000786.SZ) | yes | ok | tushare.daily | 243 | 1494.4 |  |  |
| daily 002594 (002594.SZ) | yes | ok | tushare.daily | 243 | 1330.9 |  |  |
| index_daily 000300 (000300.SH) | no | ok | tushare.index_daily | 5959 | 3116.3 |  |  |
| trade_cal SSE | no | ok | tushare.trade_cal | 243 | 1505.8 |  |  |

## Close Cross-Check

Close cross-check: PASS

| Code | Trade Date | Tushare Close | Reference Trade Date | Reference Close | Reference Source | Diff | Status |
|---|---|---:|---|---:|---|---:|---|
| 600036 | 2026-07-29 | 39.6600 | 2026-07-29 | 39.6600 | tushare.pro_bar.qfq | 0.000% | PASS |
| 000786 | 2026-07-29 | 20.3600 | 2026-07-29 | 20.3600 | tushare.pro_bar.qfq | 0.000% | PASS |
| 002594 | 2026-07-29 | 94.8500 | 2026-07-29 | 94.8500 | tushare.pro_bar.qfq | 0.000% | PASS |

## Write Gate Result

PASS

Daily production writes require Write Gate PASS. Capability DEGRADED/BLOCKED means index/calendar-dependent jobs remain on hold.
