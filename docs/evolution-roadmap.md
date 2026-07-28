# a-stock-tracker 进化路线图

**版本：** v1.32
**基线日期：** 2026-07-28
**文档定位：** 系统演化的顶层规划文档。所有后续 Phase 的修改、补丁、设计决策均以本文档为基线。若实施中发现偏差，先更新本文档，再改代码。

---

## 一、目标定义

将 a-stock-tracker 从"对固定 watchlist 打分并记录"演化为**A 股选股与买入决策支持系统**。
系统帮助用户判断“买什么、什么时候买”，目标是提高获得较高风险调整后收益的概率，
不承诺高收益，也不把工程门禁、报告完整或模型调用成功等同于投资有效性。

目标能力仍分为三层：

| 层次 | 问题 | 当前状态 |
|------|------|---------|
| L1 好公司 | 这家公司值不值得持有？ | ✅ 已成型（ROE/增速/负债率/毛利率/Gemini定性） |
| L2 好价格 | 当前估值有没有安全边际？ | ✅ TuShare PB/PE/PB历史生产物化；PB 十年覆盖不足时 fail-closed，PE 分位暂不计权 |
| L3 合适买点 | 现在是不是好的入场时机？ | ⚠️ v2 已接入生产，但当前仅证实为极端下跌风险门禁；买点能力待阶段一验证 |

**L1/L2 不使用价格动量评分**：框架的公司质量和估值层保持价值投资逻辑；价格与成交量
只允许在独立 L3 层作为待验证的入场或风险信号，不得改写 `total_score`。

### 1.1 当前最高优先级

当前瓶颈已经从数据接入和生产可靠性转为策略证据：

- Framework A 尚未证明稳定的截面排序能力；
- `buy_strong=44` 是可得分上限校准，不是收益最优门槛；
- 生产 L3 v2 只拒绝 `close < MA120 × 0.65` 的极端下跌，更接近风险门禁而非买点；
- Telegram 把定性分差值解释为择时，存在产品语义错误；
- legacy outcome 使用未复权价格，不能继续作为未来策略训练的唯一真相；
- 固定 35 股只能验证有限股票池，不能代表 A 股市场选股能力。

在这些问题解决前，不修改评分权重，不继续扩展研究治理基础设施，不启动新框架生产化。

### 1.2 三阶段收敛路线（当前权威执行顺序）

#### 阶段一：证明现有系统是否有效（当前执行）

目标：在不调整权重的前提下，判断现有评分和 L3 是否具有可复验投资价值。

执行项：

1. 比较 watchlist 等权组合与沪深 300，拆出固定股票池效应，再评估评分在池内的排序能力；
2. 使用 QFQ 个股总收益和全收益基准评估 30/60/90 日结果；
3. 按 `score_date` 计算截面 Spearman IC 并做时间汇总，同时增加 Q5−Q1 spread、
   最大回撤、时间批次和同股事件去重；
4. 报告 L3 v2 的 `NULL/0/1`、状态分布及通过/拒绝后的收益和回撤；若拒绝样本长期
   接近零，则判定当前规则无实质选择性，不无限等待样本；
5. 修正 Telegram：定性分不再解释为择时，只展示实际决定主推的 L3 版本；
6. 设计未来版本化 outcome，历史 prediction 不原地改写。

退出条件：

- 明确判断 Framework A 是否具有跨窗口、跨时间批次的正排序能力；
- 明确判断 L3 是买点信号、风险门禁还是无效规则；
- 策略报告采用可解释的总收益口径；
- 形成继续、简化或重做评分模型的明确决定。

#### 阶段二：离线扩大股票宇宙并验证框架

目标：去除固定 35 股带来的股票池效应、行业集中和选择偏差，不直接建设生产动态池。

执行项：

1. 构建 300–500 股历史截面研究集；
2. 按行业分层或行业中性评估 Framework A/B；
3. 比较不同市场阶段的 IC、top-bottom spread、命中率和回撤；
4. 纳入交易成本、停牌、可交易性和样本外验证；
5. 若 Framework A 无效，先简化或重做评分，不叠加 C/D/E/F 新框架。

退出条件：

- 完成预先约定的样本外、行业分层、成本和可交易性检验，并形成可复验结论；
- 若至少一个框架显示稳定、可解释且不依赖单一行业、固定 watchlist 或单一时间区间的
  排序优势，则定义生产候选池规则及失败边界；
- 若没有框架通过，则明确简化、重做或停止，不通过叠加新框架延长阶段二。

#### 阶段三：建设生产决策支持闭环

目标：在策略证据通过后，回答“买什么、为什么现在买、买多少、何时退出”。

执行项：

1. 建设动态候选池与受控数据采集；
2. 上线经验证的买点信号，而不是把风险门禁重新命名为买点；
3. 增加仓位、组合集中度、退出/失效条件和风险预算；
4. 先 report-only/canary，再逐步影响生产主推；
5. 持续监控收益、回撤、换手和模型漂移。

