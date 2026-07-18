# a-stock-tracker 进化路线图

**版本：** v1.14
**基线日期：** 2026-07-15
**文档定位：** 系统演化的顶层规划文档。所有后续 Phase 的修改、补丁、设计决策均以本文档为基线。若实施中发现偏差，先更新本文档，再改代码。

---

## 一、目标定义

将 a-stock-tracker 从"对固定 watchlist 打分并记录"演化为**三层选股系统**：

| 层次 | 问题 | 当前状态 |
|------|------|---------|
| L1 好公司 | 这家公司值不值得持有？ | ✅ 已成型（ROE/增速/负债率/毛利率/Gemini定性） |
| L2 好价格 | 当前估值有没有安全边际？ | 🔶 部分成型（PB分位日度化，PE分位缺失） |
| L3 合适买点 | 现在是不是好的入场时机？ | ✅ v1 保留审计；v2 已完成 QFQ 生产接入并成为 Telegram 主推门禁 |

**系统不追踪价格动量**：框架是价值投资逻辑，股价下跌+基本面不变 = 估值改善 = 评分可能上升。这是设计决策，不是缺陷。

---

## 二、当前系统基线（2026-07-15 快照）

### 能力盘点

| 模块 | 状态 | 说明 |
|------|------|------|
| Framework A 评分 | ✅ 正常 | 生产写入框架仍仅 A；2026-07-14 `daily` 写入 35 条，live DB 共 1681 条 A 记录 |
| Framework B 评分 | ⏸ report-only | 73 条历史记录保留，`SUPPORTED_FRAMEWORKS={"A"}`；B label 自然结案样本不足 `0/20`，最早 2026-08-13 后复核，不启用生产写入 |
| 每日 PB 分位 | ✅ 正常 | current_pb = 收盘价/bps，ranked in pb_hist_monthly；当前 watchlist PB 日度可计算 35/35 |
| 毛利率字段 | ✅ 正常 | 基本面缓存 35/35，金融行业 gross_margin 不适用按规则跳过 |
| Gemini 定性评分 | ✅ 正常 | 30天缓存，退避重试（429/5xx，最多3次），过期缓存降级，all-or-nothing fallback；当前缓存存在 35/35 |
| Telegram 推送 | ✅ v2 门禁已上线 | 主推条件为 ≥ buy_strong 且 `l3_v2_signal=1`；v2 为 0/NULL 的高分股进入候补 |
| Outcome 追踪 | ✅ 最近运行正常 | live DB 中 Framework A 30d/60d 结案 953/253；90d 尚无结案 |
| L3 买点层 | ✅ v2 Phase 2+3 完成；选择性待观察 | QFQ 覆盖 35/35 codes、4585 行；2026-07-13/14 共写入 70 条 v2 记录且全部 pass；cron 工作日 16:00 采集 |
| 定性评分 v2 | 🔶 两 Sprint 推进中 | M4 已生成 4,694 frame/36 sample；M5 synthetic fixture-first 完成。当前先做数据 Sprint 的 corpus/coverage/source approval/real bundle，再以 exact bundle SHA 申请模型 Sprint |
| 行情数据源 | ✅ `READY_CRON` | `a-stock-lib==0.2.0`；2026-07-15 probe 的 daily/index/calendar/close cross-check 全部 PASS |
| cron | ✅ 已按门禁重新安装 | weekly/weekly-PM/QFQ/daily/outcome 五项 managed cron 均已确认 |
| 质量门禁 | ✅ 全绿 | `859 passed`；Ruff lint/format、mypy、CLI help、`git diff --check` 与固定 sample SHA-256 复验全部通过 |

### 关键数据规模

- watchlist：35只（手动维护，固定池）
- Framework A 记录：1681 条；live DB 30d/60d 结案：953/253 条（2026-07-15 只读查询）
- Framework B 历史记录：73 条；生产写入暂停，仅 report-only 观察
- L3 v1 记录：1078 条，其中通过 148 条、30d 已结案 350 条；L3 v2 记录 70 条且 70/70 pass，两天 strong 均为 18/18 通过门禁，选择性尚未得到证明
- 最新 tracked accuracy report 生成于 2026-07-15；其中 post-fix A 30d 结案 735 条，L3 v1 pass 的 30d 已结案 71 条
- Phase 6 当前阻塞：B label 已结案样本不足 `0/20`，最早可评估日期 `2026-08-13`

### 已知系统性偏差

- **2026-05-14 前**：gross_margin=NULL，pb_percentile 月度静态 → 均分约 34-40 分
- **2026-05-15 起**：两字段修复 → 均分约 44 分（系统性上移 4-5 分）
- 跨期比较须按 score_date 分层处理，Phase 4 optimizer 训练时注意

