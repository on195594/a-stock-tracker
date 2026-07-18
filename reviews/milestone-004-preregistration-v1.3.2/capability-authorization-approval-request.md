# MILESTONE-004 v1.3.2 capability authorization external approval request

Document ID: `m4-v1.3.2-capability-approval-request-2026-07-18-01`

Prepared: `2026-07-18` (`Asia/Shanghai`)

Decision status: **UNSIGNED DRAFT — NOT AN AUTHORIZATION — NO EXECUTION AUTHORITY**

Requested authority: one bounded, read-only Tushare capability attempt under frozen protocol v1.3.2

Related input record:
[`capability-authorization-input-2026-07-18-01.md`](capability-authorization-input-2026-07-18-01.md)

External decision form:
[`capability-authorization-decision-template.md`](capability-authorization-decision-template.md)

## 审批人摘要

本申请只请求一次 v1.3.2 capability 探测：最多 35 个串行只读 Tushare REST 请求，任一普通失败即停止，
不重试、不续跑、不切换来源。与已撤销的 v1.3.1 请求不同，本矩阵不存在 ordinal 36，也不调用
`stock_st`；风险警示排除只依赖 `stock_basic.name` 的 NFKC、outer trim、casefold 后 `ST|*ST` 前缀。
因此它无法识别 provider 名称未带标记的风险警示股，审批者必须明确接受该残余风险。

本文、输入记录、决策模板和 packet checksum 都不是 authorization。外部 `APPROVE` 完成前，不得生成
canonical authorization JSON/sidecar、读取 token、调用 provider 或创建 attempt/control artifacts。

## Decision requested

The fresh external approver is asked to approve, reject, or defer publication and one-time execution of exactly one
new capability authorization using the proposed bounded values below. The approver must be independent from the
v1.3.2 protocol reviewers and from the Claude Opus implementation-review identity.

Approval, if granted, is limited to:

- one unique `authorization_id` and one unique `attempt_id`;
- one `probe_trade_date` and one future whole-second `+08:00` execution window;
- at most one attempt of the exact ordered 35-call matrix, with no retry, resume, fallback, pagination, parallelism,
  supplemental call, SDK, MCP, redirect, proxy, or alternate provider;
- read-only certificate-verified HTTPS POST access to exactly `https://api.tushare.pro:443`, using only the existing
  `TUSHARE_TOKEN` process-environment value after the create-only lock is acquired; and
- create-only attempt artifacts below the exact v1.3.2 capability root.

Approval does not authorize date-selection evidence, capture, frame/sample assembly, Reviewer/model execution,
production database access, pipeline/cron/Telegram changes, fallback providers, a second attempt, cleanup/resume,
MILESTONE-005, or production adoption.

## Proposed bounded values requiring external affirmation

These values are fixed proposals, not authority. The external decision must affirm them exactly or defer/reject and
require a replacement packet. It must not edit this request in place.

| Field | Proposed exact value |
|---|---|
| Accountable requestor/account owner | `lin` — must reaffirm current authority in the signed decision |
| External approver | `PENDING_FRESH_EXTERNAL_APPROVER` |
| Authorized publisher | `Codex`, only after signed `APPROVE`; JSON/checksum publication only; no token access |
| Authorized operator | `Codex`, exactly one invocation in the approved window |
| `authorization_id` | `qualitative-v2-m4-v132-capability-20260720-01` |
| `attempt_id` | `m4-v132-capability-20260720-01` |
| `probe_trade_date` | `2026-07-17` — approver must affirm suitability as completed SSE/SZSE data date |
| `not_before` | `2026-07-20T18:00:00+08:00` |
| `not_after` | `2026-07-20T20:00:00+08:00` |
| Authorization JSON path | `reviews/milestone-004-preregistration-v1.3.2/authorizations/capability-authorization-2026-07-20-01.json` |
| Authorization checksum path | `reviews/milestone-004-preregistration-v1.3.2/authorizations/capability-authorization-2026-07-20-01.sha256` |
| Attempt output directory | `artifacts/milestone-004/v1.3.2/capability-probes/m4-v132-capability-20260720-01` |

