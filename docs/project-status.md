# 项目状态

**更新时间：** 2026-09-21
**生命周期：** `CLOSED_UNPROVEN`
**投资状态：** Framework A 未证明有效；阶段二、阶段三均不启动
**保留运行范围：** 通用 TuShare 基本面、估值、交易日历、QFQ 个股与沪深300全收益数据采集

## 2026-09-21 项目结案

- 原 2026-08-12 阶段一实验结案为 `INVALIDATED_BY_DATA_AND_PROTOCOL_DEFECT`。PB 输入、量化输入快照、回撤和实现身份缺陷使其不能验证修正后的算法；已观察到的负向/混合结果保留，不因失效而删除。
- S2.1 successor 实验结案为 `CLOSED_UNPROVEN`：截至结案时 30/60/90 日成熟截面仍为 0/3、0/2、0/1，无法证明 Framework A 有效；不再用无限 `INSUFFICIENT_EVIDENCE` 顺延替代项目决定。
- 该结论是“未在可接受时间和证据预算内证明价值”的产品/研究去留决定，不是统计上证明 Framework A 必然无效。
- 不进入离线扩大股票宇宙的阶段二，也不进入生产决策闭环的阶段三；不再扩池、调权、增加框架或把历史输出用于买入判断。
- 已退休工作日 17:30 的 Framework A 评分、策略报告与 Telegram 观察名单定时任务。`cron-setup.sh` 只管理通用数据任务，并会清理遗留的项目 daily 规则。
- 历史代码、`config/experiment_manifest.json`、数据库记录、报告与审计证据原位保留；不回填、不改写、不删除生产数据。通用数据任务继续运行，不产生新的 Framework A 裁决证据。

以下内容均为结案前历史记录，不改变上述最终状态。

## 2026-09-20 S2.1 amendment 最后验收（历史）

- 仓库已采用 `effective_to=2026-11-30`；本次只改该字段，`effective_from=2026-09-18`、scoring hash、固定 35 股、universe hash、30/60/90、3/2/1、`min_coverage=0.90` 和 `registration_status=verified` 均不变。原始预注册历史不改写。
- 生产激活已由独立只读回验确认：生产读取路径为 `/home/lin/a-stock-tracker/config/experiment_manifest.json`，checkout 为 activation commit `a81e1b5`；回执与脱敏证据见本轮 `round-20260920-s2.1-final` 审计包。
- 软件验收在独立副本完成：修复默认 manifest 日期断言后，相关测试 1 passed、全量 pytest 332 passed、structure 11 passed、Ruff、format、mypy、`git diff --check` 均通过。生产 checkout 未因本轮测试修复而更新或发布。
- 生产只读报告仍为 `S2_EVALUATION / INSUFFICIENT_EVIDENCE`；三个窗口仅有 `2026-09-18`、成熟截面 `0/0/0`、收益指标为空。未来交易日、真实样本和正式绩效证据继续 pending。
- 结案前状态：**S2.1 ACTIVE / DEVELOPMENT FROZEN / WAITING FOR REAL SAMPLES**。

## 2026-09-20 生产实验配置激活（原始 enrollment 记录）

以下记录保留 amendment 激活前的原始 enrollment 边界；当前活动 manifest 已由上方 S2.1 验收节更新为 `effective_to=2026-11-30`。

