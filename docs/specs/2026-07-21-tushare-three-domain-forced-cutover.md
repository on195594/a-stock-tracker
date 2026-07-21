# TuShare 估值、财务、分红三域生产强切合同

创建时间：2026-07-21
状态：approved for implementation
授权：用户明确允许备份、安装 `a-stock-lib==0.4.1`、写生产 shadow 与 `stock_fundamentals`、修改 cron；失败按本合同回滚。

## 1. 目标

把 tracker 的估值、市值、通用财务指标和分红事实同时切换到 TuShare 主源，保持评分权重、历史预测、持仓与定性评分逻辑不变。

## 2. 强制边界

- 先完整采集到独立 `tushare-primary.db`，再执行一次原子 materialization；禁止边采边改 `tracker.db`。
- 不更新既有 `predictions.total_score/weights_hash/report_period`，不重算历史预测。
- 三域各有独立 `off|on` 环境开关，默认 `off`；只有 readiness PASS 才允许 `on` 写入。
- 任一股票的必需字段或来源审计失败，整批 `stock_fundamentals` 写入 rollback。
- 生产代码不使用 `shell=True`，不硬编码 Token，不打印 `.env` 或凭据。
- `a-stock-lib` 必须实际导入版本 `0.4.0`。

## 3. 数据映射

### 3.1 估值/市值

- `pe_ttm` ← 最新 `daily_basic.pe_ttm`；亏损公司允许 `NULL`，标记合法空值。
- `pb` ← 最新 `daily_basic.pb`。
- `float_to_total_ratio` ← `circ_mv / total_mv * 100`，无效分母返回 `NULL`。
- `dividend_yield` ← 最新 `daily_basic.dv_ttm`，与分红事件分开。
- `pb_hist_monthly` ← 最近十年日度 PB 的月末有效样本。
- `pb_percentile_10y` ← `a_stock_lib.valuation.compute_valuation_percentile(..., field="pb")`；仅 `FULL_10Y` 写入数值。上市历史不足时显式写 `NULL`，不得沿用旧源分位。
- 同时保存 `valuation_window_start/end/valid_months/coverage_status/source_as_of`。

### 3.2 财务

只选择公告日不晚于 materialization as-of date 的记录；`fina_indicator` 的有效公告日使用 `ann_date`。年度均值只取 `end_date` 以 `1231` 结尾的最近三个已披露年度，不混入单季指标。

- `roe_3y_avg` ← 最近三个年度 `roe_waa` 均值。
- `roe_latest` ← 最新已披露年度 `roe_waa`。
- `net_profit_growth` ← 最近三个年度 `netprofit_yoy` 均值。
- `debt_ratio` ← 最新有效报告期 `debt_to_assets`。
- `gross_margin` ← 最新有效报告期 `grossprofit_margin`；金融行业允许 `NULL`。
- `bps` ← 最新有效报告期 `bps`。
- `report_period` ← 实际选中记录的 `end_date`。
- 保存 `financial_effective_ann_date/source_as_of`。

### 3.3 分红

TuShare 官方文档 `doc_id=103`：

- `cash_div_tax`：每股税前分红；作为 `dps` 事实口径。
- `cash_div`：每股税后分红，不替代税前口径。
- 仅使用 `div_proc == "实施"` 且 `ex_date` 不晚于 as-of date 的记录。
- `dps` 为最近一次已实施事件的 `cash_div_tax`。
- `dividend_yield` 仍使用 `daily_basic.dv_ttm`，不从事件重复估算。
- 保存 `dividend_ex_date/dividend_ann_date/dividend_source_as_of`。

## 4. 生产文件与 CLI

- Shadow DB：`data/tushare-primary.db`
- Artifact：`artifacts/tushare-ingestion/`
- 批量采集：必须显式 `--db-path`、`--artifact-root`、`--as-of-date`、`--execute`。
- Materialization：默认 preview；只有显式 `--execute` 才可写目标 DB。
- Readiness scopes：`valuation|financial|dividend|all`。

## 5. Readiness

- watchlist 必须 35/35 出现在三域结果中；合法业务空值单独分类。
- PB、close、total_mv、circ_mv 不得伪造为 0。
- PE 空值只能作为合法亏损状态，不得当作抓取失败。
- 财务记录必须有报告期和有效公告日。
- 分红无实施事件允许业务空值，但必须有明确状态；抓取失败不等同于无分红。
- 所有记录必须有 source、source_as_of、observation event 和 completed ingestion run。

## 6. 生产事务

1. 重新备份 `tracker.db`、crontab、Git SHA、requirements 和运行时版本。
2. 在 shadow DB 完成采集和 readiness。
3. preview 输出 35 股字段差异，不写生产库。
4. `BEGIN IMMEDIATE`，读取并合并 35 行 `stock_fundamentals.data`。
5. 写入来源/报告期/覆盖 metadata；任何异常 `ROLLBACK`。
6. 提交后重新读取 35 行，并运行评分 smoke。

## 7. Cron

- 工作日 17:15：三域增量采集、readiness、materialization。
- 工作日 17:30：`pipeline.py daily`。
- 工作日 17:45：定性评分生产验收。
- 工作日 18:00：`outcome-update`。
- 周六 10:00：财务/分红全量刷新与 materialization。

## 8. 回滚

- 三域开关全部设为 `off`，旧 AKShare 路径恢复为读主源。
- 恢复原 crontab。
- 必要时恢复 `tracker.db` 备份和 `a-stock-lib==0.2.0`。
- 不删除 shadow DB 或 artifact，以保留审计证据。

## 9. 验收

- 新增单元测试先 RED 后 GREEN；默认测试不访问网络或生产 DB。
- tracker 默认环境和 `a-stock-lib==0.4.0` 环境全量测试通过。
- Ruff、format、mypy、结构测试、`git diff --check` 通过。
- 独立代码审查无 P0/P1。
- 生产 materialization 后 `tracker.db PRAGMA quick_check == ok`。
- 生产运行时实际导入 `a-stock-lib==0.4.0`。
