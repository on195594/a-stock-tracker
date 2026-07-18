All offline gates pass, the freeze chain re-verifies (`frozen=true`), and the working tree is byte-identical to the freeze commit apart from this review prompt. No repository file was modified. Here is the archival review.

`★ Insight ─────────────────────────────────────`
- **Triangulated determinism**: the design proves correctness three independent ways — a forward *generator* oracle (build bytes, compare), a backward *validator* oracle (read bytes, check structure with a chunked hash), and the production modules themselves in 148 directed tests. Agreement across three non-shared implementations is far stronger evidence than any single test suite.
- **Fail-closed by construction**: every closed set is checked with `set(value) != FIELDS` (exact equality, not superset), and the ordinal partition is validated so no 36th call or `stock_st` can be reintroduced through data — the matrix is derived in code, and authorizations must byte-match it.
- **Raw-first + create-only + fsync**: `os.link(O_NOFOLLOW)` with `FileExistsError→SecurityError`, signals deferred during publish, and parent-only supervisor commit after re-verification make partial/late PASS states unpublishable.
`─────────────────────────────────────────────────`

---

# Claude Independent Implementation Review — MILESTONE-004 v1.3.2

REVIEWER_IDENTITY: Claude (Opus 4.8), fresh independent read-only implementation reviewer — not a protocol-review or capability-approver identity
REVIEWED_FREEZE_COMMIT: 2d5f5aefebc433bd51af3f82bb25cc67f451c75d
REVIEWED_IMPLEMENTATION_COMMIT: a133f2df23b51b0e4aa9c97da38d7f4cd53329cc
CANDIDATE_MANIFEST_SHA256: 7c79a2106cec0b2253ac56db443571a4f163fa04814f33332811f3e3d0bceecb
ISOLATION: PASS

## Scope and implementation trace

The implementation is a five-module offline generator/verifier plus two stdlib golden oracles and one directed test file:

- `qualitative_v2_audit_v132.py` — v1.1 audit primitives: `canonical_json_bytes` (UTF-8, sorted keys, `:`/`,`, one LF), `SW2021_INDUSTRIES` (31 pairs, `_validate_frozen_mapping` enforces 31 + 6-super-strata partition), `validate_frame`/`select_sample` (36-company sample), coverage lineage.
- `qualitative_v2_m4_segmented_core_v132.py` — matrix + authorization primitives. `frame_call_matrix` derives ordinals 1 (index_classify) + 2–32 (31 `index_member_all`) + 33/34 (`stock_basic` SSE/SZSE) + 35 (`daily_basic`); `date_evidence_call_matrix` = 2 `trade_cal`. Strict canonical authorization loading (`_number` rejects `-0`, `strict_json_loads` rejects dup keys/lossy numbers), v3 schemas, exact windows, checksum-line binding.
- `qualitative_v2_m4_segmented_verify_v132.py` — token-free offline reproduction: gate re-derivation, ledger, four-state ordinal model, manifest/receipt/stop closed sets, name-only ST exclusion, provider count check, filesystem confinement.
- `qualitative_v2_m4_segmented_runtime_v132.py` — fork-supervised private runtime: raw-first `_create_only`, fsync ordering, lock/journal/publication-tmp, monotonic parent deadline, parent-only supervisor commit, `_seal_tree`.
- `qualitative_v2_m4_segmented_rest_v132.py` — thin public API/CLI (`execute`/`verify`).

Import closure is exactly the five `_v132` files (leaf = `audit_v132`); no import of the bare unversioned modules (`qualitative_v2_m4_segmented_core.py`, etc.) or of any `_v131` module. Freeze commit `2d5f5ae` changed only docs, so HEAD's generators/tests equal implementation commit `a133f2d`.

Requirement-by-requirement result: (1) freeze DAG/exact-byte chain valid, `frozen=true`; (2) verifier + generators implement the frozen protocol and schema-contract closed sets with legacy `v3`-only rejection; (3) active matrix exactly 35 frame + 2 calendar, ordinals 1–35 contiguous, four API names, **no** `stock_st`, no ordinal 36, no data path to reintroduce either; (4) authorization/window/checksum/receipt-four-state/stop/raw-first/terminal-seal/supervisor PASS|FAIL/deadline/prospective-vs-final/capability-date-capture gates internally consistent and fail-closed; (5) path confinement, symlink/traversal/nlink rejection, create-only, fsync ordering, 0o444/0o555 permissions, stale-state and forked/concurrent handling all real; (6) closed-set ledger/date/manifest validation, provider `count == len(rows)` exactly (incl. zero), ST exclusion = `NFKC → strip → casefold → startswith("st"|"*st")` sourced only from `stock_basic`, with no claim to discover unmarked names; (7) 30 golden byte files + 2 genuinely-independent, materially-different oracles agree; (8) historical v1.3.1 inventory (9/9) and baseline diff unchanged; (9) tests exercise clauses through the production path; (10) all safely-runnable gates pass.

