# Second independent Codex final rereview — MILESTONE-004 v1.3 exact bytes

Perform a fresh independent, read-only final review. This is a strict exact-byte freeze gate. Return PASS only with
no P0, P1, P2, or P3 finding. Do not edit any file, access a database, read environment variables or credentials,
make a network/provider request, invoke Tushare, run a probe, invoke another model/subagent, create authorization, or
approve implementation or execution.

First compute the candidate SHA-256. If it is not exactly the value below, return FAIL/P0 identity mismatch:

`01d223f3a85bd628fd15c88ae726dd75256bbb28e5e4f23e0b3c15da2db31374`

Primary candidate:

- `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md`

Required comparison/review inputs:

- `docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration.md`
- `docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration-v1.1.md`
- `docs/plans/2026-07-16-milestone-004-frame-source-capability-preregistration-v1.2.md`
- `reviews/milestone-004-audit-v1.2/capability-probe-2026-07-17.md`
- `reviews/milestone-004-preregistration-v1.3/agy-review.md`
- `reviews/milestone-004-preregistration-v1.3/agy-review-assessment.md`
- `reviews/milestone-004-preregistration-v1.3/p1-repair.md`
- `reviews/milestone-004-preregistration-v1.3/codex-subagent-final-review.md`
- `reviews/milestone-004-preregistration-v1.3/codex-subagent-final-review-repair.md`
- `reviews/milestone-004-preregistration-v1.3/codex-subagent-final-rereview.md`
- `reviews/milestone-004-preregistration-v1.3/codex-subagent-second-rereview-repair.md`
- `qualitative_v2_audit.py`, only for frozen taxonomy, super-strata, seed, frame validation, and sampling rules

Required checks:

1. Reassess every prior finding. Verify the exact 35-call matrix, both stock partitions, inherited v1 list/security/
   ST/age predicates, all source joins and exclusions, date cutoff boundaries, daily null-name vector, every process-
   control publication phase, credential stop paths, and corresponding tests.
2. Independently reproduce all embedded JSON-vector hashes plus the compact capability-authorization vector hash.
   Check exact no-extension authorization, receipt, stop, ledger, date-evidence, and manifest schemas, types, paths,
   states, nullability, partitions, closed file set, cross-field rules, canonical bytes, and tamper coverage.
3. Confirm the exact 31 L1 segmented calls remain all-and-only the frozen dictionary in frozen implementation order;
   the added SSE/SZSE stock calls restore inherited eligibility without changing the estimand; the daily date remains
   authorization-bound; and no pagination, retry, parallelism, fallback, or alternative source is admitted.
4. Confirm `.BJ` remains recognized/raw/ledgered but outside the SSE/SZSE frame; unknown suffixes and duplicate
   membership fail; raw rows are not silently discarded; row ceilings cannot conceal truncation.
5. Verify date evidence is hash-bound to authoritative SSE/SZSE calendar blobs, semantically rederived offline, valid
   only through the exact next-completion boundary, and incapable of contributing frame bytes.
6. Verify capability/capture/date isolation, permanent probe non-adoptability, fresh authorization, token containment,
   raw-first order, fail-fast behavior, TOCTOU/clock/lock/path controls, process propagation, offline-only verification
   and assembly, terminal dispositions, and absence of network authority.
7. Confirm v1.1 estimand, taxonomy, six super-strata, median split, sample size, seed/hash message, evidence sources,
   Reviewer/adjudication, coverage gates, and historical artifacts/hashes remain unchanged.
8. Identify any ambiguity, contradiction, unverifiable assumption, state gap, or missing test that can admit/reject the
   wrong frame, alter the sample, conceal truncation, permit unauthorized adoption/execution, or prevent independent
   byte-for-byte reproduction. P3 is also freeze-blocking.

Severity:

- P0: secret exposure, unauthorized external action, historical corruption, or identity mismatch.
- P1: frame/sample correctness or reproducibility flaw, concealed truncation, or unauthorized adoption/execution.
- P2: material ambiguity, incomplete fail-closed rule/schema/state, or missing mandatory test.
- P3: concrete non-blocking clarity/maintainability defect that still must be fixed before freeze.

Return exactly five lines and no other text:

```text
VERDICT: PASS|FAIL
P0: NONE|<findings>
P1: NONE|<findings>
P2: NONE|<findings>
P3: NONE|<findings>
FREEZE: APPROVE_EXACT_BYTES|CHANGES_REQUIRED
```

Every finding must cite exact file/lines, failure mode, and protocol-level correction.
