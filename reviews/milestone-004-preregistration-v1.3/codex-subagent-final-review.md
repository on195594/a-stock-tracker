# Codex subagent final review — MILESTONE-004 v1.3

Review date: `2026-07-17` (`Asia/Shanghai`)
Reviewer: Codex subagent `Euler`
Agent ID: `019f6df9-506d-7121-b4a6-433606e94164`
Prompt: `reviews/milestone-004-preregistration-v1.3/codex-subagent-final-review-prompt.md`
Candidate: `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md`
Required candidate SHA-256: `a9099d6e92bf1113c77064706ef9a4b1e6e5e86bd4c3cf222109f53923429ecf`
Disposition: **FAIL — CHANGES REQUIRED — NOT APPROVED**

## Verbatim review result

VERDICT: FAIL

P0: NONE

P1: P1-1 — `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md:57-60,146-153,266-271,302-304`
merely binds the path/hash of an unspecified date-selection artifact; it defines neither its schema, authoritative
calendar inputs, derivation algorithm, as-of instant, nor a freshness relationship to the capture window, and offline
verification need not semantically re-derive its claim. A stale or false artifact can therefore authorize a non-latest
sampling date and change the frame, median split, and sample. Correction: freeze a canonical date-evidence schema and
deterministic latest-complete-common-day derivation from hash-bound authoritative inputs; bind its as-of cutoff and
constrain the execution window so no newer day can become complete; require offline semantic re-derivation and
stale/false/window-crossing tests.

P2: P2-1 — `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md:217-224,261-270` does not
completely define the exclusion-ledger bytes: the outer schema, exact property names/types, provider-row ordinal base,
canonical serialization, representation of `source_kind`/`l1_code_or_empty`, and whether `name` is raw or trimmed are
unspecified. Independent implementations therefore cannot reproduce the required ledger hash or reliably test
omission/reordering. Correction: freeze the complete ledger JSON schema, index bases, value-normalization rules,
canonical byte encoding, hash preimage, and golden-vector tests. P2-2 —
`docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md:128-134,169-180,261-269` requires receipts
to contain blob references/hashes while token-echo, transport, and response-ceiling failures can legitimately produce
no persisted blob; it also partitions ordinals only into completed and unattempted without defining the
attempted-but-failed state. Correction: define an exact ordinal state machine and receipt variants, including when
blob fields must be absent/null, and manifest partitions for successful, terminally failed, and unattempted ordinals;
require corresponding offline-rederivation tests.

P3: NONE

FREEZE: CHANGES_REQUIRED

## Gate decision

The approval condition required `VERDICT: PASS`, `P0: NONE`, `P1: NONE`, `P2: NONE`, `P3: NONE`, and
`FREEZE: APPROVE_EXACT_BYTES`. It was not met. No approval SHA-256 or approval record is published. This review does
not authorize protocol edits, implementation, credentials, or network execution.
