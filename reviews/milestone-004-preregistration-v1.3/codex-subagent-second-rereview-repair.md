# Second Codex rereview repair record — MILESTONE-004 v1.3

Repair date: `2026-07-17` (`Asia/Shanghai`)
Source review: `reviews/milestone-004-preregistration-v1.3/codex-subagent-final-rereview.md`
Repaired protocol: `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md`
Repaired candidate SHA-256: `01d223f3a85bd628fd15c88ae726dd75256bbb28e5e4f23e0b3c15da2db31374`
Disposition: **P1-1, P1-2, and P2-1 through P2-4 addressed; pending a new independent Codex final review**

## Resolution of P1 findings

- Expanded both frame modes from 33 to 35 calls by adding exact, exchange-partitioned SSE/SZSE `stock_basic`
  requests and their authorization-bound fields.
- Added fail-closed 6,000-row partition ceilings and exact joins to every SSE/SZSE membership.
- Reproduced every inherited v1 eligibility predicate: listed status, ordinary RMB A-share exchange/board/code-family
  tuples, ST name normalization/prefix test, and exact 36-calendar-month anniversary.
- Added deterministic stock/membership/daily exclusion reasons and raw-row hashes, final-frame name provenance, gates,
  offline rederivation, and boundary tests.
- Aligned prior-day evidence validity with the 18:00 completion rule: it now remains valid through 17:59:59 on the
  next common trading day and expires at 18:00:00.

## Resolution of P2 findings

- Daily ledger entries now require `name=null`, with a normative typed-numeric row vector and complete daily-ledger
  vector whose hashes reproduce offline.
- Split process-control handling at every publication boundary: no blob before blob fsync, blob-bearing terminal
  receipt after blob fsync, retained success after receipt fsync, and exact next/null stop-record behavior.
- Added exact `credential_unavailable` and `credential_invalid` pre-transport stop paths, a 64-lowercase-hex token
  shape check, no ordinal receipt/blob, and tests.
- Replaced open-ended authorization/manifest descriptions with exact no-extension schemas, exact reference/file/gate/
  disposition cross-field rules, canonical byte rules, fixed artifact paths, a capability authorization hash vector,
  a complete date-authorization byte vector, and a self-consistent stopped-manifest/supporting-file vector.

Offline checks reproduced all eight embedded canonical JSON hashes, the exact 35-call/31-industry order, historical
v1/v1.1/v1.2 hashes, and the existing 33-test M4 audit suite. This record does not approve implementation,
authorization, credentials, provider access, or execution.
