# MILESTONE-004 historical hybrid frame export attempt

Attempt ID: `qualitative-v2-m4-historical-hybrid-frame-export-attempt-2026-07-16-01`
Authorization: `qualitative-v2-m4-historical-hybrid-frame-export-2026-07-16-01`
Attempted at: `2026-07-16T10:00:00+08:00` through `2026-07-16T10:18:00+08:00`
Status: **blocked before package publication**

## Authorized scope

The user instructed the agent to execute the immediately preceding 2026-07-15 historical-data plan. The bounded
operation reused the sealed official SZSE 2026-07-15 XLSX package, allowed only Tushare `daily_basic` for
`trade_date=20260715`, and allowed only AKShare `stock_sse_summary` plus the 31 frozen `index_component_sw` queries.
It did not authorize production database or pipeline access, other providers, evidence retrieval, or Reviewer execution.

## Result

The first startup stopped during local SZSE semantic-hash preflight because the new importer used a different
normalization algorithm from the already sealed provenance. No live request occurred. The importer was corrected and
covered by a regression fixture before the bounded provider operation began.

The first provider pass reached the official SSE summary and Tushare `daily_basic`. Tushare returned a successful data
object with newly observed `count` and `has_more` metadata, which the strict client rejected as schema drift before raw
bytes were published. The client was tightened to validate those fields, reject truncation and inconsistent counts, and
convert provider errors through the exporter's fail-closed boundary. A single schema-repair retry then reached the same
two endpoints and was rejected by Tushare's effective one-request-per-minute `daily_basic` limit. No SWS component call
was made.

The temporary directories were removed on every blocked path. There is no hybrid `frame.csv`, sample manifest,
provenance manifest, `SHA256SUMS`, frame stage, or sample stage. The existing read-only SZSE package is unchanged.

## Containment

- No token or request body was printed or written.
- No provider error payload or partial successful response is represented as a frozen artifact.
- No `tracker.db` file, production pipeline, cron, Telegram, Sheets, Gemini, Codex, AGY, or Reviewer path was accessed.
- No HTTP downgrade, cross-domain redirect, substitute provider, or additional automatic retry was used.

## Unblock condition

A new explicit authorization may run the same bounded exporter after the Tushare frequency window has cleared, or the
user may provide a complete provenance-bearing static `daily_basic` snapshot for 2026-07-15. Frame and sample freezing
remain blocked until the full package is published and verified.
