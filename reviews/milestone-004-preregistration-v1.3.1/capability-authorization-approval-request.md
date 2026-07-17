# MILESTONE-004 v1.3.1 capability authorization external approval request

Document ID: `m4-v1.3.1-capability-approval-request-2026-07-17-01`

Prepared: `2026-07-17` (`Asia/Shanghai`)

Decision status: **WITHDRAWN — SUPERSEDED BY DRAFT v1.3.2 REPAIR — NOT AN AUTHORIZATION**

Withdrawal record:
[`capability-authorization-withdrawal-2026-07-17.md`](capability-authorization-withdrawal-2026-07-17.md)

Requested authority: one bounded, read-only Tushare capability attempt under the frozen v1.3.1 protocol

## 审批人摘要

本申请只请求一次 v1.3.1 capability 探测：最多 36 个串行只读 Tushare REST 请求，任一普通失败即停止，
不重试、不续跑、不切换来源。历史 v1.2 探测曾因账户权限/配额收到 `40203`，因此本次仍可能在首个请求后
封存 FAIL；分段调用不会改变账户权限。审批者需要明确指定审批人、授权发布人、执行人、唯一 IDs、探测
交易日、`+08:00` 执行窗口和项目内发布路径，并接受单次消费与失败证据永久保留。本文及配套模板目前均
不是授权，也不允许生成 canonical authorization、读取 token 或联网。

## Decision requested

The external approver is asked to approve or reject publication and execution of exactly one new capability
authorization after the decision form's pending values have been completed. Approval is limited to:

- one unique `authorization_id` and one unique `attempt_id`;
- one externally selected `probe_trade_date`;
- one explicit `Asia/Shanghai` execution window;
- at most one attempt of the ordered 36-call matrix below, with no retry, resume, fallback, pagination, parallelism,
  supplemental call, SDK, or alternate provider;
- read-only HTTPS POST access to exactly `https://api.tushare.pro:443` using the existing `TUSHARE_TOKEN` environment
  value; and
- create-only artifacts below
  `artifacts/milestone-004/v1.3.1/capability-probes/<attempt_id>`.

The attempt is fail-fast. A provider, HTTP, transport, parsing, schema, deadline, or integrity failure can stop before
ordinal 36. The 36-call figure is therefore the maximum authorized request budget, not a promise that every request
will be sent.

Approval does not authorize date-selection evidence, formal capture, frame/sample assembly, Reviewer/model execution,
production database access, pipeline/cron/Telegram changes, a retry, or MILESTONE-005.

## Binding engineering baseline

| Item | Reviewed value |
|---|---|
| Protocol ID/version | `qualitative-v2-m4-prereg-v1.3.1` / `1.3.1` |
| Protocol path | `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.1.md` |
| Protocol SHA-256 | `f494c1973f002536874782af41221d37a2ca618c329fa48a2ae650b544c88042` |
| Direct predecessor SHA-256 | `0ad8c6b3d5175af96409e570bdc17839ac7cf266227410f2e9e9ea756a7a12df` |
| Golden vectors SHA-256 | `7135598a0095d9a1496b39f3f82d83fa6e629846046d87bae8b9780cc619ee2c` |
| Implementation commit | `0bb791653927118a8ef661b7275f2854cec5623e` |
| Implementation review | [`codex-independent-implementation-review.md`](codex-independent-implementation-review.md): PASS, P0–P3 NONE; later P2/P3 findings remediated in the recorded follow-up |
| Post-remediation gates | 133 directed tests; 300 M4 tests; 778 repository tests; Ruff, F401, format, mypy, frozen checksums and diff check PASS |

The protocol bytes are frozen and must not be edited to complete this request. If the protocol, its checksum, the
authorization after publication, or any of the five generator files drifts, execution or later offline verification
must fail closed. The generator hashes observed while preparing this request are recorded in Appendix B for audit
comparison; the sealed attempt manifest, not this request, is the normative generator binding.

## Values the external decision must fix

No executable authorization may be published until every pending value below is supplied by the external approver or
an explicitly named authorized publisher acting from the approved decision.