If the window is no longer future when the decision is signed, the approver must select `DEFER`, and a new packet
with new IDs and paths must be created. No timestamp may be silently shifted after approval.

## Binding frozen baseline

| Item | Exact reviewed value |
|---|---|
| Protocol ID | `qualitative-v2-m4-prereg-v1.3.2` |
| Protocol path | `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.2.md` |
| Protocol SHA-256 | `555b1d4c410da1dc68be3279a2070f55b5e73f7b6ef9ca7d850f89d0533dbeea` |
| Direct predecessor SHA-256 | `f494c1973f002536874782af41221d37a2ca618c329fa48a2ae650b544c88042` |
| Schema contract SHA-256 | `a3552bf2fb1325839439a2e26dc4b2423d3d9dd07c68effde89cef1c576c82f7` |
| Golden index SHA-256 | `3d2ba5878e1d8ba0e09448772341736e3c3b5b51741cd48fb9a10348a3f0ab01` |
| Preregistration inventory SHA-256 | `961dbf22b0a12dba4e51769c70687fd91bba09497782754b67bf91969b1b8086` |
| Candidate manifest SHA-256 | `7c79a2106cec0b2253ac56db443571a4f163fa04814f33332811f3e3d0bceecb` |
| Freeze attestation SHA-256 | `9164984a0177a462c95947b37cad8aec445151fdc81734968534e3c6ab6aea42` |
| Reviewed implementation commit | `a133f2df23b51b0e4aa9c97da38d7f4cd53329cc` |
| Freeze commit | `2d5f5aefebc433bd51af3f82bb25cc67f451c75d` |
| Claude implementation review | `85cb9fdd0230fa37843f63671cc1d8c9b916549d2955acd8bd96bb24ce5730f3`; PASS, P0–P3 NONE |

The valid external freeze attestation makes the exact protocol candidate effective. This request is downstream
governance evidence and does not alter `P/K/G/C/M`. Any drift in the frozen protocol, preregistration inventory,
authorization after publication, or five-file generator closure must fail closed.

## Fixed authorization contract after approval

The authorized publisher has no discretion over these values:

| Field | Exact value |
|---|---|
| `schema_version` | `m4-segmented-rest-frame-authorization-v3` |
| `mode` | `capability` |
| `protocol_path` | `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.2.md` |
| `protocol_sha256` | `555b1d4c410da1dc68be3279a2070f55b5e73f7b6ef9ca7d850f89d0533dbeea` |
| `supersedes_sha256` | `f494c1973f002536874782af41221d37a2ca618c329fa48a2ae650b544c88042` |
| `origin` | `https://api.tushare.pro` |
| `credential_env_var` | `TUSHARE_TOKEN` |
| `response_byte_limit` | `33554432` |
| `attempt_byte_limit` | `134217728` |
| `max_attempts_per_ordinal` | `1` |
| `trade_date_kind` | `probe_trade_date` |
| `capability_manifest_ref` | `null` |
| `date_evidence_ref` | `null` |

After approval, the publisher must mechanically generate `calls` with
`frame_call_matrix("2026-07-17")`, serialize canonical UTF-8 JSON with one LF, create the exact checksum line
`<authorization_sha256>  <authorization_project_relative_path>\n`, and validate the pair with
`load_authorization()` before handoff. Hand-authoring, copying the golden authorization, or adapting any v1.3.1
authorization is prohibited.

## Exact request matrix and data exposure

| Ordinal(s) | API and exact parameters | Ordered response fields | Max calls |
|---|---|---|---:|
| 1 | `index_classify(level=L1,src=SW2021)` | `index_code,industry_name,level,src` | 1 |
| 2–32 | `index_member_all(l1_code=<one frozen SW2021 L1 code>,is_new=Y)` | `l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,ts_code,name,in_date,out_date,is_new` | 31 |
| 33–34 | `stock_basic(exchange=SSE/SZSE,list_status=L)` | `ts_code,name,market,exchange,curr_type,list_status,list_date` | 2 |
| 35 | `daily_basic(trade_date=20260717)` | `ts_code,trade_date,total_mv` | 1 |
| **Total** | No ordinal 36; no `stock_st` |  | **35** |

