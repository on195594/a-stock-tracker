# Tushare Capability Probe

Run date: 2026-07-02
Token configured: yes
Date range: 2025-07-02 -> 2026-07-02
Volume unit assumption: `hand`

## Decision

Write Gate: PASS
Capability Checks: PASS
Production Decision: DAILY_WRITES_ALLOWED
Index/Calendar Dependent Jobs: ALLOWED
Reason: daily close cross-check passed

| Check | Blocking | Status | Source | Rows | Latency ms | Error | Message |
|---|---|---|---|---:|---:|---|---|
| daily 600036 (600036.SH) | yes | ok | tushare.daily | 242 | 1036.0 |  |  |
| daily 000001 (000001.SZ) | yes | ok | tushare.daily | 242 | 867.4 |  |  |
| daily 002594 (002594.SZ) | yes | ok | tushare.daily | 242 | 586.6 |  |  |
| index_daily 000300 (000300.SH) | no | ok | tushare.index_daily | 5218 | 1742.3 |  |  |
| trade_cal SSE | no | ok | tushare.trade_cal | 243 | 965.2 |  |  |

## Close Cross-Check

Close cross-check: PASS

| Code | Trade Date | Tushare Close | Reference Trade Date | Reference Close | Reference Source | Diff | Status |
|---|---|---:|---|---:|---|---:|---|
| 600036 | 2026-07-01 | 35.9100 | 2026-07-01 | 35.9100 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 000001 | 2026-07-01 | 10.1600 | 2026-07-01 | 10.1600 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 002594 | 2026-07-01 | 80.6600 | 2026-07-01 | 80.6600 | baostock.query_history_k_data_plus | 0.000% | PASS |

## Write Gate Result

PASS

Daily production writes require Write Gate PASS. Capability DEGRADED/BLOCKED means index/calendar-dependent jobs remain on hold.
