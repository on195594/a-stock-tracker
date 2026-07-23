# TuShare-only 行情强切与 BaoStock 数据清理合同

创建时间：2026-07-23
状态：approved for implementation
授权：用户明确选择彻底清除 BaoStock 数据痕迹、禁用 fallback，并要求缺口只用 TuShare API 补齐；既有 outcome 本轮仅评估，不直接重写。

## 1. 目标

将 tracker 活动行情路径强切为 TuShare-only，清除生产 `tracker.db` 中 BaoStock 行情和审计记录，并在删除前使用 TuShare `pro_bar(adj="qfq")` 补齐 35 股 QFQ 历史窗口。

## 2. 范围

- `a_stock_tracker/data/market_data.py` 默认与 backfill provider 只允许 TuShare；缺 token 或 TuShare 失败时 fail-closed。
- 移除 `MARKET_DATA_ALLOW_BAOSTOCK_ONLY` tracker 路径。
- `a_stock_tracker/signals/l3_v2_pipeline.py` 不再硬编码 BaoStock 来源，QFQ 窗口必须全部来自 `tushare.pro_bar.qfq`。
- 清除生产 `daily_bars` 中所有非 TuShare 行，包括 BaoStock QFQ 和旧 AKShare legacy 行。
- 清除 `market_data_audit` 中 source/fallback 字段包含 BaoStock 的历史行。
- 使用官方 TuShare `pro_bar`、`adj="qfq"` 补齐生产 `adjusted='qfq'` 分区。

## 3. 非目标

- 不改写任何 `predictions` 行，包括既有 outcome、benchmark、alpha、评分、定性快照和信号快照。
- 不删除 a-stock-lib 内的 BaoStock provider 实现或卸载依赖；本项目只停止实例化和消费。
- 不改变评分权重、watchlist、cron 时序或定性评分。
- 不把本次来源统一冒充为复权收益 P0 已修复；outcome 重算另行评估。

## 4. 数据安全合同

1. 使用 SQLite backup API 创建时间戳生产备份，并验证 `PRAGMA quick_check=ok`。
2. 记录切换前完整 `predictions` canonical SHA-256、行数和生产 DB SHA-256。
3. 只在生产 DB 副本执行网络补齐和清理；生产库在全部门禁通过前保持不变。
4. 逐股通过 TuShare API 获取 `2025-12-24` 至切换日的 QFQ 日线。
5. 逐股要求：至少 120 行、无重复日期、OHLC/close 有限、volume 非负、最新日期等于 TuShare 未复权分区最新日期。
6. QFQ 日期集合必须覆盖同区间 TuShare 未复权日线日期集合；不得填充或混合旧源。
7. 在副本单事务 upsert TuShare QFQ、删除非 TuShare `daily_bars`、删除 BaoStock `market_data_audit`；任一异常 rollback。
8. 切换前确认无 daily/outcome/QFQ writer；通过同目录临时文件原子替换生产 DB。
9. 切换后若任一验收失败，立即从已验证备份原子恢复。

## 5. TDD 合同

- RED：默认 provider 在 token 存在时不是直接 `TushareMarketDataProvider`；backfill 在 BaoStock-only 环境仍会实例化 BaoStock；L3 接受/伪装 BaoStock QFQ。
- GREEN：默认与 backfill 均只返回 TuShare provider；无 token 均返回 disabled；L3 对非 TuShare QFQ fail-closed，TuShare QFQ 保持 pass-strong。
- 保留 a-stock-lib provider 自身的单元测试，但删除 tracker 对生产 fallback 的行为期待。

## 6. 验收

- 生产 `daily_bars`：所有 source 均匹配 TuShare；不存在 BaoStock 或 AKShare 行。
- 生产 `market_data_audit`：不存在任何 BaoStock 文本命中。
- 35/35 股 QFQ 每股至少 120 行，日期覆盖与 TuShare unadjusted 对齐，最新交易日一致。
- `compute_l3_v2_from_daily_bars` 对 35 股均不因来源或窗口不足降级。
- `predictions` 行数与完整 canonical SHA-256 切换前后完全一致。
- `PRAGMA quick_check=ok`。
- 定向测试、全量 pytest、Ruff lint/format、mypy、结构测试和 `git diff --check` 全部通过。
- 独立只读审查无 P0/P1。

## 7. 回滚

- 保留切换前数据库备份、SHA-256、baseline JSON 和隔离副本。
- 数据切换失败：生产库不替换。
- 切换后验证失败：确认无 writer，使用备份临时副本校验后原子恢复，再复验 DB hash、`quick_check` 和 predictions hash。
- 代码回滚：恢复本次受控 diff；不得通过重新启用 BaoStock 来掩盖 TuShare 缺口。

## 8. Outcome 评估边界

本次只统计 BaoStock 审计曾覆盖的 purpose/code/run_date，并评估既有 outcome 的来源可追溯性与复权口径风险。由于现有 audit 不绑定 prediction id/window，且既有收益还存在未复权 P0，禁止在本任务中凭推断批量改写 outcome；后续应按独立 shadow 重算 spec 处理。
