# Third independent Codex final rereview — MILESTONE-004 v1.3 exact bytes

Perform a fresh, independent, read-only final review. This is a strict exact-byte freeze gate. Do not edit files,
read credentials/environment variables, access databases, use network/providers, invoke Tushare/probes, invoke
models/subagents, create authorization, or approve implementation/execution.

First verify the candidate hash is exactly:

`5bde2a84c2642f6c659aa5d52b8efa04b9e0569097d50284b82c40d6fb675961`

Candidate:

- `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md`

Read all historical protocols and every review/repair record in
`reviews/milestone-004-preregistration-v1.3/`, especially
`codex-subagent-second-rereview.md` and `codex-subagent-third-rereview-repair.md`. Compare the frozen taxonomy,
super-strata, seed, frame and sampling rules with `qualitative_v2_audit.py` without inspecting unrelated runtime
data.

Required checks:

1. Reassess every finding from all prior AGY/Codex reviews; a finding is closed only if its full failure mode and
   synthetic tests are resolved without changing the estimand.
2. Trace the exact 36-call frame matrix: classification, all-and-only 31 frozen L1 calls in frozen order, SSE/SZSE
   stock-basic partitions, date-bound historical ST status, and daily market value. Verify all fields, ceilings,
   joins, ledger paths, no pagination/retry/parallel/fallback, and exact authorization binding.
3. Verify every inherited v1 eligibility rule, including listed status, RMB ordinary-A board/code tuples, explicit
   `689`/CDR rejection, BSE/B-share/fund/bond/preferred exclusion, date-bound ST union, delisting-consolidation name
   predicate, 36-month age, active membership, date-aligned market value, and deterministic first-reason handling.
4. Trace execution immediately before and across `not_after`/evidence expiry through DNS, TLS, body read, scan, blob
   fsync, receipt fsync, gates, and manifest publication. Ensure no byte or manifest that crosses the deadline can
   PASS or become adoptable; verify failure sealing, timestamps, status codes, manifest gate, and boundary tests.
5. Independently reproduce all embedded vector hashes and the compact 36-call capability-authorization vector.
   Verify every no-extension authorization/receipt/stop/ledger/date-evidence/manifest schema, canonical bytes,
   closed-file set, state partition, timing/nullability/cross-field rule, and tamper test.
6. Verify `.BJ` and every excluded raw source row remain accounted for without entering the SSE/SZSE frame; unknown
   suffixes, duplicate membership, ceilings, and missing joins fail closed.
7. Verify date evidence semantic rederivation/freshness, capability/capture/date isolation, permanent probe non-
   adoptability, token containment, raw-first order, process control, TOCTOU/path/lock controls, offline-only
   verification/assembly, and no implied network authority.
8. Confirm historical files/hashes and every other v1.1 estimand, evidence, Reviewer, adjudication, coverage, and
   sample rule remain unchanged. Identify any P0–P3 defect; P3 is also freeze-blocking.

Severity: P0 secret/unauthorized action/history/identity; P1 frame/sample/adoption correctness; P2 material
ambiguity/schema/state/test gap; P3 concrete clarity/maintainability issue.

Return exactly five lines with no other text:

```text
VERDICT: PASS|FAIL
P0: NONE|<findings>
P1: NONE|<findings>
P2: NONE|<findings>
P3: NONE|<findings>
FREEZE: APPROVE_EXACT_BYTES|CHANGES_REQUIRED
```

Every finding must cite exact file/lines, failure mode, and correction.