## Command evidence

| Gate | Result |
|------|--------|
| `sha256sum candidate-manifest.json` | `7c79a2106cec…eecb` (matches attestation) |
| `verify_m4_v132_freeze.py --attestation` | `PASS candidate=7c79a2…eecb review=2ec40e…7eea commit=a133f2d frozen=true` |
| v1.3.2 directed (`test_segmented_rest_v132.py`) | **148 passed** (sockets blocked) |
| v1.3.1 directed (`test_segmented_rest_v131.py`) | **133 passed** |
| all M4 (`tests/milestone004/`) | **448 passed** |
| full repo (`pytest -q`) | **926 passed** |
| oracle 1 spec (`verify_m4_v132_golden.py`) | `PASS m4-v132-spec-oracle vectors=30 protocol_sha256=555b1d4c…beea` |
| oracle 2 independent (`check_m4_v132_golden_independent.py`) | `PASS m4-v132-independent-crosscheck vectors=30 protocol_sha256=555b1d4c…beea` |
| Ruff lint (closure) | `All checks passed!` |
| Ruff `--select F401` | `All checks passed!` |
| `ruff format --check` | `9 files already formatted` |
| `mypy` (configured gate) | `Success: no issues found in 90 source files` |
| `git diff --check` (freeze + tree) | clean (exit 0) |
| `preregistration.sha256` inventory | 35/35 OK |
| `historical-v1.3.1-inventory.sha256` | 9/9 OK |
| baseline diff `0bb7916→HEAD` (v131 files) | empty (unchanged) |
| final `git status --porcelain` | only untracked review prompt; HEAD still `2d5f5ae` |

Key hashes: protocol `P` = `555b1d4c410da1dc68be3279a2070f55b5e73f7b6ef9ca7d850f89d0533dbeea`; predecessor lineage `f494c1973f00…8042`; review `2ec40ec3705180…d7eea`. Socket blocking was verified active (`OSError: network disabled`) before every dynamic gate; the repo's own `conftest` additionally blocks non-loopback sockets.

## Findings

### P0
NONE

### P1
NONE

### P2
NONE

### P3
NONE

## Verdict

VERDICT: PASS
IMPLEMENTATION_ACCEPTANCE: APPROVE

Every severity level is exactly `NONE` and all safely runnable required gates pass. The v1.3.2 implementation and verifier faithfully and completely realize the frozen protocol and canonical schema contract, fail closed on every checked invariant, and are exact-byte consistent with the frozen manifest.

## Unresolved operational risks / non-goals (not implementation defects)

These are declared open facts of the milestone, consistent with the code (the code makes no contrary claim):

- **No capability authorization** exists; no external capability approver acted here, and this review did not create one.
- **No real execution** occurred — no Tushare/provider call, no token use, no attempt directory, no assembler/frame/sample was produced. The reviewed artifacts are generators, verifier, golden fixtures, and tests only.
- **No external adoption approval** is conferred; this review only accepts the implementation, not any downstream capability/date/capture authorization or cutover.
- **Known unmarked-name risk**: ST exclusion detects only names literally carrying `ST`/`*ST` (after NFKC/trim/casefold). Risk-warning securities whose provider `name` is unmarked are inherently undiscoverable by this rule; the implementation correctly does **not** claim otherwise (`LEDGER-ST-SOURCE-019`).

## Attestation

No repository file was modified, created, or overwritten during this review (only read-only Git/filesystem commands, the checked-in offline oracles/tests, and the repo's already-installed quality gates were run; `git status` shows solely the untracked review prompt and HEAD is unchanged). No `.env`, token/key variable, `tracker.db`, production log, or credential store was read. No network, provider, pipeline, cron, Telegram, Gemini, reviewer, or browser call was made. No authorization or approval artifact was created. This review does not use `FREEZE: APPROVE_EXACT_BYTES`; the protocol is already frozen and this review only accepts the implementation.
