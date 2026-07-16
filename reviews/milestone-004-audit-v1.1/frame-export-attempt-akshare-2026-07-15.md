# MILESTONE-004 AKShare frame export attempt

Attempt ID: `qualitative-v2-m4-akshare-frame-export-attempt-2026-07-15-01`
Authorization: `qualitative-v2-m4-akshare-frame-export-2026-07-15-01`
Attempted at: `2026-07-15` after 16:00 (`Asia/Shanghai`)
Status: **blocked before package publication**

## Result

The one authorized exporter execution stopped fail-closed when AKShare `stock_szse_summary` raised a transport
`ConnectionError` under the exporter's mandatory HTTPS and certificate-verification policy. The exporter did not
downgrade the SZSE request to plaintext HTTP, retry the real execution, or publish a partial package.

`artifacts/milestone-004/incoming/` remained empty. Consequently there is no frame dataset, provenance manifest, raw
snapshot set, `SHA256SUMS`, frame stage, or sample stage from this attempt. A partial upstream response is not retained
or represented as audit evidence.

## Calls and containment

- The execution reached `stock_sse_summary` and `stock_szse_summary` only.
- `stock_zh_a_spot_em` and all 31 `index_component_sw` calls were not reached.
- The transport guard restricted each function to its approved official/provider host, forced HTTPS, enabled TLS
  certificate verification, disabled redirects, and imposed per-response and aggregate byte limits.
- No production `tracker.db` or pipeline path was opened; no Reviewer/model process ran; the disabled production
  AKShare/东方财富 market-data entry point was not restored.
- No credential, response body, or partial package was written to the repository or incoming directory.

## Unblock conditions

A new real attempt requires separate user authorization and one of:

1. a provenance-bearing pre-exported static package containing the four approved source snapshots;
2. an independently approved transport/source for the SZSE same-date integrity check; or
3. an approved static dataset from another provider.

The frame, sample, corpus, Reviewer, adjudication, and report stages remain unfrozen.