---

## 三、进化路线（四个阶段）

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

### Phase 5：买点层（L3 补全）[v1 已完成；v2 Phase 2+3 已完成]

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
- 采集结果：35/35 codes × 130 行回填（≥ 120 行门槛），全量验证 pass_strong
- cron：`00 16 * * 1-5` 每工作日 16:00 自动采集
- pipeline 变更：`lib/l3_v2_pipeline.py` 新增 `_select_daily_rows()`，QFQ 优先（≥120 行 → pass_strong），回退 none-adjusted（→ pass_weak/QFQ_UNAVAILABLE）
- Decision gate：已清除 `NEED_QFQ`；v2 信号从下个工作日起写入生产

**2026-07-12 Phase 3（推送触发切换）完成：**

- spec：`docs/specs/phase3-l3-v2-push-trigger-switch.md`
- 变更：`telegram_push.py` 主推/备推查询条件 `entry_signal=1` → `l3_v2_signal=1`；NULL fail-closed 自动生效（SQLite NULL≠1）
- 测试：`tests/test_telegram_push.py` 新增 3 条 gate 测试（v2 pass/v2 reject/v2 null 三路由验证）；298/298 passed
- commit：`e080f15`

---

### Phase 6：多框架激活（L1 完备化）[report-only 深化中]

**目标：** 为不同行业激活对应框架，解决"白酒用 A 框架不合适"的问题。

**当前状态（2026-07-15）：** Phase 6 仍为 report-only 深化期。最新 tracked `accuracy-report` 显示 B label `0/20`、最早 2026-08-13 可评估；`docs/plans/2026-06-26-phase6-report-only-next-steps.md` 仍是执行边界。Framework B 生产写入未启用。当日 probe/cron 已恢复 READY，weekly PM 误报和质量基线漂移已修复。

**本轮推进复盘：**
- 行情链路已恢复并升级到 `a-stock-lib==0.2.0`：Tushare 主源 + 隔离 BaoStock degraded fallback；真实 probe/backfill/daily 均有成功记录。
- cron 已恢复为 managed block；`READY_CRON` 是 daily/outcome-update 恢复门禁。2026-07-15 probe 全部通过，managed cron 已按 runbook 重新安装。
- 已把 Phase 6 readiness 从单一结论扩展为可执行检查清单，明确展示 post-fix A 框 30d 结案、数据质量、Framework B 金融候选 dry-run 覆盖、B label 已结案样本和 overdue outcome 风险。
- 保持生产边界不变：未启用 `SUPPORTED_FRAMEWORKS` 的 B 写入，未新增 B predictions，未修改 `weights.json`，未改写历史 prediction/outcome 数据。
- 当前阻塞：B label 自然结案样本不足 `0/20`，最早可评估日期 `2026-08-13`；在此之前只做 report-only 观察和 cron 稳定性复核。

**Phase 6 生产化前置条件：**
- post-fix Framework A 30d 结案样本 ≥ 100。
- watchlist 数据质量门槛通过：无缺失缓存、required 字段全部可接受、无 `cache_report_period` 缺失。
- Framework B 金融候选 dry-run 全覆盖：`scored_count == candidate_count` 且候选数 > 0。
- B label 非金融质量候选自然结案样本 ≥ 20，且不存在到期但 outcome 为空的 overdue 风险。
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
1. 继续运行 `daily` / `outcome-update`，让 post-fix A 框、L3 和 B label 样本自然结案。
2. 每周查看 `accuracy-report` 的 Phase 6 readiness、阻塞项和下一步，不根据样本不足报告做生产化判断。
3. 每周复核 cron 日志和 `READY_CRON`；若 readiness 返回 `HOLD_CRON`，先处理行情链路，暂停 Phase 6 深化。
4. 若数据质量或 B dry-run 覆盖未满足，先修复字段来源、缓存或跳过原因。
5. B label 已结案样本 ≥ 20 且 overdue 风险为 0 后，先写 `docs/reviews/YYYY-MM-DD-phase6-b-label-review.md`。
6. 条件满足后，再为 Framework B 生产化单独写 spec/implementation plan；计划必须覆盖 `weights.json`、`SUPPORTED_FRAMEWORKS`、watchlist/framework 映射、历史断层解释、测试和回滚策略。
7. 每个新框架上线前须通过完整测试用例（mock AKShare + 边界值覆盖）。

**注意：** 多框架激活后，不同框架的 total_score 不可跨框架直接比较（D框架高股息股天然比F框架科技股分数高），accuracy-report 须按 framework 分层。

---

### Phase 7：选股宇宙扩展

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

1. **predictions 表历史数据不可改写**：任何评分逻辑变更只影响新记录，旧记录保留原始值
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
