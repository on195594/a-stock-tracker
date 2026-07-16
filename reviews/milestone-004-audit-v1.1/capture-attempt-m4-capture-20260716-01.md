# MILESTONE-004 capture-first attempt record

Attempt ID: `m4-capture-20260716-01`
Authorization ID: `qualitative-v2-m4-capture-authorization-2026-07-16-01`
Authorization SHA-256: `46f0ffe1bc54e0512f5aa274394d91f2d7fbd68cda3721feaa1cfa3bbd4c9faa`
Attempted at: `2026-07-16T17:15:00+08:00`
Status: **sealed incomplete; no retry and no assembly**

## Result

The one authorized capture exited with code 2 when the first frozen SWS component request
(`index_component_sw:801010`) failed strict TLS verification with `SSLError`. No response bytes existed for that call,
so it and the remaining 30 SWS calls are recorded as missing.

Two earlier responses were published raw-first and retained:

- `tushare:daily_basic:20260715`: captured at `2026-07-16T17:15:08.518003+08:00`; provider code `40203` reported
  the account limit of five `daily_basic` requests per day. The receipt is `raw_only`, so it is not reusable data.
- `akshare:stock_sse_summary`: captured at `2026-07-16T17:15:09.377316+08:00`; parsing status `parsed`.

The sealed `incomplete-capture-manifest.json` has SHA-256
`905fda74cae7a3b916e430e2554c1bce69aee7538cf638cea9d3415b62eb535d`, two call records, and 31 missing call IDs.
Offline `_verify_attempt(..., require_complete=False)` passed against the frozen SZSE package. No `.in-progress-*`
directory or complete attempt remains.

## Boundary

The authorization was single-use and did not permit automatic retry or resume. No Reviewer/model, production database,
production pipeline, cron, Telegram, or company evidence path was accessed. The incomplete attempt cannot be assembled.

A future live attempt needs a new canonical authorization and should not be attempted until the Tushare daily quota has
reset and the SWS TLS failure has been separately resolved or replaced by a provenance-bearing static source package.

## Offline TLS diagnosis

The installed AKShare `index_component_sw` implementation hardcodes `requests.get(..., verify=False)`. The capture
transport intentionally overrides that unsafe setting with `verify=True`, as required by the frozen authorization
boundary. The local environment uses Requests 2.33.1, Certifi 2026.02.25, and OpenSSL 3.5.6, with no HTTPS proxy,
`REQUESTS_CA_BUNDLE`, or `SSL_CERT_FILE` override detected.

No certificate validation was disabled and no diagnostic network request was made. Repeating the same adapter without
new evidence is not justified. The next safe path is a separately authorized strict-TLS diagnostic or a
provenance-bearing official static package; accepting AKShare's `verify=False` behavior is prohibited.
