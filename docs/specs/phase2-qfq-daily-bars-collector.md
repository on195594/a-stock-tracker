# Phase 2 Engineering Spec: QFQ Daily Bars Collector

## 1. Goal & Scope

Phase 2 makes L3 v2 eligible to emit authoritative-quality `pass_strong` signals by collecting BaoStock forward-adjusted (QFQ, 前复权) daily bars and making the production wrapper prefer them. The validated out-of-sample v2 result used QFQ prices; therefore an unadjusted `pass_weak` result is not an acceptable substitute for the later Phase 3 trigger switch.

This phase has exactly four implementation deliverables:

1. Add the idempotent `scripts/fetch_qfq_daily_bars.py` collector.
2. Change `compute_l3_v2_from_daily_bars()` to prefer a complete 120-bar QFQ window and otherwise preserve the unadjusted fallback.
3. Schedule the QFQ collector before the existing weekday 16:30 `pipeline.py daily` job.
4. Add focused QFQ selection and fallback tests in `tests/test_l3_v2_pipeline_qfq.py`.

There is no database migration. `daily_bars` already uses `(code, trade_date, adjusted)` as its primary key, so `adjusted='qfq'` rows coexist with `adjusted='none'` rows. This phase does not switch Telegram or any other production trigger to L3 v2; that remains Phase 3 and requires rollout evidence from this phase. It also does not alter the QFQ calculation, back-adjust historical unadjusted rows locally, add dependencies, or change `lib/cache.py`.

Acceptance criteria are:

- A full-watchlist collection run continues after an individual symbol fails and commits successful symbols.
- Re-running the collector updates the same QFQ keys without creating duplicates.
- A non-freefall stock with 120 QFQ rows produces `signal == 1`, `status == "pass_strong"`, and `reason == "PASS_STRONG"`.
- Fewer than 120 QFQ rows never form the input panel; the wrapper instead uses up to 120 unadjusted rows and retains current `pass_weak` behavior.
- The existing unadjusted and exception-isolation tests continue to pass.

## 2. Architecture Decision: Why QFQ via BaoStock direct (not via IsolatedBaoStockMarketDataProvider)

Use the installed `baostock` package directly for this narrow collector. The script needs one login, one sequential pass over the watchlist, a fixed daily-history query, and one database upsert per code. Direct access exposes BaoStock's required `adjustflag="2"` without extending or depending on the private behavior of `a_stock_lib`'s general provider abstraction. It also permits a single authenticated session across all codes; constructing the isolated provider per request would add subprocess and login overhead and obscure the adjustment flag behind a provider intended for broader fallback use.

This is a deliberate exception for a bounded backfill/daily collection command, not a new default for online market-data calls. `IsolatedBaoStockMarketDataProvider` remains preferable when hard subprocess timeout isolation is required. Direct BaoStock can hang inside a network call, so the cron invocation must remain observable through the existing cron wrapper and log, and a persistent hang is a reason to move this collector to the isolated provider rather than add ad hoc process management inside the script.

The query contract is BaoStock's daily endpoint:

```python
rs = bs.query_history_k_data_plus(
    to_baostock_stock_code(code),
    "date,code,open,high,low,close,volume,amount,adjustflag,tradestatus",
    start_date=start_date,
    end_date=end_date,
    frequency="d",
    adjustflag="2",  # QFQ; 3 is unadjusted and 1 is HFQ
)
```

The implementation should reuse `to_baostock_stock_code` from the installed library if it is part of the package's public import surface. If it is not public, add a small typed local conversion helper (`6xxxxx` to `sh.6xxxxx`, otherwise `sz.xxxxxx`) and unit-test it; do not copy a private provider implementation wholesale.

## 3. Deliverable 1: `fetch_qfq_daily_bars.py` — full design with pseudocode/skeleton

### CLI and runtime behavior

The command is:

```text
python scripts/fetch_qfq_daily_bars.py [--backfill-days N] [--code CODE]
```

