I will search for the target files to review their location in the filesystem.
I will wait for the find command to finish and check its output.
I will read the spec file `2026-07-08-l3-v2-entry-signal-spec.md`.
I will read the backtest plan file `2026-07-08-l3-v2-offline-backtest-plan.md`.
I will read the engineering review document `agy_stdout.txt`.
I will read the investment review document `agy_stdout.txt`.
I will read the backtest plan review document `agy_stdout.txt`.
I will verify if the database file `tracker.db` exists in `/home/lin/a-stock-tracker`.
I will check the reviews directory to see what reviews and folders are already present.
I will check the rereview document to see if there is any additional review feedback.
I will search for the index_prices table schema in the codebase to confirm if the symbol matches.
I will view the `lib/cache.py` file around line 143 to examine the `index_prices` table schema.
I will search for `index_prices` insertions or queries in the codebase to confirm how the symbol is used.
### 1. Verdict

**PASS_WITH_NOTES**

Both [L3 v2 Entry Signal Spec](file:///home/lin/a-stock-tracker/docs/specs/2026-07-08-l3-v2-entry-signal-spec.md) and [L3 v2 Offline Backtest Plan](file:///home/lin/a-stock-tracker/docs/plans/2026-07-08-l3-v2-offline-backtest-plan.md) have successfully integrated the prior review feedback. The three critical findings have been fully addressed:
* **QFQ decision gate** is resolved: `NEED_QFQ` / `NEED_SPEC_FIX` is enforced, preventing false `GO_TDD` claims on unadjusted data.
* **Backtest sample domain** is resolved: Core decision metrics are isolated to the `total_score >= buy_strong` subset, while using the full Framework A universe solely for distribution diagnostics.
* **Data contract** is resolved: Both the pure function signatures and CSV outputs now receive and expose full contract fields (`source`, `adjusted`, `volume_unit`, `freshness`, `unavailable_reason`) rather than a simplified boolean check.

---

### 2. Blocking Findings

**None**

---

### 3. Important Notes

While the spec and plan are technically complete and safe to execute, the following non-blocking improvements should be kept in mind during script implementation:

1. **Historical Data Padding (Pre-Start window)**
   * **Observation**: To compute moving averages (MA60, MA120) and historical resistance (`high_20d_prev` excluding today) at the very start date of the backtest (e.g. `--start 2025-01-01`), the data loader must fetch daily bars starting at least **120 trading days prior** to the start date. 
   * **Recommendation**: Ensure the database query in [scripts/offline_l3_v2_backtest.py](file:///home/lin/a-stock-tracker/scripts/offline_l3_v2_backtest.py) fetches historical data using a padded date range to avoid a cluster of invalid/unavailable data points in the first few months of the backtest.

2. **In-Memory QFQ Derivation Robustness**
   * **Observation**: When Tushare fetches `daily` and `adj_factor` data to perform memory-only QFQ derivation:
     $$\text{qfq\_factor}_t = \frac{\text{adj\_factor}_t}{\text{adj\_factor}_{\text{latest}}}$$
     Missing adjustment factors or date mismatches can introduce calculation errors.
   * **Recommendation**: Implement check assertions verifying that the length of the price series matches the adjustment factor series, and fallback to logging a warnings-level diagnostic if factor alignment fails for a given ticker.

3. **Time-Splitting Out-of-Sample (OOS) Details**
   * **Observation**: The watchlist is relatively small (43 stocks) and contains predominantly high-performing dividend stocks from recent cycles. 
   * **Recommendation**: When generating the OOS diagnostics requested in REQ-023A, enforce a clear temporal split (e.g., In-Sample: 2025-01-01 to 2025-12-31, Out-of-Sample: 2026-01-01 to 2026-07-08) to quantify risk against overfitting parameter limits.

---

### 4. Safety Boundary Assessment

The safety boundaries defined in the plan are **fully compliant and safe**:
* **No Database Mutation**: The database connection is explicitly restricted using SQLite read-only URI params (`file:tracker.db?mode=ro`) and `PRAGMA query_only=ON`.
* **No Side-Effects**: The backtest script is isolated and does not import Telegram, Gemini, or Google Sheets modules. It does not trigger pipelines or outcome-updates.
* **No Code Pollution**: Rules are evaluated via local candidate functions in the script. They do not overwrite or modify the production table columns or trigger main-pipeline calculations.
* **Controlled Network Requests**: Tushare integration requires an explicit CLI flag (`--allow-tushare-fetch`) and defaults to a local dry-run diagnostics mode (`--no-external-fetch`).

---

### 5. Recommended Next Step

Proceed to implement [scripts/offline_l3_v2_backtest.py](file:///home/lin/a-stock-tracker/scripts/offline_l3_v2_backtest.py) following Phase 0 of the [L3 v2 Offline Backtest Plan](file:///home/lin/a-stock-tracker/docs/plans/2026-07-08-l3-v2-offline-backtest-plan.md).
