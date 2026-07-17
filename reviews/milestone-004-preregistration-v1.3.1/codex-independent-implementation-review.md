# MILESTONE-004 v1.3.1 independent implementation review

Date: 2026-07-17

Verdict: **PASS**

- P0: NONE
- P1: NONE
- P2: NONE
- P3: NONE

The independent read-only review confirmed the production-path coverage for cumulative attempt size,
invalid credentials, attempt-artifact symlinks, path traversal, mixed-attempt identity, and two forked
executors contending for one create-only attempt. It also confirmed the supervisor deadline/cancellation
phase matrix, late-PASS non-commit invariant, exact REST boundary, authorization schemas, data gates,
calendar derivation, tamper detection, and legacy rejection guard.

Observed gates:

- v1.3.1 directed tests: 132 passed
- `tests/milestone004`: 299 passed
- full repository: 777 passed
- Ruff lint, F401, format, mypy, and `git diff --check`: PASS
- frozen v1.3 SHA-256: `0ad8c6b3d5175af96409e570bdc17839ac7cf266227410f2e9e9ea756a7a12df`
- v1.3.1 protocol SHA-256: `f494c1973f002536874782af41221d37a2ca618c329fa48a2ae650b544c88042`
- golden vectors SHA-256: `7135598a0095d9a1496b39f3f82d83fa6e629846046d87bae8b9780cc619ee2c`
- reviewed runtime SHA-256: `2def99e1e5d51d5a214c0b31c04ba991d714446f627a42e8e02a5278bd54259e`

The review modified no files and used no real token, network request, or production database.

## Follow-up review remediation

A later review found one P2 response-contract bug and four P3 dead-code findings. The implementation now rejects a
supplied provider count unless it exactly matches the number of rows, including rejecting `count: 0` with nonempty
rows. The unused regex constants, verifier field sets, exception binding, and recovery parameters were removed.

Post-remediation gates passed: 133 directed v1.3.1 tests, all 300 `tests/milestone004` tests, all 778 repository tests,
Ruff lint/F401/format, mypy, protocol/golden checksums, and `git diff --check`. No real token, network request, or
production database was used.