- `--backfill-days N` is a positive integer number of calendar days, defaulting to `200`. The A-share market has ~242 trading days/year, so 150 calendar days yields only ~100–108 trading days — insufficient to guarantee a 120-bar QFQ window. 200 calendar days reliably covers 120+ trading sessions in all non-exceptional years. The query range is inclusive: `today - timedelta(days=N)` through `today`.
- `--code CODE` restricts the run to one six-digit A-share code. Without it, codes come from `config.WATCHLIST` via each entry's `"code"`; no symbols are hardcoded.
- The script imports `DB_PATH` and `upsert_daily_bars` from `lib.cache`. It must not duplicate or derive the database path.
- BaoStock login happens once before code iteration and logout happens once in `finally`.
- Each code has its own `try/except`. A failure rolls back that code's pending transaction, logs `WARNING` with the code and a stable error-code string, and continues. A successful code is committed immediately, so a later failure cannot discard it.
- Empty successful responses are warnings, not fabricated bars. Suspended/non-trading records (`tradestatus != "1"`) and rows without a valid close are excluded.
- Production output uses `logging.getLogger(__name__)`; there are no `print` calls, `shell=True`, subprocess calls, or `raise Exception` statements.
- `upsert_daily_bars` supplies idempotence through `ON CONFLICT(code, trade_date, adjusted) DO UPDATE`.

Stable collector error strings should include at least `QFQ_LOGIN_FAILED`, `QFQ_QUERY_FAILED`, `QFQ_EMPTY_RESULT`, `QFQ_ROW_INVALID`, and `QFQ_CODE_FAILED`. A typed `QfqFetchError(RuntimeError)` may carry one of these codes; callers must not raise the base `Exception` class directly.

### Skeleton

The skeleton is illustrative but defines the required boundaries. Implementations may split parsing further to keep every function at or below 50 lines.

```python
from __future__ import annotations

import argparse
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

import baostock as bs
import pandas as pd

from config import WATCHLIST
from lib.cache import DB_PATH, upsert_daily_bars

logger = logging.getLogger(__name__)
FIELDS = "date,code,open,high,low,close,volume,amount,adjustflag,tradestatus"


class QfqFetchError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class Args:
    backfill_days: int
    code: str | None


from a_stock_lib.providers.baostock_quotes import to_baostock_stock_code as to_bs_code
# to_baostock_stock_code is public and handles SH / SZ / BJ prefixes correctly.
# Do NOT use a local helper — it would omit Beijing Stock Exchange (8/4 prefix → .bj) codes.


def query_qfq(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    result = bs.query_history_k_data_plus(
        to_bs_code(code),
        FIELDS,
        start_date=start_date,
        end_date=end_date,
        frequency="d",
        adjustflag="2",
    )
    if result.error_code != "0":
        raise QfqFetchError("QFQ_QUERY_FAILED", result.error_msg)
    rows: list[list[str]] = []
    while result.next():
        row = result.get_row_data()
        if row[-1] == "1" and row[5]:
            rows.append(row)
    frame = pd.DataFrame(rows, columns=FIELDS.split(","))
    return normalize_frame(frame)


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["date", "open", "high", "low", "close", "volume"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    output = frame.loc[:, columns].copy()
    for column in columns[1:]:
        output[column] = pd.to_numeric(output[column], errors="coerce")
    return output.dropna(subset=["date", "close"])


def fetch_one(
    conn: sqlite3.Connection,
    code: str,
    start_date: str,
    end_date: str,
) -> int:
    frame = query_qfq(code, start_date, end_date)
    if frame.empty:
        logger.warning("QFQ_EMPTY_RESULT code=%s", code)
        return 0
    count = upsert_daily_bars(
        conn,
        code,
        frame,
        source="baostock.qfq",
        adjusted="qfq",
        volume_unit="share",
    )
    conn.commit()
    return count


def run(args: Args) -> int:
    end = date.today()
    start = end - timedelta(days=args.backfill_days)
    codes = [args.code] if args.code else [item["code"] for item in WATCHLIST]
    login = bs.login()
    if login.error_code != "0":
        logger.error("QFQ_LOGIN_FAILED detail=%s", login.error_msg)
        return 1
    try:
        with sqlite3.connect(DB_PATH) as conn:
            collect_codes(conn, codes, start.isoformat(), end.isoformat())
    finally:
        bs.logout()
    return 0


def collect_codes(
    conn: sqlite3.Connection,
    codes: Sequence[str],
    start_date: str,
    end_date: str,
) -> None:
    for code in codes:
        try:
            count = fetch_one(conn, code, start_date, end_date)
            logger.info("QFQ_FETCH_OK code=%s rows=%d", code, count)
        except Exception as exc:  # broad catch: fault isolation per acceptance criterion
            conn.rollback()
            error_code = getattr(exc, "error_code", "QFQ_CODE_FAILED")
            logger.warning("%s code=%s detail=%s", error_code, code, exc)


def parse_args(argv: Sequence[str] | None = None) -> Args:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill-days", type=int, default=200)
    parser.add_argument("--code")
    namespace = parser.parse_args(argv)
    if namespace.backfill_days <= 0:
        parser.error("--backfill-days must be positive")
    return Args(namespace.backfill_days, namespace.code)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    return run(parse_args(argv))
```

