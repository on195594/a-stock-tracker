# MILESTONE-004 v1.3.2 capability authorization external decision

Document ID: `m4-v1.3.2-capability-decision-2026-07-18-subagent-01`

APPROVER_IDENTITY: `Codex subagent /root/v132_capability_approver`

APPROVER_ROLE: Fresh independent, read-only M4 v1.3.2 capability external approver

ISOLATION: `PASS` — this approver did not author or fix the candidate and did not participate in protocol or implementation review.

Decision timestamp: `2026-07-18T12:38:14+08:00` (`Asia/Shanghai`)

## Packet and frozen-baseline verification

| Item | Result |
|---|---|
| Packet checksum-file path | `reviews/milestone-004-preregistration-v1.3.2/capability-approval-packet-2026-07-18-01.sha256` |
| Packet checksum-file SHA-256 | `6bc1f336dd3cf8e5bc996bc21eb2f4e36c5c175dbdab6bd3bfcec379990c58c7` |
| Packet inventory verification | `PASS` — all three listed files returned `OK` |
| Freeze-attestation SHA-256 | `9164984a0177a462c95947b37cad8aec445151fdc81734968534e3c6ab6aea42` |
| Freeze-chain verification | `PASS` — `frozen=true` |
| Candidate-manifest SHA-256 | `7c79a2106cec0b2253ac56db443571a4f163fa04814f33332811f3e3d0bceecb` |
| Reviewed implementation commit | `a133f2df23b51b0e4aa9c97da38d7f4cd53329cc` |
| Claude implementation-review SHA-256 | `85cb9fdd0230fa37843f63671cc1d8c9b916549d2955acd8bd96bb24ce5730f3` |
| Claude implementation-review result | `PASS`; P0–P3 `NONE` |
| Checkout HEAD during decision | `f06400492990d3db3b30ecfdc46f2815ff4bd249` |
| Working tree during decision | Clean |

## Exact proposed boundary reviewed

| Field | Exact proposed value |
|---|---|
| Accountable authority/account owner | `lin` |
| Publisher | `Codex` — only after valid APPROVE; JSON/checksum publication only; no token access |
| Operator | `Codex` — exactly one invocation |
| `authorization_id` | `qualitative-v2-m4-v132-capability-20260720-01` |
| `attempt_id` | `m4-v132-capability-20260720-01` |
| `probe_trade_date` | `2026-07-17` |
| `not_before` | `2026-07-20T18:00:00+08:00` |
| `not_after` | `2026-07-20T20:00:00+08:00` |
| Authorization JSON | `reviews/milestone-004-preregistration-v1.3.2/authorizations/capability-authorization-2026-07-20-01.json` |
| Authorization checksum | `reviews/milestone-004-preregistration-v1.3.2/authorizations/capability-authorization-2026-07-20-01.sha256` |
| Attempt output | `artifacts/milestone-004/v1.3.2/capability-probes/m4-v132-capability-20260720-01` |
| Request boundary | Maximum 35 serial read-only calls; no ordinal 36; no `stock_st` |
| API families | `index_classify`, `index_member_all`, `stock_basic`, `daily_basic` |

## Mandatory evidence and affirmations

| Decision field | Independent result |
|---|---|
| `lin` current accountable authority confirmed | `MISSING` — only a historical record is present; no current reaffirmation |
| Current account tier/expiry evidence | `MISSING` — historical 2000-point statement is not current account evidence |
| `index_classify` entitlement and remaining quota | `MISSING` |
| `index_member_all` entitlement and quota for 31 calls | `MISSING` |
| `stock_basic` entitlement and quota for 2 calls | `MISSING` |
| `daily_basic` entitlement and remaining quota | `MISSING` |
| Cost/points consumption accepted | `MISSING` — historical acceptance does not establish current acceptance |
| Current provider terms permit attempt and sealed retention | `MISSING` |
| `2026-07-17` completed probe-date suitability | `MISSING` — not independently affirmed |
| Window remains future and operationally sufficient | `PARTIAL PASS` — future at decision time; operational sufficiency not independently established |
| Target authorization/checksum/attempt/control paths absent | `PASS` — targeted paths and their parent directories were absent |
| Historical `40203` and fail-fast/no-retry accepted | `MISSING` — disclosed, but no current explicit acceptance supplied |
| Name-only ST residual risk explicitly accepted | `MISSING` — disclosed, but no current explicit acceptance supplied |
| Exact packet checksum inventory verified | `PASS` |

Public documentation and historical point statements were not treated as proof of current account entitlement or quota.

## Decision

### APPROVE

Selection: `NOT_SELECTED`

### REJECT

Selection: `NOT_SELECTED`

### DEFER

Selection: `SELECTED`

Blocking evidence or decision needed:

1. Current non-secret, account-side entitlement and remaining-quota evidence for all four API families.
2. Current account tier/expiry, cost/points acceptance, and provider-terms confirmation.
3. Current affirmation of `lin`’s accountable authority.
4. Affirmation of probe-date suitability and operational sufficiency.
5. Explicit acceptance of historical `40203`, immutable fail-fast/no-retry behavior, and name-only ST residual risk.
6. If the proposed window is no longer future when these items are supplied, a replacement packet with fresh IDs, paths, and window.

The blockers are potentially curable, so `DEFER` is appropriate rather than permanent `REJECT`.

DECISION: `DEFER`

EXECUTION_AUTHORITY: `NOT_GRANTED`

Signature/attestation: I attest that this decision was reached independently from the exact reviewed material and direct offline verification. Missing mandatory evidence was not inferred or waived.

## Safety attestation

No repository file was edited or created. No authorization, checksum, attempt, lock, journal, or publication artifact was generated. No `.env`, token, environment secret, credential store, `tracker.db`, production log, provider, network, preflight, pipeline, cron, Telegram, Gemini, or model execution was accessed.
