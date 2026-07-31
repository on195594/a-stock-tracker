# TuShare BPS 送转口径修正

状态：implemented and production-verified（2026-07-31）

## Goal

让 `stock_fundamentals.bps` 与估值日 `daily_basic.pb` 使用相同的股本口径：保留 TuShare `fina_indicator.bps` 原值，并按最新财报期之后、估值日之前已实施的送股/转增事件累计调整每股净资产，消除因除权导致的伪估值冲突。

## Evidence

TuShare 官方 `dividend` 文档（https://tushare.pro/document/2?doc_id=103）定义：`stk_div` 为“每股送转”，`stk_bo_rate` 为“每股送股比例”，`stk_co_rate` 为“每股转增比例”，`ex_date` 为除权除息日。

截至 2026-07-31：

- `603606`：报告期 `20260331`，原始 BPS `12.368`；2026-05-26 已实施每股转增 `0.2`，调整因子 `1.2`，可比 BPS `10.306666...`。价格 `41.15` 对应重算 PB `3.99256`，与源 PB `3.9904` 相对差异约 `0.054%`。
- `600785`：报告期 `20260331`，原始 BPS `10.235`；2026-06-25 已实施每股转增 `0.4`，调整因子 `1.4`，可比 BPS `7.310714...`。价格 `8.37` 对应重算 PB `1.144895`，与源 PB `1.1449` 基本一致。
- 当前 watchlist 中仅上述两只股票在最新财报期后、估值日前存在正送转事件。

## Non-goals

- 不修改 TuShare shadow 原始观察、schema、`daily_basic.pb`、历史 PB 序列、估值阈值或监控容差。
- 不反向改写 `fina_indicator.bps`；原值持久化为 `bps_reported`。
- 不使用价格/PB 反推 BPS，不用人工股票白名单或硬编码 `1.2/1.4`。
- 不改变现金分红 DPS 逻辑。
- 不在本任务中重建历史每个交易日的 BPS 口径。

## Requirements

### R1 — 事件选择

对每只股票选择满足以下全部条件的事件：

1. `div_proc == "实施"`；
2. `report_period < ex_date <= as_of_date`；其中 `report_period` 是最新有效财报期末日，`as_of_date` 是 valuation materialization 的估值基准交易日，三者解析为 ISO 日历日期后直接比较；TuShare `ex_date` 按官方除权生效日处理，不做前后交易日映射；
3. 事件已存在于 TuShare dividend shadow。

按 `(code, end_date, record_date, ex_date)` 去重；同一事件的多个观察/修订按 `(observed_at, ann_date, payload_sha256)` 降序形成确定性全序，选择第一条，空值按空字符串排序，防止重复乘算。

### R2 — 送转比率

- 优先采用 TuShare 聚合字段 `stk_div`。
- 仅当 `stk_div` 为 `NULL` 时，回退到 `stk_bo_rate + stk_co_rate`；TuShare 可选分项中的 `NULL` 按 0 处理，两个分项均缺失表示现金分红事件、送转率为 0。
- `stk_div` 非空且合法时仅使用 `stk_div`，不得与分项同时累加；若已提供分项之和与 `stk_div` 在 `1e-9` 容差内不一致，则 fail-closed，避免供应商字段矛盾被静默接受。
- 现金分红或送转率为 0 的事件不改变因子。
- 任何已提供但非有限或小于 0 的比率必须以 `INVALID_SHARE_DISTRIBUTION_RATE:<code>:<ex_date>` fail-closed，禁止静默忽略。

### R3 — BPS 口径

- 累计因子：`Π(1 + share_distribution_rate)`。
- `bps_reported`：最新 PIT-safe `fina_indicator.bps` 原值。
- `bps`：`bps_reported / cumulative_factor`，保留足够精度，禁止两位小数截断。
- 物化审计字段：
  - `bps_share_adjustment_factor`：有限 `float` 且 `>=1.0`；无事件时为 `1.0`；
  - `bps_adjustment_ex_dates`：按日期升序、去重的 ISO `YYYY-MM-DD` JSON 数组；无事件时为空数组，不为 `NULL`；
  - `bps_basis_report_period`：本轮依据的 `fina_indicator.end_date`，`YYYYMMDD`，不可空；
  - `bps_basis_as_of`：本轮估值基准日，ISO `YYYY-MM-DD`，不可空；
  - `bps_basis_source`：无调整时为 `tushare.fina_indicator.bps`；有调整时为 `tushare.fina_indicator.bps+tushare.dividend.share_distribution`。

