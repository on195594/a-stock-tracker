# Read-only protocol review prompt — M4 v1.3.2

Review only the candidate identified by
`reviews/milestone-004-preregistration-v1.3.2/candidate-manifest.json`. Treat that manifest as the sole entry point;
follow and verify every bound path and SHA-256. Do not edit files, generate fixtures, amend commits, create an
authorization, read credentials, use network access, or execute provider/runtime/assembler paths.

Required checks:

1. Verify the candidate manifest canonical bytes, all file hashes, preregistration sidecar, historical inventory,
   repair-plan review, and exact-path diff against baseline `0bb791653927118a8ef661b7275f2854cec5623e`.
2. Confirm the dependency DAG is acyclic: P and K do not bind downstream hashes; G may bind H(P); C binds P/K/G;
   M binds P/K/G/C and governance inputs but neither itself nor a Git commit; review R binds H(M) and the reviewed
   commit; attestation S is absent during review and later binds H(M), H(R), and that same commit.
3. Independently inspect the complete protocol and canonical schema contract: 35/2 matrices, v3 closed schemas,
   authorization refs/windows, receipt states, stop codes, deadline/cancellation precedence, raw-first publication,
   fsync/permission/link/path invariants, candidate/post-publication commit, ledger/date/manifest closed sets,
   offline verification, adoptability, and legacy rejection.
4. Confirm no live v1.3.2 call/dispatch/parser/gate/ledger-source/manifest/fixture contains ordinal 36 or `stock_st`;
   the only permitted textual references explain its prohibition or historical inventory. Confirm the name-only
   NFKC/outer-trim/casefold `ST|*ST` limitation and exact provider `count`, including zero.
5. Run both standard-library golden checkers, directed v1.3.2 tests, historical v1.3.1 tests, M4 tests, repository
   tests, Ruff lint/F401/format, mypy, checksum/inventory/import-closure checks, and Git whitespace/status checks in
   an environment with `TUSHARE_TOKEN`, proxy variables, and provider access removed and socket creation denied.
6. Verify the five-file v1.3.2 closure imports no v1.3.1 segmented module or unversioned audit module and that the
   v1.3.1 inventory matches both the sidecar and baseline exact-path diff.

Return only this strict record shape, with concrete command evidence and no edits:

```text
REVIEWER_IDENTITY: <fresh read-only identity>
REVIEWED_COMMIT: <40 lowercase hex>
CANDIDATE_MANIFEST_SHA256: <64 lowercase hex>
ISOLATION: PASS|FAIL
COMMAND_EVIDENCE: <semicolon-separated commands and results>
P0: NONE|<finding>
P1: NONE|<finding>
P2: NONE|<finding>
P3: NONE|<finding>
VERDICT: PASS|FAIL
FREEZE: APPROVE_EXACT_BYTES|CHANGES_REQUIRED
```

Strict PASS requires `ISOLATION: PASS`, every P-level `NONE`, `VERDICT: PASS`, and
`FREEZE: APPROVE_EXACT_BYTES`.