退出条件：

- 推荐拥有完整决策时快照、行动建议和事后归因；
- 风险调整后结果优于明确基准；
- 优势不是由单一行业、市场暴露或固定股票池效应驱动。

### 1.3 与既有 Phase 的映射

- 既有 Phase 4/5 的后续工作并入阶段一；
- Phase 6 Framework B cohort 继续被动积累，但不阻塞阶段一，也不提前生产化；
- Phase 7 的离线研究部分前移到阶段二，生产动态池保留到阶段三；
- M4/M5 和 outcome shadow 既有证据冻结，不再作为当前执行路线；
- 详细减法结论见
  `docs/reviews/2026-07-28-product-direction-and-engineering-subtraction-review.md`。

### 1.4 防止再次工程过重

新任务必须先说明它验证哪一个可证伪假设：提高 alpha、改善买入时点或降低回撤/不可逆风险。
只读研究不创建生产级 migration、seal、reviewer 或 authorization 系统；只有生产 DB、cron、
凭证、真实外部调用、权重和不可逆行为使用重治理。研究不确定时应限制声明，而不是用更多
工程把“不确定”包装成“完成”。

文档职责保持单一：本文只管理方向、阶段顺序和退出条件；`TODOS.md` 只管理当前动作；
`docs/project-status.md` 只记录带日期的运行事实和 blocker；阶段总结只保留决策理由；
README/CLAUDE 只做入口与短摘要。运行数字冲突时以最新只读查询和日志为准，执行顺序
冲突时以本文为准，避免在多份文档中各自演化一套路标。

---

## 二、当前系统基线（2026-07-28；运行数据截至 2026-07-27）

### 能力盘点

| 模块 | 状态 | 说明 |
|------|------|------|
| Framework A 评分 | ✅ 正常 | 生产写入框架仍仅 A；live DB 共 1,996 条 A 记录，另有 73 条 legacy B |
| Framework B 评分 | ⏸ report-only | 73 条 legacy B 记录仅作回溯；prospective cohort 改为显式按周冻结并绑定固定 A prediction，不启用生产写入 |
| 每日 PB 分位 | ✅ TuShare 主源 | 35/35 已物化；28 FULL_10Y、5 SINCE_LISTING、2 INSUFFICIENT_HISTORY，历史不足不沿用旧源十年分位 |
| 通用财务/分红 | ✅ TuShare 主源 | 35/35 单事务物化；财务按有效公告日和三个已披露年度选择，分红仅取最新已实施税前事件 |
| 定性评分 | ✅ v1 + v2 hybrid | v1 保持 30 天缓存与既有 fallback；v2 全局选择器已启用，6 股使用 source-grounded moat/market_pos，其余维度或股票回退 v1 |
| Telegram 推送 | ✅ v2 门禁已上线 | 主推条件为 ≥ buy_strong 且 `l3_v2_signal=1`；v2 为 0/NULL 的高分股进入候补 |
| Outcome 追踪 | ✅ legacy 正常；versioned shadow 已物化 | live DB 中 Framework A 30d/60d/90d 结案 1,303/568/40；另有不可变 shadow 1 run、1,776 results、2,520 observations。报告仍读取 legacy outcome |
| L3 风险/买点层 | ⚠️ v2 生产运行；买点能力未证实 | TuShare QFQ 覆盖 35/35 codes、4,935 行；非 TuShare/mixed source fail-closed；cron 工作日 16:00 采集 |
| 定性评分 v2 | ✅ 全局生产读路径；覆盖扩展中 | `QUALITATIVE_V2_MODE=on`；35 股全部进入选择器，6 股 hybrid、29 股 v1 fallback。M5 36 股审计改为发布后独立研究，不阻塞安全读取 |
| 行情数据源 | ⚠️ TuShare-only，probe stale | 运行时 `a-stock-lib==0.4.1`；provider 失败即关闭；2026-07-27 probe 本身 PASS，但 cron 要求当日报告，2026-07-28 已转 stale |
| cron | ✅ 新时序已安装 | 16:00 QFQ、17:15 TuShare valuation、17:30 daily、17:45 acceptance、18:00 outcome；周六 10:00 financial/dividend |
| 质量门禁 | ✅ 全绿 | Phase 2 最终全仓 `1112 passed`；Ruff、format、mypy、pip check、shell syntax 与 `git diff --check` 通过 |

### 关键数据规模

- watchlist：35只（手动维护，固定池）
- Framework A 记录 1,996 条、Framework B 历史记录 73 条；predictions 合计 2,069 条（2026-07-27 只读查询）
- Framework B 生产写入暂停，仅 report-only 观察
- L3 v1 记录：1,393 条，其中通过 204 条、通过组 30d 已结案 124 条；L3 v2 共 385 条，
  其中 375 pass、10 reject；当前 QFQ 为 35×141 行，选择性仍待阶段一验证
