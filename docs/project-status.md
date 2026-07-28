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
| QFQ 日线 | 35/35，工作日 16:00 自动采集 |
| L3 v2 | 385 条：375 pass、10 reject；strong v2 198 pass、0 reject |
| L3 v1 | 计算、写入和回填已删除；历史字段与 1,393 条记录保持只读 |
| 定性输入 | 不联网；读取已有本地缓存或固定 fallback；已有 v2 行仍自动读取 |
| Telegram | 唯一生产展示面 |
| Google Sheets | 代码、依赖和自动同步已删除 |
| legacy outcome | 自动更新仍运行，等待第二批 QFQ 默认报告替换 |
| qualitative acceptance | 已删除 |
| weekly PM loop | 已删除 |

## 自动 cron

- 周六 10:00：TuShare 财务/分红；
- 工作日 16:00：QFQ；
- 工作日 17:15：TuShare 估值；
- 工作日 17:30：Framework A daily 与 Telegram；
- 工作日 18:00：outcome-update。

`cron-setup.sh` 会清理已退休的 Framework B、weekly PM 和 acceptance 旧规则。

## 第一批减法结果

删除范围：

- Framework B report/cohort 生产模块、runner、CLI、测试和 cron；
- L3 v1 纯计算、新写入、回填和专属测试；
- Sheets 模块、依赖、测试和 post-step；
- Gemini v1 API、缓存刷新和测试；
- qualitative acceptance 模块、baseline、脚本、测试和 cron；
- weekly PM 模块、测试和 cron。

兼容边界：

- 不删除或改写生产数据库历史行；
- predictions 的 L3 v1 列暂时保留，未来新行保持 NULL；
- existing qualitative v2 表和自动读路径暂时保留；
- existing outcome shadow 表不在第一批修改。

## 当前 blocker

1. 默认策略报告仍读取 legacy 未复权 outcome；
2. 尚未完成 watchlist 等权组合与沪深 300 全收益基准拆解；
3. 尚未完成按日期截面的 IC、Q5−Q1、回撤和时间批次评估；
4. L3 v2 strong 候选尚无实际拒绝，当前没有买点选择能力证据；
5. 第二批历史研究与 shadow 工具链尚未删除。

## 下一步

执行第二批：

1. 将最小 QFQ 总收益评估接入默认 `accuracy-report`；
2. 删除 outcome shadow/migration；
3. 删除 M4/M5、历史 qualitative pilot、手工 qualitative writer/shadow 及其测试、脚本和治理文档；
4. 更新结构合同与默认验证面。
