# MILESTONE-004 v1.3.2 capability approval supplied-input record

Document ID: `m4-v1.3.2-capability-input-2026-07-18-01`

Recorded by: `Codex`

Record date: `2026-07-18` (`Asia/Shanghai`)

Status: **DRAFT INPUT — REQUIRES EXTERNAL REAFFIRMATION — NOT AN AUTHORIZATION**

Related request:
[`capability-authorization-approval-request.md`](capability-authorization-approval-request.md)

## Source separation

This record separates previously supplied account facts from new proposed attempt values. It does not silently treat
historical assertions as current entitlement evidence.

### Previously supplied by `lin`

The withdrawn v1.3.1 input record states:

- accountable authority and Tushare account owner: `lin`;
- 2000 points purchased on `2026-07-17`, stated expiry `2027-07-17`;
- fee/points use and service terms accepted;
- publisher: Codex only after final approval, with no token access;
- operator: Codex;
- `TUSHARE_TOKEN` can be supplied only through the process environment and is exactly 64 lowercase hexadecimal
  characters; `.env`, chat, Git, commands, screenshots, logs, and approval artifacts will not contain it.

Source:
[`../milestone-004-preregistration-v1.3.1/capability-authorization-input-2026-07-17-01.md`](../milestone-004-preregistration-v1.3.1/capability-authorization-input-2026-07-17-01.md).

The same historical record captured public documentation supporting the 2000-point threshold for
`index_classify`, `index_member_all`, `stock_basic`, and `daily_basic`, plus general quota figures. Its blocking
`stock_st` finding applied only to the withdrawn 36-call v1.3.1 matrix. v1.3.2 explicitly prohibits `stock_st`.

### Proposed for this v1.3.2 decision

| Field | Proposed value | Status |
|---|---|---|
| `authorization_id` | `qualitative-v2-m4-v132-capability-20260720-01` | fresh; external affirmation required |
| `attempt_id` | `m4-v132-capability-20260720-01` | fresh; external affirmation required |
| `probe_trade_date` | `2026-07-17` | completed-date suitability must be affirmed |
| `not_before` | `2026-07-20T18:00:00+08:00` | future when drafted; must remain future when signed |
| `not_after` | `2026-07-20T20:00:00+08:00` | immutable hard deadline if approved |
| External approver | `PENDING_FRESH_EXTERNAL_APPROVER` | blocking |
| Publisher | `Codex` | proposed; effective only after APPROVE |
| Operator | `Codex` | proposed; effective only after APPROVE |

At preparation time, the proposed authorization JSON, checksum, attempt directory, sibling lock, phase journal, and
publication-temp paths were absent. This was a filesystem-only check. No directory or control file was created.

## Evidence still required for a valid external decision

The external approver must not infer these values from points alone:

| Evidence | Required content |
|---|---|
| Account authority | `lin` reaffirms ownership/authority at decision time |
| Account tier | current 2000-point status and expiry, without exposing credentials |
| Per-family entitlement | account-specific access for `index_classify`, `index_member_all`, `stock_basic`, and `daily_basic` |
| Remaining quota | sufficient current per-minute and per-day quota for 1 + 31 + 2 + 1 serial calls |
| Cost/points | explicit acceptance of any fee/points consumption for the one attempt |
| Terms | current provider terms permit this read-only evidence collection and local sealed retention |
| Probe date | `2026-07-17` is acceptable completed data date for all authorized frame calls |
| Risk acceptance | explicit acceptance of historical `40203`, no retry, immutable evidence, and unmarked-name ST risk |

Evidence references must not contain the token, cookies, session secrets, account passwords, or private credential
material. A provider call is not authorized as an entitlement preflight.

## Preparation safety record

- No authorization JSON or authorization checksum was created.
- No attempt, lock, phase journal, or publication-temp artifact was created.
- No `.env`, token, credential store, `tracker.db`, or production log was read.
- No network, Tushare/provider, pipeline/cron, Telegram, Gemini, Reviewer, or model call was made.
- Frozen `P/K/G/C/M`, implementation files, review, and attestation were not modified.
