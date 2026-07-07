# CHANGELOG

所有重大变更按时间倒序记录。

## 2026-07-07 — 工程审查与文档清理
- 修复核心文档（CLAUDE.md、test-plan.md、project-status.md）中的数据不一致问题。
- 归档历史修复计划（REPAIR-PLAN.md）并清理无用备份、空目录及缓存文件。
- 完善 `.gitignore`，增加工具缓存和 agent 配置目录。

## 2026-07-05 — 推送分层推荐与代码清理
- Telegram 推送新增三级分层推荐（Primary, Backup, Radar）并修复了由于无日期过滤导致的 JOIN 重复行。
- 清理 `pipeline.py` 中遗留的死代码（`_retry`, `_normalize`, `snapshot_data`）。

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

## 2026-07-02 — Phase 6 周期性自动化与加固
- 自动化 Phase 6 周度 PM 复核。
- 强化 cron 异常捕获与 flock 失败告警。
- 加固行情 cron 恢复门禁并清理残余进程。

## 2026-06-25 — 行情数据源演进（Market Data Boundary Refactor）
- 引入统一的 `a-stock-lib`，实现 Provider 会话复用、限流重试、本地缓存及安全回退。
- 退役 tracker 本地的行情 Provider 副本，并彻底隔离 `BaoStock` 作为 Tushare 失败后的降级。

## 2026-05-30 — Phase 5：L3 买点层接入
- 实现并验证 L3 Entry Signal（价量分析）并将过滤结果集成至 Telegram 日常推送及 accuracy-report。

## 2026-05-29 — Phase A 数据治理规划
- 新增数据源 Registry 及质量门槛，迁移 PB 分位至 `scorer.py` 减少耦合，定义只读 agent reviewer 接口。

## 2026-05-15 — REPAIR-PLAN v2.0 修复
- 对齐数据质量的 PB 历史长度判断；解决日度无实质变化的 Bug。

## 2026-04-27 — Phase 3.6 完成与重构
- 弃用 Python 内容聚合，Google Sheets 迁移使用 COUNTIFS 公式提高同步效率与稳定性；对齐精度报告阈值。

## 2026-04-20 — Phase 3 上线
- Gemini 2.5 Flash 替代定性评分，集成 30 天缓存与退避重试降级。
- 增加 .env 自动加载与基于 SQLite 的本地推送缓存机制。
