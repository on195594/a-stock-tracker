# 项目状态

**更新时间：** 2026-08-23
**当前阶段：** 三阶段路线的阶段一
**当前目标：** 证明 Framework A 是否具有可复验投资价值；L3 v2 已完成判定

## 生产基线

| 能力 | 状态 |
|---|---|
| Framework A | 自动 daily 写入；live DB 截至 2026-08-21 为 2,661 条，最新批次 35/35 |
| Framework B | 活动代码、入口和 cron 已删除；历史 73 条 prediction 与 15 条 cohort 数据保持只读 |
| TuShare 基本面/估值/分红 | 35/35 生产物化 |
| 共享数据包 | `a-stock-lib==0.6.0`；生产 `.venv` 已回读 metadata/module 版本 `0.6.0` 与 site-packages 路径；项目结构 10 tests、全量 256 tests、Ruff、format、mypy、pip check 和 CLI smoke 通过；Research A—F scorer 未接入 tracker |
| QFQ 日线与全收益基准 | 35/35 个股截至 2026-08-21；`H00300` 148 条，截至 2026-08-20 |
| L3 v2 | 700 条：688 正常、12 触发；高分候选 352 正常、0 触发；仅作极端风险提示，不参与候选分层 |
| L3 v1 | 计算、写入和回填已删除；历史字段与 1,393 条记录保持只读 |
| 定性输入 | 不联网、不更新；只读已有本地 v1/v2 或固定 fallback |
| Telegram | 自动展示未验证观察名单；高分候选不再按 L3 分层，L3 仅显示极端风险提示 |
| Google Sheets | 代码、依赖和自动同步已删除 |
| 默认策略报告 | daily 成败均自动刷新；合并经核实语义等价且 qualitative provenance 合格的当前评分 cohort |
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

## 已解除

1. `H00300` 已进入自动采集和默认报告；报告现在显式显示个股与基准各自截止日；
2. 2026-07-31 仅修改评分配置说明文字曾错误切断 cohort；现已使用排除 `note` 的语义 hash，并只读合并已核实等价的 `8aea81ed`、`832893a3` 与 canonical `8181a13c`；
3. L3 v2 高分候选 352 次评估零触发，已从候选分层移除，仅保留极端风险提示。

## 当前 blocker

1. 2026-08-21 中期检查结果为 `INSUFFICIENT_EVIDENCE`：30 日 2/3 个截面、60 日 1 个截面、90 日 1/1 个截面，不足以进入四级分类；
2. 沪深300全收益截至 2026-08-20，比个股 QFQ 晚一个交易日，检查时未满足共同日终对齐要求；
3. 当前方向未显示稳定优势：30 日 IC 为正，但 Q5−Q1 与 Q5−观察池为负；60 日仅一个截面；90 日也是一正两负。中期方向不构成最终裁决。

## 下一步

1. 当沪深300全收益 `as_of >= 2026-08-23` 的首个完整日终报告生成后（按当前发布节奏预计最早 2026-08-25），复核 `2026-07-24` 第三个 30 日截面；
2. 当沪深300全收益 `as_of >= 2026-09-22` 且第二个 60 日截面完整后（预计最早 2026-09-23），按预注册协议完成阶段一继续、简化、重做或停止的决定；证据仍不足则继续输出 `INSUFFICIENT_EVIDENCE`；
3. 不扩大股票池、不调权重、不新增框架，避免在阶段一结论前再次重置可比口径。