### R4 — 日/周生产链一致

- 周期财务物化写入完整财务字段和调整后的 BPS。
- 每日 valuation-only 物化也必须从已有 financial/dividend shadow 重算 BPS 口径，确保除权日后不必等到周末。
- 缺少有效 BPS 基础时 valuation 或 financial 物化应 fail-closed，不得保留看似当日有效的旧口径 BPS。

### R5 — 原子性

- preview 不写 target DB。
- preview 与 execute 必须复用完全相同的事件选择、去重、计算和校验路径；fail-closed 时抛出带股票代码和事件日期的 `MaterializationReadinessError`，CLI 非零退出，不得伪装为空 diff。
- execute 继续复用现有 `BEGIN IMMEDIATE` 全 watchlist 原子事务。
- 任一股票比率非法或基础数据不完整时，整轮执行不得部分写入。

### R6 — 数据契约文档

更新 `docs/data-source-registry.yaml`：明确 `bps_reported` 来自周度 `fina_indicator`，`bps` 是由财报原值与已实施 `dividend` 送转事件计算的估值可比口径，并由每日 valuation materialization 重算。

## Acceptance criteria

1. RED 测试覆盖：单次送转、`stk_div`/分项防双计、多个事件累计、财报期前事件忽略、估值日后事件忽略、现金分红不调整、重复修订去重、非法负数/非有限值 fail-closed。
2. valuation-only 与 financial-only 两条物化路径均输出相同 BPS basis。
3. 真实 shadow preview 对 `603606` 输出因子 `1.2`、可比 BPS约 `10.306667`；对 `600785` 输出因子 `1.4`、可比 BPS约 `7.310714`。
4. 全 watchlist preview 只新增 BPS 审计字段；旧 `bps` 仅对存在报告期后正送转的股票发生数值变化，预期恰为 `603606`、`600785`。
5. 候选 tracker DB execute 后：`PRAGMA quick_check=ok`，除 `stock_fundamentals.data/updated_at` 外其他表行级摘要不变。
6. `east-cable-monitor` 只读 smoke 中 `603606` 源 PB 与重算 PB 相对差异小于 `1%`，`valuation_consistent=true`。
7. tracker 全套 pytest、Ruff、format、mypy、`git diff --check` 通过；独立只读审查无 blocker。

## Rollback and stop conditions

- 在隔离 worktree 开发；生产切换前记录代码 SHA，并使用 SQLite online backup 生成带时间戳备份。
- 若 preview 变化股票集合不是恰好 `{600785, 603606}`、任一 PB 差异仍超过 1%、候选 quick_check 失败、非目标表摘要变化、或审查存在 blocker，则停止生产写入。
- 生产 execute 使用现有原子 materializer；执行后独立只读验证。失败时停止监控依赖链并从已验证备份恢复 `tracker.db`。

## Production verification

- 实现提交：`4279c22`。
- 生产 SQLite online backup：`/home/lin/.hermes/backups/a-stock-tracker/tracker.db.pre-bps-basis-20260731T205416+0800`；`PRAGMA integrity_check=ok`。
- 生产物化：35 只 watchlist 全部写入 BPS basis 审计字段；旧 `bps` 仅 `600785`、`603606` 两只发生数值变化。
- `600785`：`10.235 -> 7.31071429`，因子 `1.4`；adapter 重算 PB `1.1449`，`valuation_consistent=true`。
- `603606`：`12.368 -> 10.30666667`，因子 `1.2`；adapter 重算 PB `3.9926`，相对源 PB `3.9904` 差异约 `0.0542%`，`valuation_consistent=true`。
- `PRAGMA quick_check=ok`；14 张非目标表摘要与备份一致；`stock_fundamentals.data` 无非 basis 字段变化。
- 生产 checkout：263 tests passed；Ruff、format、mypy、`git diff --check` 通过；独立聚焦复审结论 `APPROVE`。
