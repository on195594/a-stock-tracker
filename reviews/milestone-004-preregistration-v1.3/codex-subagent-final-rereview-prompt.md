# Independent Codex final rereview prompt — MILESTONE-004 v1.3 exact-byte candidate

Perform a new independent, read-only final review of the exact MILESTONE-004 v1.3 protocol candidate. This is a
strict freeze gate. Return PASS only when there are no P0, P1, P2, or P3 findings. Do not edit files, access a
database, read environment variables or credentials, make any network/provider request, call Tushare, run a probe,
invoke another model or subagent, create an authorization, or approve real execution.

First compute the candidate SHA-256 and stop with a P0 finding if it is not exactly:

`06f75727b53702cc34e638d12de92dfd282a2083597483bc09e6ce5c571d86ef`

Primary candidate:

- `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md`

Required comparison and review records:

- `docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration.md`
- `docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration-v1.1.md`
- `docs/plans/2026-07-16-milestone-004-frame-source-capability-preregistration-v1.2.md`
- `reviews/milestone-004-audit-v1.2/capability-probe-2026-07-17.md`
- `reviews/milestone-004-preregistration-v1.3/agy-review.md`
- `reviews/milestone-004-preregistration-v1.3/agy-review-assessment.md`
- `reviews/milestone-004-preregistration-v1.3/p1-repair.md`
- `reviews/milestone-004-preregistration-v1.3/codex-subagent-final-review.md`
- `reviews/milestone-004-preregistration-v1.3/codex-subagent-final-review-repair.md`
- `qualitative_v2_audit.py`, limited to frozen SW2021 mapping, super-strata, seed, frame validation, and sampling rules

Review requirements:

1. Reassess every prior Codex finding. For P1-1, verify the authoritative date inputs, exact evidence schema,
   deterministic latest-complete-common-day derivation, as-of instant, evidence-validity interval, capture-window
   relationship, raw semantic offline rederivation, and tests. For P2-1, independently reproduce both golden hashes
   and check the complete ledger schema, index bases, raw/normalized value rules, canonical bytes, sort, and manifest
   binding. For P2-2, trace pre-transport, no-response, token-echo, size, HTTP/provider/parse/schema, process-control,
   data-gate, and full-success paths through receipts, stop records, manifest partitions, completion, and tests.
2. Confirm the earlier AGY `.BJ` P1 remains fixed without adding BSE to the SSE/SZSE target or silently dropping any
   raw row. Check unknown suffixes, duplicate membership, total-versus-eligible ceilings, coverage, and exclusions.
3. Verify the exact deterministic frame matrix has one classification call, all and only 31 frozen L1 calls in
   frozen order, and one authorization-bound daily call, with exact fields and no pagination/retry/parallel/fallback.
   Verify the date-evidence matrix is auxiliary and cannot contribute frame bytes.
4. Verify the v1.1 estimand, dictionary, super-strata, median split, sample size, seed/hash message, evidence sources,
   Reviewer/adjudication, and coverage rules remain unchanged.
5. Check capability/capture/date-evidence isolation, permanent probe non-adoptability, fresh authorization, raw-first
   order, token containment, TOCTOU/clock/lock/path controls, REST transport, ceilings, closed file sets,
   process-control propagation, offline-only verification/assembly, and terminal decisions.
6. Identify any remaining ambiguity, contradiction, unverifiable completeness assumption, state transition, or test
   gap that could admit an invalid frame, reject an in-scope valid frame, change the frozen population/sample, conceal
   truncation, permit unauthorized adoption/execution, or prevent independent byte-for-byte reproduction.
7. Confirm conditional final-candidate status can be approved without editing the reviewed protocol. A PASS permits
   only publication of the exact reviewed SHA-256 and a protocol approval record; it does not approve implementation,
   authorization, credentials, or execution.

Severity rubric:

- P0: secret exposure, unauthorized external action, historical evidence corruption, or identity mismatch.
- P1: correctness/reproducibility flaw that can admit or systematically prevent a valid target frame, change the
  frozen population/sample, conceal truncation, or allow unauthorized adoption/execution.
- P2: material ambiguity, incomplete fail-closed rule, or missing validation/test requirement that must be fixed.
- P3: non-blocking but concrete clarity or maintainability defect that must still be fixed before exact-byte freeze.

Output exactly this structure, with no text before or after it:

```text
VERDICT: PASS|FAIL
P0: NONE|<findings>
P1: NONE|<findings>
P2: NONE|<findings>
P3: NONE|<findings>
FREEZE: APPROVE_EXACT_BYTES|CHANGES_REQUIRED
```

For every finding, cite exact file and line numbers, explain the failure mode, and give a protocol-level correction.