- 经单独授权，生产 checkout 已从 `c455f7d` 快进到包含舍入修复 `db2df78` 的 master；生产回验记录为 322 tests、11 structure tests、Ruff、format、mypy、diff check 通过。既有 cron 仍直接引用该可变 checkout；实际安装的 a-stock-lib 0.8.0 与发布 wheel `a811945b23d97eb121ff82d54bc0ba0810000a5379a9e9786fdcdc9220b30310` 的包文件一致。
- 落库证据确认当前 writer 身份对应 scoring hash `d312c8995522b563`，于 2026-09-18 首次出现且有预定 35/35 唯一记录；固定 35 股的来源文件和 universe hash 与 manifest 一致。
- 旧生产评估器曾因舍入顺序错误只接受 27/35；`db2df78` 按可证明区间修复后，同一生产只读数据为 35/35，首次完整及 90% 协议合格日期均为 2026-09-18。原始 enrollment 为 2026-09-18 至 2026-11-17；随后由 S2.1 amendment 延长活动 manifest 截止日，历史 prediction 未改写。
- 经单独授权，verified manifest 与 TuShare SSE `trade_cal` 本地证据已激活。工作日 16:00 的既有 QFQ 任务会先原子刷新忽略目录中的运行态日历和按 hash 保存的规范化提供方返回行，并核对已有官方休市证据；默认报告优先读取运行态证据，首次运行前回退到 tracked seed。刷新失败恢复旧文件并告警，过期报告 fail-closed，不预填未来交易日。
- 默认报告从无关 cwd 加载活动 manifest 和日历，以只读事务读取生产数据并返回 `S2_EVALUATION / INSUFFICIENT_EVIDENCE`；三个窗口均固定 2026-09-18 的 35/35 截面。实验已登记生效，但真实 30/60/90 日窗口尚未成熟，策略仍未验证有效。未发现用户已提供的真实账户样例，S3b 状态为 `NOT_RUN_INPUT_NOT_PROVIDED`。

## 2026-09-20 隔离审计测试修复（未部署）

- 隔离副本仅冻结 `Asia/Shanghai` 时钟，修复 QFQ 日历跨日/跨年测试对主机 `date.today()` 的依赖；未修改生产逻辑、评分、预测、数据库、配置或 cron。
- 隔离副本验证：focused 29 passed、全量 332 passed、structure 11 passed、Ruff、format、mypy、`git diff --check` 通过。该测试修复已合入远端 master 的 `e8a6ce8`；生产 checkout 尚未部署该提交。

## 2026-09-20 S2.1 协议修订候选（历史记录，已由后续激活替代）

本节保留候选阶段的原始状态，不代表当前活动 manifest，也不改写原始预注册。

- 只读审计确认原 `2026-09-18..2026-11-17` 边界无法在开放日评分假设下容纳 30 日的 3 个非重叠截面；用户已裁决 pending candidate 将 `effective_to` 延至 `2026-11-30`，为数学下界 2026-11-18 提供 12 天运行容错，起点、35 股、scoring hash `d312c8995522b563`、3/2/1 门槛和 90% 覆盖率均不变。
- 候选当时保持 `registration_status=pending`、`evidence_ref=null`，未覆盖活动 manifest；未来交易日证据、未来写入完整性和成熟收益仍为 pending/`INSUFFICIENT_EVIDENCE`。该 amendment 在观察到起始截面后提出，不冒充原始预注册。
- 当时仅观察到 `2026-09-18` 的 35/35 合格截面；候选阶段停止新增开发，等待协议裁决与真实样本，不因候选修订或软件通过宣称投资有效。

## 2026-09-18 S2 生产部署（manifest pending）

- Tracker 的 S2 软件源码 `7516535` 已从原生产基线 `69f11c9` 上线，运行依赖从 a-stock-lib 0.7.0 对齐到锁定的 0.8.0；后续仅追加状态文档，生产 checkout 与远端 master 保持同步。
- 上线后通过 299 tests、11 structure tests、Ruff check/format、mypy 与 diff check；只读生产报告 smoke 返回 `MANIFEST_PENDING / INSUFFICIENT_EVIDENCE`。
- 既有 cron 未修改，未手工运行 daily、未写生产数据库、未发通知、未登记真实 scoring hash/日期/日历。S2 软件已部署，但生产实验与投资有效性仍未验证，S4 继续延后。

## 2026-09-17 S2 评估实现（软件证据；manifest pending）