The final implementation should also validate `--code` with a six-digit regular expression. An unexpected per-code library exception should still satisfy the isolation requirement: catch it at the code boundary, log `QFQ_CODE_FAILED`, roll back, and continue. If a broad final catch is used for that boundary, it must be documented as fault isolation, must not re-raise, and must not hide login/session setup failures. The process returns nonzero for a login or database-open failure; individual symbol failures are summarized in logs but do not abort the run.

## 4. Deliverable 2: `l3_v2_pipeline.py` changes — modified function signature and logic

The public signature remains unchanged:

```python
def compute_l3_v2_from_daily_bars(
    db: sqlite3.Connection,
    code: str,
    today: str,
) -> SignalResult:
```

Selection must occur before panel construction:

```python
def _select_daily_rows(
    db: sqlite3.Connection,
    code: str,
    today: str,
) -> tuple[list[dict[str, Any]], DataContractState]:
    qfq_rows = load_daily_bars(db, code, today, 120, adjusted="qfq")
    if len(qfq_rows) >= 120:
        return qfq_rows, DataContractState(
            adjusted="qfq",
            source="baostock.qfq",
            volume_unit=qfq_rows[-1]["volume_unit"],
            is_stale=False,
            stale_reason=None,
            unavailable_reason=None,
            alignment_reason=None,
        )

    rows = load_daily_bars(db, code, today, 120, adjusted="none")
    return rows, DataContractState(
        adjusted="none",
        source=rows[-1]["source"] if rows else "daily_bars",
        volume_unit=rows[-1]["volume_unit"] if rows else "unknown",
        is_stale=False,
        stale_reason=None,
        unavailable_reason="QFQ_UNAVAILABLE",
        alignment_reason=None,
    )
```

The QFQ contract values are exact: `adjusted="qfq"`, `source="baostock.qfq"`, `is_stale=False`, `stale_reason=None`, `unavailable_reason=None`, and `alignment_reason=None`; `volume_unit` is taken from the latest selected QFQ row. `unavailable_reason=None` is what makes `compute_l3_v2_candidate()` eligible to return the confirmed exact status `pass_strong`.

The wrapper then builds all `DailyBar` objects and the `PricePanel` from the same selected row set and contract. It must not mix QFQ and unadjusted rows to reach 120:

```python
def compute_l3_v2_from_daily_bars(
    db: sqlite3.Connection,
    code: str,
    today: str,
) -> SignalResult:
    try:
        rows, contract = _select_daily_rows(db, code, today)
        if not rows:
            return SignalResult(None, V2_VERSION, "unavailable", "NO_DAILY_BARS", empty_metrics())
        panel = _build_price_panel(code, rows, contract)
        return compute_l3_v2_candidate(panel, contract)
    except Exception as exc:  # existing wrapper-level fault isolation
        logger.warning("L3 v2 computation failed for %s: %s", code, exc)
        return SignalResult(None, V2_VERSION, "unavailable", "L3_V2_ERROR", empty_metrics())
```

`_build_price_panel()` is a typed extraction of the current date parsing and `DailyBar` tuple construction. Its `PricePanel.adjusted`, `source`, and `volume_unit` fields come from the selected contract, not hardcoded `"none"`. `preload_start` remains the first selected row's date; `stale_reason` and `limitation` remain `None`. Extracting selection and construction keeps the public function below 50 lines and preserves its no-network and exception-isolation guarantees.

The threshold is strictly `len(qfq_rows) >= 120`. With 119 QFQ rows, even if they are newer than the unadjusted data, discard them as a candidate input and load a separate 120-row unadjusted window. If the fallback contains fewer than 120 rows, the existing core function returns `status="unavailable"`, `reason="INSUFFICIENT_WINDOW"`. No freshness behavior is added in this phase.

