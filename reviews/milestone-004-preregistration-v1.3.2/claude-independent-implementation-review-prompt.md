# Claude independent implementation review — MILESTONE-004 v1.3.2

You are the fresh, independent, read-only implementation reviewer for MILESTONE-004 v1.3.2 in the
`a-stock-tracker` repository. This is a new role after the exact-byte protocol review and freeze. Do not reuse or
claim either prior Codex protocol-review identity. Do not act as the capability external approver.

## Immutable review target

- Freeze commit: `2d5f5aefebc433bd51af3f82bb25cc67f451c75d`
- Reviewed implementation commit: `a133f2df23b51b0e4aa9c97da38d7f4cd53329cc`
- Candidate-manifest entry point:
  `reviews/milestone-004-preregistration-v1.3.2/candidate-manifest.json`
- Frozen protocol:
  `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.2.md`
- Freeze-chain verifier: `scripts/verify_m4_v132_freeze.py --attestation`

The working tree may contain only this prompt as a new downstream review input. Do not treat that prompt addition as
candidate drift. All `P/K/G/C/M` bytes and every candidate-manifest artifact must remain identical to the frozen
manifest. Review the implementation as committed; do not edit, format, generate, or overwrite any repository file.

## Safety and isolation

This is an offline, read-only review. Do not read `.env`, process-environment secrets, token/key variables,
`tracker.db`, production logs, or user credential/config stores. Do not use network access, Tushare or any provider,
pipeline/cron entry points, Telegram, Gemini, Reviewer services, browser/search tools, or real production data. Do not
install dependencies. Do not create authorization artifacts. Tests must use only checked-in fixtures and must prevent
socket access. You may run read-only Git/filesystem commands and the repository's already-installed offline Python
quality gates. Never run a command that writes to tracked files; in particular, do not run Ruff format without
`--check`.

The user has explicitly approved execution of all required offline, read-only gates in this prompt. The CLI is
launched with Bash permission so those gates are not blocked by an interactive permission harness. Do not ask for
approval, do not stop at an execution plan, and do not substitute static inspection for safely runnable dynamic
gates. The broader Bash permission exists only to run the enumerated read-only checks; it does not authorize writes,
network calls, dependency changes, or any action outside this review.

## Required review

Start from the candidate manifest rather than trusting summaries. Independently verify:

1. The freeze DAG and exact-byte chain are valid, `P/K/G/C/M` and all manifest artifacts are unchanged, and the
   implementation source closure is exactly the five versioned v1.3.2 files. Confirm it does not import v1.3.1
   segmented modules or an unversioned audit module.
2. The v1.3.2 implementation and verifier completely implement the frozen protocol and canonical schema contract,
   including exact closed sets and legacy rejection. Trace production code, not only tests.
3. The active matrix is exactly 35 frame calls plus 2 calendar calls; there is no ordinal 36, `stock_st`, hidden
   dispatch/parser/gate/ledger source, or manifest field that can reintroduce it.
4. Authorization identity, windows, checksum line, request descriptions, receipt four-state model, stop codes,
   raw-first publication, terminal sealing, supervisor `PASS|FAIL`, deadline/cancellation precedence, prospective
   versus final commit fields, and capability/date/capture phase gates are internally consistent and fail closed.
5. Filesystem safety is real: path confinement, traversal/symlink/link handling, create-only semantics, fsync ordering,
   permissions, mixed-attempt rejection, and concurrent/forked executor handling cannot publish or commit partial or
   late state.
6. Ledger/date/manifest validation uses the frozen closed sets and canonical byte rules. Provider `count` must equal
   rows exactly even when zero. `stock_basic.name` exclusion must be only NFKC + outer trim + casefold followed by
   `ST|*ST` prefix detection, with no claim that unmarked risk-warning names can be discovered.
7. The 30 materialized golden fixture byte files, index byte counts/hashes, schema contract, and both pure-stdlib
   oracles agree. Confirm the two oracles are genuinely independent of runtime/project canonicalizers and materially
   different from each other, not merely two wrappers around the same logic. Look for missing negative cases,
   tautological tests, over-mocking, or fixtures that fail to exercise production branches.
8. Historical v1.3.1 inventory/sidecars and the exact-path diff from
   `0bb791653927118a8ef661b7275f2854cec5623e` protect the declared historical closure.
9. Tests cover authorization, recovery, tamper/failure states, concurrency, deadline races, schema closed sets,
   canonical numeric/Unicode/null/sorting behavior, name-only ST behavior, exact count behavior, and commit
   eligibility. Identify any protocol clause that is not exercised through the real implementation path.
10. Run the relevant offline gates in a stripped environment with socket access disabled: v1.3.2 directed tests,
    historical v1.3.1 directed tests, all M4 tests, the full repository tests, both golden oracles, freeze-chain and
    checksum/inventory checks, Ruff lint/F401/format check, the existing mypy gate, import/generator closure scans,
    `git diff --check`, and final status. If a command cannot safely be run, report it rather than weakening safety.

Review for semantic defects and underspecification, not only test failures. Assign each finding a severity:

- P0: security/data-integrity catastrophe or unauthorized real-world action.
- P1: implementation can violate a mandatory protocol invariant or freeze/adoption claim.
- P2: meaningful correctness, coverage, or evidence weakness that should block acceptance.
- P3: smaller but actionable defect in the reviewed scope.

Any finding at P0, P1, P2, or P3 requires `VERDICT: FAIL`. Do not repair findings. A PASS is allowed only when every
level is exactly `NONE` and all safely runnable required gates pass. Keep open operational facts—no capability
authorization, no external approval, no real execution, no assembler/frame/sample, and the known unmarked-name
risk—separate from implementation findings unless the code falsely claims otherwise.

## Required output

Return one self-contained Markdown review suitable for exact archival. Include:

- `REVIEWER_IDENTITY:` beginning with `Claude` and naming the model if known
- `REVIEWED_FREEZE_COMMIT: 2d5f5aefebc433bd51af3f82bb25cc67f451c75d`
- `REVIEWED_IMPLEMENTATION_COMMIT: a133f2df23b51b0e4aa9c97da38d7f4cd53329cc`
- `CANDIDATE_MANIFEST_SHA256:` independently computed
- `ISOLATION: PASS|FAIL`
- concise scope and implementation trace
- command evidence with exact pass/fail counts and relevant hashes
- findings grouped under `P0`, `P1`, `P2`, and `P3`; write exactly `NONE` for an empty level
- `VERDICT: PASS|FAIL`
- `IMPLEMENTATION_ACCEPTANCE: APPROVE|CHANGES_REQUIRED`
- unresolved operational risks/non-goals
- an explicit statement that no repository file was modified, no secret/token was read, no network/provider call was
  made, and no authorization was created

Do not use `FREEZE: APPROVE_EXACT_BYTES`; the protocol is already frozen and this review only accepts or rejects the
implementation.
