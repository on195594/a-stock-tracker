# MILESTONE-004 Tushare frame export attempt

Attempt ID: `qualitative-v2-m4-tushare-frame-export-attempt-2026-07-15-01`
Authorization: `qualitative-v2-m4-tushare-frame-export-2026-07-15-01`
Attempted at: `2026-07-15T17:41:09+08:00`
Status: **blocked before package publication**

## Result

The bounded exporter stopped fail-closed before creating `frame.csv`, provenance, or any frozen audit stage. The
configured Tushare account reported an effective per-account limit of one `stock_basic` request per hour and one
`trade_cal` request per hour. The originally planned 31 `index_member_all` L1 requests therefore cannot produce a
coherent same-run snapshot within this execution window.

`artifacts/milestone-004/incoming/` remained empty. No partial response was published or treated as audit evidence.

## Calls and containment

- Bounded schema-diagnostic retries were made only to authorized `stock_basic` and `trade_cal` endpoints.
- `index_classify`, `index_member_all`, and `daily_basic` were not reached.
- The first production envelopes exposed additional provider metadata; the exporter was tightened to accept only the
  observed metadata keys and to redact the token from provider error messages.
- No request body, token, credential, raw partial response, or provider error payload was written to disk.
- No database was opened; no pipeline, cron, Reviewer, model, or evidence-retrieval path was invoked.

## Unblock conditions

One of the following is required before a new export attempt:

1. a Tushare token with sufficient documented frequency for a bounded same-run snapshot;
2. a complete pre-exported Tushare static frame package with provenance and hashes; or
3. separate authorization for a different provider/static dataset.

The frame, sample, corpus, Reviewer, adjudication, and report stages remain unfrozen.