- 最新 tracked accuracy report 生成于 2026-07-15；其中 post-fix A 30d 结案 735 条，L3 v1 pass 的 30d 已结案 71 条
- Phase 6 当前阻塞：2026-W30 首批 frozen cohort 已入组 7 条，当前结案 0/20、已结案周 0/3，7 条预计最早 2026-08-19 结案；门禁仍要求无 overdue
- 定性评分 v2：独立表 6 行，000963/002050/600036/600900/601088/603606 的 moat/market_pos 来自 v2，sentiment 因证据不足为 `NULL` 并回退 v1；其余 29 股完整回退 v1
- TuShare 三域 ingestion：398 个 completed runs、0 failed；最新 daily cycle
  `run_id=398`、`source_as_of=2026-07-27`、`changed_count=35`
- 历史 outcome versioned shadow：生产 1 个 immutable run、1,776 results（1,767 computed / 9 missing）、2,520 observations、3 表 6 触发器；legacy outcome 和 consumer 未切换

### 已知系统性偏差

- **2026-05-14 前**：gross_margin=NULL，pb_percentile 月度静态 → 均分约 34-40 分
- **2026-05-15 起**：两字段修复 → 均分约 44 分（系统性上移 4-5 分）
- 跨期比较须按 score_date 分层处理，Phase 4 optimizer 训练时注意

---

## 三、既有 Phase 能力记录

本节保留原 Phase 4–7 的设计和实施历史，但执行优先级已由 1.2 节三阶段收敛路线取代。
不得仅因下列旧 Phase 尚有未完成项，就绕过当前阶段退出条件继续投入工程。

### Phase 4：验证基础 [已完成 2026-06-26，进入持续观察]

**目标：** 积累足够 outcome 数据，验证现有评分因子的预测效力。

**完成状态：**

*继续开发允许条件：*
- [x] 30d 结案记录 ≥ 100 条：最新 tracked report 为 Framework A 953 条、post-fix 735 条。
- [x] accuracy-report 各信号层级 post-fix 样本已达到可观察规模。

*评分有效性结论：*
- [ ] hit_rate_vs_300 > 55% at strong 层级尚未满足。
- [ ] 若 strong 层级持续弱于基准且样本进一步扩大，再单独触发权重调整 spec；不得在 Phase 6 report-only 中顺手改权重。

> **注意**：Phase 4 的“允许继续开发”已满足，但“评分有效性优化”仍是持续观察项。60d/90d 是后验确认指标，不作为 Phase 6 启动阻塞。

**后续工作：**
- 保持 `daily` / `outcome-update` cron 正常运行。
- 每周查看 `pipeline.py accuracy-report` 数据趋势。
- 分层记录：2026-05-14 前（pre-fix）和 2026-05-15 起（post-fix）分数分布，**不得混合两组样本计算 hit_rate**。

---

### Phase 5：L3 风险/买点层 [工程接入完成；买点有效性回到收敛阶段一验证]

**目标：** 在现有评分基础上增加"入场时机确认"信号，解决最大缺口。

**2026-05-30 决策与实现：** 多 AI 辩论后确认 Phase 5 优先级高于 Framework B 复活。Framework B 保留到 Phase 6；当前已把 L3 做成可版本化、可回测、可推送过滤的独立层。

**授权边界：** 已确认允许新增 `predictions` 字段、修改 Telegram 推送、增加日线历史窗口读取，并把 L3 report 纳入 `accuracy-report`。仍禁止自动交易、改写历史评分、真实外部 API 测试和隐式启用 cron。

**设计原则：**
- L3 买点信号是**过滤层**，不修改 L1/L2 的 total_score，不能反向影响公司质量评分
- L3 使用趋势/动量类信号（均线、成交量、行业指数）作为**入场确认**，这是允许的；但这些信号只在 L3 层生效，不得渗入 L1/L2 评分逻辑（见第四节边界说明）
- v1 历史格式为 `entry_signal: int`（0/1/NULL）；自 2026-07-12 起生产主推权威门禁为评分 ≥ `buy_strong` AND `l3_v2_signal=1`
- 信号生效不改变 `weights_hash`，不影响 total_score 历史数据可比性
- **版本化约束**：L3 规则变更时须同步更新 `entry_signal_version`（字符串，格式 `v{N}`，存入 predictions 表），使回测可区分不同版本规则下的信号记录
- **空值语义**：旧记录为 `entry_signal=NULL AND entry_signal_version IS NULL`；L3 v1 已运行但不可计算为 `entry_signal=NULL AND entry_signal_version='v1'`；二者必须分开统计
- **报告约束**：`accuracy-report` 必须包含 L3 买点层 section，且将 `NULL`、`0`、`1` 分开统计；样本不足时不得输出确定性结论

**候选信号（按实现难度排序）：**

