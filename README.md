# a-stock-tracker

**当前目标：可靠采集通用 TuShare 数据，并保全历史审计记录。** 不是继续开发的选股评分引擎。

## 产品与历史边界

Framework A 已于 **2026-09-21 `CLOSED_UNPROVEN`** 结案：未在可接受的时间和证据预算内证明投资价值，不等于统计上证明其必然无效。不再收样、等待 30/60/90 日窗口、扩池、调权或进入阶段二/三。

- 本仓：数据可靠性、完整性、安全修复。
- `a-stock-lib`：共享确定性计算与 Provider。
- `a-stock-agent-skills`：首次研究、持仓监控及文本 QA；独立状态与授权。
- `a-stock-screen`：同业发现、个人研究记录与事实变化。当前产品合同及待办只在其 `docs/FLET_DESIGN.md` 维护；本仓仅保留 [peer-screen-v1 Spec](docs/specs/2026-09-22-peer-screen-spec.md) 的原规则合同，不再维护第二份路线图。

退休的 `cli.py`、`scoring.py`、`signals/`、`reporting/`、`qualitative/`、`pipeline.py` 及旧 QFQ 文件缓存由 Git 保留，活动目录不留备用执行链。manifest、权重、数据库行、报告与原始审计证据保持不动；不回填或重算，不恢复 Framework A daily、策略报告或 Telegram 观察名单。历史收益未计费用、滑点、成交限制、仓位及退出，不能称为可执行回测。超出已批准个人同业工具范围的新投资研究仍须另立明确假设、可达样本、硬截止日和样本外验证的项目。

## 活动数据链

```text
TuShare 财务/分红、估值、SSE 日历、QFQ 行情、沪深300全收益
  → 来源审计/readiness → SQLite 通用数据
```

`cron-setup.sh` 的保留配置为周六 10:00 财务/分红、工作日 16:00 日历/QFQ、17:15 估值。运行状态需现场核查；文档不是部署证据。采集失败保留上一份有效证据并告警，不预填未来日历、不伪造 readiness、不静默改变来源口径。凭据仅从环境或已忽略的本地配置提供，禁止提交。

```bash
python3 -m scripts.fetch_qfq_daily_bars_tushare
python3 -m scripts.check_market_data_readiness --scope cron
python3 -m scripts.run_tushare_primary_production_cycle daily
python3 -m scripts.run_tushare_primary_production_cycle weekly
```

## 开发与验证

先读 [AGENTS.md](AGENTS.md) 和 [模块边界](docs/architecture.md)，数据语义见 `docs/data-source-registry.yaml`，操作见 `docs/runbooks/`。业务代码在 `a_stock_tracker/`，配置在 `config/`，运维入口在 `scripts/`；`data/`、`logs/`、`artifacts/` 为忽略的运行产物，不能顺手删除。

```bash
.venv/bin/python -m pytest tests/test_project_structure.py -q
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
git diff --check
```

纯文档修改至少 `git diff --check`。结案依据见 [evolution-roadmap](docs/evolution-roadmap.md)，原始变更/审查记录见 [CHANGELOG](CHANGELOG.md) 和 `docs/reviews/`。退休源码、旧路线图和重复状态页通过 `git show 7468568:<原路径>` 恢复，不再作为当前开发入口。
