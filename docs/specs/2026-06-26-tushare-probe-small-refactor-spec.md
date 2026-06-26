# Tushare Probe Small Refactor Spec

Date: 2026-06-26

## Background

`scripts/probe_tushare_market_data.py` currently mixes four responsibilities in one linear flow:

- loading runtime configuration
- running Tushare daily, index, and trade calendar checks
- validating Tushare daily close prices against a BaoStock-backed reference source
- rendering the Markdown report and deciding the process exit code

The latest real probe run showed:

- `tushare.daily` samples returned valid rows
- BaoStock isolated reference close checks passed for the same trade date
- `tushare.index_daily` and `tushare.trade_cal` returned `RATE_LIMITED` because those endpoints allow only `1次/分钟`

The current `passed` condition treats every check as equally blocking:

```python
passed = all(item["status"] != "failed" for item in checks) and close_status == "PASS"
```

This makes non-critical capability rate limits override a successful daily close cross-check, producing a report-level `FAIL` even when the daily write safety gate has passed.

## Decision

Do a small structural refactor of the probe script before changing behavior. This is not a large rewrite and not a one-line patch.

The refactor should separate:

- **write gate**: whether daily production writes may proceed
- **capability checks**: whether optional/index/calendar-dependent capabilities are currently healthy
- **reporting**: how both decisions are shown to PM and operators

The process exit code should represent the write gate result only.

## Non-Goals

- Do not modify `a_stock_lib.providers` public interfaces.
- Do not modify BaoStock isolated reference source behavior.
- Do not introduce concurrency, async IO, multiprocessing, or background workers.
- Do not add a long `sleep(60+)` path to wait out known Tushare endpoint limits.
- Do not add a persistent TTL cache in this step.
- Do not restore cron or enable production writes as part of this change.
- Do not change tracker data write paths.

## Scope

Primary file:

- `scripts/probe_tushare_market_data.py`

Tests:

- `tests/test_probe_tushare_market_data.py`

Generated/updated report:

- `docs/reviews/YYYY-MM-DD-tushare-capability-probe.md`

## Proposed Internal Model

Keep the script as a single file, but introduce typed internal records and pure decision helpers.

Suggested records:

```python
class ProbeCheck(NamedTuple):
    label: str
    kind: str
    blocking: bool
    code: str
    status: str
    source: str
    error_code: str
    error_message: str
    rows: int
    elapsed_ms: float
    latest_trade_date: str
    latest_close: float | None


class ProbeDecision(NamedTuple):
    write_gate_status: str
    capability_status: str
    exit_code: int
    production_decision: str
    reason: str
```

Allowed `kind` values:

- `daily`
- `index_daily`
- `trade_cal`

Allowed write gate statuses:

- `PASS`
- `FAIL`
- `MANUAL_REQUIRED`

Allowed capability statuses:

- `PASS`
- `DEGRADED`
- `BLOCKED`

## Write Gate Rules

The write gate is the only source of the script exit code.

Return exit code `0` only when:

- token is configured
- all blocking daily checks are not `failed`
- close cross-check status is `PASS`

Return exit code `1` when:

- token is missing
- any blocking daily check is `failed`
- close cross-check status is `FAIL`
- close cross-check status is `MANUAL_REQUIRED`

When token is missing:

- `Write Gate` must be `FAIL`
- `Production Decision` must be `DAILY_WRITES_BLOCKED`
- `Index/Calendar Dependent Jobs` must be `HOLD`

`index_daily` and `trade_cal` must not affect the write gate exit code.

## Capability Rules

Capability checks include:

- `index_daily 000300`
- `trade_cal SSE`

Capability result classification:

- all capability checks are ok: `PASS`
- one or more capability checks are `RATE_LIMITED`: `DEGRADED`
- if any capability check fails with an error other than `RATE_LIMITED`: `BLOCKED`
- mixed `RATE_LIMITED` and non-`RATE_LIMITED` failures: `BLOCKED`

The classification must be a default-deny rule. New or unexpected error codes, including `TIMEOUT` and `UNKNOWN_ERROR`, must classify capability status as `BLOCKED` unless they are explicitly added to the degraded whitelist later.

`DEGRADED` means:

- daily close write gate may still pass
- jobs depending on index/calendar freshness must remain on hold
- PM should not interpret the full Tushare account capability as verified

## Report Contract

The report must stop using a single ambiguous `PASS`/`FAIL` result.

Required report fields:

```text
Write Gate: PASS|FAIL|MANUAL_REQUIRED
Capability Checks: PASS|DEGRADED|BLOCKED
Production Decision: DAILY_WRITES_ALLOWED|DAILY_WRITES_BLOCKED
Index/Calendar Dependent Jobs: ALLOWED|HOLD
```

