# L3 v2 Offline Backtest Report

## 1. Run metadata
- Generated at: 2026-07-10T08:32:52
- DB path: tracker.db
- SQLite query_only: True
- Score window: 2025-01-01 to 2026-07-08
- Preload trading days: 120
- Effective preload start: 2024-07-05
- External fetch mode: allow_tushare_fetch
- buy_strong threshold: 44.0 (weights.json:thresholds.buy_strong)
- IS split: 2025-01-01 to 2025-12-31
- OOS split: 2026-01-01 to 2026-07-08
- Cost bps: commission=2.5, slippage=5.0, stamp_tax=5.0, round_trip=20.0

## 2. Data coverage
- Framework A prediction rows: 1541
- buy_strong rows: 766
- Stock count: 38
- Local none panels: 35 (bars min/max 476/486)
- qfq panels: 0 (bars min/max 0/0)
- qfq reason counts: `{"TUSHARE_RATE_LIMIT": 38}`
- buy_strong qfq issue rows: 766
- Data contract counts: `{"none|none|unknown|QFQ_UNAVAILABLE": 3, "tushare.daily|none|hand|QFQ_UNAVAILABLE": 1538}`

## 3. Signal distribution
All Framework A sample:
- v1: `{"pass": 292, "reject": 1246, "unavailable": 3}`
- v2: `{"pass_weak": 292, "reject": 1246, "unavailable": 3}`

buy_strong subset:
- v1: `{"pass": 95, "reject": 670, "unavailable": 1}`
- v2: `{"pass_weak": 95, "reject": 670, "unavailable": 1}`

High-dividend qfq vs none examples (600900):
| score_date | code | v2_adjusted | v2_status | v2_reason | close | ma60 | ma120 | alignment_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-04-27 | 600900 | none | reject | BELOW_MA60 | 26.6600 | 26.6853 | 27.2544 | QFQ_UNAVAILABLE |
| 2026-04-29 | 600900 | none | reject | BELOW_MA120 | 26.7700 | 26.7107 | 27.2273 | QFQ_UNAVAILABLE |
| 2026-05-11 | 600900 | none | reject | BELOW_MA120 | 27.0100 | 26.8018 | 27.1743 | QFQ_UNAVAILABLE |
| 2026-05-12 | 600900 | none | reject | BELOW_MA120 | 27.0400 | 26.8192 | 27.1620 | QFQ_UNAVAILABLE |
| 2026-05-13 | 600900 | none | reject | BELOW_MA120 | 27.0000 | 26.8295 | 27.1492 | QFQ_UNAVAILABLE |
| 2026-05-14 | 600900 | none | reject | BELOW_MA120 | 26.9400 | 26.8352 | 27.1352 | QFQ_UNAVAILABLE |
| 2026-05-15 | 600900 | none | reject | BELOW_MA120 | 27.0300 | 26.8437 | 27.1221 | QFQ_UNAVAILABLE |
| 2026-05-18 | 600900 | none | reject | BELOW_MA120 | 26.8900 | 26.8508 | 27.1092 | QFQ_UNAVAILABLE |
| 2026-05-19 | 600900 | none | pass_weak | QFQ_UNAVAILABLE;ADJUSTED_NONE | 27.2500 | 26.8622 | 27.1001 | QFQ_UNAVAILABLE |
| 2026-05-20 | 600900 | none | reject | BELOW_MA120 | 26.9000 | 26.8692 | 27.0896 | QFQ_UNAVAILABLE |
| 2026-05-21 | 600900 | none | reject | BELOW_MA60 | 26.6600 | 26.8782 | 27.0772 | QFQ_UNAVAILABLE |
| 2026-05-22 | 600900 | none | reject | BELOW_MA60 | 26.5600 | 26.8875 | 27.0633 | QFQ_UNAVAILABLE |
| 2026-05-25 | 600900 | none | reject | BELOW_MA60 | 26.6700 | 26.8978 | 27.0510 | QFQ_UNAVAILABLE |
| 2026-05-26 | 600900 | none | pass_weak | QFQ_UNAVAILABLE;ADJUSTED_NONE | 27.1400 | 26.9173 | 27.0425 | QFQ_UNAVAILABLE |
| 2026-05-27 | 600900 | none | pass_weak | QFQ_UNAVAILABLE;ADJUSTED_NONE | 27.2400 | 26.9373 | 27.0346 | QFQ_UNAVAILABLE |
| 2026-05-28 | 600900 | none | reject | LOW_VOLUME | 27.2200 | 26.9570 | 27.0278 | QFQ_UNAVAILABLE |
| 2026-05-29 | 600900 | none | pass_weak | QFQ_UNAVAILABLE;ADJUSTED_NONE | 27.7500 | 26.9767 | 27.0263 | QFQ_UNAVAILABLE |
| 2026-06-01 | 600900 | none | pass_weak | QFQ_UNAVAILABLE;ADJUSTED_NONE | 27.7700 | 26.9900 | 27.0236 | QFQ_UNAVAILABLE |
| 2026-06-02 | 600900 | none | pass_weak | QFQ_UNAVAILABLE;ADJUSTED_NONE | 27.9600 | 27.0045 | 27.0234 | QFQ_UNAVAILABLE |
| 2026-06-03 | 600900 | none | pass_weak | QFQ_UNAVAILABLE;ADJUSTED_NONE | 27.8900 | 27.0180 | 27.0219 | QFQ_UNAVAILABLE |

