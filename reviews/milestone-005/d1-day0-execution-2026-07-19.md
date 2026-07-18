# M5 D1 day-0 execution record

Status: **STOPPED — protocol/source capability drift; no corpus freeze**

Normative timezone: `Asia/Shanghai`

## Authorization

The user approved `m5-data-readiness-20260718-01` with exact authorization SHA-256
`93c471fb070376d7d2004b5df8898fc7c7628658c8a82906be9242f847dc0cf1`. The canonical authorization and checksum are:

- `reviews/milestone-005/m5-data-readiness-20260718-01.json`;
- `reviews/milestone-005/m5-data-readiness-20260718-01.sha256`.

The authorization was sealed before the window opened. Zero-call preflight reported `not_yet_valid`; execution waited for
the real clock and did not inject a synthetic `now`. At `2026-07-19T00:00:00+08:00`, active preflight passed with the
fixed sample SHA-256 `b278a7b00b71fd54e34519dead098602a8748635f1350414e1b78538a3d7635d`, 756 search groups,
4,536 maximum HTTP attempts, one read-only database transaction, and zero model/reviewer permissions.

## Read-only fundamentals snapshot

The authorized snapshot used SQLite `mode=ro`, `PRAGMA query_only=ON`, and exactly one transaction. The main database
file identity was byte-for-byte stable before and after the transaction. No database write, network call, credential
read, model call, pipeline execution, or production-cache access occurred.

- Snapshot SHA-256: `c9eae1ce87f9222eba5e636dccbf80f93f698908fa675c07ad27f175b0fe919e`.
- Company count: 36.
- Status: `missing=36`; therefore no fundamental metric value was emitted.
- Local artifact: `artifacts/milestone-005/data/m5-data-20260719-01/fundamentals-snapshot.json` (`0600` under a `0700`
  run root; Git-ignored).

## Official front-door capture

At `2026-07-19T00:03:06+08:00`, one GET was made to each frozen official front door. All redirects were restricted to
verified same-origin HTTPS, every response was capped at 64 MiB, and raw bytes plus provenance were create-only.

- Front-door manifest SHA-256: `94db197c7f7b9228cf67351be540ad3f9ad34983ab799512d0c03f5989a318f1`.
- HTTP attempts consumed: 4.
- CNINFO: HTTP 200, 78,608 bytes, raw SHA-256
  `bb0388f434c614371abeeb1a785d409f7aaafed9fd2b05aaf028504d01c2f8f4`.
- SSE: HTTP 200, 27,958 bytes, raw SHA-256
  `0acb2b6cebb4fd55691807bdc467e46f5bb5280486debaeb86d489e6e00fcdfe`.
- SZSE: `URLError`, no response bytes; same-day retry was not attempted.
- CNIPA: HTTP 412, 2,431 bytes, raw SHA-256
  `b36425c664f43b39dbf1ba0e26d94fe880d570a7254b4ac3606b1e1a6697e560`; same-day retry was not attempted.

The two successful pages explicitly referenced their query-controller scripts. Two further official GETs captured those
exact assets and bound them to the front-door manifest:

- UI-asset manifest SHA-256: `b54f5af2109e1e700c9b2e1d41a799ba376b2c9663791c6207d933366ab59ec4`.
- Parent front-door manifest SHA-256:
  `94db197c7f7b9228cf67351be540ad3f9ad34983ab799512d0c03f5989a318f1`.
- HTTP attempts consumed: 2.
- CNINFO controller SHA-256: `dddedad55676856a657815505f92e40bf28b4d0675a03a683f6f721156893e19`.
- SSE controller SHA-256: `e2685c1e237664ca9b4e792d2c5d95c59e2914deb51e0acd9c1c8b54c94e4227`.

Total authorized HTTP attempts consumed in this run: **6**, counted against the search budget. No document download or
relationship-report attempt occurred.

## Stop finding

The frozen CNINFO controller still exposes `searchType=1` as `标题+全文`, a 20-result page size, custom dates, and the
full-text endpoint. The frozen SSE controller exposes `TITLE` as the only keyword request parameter. Its UI text
`只看公告正文` changes only client-side main-announcement presentation: the controller removes `hasDelMain` before the
request and sends no full-text/body-search flag.

The v1.1 protocol requires the SSE keyword to apply to official announcement content. Treating the current `TITLE`
request as full-text without source evidence would silently weaken the protocol. This is therefore a source-capability
ambiguity/drift and activates the registered stop condition. SZSE and CNIPA also remain technical errors after their
first D-date attempts, but D+1/D+2 transport retries cannot cure the SSE contract mismatch.

No 756-group result matrix, candidate document set, relationship-report set, corpus manifest, coverage report, real M5
bundle, reviewer call, Claude call, or Gemini call was produced. Continuing requires a revised protocol and new exact
authorization, not an in-place relaxation of this run.

## v1.2 follow-up

The later offline capability probe did not resume this run or make an external call. It separated the three axes and
identified CNINFO as a viable full-text candidate route while leaving evidence quality unevaluated. Its report SHA-256
is `792743898f6805599f17b99061588eb1dab5d08d02d765b317a676be0ebca6fc`; see
`reviews/milestone-005/v1.2-capability-report-2026-07-19.md`.
