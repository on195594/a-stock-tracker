# Tushare Capability Probe

Run date: 2026-07-27
Token configured: yes
Date range: 2025-07-27 -> 2026-07-27
Volume unit assumption: `hand`

## Decision

Write Gate: PASS
Capability Checks: PASS
Production Decision: DAILY_WRITES_ALLOWED
Index/Calendar Dependent Jobs: ALLOWED
Reason: daily close cross-check passed

| Check | Blocking | Status | Source | Rows | Latency ms | Error | Message |
|---|---|---|---|---:|---:|---|---|
| daily 600036 (600036.SH) | yes | ok | tushare.daily | 241 | 1903.0 |  |  |
| daily 000786 (000786.SZ) | yes | ok | tushare.daily | 241 | 496.0 |  |  |
| daily 002594 (002594.SZ) | yes | ok | tushare.daily | 241 | 742.2 |  |  |
| index_daily 000300 (000300.SH) | no | ok | tushare.index_daily | 5956 | 1995.2 |  |  |
| trade_cal SSE | no | ok | tushare.trade_cal | 242 | 813.2 |  |  |

## Close Cross-Check

Close cross-check: PASS

| Code | Trade Date | Tushare Close | Reference Trade Date | Reference Close | Reference Source | Diff | Status |
|---|---|---:|---|---:|---|---:|---|
| 600036 | 2026-07-24 | 39.2000 | 2026-07-24 | 39.2000 | tushare.daily | 0.000% | PASS |
| 000786 | 2026-07-24 | 19.0000 | 2026-07-24 | 19.0000 | tushare.daily | 0.000% | PASS |
| 002594 | 2026-07-24 | 91.8900 | 2026-07-24 | 91.8900 | tushare.daily | 0.000% | PASS |

## Write Gate Result

PASS

Daily production writes require Write Gate PASS. Capability DEGRADED/BLOCKED means index/calendar-dependent jobs remain on hold.
