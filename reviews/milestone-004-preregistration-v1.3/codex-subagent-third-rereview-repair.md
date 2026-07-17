# Third Codex rereview repair record — MILESTONE-004 v1.3

Repair date: `2026-07-17` (`Asia/Shanghai`)
Source review: `reviews/milestone-004-preregistration-v1.3/codex-subagent-second-rereview.md`
Repaired candidate SHA-256: `5bde2a84c2642f6c659aa5d52b8efa04b9e0569097d50284b82c40d6fb675961`
Disposition: **both remaining P1 findings addressed; pending a third fresh independent Codex final review**

## P1-1 — hard execution deadline

- Defined one `execution_deadline` from authorization expiry and capture evidence validity.
- Applied one remaining-time hard watchdog to DNS, connect, TLS, request, complete response, token scan, blob fsync,
  parse, receipt fsync, gate evaluation, and PASS-manifest publication.
- Added exact before/after-blob window-overrun receipt codes, authorization status category, stop behavior, and a
  mandatory `authorization_window` manifest gate.
- Added post-fsync times to receipt/blob references, exact deadline/manifest cross-fields, and a rule that post-window
  artifacts can only seal a failure disposition and are never adoptable.
- Added crossing tests at each network/publication phase and exact boundary tests.

## P1-2 — CDR and delisting consolidation

- Removed `689` from the accepted ordinary-A-share code families and explicitly rejects it.
- Added an exact date-bound `stock_st(trade_date=...)` call and fields, raising the frame matrix to 36 calls.
- Added the official 1,000-row fail-closed ceiling, complete raw-row/ledger treatment, and historical ST join.
- Added explicit NFKC official-name delisting predicates (`退市` prefix or `退` suffix), a dedicated
  `delisting_consolidation` reason, deterministic priority, and source-specific ledger/count/manifest rules.
- Added tests for `689`, CDR/board/code-family crossings, delisting name boundaries, empty/full/error ST lists, and
  stock-name versus historical-ST disagreement.

No approval, implementation authority, credential access, provider probe, or execution authority is granted here.
