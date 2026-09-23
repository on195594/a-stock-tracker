# 项目状态

**更新时间：** 2026-09-23
**Framework A 生命周期：** `CLOSED_UNPROVEN`
**投资状态：** Framework A 未证明有效；阶段二、阶段三均不启动
**活动范围：** 通用 TuShare 数据链，以及相邻独立仓库中的个人同业选股工具

## 当前结论

Framework A 已于 2026-09-21 结案。原 2026-08-12 实验存在数据和协议缺陷，successor 实验在截止时没有形成足以证明投资价值的成熟样本。`CLOSED_UNPROVEN` 表示“不继续投入”，不等于统计上证明其必然无效。

Framework A daily 评分、30/60/90 日策略报告、Telegram 观察名单、S2.1 cohort 收样、阶段二扩池和阶段三生产决策均已停止。历史收益未计费用、滑点、成交限制、仓位和退出，也不能作为可执行回测。

## 本仓库活动链

本仓库只继续维护以下通用数据任务：

- 周六 10:00：财务和分红采集、readiness 与物化；
- 工作日 16:00：SSE 交易日历证据、QFQ 个股日线和沪深 300 全收益；
- 工作日 17:15：估值采集、readiness 与物化。

`cron-setup.sh` 只管理这三类任务，并清理本项目旧的 Framework A、Framework B、weekly PM、qualitative acceptance 和 outcome-update 规则。数据失败时保留上一份有效证据并告警；不得预填未来交易日、伪造 readiness、用 fallback 改变来源口径或改写历史记录。

活动命令、依赖和恢复步骤分别以 `README.md`、`requirements.txt`、`docs/data-source-registry.yaml` 及 `docs/runbooks/` 为准。默认测试只覆盖活动数据链、来源审计、安全边界和项目结构。

## 个人同业选股工具

正式范围由 `plans/2026-09-22-personal-stock-selection-roadmap.md` 和 `specs/2026-09-22-peer-screen-spec.md` 定义；实现在相邻独立公开仓库 `a-stock-screen/`（`on195594/a-stock-screen`），不写本仓库的历史实验数据。

同业发现、前三名候选审查和显式快照对比均已实现。本人已接受“国投电力继续研究，甘肃能源、湖北能源暂不研究”作为研究优先级。2026-09-23 补证确认国投电力 2025 年报为标准无保留意见、分红预案为每股 0.5081 元；因尚无权益分派实施公告及可被 runtime 接受的完整 P0 证据，正式评分、择时和动作继续保持 `not_formed`。

下一次只在出现已实施 DPS 或可验 P0 新证据时复核。该工具不恢复 Framework A，不证明投资 alpha，也不授权交易。

## 历史保留边界

以下内容只为审计保留，不属于活动生产链：

- `a_stock_tracker/cli.py`、`scoring.py`、`signals/`、`reporting/`、`qualitative/` 与薄启动器 `pipeline.py`；
- `config/experiment_manifest.json`、数据库历史行和既有报告；
- `docs/evolution-roadmap.md`、`CHANGELOG.md` 与 `docs/reviews/` 中的结案和审查记录。

不回填或重算历史核心评分，不修改 manifest 延续实验，不恢复退休入口或定时任务。已删除的旧设计、计划和 Spec 仅通过 Git 历史追溯。

超出当前个人同业选股路线图的新研究必须另立项目，重新提出明确假设、可达样本、硬截止日和独立样本外验证。
