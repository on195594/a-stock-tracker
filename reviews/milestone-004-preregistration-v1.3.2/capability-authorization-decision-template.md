# MILESTONE-004 v1.3.2 capability authorization external decision

Document ID: `m4-v1.3.2-capability-decision-2026-07-18-01`

Related request:
[`capability-authorization-approval-request.md`](capability-authorization-approval-request.md)

Decision status: **UNSIGNED TEMPLATE — NOT AN AUTHORIZATION**

This template must be copied to a new immutable decision record and completed by a fresh external approver. Do not
edit this template in place. Until exactly one decision is selected and every blocking field is completed, it grants
no publication, token, provider, or execution authority.

The approver must not be the author/fixer, either v1.3.2 protocol reviewer, or the Claude Opus implementation-review
identity. The approver acts only as capability external approver and does not retroactively change protocol freeze or
implementation acceptance.

## Exact proposed values

| Field | Value to affirm or reject |
|---|---|
| Accountable authority/account owner | `lin` |
| External approver identity and role | `PENDING_EXTERNAL_DECISION` |
| Authorized publisher | `Codex` — post-APPROVE JSON/checksum publication only; no token access |
| Authorized operator | `Codex` — one invocation only |
| `authorization_id` | `qualitative-v2-m4-v132-capability-20260720-01` |
| `attempt_id` | `m4-v132-capability-20260720-01` |
| `probe_trade_date` | `2026-07-17` |
| `not_before` | `2026-07-20T18:00:00+08:00` |
| `not_after` | `2026-07-20T20:00:00+08:00` |
| Authorization JSON path | `reviews/milestone-004-preregistration-v1.3.2/authorizations/capability-authorization-2026-07-20-01.json` |
| Authorization checksum path | `reviews/milestone-004-preregistration-v1.3.2/authorizations/capability-authorization-2026-07-20-01.sha256` |
| Attempt output directory | `artifacts/milestone-004/v1.3.2/capability-probes/m4-v132-capability-20260720-01` |
| Exact request budget | maximum 35 serial read-only calls; no ordinal 36 and no `stock_st` |

## Mandatory evidence and affirmations

Every row must be completed in the copied decision record. `PENDING_EXTERNAL_DECISION` is a blocker.

| Decision field | External decision evidence |
|---|---|
| `lin` current accountable authority confirmed | `PENDING_EXTERNAL_DECISION` |
| Current account tier/expiry evidence reference | `PENDING_EXTERNAL_DECISION` |
| `index_classify` entitlement and remaining quota | `PENDING_EXTERNAL_DECISION` |
| `index_member_all` entitlement and quota for 31 calls | `PENDING_EXTERNAL_DECISION` |
| `stock_basic` entitlement and quota for 2 calls | `PENDING_EXTERNAL_DECISION` |
| `daily_basic` entitlement and remaining quota | `PENDING_EXTERNAL_DECISION` |
| Cost/points consumption accepted | `PENDING_EXTERNAL_DECISION` |
| Current provider terms permit the attempt and sealed retention | `PENDING_EXTERNAL_DECISION` |
| `2026-07-17` completed probe-date suitability confirmed | `PENDING_EXTERNAL_DECISION` |
| Window is still future and operationally sufficient | `PENDING_EXTERNAL_DECISION` |
| Target JSON/checksum/attempt/control paths are absent | `PENDING_EXTERNAL_DECISION` |
| Historical `40203` and fail-fast/no-retry accepted | `PENDING_EXTERNAL_DECISION` |
| Name-only ST residual risk explicitly accepted | `PENDING_EXTERNAL_DECISION` |
| Exact packet checksum inventory verified | `PENDING_EXTERNAL_DECISION` |

## Decision

Select exactly one outcome in the copied record. Set the selected marker to `SELECTED`, both others to
`NOT_SELECTED`, and complete all fields for that outcome. Do not preselect an outcome in this template.

