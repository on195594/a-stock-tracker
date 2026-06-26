# Tushare Capability Probe

Run date: 2026-06-26
Token configured: yes
Date range: 2025-06-26 -> 2026-06-26
Volume unit assumption: `hand`

| Check | Status | Source | Rows | Latency ms | Error | Message |
|---|---|---|---:|---:|---|---|
| daily 600036 (600036.SH) | ok | tushare.daily | 242 | 1186.9 |  |  |
| daily 000001 (000001.SZ) | ok | tushare.daily | 242 | 895.8 |  |  |
| daily 002594 (002594.SZ) | ok | tushare.daily | 242 | 1040.1 |  |  |
| index_daily 000300 (000300.SH) | ok | tushare.index_daily | 5214 | 2011.7 |  |  |
| trade_cal SSE | ok | tushare.trade_cal | 243 | 993.7 |  |  |

## Close Cross-Check

Close cross-check: PASS

| Code | Trade Date | Tushare Close | Reference Trade Date | Reference Close | Reference Source | Diff | Status |
|---|---|---:|---|---:|---|---:|---|
| 600036 | 2026-06-25 | 36.2300 | 2026-06-25 | 36.2300 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 000001 | 2026-06-25 | 10.4200 | 2026-06-25 | 10.4200 | baostock.query_history_k_data_plus | 0.000% | PASS |
| 002594 | 2026-06-25 | 82.2000 | 2026-06-25 | 82.2000 | baostock.query_history_k_data_plus | 0.000% | PASS |

## Result

PASS

Close-price cross-check must be PASS before enabling production writes.
