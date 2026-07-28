# 项目状态

**更新时间：** 2026-07-28
**当前阶段：** 三阶段路线的阶段一
**当前目标：** 证明 Framework A 与 L3 v2 是否具有可复验投资价值

## 生产基线

| 能力 | 状态 |
|---|---|
| Framework A | 自动 daily 写入；live DB 截至 2026-07-27 为 1,996 条 |
| Framework B | 活动代码、入口和 cron 已删除；历史 73 条 prediction 与 15 条 cohort 数据保持只读 |
| TuShare 基本面/估值/分红 | 35/35 生产物化 |
| QFQ 日线与全收益基准 | 35/35 个股；16:00 自动任务已接入 `H00300`，live DB 尚待下一次成功调度首次写入 |
| L3 v2 | 385 条：375 pass、10 reject；strong v2 198 pass、0 reject |
| L3 v1 | 计算、写入和回填已删除；历史字段与 1,393 条记录保持只读 |
| 定性输入 | 不联网、不更新；只读已有本地 v1/v2 或固定 fallback |
| Telegram | 唯一生产展示面 |
| Google Sheets | 代码、依赖和自动同步已删除 |
| 默认策略报告 | 直接使用 QFQ 与沪深300全收益口径，输出 IC、spread、非重叠批次收益/回撤和 L3 对比 |
| qualitative acceptance | 已删除 |
| weekly PM loop | 已删除 |

## 自动 cron

- 周六 10:00：TuShare 财务/分红；
- 工作日 16:00：QFQ；
- 工作日 17:15：TuShare 估值；
- 工作日 17:30：Framework A daily 与 Telegram；
- 工作日 18:00：outcome-update。

`cron-setup.sh` 会清理已退休的 Framework B、weekly PM 和 acceptance 旧规则。

## 两批减法结果

删除范围：

- Framework B report/cohort 生产模块、runner、CLI、测试和 cron；
- L3 v1 纯计算、新写入、回填和专属测试；
- Sheets 模块、依赖、测试和 post-step；
- Gemini v1 API、缓存刷新和测试；
- qualitative acceptance 模块、baseline、脚本、测试和 cron；
- weekly PM 模块、测试和 cron。

第二批删除：

- M4/M5 代码、脚本、fixture、测试、证据和治理文档；
- qualitative 历史合同、pilot、Gemini client、writer/shadow 与 authorization；
- outcome shadow 构建、报告、import/revert、migration 及对应测试；
- 已完成且只能手工运行的离线 L3 v2 backtest 与复审产物；
- 手工沪深300全收益 snapshot，改由 QFQ cron 自动写入 `index_prices.H00300`。

兼容边界：

- 不删除或改写生产数据库历史行；
- predictions 的 L3 v1 列暂时保留，未来新行保持 NULL；
- existing qualitative v2 表保留只读，6 条历史分数仍可被选择；
- existing outcome shadow 表及历史行不删除，但活动代码不再消费。

## 当前 blocker

1. live DB 当前只有价格指数 `000300`；`H00300` 首次自动写入前报告按设计显示无可用样本；
2. 尚未完成 watchlist 等权组合与沪深 300 全收益基准拆解；
3. 自动报告已具备核心指标，但跨月份样本和非重叠批次数仍少；
4. L3 v2 strong 候选尚无实际拒绝，当前没有买点选择能力证据。

## 下一步

1. 复核自动报告真实输出与全收益数据新鲜度；
2. 拆解固定 watchlist 的股票池效应；
3. 等更多非重叠时间批次后对 Framework A 和 L3 v2 做阶段一去留决策。
