# MILESTONE-004 v1.3.1 capability authorization supplied decision input

Input supplied by: `lin` (project owner and Tushare account owner)

Supplied on: `2026-07-17` (`Asia/Shanghai`)

Record status: **INSUFFICIENT ENTITLEMENT — NOT AN AUTHORIZATION — DEFER REMAINS**

Related request: [`capability-authorization-approval-request.md`](capability-authorization-approval-request.md)

Prior external decision:
[`capability-authorization-external-review-2026-07-17.md`](capability-authorization-external-review-2026-07-17.md)

## Supplied authority and roles

| Field | Supplied value |
|---|---|
| Accountable authority | `lin` |
| Role | Project owner |
| Authority for maximum 36 read-only Tushare requests | Confirmed: yes |
| Authorized publisher | Codex, only after final APPROVE; canonical JSON/checksum generation only; no token access |
| Authorized operator | Codex |

## Supplied and normalized attempt identity

| Field | Value |
|---|---|
| `authorization_id` | `qualitative-v2-m4-v131-capability-20260717-01` |
| `attempt_id` | `m4-v131-capability-20260717-01` |
| `probe_trade_date` | `2026-07-17` |
| `not_before` | `2026-07-18T09:00:00+08:00` |
| `not_after` | `2026-07-18T11:00:00+08:00` |
| Authorization JSON path | `reviews/milestone-004-preregistration-v1.3.1/authorizations/capability-authorization-2026-07-17-01.json` |
| Checksum path | `reviews/milestone-004-preregistration-v1.3.1/authorizations/capability-authorization-2026-07-17-01.sha256` |
| Attempt output directory | `artifacts/milestone-004/v1.3.1/capability-probes/m4-v131-capability-20260717-01` |

The user supplied the filename placeholder `YYYY-MM-DD`; Codex mechanically expanded it to `2026-07-17`, matching
the IDs and probe date. At `2026-07-17T15:38:35+08:00`, the JSON, checksum, attempt directory, attempt lock, phase
journal, publication temporary directory, and both IDs were absent from the project. This was a filesystem-only check
and made no provider request.

## Account and service attestation

The user supplied the following account statement:

- 2000 Tushare points purchased on `2026-07-17`;
- expiry on `2027-07-17`, described as 364 days remaining;
- points level: 2000;
- fee/points use accepted: yes;
- service terms permit this read-only probe: yes;
- account-specific access, per-API quota, remaining daily quota, and evidence file path: not supplied;
- `index_classify`, `index_member_all`, `stock_basic`, `stock_st`, and `daily_basic` entitlement: each originally
  supplied as uncertain.

The user entered evidence acquisition time as `2027-07-17`. That conflicts with the purchase date, current date, and
stated expiry date, so this record preserves it as an unresolved likely typo. Codex has not silently changed it.

## Official public documentation check

Checked on `2026-07-17` without using the user's token or account:

- Tushare's official permission table lists `daily_basic`, `index_classify`, and `index_member_all` at a 2000-point
  minimum: <https://tushare.pro/document/1?doc_id=108>.
- Tushare's official `stock_basic` page states a 2000-point minimum and 50 requests per minute:
  <https://tushare.pro/document/1?doc_id=25>.
- Tushare's official points/frequency page states that the 2000-point tier generally permits 200 requests per minute
  and 100,000 requests per API per day, subject to each interface's own requirement:
  <https://tushare.pro/document/1?doc_id=290>.
- The current official permission table does not enumerate the new `stock_st` interface. No official account-specific
  or public evidence was supplied that 2000 points grants `stock_st` access. This remains unresolved and no live
  preflight is authorized.

The public documentation supports the minimum point threshold for four of the five API families and a general request
rate above the 36-call protocol maximum. It cannot prove the user's current account state, remaining quota, or
`stock_st` entitlement, and therefore does not replace account-side evidence.

## Credential-supply attestation

`lin` confirmed on `2026-07-17` that:

- `TUSHARE_TOKEN` can be supplied through the process environment during the approved window;
- it is exactly 64 lowercase hexadecimal characters;
- `.env` will not be read;
- the token will not be sent to chat, Git, commands, screenshots, logs, or approval material;
- no approver or Codex process is asked to read or validate it before approval; and
- inability to supply it safely will cause execution to be abandoned rather than bypassing the gate.

No token value was supplied, read, or validated while recording this statement.

## Pending clarifications before external rereview

1. Confirm whether the evidence acquisition time should be `2026-07-17`, rather than the supplied
   `2027-07-17`.
2. Supply official/account-side evidence that the 2000-point account can call `stock_st`, or explicitly state that no
   such evidence exists. Unknown entitlement cannot be converted into an APPROVE decision by a live preflight because
   the frozen protocol and prior external review prohibit any extra provider call.

Until both points are resolved, the prior **DEFER** remains in force. Canonical authorization publication, token access,
provider preflight, and the real capability attempt remain prohibited.

## User-supplied permission-table verification

At `2026-07-17T15:52:50+08:00`, Codex read the public, guest-readable Tencent sheet supplied by `lin`:
<https://docs.qq.com/sheet/DT0FIYUxYakJ5c1FF?tab=BB08J2>. The document title is `积分权限表`; the public response reports
its last save as `2026年06月04日 21:44`. No login, token, account access, edit, export, or Tushare provider request was
used.

The sheet presents the point tiers in increasing order: 120, 2000, 3000, 5000, 6000, 8000, 10000, and 15000. Its
2000-point column covers general base data, low-frequency market data, financial data, and macro data, but does not
list `ST股票列表`. `ST股票列表` first appears in the 3000-point column and remains present in higher tiers.

For the frozen v1.3.1 matrix, `ST股票列表` maps to ordinal 35 `stock_st(trade_date=YYYYMMDD)`. Therefore:

- 2000 points support the other four required API families according to the public official documentation recorded
  above;
- 2000 points do **not** meet the supplied permission table's threshold for `stock_st`;
- the exact five-family, maximum-36-call capability prerequisite is not satisfied; and
- approving execution would predictably spend up to the first 34 calls before reaching an entitlement-blocked ordinal
  35, without any retry or alternate API permitted.

This resolves the `stock_st` uncertainty as **INSUFFICIENT AT 2000 POINTS** under the user-designated permission table.
The evidence-timestamp typo remains administratively unresolved, but correcting it cannot cure the entitlement gap.
External rereview is unnecessary until the account has at least the table's 3000-point tier (or stronger authoritative
account-side evidence for `stock_st`) and a fresh future execution window is proposed. The prior **DEFER** continues to
prohibit canonical authorization publication, token access, provider preflight, and real execution.