- 评估 as-of 独立于 enrollment `effective_to`；评分时资格仍按 manifest 日期范围限制，后续 outcome 可以成熟。正式指标只消费已到自然日观察期限的固定批次；未成熟批次保留诊断、不阻塞已有成熟结果，到期坏批次绝不剔除或替换。
- 历史资格只校验 prediction 已记录的 scoring/qualitative 输入、结果、来源、mode 和 as-of；不回查可变 qualitative 缓存，允许真实的 hybrid 各维度来源向量。
- 历史结果还校验真实 scorer `component_scores` 的固定定性分项；非法 UTF-8/超大 JSON 受控为证据不足；固定批次与 Q1/Q5 权重不消费未来行情来选样本。
- entry 与 d+window endpoint 均按已证明日历向后对齐（沿用 10 日 lag），并披露实际日期；相关冻结篮子逐日路径或批次连续性缺失时，全链复合指标为 NULL，端点数仅作批次诊断；无关坏行情保留独立诊断。
- pending manifest 报告已知 expected=35，实际/合格/结果计数保持 NULL，不读取 DB；尚未执行评估时 evaluation_version 为 NULL，不伪造有效样本。
- 默认 manifest 位于 `config/experiment_manifest.json`，由 `paths.py` 解析为与 cwd 无关的项目路径。它只固定原始 35 股来源 commit/content hash；生产 scoring hash、适用日期和证据引用使用空/NULL unknown，状态保持 `pending`。
- 软件评估口径为 `2026-09-17.e2`。默认报告已具备本地只读日历证据加载能力；真实生产日历的核实与启用状态，以对应取证和部署回执为准。缺失或畸形日历仍 fail-closed，且运行时不联网补日历或生成生产表。e2 是软件口径，不是生产启用或投资有效性证明。
- 隔离 Python 3.13.5 / 声明 lib 0.8.0 验证：299 tests、11 structure tests、Ruff check/format、mypy 均通过。真实 Codex 只读审查发现的两个 blocker（未成熟批次阻塞、畸形日历异常）已补反例修复；限定复审 PASS，53 focused tests 通过。完整生产登记/日历仍 NOT_VERIFIED。
- 本次未修改评分、预测写入、股票池、schema、cron、通知、生产数据库或 a-stock-lib；真实生产 manifest、样本成熟度和客户端/生产证据仍未核实。

### 后续文档与等价清理（2026-09-17）

- README、路线图和 TODOS 对齐 S2/e2 当时的未部署/未登记边界；历史协议、生产版本记录和旧指标不冒充当前核验。
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
| 默认策略报告 | verified manifest 与本地日历已启用；真实窗口未成熟时保持 `INSUFFICIENT_EVIDENCE` |
| legacy outcome / Phase4 | 写入、手工 CLI、cron 和里程碑通知已删除；历史字段与数据只读保留 |
| qualitative acceptance | 已删除 |
| weekly PM loop | 已删除 |

## 结案后自动 cron

- 周六 10:00：TuShare 财务/分红；
- 工作日 16:00：SSE 交易日历、QFQ 个股日线与沪深300全收益；
- 工作日 17:15：TuShare 估值。

`cron-setup.sh` 只安装以上三类通用数据任务，并清理已退休的 Framework A daily、Framework B、weekly PM、acceptance 和 legacy outcome-update 旧规则。

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

## 结案时未解决的投资缺口

1. 修正前 cohort 的量化输入未冻结，不能验证修正后的算法；其 30 日负向、60/90 日混合结果仍是历史反证，不能抹去。
2. 修正版本在结案时没有成熟截面，无法形成有效/无效的统计结论。
3. 固定 35 股存在行业、Beta 和选择偏差；3/2/1 只是低样本方向门槛，不是统计显著性或实盘采用门槛。
4. 评分日收盘起算不可成交，费用、滑点、成交限制、仓位和退出均未验证。

## 结案后维护边界

1. 只维护通用数据采集的正确性、安全性和审计能力；不再维护 Framework A 的投资有效性路线图。
2. 不手工运行或恢复 Framework A daily、策略报告或 Telegram 观察名单定时任务。
3. 如未来重新研究，必须作为新项目重新提出假设、数据可达性、硬截止日和独立样本外验证；本项目不得原地复活或继续顺延。