Responses may include listed-company identifiers/names, industry membership, listing metadata, and market
capitalization. Successful raw responses are sealed locally with a 32 MiB per-response and 128 MiB aggregate limit.
Capability bytes are permanently non-adoptable as frame/sample input.

## Provider entitlement, quota, cost, and terms gate

The historical v1.3.1 input record states that `lin` purchased a 2000-point tier on `2026-07-17`, expiring
`2027-07-17`, accepted fee/points use and service terms, and can supply a correctly shaped token without disclosure.
That historical record also captured public-document evidence that the 2000-point tier covers the four API families
remaining in v1.3.2. Removing `stock_st` removes the documented 3000-point blocker that caused v1.3.1 withdrawal.

Those facts do **not** prove current account state, account-specific entitlement, or remaining quota. Before APPROVE,
the external decision must record current, non-secret evidence for all four API families, remaining per-minute and
per-day quota sufficient for the maximum calls below, fee/points impact, and current terms acceptance:

| API family | Maximum calls | Required decision evidence |
|---|---:|---|
| `index_classify` | 1 | current account entitlement + remaining quota |
| `index_member_all` | 31 | current account entitlement + remaining quota for 31 serial calls |
| `stock_basic` | 2 | current account entitlement + remaining quota |
| `daily_basic` | 1 | current account entitlement + remaining quota |

No provider call or token read may be used as a preflight. If evidence is unavailable or contradictory, the only
valid external outcome is `DEFER` or `REJECT`.

## Risks requiring explicit acceptance

- Historical v1.2 capability calls returned provider `40203` for `index_classify`, `index_member_all`, and
  `daily_basic`. The new protocol cannot grant entitlement; the attempt may seal FAIL on ordinal 1.
- The name-only ST rule excludes only `stock_basic.name` values beginning with normalized `ST` or `*ST`. A risk-warning
  security whose provider name is unmarked can remain eligible; no alternate ST endpoint or supplemental lookup is
  permitted.
- A capability attempt may leave immutable PASS/FAIL evidence or a stale/orphan control state. It cannot be repaired,
  deleted, resumed, or retried automatically.
- The 35-call figure is a maximum request budget, not a promise that every request is sent. Any ordinary failure is
  fail-fast.
- Capability PASS authorizes only preparation of a separate date-evidence approval request. It is not date evidence,
  capture authority, frame/sample evidence, or production adoption.

## Approval prerequisites

- [ ] The fresh external approver reviewed this exact packet and its checksum inventory.
- [ ] `lin` reaffirmed accountable authority, current 2000-point account status, cost/points acceptance, and terms.
- [ ] Current non-secret entitlement/quota evidence covers all four API families and the 35-call maximum.
- [ ] The approver accepts historical `40203`, single-use/no-retry, immutable evidence, and unmarked-name risk.
- [ ] The approver affirms the exact roles, IDs, probe date, window, paths, origin, limits, and non-goals above.
- [ ] The proposed authorization/checksum, attempt directory, lock, journal, and publication-temp paths remain absent.
- [ ] The operator can supply a fresh exact 64-character lowercase hexadecimal `TUSHARE_TOKEN` through the process
      environment without reading `.env` or exposing it in chat, commands, logs, screenshots, or artifacts.
- [ ] The checkout and five generator files will remain unchanged from authorization publication through offline
      verification.
- [ ] Production `tracker.db`, pipeline, cron, Telegram, Gemini, Reviewer/model systems, fallback providers, and
      legacy assemblers remain untouched.

## Outcome handling

After the approved single invocation, the operator removes `TUSHARE_TOKEN` and runs token-free offline verification.

- verified PASS: preserve the sealed capability attempt and prepare a separate date-evidence approval request;
- verified sealed FAIL: preserve the attempt, make no retry, and return to external decision-making;
- exit 2, integrity block, or stale/orphan state: preserve all evidence, perform no cleanup or network resume, and
  escalate for a new protocol decision.

No outcome advances directly to capture, assembly, Reviewer, production integration, or MILESTONE-005.