## 5. Deliverable 3: Cron integration — exact crontab line

Run collection at **16:00 on weekdays**, before the existing 16:30 daily pipeline:

```bash
QFQ_RULE="00 16 * * 1-5 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python scripts/fetch_qfq_daily_bars.py\" qfq-daily-bars >> $PROJECT_DIR/logs/qfq-daily-bars.log 2>&1"
```

This order is required: the 16:00 collector commits QFQ rows first, then `pipeline.py daily` reads them at 16:30. Running the fetch after the pipeline would delay `pass_strong` eligibility by a day. BaoStock is expected to provide T-1 data in this operating mode, so a 15:30 run would also be semantically valid; 16:00 is preferred because it is after the A-share close while retaining a 30-minute buffer before scoring.

Implementation must:
1. Define `QFQ_RULE` in `cron-setup.sh` using `$PROJECT_DIR` (matching the style of `$DAILY_RULE` / `$WEEKLY_RULE`), placed adjacent to and before `DAILY_RULE`.
2. Update the `awk` deletion block to also remove legacy QFQ collector entries. Add the following pattern alongside the existing `pipeline.py` patterns:
   ```awk
   /a-stock-tracker\/cron-alert-wrap\.sh/ && /fetch_qfq_daily_bars\.py/ { next }
   ```
   Without this, re-running `cron-setup.sh` accumulates duplicate QFQ cron entries.

Installing or changing the live crontab is a rollout operation, not part of writing this spec. The collector's independent log and wrapper label make failures distinguishable from `daily` failures.

## 6. Deliverable 4: Tests — test names and assertions

Add `tests/test_l3_v2_pipeline_qfq.py`. Use an in-memory SQLite database with the production `daily_bars` shape, or a typed helper that creates that exact table. Insert deterministic rows under the composite key so QFQ and unadjusted data can share trade dates. Use constant closes above the freefall threshold unless a test intentionally makes the unadjusted series reject. No test performs a network request or writes `tracker.db`.

The four required tests are:

1. `test_pass_strong_when_120_qfq_bars_available`

   Insert exactly 120 `adjusted="qfq"`, `source="baostock.qfq"` rows with close `100.0`. Assert `result.signal == 1`, `result.status == "pass_strong"`, `result.reason == "PASS_STRONG"`, and `result.metrics["close"] == 100.0`. The exact status is confirmed by `compute_l3_v2_candidate`; no alternative `pass_weak` expectation is needed.

2. `test_fallback_to_pass_weak_when_only_119_qfq_bars`

   Insert 119 QFQ rows plus a complete, non-freefall 120-row `adjusted="none"` series. Assert `result.signal == 1`, `result.status == "pass_weak"`, `result.reason == "QFQ_UNAVAILABLE"`, and the reported close equals the latest unadjusted close. This proves a partial QFQ window is not padded or used.

3. `test_fallback_to_pass_weak_when_no_qfq_bars`

   Insert only 120 non-freefall unadjusted rows. Assert `result.signal == 1`, `result.status == "pass_weak"`, `result.reason == "QFQ_UNAVAILABLE"`, and the metrics are computed from that unadjusted series. This locks in current behavior for databases not yet backfilled.

4. `test_qfq_takes_priority_over_unadjusted`

   Insert both complete 120-row versions on identical dates. Make QFQ closes constant at `100.0`. Make the unadjusted series end in a close low enough to trigger `FREEFALL` if selected. Assert the actual result is `signal == 1`, `status == "pass_strong"`, `reason == "PASS_STRONG"`, and `result.metrics["close"] == 100.0`. The distinct metric and counterfactual unadjusted rejection prove QFQ priority without adding contract state to `SignalResult`.

Representative test structure:

```python
def test_qfq_takes_priority_over_unadjusted() -> None:
    db = make_db()
    insert_bars(db, adjusted="qfq", closes=[100.0] * 120, source="baostock.qfq")
    insert_bars(db, adjusted="none", closes=[100.0] * 119 + [60.0], source="tushare.daily")

    result = compute_l3_v2_from_daily_bars(db, CODE, END_DATE)

    assert result.signal == 1
    assert result.status == "pass_strong"
    assert result.reason == "PASS_STRONG"
    assert result.metrics["close"] == 100.0
```

