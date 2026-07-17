# MILESTONE-004 v1.3.1 capability authorization 外部审批意见

审批角色：Claude（独立只读外部审批者）

审批任务：`/root/claude_external_approver`

审批时间：`2026-07-17T15:19:56+08:00`

相关申请：`m4-v1.3.1-capability-approval-request-2026-07-17-01`

## DECISION

**DEFER（暂缓审批）**

当前申请不能批准。原因不是已发现新的实现缺陷，而是形成有效授权所必需的身份、参数、执行边界及
provider 权限证据仍全部为 `PENDING_EXTERNAL_DECISION`。审批者不得代填或推定这些事实。

## Findings

- **P0: NONE**
- **P1: APPROVAL BLOCKER** — approver、publisher、operator、两个唯一 ID、trade date、执行窗口、发布路径及
  entitlement/quota 证据均未确定。历史证据明确显示 `index_classify`、`index_member_all` 无访问权限，
  `daily_basic` 触发 `5次/天` 限额；在没有新的账户侧证明前，不能认定本次最多 36 次调用已获 provider
  权限和配额支持。
- **P2: AUDITABILITY** — 当前审批请求和 decision template 尚未纳入 Git，相关 runbook/status/CHANGELOG
  也处于未提交修改状态。签署前应将完整、已填值的最终决策材料固定为可复核的提交或显式内容哈希，
  避免审批对象发生漂移。
- **P3: NONE**

P1/P2 是治理与授权阻断项，不是对已提交 v1.3.1 Python 实现的新代码缺陷判定。

## 已核对基线

- Protocol ID/version：`qualitative-v2-m4-prereg-v1.3.1` / `1.3.1`
- Protocol path：`docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.1.md`
- Protocol SHA-256：`f494c1973f002536874782af41221d37a2ca618c329fa48a2ae650b544c88042`
- Direct predecessor：`0ad8c6b3d5175af96409e570bdc17839ac7cf266227410f2e9e9ea756a7a12df`
- Golden vectors：`7135598a0095d9a1496b39f3f82d83fa6e629846046d87bae8b9780cc619ee2c`
- Implementation commit：`0bb791653927118a8ef661b7275f2854cec5623e`
- `preregistration.sha256`：校验 PASS
- 当前五个 generator 文件与上述 commit 无 diff，实测 SHA-256 与申请 Appendix B 一致。
- 独立实现审查记录为 PASS；后续 P2 count 校验问题和四项 P3 清理已有修复记录，记录的
  post-remediation gates 为 133/300/778 tests 及 Ruff、mypy、checksum、diff check PASS。
- 36-call 矩阵、fail-fast、单 ordinal 最多一次、无 retry/resume/fallback、只读固定 origin、capability
  永久 non-adoptable 等边界与冻结协议一致。
- 历史 `40203` 风险有项目内 manifest、blob 和审计报告支持，不是推测。
- 未发现 v1.3.1 正式 authorization；约定俗成的 `capability-authorization.json` 与 `.sha256` 路径目前
  不存在，但最终目标路径尚未选定，因此不能完成唯一性检查。

本次复核未读取 token、未联网、未访问生产数据库、未执行 provider 请求、pipeline 或 Reviewer，也未
修改任何文件。

## 审批前置条件状态

| 前置条件 | 状态 | 审批意见 |
|---|---|---|
| 冻结协议、lineage、checksum | PASS | 已核对 |
| 实现 commit 与 generator closure | PASS | 当前 generator 与 commit 一致 |
| 最大 36 次、单次、无重试边界 | PASS | 申请描述与协议一致 |
| Approver 身份和角色 | BLOCKED | 未提供 |
| Authorized publisher | BLOCKED | 未提供 |
| Authorized operator | BLOCKED | 未提供 |
| 唯一 `authorization_id` | BLOCKED | 未提供 |
| 唯一 `attempt_id` 及输出目录不存在性 | BLOCKED | 未提供，无法核验 |
| `probe_trade_date` | BLOCKED | 未提供 |
| `not_before` / `not_after` | BLOCKED | 未提供 |
| JSON/checksum 精确项目内路径 | BLOCKED | 未提供 |
| 五个 API family entitlement | BLOCKED | 未提供新的账户侧证据；历史为权限拒绝 |
| quota/cost/terms 确认 | BLOCKED | 未提供 |
| 64 位小写十六进制 token 的安全供应能力确认 | BLOCKED | 只需证明能力，不得提交 token |
| 最终审批记录固定和签署 | BLOCKED | 当前模板未签署且未纳入版本控制 |

## 当前权限结论

- **此刻生成 canonical authorization JSON/checksum：不允许。**
- **此刻读取、请求或验证 `TUSHARE_TOKEN`：不允许。**
- **此刻执行真实 capability attempt：不允许。**
- **执行 entitlement preflight 或任何额外 provider 调用：不允许。**

## 解除 DEFER 所需的最小信息

用户或具备责任权限的审批主体需要提供：

1. Approver 的姓名/组织身份和可承担责任的角色。
2. Authorized publisher 与 authorized operator 的明确身份。
3. 新且唯一的 `authorization_id` 和 `attempt_id`。
4. 已完成并数据应已终结的 `probe_trade_date`。
5. 精确到秒、带 `+08:00` 的 `not_before` 和 `not_after`。
6. JSON 项目相对路径及其对应 `.sha256` 路径。
7. 对以下五类 API 的账户 entitlement 证据：`index_classify`、`index_member_all`、`stock_basic`、
   `stock_st`、`daily_basic`。
8. 足以覆盖最多 36 次串行请求的 quota/cost/terms 书面确认，特别说明历史 `40203` 是否已解决。
9. operator 能在不披露凭证的情况下通过环境提供合规 token 的确认；不得发送 token 本身。
10. 最终审批记录的归档引用，并将最终填值材料固定到 Git commit 或内容哈希。

上述信息齐全后，必须对“已填完整的最终决策记录”重新进行一次外部复核。只有届时明确签署
**APPROVE**，才可由已命名 publisher 机械生成 canonical JSON/checksum；生成后仍须先以
`load_authorization()` 无网络校验，真实执行权才可能生效。
