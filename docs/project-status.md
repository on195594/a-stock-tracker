# 项目状态

**更新时间：** 2026-09-17
**当前阶段：** 三阶段路线的阶段一
**当前目标：** 证明 Framework A 是否具有可复验投资价值；L3 v2 已完成判定

## 2026-09-17 S2 评估实现（软件证据；manifest pending）

- 评估 as-of 独立于 enrollment `effective_to`；评分时资格仍按 manifest 日期范围限制，后续 outcome 可以成熟。正式指标只消费已到自然日观察期限的固定批次；未成熟批次保留诊断、不阻塞已有成熟结果，到期坏批次绝不剔除或替换。
- 历史资格只校验 prediction 已记录的 scoring/qualitative 输入、结果、来源、mode 和 as-of；不回查可变 qualitative 缓存，允许真实的 hybrid 各维度来源向量。
- 历史结果还校验真实 scorer `component_scores` 的固定定性分项；非法 UTF-8/超大 JSON 受控为证据不足；固定批次与 Q1/Q5 权重不消费未来行情来选样本。
- entry 与 d+window endpoint 均按已证明日历向后对齐（沿用 10 日 lag），并披露实际日期；相关冻结篮子逐日路径或批次连续性缺失时，全链复合指标为 NULL，端点数仅作批次诊断；无关坏行情保留独立诊断。
- pending manifest 报告已知 expected=35，实际/合格/结果计数保持 NULL，不读取 DB；尚未执行评估时 evaluation_version 为 NULL，不伪造有效样本。
- 默认 manifest 位于 `config/experiment_manifest.json`，由 `paths.py` 解析为与 cwd 无关的项目路径。它只固定原始 35 股来源 commit/content hash；生产 scoring hash、适用日期和证据引用使用空/NULL unknown，状态保持 `pending`。
- 软件评估口径为 `2026-09-17.e2`。无 live calendar；未注入显式 `CalendarEvidence` 时 fail-closed，畸形日历受控降级；未联网补日历或生成生产表。e2 是软件口径，不是生产启用或投资有效性证明。
- 隔离 Python 3.13.5 / 声明 lib 0.8.0 验证：299 tests、11 structure tests、Ruff check/format、mypy 均通过。真实 Codex 只读审查发现的两个 blocker（未成熟批次阻塞、畸形日历异常）已补反例修复；限定复审 PASS，53 focused tests 通过。完整生产登记/日历仍 NOT_VERIFIED。
- 本次未修改评分、预测写入、股票池、schema、cron、通知、生产数据库或 a-stock-lib；真实生产 manifest、样本成熟度和客户端/生产证据仍未核实。

### 后续文档与等价清理（2026-09-17）

- README、路线图和 TODOS 对齐 S2/e2 源码状态与未部署/未登记边界；历史协议、生产版本记录和旧指标不冒充当前核验。
- endpoint 复用已有日期对齐函数，删除重复的 as-of 过滤；10 日 lag、固定选择、权重和缺口处理不变。未发现可直接删除的无调用 Tracker 私有函数，不为清理而删测试或兼容入口。
- 重新通过 299 tests、11 structure tests、Ruff check/format、mypy；日志 `/tmp/a-stock-tracker-cleanup-check.log`。本次未新增独立审查，上方审查结论对应 S2 落地范围；旧发布候选继续按原 commit/hash 识别。

## 2026-09-13 声明依赖对齐 a-stock-lib 0.8.0 与共享架构收敛

- 仓库声明依赖已切换到不可变的 `a-stock-lib==0.8.0` GitHub Release wheel，SHA-256 为 `a811945b23d97eb121ff82d54bc0ba0810000a5379a9e9786fdcdc9220b30310`；当时记录的生产 `.venv` 为 `0.7.0`，本次未重新核验现场版本。
- A-F executable scoring contract 与 cache-only 行业映射由 `a-stock-lib` 单独拥有；tracker 只消费公开 contract，不读取共享包私有 cache。
- release-candidate 下游验证、全量 264 tests、项目结构 11 tests、Ruff、format、mypy、独立 cwd import 与跨仓只读验收通过。
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
| 默认策略报告 | S2 代码已接入结构化评估；默认 manifest pending 时 fail-closed，不宣称生产启用 |
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