## 4. Raw daily metrics
### all
| state | n | settled | avg_alpha | avg_net_alpha | hit_rate | median_alpha | worst | maxdd_proxy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1:pass | 292 | 212 | -1.1794 | -1.3794 | 0.3255 | -4.4033 | -35.3008 | -594.2689 |
| v1:reject | 1246 | 633 | -6.4038 | -6.6038 | 0.2227 | -6.9977 | -39.8911 | -4180.2280 |
| v1:unavailable | 3 | 3 | -5.8923 | -6.0923 | 0.3333 | -8.8515 | -9.5625 | -18.8140 |
| v2:pass_weak | 292 | 212 | -1.1794 | -1.3794 | 0.3255 | -4.4033 | -35.3008 | -594.2689 |
| v2:reject | 1246 | 633 | -6.4038 | -6.6038 | 0.2227 | -6.9977 | -39.8911 | -4180.2280 |
| v2:unavailable | 3 | 3 | -5.8923 | -6.0923 | 0.3333 | -8.8515 | -9.5625 | -18.8140 |
### buy_strong
| state | n | settled | avg_alpha | avg_net_alpha | hit_rate | median_alpha | worst | maxdd_proxy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1:pass | 95 | 80 | -11.7411 | -11.9411 | 0.0750 | -12.2502 | -35.3008 | -955.2906 |
| v1:reject | 670 | 301 | -8.3342 | -8.5342 | 0.2027 | -8.0994 | -35.1738 | -2568.7960 |
| v1:unavailable | 1 | 1 | -8.8515 | -9.0515 | 0.0000 | -8.8515 | -8.8515 | -9.0515 |
| v2:pass_weak | 95 | 80 | -11.7411 | -11.9411 | 0.0750 | -12.2502 | -35.3008 | -955.2906 |
| v2:reject | 670 | 301 | -8.3342 | -8.5342 | 0.2027 | -8.0994 | -35.1738 | -2568.7960 |
| v2:unavailable | 1 | 1 | -8.8515 | -9.0515 | 0.0000 | -8.8515 | -8.8515 | -9.0515 |

## 5. Dedup 20d metrics
This report's dedup hit rate depends on a 20-trading-day same-stock cooldown. Do not use it as a rollout claim unless production push implements an equivalent cooldown.
### all
| state | n | settled | avg_alpha | avg_net_alpha | hit_rate | median_alpha | worst | maxdd_proxy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1:pass | 38 | 31 | -2.5586 | -2.7586 | 0.2581 | -5.8848 | -34.0463 | -122.5275 |
| v2:pass_weak | 38 | 31 | -2.5586 | -2.7586 | 0.2581 | -5.8848 | -34.0463 | -122.5275 |
### buy_strong
| state | n | settled | avg_alpha | avg_net_alpha | hit_rate | median_alpha | worst | maxdd_proxy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1:pass | 12 | 10 | -10.8259 | -11.0259 | 0.0000 | -9.5192 | -34.0463 | -110.2593 |
| v2:pass_weak | 12 | 10 | -10.8259 | -11.0259 | 0.0000 | -9.5192 | -34.0463 | -110.2593 |

## 6. Cost-adjusted metrics
- Default round-trip cost: 20.0 bps, subtracted from alpha as 0.2000 percentage points.
| state | round_trip_bps | avg_net_alpha |
| --- | --- | --- |
| v1:pass | 10.0000 | -11.8411 |
| v2:pass_strong | 10.0000 |  |
| v2:pass_weak | 10.0000 | -11.8411 |
| v1:pass | 20.0000 | -11.9411 |
| v2:pass_strong | 20.0000 |  |
| v2:pass_weak | 20.0000 | -11.9411 |
| v1:pass | 30.0000 | -12.0411 |
| v2:pass_strong | 30.0000 |  |
| v2:pass_weak | 30.0000 | -12.0411 |

## 7. IS/OOS diagnostics
### in_sample
raw_buy_strong:
_No rows._
dedup_buy_strong:
_No rows._
### out_of_sample
raw_buy_strong:
| state | n | settled | avg_net_alpha | hit_rate |
| --- | --- | --- | --- | --- |
| v1:pass | 95 | 80 | -11.9411 | 0.0750 |
| v1:reject | 670 | 301 | -8.5342 | 0.2027 |
| v1:unavailable | 1 | 1 | -9.0515 | 0.0000 |
| v2:pass_weak | 95 | 80 | -11.9411 | 0.0750 |
| v2:reject | 670 | 301 | -8.5342 | 0.2027 |
| v2:unavailable | 1 | 1 | -9.0515 | 0.0000 |
dedup_buy_strong:
| state | n | settled | avg_net_alpha | hit_rate |
| --- | --- | --- | --- | --- |
| v1:pass | 12 | 10 | -11.0259 | 0.0000 |
| v2:pass_weak | 12 | 10 | -11.0259 | 0.0000 |

## 8. Decision gate
- Decision: **NEED_QFQ**
- Reason: No qfq panels were available; v2 pass_strong cannot be validated.

Allowed decisions are GO_TDD / NEED_QFQ / NEED_SPEC_FIX / STOP. GO_TDD is not production approval.

## 9. Known limitations
- v2 thresholds are fixed candidate values; ATR/beta/industry adaptive thresholds are not implemented here.
- MaxDD and Sharpe are proxy metrics over realized event alpha, not portfolio path risk.
- Dedup uses score-date trading rows for each code; future production needs an explicit push cooldown if v2 is promoted.
- With no qfq coverage, pass_strong validation is intentionally blocked and the decision must remain NEED_QFQ.