| Decision field | Required value and constraint |
|---|---|
| Approver identity | Named accountable person or approval authority |
| Authorized publisher | Named person/process permitted to materialize the canonical JSON and checksum |
| Authorized operator | Named person/process permitted to invoke the executor once |
| `authorization_id` | New unique ID matching `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` |
| `attempt_id` | New unique ID under the same ID rule; its output directory must not already exist |
| `probe_trade_date` | `YYYY-MM-DD`; preferably a completed common SSE/SZSE open day whose Tushare data should be final |
| `not_before` | Exact `YYYY-MM-DDTHH:MM:SS+08:00` start |
| `not_after` | Exact `YYYY-MM-DDTHH:MM:SS+08:00` hard deadline, not earlier than `not_before` |
| Authorization JSON path | New normalized project-relative path, conventionally below this review directory |
| Checksum path | Exactly the JSON path with suffix replaced by `.sha256` |

The window should provide operational margin for 36 serial calls while remaining narrowly bounded. The executor may
read the token only after acquiring the create-only attempt lock, and may never extend `not_after`.

## Fixed authorization values

The authorized publisher has no discretion over these values:

| Field | Exact value |
|---|---|
| `schema_version` | `m4-segmented-rest-frame-authorization-v2` |
| `mode` | `capability` |
| `protocol_path` | `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.1.md` |
| `protocol_sha256` | `f494c1973f002536874782af41221d37a2ca618c329fa48a2ae650b544c88042` |
| `supersedes_sha256` | `0ad8c6b3d5175af96409e570bdc17839ac7cf266227410f2e9e9ea756a7a12df` |
| `origin` | `https://api.tushare.pro` |
| `credential_env_var` | `TUSHARE_TOKEN` |
| `response_byte_limit` | `33554432` |
| `attempt_byte_limit` | `134217728` |
| `max_attempts_per_ordinal` | `1` |
| `trade_date_kind` | `probe_trade_date` |
| `capability_manifest_ref` | `null` |
| `date_evidence_ref` | `null` |
| `attempt_output_dir` | `artifacts/milestone-004/v1.3.1/capability-probes/<attempt_id>` |

The published JSON must be UTF-8 canonical JSON: compact, recursively key-sorted, Unicode-preserving, with exactly one
trailing LF. Its sidecar must contain exactly
`<authorization_sha256>  <authorization_project_relative_path>\n`. The external publisher must generate the call
array from `frame_call_matrix(probe_trade_date)` and validate the completed pair with `load_authorization()` before
handoff. Hand-authoring or copying a historical v1.2/v1.3 authorization is not acceptable.

## Request matrix and data exposure

All calls are read-only provider queries. Request bodies contain only the fixed API name, the in-memory token, exact
parameters, and exact ordered fields. The implementation does not persist request bodies, headers, cookies, token
material, or raw exception text.

| Ordinal(s) | API and exact parameters | Ordered response fields | Maximum calls |
|---|---|---|---:|
| 1 | `index_classify(level=L1,src=SW2021)` | `index_code,industry_name,level,src` | 1 |
| 2–32 | `index_member_all(l1_code=<frozen code>,is_new=Y)` | `l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,ts_code,name,in_date,out_date,is_new` | 31 |
| 33–34 | `stock_basic(exchange=SSE/SZSE,list_status=L)` | `ts_code,name,market,exchange,curr_type,list_status,list_date` | 2 |
| 35 | `stock_st(trade_date=YYYYMMDD)` | `ts_code,name,trade_date,type,type_name` | 1 |
| 36 | `daily_basic(trade_date=YYYYMMDD)` | `ts_code,trade_date,total_mv` | 1 |
| **Total** |  |  | **36** |

Responses may contain listed-company identifiers, names, industry membership, listing metadata, ST status, and market
capitalization. Raw successful responses are sealed locally, with a 32 MiB per-response and 128 MiB aggregate limit.
They are capability evidence only and are permanently non-adoptable as frame/sample input.

## Known risks requiring explicit acceptance

- The v1.2 probe received Tushare `40203` for `index_classify`, `index_member_all`, and `daily_basic`. Segmentation in
  v1.3.1 does not grant account entitlement or increase provider quota. A new attempt may fail immediately and remain
  useful only as sealed negative capability evidence.
- The implementation cannot perform a live entitlement or quota preflight without consuming an unapproved provider
  request. The approver should confirm account permissions, quota/cost expectations, and provider terms outside the
  executor before authorizing publication.
- Invoking the attempt can leave an immutable FAIL, stale lock, or orphan state. It must not be repaired, deleted,
  resumed, or retried automatically; another attempt requires a new explicit decision and new authorization.
- Offline verification intentionally depends on retaining the exact authorization/checksum, frozen protocol/checksum,
  and current five generator files in the project. Deletion or modification produces provenance drift.
