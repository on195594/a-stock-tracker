# 项目状态

**更新时间：** 2026-09-12
**当前阶段：** 三阶段路线的阶段一
**当前目标：** 证明 Framework A 是否具有可复验投资价值；L3 v2 已完成判定

## 2026-09-12 共享架构收敛

- 仓库声明依赖已切换到不可变的 `a-stock-lib==0.8.0` GitHub Release wheel，SHA-256 为 `a811945b23d97eb121ff82d54bc0ba0810000a5379a9e9786fdcdc9220b30310`；本次未修改生产 `.venv`。
- A-F executable scoring contract 与 cache-only 行业映射由 `a-stock-lib` 单独拥有；tracker 只消费公开 contract，不读取共享包私有 cache。
- release-candidate 下游验证、完整测试、Ruff、format、mypy、独立 cwd import 与跨仓只读验收通过。
- 本次只调整代码、依赖与文档；未修改生产数据库、cron、真实持仓或 W1 状态。以下投资有效性 blocker 保持不变。

## 2026-09-07 投资审查修复

- 已修复PB历史长度混用：只消费当日十年窗口物化分位，120–121个月桶；短历史或窗口内缺月保持缺失，不再以价格/BPS重新覆盖。
- 原“最大回撤”更名为批次端点回撤，新增日收盘最大回撤。当前本地数据重算30/60/90日分别为 **-13.18% / -15.91% / -15.91%**，原研究收益/IC/spread及截面选择不变。
- 新prediction将保存量化原始输入、实际输入、配置、分项、定性缓存日期及实现指纹；沿用get_db的增量补列，不改写历史评分或补造历史快照。
- 新16位hash覆盖权重、输入处理/评分源码与共享包版本，和旧8位hash隔离。原9月最终检查不适用于新版本；新cohort从首次完整写入开始积累。
- Telegram只用prediction快照显示定性判断，标明冻结日期及未验证边界；不再用最新缓存替代缺失的历史快照，评估总数与名单数量分开显示。
- 验证：263 tests、项目结构10 tests、Ruff、format、mypy及diff检查通过。35股本地shadow只读preview已核验；生产tracker.db未改写、未手动运行daily或联网抓取，首次新hash/快照落库仍待既有自动链。
- 尚未解决投资有效性：行业适用性、旧定性分本身的可信度、阈值收益验证、真实可成交/扣费收益、仓位与退出。当前仅为研究工具，不是实盘策略。

## 修复前生产基线（以下表格保留2026-09-04记录，不代表新版本已落库）

| 能力 | 状态 |
|---|---|
| Framework A | 自动 daily 写入；live DB 截至 2026-09-02 为 2,941 条，最新批次 35/35 |
| Framework B | 活动代码、入口和 cron 已删除；历史 73 条 prediction 与 15 条 cohort 数据保持只读 |
| TuShare 基本面/估值/分红 | 35/35 生产物化 |
| 共享数据包 | `a-stock-lib==0.6.3`；生产 `.venv` 已回读 metadata/module 版本 `0.6.3` 与 site-packages 路径；项目结构 10 tests、全量 256 tests、Ruff、format、mypy、pip check 和 CLI smoke 通过；Research A—F scorer 未接入 tracker |
| QFQ 日线与全收益基准 | 35/35 个股截至 2026-09-02；`H00300` 156 条，截至 2026-09-01 |
| L3 v2 | 1,330 条：1,318 正常、12 触发；高分候选 666 正常、0 触发；仅作极端风险提示，不参与候选分层 |
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

1. 修正前cohort当前仍为3/1/1个30/60/90日截面，且量化输入未冻结；不能用其成熟日期为修正后算法背书。
2. 2026-09-07只读核查：Framework A为3,011条，最新2026-09-04为35/35；个股QFQ截至09-04，沪深300全收益截至09-03。修正版本尚无成熟样本。
3. 历史Q5相对观察池等权收益差三个窗口均负；这只支持继续保持未验证定位，不支持立刻调参。
4. 严格十年窗口会暴露短历史和缺月数据；保持缺失，不靠旧数据或中性常量伪造覆盖。未修复的行业和缺失值计分偏差必须继续披露。

## 下一步

1. 核实下一次17:15/17:30自动链的PB物化、新hash、35股覆盖和评分/定性日期快照；不手工删除今日记录重跑。
2. 按路线图2026-09-07修订积累新cohort，未满足数据/快照和截面门槛时为 `INSUFFICIENT_EVIDENCE`；不承诺旧9月检查日期。
3. 保持权重、阈值、股票池不变，先验证最小排序优势；行业内排序、消融和可交易收益实验另行预注册。
