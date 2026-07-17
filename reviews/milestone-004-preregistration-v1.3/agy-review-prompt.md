# AGY independent review prompt — MILESTONE-004 v1.3 draft

Independently review the proposed MILESTONE-004 segmented REST frame-source protocol. This is a documentation-only,
read-only review. Do not edit files, access any database, read environment variables or credentials, make any network
request, call Tushare, run a provider probe, invoke another model, or authorize execution.

Primary draft:

- `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md`

Normative predecessors and frozen definitions to compare:

- `docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration-v1.1.md`
- `docs/plans/2026-07-16-milestone-004-frame-source-capability-preregistration-v1.2.md`
- `reviews/milestone-004-audit-v1.2/capability-probe-2026-07-17.md`
- `qualitative_v2_audit.py` only for the frozen `SW2021_INDUSTRIES`, `SUPER_STRATA`, sampling seed, frame validation,
  and sampling definitions; do not inspect unrelated production data.

Review objectives:

1. Verify that v1.3 changes only the frame-source acquisition strategy and does not silently change the v1.1
   estimand, exact 31-industry mapping, six super-strata, low/high split, sample size, seed, hash message, or later
   evidence/Reviewer rules.
2. Verify the exact deterministic 33-call matrix: one `index_classify`, exactly one `index_member_all` for each frozen
   L1 code, and one authorization-bound `daily_basic`, with no missing, duplicate, or crossed code/name entry.
3. Evaluate whether the below-limit exhaustive 31-partition rule is sufficient to detect truncation and whether the
   classification, membership, daily, intersection, and 12-cell gates fail closed without introducing coverage bias.
4. Check capability/capture separation, permanent capability non-adoptability, fresh authorization/attempt/output
   requirements, capture-date evidence, and offline-only assembly.
5. Check authorization TOCTOU controls, exact host/method/body restrictions, token containment and echo handling,
   raw-first publication, finite errors, fail-fast behavior, process-control propagation, byte limits, create-only
   locks, closed file sets, and offline deterministic verification.
6. Identify internal contradictions, underspecified states, provider-contract assumptions that are not safely frozen,
   test gaps, or terminal decisions that could permit an unauthorized retry, fallback, adoption, or project-state
   advance.
7. Confirm that historical v1/v1.1/v1.2 files and hashes remain immutable and that draft status grants no execution
   authority.

Severity rubric:

- P0: secret exposure, unauthorized external action, historical evidence corruption, or an immediately unsafe design.
- P1: correctness/reproducibility flaw that can admit an invalid frame, omit part of the target population, violate
  the frozen estimand/sample, or allow unauthorized adoption/execution.
- P2: material ambiguity or missing fail-closed/test requirement that should be resolved before freeze.
- P3: non-blocking clarity, maintainability, or documentation improvement.

Output format:

1. Start with exactly `VERDICT: PASS`, `VERDICT: PASS_WITH_NOTES`, or `VERDICT: FAIL`.
2. Then list findings in descending severity using identifiers `P0-1`, `P1-1`, `P2-1`, etc. For each finding cite
   exact file and line numbers, explain the failure mode, and give a concrete protocol-level correction.
3. If a severity has no findings, write `P0: NONE`, `P1: NONE`, `P2: NONE`, or `P3: NONE`.
4. End with a short `Freeze recommendation` stating whether the draft can be frozen unchanged.

Do not praise style or summarize the document except where needed to explain a finding. Do not propose implementation
work outside the scope of this protocol review.