`Index/Calendar Dependent Jobs` must be `ALLOWED` only when:

- `Write Gate == PASS`
- `Capability Checks == PASS`

All other combinations must render `Index/Calendar Dependent Jobs: HOLD`.

The existing close cross-check table should remain unchanged.

The existing raw check table may remain, but it should include whether a check is blocking.

The bottom note should be rewritten from:

```text
Close-price cross-check must be PASS before enabling production writes.
```

to:

```text
Daily production writes require Write Gate PASS. Capability DEGRADED/BLOCKED means index/calendar-dependent jobs remain on hold.
```

## Expected Behavior Matrix

| Daily samples | Close cross-check | index_daily | trade_cal | Exit | Write Gate | Capability | Production Decision |
|---|---|---|---|---:|---|---|---|
| ok | PASS | ok | ok | 0 | PASS | PASS | DAILY_WRITES_ALLOWED |
| ok | PASS | RATE_LIMITED | RATE_LIMITED | 0 | PASS | DEGRADED | DAILY_WRITES_ALLOWED |
| ok | PASS | RATE_LIMITED | ok | 0 | PASS | DEGRADED | DAILY_WRITES_ALLOWED |
| ok | PASS | SCHEMA_CHANGED | ok | 0 | PASS | BLOCKED | DAILY_WRITES_ALLOWED |
| ok | PASS | TIMEOUT | ok | 0 | PASS | BLOCKED | DAILY_WRITES_ALLOWED |
| ok | PASS | UNKNOWN_ERROR | ok | 0 | PASS | BLOCKED | DAILY_WRITES_ALLOWED |
| failed | PASS or missing | any | any | 1 | FAIL | any | DAILY_WRITES_BLOCKED |
| ok | FAIL | any | any | 1 | FAIL | any | DAILY_WRITES_BLOCKED |
| ok | MANUAL_REQUIRED | any | any | 1 | MANUAL_REQUIRED | any | DAILY_WRITES_BLOCKED |
| token missing | any | any | any | 1 | FAIL | any | DAILY_WRITES_BLOCKED |

## Test Plan

Add focused tests without real network calls.

Required tests:

- daily checks ok + close cross-check PASS + both capability checks `RATE_LIMITED` returns exit code `0`
- daily checks failed returns exit code `1` even if capability checks pass
- close cross-check `FAIL` returns exit code `1`
- close cross-check `MANUAL_REQUIRED` returns exit code `1`
- capability `RATE_LIMITED` renders `Capability Checks: DEGRADED`
- capability schema/auth style hard failure renders `Capability Checks: BLOCKED`
- capability `TIMEOUT` or `UNKNOWN_ERROR` renders `Capability Checks: BLOCKED`
- missing token renders `Write Gate: FAIL` and exit code `1`
- report includes `Write Gate`, `Capability Checks`, `Production Decision`, and `Index/Calendar Dependent Jobs`
- `Index/Calendar Dependent Jobs` renders `ALLOWED` only when both write gate and capability checks pass
- raw check rows include blocking/non-blocking distinction
- pure decision helper tests cover `ProbeDecision` construction from explicit mock `ProbeCheck` inputs

Existing tests for BaoStock fallback reference source must continue to pass.

## Validation Commands

From `/home/lin/a-stock-tracker`:

```bash
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/python -m pytest tests/ -q
```

Optional real dry-run after unit tests:

```bash
.venv/bin/python scripts/probe_tushare_market_data.py
```

Expected real dry-run result under current Tushare endpoint frequency limits:

- exit code `0` if daily close cross-check remains `PASS`
- report shows `Write Gate: PASS`
- report shows `Capability Checks: DEGRADED` when `index_daily` or `trade_cal` returns `RATE_LIMITED`

## Rollback Plan

If the refactor produces ambiguous behavior or fails validation:

- revert only `scripts/probe_tushare_market_data.py`
- revert only new/updated `tests/test_probe_tushare_market_data.py` cases
- keep generated probe reports out of the rollback unless they were committed as part of the implementation

Do not revert unrelated current worktree state such as existing probe reports or `backups/`.

## Review Questions for agy

- Does this spec preserve the safety invariant that daily writes require same-date close cross-check PASS?
- Does it accidentally permit index/calendar-dependent jobs when capability checks are degraded?
- Is exit code `0` for `RATE_LIMITED` capability checks acceptable when write gate is PASS?
- Are there missing hard failure classes that should make capability status `BLOCKED`?
- Is avoiding TTL cache in this step the right tradeoff for Phase 4 risk control?
