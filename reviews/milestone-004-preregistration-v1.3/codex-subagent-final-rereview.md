# Independent Codex final rereview — MILESTONE-004 v1.3

Review date: `2026-07-17` (`Asia/Shanghai`)
Reviewer: Codex subagent `Fermat`
Agent ID: `019f6e08-9815-7d71-bb2a-6a05409a68f0`
Prompt: `reviews/milestone-004-preregistration-v1.3/codex-subagent-final-rereview-prompt.md`
Candidate: `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md`
Required candidate SHA-256: `06f75727b53702cc34e638d12de92dfd282a2083597483bc09e6ce5c571d86ef`
Disposition: **FAIL — CHANGES REQUIRED — NOT APPROVED**

## Verbatim review result

VERDICT: FAIL

P0: NONE

P1: P1-1 — `docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration.md:59-78`,
`docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration-v1.1.md:10-14,76-82`, and
`docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md:55-114,437-446` conflict: v1.1 claims to
preserve v1’s sample space, but v1.3 captures no `stock_basic` fields and cannot enforce listed status, ordinary-A-
share/security type, ST exclusion, or 36-month listing age; ineligible securities can therefore change the frame,
median split, and sample. Correction: freeze authorization-bound inputs, fields, gates, receipts, and tests
reproducing every inherited eligibility predicate. P1-2 —
`docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md:149-158` defines day completion at 18:00
but expires prior-day evidence at the next common day’s 15:00, systematically preventing a valid prior-day capture
from 15:00 through 17:59:59. Correction: align `valid_until` with the frozen completion rule—17:59:59 before the next
18:00 cutoff—or redefine completion consistently, with boundary tests.

P2: P2-1 — `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md:100-105,361-395,419-433`
requires every exclusion entry to contain an exact raw `name`, but `daily_basic` has no `name` field; daily `.BJ` and
nonmember `.SH`/`.SZ` exclusions therefore have no reproducible valid ledger encoding. Correction: make daily `name`
exactly null or define a source-specific entry schema, then freeze sorting, hashes, and a daily golden vector. P2-2 —
`docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md:249-255,289-299,317-320` cannot represent
a process-control interrupt after a blob is fsync-sealed but before parsing or receipt publication: the process-
control variant requires null blob metadata while the sealed blob remains in the closed file set. Correction: define
phase-specific interruption states or a blob-bearing process-control receipt and test every publication boundary.
P2-3 — `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md:195-206,301-308,494-510` defines no
pre-transport stop code or test for a missing, unreadable, empty, or malformed token. Correction: add finite
credential-unavailable/invalid stop codes, exact no-receipt behavior, manifest propagation, and synthetic tests.
P2-4 — `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md:210-224,448-467` specifies
authorization only “at minimum” and lists manifest semantics without exact property names, types, extension policy,
or complete canonical schemas, preventing independent byte-for-byte reproduction and uniform authorization
validation. Correction: freeze exact no-extension authorization and manifest schemas, canonical examples, hashes,
and tamper tests.

P3: NONE

FREEZE: CHANGES_REQUIRED

## Gate decision

The strict approval condition was not met. No v1.3 approval SHA-256 or approval record was published. This review
does not authorize implementation, credentials, provider access, or network execution.