| 信号 | 数据来源 | 难度 | 说明 |
|------|---------|------|------|
| 60/120日均线位置 | AKShare 日线 | 低 | 价格站上均线 = 趋势改善 |
| 成交量变化 | AKShare 日线 | 低 | 近5日量能 vs 20日均量 |
| 板块景气信号 | AKShare 行业指数 | 中 | 行业指数 30d 涨跌幅 |
| 预期差标记 | Gemini 辅助分析 | 高 | 市场是否已定价公司优势 |

**实施结果：**
1. `lib/entry_signal.py` 新增 L3 v1 纯计算 seam，输入固定为归一化 `date/close/volume`
2. `pipeline.py` 在 daily 中读取 120+ 日线窗口、计算并写入 `entry_signal` / `entry_signal_version`
3. `telegram_push.py` 推送条件从 `score >= buy_strong` 改为 `score >= buy_strong AND entry_signal=1`
4. `predictions` 表新增 `entry_signal INT` 列和 `entry_signal_version TEXT` 列；旧记录保持 `NULL/NULL`
5. `accuracy-report` 增加 L3 买点层 section，区分 `NULL/NULL`、`NULL/v1`、`0/v1` 与 `1/v1`，样本不足时提示

以上 1~5 是 v1 历史实施结果。当前生产仍保留 v1 字段用于审计，但 Telegram 主推不再读取 `entry_signal=1`，而读取下文 Phase 3 的 `l3_v2_signal=1`。

**预期观测范围：** L3 过滤后，strong 信号从当前约 6/35 = 17% 降至 3-5%。这是产品/策略观察目标，不是 CI 通过条件；工程验收以字段语义、推送过滤和 accuracy-report 统计正确为准。

**项目文档：**
- Spec：`docs/specs/2026-05-30-phase5-l3-entry-signal-spec.md`
- Plan：`docs/plans/2026-05-30-phase5-l3-entry-signal-implementation-plan.md`

**2026-07-10 L3 v2 离线回测状态：**

- v2 spec：`docs/specs/2026-07-08-l3-v2-entry-signal-spec.md`
- 离线计划：`docs/plans/2026-07-08-l3-v2-offline-backtest-plan.md`
- 实现：`scripts/offline_l3_v2_backtest.py`
- 报告：`docs/reviews/2026-07-08-l3-v2-backtest-report.md`
- 复盘：`docs/reviews/2026-07-10-l3-v2-backtest-retro.md`
- 复审证据：`docs/reviews/agy-l3-v2-backtest-review/`

**2026-07-12 Phase 2（QFQ 采集）完成：**

- QFQ spec：`docs/specs/phase2-qfq-daily-bars-collector.md`
- 实现：`scripts/fetch_qfq_daily_bars.py`（BaoStock `adjustflag="2"`，`daily_bars` 表 `adjusted='qfq'`）
- 2026-07-22 更新：采集入口已从 BaoStock 切换至 TuShare（`scripts/fetch_qfq_daily_bars_tushare.py`，`tushare.pro_bar(adj="qfq")`），`daily_bars` 表 `adjusted='qfq'` 结构不变
- 采集结果：35/35 codes × 130 行回填（≥ 120 行门槛），全量验证 pass_strong
- cron：`00 16 * * 1-5` 每工作日 16:00 自动采集
- pipeline 变更：`a_stock_tracker/signals/l3_v2_pipeline.py` 新增 `_select_daily_rows()`，QFQ 优先（≥120 行 → pass_strong），回退 none-adjusted（→ pass_weak/QFQ_UNAVAILABLE）
- Decision gate：已清除 `NEED_QFQ`；v2 信号从下个工作日起写入生产

**2026-07-12 Phase 3（推送触发切换）完成：**

- spec：`docs/specs/phase3-l3-v2-push-trigger-switch.md`
- 变更：`telegram_push.py` 主推/备推查询条件 `entry_signal=1` → `l3_v2_signal=1`；NULL fail-closed 自动生效（SQLite NULL≠1）
- 测试：`tests/test_telegram_push.py` 新增 3 条 gate 测试（v2 pass/v2 reject/v2 null 三路由验证）；298/298 passed
- commit：`e080f15`

---

### Phase 6：多框架激活（L1 完备化）[report-only 被动观察]

**目标：** 为不同行业激活对应框架，解决"白酒用 A 框架不合适"的问题。

**当前状态（2026-07-27）：** Phase 6 仅做 report-only 被动观察。旧版 B label 跟踪因
每次选择 latest-A 而让到期日随 daily 后移，已由绑定固定 `source_a_prediction_id`
的 prospective cohort 替代。73 条 legacy B 记录只做回溯；W30/W31 已冻结 15 条、
覆盖 2 周，尚未自然结案。Framework B 生产写入未启用。