### APPROVE

Selection: `PENDING_EXTERNAL_DECISION`

> I approve one MILESTONE-004 v1.3.2 Tushare capability attempt using only the exact values above and the exact
> frozen 35-call v3 boundary. I authorize the named publisher to create one canonical
> `m4-segmented-rest-frame-authorization-v3` JSON/checksum pair and the named operator to invoke it exactly once in
> the approved window. I have verified current non-secret account entitlement and remaining quota for all four API
> families. I accept the maximum 35 serial read-only calls, 128 MiB aggregate limit, historical `40203` risk,
> fail-fast/single-use/no-retry behavior, immutable PASS/FAIL or stale-state evidence, current-checkout provenance,
> and the inability of name-only `ST|*ST` detection to identify an unmarked risk-warning security. This decision does
> not authorize date evidence, capture, assembly, Reviewer/model execution, production DB/pipeline access, fallback,
> cleanup/resume, MILESTONE-005, or adoption.

Approver identity and accountable role: `PENDING_EXTERNAL_DECISION`

Decision evidence reference: `PENDING_EXTERNAL_DECISION`

Decision timestamp: `PENDING_EXTERNAL_DECISION` (`Asia/Shanghai`)

Signature/attestation: `PENDING_EXTERNAL_DECISION`

### REJECT

Selection: `PENDING_EXTERNAL_DECISION`

Reason and required changes: `PENDING_EXTERNAL_DECISION`

Approver identity and accountable role: `PENDING_EXTERNAL_DECISION`

Decision timestamp: `PENDING_EXTERNAL_DECISION` (`Asia/Shanghai`)

Signature/attestation: `PENDING_EXTERNAL_DECISION`

### DEFER

Selection: `PENDING_EXTERNAL_DECISION`

Blocking evidence or decision needed: `PENDING_EXTERNAL_DECISION`

Approver identity and accountable role: `PENDING_EXTERNAL_DECISION`

Decision timestamp: `PENDING_EXTERNAL_DECISION` (`Asia/Shanghai`)

Signature/attestation: `PENDING_EXTERNAL_DECISION`

## Post-approval handoff gate

Only a complete, fresh `APPROVE` decision permits these later actions:

1. The publisher re-verifies freeze attestation, packet checksum, exact HEAD, generator hashes, and absence of the
   approved authorization/checksum/attempt/control paths.
2. Without reading a credential, the publisher mechanically builds the exact authorization from
   `frame_call_matrix("2026-07-17")`, publishes new canonical JSON and exact checksum create-only, then validates them
   with `load_authorization()` and records the authorization SHA-256.
3. A separate token-free check confirms the JSON/checksum bytes match the signed decision. No `execute` command is
   run during publication or validation.
4. The operator confirms the approved checkout and supplies `TUSHARE_TOKEN` only through the process environment.
5. During the approved window, the operator invokes exactly once:

   ```bash
   .venv/bin/python qualitative_v2_m4_segmented_rest_v132.py execute \
     --authorization reviews/milestone-004-preregistration-v1.3.2/authorizations/capability-authorization-2026-07-20-01.json \
     --authorization-sha256 reviews/milestone-004-preregistration-v1.3.2/authorizations/capability-authorization-2026-07-20-01.sha256
   ```

6. The operator removes the token and runs offline verification:

   ```bash
   env -u TUSHARE_TOKEN \
     .venv/bin/python qualitative_v2_m4_segmented_rest_v132.py verify \
     --attempt-dir artifacts/milestone-004/v1.3.2/capability-probes/m4-v132-capability-20260720-01
   ```

   Exit `0` is verified PASS, `1` is sealed verified FAIL, and `2` is not executed or integrity-blocked.

7. PASS proceeds only to a new date-evidence approval request. FAIL or integrity block is preserved and returns to
   external decision-making. No retry or direct capture is permitted.

Completion of this checklist cannot expand the signed decision.
