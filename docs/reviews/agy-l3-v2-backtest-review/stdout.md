### Verdict
**APPROVE**

---

### Audit Findings

#### 1. SQLite Read-Only & DB Write Avoidance
* **Status**: **Pass**
* **Details**: The script [offline_l3_v2_backtest.py](file:///home/lin/a-stock-tracker/scripts/offline_l3_v2_backtest.py) correctly enforces read-only access to `tracker.db`.
* **Evidence**:
  * In [open_readonly_db](file:///home/lin/a-stock-tracker/scripts/offline_l3_v2_backtest.py#L220-L230), the connection is opened with URI parameter `mode=ro` (Line 222), `PRAGMA query_only=ON` is executed (Line 225), and a validation check raises a `RuntimeError` if query-only mode fails to activate (Lines 226-228).
  * No database write or edit commands are present. It contains no imports of the production pipeline, Telegram, Gemini, or Google Sheets.

#### 2. QFQ Math Correctness
* **Status**: **Pass**
* **Details**: The math for backward-adjusted (QFQ) price is mathematically correct: $qfq\_price_t = raw\_price_t \times \frac{adj\_factor_t}{adj\_factor_{latest}}$.
* **Evidence**:
  * In [derive_qfq_bars_from_tushare](file:///home/lin/a-stock-tracker/scripts/offline_l3_v2_backtest.py#L536-L585), QFQ prices are calculated using `ratio = factor / latest_factor` (Line 566), where `factor` is the adjustment factor at time $t$ and `latest_factor` is the adjustment factor on the latest trade date (Line 554).
  * Raw prices are multiplied by this ratio (Lines 571-574).
  * Raw volumes are divided by this ratio (`raw_volume / ratio` on Line 575), which correctly preserves the price-volume value invariant.

#### 3. QFQ & adj_factor Alignment and Blocking
* **Status**: **Pass**
* **Details**: The alignment checks are exact, and any misalignment or data unavailability successfully blocks v2 `pass_strong`.
* **Evidence**:
  * In [derive_qfq_bars_from_tushare](file:///home/lin/a-stock-tracker/scripts/offline_l3_v2_backtest.py#L547-L550), it ensures date sets match exactly (`set(daily_dates) == set(factor_dates)`) and sizes match (`len(daily_dates) == len(factor_dates)`).
  * In [compute_l3_v2_candidate](file:///home/lin/a-stock-tracker/scripts/offline_l3_v2_backtest.py#L617-L698), if `data_contract.adjusted != "qfq"` or any alignment/unavailability reason is present, the function returns a `pass_weak` signal early (Lines 665-670), blocking it from reaching the `pass_strong` logic.
  * In [decide](file:///home/lin/a-stock-tracker/scripts/offline_l3_v2_backtest.py#L1001-L1037), if `buy_strong_qfq_issue_count` is greater than 0, the decision gate returns `NEED_QFQ` (Lines 1012-1013), blocking `GO_TDD`.

#### 4. Future Function / Lookahead Bias Audit
* **Status**: **Pass**
* **Details**: No lookahead bias exists in the core indicators.
* **Evidence**:
  * **MA / Volume / high_20d_prev**: Input panels are sliced strictly to $\le$ the prediction's `score_date` (Lines 266-267). The `high_20d_prev` indicator uses index range `[-21:-1]`, which correctly excludes the current day's price (Line 643).
  * **Market State**: Slices index prices up to `score_date` (Line 701) and requires the latest date to match the prediction `score_date` (Line 705).
  * **Outcome Use**: `outcome_30d` and `benchmark_30d` are only referenced to calculate performance stats (Lines 771-773) and are not parameters for signal decision logic.
  * **Dedup**: [build_dedup_events](file:///home/lin/a-stock-tracker/scripts/offline_l3_v2_backtest.py#L837-L851) sorts events chronologically (Line 839) and only performs backward-looking distance checks relative to previously accepted events (Line 846), avoiding lookahead.
  * *Note on QFQ*: While `latest_factor` at the end of the backtest is used as the base divisor, it acts as a constant scaling factor across all prices/volumes for a given stock series. Relative comparisons and indicator ratios (e.g. price crossover, vol ratios) remain invariant to this base date choice.

#### 5. Sample Domain Fairness
* **Status**: **Pass**
* **Details**: The prediction sample domain is identical for both v1 and v2.
* **Evidence**:
  * Predictions are loaded in a single query from the SQLite `predictions` table (Line 317) and both `v1` and `v2` are run over the exact same prediction set (Lines 269-278).

#### 6. 20-Trading-Day Cooldown Dedup
* **Status**: **Pass**
* **Details**: The deduplication logic matches the report description.
* **Evidence**:
  * In [build_dedup_events](file:///home/lin/a-stock-tracker/scripts/offline_l3_v2_backtest.py#L846), events are skipped if the trading distance is $\le 20$. This enforces that the next valid trade can only occur on the 21st trading day, matching a 20-day cooldown period.

#### 7. Round-Trip Cost Model
* **Status**: **Pass**
* **Details**: The cost model is correctly parameterized and applied.
* **Evidence**:
  * In [round_trip_cost_bps](file:///home/lin/a-stock-tracker/scripts/offline_l3_v2_backtest.py#L1241-L1242), the formula `commission_bps * 2.0 + slippage_bps * 2.0 + stamp_tax_bps` is used. With default parameters, this yields exactly `20.0` bps.
  * This is correctly scaled as a percentage point deduction (`cost / 100.0` or `0.2000`) when subtracted from alpha percentage units (Line 773).

#### 8. Report Decision Support
* **Status**: **Pass**
* **Details**: The `NEED_QFQ` decision is fully supported by the data and artifacts.
* **Evidence**:
  * The report [2026-07-08-l3-v2-backtest-report.md](file:///home/lin/a-stock-tracker/docs/reviews/2026-07-08-l3-v2-backtest-report.md) records 0 QFQ panels due to `TUSHARE_RATE_LIMIT` failures.
  * In [metrics_summary.json](file:///home/lin/a-stock-tracker/docs/reviews/l3-v2-backtest-artifacts/metrics_summary.json), v2 yields only `pass_weak` because the QFQ data was unavailable.
  * Consequently, the script's decision gate correctly output `NEED_QFQ` rather than `GO_TDD`.