**本轮推进复盘：**
- 行情链路当前运行于 `a-stock-lib==0.4.1`：2026-07-23 已强切为 TuShare-only、失败即关闭；旧 fallback 仅保留在历史记录中。
- cron 已迁移到新 managed block；`READY_CRON` 仍是从零恢复行情依赖任务的门禁。
  2026-07-27 probe 本身 PASS，但严格当日 freshness 下已于 2026-07-28 stale。
- Phase 6 readiness 使用 prospective frozen cohort；滚动 latest-A 仅保留为 `[UNFROZEN-PREVIEW]`，不参与门禁。
- legacy 回溯显示真实 B 历史表现，但必须披露同日样本相关性，永不计入 prospective 门禁。
- 保持生产边界不变：未启用 `SUPPORTED_FRAMEWORKS` 的 B 写入，未新增 B predictions，未修改 `weights.json`，未改写历史 prediction/outcome 数据。
- 当前观察：W30 7 条、W31 8 条，共 15 条、2 周；预计约于 2026-08-19/26 自然结案，
  W32 仍需按既定自动流程冻结。满足 20 条/3 周门槛后也只进入人工 review。

**Phase 6 生产化前置条件：**
- post-fix Framework A 30d 结案样本 ≥ 100。
- watchlist 数据质量门槛通过：无缺失缓存、required 字段全部可接受、无 `cache_report_period` 缺失。
- Framework B 金融候选 dry-run 全覆盖：`scored_count == candidate_count` 且候选数 > 0。
- prospective B label 去重后自然结案样本 ≥ 20、覆盖至少 3 个已结案 cohort 周，且不存在 overdue/source 缺失风险。
- 上述条件满足后，仍先进入人工 report-only 审阅；生产写入需要单独计划、测试和明确授权。

**优先级：**

| 框架 | 行业 | 主估值轴 | 优先级 |
|------|------|---------|--------|
| B 银行/金融 | 农行/招行/国信/招商证 | PB < 近10年30%分位 | 高（已有73条历史数据） |
| E 消费 | 贵州茅台/五粮液等（如加入watchlist） | PEG < 1 或 PE < 近5年40%分位 | 中 |
| D 公用事业 | 长江电力/浙能/大唐 | 股息率 vs 国债溢价 > 200bps | 中（当前用A框架扭曲估值） |
| C 资源能源 | 中国神华/中国海油/紫金矿业 | 归一化FCF股息率 | 中 |
| F 科技 | 比亚迪/立讯精密/工业富联 | PS历史分位 / PEG | 低（盈利模式差异大） |

**各框架必需数据清单：**

| 框架 | 必需字段 | 数据来源 | 历史窗口 | 缺失处理 |
|------|---------|---------|---------|---------|
| B 银行 | pb_hist_monthly / roe_3y_avg / nim / npl_ratio / provision_coverage | AKShare + web | PB 近10年 | nim/npl 缺失 → data_quality 降级，不跳过 |
| D 公用 | pb_hist_monthly / dividend_yield / debt_ratio | AKShare | PB 近10年 | 股息率缺失 → 该维度0分，不终止 |
| C 资源 | pb_hist_monthly / dividend_yield / debt_ratio / gross_margin | AKShare | PB 近10年 | gross_margin 缺失 → 该维度0分 |
| E 消费 | pe_hist / gross_margin / roe_3y_avg / net_profit_growth | AKShare + 新浪 | PE 近5年 | **PE历史分位缺失 = L2核心字段缺失 → data_quality < 0.5 → InsufficientDataError** |
| F 科技 | ps_hist / gross_margin / net_profit_growth | AKShare | PS 近5年 | PS历史分位缺失同上 |

> E/F 框架的主估值轴（PE/PS历史分位）尚无对应 AKShare 接口，需要先验证数据可获取性，再启动框架实现。这是 E/F 被列为低/中优先级的主要原因。

**实施方案：**
1. 每周一 09:20 由 `run_framework_b_cohort_freeze.py` 自动执行 readiness → dry-run → 显式冻结；候选为 0、合同不一致或 readiness HOLD 时 fail-closed 告警，同周重跑必须幂等。
2. 继续运行 `daily` / `outcome-update`，让冻结 cohort 绑定的 A prediction 自然结案；禁止切换到 latest-A。
3. 每周查看 `accuracy-report` 的 legacy 与 prospective 两轨、Phase 6 readiness、阻塞项和下一步。
4. 每周复核 cron 日志和 `READY_CRON`；若 readiness 返回 `HOLD_CRON`，先处理行情链路，暂停 Phase 6 深化。
5. 若数据质量或 B dry-run 覆盖未满足，先修复字段来源、缓存或跳过原因。
6. prospective 已结案 ≥20、已结案周 ≥3 且 overdue=0 后，先写 `docs/reviews/YYYY-MM-DD-phase6-b-label-review.md`。
7. 条件满足后，再为 Framework B 生产化单独写 spec/implementation plan；计划必须覆盖 `weights.json`、`SUPPORTED_FRAMEWORKS`、watchlist/framework 映射、历史断层解释、测试和回滚策略。
8. 每个新框架上线前须通过完整测试用例（mock 外部行情源 + 边界值覆盖）。

