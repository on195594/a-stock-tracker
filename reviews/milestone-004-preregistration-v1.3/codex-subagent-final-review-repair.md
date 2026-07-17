# Codex final-review repair record — MILESTONE-004 v1.3

Repair date: `2026-07-17` (`Asia/Shanghai`)
Source review: `reviews/milestone-004-preregistration-v1.3/codex-subagent-final-review.md`
Repaired protocol: `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md`
Repaired candidate SHA-256: `06f75727b53702cc34e638d12de92dfd282a2083597483bc09e6ce5c571d86ef`
Disposition: **P1-1, P2-1, and P2-2 addressed; pending a new independent Codex final review**

## P1-1 — capture-date evidence

- Added a separately authorized, exact two-call SSE/SZSE `trade_cal` evidence matrix with hash-bound raw inputs,
  fixed fields, fixed range arithmetic, byte ceilings, raw-first receipts, and no retries.
- Froze complete calendar-row validation and a deterministic as-of algorithm for the greatest completed common-open
  date, its next common-open date, and an inclusive evidence-validity interval.
- Required the capture authorization's entire window to remain inside that interval and to bind the exact canonical
  evidence path, hash, authorization ID, and attempt ID.
- Froze the complete date-evidence document schema and required offline verification to reopen raw calendar blobs,
  semantically rederive every date, and recheck capture-window inequalities.
- Added stale/false/tampered/calendar-range/window-crossing synthetic-test requirements.

## P2-1 — exclusion-ledger bytes

- Froze the exact five-property outer schema and exact entry property names, types, finite reason codes, and source
  constraints.
- Defined one-based provider-row and source ordinals, raw Unicode name preservation, JSON-null daily L1 fields, and
  non-mutating whitespace validation.
- Defined canonical UTF-8/LF JSON, typed-cell row-hash preimages, exact numeric lexeme preservation, explicit sort
  ranks and derived-only sort keys, and complete-ledger hash preimages.
- Added normative row and one-entry-ledger golden vectors with independently recomputed SHA-256 values.

## P2-2 — attempted failure without a blob

- Froze four ordinal states: `success_blob`, `terminal_failure_blob`, `terminal_failure_no_blob`, and `unattempted`.
- Froze the complete receipt schema, exact blob-field nullability, finite error/category mapping, and no receipt for an
  unattempted ordinal.
- Added an exact pre-transport stop-record schema and finite stop codes.
- Replaced completed/unattempted ambiguity with disjoint manifest partitions for successful, terminally failed, and
  unattempted ordinals, plus `failed_ordinal` and an exact `complete` predicate.
- Required state-transition, transport/token/size/process failure, partition, and data-gate-after-completion tests.

This repair record does not approve the candidate, implementation, authorization, credentials, or network execution.
