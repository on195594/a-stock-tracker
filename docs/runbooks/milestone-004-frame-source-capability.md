# MILESTONE-004 v1.2 frame-source capability runbook

Status: live probe completed 2026-07-17; capability failed; terminal state `NO_QUALIFIED_FRAME_SOURCE`.

Archive note: this runbook describes frozen, retired M4 tooling under `scripts/archive_m4/`; it is not an actively maintained operational entrypoint.

## Boundary

This command probes whether Tushare can support a later sampling-frame capture. It does not capture a formal frame.
Every output is marked `non_adoptable` and is rejected by both the v1.2 guard and the retained v1.1
assemble/resume boundary.

The probe issues exactly three body-only HTTPS POSTs to `api.tushare.pro`, at most once each and without retry,
pagination, or fallback:

1. `index_classify(level="L1", src="SW2021")`
2. `index_member_all(is_new="Y")`, with no `l1_code`
3. `daily_basic(trade_date="20260715")`, fields `ts_code,trade_date,total_mv`

The date comes from the canonical authorization, not a code default. It tests access/schema/capacity only and is not
a freshness claim.

## Authorized execution

The execution tool cannot create or renew an authorization. For the fixed authorization supplied outside the tool,
run only inside its inclusive window:

```bash
.venv/bin/python scripts/archive_m4/probe_m4_frame_source.py probe \
  --authorization reviews/milestone-004-preregistration-v1.2/capability-authorization-2026-07-16-01.json \
  --authorization-sha256 reviews/milestone-004-preregistration-v1.2/capability-authorization-2026-07-16-01.sha256 \
  --attempt-id m4-v1.2-capability-20260716-01
```

Before `2026-07-17T00:00:00+08:00`, the command exits with
`M4_CAPABILITY_NOT_EXECUTED: authorization_not_yet_valid`; after `2026-07-18T00:00:00+08:00`, it exits with
`authorization_expired`. Neither case constructs a network backend, reads a token, creates the output root, or
consumes the attempt ID.

Do not run the command a second time. A sealed attempt directory is create-only and an existing attempt is rejected.
Do not run pipeline, cron, Telegram, Gemini, Reviewer, or any production database command as part of this operation.

## Offline verification

Verification requires no token and creates no transport:

```bash
.venv/bin/python scripts/archive_m4/probe_m4_frame_source.py verify \
  --attempt-dir artifacts/milestone-004/v1.2/capability-probes/probe-m4-v1.2-capability-20260716-01
```

It rechecks canonical authorization bytes embedded in the manifest, v1.2 protocol and generator hashes, the exact
call matrix, receipt/blob paths and hashes, the closed file set, `non_adoptable`, and all four capability gates from
the raw blobs.

`complete=true` means all three diagnostic calls reached a sanitized terminal receipt.
`capability_pass=true` additionally means every classification, membership, daily, and 12-cell gate passed.

- PASS permits only a proposal for a new complete-capture authorization with a new attempt. Probe bytes remain
  forbidden as capture input.
- FAIL terminates this candidate at `NO_QUALIFIED_FRAME_SOURCE`. Do not fall back to SWS, prior responses, production
  data, or another source. Continuing requires a v1.3 candidate-source protocol.

## Final execution

The single authorized attempt completed all three calls on 2026-07-17. Both industry APIs returned provider code
`40203` for missing account access, and `daily_basic` returned `40203` for its five-requests-per-day limit. Offline
verification reproduced manifest SHA-256
`70f6d73a4b62f9725b3fe983cbdcf212ded5d84cbdedbe777088793b0c33957a` with `complete=true` and
`capability_pass=false`.

The command must not be run again. There is no qualified v1.2 frame source and no authorized next command in this
runbook.