**注意：** 多框架激活后，不同框架的 total_score 不可跨框架直接比较（D框架高股息股天然比F框架科技股分数高），accuracy-report 须按 framework 分层。

---

### Phase 7：选股宇宙扩展（历史设计；生产实现延后至收敛阶段三）

**目标：** 解决"35只固定股票宇宙太小"的根本问题，实现动态候选池。

**架构：**

```
全市场粗筛（月度，AKShare 批量）
  → 候选池（约 200-500 只，满足 ROE>12%、负债率<60%、市值>50亿）
    → Framework A/B/C/D/E/F 精筛（每周/每日）
      → 评分 ≥ 45 → 进入 watchlist_dynamic 表
        → L3 买点过滤 → 推送
```

**新增数据结构：**
- `watchlist_dynamic` 表：代码、名称、行业、首次入池日期、退出日期、退出原因
- `screening_log` 表：每次粗筛的时间戳、候选股数、入池数

**动态池生命周期规则：**
- **入池条件**：粗筛通过（ROE/负债率/市值） + 精筛 total_score ≥ 45
- **保留条件**：每月复核，total_score 仍 ≥ 40
- **退出条件**（满足其一）：① total_score 连续 4 周 < 40；② data_quality < 0.5 连续 2 次；③ 基本面触发红线（由 pipeline 写入退出原因）
- **冷却期**：退出后 60 天内不重新入池，防止评分抖动导致频繁进出
- 静态 watchlist（config.py）永久保留，不受动态池规则影响

**风险控制：** 扩大宇宙会显著增加 API 调用量（AKShare 频率限制），需要分批拉取 + 本地缓存 + 错误重试机制。建议先从 300 只做压力测试。

**前置条件：** Phase 5 买点层已实现（避免大宇宙产生大量噪音推送）+ Phase 6 至少完成目标行业的主要框架，或明确定义 Phase 7 运行期间仅用 Framework A 作为降级策略

---

## 四、不做什么（边界）

以下方向明确排除，不纳入演化计划：

| 方向 | 排除原因 |
|------|---------|
| 高频/日内交易 | 框架是基本面逻辑，日内信号与之矛盾 |
| 量化对冲策略 | 单账户个人投资，不需要对冲层 |
| 价格动量因子（纳入 L1/L2 评分） | 动量不影响公司价值评分。**例外**：均线、成交量可作为 L3 入场过滤信号（Phase 5），但不得修改 total_score |
| 独立舆情/NLP 数据源 | 当前阶段不引入独立 NLP 舆情层；Gemini 定性提供有限的情绪辅助，不是完整舆情覆盖 |
| 实盘自动下单 | 系统输出信号，人工执行，不做自动交易 |

---

## 五、跨阶段约束（所有 Phase 必须遵守）

1. **predictions 核心评分字段历史数据不可改写**：评分逻辑变更只影响新记录，不得改写既有行的 `quant_score`、`total_score`、`weights_hash` 等核心评分字段；L3 / `entry_signal` 等衍生信号 metadata 会随其自身逻辑演进对既有行回填或修正，这是有意设计，不属于评分口径改写
2. **weights_hash 完整性**：weights.json 的 frameworks 子树变更必须被 pipeline 检测到（hash 变更会退出）
3. **alpha_*d 不直接写入**：Generated Column，任何 INSERT/UPDATE 都不能包含此列
4. **AKShare mock 原则**：所有测试用 monkeypatch，不发真实网络请求
5. **Sheets sync 不阻断 daily**：Google Sheets 是展示层，失败只记 WARNING
6. **文档先行**：Phase N 实施前，先在本文档中将对应 Phase 标记为 `[实施中]`，完成后标记为 `[已完成 YYYY-MM-DD]`
7. **PM 控制面**：`docs/project-status.md` 是当前 spec 台账；压缩上下文或跨会话交接时优先读取该文件确认 active phase、阻塞项和下一检查点。

---

