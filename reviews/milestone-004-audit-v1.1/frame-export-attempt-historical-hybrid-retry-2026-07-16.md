# MILESTONE-004 historical hybrid frame export retry

Attempt ID: `qualitative-v2-m4-historical-hybrid-frame-export-attempt-2026-07-16-02`
Authorization: `qualitative-v2-m4-historical-hybrid-frame-export-2026-07-16-02`
Attempted at: `2026-07-16T10:42:15+08:00`
Status: **blocked before package publication**

## Authorization

The user explicitly approved one retry after the Tushare frequency window cleared. The scope was limited to Tushare
`daily_basic(trade_date=20260715)`, AKShare `stock_sse_summary`, 31 `index_component_sw` calls, and reuse of the sealed
official SZSE 2026-07-15 package. Production `tracker.db`, production pipeline, and Reviewer execution remained
prohibited; all date, pagination, and completeness errors remained fail-closed.

## Result

The exporter verified the local SZSE package and called the official SSE summary. Tushare then rejected
`daily_basic(trade_date=20260715)` with an account-specific limit of one request per hour. This supersedes the earlier
one-request-per-minute assumption for this endpoint. No SWS component request was reached.

The exporter returned its sanitized blocking status, removed its temporary directory, and published no hybrid package,
frame, sample, provenance manifest, checksum manifest, or frozen stage. The only incoming entry remains the unchanged
read-only `manual-szse` package.

## Containment

- No token, request body, or credential was written or printed.
- No HTTP downgrade, redirect, alternate source, or automatic retry occurred.
- No production database, pipeline, cron, model, or Reviewer path was accessed.

## Unblock condition

Another live attempt requires a new explicit authorization and must start no earlier than one hour after this attempt,
with additional safety margin recommended. A provenance-bearing static `daily_basic` snapshot for 2026-07-15 remains
the non-live alternative.
