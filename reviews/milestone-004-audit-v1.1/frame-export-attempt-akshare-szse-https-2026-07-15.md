# MILESTONE-004 AKShare plus direct SZSE HTTPS frame export attempt

Attempt ID: `qualitative-v2-m4-akshare-szse-https-frame-export-attempt-2026-07-15-01`
Authorization: `qualitative-v2-m4-akshare-szse-https-frame-export-2026-07-15-01`
Attempted at: `2026-07-15` after 16:00 (`Asia/Shanghai`)
Status: **blocked before package publication**

## Result

The one authorized execution completed the initial AKShare `stock_sse_summary` call, then stopped fail-closed when the
direct request to `https://www.szse.cn/api/report/ShowReport` raised a transport `ConnectionError`. The request was
date-bound, certificate-verified, streaming, redirect-disabled, and restricted to the exact official HTTPS origin.

No HTTP downgrade, alternate hostname, alternate provider, or second execution was attempted.
`artifacts/milestone-004/incoming/` remained empty, so there is no static frame package, provenance manifest, raw
snapshot set, `SHA256SUMS`, frame stage, or sample stage. A partial upstream response is not retained or represented as
audit evidence.

## Calls and containment

- The execution reached AKShare `stock_sse_summary` and the direct SZSE HTTPS interface only.
- AKShare `stock_zh_a_spot_em` and all 31 `index_component_sw` calls were not reached.
- The direct SZSE request used `SHOWTYPE=xlsx`, catalog `1803_sczm`, tab `tab1`, and the SSE-derived ISO source date.
- No production `tracker.db` or pipeline path was opened; no Reviewer/model process ran; the disabled production
  AKShare/东方财富 market-data entry point was not restored.
- No credential, response body, or partial package was written to the repository or incoming directory.

## Unblock conditions

The current runtime cannot establish the required direct HTTPS connection to the SZSE interface. Further progress
requires one of:

1. a user-provided provenance-bearing static SZSE market-overview XLSX from the official HTTPS page;
2. execution of the bounded exporter in a network environment that can reach the same official HTTPS interface; or
3. separate authorization for a different date-integrity source.

The frame, sample, corpus, Reviewer, adjudication, and report stages remain unfrozen.

## 2026-07-16 follow-up

An isolated Microsoft Playwright MCP container with bundled headless Chromium successfully downloaded the exact
date-bound SZSE URL without a cross-domain redirect. Two independently generated XLSX byte streams were retained
because their raw SHA-256 values differ while their normalized 14-row tables are identical. Raw bytes, provenance and
`SHA256SUMS` are read-only under `artifacts/milestone-004/incoming/manual-szse/` (gitignored).

This recovers only the SZSE portion. It does not retroactively complete the failed 2026-07-15 execution and cannot be
combined with 2026-07-16 market data. Same-date Eastmoney total-market-cap and all 31 SWS component snapshots remain
required before a frame can be frozen.
