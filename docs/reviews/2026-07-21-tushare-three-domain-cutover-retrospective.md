# TuShare 估值、财务、分红三域生产强切复盘

日期：2026-07-21
状态：completed
范围：`a-stock-tracker` 的估值/市值、通用财务指标、分红事实
关联合同：`docs/specs/2026-07-21-tushare-three-domain-forced-cutover.md`

## 结论

本次任务已完成生产强切：35 股的估值、通用财务指标与分红事实同时由 TuShare shadow 物化到 `tracker.db.stock_fundamentals`。评分权重、watchlist、持仓、定性评分逻辑和历史 `predictions` 均未改写。

最终生产事实：

- 运行时实际导入 `a-stock-lib==0.4.1`；`pip check` 无破损依赖；
- shadow DB 为 `data/tushare-primary.db`，原始响应位于 `artifacts/tushare-ingestion/`；
- 最终真实 daily cycle 为 `run_id=215`、`row_count=5525`、`source_as_of=2026-07-21`、`changed_count=35`；
- shadow 共 215 个 completed runs、0 个 failed runs，`PRAGMA quick_check=ok`；
- 生产 `tracker.db PRAGMA quick_check=ok`，35/35 来源审计通过，评分 smoke 错误 0；
- 估值覆盖为 `FULL_10Y=28`、`SINCE_LISTING=5`、`INSUFFICIENT_HISTORY=2`；历史不足股票未沿用旧源十年分位；
- `predictions` 保持 1,894 行，切换前后 canonical SHA-256 均为 `fc60e1f404a3303af2692625605fb8b878c1abe73661ac8e951f33111f6979e5`；
- 最终结果文件为 `backups/tushare-cutover-20260721-145652/final-cutover-result.json`。

## 实施路径

1. 冻结三域合同和字段口径，独立核对 TuShare 官方字段。
2. 在隔离 worktree 中实现 shadow cache、ingestion、readiness、materialization 和三域 feature flags。
3. 使用 `a-stock-lib==0.4.1` wheel 做真实 35 股有界采集；不接触生产 DB。
4. 对 35 股执行 preview、来源审计、评分 smoke、预测表 hash 对账和独立代码审查。
5. 备份 `tracker.db`、crontab、Git/requirements/runtime 版本与 0.2.0 回滚 wheel。
6. 将 shadow DB 与 210 份初始 artifact 原子部署到生产路径。
7. 用户要求取消 17:20 等待后立即执行：重新采集当日估值、三域 `all` readiness、单事务物化、cron 安装与终验。
8. 修复生产 smoke 暴露的 cron 接线问题后，真实运行 `python -m scripts.run_tushare_primary_production_cycle daily`，最终退出码 0。

## 做得正确的部分

### 数据写入边界

- 先完整写 shadow，再物化生产；没有边采边改 `tracker.db`。
- materialization 使用 `BEGIN IMMEDIATE`，35 股任一失败整批回滚。
- backfill 只作为当前生产 materialization，不冒充历史评分时点可见数据。
- `predictions` 用行数和 canonical hash 双重证明未改写。

### 回滚不是纸面设计

生产切换过程中两次触发自动回滚，均返回 `rollback_errors=[]`：

1. cron 周度命令超过 crontab 单行限制；
2. 一次性验收器仍检查旧的 materialization 命令名称。

两次均恢复：

- 切换前 `tracker.db`；
- 原 crontab；
- `a-stock-lib==0.2.0`；
- 未变的 `predictions`。

最终修复后重新安装 0.4.1 并完成切换。另用隔离 PATH 下的假 `crontab` 验证 `cron-setup.sh --rollback <snapshot>` 会消费指定快照，不触碰生产 crontab。

### 验证层次

- 切换前全仓：`1006 passed`，Ruff lint/format、mypy、compileall、`git diff --check` 通过；
- cron cycle 修复：聚焦测试 26 项、6 项、3 项分别通过，mypy 152 source files 无错误；
- 生产数据：readiness、SQLite quick check、来源字段、估值覆盖、评分 smoke、预测 hash；
- 生产接线：不只调用底层命令，还真实运行 cron 对应 module entrypoint 并检查退出码。

