# Fourth independent Codex final rereview — MILESTONE-004 v1.3 exact bytes

Conduct a fresh independent read-only strict freeze review. Do not edit files; do not read credentials/environment,
use databases/network/providers/Tushare/probes, invoke models/subagents, create authorization, or approve execution.

Candidate and required SHA-256:

- `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md`
- `0ad8c6b3d5175af96409e570bdc17839ac7cf266227410f2e9e9ea756a7a12df`

Read all historical protocols and every record under `reviews/milestone-004-preregistration-v1.3/`, especially the
third rereview and fourth repair. Compare only relevant frozen constants/rules with `qualitative_v2_audit.py`.

Reassess every prior finding and the whole protocol. In particular:

1. Verify current undated membership/stock snapshots cannot cross a civil date: capture authorization and every
   provider/publication phase must remain within sampling-date 18:00:00–23:59:59, while next-day calendar-evidence
   validity alone cannot authorize capture. Trace membership/list-status/name transitions and all required tests.
2. Verify the hard execution deadline at every transport/blob/receipt/gate/manifest boundary and that no overrun can
   PASS or become adoptable.
3. Verify the exact 36-call matrix, all 31 industries/order, stock-basic partitions, date-bound stock-ST and daily,
   fields, limits, joins, eligibility predicates, `.BJ` and all exclusion-ledger accounting.
4. Verify CDR/`689`, delisting consolidation, ST, security type, listing status/age, membership date, market value,
   frame and deterministic sample rules reproduce the inherited estimand.
5. Reproduce every embedded golden hash and compact authorization vector; inspect all exact schemas, canonical bytes,
   state/null/timing/path/cross-field rules, closed file set, tamper tests, authorization isolation, raw-first/token/
   process controls, and offline-only adoption.
6. Verify all historical hashes/artifacts and unrelated v1.1 evidence/Reviewer/adjudication/coverage rules remain
   unchanged. Identify any P0–P3 issue; P3 blocks freeze.

Return exactly five lines, with findings citing file/lines, failure mode, and correction:

```text
VERDICT: PASS|FAIL
P0: NONE|<findings>
P1: NONE|<findings>
P2: NONE|<findings>
P3: NONE|<findings>
FREEZE: APPROVE_EXACT_BYTES|CHANGES_REQUIRED
```
