# Tushare Capability Probe

Run date: 2026-06-21
Token configured: yes
Date range: 2025-06-21 -> 2026-06-21
Volume unit assumption: `hand`

| Check | Status | Source | Rows | Latency ms | Error | Message |
|---|---|---|---:|---:|---|---|
| daily 600036 (600036.SH) | ok | tushare.daily | 241 | 1135.4 |  |  |
| daily 000001 (000001.SZ) | ok | tushare.daily | 241 | 987.5 |  |  |
| daily 002594 (002594.SZ) | ok | tushare.daily | 241 | 492.4 |  |  |
| index_daily 000300 (000300.SH) | ok | tushare.index_daily | 5210 | 5398.9 |  |  |
| trade_cal SSE | ok | tushare.trade_cal | 241 | 1007.5 |  |  |

## Close Cross-Check

Close cross-check: PASS

| Code | Trade Date | Tushare Close | Reference Close | Reference Source | Diff | Status |
|---|---|---:|---:|---|---:|---|
| 600036 | 2026-06-18 | 37.2600 | 37.2600 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 000001 | 2026-06-18 | 10.5200 | 10.5200 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 002594 | 2026-06-18 | 88.1300 | 88.1300 | baostock.query_history_k_data_plus | 0.000% | PASS |

## Result

PASS

Close-price cross-check must be PASS before enabling production writes.
