# MILESTONE-004 v1.3.1 segmented REST runbook

Status: implementation and offline verification available; **no v1.3.1 authorization exists and no real attempt is
authorized**.

## Boundary

v1.3.1 supports three separately authorized attempt kinds:

- `capability`: the exact 36-call segmented frame matrix; permanently non-adoptable;
- `date_selection_evidence`: exactly two SSE/SZSE calendar calls; permanently non-adoptable;
- `capture`: the same 36-call matrix, permitted only with a verified capability PASS and still-valid date evidence.

The implementation does not create authorizations or assemble a frame/sample. It never reads `.env`. Execution reads
only a pre-existing canonical authorization/checksum and, after acquiring its create-only lock, the exact
64-lowercase-hex `TUSHARE_TOKEN` environment value. Do not pass a token, mode, date, output path, timeout, or backend
on the CLI.

## Execute one externally authorized attempt

There is currently no valid command to run because no authorization has been published. If one is later approved,
the exact interface is:

```bash
.venv/bin/python qualitative_v2_m4_segmented_rest.py execute \
  --authorization PATH \
  --authorization-sha256 PATH
```

The authorization fixes kind, attempt ID, dates, root, limits, references, window, and complete call matrix. The
supervisor enforces the hard deadline and seals a failure attempt on ordinary credential/provider/transport/data-gate
failure. A candidate manifest pre-binds `supervisor-commit.json`; only the parent publishes this positive commit
after manifest fsync, post-publication candidate verification, and its deadline observation. Offline verification
and capture eligibility require it, so deleting stale controls cannot revive a late PASS. Exit `0` is verified PASS,
`1` is a sealed verified FAIL, and `2` is not executed or integrity-blocked.

Never retry, resume, break a stale lock, clean an orphan attempt, substitute an earlier artifact, or use an earlier
protocol root. A stale lock/orphan is deliberate fail-closed evidence requiring a new linked protocol decision.

## Offline verification

Verification is token-free and network-free:

```bash
env -u TUSHARE_TOKEN \
  .venv/bin/python qualitative_v2_m4_segmented_rest.py verify \
  --attempt-dir PATH
```

It reopens the authorization and checksum, frozen protocol/checksum, exact five generator files, every raw blob and
receipt, supporting ledger/evidence, manifest, permissions, links, and closed file set. It reconstructs all data gates
and date derivation from raw bytes. Deleting or changing project provenance intentionally makes historical attempts
fail verification.

Capability PASS permits only proposing a date-evidence authorization. Date-evidence PASS permits only proposing a
same-date capture authorization inside its validity inequalities. Neither makes frame bytes adoptable. A future
assembler must call `require_capture_input_eligible(attempt_dir)`; all retained legacy assemblers reject v1.3.1.

## Offline acceptance evidence

The 2026-07-17 synthetic, socket-free acceptance run passed 133 directed v1.3.1 tests, all 300
`tests/milestone004` tests, and all 778 repository tests. Ruff lint, F401, format, mypy, protocol/golden SHA-256, and
`git diff --check` also passed. These results are engineering evidence only: they are not an authorization, a real
Tushare attempt, or an adoptable frame.

## Operational prohibitions

- Do not create or backdate an authorization with this tool.
- Do not read `tracker.db`, run pipeline/cron, Telegram, Gemini, AGY, Reviewer, SDK, MCP, or a fallback provider.
- Do not copy authorization/source files into an attempt.
- Do not use capability or date-evidence bytes as frame rows.
- Do not claim `FRAME_AND_SAMPLE_FROZEN` from a capture PASS; assembly remains a separate future implementation and
  authorization boundary.