Run the focused file and then the full suite:

```bash
pytest tests/test_l3_v2_pipeline_qfq.py -v
pytest tests/ -v
```

## 7. Rollout sequence (backfill first, then Phase 3 trigger switch)

1. Implement the collector, pipeline selection, and tests without changing the live trigger.
2. Run the focused and full test suites.
3. Back up the production SQLite database using the project's normal operational procedure.
4. Perform an initial explicit backfill before installing cron. Because the CLI unit is calendar days, use a conservative window rather than relying on the default:

   ```bash
   cd /home/lin/a-stock-tracker
   .venv/bin/python scripts/fetch_qfq_daily_bars.py --backfill-days 200
   ```

5. Query `daily_bars` by code and `adjusted='qfq'`; require every intended Phase 3 watchlist code to have at least 120 rows through the expected BaoStock date. Investigate failures rather than treating unadjusted fallback as completed coverage.
6. Run `pipeline.py daily` in the existing shadow mode and verify that covered, non-freefall names emit `pass_strong`, while missing/short names remain `pass_weak` or `unavailable` as specified. Confirm no historical predictions are rewritten.
7. Add the 16:00 managed cron rule. Observe collector logs, per-code QFQ counts, and shadow v2 outcomes through multiple natural weekday runs.
8. Only after coverage and signal-quality checks pass, write/approve the separate Phase 3 change that makes v2 the authoritative push trigger. Phase 2 completion does not itself authorize that switch.

Rollback is operationally simple: disable/remove the managed QFQ cron rule and revert the QFQ-preference code. Existing `adjusted='none'` rows are untouched. Retained QFQ rows are inert under the old wrapper and need not be deleted; avoiding deletion preserves auditability and makes rollback reversible.

## 8. Risks & open questions

- **Calendar-day window:** 200 calendar days (the revised default) reliably covers 120+ A-share trading sessions in all normal years (~134 sessions). The initial rollout backfill uses `--backfill-days 200`; daily incremental runs continue using the default 200 to keep the rolling window fresh.
- **BaoStock availability and hangs:** direct calls lack the isolated provider's subprocess timeout. Per-code exceptions isolate ordinary failures, but not a blocked native/network call. Observe runtime; if hangs occur, migrate the collector to `IsolatedBaoStockMarketDataProvider` or add an outer process timeout through the established cron mechanism.
- **T-1 timing:** at 16:00, BaoStock may still expose only T-1. That is acceptable under the stated contract, but Phase 3 must explicitly decide whether a T-1 panel is fresh enough for same-day pushes. This phase sets `is_stale=False` exactly as required and does not introduce a freshness calculation.
- **Adjustment revisions:** QFQ history can change after corporate actions. The rolling re-fetch plus upsert deliberately refreshes overlapping dates. A 150-day daily window only revises that window; occasional longer reconciliation backfills may be needed.
- **Volume semantics:** price adjustment does not imply volume adjustment. Confirm BaoStock daily volume is stored in the chosen `volume_unit` (`share` is proposed in the skeleton). An incorrect unit does not affect the current no-tech-gate candidate but could affect other v2 variants.
- **Source consistency:** the QFQ contract requires `source="baostock.qfq"`. The collector and pipeline must treat any differently sourced `adjusted='qfq'` rows as an explicit future contract decision, not silently label mixed provenance as BaoStock.
- **Partial successes and exit status:** continuing per code is required. The open operational choice is whether any partial failure should make the overall exit nonzero and trigger cron alerting, or remain zero with warnings. The baseline design remains zero after a valid session so one symbol does not classify the entire daily run as failed; add a final failure summary and threshold if operations needs stronger alerting.
- **Trading calendar and delistings:** suspended or newly listed stocks may legitimately have fewer than 120 rows. They must remain on the unadjusted fallback (or become `INSUFFICIENT_WINDOW`) and must not be padded, forward-filled, or mixed across adjustment modes.
- **Concurrency:** the 16:00 job must finish before 16:30. SQLite commits per code minimize lock duration, but the implementation should monitor total runtime and avoid overlapping manual/backfill and cron runs. If overlap becomes plausible, use the project's established process-lock convention.
