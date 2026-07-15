# CHANGELOG

所有重大变更按时间倒序记录。

## 2026-07-15 — MILESTONE-004 v1.1 离线审计工具链
- 冻结 evidence-feasibility audit v1.1：保留原 sampling seed，新增官方 SW2021 31 行业映射、source-aware URL 归一化和 binary64 Wilson 展示契约。
- 新增纯 Python frame/sample/corpus、technical-attempt ledger、create-only artifact hash chain、双 reviewer seal/index、disagreement/adjudication 和 coverage report API。
- Reviewer 默认禁用；执行授权绑定 bundle/prompt/model/backend/command/environment，隔离 HOME/cache/state/tmp，增量限制 stdout/stderr 并在超时或超限时终止整个进程组。
- 严格审查后补强报告 lineage、company-scoped relationship evidence、原子 no-replace 发布、跨进程 stage 锁、manifest/seal TOCTOU 和完整裁决 schema。
- 验证基线：76 项 M4 定向测试、554 项全量测试；Ruff lint/format、mypy、协议 v1/v1.1 SHA-256 和 `git diff --check` 全部通过。
- 本轮未读取真实数据库、未检索真实公司、未运行真实 Reviewer；工具链完成不等同于 M4 coverage audit 完成或 MILESTONE-005 授权。

## 2026-07-15 — 运维门禁、PM 监控与质量基线修复
- 刷新当天 Tushare capability probe：daily/index/calendar/close cross-check 全部 PASS，恢复 `READY_CRON` 并重新安装 managed cron。
- 修复 weekly PM loop 将中文“失败 0 只”和降级 WARNING 误判为 FAIL：零失败忽略，降级 warning 保持 WARN，明确 ERROR/非零失败仍为 FAIL。
- 修复全库 mypy 24 个错误；对 31 个历史文件执行一次性 Ruff format 基线化。
- 新基线：`373 passed`，Ruff lint/format、mypy、`git diff --check` 全部通过。

## 2026-07-15 — 定性评分 v2 fixture-first 合同（进行中）
- 批准 `docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md`，授权范围仅限 MILESTONE-002 fixture-first。
- task 2.1 已实现 `qualitative_v2_types.py` 与 `qualitative_v2_taxonomy.py`：版本化 dataclass、input hash 和 `evidence_type × claim_category` 两层 taxonomy。
- task 2.2 已实现 `qualitative_v2_validator.py`：shape、版本、引用、freshness、rubric 和 all-or-nothing 的纯本地 fail-closed 校验。
- 尚未实现 schema/prompt builder，也未接真实 Gemini、生产 DB、pipeline、cron、Telegram 或权重。

## 2026-07-12 — L3 v2 QFQ 生产接入与推送切换
- QFQ 日线完成 35/35 股票回填并增加工作日 16:00 采集 cron，生产 wrapper 优先使用完整 QFQ 窗口。
- daily 已写入 L3 v2 审计字段；Telegram 主推条件从 v1 `entry_signal=1` 切换为 `l3_v2_signal=1`。
- v1 继续保留用于历史审计，L3 v2 不修改 L1/L2 `total_score`。

## 2026-07-10 — L3 v2 只读离线回测与复盘
- 新增 `scripts/offline_l3_v2_backtest.py`，以 SQLite `mode=ro` + `PRAGMA query_only=ON` 只读回测 Framework A predictions。
- 输出 `signals_daily.csv`、`events_raw_daily.csv`、`events_dedup_20d.csv`、`metrics_summary.json` 和 Markdown 报告。
- 支持项目 `.env` 中的 `TUSHARE_TOKEN`，但当前 Tushare `adj_factor` 返回 `1次/分钟` 限频，qfq 覆盖为 `0/38`，decision gate 保持 `NEED_QFQ`。
- 修复复审发现的回测口径问题：20 日冷却改为按市场交易日计算；qfq volume 按复权比例反向调整。
- AGY 独立复审最终 `APPROVE`；本轮结论写入 `docs/reviews/2026-07-10-l3-v2-backtest-retro.md`。

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