- A capability PASS authorizes only a future proposal for a separately approved two-call date-evidence attempt. It is
  not an approval for that attempt and is not evidence that a frame or sample has been frozen.

## Outcome handling

After the one invocation, the operator must remove `TUSHARE_TOKEN` from the verification environment and run the
token-free offline verifier. The accepted outcomes are:

- verified PASS: preserve the sealed capability attempt and prepare a separate date-evidence approval request;
- verified sealed FAIL: preserve the attempt, make no retry, and return to external decision-making; or
- exit 2 / integrity block / stale-orphan state: preserve all evidence, make no cleanup or network resume, and
  escalate for a new protocol decision.

No outcome advances directly to capture, assembly, Reviewer, production integration, or MILESTONE-005.

## Approval prerequisites checklist

- [ ] The approver has reviewed the exact protocol hash and this request's non-authorization status.
- [ ] Provider entitlement, quota/cost, and terms have been checked for all five API families.
- [ ] The approver accepts the prior `40203` failure risk and single-use/no-retry rule.
- [ ] Approver, publisher, operator, IDs, trade date, and execution window are fixed in the decision record.
- [ ] The operator can supply a fresh exact 64-character lowercase hexadecimal `TUSHARE_TOKEN` via the environment
      without reading `.env` or disclosing it in chat, commands, logs, or artifacts.
- [ ] The target authorization/checksum paths and attempt output directory do not already exist.
- [ ] The checkout and five generator files will remain unchanged from authorization publication through offline
      verification.
- [ ] Production `tracker.db`, pipeline, cron, Telegram, Reviewer/model systems, fallback providers, and legacy
      assemblers will remain untouched.

## Appendix A — frozen membership ordinals

| Ordinal | `l1_code` | Industry |
|---:|---|---|
| 2 | `801010.SI` | 农林牧渔 |
| 3 | `801030.SI` | 基础化工 |
| 4 | `801040.SI` | 钢铁 |
| 5 | `801050.SI` | 有色金属 |
| 6 | `801080.SI` | 电子 |
| 7 | `801110.SI` | 家用电器 |
| 8 | `801120.SI` | 食品饮料 |
| 9 | `801130.SI` | 纺织服饰 |
| 10 | `801140.SI` | 轻工制造 |
| 11 | `801150.SI` | 医药生物 |
| 12 | `801160.SI` | 公用事业 |
| 13 | `801170.SI` | 交通运输 |
| 14 | `801180.SI` | 房地产 |
| 15 | `801200.SI` | 商贸零售 |
| 16 | `801210.SI` | 社会服务 |
| 17 | `801230.SI` | 综合 |
| 18 | `801710.SI` | 建筑材料 |
| 19 | `801720.SI` | 建筑装饰 |
| 20 | `801730.SI` | 电力设备 |
| 21 | `801740.SI` | 国防军工 |
| 22 | `801750.SI` | 计算机 |
| 23 | `801760.SI` | 传媒 |
| 24 | `801770.SI` | 通信 |
| 25 | `801780.SI` | 银行 |
| 26 | `801790.SI` | 非银金融 |
| 27 | `801880.SI` | 汽车 |
| 28 | `801890.SI` | 机械设备 |
| 29 | `801950.SI` | 煤炭 |
| 30 | `801960.SI` | 石油石化 |
| 31 | `801970.SI` | 环保 |
| 32 | `801980.SI` | 美容护理 |

## Appendix B — preparation-time generator hashes

| Project-relative path | SHA-256 at commit `0bb7916` |
|---|---|
| `qualitative_v2_audit.py` | `9472444a19c1abca942f5ff63bc5c092f1b6e0a713a7b12369d67c6ef2251bb1` |
| `qualitative_v2_m4_segmented_core.py` | `f6f683da1158527e39b0eb9a534dbce0e82c88a2a9012284043a88ab818209d3` |
| `qualitative_v2_m4_segmented_rest.py` | `6b17b614efd72f0b744dfade266487e81b029e91175a8705f098f75ff3411331` |
| `qualitative_v2_m4_segmented_runtime.py` | `0aee0361834eee8be93a8096f8bbdd09376223ded80ac287dd7210cb217fb04e` |
| `qualitative_v2_m4_segmented_verify.py` | `196ea57de992973d810a9f9b8d710ace4ee79e9f84524fc0d1b97b624593b557` |