## 暴露的问题与根因

### 1. 部署提交依赖链不完整

**现象：** 最初只 cherry-pick 强切提交，生产缺少 `tushare_primary_cache.py` 和 `tushare_primary_ingestion.py`。
**根因：** 按“最新功能提交”部署，没有先比较 `production..feature` 的完整祖先链。
**修复：** 补齐 `4faf392 → 01c5449 → 648a65b` 前置提交后重新执行测试。
**防复发：** 跨 worktree 部署前必须先列出并审阅完整提交差集，不能把单个顶层 commit 当成自包含发布单元。

### 2. cron 命令过长

**现象：** crontab 拒绝周度命令。
**根因：** 将采集、readiness 和 materialization 全部内联在一条 shell 命令中。
**修复：** 新增有界 production cycle，cron 只调用 `daily|weekly` 短入口。
**防复发：** cron 负责编排时间和告警，不承载长业务流程；业务步骤应在受测试的 Python orchestration 中。

### 3. 文件路径入口无法导入项目包

**现象：** `.venv/bin/python scripts/run_tushare_primary_production_cycle.py` 返回 `ModuleNotFoundError`。
**根因：** 文件路径执行时 `sys.path[0]` 是 `scripts/`，项目根不在导入路径；生产模块又正确地禁止修改 `sys.path`。
**修复：** cron 改为 `.venv/bin/python -m scripts.run_tushare_primary_production_cycle`。
**防复发：** 新运维入口的 smoke 必须使用 cron 中的精确命令，而不是等价但不同的启动方式。

### 4. 写入成功但进程仍失败

**现象：** daily cycle 已完成采集和物化，最终 JSON 输出因 `Path` 不可序列化而退出 1。
**根因：** 测试覆盖了业务编排，没有覆盖 CLI 返回对象的最终序列化。
**修复：** 输出使用 `default=str`，增加 `Path` 回归测试。
**防复发：** 成功条件必须同时包含业务状态、持久化结果和进程退出码；不能以“数据已经写入”替代完整 smoke。

### 5. 验收器与实现入口漂移

**现象：** cron 已正确安装 cycle，但一次性验收器仍要求出现旧的 `materialize_tushare_primary` 子串，产生假失败。
**根因：** 接线重构后未同步更新发布验收条件。
**修复：** 验收器改查实际 cycle module。
**防复发：** 发布入口、cron 内容和验收断言必须共用同一控制面事实，入口变更必须同时更新验收测试。

### 6. 两套 readiness 语义混杂

旧 `check_market_data_readiness.py` 检查 daily/index/calendar/close 行情恢复，最新 probe 已 stale；本次 `check_tushare_primary_readiness.py` 检查估值/财务/分红 shadow 完整性。两者用途不同，不能互相替代。

本次处理：

- 新 TuShare primary daily 独立安装，以三域 readiness 为门禁；
- 旧评分任务在切换前已存在，因此仅保留并后移，没有借 stale probe 新启用额外行情能力；
- 旧 market-data probe 仍需刷新，但不否定本次三域 35/35 的生产证据。

## 未改变的边界

- Framework B 继续 report-only；
- 不修改 `config/weights.json`；
- 不重算或改写历史预测；
- 不修改 watchlist、持仓或交易动作；
- 行情 price/outcome/index provider 与本次三域基本面/估值主源保持独立治理；
- 公告正文、新闻、银行专属指标仍不由本次 TuShare 三域替代。

## 后续动作

1. 观察下一次 17:15 daily cycle 和周六 10:00 weekly cycle 的自然日志与告警。
2. 刷新旧 market-data capability probe，消除 stale 控制面告警；不得伪造 PASS。
3. 后续 consumer 迁移按 `tracker → research → monitor/QA → east-cable` 分项目执行，不因 tracker 强切自动宣称下游已完成。
4. 若字段口径、cycle 时序或 feature flag 发生变化，同时更新 registry、spec、runbook 和验收测试。
