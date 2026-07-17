# Second independent Codex final rereview — MILESTONE-004 v1.3

Review date: `2026-07-17` (`Asia/Shanghai`)
Reviewer: Codex subagent `Poincare`
Agent ID: `019f6e18-a7d1-7593-b425-4fa49741c086`
Prompt: `reviews/milestone-004-preregistration-v1.3/codex-subagent-second-rereview-prompt.md`
Candidate SHA-256: `01d223f3a85bd628fd15c88ae726dd75256bbb28e5e4f23e0b3c15da2db31374`
Disposition: **FAIL — CHANGES REQUIRED — NOT APPROVED**

## Verbatim result

VERDICT: FAIL

P0: NONE

P1: P1-1 — `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md:159-163,249-252,
309-312,359,636,654` validates time only before calls and imposes no transport deadline or receipt/manifest upper
bound; a capture begun at 17:59:59 may complete after the 18:00 evidence-expiry boundary and remain adoptable,
permitting out-of-window execution and a stale sampling date. Correction: cap each transport at
`not_after`/`valid_until`, fail closed on boundary overrun, require all receipt and sealing times within the
authorization window, and add crossing-boundary tests. P1-2 —
`docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration.md:71-78` excludes CDRs and delisting-
consolidation securities, but
`docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md:544-571` admits the CDR-associated `689`
family and defines no delisting-consolidation predicate; ineligible securities can enter the frame and alter the
sample. Correction: bind authoritative security-type/status inputs, exclude CDR and delisting-consolidation rows
explicitly, remove or disambiguate `689`, and add positive/negative boundary tests.

P2: NONE

P3: NONE

FREEZE: CHANGES_REQUIRED

## Gate decision

The strict approval condition was not met. No v1.3 approval SHA-256 or approval record was published.
