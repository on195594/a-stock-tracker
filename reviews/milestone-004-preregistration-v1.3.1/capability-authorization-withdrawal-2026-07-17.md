# MILESTONE-004 v1.3.1 capability authorization request withdrawal

Decision date: `2026-07-17` (`Asia/Shanghai`)

Decision authority: `lin` (project owner)

Status: **WITHDRAWN — NO AUTHORIZATION PUBLISHED — NO ATTEMPT EXECUTED**

## User direction

> 修改方案，不做st股票列表分析

## Effect

- The v1.3.1 external approval request is closed without approval.
- No canonical v1.3.1 authorization JSON/checksum was created.
- No v1.3.1 token was read and no provider request or capability attempt was executed.
- The proposed v1.3.1 authorization ID, attempt ID, paths, and `2026-07-18T09:00:00+08:00` through
  `2026-07-18T11:00:00+08:00` window are retired and must not be reused.
- Claude's DEFER decision remains the final external decision for the closed v1.3.1 request.
- Frozen v1.3.1 protocol, checksum, golden vectors, implementation, tests, and review records remain unchanged.

The replacement direction is a draft v1.3.2 repair plan that removes the independent `stock_st` request and its gate,
retains only a local `stock_basic.name` prefix safety exclusion, and reduces the frame matrix from 36 to 35 calls. The
draft is not frozen or authorized and must complete protocol and implementation review before any new external
capability approval can be requested.

Replacement plan:
[`docs/plans/2026-07-17-milestone-004-segmented-rest-v1.3.2-repair-plan.md`](../../docs/plans/2026-07-17-milestone-004-segmented-rest-v1.3.2-repair-plan.md)
