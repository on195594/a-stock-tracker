# MILESTONE-004 historical hybrid count-sentinel attempt

Attempt ID: `qualitative-v2-m4-historical-hybrid-frame-export-attempt-2026-07-16-04`
Authorization: `qualitative-v2-m4-historical-hybrid-frame-export-2026-07-16-04`
Attempted at: `2026-07-16T13:34:00+08:00`
Status: **blocked before package publication**

## Authorization

The user explicitly instructed the agent to continue execution after the prior hourly window. The same one-run
allowlist remained in force: Tushare `daily_basic(trade_date=20260715)`, AKShare `stock_sse_summary`, 31
`index_component_sw` calls, and the sealed SZSE 2026-07-15 package. Production database, pipeline, and Reviewer access
remained prohibited.

## Result

The attempt began more than 88 minutes after the prior Tushare call. The exporter verified the SZSE package, called the
SSE summary, and received 5,525 Tushare rows with `count=0`. The strict parser rejected zero because it had treated every
non-null count as a total-record value. No SWS request was reached, no temporary artifact survived, and nothing was
published.

The observed pair (`count=0`, non-empty items) establishes zero as an unknown-count sentinel for this response shape.
The client was corrected offline to accept only either zero or a count at least as large as the current page, while
still requiring `has_more=false`. Successful provenance retains the raw count and flag. Regression tests cover the zero
sentinel and rejection of a nonzero count smaller than its page.

## Unblock condition

Another live attempt requires new explicit authorization and must begin no earlier than one hour after 13:34:00, with
additional safety margin recommended. No further call is authorized by this attempt.