## 六、版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-05-15 | 初始版本，基于 REPAIR-PLAN v2.0 完成后的系统状态建立基线 |
| v1.1 | 2026-05-15 | Codex 独立审查后修复 8 处问题：Phase 4 里程碑分层、L3 版本化约束、Phase 6 数据依赖表、Phase 7 前置条件补全、动态池退出规则、边界说明澄清 |
| v1.2 | 2026-05-30 | 记录多 AI 辩论结论：Phase 5 L3 买点层优先，Framework B 后置；确认 L3 字段、Telegram、日线窗口、accuracy-report 授权边界 |
| v1.3 | 2026-05-30 | Phase 5 L3 买点层实现完成：schema、纯计算 seam、daily 写入、Telegram 过滤、accuracy-report L3 section |
| v1.4 | 2026-06-05 | Phase 6 readiness 报告增强：明确生产化阻塞项与下一步，保持 Framework B report-only，不启用生产写入 |
| v1.5 | 2026-06-21 | 补记 2026-06-09~06-16 行情数据源迁移：AKShare/东方财富行情入口禁用，迁移到 Tushare 主源 + BaoStock degraded fallback（基本面/估值/财报抓取不受影响，仍用 AKShare）；2026-06-21 重新探测 `scripts/probe_tushare_market_data.py` 结果 PASS，`check_market_data_readiness.py` 转为 READY，此前 06-09 探测因 Tushare 限频(1次/小时)误报 FAIL 已更新为最新通过记录 |
| v1.6 | 2026-06-26 | 同步真实恢复状态：`a-stock-lib==0.1.2`、隔离 BaoStock fallback、真实 probe/backfill/daily、`READY_CRON`、managed cron block；Phase 4 标记完成并转持续观察，Phase 6 明确为 report-only 深化，不启用 Framework B 生产写入；新增 `docs/project-status.md` 作为 PM/spec 台账 |
| v1.7 | 2026-07-04 | agy 工程审查修复（Batch A+B，11 commits）：DB per-stock SAVEPOINT 隔离、spot_em 重试计数器（连续3次才今日锁定）、Gemini 退避重试+过期缓存降级、subprocess stderr 转发、cache._ensure_columns SQL identifier allowlist、_OUTCOME_WINDOWS frozenset、agent_reviewer 接入真实 Gemini REST API（_fake_review_fallback 降级）；新增 cmd_init/cmd_weekly/cmd_outcome_update 测试覆盖；测试基线 225 passed, 1 skipped（含 3 个新测试模块补丁） |
| v1.8 | 2026-07-12 | Phase 5 L3 v2 Phase 2+3 完成：QFQ 采集（35/35×130行，pass_strong 激活）+ 推送触发切换至 l3_v2_signal=1；Framework A 倒置诊断（Q5 avg_alpha=-9.91%，根因=截面校准偏差+11支伪复制，不调权重）；agy 投资视角审查（持有期错配+价值风格轮出；Priority 1=延伸60d/90d评估）；Tushare probe 刷新（上次 2026-07-02 已过期）；daily+outcome-update cron 正式恢复（market-data-backfill ok=35） |
| v1.9 | 2026-07-15 | 对齐 L3 v2 已上线事实、live DB/报告样本、B label 日期和 qualitative v2 MILESTONE-002 进度；记录 readiness 因 probe stale 转 HOLD、weekly-PM failure-marker 误报，以及 mypy/Ruff format 基线漂移，明确下一步 P0/P1 顺序。 |
| v1.10 | 2026-07-15 | 完成 v1.9 识别的 P0/P1：当天 probe 全 PASS 并恢复 READY_CRON/managed cron；修复 weekly-PM 中文零失败与降级 WARNING 分级；mypy 24 errors 清零并完成 Ruff format 全库基线；同步最新 accuracy report 与 B label 日期。 |
| v1.11 | 2026-07-15 | 完成定性评分 v2 MILESTONE-002 fixture-first 合同：新增静态 response schema、版本化 prompt、namespace/hash/rationale/sentiment 合同修复与回归测试；AGY 只读审查 PASS，保持 Gemini/DB/pipeline/cron/Telegram 生产路径不变。 |
| v1.12 | 2026-07-15 | 修复 MILESTONE-002 生产就绪审查的全部 P1/P2：集中合同常量，typed context 双边界复验，拒绝 NaN/Infinity 与非 canonical 日期，统一 64 项及字符串/JSON/prompt 资源上限；145 项定向、450 项全量测试和 AGY 严格复审 PASS，生产路径仍不变。 |
| v1.13 | 2026-07-15 | 完成 MILESTONE-003 文件型 shadow seam：新增隔离 Gemini client、显式 CLI、0600 JSONL artifact、并发锁/同 hash 去重、bounded retry/响应/错误分类和可选 legacy comparison；两次空 packet 受控 Gemini smoke 通过，重复 CLI 运行未发起第三次调用；478 项全量门禁与 AGY 最终只读审查 PASS，生产 DB/pipeline/cron/Telegram 保持不变。 |
| v1.14 | 2026-07-15 | 完成 MILESTONE-004 v1.1 预注册与离线审计工具链：冻结抽样、source-aware corpus、create-only hash chain、隔离双 reviewer、裁决和 coverage API；严格审查修复后 554 项全量门禁通过，真实审计与 Reviewer 尚未执行。 |
| v1.15 | 2026-07-15 | 记录 MILESTONE-004 静态输入执行授权：仅允许带 provenance 的只读 dataset 冻结 frame/sample/corpus；当前阻塞改为 dataset 未提供，Reviewer、生产 DB/live provider/pipeline 和 MILESTONE-005/006 仍未授权。 |
| v1.16 | 2026-07-15 | 记录 M4 两次一次性 audit-only frame 导出结果：Tushare 受账户频率阻断，AKShare 受 SZSE 强制 HTTPS transport 阻断；均未发布 package，incoming 为空，Reviewer 仍未授权。 |
| v1.17 | 2026-07-15 | 记录第三次获批 audit-only 导出：AKShare 限定三函数，SZSE 改直连官方 HTTPS 接口；当前环境仍发生 SZSE `ConnectionError`，未降级 HTTP、未发布 package。 |
| v1.18 | 2026-07-16 | 通过隔离 Microsoft Playwright MCP/Chromium 下载并只读封存两份语义一致的 2026-07-15 SZSE XLSX、provenance 和 SHA-256；仍缺同日东方财富总市值及申万成分，不冻结 frame/sample。 |
| v1.19 | 2026-07-16 | 新增并离线验证 2026-07-15 历史混合导出器，复用只读 SZSE 包并限定 Tushare `daily_basic`、SSE 与 31 个申万查询；真实执行在 SWS 前受 Tushare 分钟频控阻断，未发布 frame/sample。 |
| v1.20 | 2026-07-16 | 10:42 获批重试 historical-hybrid 导出；Tushare 明确返回 `daily_basic` 每小时一次账户频控，执行在 SWS 前 fail closed，临时目录清理且无 frame/sample 发布。 |
| v1.21 | 2026-07-16 | 12:05 historical-hybrid 请求通过小时频控后因 `count` 与当前页行数不同而 fail closed；离线修正为严格验证非负 count、`has_more=false` 并记录 provenance，仍未触达 SWS 或发布 frame/sample。 |
| v1.22 | 2026-07-16 | 13:34 historical-hybrid 请求返回 5,525 行及 `count=0`，确认零为未知总数哨兵；离线校验允许零或不小于当前页的 count，继续强制 `has_more=false`，未触达 SWS。 |
| v1.23 | 2026-07-16 | 完成 M4 capture-first exporter：拆分 capture/recover/assemble，新增机器授权、即时 raw/receipt、complete/incomplete 隔离、resume/TOCTOU/并发锁和纯离线确定性组装；13:34 旧响应不可恢复，新 live capture 仍待单独授权。 |
| v1.24 | 2026-07-16 | 执行首次机器授权 capture-first attempt：保留 Tushare raw-only 限频响应和有效 SSE receipt，首个 SWS 请求严格 TLS `SSLError`；manifest 封存 31 项 missing 并通过离线重验，未重试或组装。 |
| v1.25 | 2026-07-16 | 单次 strict-TLS 诊断确认 SWS 服务端只发送有效叶证书、缺 GeoTrust/DigiCert 中间证书，验证 code 20；不接受 AKShare `verify=False`，下一步等待服务端修复或单独授权官方中间证书/静态包路径。 |
| v1.26 | 2026-07-18 | M4 轻量 builder 35/35 请求完成，冻结 4,694 行 frame、1,170 行 exclusions 和 36 股 12-cell sample；sample SHA-256 为 `b278a7…d7635d`，不等同 coverage 或 bundle。 |
| v1.27 | 2026-07-18 | M5 synthetic fixture-first 编排、blind-reference seal、Gemini early-stop、support audit 和可复验 aggregate report 完成；路线压缩为数据就绪 Sprint + 模型执行 Sprint，D1 来源授权待批准。 |
| v1.28 | 2026-07-19 | 定性评分 v2 完成可回滚生产闭环：东方电缆和指定五股形成 6 行合法 partial v2，全局模式切到 `on`，35 股选择器只读验证为 6 股 hybrid + 29 股 v1 fallback；M5 代表性/agreement 审计改为发布后独立研究。 |
| v1.29 | 2026-07-19 | 新增只读生产验收与回滚检查：tracked baseline 绑定 6 股逐维分数和截至 2026-07-17 的历史评分 seal；自动输出 PASS/ROLLBACK，并可在自然 daily 后复验 35 股 prediction 与 v2 adoption 日志。 |
| v1.30 | 2026-07-23 | 行情链路强切 TuShare-only：默认/backfill provider 失败即关闭；35 股 QFQ 通过 TuShare API 全量补至各 139 行；生产活动行情与历史审计清除非 TuShare 数据；删除旧采集/双源对账入口，predictions 完整 hash 不变。 |
| v1.31 | 2026-07-24 | 同步 TuShare 三域、历史 outcome versioned shadow、Framework B prospective cohort 与 Phase 6 report-only 当前基线。 |
| v1.32 | 2026-07-28 | 澄清产品目标为 A 股选股与买入决策支持；冻结非关键 M4/M5 与 shadow 扩展；新增“证明现有系统→离线扩大宇宙→生产决策闭环”三阶段收敛路线及防过度治理约束。 |
