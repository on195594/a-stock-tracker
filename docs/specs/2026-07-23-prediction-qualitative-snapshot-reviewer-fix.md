# Prediction Qualitative Snapshot Reviewer Fix

状态：approved for production implementation
日期：2026-07-23
Owner：Hermes parent

## Goal Contract

- **goal**：确保 Telegram 展示与 Gemini reviewer 使用的定性分项，和对应 `predictions` 行生成 `total_score` 时实际采用的 v1/v2/hybrid 分项完全一致。
- **done_when**：新 prediction 原子保存定性分值快照、逐维来源和采用模式；Telegram 只用该快照调用 reviewer；无合法快照时 reviewer fail-closed；生产 DB 完成 additive migration；历史评分字段与行数不变；全部质量门通过。
- **must_not**：不得重算或回填历史 prediction；不得修改 `total_score`、`weights_hash`、outcome、L3 信号、权重、watchlist、cron、凭证或外部通知配置；测试不得访问网络或真实 DB；不得新增依赖。
- **verify**：原失败案例 + 纯 v1 反向边界；定向测试；全量 pytest；Ruff lint/format；mypy；结构测试；DB quick_check；migration 前后 predictions 逻辑快照与受保护字段 hash/行数一致。
- **closeout**：更新项目状态/经验文档；保存实现 commit、独立 reviewer 结论、生产迁移证据和回滚方式。

## Requirements

### REQ-001 — Typed production selection

`a_stock_tracker.qualitative.production` 必须提供 typed selection，包含：

- `scores`：`moat`、`market_pos`、`sentiment` 的实际采用值；
- `sources`：每个维度精确标记为 `v1` 或 `v2`；
- `mode`：`v1`、`v2` 或 `hybrid_v2`。

现有 `get_production_qualitative_score()` 保持返回 `dict[str, int]` 的兼容合同。

### REQ-002 — Prediction-level immutable snapshot

`predictions` 增加 nullable additive 字段：

- `qualitative_snapshot_json`；
- `qualitative_sources_json`；
- `qualitative_mode`。

新 prediction INSERT 必须和 `total_score` 在同一 stock SAVEPOINT 中写入这三个字段。`INSERT OR IGNORE` 命中既有行时不得用当前值覆盖旧快照。历史行保持 NULL，不做回填。

### REQ-003 — Reviewer and display consistency

Telegram 对有合法 prediction 快照的行：

- 展示分项和 reviewer 输入均使用快照；
- reviewer 输入显式包含逐维来源和采用模式；
- 不再用最新 legacy 行解释 v2/hybrid 生成的总分。

旧行缺少快照、JSON 损坏、字段/范围/来源/模式不合法时：

- reviewer 不得调用；
- 展示层可继续使用 legacy 值作为兼容展示，但必须记录 WARNING；
- 不得静默把 legacy 值标为 prediction 当时输入。

### REQ-004 — Validation

快照只接受精确字段 `moat`、`market_pos`、`sentiment`；整数范围分别为 1..10、1..5、1..5；bool 必须拒绝。来源只接受 `v1`/`v2`，且必须与 mode 一致：

- `v1`：全部来源为 v1；
- `v2`：全部来源为 v2；
- `hybrid_v2`：同时包含 v1 和 v2。

### REQ-005 — Production migration safety

生产迁移前必须：

1. 确认无运行中的 tracker 进程；
2. 使用 SQLite online backup 创建快照；
3. 验证 backup `quick_check=ok`、predictions 行数和逻辑 dump hash；
4. 在隔离 worktree 完成测试和审查。

生产迁移后必须：

1. 三个字段存在；
2. migration 前的 1999 条历史 predictions 仍存在且新字段为 NULL；
3. predictions 受保护旧字段逻辑 hash不变；
4. `quick_check=ok`；
5. 在临时 DB 完成一次新行 snapshot→Telegram reviewer smoke，禁止真实网络。

## Acceptance tests

1. **原失败案例**：prediction 快照为 hybrid `9/5/3`、legacy 最新行为 `7/3/3` 时，展示和 reviewer 必须接收 `9/5/3` 及来源 `v2/v2/v1`。
2. **反向边界**：纯 v1 prediction 快照行为保持 reviewer 可用，来源全部为 v1。
3. **Fail-closed**：无快照或损坏快照时 reviewer 不调用。
4. **Schema**：新库和旧库 additive migration 均出现三个字段；旧数据值保持 NULL。
5. **Compatibility**：现有 `get_production_qualitative_score()` 测试保持通过。

## Execution lane

- 实现、测试、迁移由父级 Hermes 直接执行。
- 独立 reviewer 只读检查 diff；其自报结论必须由父级核验。
- 外部 Gemini/Telegram、真实 daily、cron 变更均不在本任务中。

## Stop conditions

出现以下任一项立即停止并回滚代码落地：

- 需要修改既有 prediction 分数或回填历史快照；
- migration 前后 predictions 旧字段 hash/行数变化；
- 新依赖；
- 真实外部调用；
- cron/runtime 配置变化；
- 全量测试、Ruff、mypy、结构检查或 DB quick_check 失败且三轮内不能定位修复；
- reviewer 与确定性验证对核心合同结论冲突。

## Rollback

- 代码：在未产生新格式 prediction 前 revert landing commit；additive nullable 列可保留，不影响旧代码。
- 数据：若 migration 后尚无新业务写入且 DB 检查失败，使用已验证 backup 原子恢复；若已有新业务写入，不覆盖生产 DB，改为代码回退并保留 additive 列后另行处理。
