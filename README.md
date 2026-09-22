# a-stock-tracker

Framework A 已结案的 A 股研究项目；历史代码和数据保留供审计，通用 TuShare 数据采集继续运行，并维护个人同业选股工具的正式文档。

## 当前状态

**Framework A：`CLOSED_UNPROVEN`（2026-09-21）**。它未在可接受的时间与证据预算内证明可复验投资价值。原 2026-08-12 实验因数据和协议缺陷失效，S2.1 successor 实验以“未证明”结案；不进入阶段二或阶段三，不再用于买入判断、候选扩张或实盘授权。

结案后保留的自动链：

```text
TuShare 基本面、估值与 QFQ 行情
  → SQLite 通用数据与历史审计记录
```

Framework A 评分、30/60/90 日策略报告和 Telegram 观察名单的定时任务已停止。相关代码、`config/experiment_manifest.json`、数据库历史行和既有报告仅作为历史证据保留，不回填、不改写，也不继续等待新的投资裁决。

16:00 QFQ 数据任务继续刷新有来源的 SSE 本地交易日历、个股日线与沪深300全收益指数；17:15 TuShare production cycle 继续物化估值。二者只维护通用数据，不产生新的 Framework A 评分或投资结论。

**结论边界：** `CLOSED_UNPROVEN` 表示项目未证明 Framework A 值得继续，不等于统计上证明其必然无效。历史收益从评分日收盘起算，未纳入费用、滑点、成交限制、仓位或退出，不能作为可执行策略回测。

## 当前个人工具方向

2026-09-22 起，本项目正式维护个人同业选股工具的[路线图](docs/plans/2026-09-22-personal-stock-selection-roadmap.md)与[实施 Spec](docs/specs/2026-09-22-peer-screen-spec.md)。该工具用于当前资料下的同业发现与研究排序，不恢复 Framework A，也不证明收益有效性。相邻独立公开仓库 [`a-stock-screen/`](https://github.com/on195594/a-stock-screen) 已完成真实池外同业发现、前三名候选审查和显式指定旧快照的变化跟踪；同日真实对照未发现变化。候选研究因监管门与部分股息输入不完整而保持 fail-closed；本人已确认“国投电力继续研究，甘肃能源、湖北能源暂不研究”的研究优先级，但这不构成买入结论。

## 常用命令

```bash
python3 scripts/fetch_qfq_daily_bars_tushare.py
python3 scripts/check_market_data_readiness.py --scope cron
python3 -m scripts.run_tushare_primary_production_cycle daily
python3 -m scripts.run_tushare_primary_production_cycle weekly
```

历史策略报告位于被 Git 忽略的 `artifacts/reports/accuracy-report.txt`。SQLite 继续作为通用数据和历史记录来源；不再自动生成 Framework A 评分、报告或 Telegram 观察名单。

## 自动任务

`cron-setup.sh` 管理以下任务：

- 周六 10:00：财务与分红刷新；
- 工作日 16:00：TuShare SSE 日历证据、QFQ 日线与沪深300全收益；
- 工作日 17:15：TuShare 估值物化；

脚本会清理已退休的 Framework A daily、Framework B、weekly PM、qualitative acceptance 和 legacy outcome-update 旧 cron 规则。

## 目录

- `a_stock_tracker/data/`：活动的 SQLite、行情、TuShare ingestion/readiness/materialization；
- `a_stock_tracker/signals/`：历史 L3 v2 风险信号实现（不再运行）；
- `a_stock_tracker/reporting/`：历史 Telegram 与策略报告实现（不再运行）；
- `a_stock_tracker/qualitative/`：历史 qualitative 只读选择实现（不再运行）；
- `scripts/`：通用数据自动运维、采集和诊断入口；
- `tests/`：活动数据链与安全边界测试；
- `docs/`：架构、状态、个人同业选股工具正式文档和历史记录。

## 配置

保留数据任务只需要从环境提供 TuShare 凭据：

```dotenv
TUSHARE_TOKEN=你的_TuShare_Token
```

## 验证

```bash
.venv/bin/python -m pytest tests/test_project_structure.py -q
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
git diff --check
```

Framework A 结案依据见 `docs/evolution-roadmap.md`；当前状态与运行边界见 `docs/project-status.md`；活动事项见 `TODOS.md`。
