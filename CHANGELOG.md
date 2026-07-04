# CHANGELOG

## 2026-07-04 — agy 工程审查修复（Batch A + B）

### 修复
- DB 写入改为 per-stock SAVEPOINT 隔离，崩溃重跑幂等（06f3d88/b3f1c4c）
- spot_em 改重试计数器（连续3次才今日锁定，单次抖动不再锁死）（23bd764/e056e93）
- Gemini 退避重试（429/5xx，最多3次）+ 过期缓存降级（4058688）
- subprocess TimeoutExpired 绑定异常变量，转发 stderr 日志（1e8b401）
- cache._ensure_columns 列名正则白名单，防 SQL identifier injection（ab9fdb9）
- outcome window SQL allowlist 常量（_OUTCOME_WINDOWS frozenset）（1ccc3da）

### 新增
- agent_reviewer 接入真实 Gemini REST API，任何错误回退 _fake_review_fallback（df765cb）
- cmd_init / cmd_weekly 测试覆盖（delegation + fetcher call-count 断言）（3a72a47/63e3eb4）
- cmd_outcome_update Telegram mock 测试（smoke test + window guard）（5e44ec2/165aa1b）

**测试基线：225 passed, 1 skipped（vs 修复前 211 passed）**
