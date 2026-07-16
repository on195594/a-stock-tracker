# MILESTONE-004 historical hybrid pagination attempt

Attempt ID: `qualitative-v2-m4-historical-hybrid-frame-export-attempt-2026-07-16-03`
Authorization: `qualitative-v2-m4-historical-hybrid-frame-export-2026-07-16-03`
Attempted at: `2026-07-16T12:05:42+08:00`
Status: **blocked before package publication**

## Authorization

The user instructed the agent to continue after the prior hourly window. The agent treated that instruction as one new
execution with the unchanged allowlist: Tushare `daily_basic(trade_date=20260715)`, AKShare `stock_sse_summary`, 31
`index_component_sw` calls, and the sealed SZSE 2026-07-15 package. Production `tracker.db`, pipeline, and Reviewer
access remained prohibited.

## Result

The attempt started about 23 minutes after the confirmed hourly limit cleared. The exporter verified the SZSE package,
called the SSE summary, and received a successful Tushare data response. The strict client rejected the response because
the newly observed `count` metadata did not equal the number of returned `items`. No SWS request was reached.

The official Python SDK consumes `fields` and `items` and does not expose this additive metadata. The local client was
therefore corrected offline to use `has_more` as the truncation gate, while requiring `count` to remain a non-negative
integer no smaller than the returned page. Both values are now retained in successful provenance. Synthetic regression
tests cover a complete page whose total `count` exceeds its current item count, a truncated page, and invalid metadata.

The temporary directory was removed and no package or frozen stage was published. No additional provider call was made
under this authorization.

## Unblock condition

Another live attempt requires new explicit authorization and must begin no earlier than one hour after 12:05:42, with
additional safety margin recommended. If the next response reports `has_more=true` or a count smaller than its item
page, the exporter will remain blocked with sanitized numeric diagnostics.
