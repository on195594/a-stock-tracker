# MILESTONE-004 capture-first seal review

Date: 2026-07-16
Verdict: **PASS**
Remaining P1/P2/P3: **NONE**

## Scope

This local review covered the capture-first authorization boundary, all retained legacy exporter entry points,
per-call authorization expiry checks, raw-first receipt timestamps, process-control exception propagation,
complete/incomplete attempt isolation, and the related synthetic regression tests and runbook updates.

No network source, production database, production pipeline, or Reviewer/model process was used.

## Findings closed

- The four retained legacy exporter API/CLI/direct-source paths fail before token loading, runner/session construction,
  output creation, or transport.
- Authorization is checked against a live timezone-aware clock before each provider call and at raw-first publication;
  receipts record the actual response-capture time.
- `KeyboardInterrupt`, `SystemExit`, and `GeneratorExit` stop further calls, preserve any prepublished response in the
  incomplete manifest, seal the attempt, and propagate unchanged.
- The unused `CaptureManifest` boundary type and unused imports were removed; explicit F401 checking passes.
- A seal-review regression was fixed: a response prepublished immediately before a process-control exception is now
  represented by both its immutable receipt/blob and its incomplete-manifest call record, so the sealed attempt remains
  internally verifiable.

## Verification

- M4 synthetic suite: `115 passed`.
- Full suite: `593 passed`.
- Ruff lint, Ruff format check, explicit F401 check, and mypy: PASS.
- M4 protocol v1/v1.1 SHA-256, static SZSE `SHA256SUMS`, CLI help smoke, and `git diff --check`: PASS.

The next real capture remains blocked on a new external canonical authorization JSON and matching SHA-256 manifest.
Reviewer execution remains separately unauthorized.
