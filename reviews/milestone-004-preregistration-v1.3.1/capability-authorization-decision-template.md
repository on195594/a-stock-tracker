# MILESTONE-004 v1.3.1 capability authorization external decision

Related request: [`capability-authorization-approval-request.md`](capability-authorization-approval-request.md)

Decision status: **CLOSED WITH v1.3.1 REQUEST — UNSIGNED — NOT AN AUTHORIZATION**

This template is retained only as historical preparation evidence. It must not be completed or reused for v1.3.2.

This record must not be renamed to an approval, used to create canonical authorization bytes, or treated as execution
authority until every pending field is completed and an accountable external approver signs one decision below.

## Proposed bounded values

| Field | External decision |
|---|---|
| Approver identity and role | `PENDING_EXTERNAL_DECISION` |
| Authorized publisher identity | `PENDING_EXTERNAL_DECISION` |
| Authorized operator identity | `PENDING_EXTERNAL_DECISION` |
| `authorization_id` | `PENDING_EXTERNAL_DECISION` |
| `attempt_id` | `PENDING_EXTERNAL_DECISION` |
| `probe_trade_date` | `PENDING_EXTERNAL_DECISION` |
| `not_before` (`+08:00`) | `PENDING_EXTERNAL_DECISION` |
| `not_after` (`+08:00`) | `PENDING_EXTERNAL_DECISION` |
| Authorization JSON project-relative path | `PENDING_EXTERNAL_DECISION` |
| Authorization checksum project-relative path | `PENDING_EXTERNAL_DECISION` |
| Provider entitlement/quota check evidence | `PENDING_EXTERNAL_DECISION` |
| External decision evidence reference | `PENDING_EXTERNAL_DECISION` |

## Decision

Select exactly one and replace its pending marker with `APPROVED`, `REJECTED`, or `DEFERRED`. Replace both unselected
markers with `NOT_SELECTED`, then complete the matching evidence and identity fields. Do not preselect an outcome.

### Approve

`PENDING_EXTERNAL_DECISION`

> I approve one MILESTONE-004 v1.3.1 Tushare capability attempt using only the exact values fixed above and the fixed
> 36-call, fail-fast boundary in the related request. I authorize the named publisher to create one canonical
> `m4-segmented-rest-frame-authorization-v2` JSON/checksum pair and the named operator to invoke it once within the
> approved window. I accept the prior `40203` entitlement/quota risk, immutable failure evidence, maximum 36 read-only
> calls, 128 MiB aggregate response limit, single-use/no-retry rule, and current-checkout provenance dependency. This
> decision does not authorize date evidence, capture, assembly, Reviewer/model execution, production DB or pipeline
> access, fallback providers, cleanup/resume, or MILESTONE-005.

Approver signature/name: `PENDING_EXTERNAL_DECISION`

Decision timestamp: `PENDING_EXTERNAL_DECISION` (`Asia/Shanghai`)

### Reject

`PENDING_EXTERNAL_DECISION`

Reason and required changes: `PENDING_EXTERNAL_DECISION`

Approver signature/name: `PENDING_EXTERNAL_DECISION`

Decision timestamp: `PENDING_EXTERNAL_DECISION` (`Asia/Shanghai`)

### Defer

`PENDING_EXTERNAL_DECISION`

Blocking evidence or decision needed: `PENDING_EXTERNAL_DECISION`

Approver signature/name: `PENDING_EXTERNAL_DECISION`

Decision timestamp: `PENDING_EXTERNAL_DECISION` (`Asia/Shanghai`)

## Post-approval handoff gate

Only after an Approve decision is complete:

1. The named publisher verifies that the target JSON, checksum, attempt directory, lock, and journal paths do not
   exist.
2. The publisher mechanically generates the exact 36-call array with `frame_call_matrix(probe_trade_date)`, writes
   new canonical JSON and the exact checksum sidecar create-only, and does not read any credential.
3. A second person or isolated check calls `load_authorization(json_path, checksum_path)` before the window and records
   the resulting authorization SHA-256. This validation must not invoke `execute`.
4. The operator confirms the exact approved checkout and supplies `TUSHARE_TOKEN` only in the process environment.
5. During the window, the operator invokes the executor exactly once. No preflight provider call, retry, resume,
   alternate origin, SDK, MCP, or supplemental call is permitted. The only allowed command shape is:

   ```bash
   .venv/bin/python qualitative_v2_m4_segmented_rest.py execute \
     --authorization APPROVED_JSON_PATH \
     --authorization-sha256 APPROVED_CHECKSUM_PATH
   ```

6. The operator removes the token from the environment and runs offline verification. All authorization, protocol,
   generator, control, artifact, and checksum files are preserved unchanged. The only verification command shape is:

   ```bash
   env -u TUSHARE_TOKEN \
     .venv/bin/python qualitative_v2_m4_segmented_rest.py verify \
     --attempt-dir artifacts/milestone-004/v1.3.1/capability-probes/APPROVED_ATTEMPT_ID
   ```

   Exit `0` is verified PASS, `1` is a sealed verified FAIL, and `2` is not executed or integrity-blocked.

7. PASS proceeds only to a new date-evidence approval request. FAIL or integrity block returns to external decision.

Completion of this checklist is operational evidence; it cannot expand the signed approval.
