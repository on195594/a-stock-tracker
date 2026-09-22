# a-stock-tracker 开发入口

## 目标

Framework A 已于 2026-09-21 以 `CLOSED_UNPROVEN` 结案。仓库内代码当前只维护通用 TuShare 数据采集、历史数据完整性和安全修复；不再验证或恢复 Framework A/L3 投资链路。2026-09-22 获准的个人同业选股工具由本项目正式文档定义，但实现在相邻独立 Git 仓库 `a-stock-screen/`。

## 当前生产边界

- 周六 10:00 采集并物化财务/分红；
- 工作日 16:00 刷新 SSE 交易日历、QFQ 个股行情和沪深300全收益；
- 工作日 17:15 采集并物化估值；
- 不运行 Framework A daily，不生成自动策略报告，不发送 Telegram 观察名单；
- SQLite 历史 prediction、manifest、报告和审计证据保持不动，不回填、不改写、不据此恢复投资链路。

## 架构

- `a_stock_tracker/data/`：活动的数据库、provider、采集、readiness、物化；
- `a_stock_tracker/signals/`、`reporting/`、`qualitative/`：结案审计保留的历史实现；
- `a_stock_tracker/cli.py`、`pipeline.py`：已退休 Framework A 的历史编排与薄兼容启动器，不受 cron 调用。

## 约束

- 不修改历史核心评分字段；
- 不恢复 Framework A daily、自动策略报告、Telegram 观察名单或 S2.1 cohort 收样；
- 不把历史评分、风险门禁或报告称为已验证买点；
- 不修改活动权重、阈值或 manifest 以续期已结案实验；
- 不新增手工备用入口；
- 不增加治理、reviewer、seal、authorization 或 migration；
- 不在测试中真实访问网络；
- 本仓库代码变更仅限通用数据可靠性、安全性和历史审计维护；个人同业选股工具按 `docs/plans/2026-09-22-personal-stock-selection-roadmap.md` 与 `docs/specs/2026-09-22-peer-screen-spec.md` 在相邻目录实现。超出该范围的新投资研究必须另立项目。

## 验证

```bash
.venv/bin/python -m pytest tests/test_project_structure.py -q
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
git diff --check
```
