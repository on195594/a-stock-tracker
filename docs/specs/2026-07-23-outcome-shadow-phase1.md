# 历史 outcome versioned shadow Phase 1 规格

日期：2026-07-23
状态：approved for Phase 1 implementation
算法版本：`tushare_raw_price_return_v1`
生产迁移：未授权

## 1. 目标

在不修改生产 `predictions` 和现有报告读取口径的前提下，冻结截至 2026-07-23 已到期的 prediction/window 事件，在隔离数据库中构建可审计、可重放的 TuShare raw-close shadow，并量化旧/新 outcome、benchmark 与 alpha 差异。

## 2. 固定范围

- `as_of_date=2026-07-23`。
- 窗口为 30/60/90 个自然日。
- 固定 cohort 预期 1,776 行：30d 1,271、60d 501、90d 4。
- 输入 prediction 身份字段：`id/code/name/framework/score_date/price_at_score/quant_score/total_score/weights_hash/report_period/created_at`。
- 后续新增 prediction 不进入本 run。

## 3. 非目标

- 不创建或导入生产 shadow 表；不更新 `predictions` 的任何字段。
- 不改变 `outcome-update`、`accuracy-report`、Sheets、Phase 4、Framework B 或 cron。
- 不把 QFQ、现金分红或 total return 混入 `tushare_raw_price_return_v1`。
- 不把差异整体归因于 BaoStock；审计只能证明 14 次成功 fallback 下界。
- 不自动切换报告口径。

## 4. 数据源与日期合同

### 4.1 股票

仅接受 `daily_bars.source='tushare.daily' AND adjusted='none'`。entry anchor 为 `score_date`，target anchor 为 `score_date + window_days`；两者都只选择不晚于 anchor、向前最多10个自然日内的最近交易日。当前 TuShare entry 永远标记为 `ex_post_reconstructed`，不得冒充决策时快照。

### 4.2 Benchmark

沪深300仅使用新获取并冻结的 `tushare.index_daily` 标准化快照，不读取旧 `index_prices`。entry/target 独立执行同样的向前10自然日规则，保存 source、抓取时间、输入 hash、anchor 与实际交易日。

### 4.3 计算分离

每个事件同时保存：

1. `stored_entry_shadow_outcome`：旧 `price_at_score` + TuShare target，用于隔离 target/source 差异；
2. `reconstructed_shadow_outcome`：TuShare entry + TuShare target，用于完整 raw-close 事后重建；
3. TuShare benchmark 与两套 alpha。

股票和 benchmark entry 实际交易日不一致时，结果标记 `computed_calendar_mismatch`，不进入主要 aligned-alpha 汇总。

## 5. 隔离 schema

### 5.1 `outcome_shadow_runs`

仅保存完整提交的 immutable run。`run_id` 由 schema/algorithm/as-of/cohort/stock-input/benchmark-input manifest 派生；保存各输入 hash、production protection hash、状态计数和创建时间。失败或半成品只进入 ignored artifact，不进入 final run 表。

### 5.2 `outcome_shadow_observations`

保存冻结 cohort 日期解析所需的股票和指数输入窗口，按 run/instrument/date/source/adjusted 去重；保存 OHLC/close、抓取时间和 observation hash。

### 5.3 `outcome_shadow_results`

主键 `(run_id,prediction_id,window_days)`；保存 prediction 身份快照、旧值、两套新 outcome、benchmark/alpha、所有 anchor/actual date/lag、source/adjusted/status/reason 和 row hash。缺数据字段允许 NULL。为保留历史审计，不对 prediction 建强制外键。

表只允许 append-only `INSERT`；禁止 `UPDATE`、`UPSERT` 覆盖或跨 run identity 唯一约束。三张 shadow 表均安装 `BEFORE UPDATE/DELETE` 触发器，并以 `OUTCOME_SHADOW_IMMUTABLE` fail-closed。

## 6. 状态

- `computed_aligned`
- `computed_calendar_mismatch`
- `missing_stock_entry`
- `missing_stock_target`
- `missing_benchmark_entry`
- `missing_benchmark_target`
- `source_impure`
- `invalid_prediction`
- `contract_error`

所有 frozen 事件都必须生成结果行；缺数据不能从分母消失。

## 7. Phase 1 命令合同

- `outcome-shadow-build --source-db ... --candidate-db ... --benchmark-snapshot ... --as-of-date ...`
- `outcome-shadow-build --dry-run` 必须以 `mode=ro` 打开源库，不能创建 schema 或文件，并拒绝会被忽略的 `--candidate-db`/`--benchmark-snapshot`。
- build 先通过 SQLite backup 生成一致候选快照，再从候选读取 cohort 与股票行情；不得在源库“读取后再备份”。benchmark snapshot 必须严格校验 schema/source/symbol/fetched_at/rows/date/close/duplicate/coverage，异常时删除候选并 fail-closed。
- `outcome-shadow-report --candidate-db ... --run-id ... --output ...json` 只读数据库；仅接受 `.json` 输出并同时生成同名 `.md`，文件进入 ignored artifacts/backups。
- 命令必须显式提供路径；不得隐式写默认生产库。

## 8. 验收门禁

1. frozen event 数恰为 1,776，重复为0。
2. 当前输入 hash 与基线一致时，computed=1,767、missing=9；若 hash 漂移则停止并重新只读审查基线，不放宽阈值。
3. 股票 source/adjusted 纯净；benchmark 仅 `tushare.index_daily`；`source_impure=0`。
4. 日期测试覆盖交易日、非交易日、恰好10日、超过10日、anchor 后数据不得选中及股票/指数 entry 错位。
5. dry-run 前后源 DB 文件 hash、schema、prediction count 不变。
6. 相同 manifest 得到相同 run ID、observation hash 和 row hash；已有 run 不覆盖。
7. 候选 `PRAGMA quick_check=ok`，`predictions` 1,999 行且 v2 保护 hash 为 `05e8d5569edf4948c3ef3828ce43e5284a0e1a218a32b03a233d41e38070f1ce`。保护字段排除描述性 `name`；旧 `baseline.json` 被 `baseline-v2.json` 取代但保留审计。
8. 报告分开列出可比/不可比，并按 framework/window/date/code/score quantile/pre-post-fix 分层；明确同日相关性和非因果归因限制。
9. 全量 pytest、Ruff、format、mypy、pip check、shell syntax 和 `git diff --check` 通过。
10. 独立只读复审无 P0/P1 后，Phase 1 才算完成。

## 9. 停止与回滚

Phase 1 只写隔离候选及 ignored artifacts；任何门禁失败立即停止并删除/保留候选作为失败证据，生产无回滚动作。禁止把 Phase 1 结果导入生产。Phase 2 必须另行获得用户确认，并在无活动 writer、在线备份、`BEGIN IMMEDIATE` 和单事务导入条件下执行。
