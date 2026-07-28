# 项目状态

**更新时间：** 2026-07-28
**当前阶段：** 三阶段路线的阶段一
**当前目标：** 证明 Framework A 与 L3 v2 是否具有可复验投资价值

## 生产基线

| 能力 | 状态 |
|---|---|
| Framework A | 自动 daily 写入；live DB 截至 2026-07-28 为 2,031 条 |
| Framework B | 活动代码、入口和 cron 已删除；历史 73 条 prediction 与 15 条 cohort 数据保持只读 |
| TuShare 基本面/估值/分红 | 35/35 生产物化 |
| QFQ 日线与全收益基准 | 35/35 个股；16:00 自动任务已接入 `H00300`，live DB 尚待下一次成功调度首次写入 |
| L3 v2 | 420 条：409 pass、11 reject；strong v2 216 pass、0 reject |
| L3 v1 | 计算、写入和回填已删除；历史字段与 1,393 条记录保持只读 |
| 定性输入 | 不联网、不更新；只读已有本地 v1/v2 或固定 fallback |
| Telegram | 自动展示未验证观察名单，不再使用“主推/候补”买入语义 |
| Google Sheets | 代码、依赖和自动同步已删除 |
| 默认策略报告 | daily 成败均自动刷新；只评估 qualitative provenance 已记录的当前评分 cohort |
| legacy outcome / Phase4 | 写入、手工 CLI、cron 和里程碑通知已删除；历史字段与数据只读保留 |
| qualitative acceptance | 已删除 |
| weekly PM loop | 已删除 |

## 自动 cron

- 周六 10:00：TuShare 财务/分红；
- 工作日 16:00：QFQ；
- 工作日 17:15：TuShare 估值；
- 工作日 17:30：Framework A daily、自动策略报告与 Telegram 观察名单。

`cron-setup.sh` 会清理已退休的 Framework B、weekly PM、acceptance 和 legacy outcome-update 旧规则。

## 减法结果

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

阶段一收口：

- Telegram 改为未验证观察名单；
- 报告改为 daily 后自动刷新，删除手工 CLI；
- 删除 legacy outcome-update、Phase4 通知和 18:00 cron；
- 报告按最新评分版本、qualitative provenance、90% 完整非重叠截面和观察池等权基线评估；
- L3 只评估高分候选；高分候选零拒绝时直接显示暂无选择能力样本；
- daily 恢复不再受已独立运行的 index/calendar 能力门禁阻断。

兼容边界：

- 不删除或改写生产数据库历史行；
- predictions 的 L3 v1 列暂时保留，未来新行保持 NULL；
- existing qualitative v2 表保留只读，6 条历史分数仍可被选择；
- existing outcome shadow 表及历史行不删除，但活动代码不再消费。
- predictions 的 legacy outcome/benchmark/alpha/estimate 字段和既有值不删除，但不再写入；
- existing phase_milestones 表及历史行不删除，新库不再创建或写入。

## 当前 blocker

1. live DB 当前只有价格指数 `000300`；`H00300` 首次自动写入前报告按设计显示无可用样本；
2. provenance 完整的当前评分 cohort 从 2026-07-24 开始，尚无到期样本；
3. L3 v2 strong 候选尚无实际拒绝，当前没有买点选择能力证据。

## 下一步

1. 等下一次自动 QFQ 任务写入 `H00300` 后，复核自动报告真实输出与全收益数据新鲜度；
2. 当前 provenance cohort 到期后，直接使用其 30/60 日 IC 与 Q5−观察池等权收益差，对 Framework A 做阶段一去留决策；旧 provenance 不明记录不得混入；
3. L3 v2 strong 持续零拒绝时，从买入决策路径移除或只保留极端风险提示。
