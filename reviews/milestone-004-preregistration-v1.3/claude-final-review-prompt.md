# Claude final review prompt — MILESTONE-004 v1.3 exact-byte candidate

Perform an independent, read-only final review of the exact MILESTONE-004 v1.3 protocol candidate. Your verdict gates
protocol approval, so return PASS only if there are no P0, P1, P2, or P3 findings. Do not edit files, access a
database, read environment variables or credentials, make a provider/network request, call Tushare, run a probe,
invoke another model, create an authorization, or approve real execution.

Primary exact-byte candidate:

- `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md`

Required comparison and review records:

- `docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration-v1.1.md`
- `docs/plans/2026-07-16-milestone-004-frame-source-capability-preregistration-v1.2.md`
- `reviews/milestone-004-audit-v1.2/capability-probe-2026-07-17.md`
- `reviews/milestone-004-preregistration-v1.3/agy-review.md`
- `reviews/milestone-004-preregistration-v1.3/agy-review-assessment.md`
- `reviews/milestone-004-preregistration-v1.3/p1-repair.md`
- `qualitative_v2_audit.py`, limited to frozen SW2021 mapping, super-strata, seed, frame validation, and sampling rules

Review requirements:

1. Confirm the AGY P1 is fully fixed without adding BSE to the frozen SSE/SZSE target or silently dropping raw rows.
   Check membership and daily `.BJ` handling, unknown suffix failure, canonical exclusion-ledger schema/order/hash,
   total versus eligible row counts, daily coverage, manifest binding, offline re-derivation, and tamper tests.
2. Confirm the AGY P2 rejection is correct: exactly 6,000 total raw daily items remains a fail-closed truncation signal
   under the frozen one-call 6,000-row provider ceiling.
3. Verify the exact deterministic 33-call matrix contains one classification call, all and only 31 frozen L1 calls in
   frozen order, and one authorization-bound daily call, with exact fields and no pagination/retry/parallel/fallback.
4. Verify v1.1 estimand, dictionary, super-strata, median split, sample size, seed/hash message, evidence, Reviewer,
   adjudication, and coverage rules are unchanged.
5. Check capability/capture isolation, permanent probe non-adoptability, fresh authorization and date evidence,
   raw-first ordering, token containment, fail-fast/incomplete semantics, TOCTOU/clock/lock/path controls, exact REST
   transport, response ceilings, closed file sets, process-control propagation, offline-only verification/assembly,
   terminal states, and the no-automatic-retry boundary.
6. Identify any ambiguity, contradiction, unverifiable completeness assumption, state transition, or test gap that
   could admit an invalid frame, reject an in-scope valid frame, conceal truncation, permit unauthorized adoption,
   mutate historical evidence, or imply network authority.
7. Confirm conditional final-candidate status can be approved without editing the reviewed protocol: a PASS permits
   only publication of its exact SHA-256 and an approval record, not implementation, authorization, or execution.

Severity rubric:

- P0: secret exposure, unauthorized external action, or historical evidence corruption.
- P1: correctness/reproducibility flaw that can admit or systematically prevent a valid target frame, change the
  frozen population/sample, conceal truncation, or allow unauthorized adoption/execution.
- P2: material ambiguity, incomplete fail-closed rule, or missing validation/test requirement that must be fixed before
  approval.
- P3: non-blocking but concrete clarity or maintainability defect that should still be fixed before exact-byte freeze.

Output exactly in this structure:

```text
VERDICT: PASS|FAIL
P0: NONE|<findings>
P1: NONE|<findings>
P2: NONE|<findings>
P3: NONE|<findings>
FREEZE: APPROVE_EXACT_BYTES|CHANGES_REQUIRED
```

For every finding, cite exact file and line numbers, explain the failure mode, and provide a protocol-level correction.
Do not include a work summary, praise, implementation plan, or text after `FREEZE`.
