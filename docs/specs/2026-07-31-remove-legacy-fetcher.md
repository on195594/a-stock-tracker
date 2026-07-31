# 移除 legacy AKShare fetcher 并收口 TuShare 只读消费

## Goal

删除 `a_stock_tracker/data/fetcher.py` 及其活动调用面，确保生产基本面、估值和行情只由既有 TuShare production cycles 写入；`east-cable-monitor` 只读 `tracker.db`，不得在监控运行中刷新或修改 tracker 缓存。

## Non-goals

- 不修改 TuShare ingestion、readiness、materialization、评分或信号算法。
- 不修改 `tracker.db` schema、现有数据、cron 时间或 Telegram 配置。
- 不调整估值阈值、PB 容差或告警状态机。
- 不在本删除任务中修复 TuShare `fina_indicator.bps` 与 `daily_basic.pb` 的送转口径差异；监控继续 fail-closed，并把该问题作为独立数据契约修复处理。
- 不清理仅用于历史追溯的 changelog/旧设计记录；当前 README、运行契约和活动测试必须更新。

## Requirements

### R1 — 删除 legacy fetcher

- 删除 `a_stock_tracker/data/fetcher.py`。
- 删除仅供该 fetcher 使用的 `a_stock_tracker/data/akshare_provider.py`，并从运行依赖移除 `akshare`。
- 删除其专属测试 `tests/test_fetcher.py`。
- 活动源码和当前运行文档不得再引用 `a_stock_tracker.data.fetcher`、`data/fetcher.py` 或 AKShare `init/weekly` 刷新路径。

### R2 — 收口 tracker CLI

- 删除 `_run_fetcher_process`、`_refresh_fundamentals`、`cmd_init`、`cmd_weekly` 及其专属测试。
- `pipeline.py` 当前命令面仅保留仍受支持的 `daily` 和 `remove`。
- README 的常用命令只指向 TuShare production cycles 和当前 daily 链路。

### R3 — east-cable 只读消费

- `fetcher_adapter.py fetch 603606` 不得启动上游 subprocess，不得调用任何外部 API，不得写 `tracker.db`。
- adapter 使用 SQLite `mode=ro` 从同一个 `UPSTREAM_TRACKER_DB` 读取 `stock_fundamentals.data`、当日价格和技术字段。
- 保持现有价格优先级、PB 重算、冲突 fail-closed 和 JSON 输出契约。
- 删除 `UPSTREAM_FETCHER_PATH`、`UPSTREAM_FETCHER_PYTHONPATH` 配置和文档。

### R4 — 故障语义

- DB 不存在、不可读、schema 缺失、JSON 非法或股票缓存缺失时，adapter 仍输出结构化不可用/冲突信息或以现有 monitor 可识别的非零状态失败；不得回退到 legacy/API 刷新。
- 只读 smoke 前后 `tracker.db` 的大小、mtime 和 SHA-256 必须不变。

## Acceptance criteria

1. `test ! -e a_stock_tracker/data/fetcher.py` 且 `test ! -e a_stock_tracker/data/akshare_provider.py`，活动 Python 源码不再导入 AKShare。
2. 活动源码、当前 README、Fetcher Contract 和测试中无 legacy fetcher 路径；允许历史 `docs/impl-plan.md`、changelog 等保留历史事实。
3. tracker 全套 pytest、Ruff check/format、mypy、结构测试和 `git diff --check` 通过。
4. east-cable 全套 pytest、语法检查和 `git diff --check` 通过。
5. 使用复制的生产 `tracker.db` 运行 adapter，得到合法 JSON；原生产 DB 哈希、mtime、大小前后完全一致。
6. 定向回归证明 adapter 不调用 `subprocess.run`，并从只读 SQLite 取到 fundamentals。

## Rollback and stop conditions

- 两仓分别以独立分支提交；生产切换前记录提交前 SHA，删除文件由 Git 历史提供回滚，不保留额外活动副本。
- 任一全套测试失败、adapter 输出契约变化、生产 DB 被写、或活动 cron 仍引用 legacy 命令时停止切换。
- 回滚方式：两仓分别 fast-forward/checkout 回切换前 SHA，或对切换提交执行 revert；本任务不改 cron，数据库仅在事故恢复中按既有 TuShare shadow 重物化。

## Execution order

1. 先在隔离 worktree 修改 tracker，删除 legacy 模块和 CLI 面。
2. 在独立 east-cable worktree 以测试先行改为 SQLite 只读读取。
3. 分别跑全套验证和独立只读 diff review。
4. 验证通过后，备份活动文件并将两个提交切入当前 checkout；再次运行活动 checkout 测试与只读 smoke。
