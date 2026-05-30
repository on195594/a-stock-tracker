# a-stock-tracker 进化路线图

**版本：** v1.2
**基线日期：** 2026-05-30
**文档定位：** 系统演化的顶层规划文档。所有后续 Phase 的修改、补丁、设计决策均以本文档为基线。若实施中发现偏差，先更新本文档，再改代码。

---

## 一、目标定义

将 a-stock-tracker 从"对固定 watchlist 打分并记录"演化为**三层选股系统**：

| 层次 | 问题 | 当前状态 |
|------|------|---------|
| L1 好公司 | 这家公司值不值得持有？ | ✅ 已成型（ROE/增速/负债率/毛利率/Gemini定性） |
| L2 好价格 | 当前估值有没有安全边际？ | 🔶 部分成型（PB分位日度化，PE分位缺失） |
| L3 合适买点 | 现在是不是好的入场时机？ | ❌ 基本缺失（无均线、无板块景气、无催化剂信号） |

**系统不追踪价格动量**：框架是价值投资逻辑，股价下跌+基本面不变 = 估值改善 = 评分可能上升。这是设计决策，不是缺陷。

---

## 二、当前系统基线（2026-05-15 快照）

### 能力盘点

| 模块 | 状态 | 说明 |
|------|------|------|
| Framework A 评分 | ✅ 正常 | 满分 80 分（量化 60 + Gemini 定性 20），今日均分 44.3 |
| Framework B 评分 | ⏸ 暂停 | 73 条历史记录保留，SUPPORTED_FRAMEWORKS={"A"}，待重启 |
| 每日 PB 分位 | ✅ 2026-05-15 修复 | current_pb = 收盘价/bps，ranked in pb_hist_monthly（~731点） |
| 毛利率字段 | ✅ 2026-05-15 修复 | 新浪利润表自动计算，金融行业跳过 |
| Gemini 定性评分 | ✅ 正常 | 30天缓存，all-or-nothing fallback |
| Telegram 推送 | ✅ 正常 | ≥55分触发，今日 strong 信号 6 只 |
| Outcome 追踪 | 🔶 积累中 | 326条记录，30d 结案 0 条（系统运行 < 1个月） |
| 多框架支持 | ❌ 仅 A | B/C/D/E/F 框架逻辑未实现 |

### 关键数据规模

- watchlist：35只（手动维护，固定池）
- predictions 记录：326条（2026-04-21 至今）
- 30d 结案目标：≥100条（预计 2026-06 中）

### 已知系统性偏差

- **2026-05-14 前**：gross_margin=NULL，pb_percentile 月度静态 → 均分约 34-40 分
- **2026-05-15 起**：两字段修复 → 均分约 44 分（系统性上移 4-5 分）
- 跨期比较须按 score_date 分层处理，Phase 4 optimizer 训练时注意

---

## 三、进化路线（四个阶段）

### Phase 4：验证基础（当前 → 2026-06 中）

**目标：** 积累足够 outcome 数据，验证现有评分因子的预测效力。

**里程碑（两类，独立管理）：**

*继续开发允许条件（满足即可启动 Phase 5 开发，不依赖评分有效性）：*
- [ ] 30d 结案记录 ≥ 100 条（**仅统计 2026-05-15 修复后**的记录，预计 2026-06-15 前后）
- [ ] accuracy-report 各信号层级 post-fix 样本 ≥ 20 条

*评分有效性结论条件（决定是否调整权重，可晚于 Phase 5 启动）：*
- [ ] hit_rate_vs_300 > 55% at strong 层级，post-fix 样本 ≥ 20 条 → 初步验证有效
- [ ] 或明确验证无效（strong 层级 hit_rate_vs_300 ≤ 45%，样本 ≥ 30 条）→ 触发权重调整流程

> **注意**：60d/90d 是后验确认指标（上线更晚），不作为 Phase 4 主触发条件，但应在 accuracy-report 中持续观察趋势一致性。

**工作内容：**
- 无需代码改动，保持 daily cron 正常运行
- 每周查看 `pipeline.py accuracy-report` 数据趋势
- 分层记录：2026-05-14 前（pre-fix）和 2026-05-15 起（post-fix）分数分布，**不得混合两组样本计算 hit_rate**

**触发下一阶段的条件：** 满足"继续开发允许条件"即可启动 Phase 5，无需等待评分有效性结论

---

### Phase 5：买点层（L3 补全）[spec/plan 已启动 2026-05-30]

**目标：** 在现有评分基础上增加"入场时机确认"信号，解决最大缺口。

**2026-05-30 决策：** 多 AI 辩论后确认 Phase 5 优先级高于 Framework B 复活。Framework B 保留到 Phase 6；当前先把 L3 做成可版本化、可回测、可推送过滤的独立层。

**授权边界：** 已确认允许新增 `predictions` 字段、修改 Telegram 推送、增加日线历史窗口读取，并把 L3 report 纳入 `accuracy-report`。仍禁止自动交易、改写历史评分、真实外部 API 测试和隐式启用 cron。

**设计原则：**
- L3 买点信号是**过滤层**，不修改 L1/L2 的 total_score，不能反向影响公司质量评分
- L3 使用趋势/动量类信号（均线、成交量、行业指数）作为**入场确认**，这是允许的；但这些信号只在 L3 层生效，不得渗入 L1/L2 评分逻辑（见第四节边界说明）
- 格式：`entry_signal: int`（0/1），只有评分 ≥ 阈值 AND `entry_signal=1` 时才触发推送
- 信号生效不改变 `weights_hash`，不影响 total_score 历史数据可比性
- **版本化约束**：L3 规则变更时须同步更新 `entry_signal_version`（字符串，格式 `v{N}`，存入 predictions 表），使回测可区分不同版本规则下的信号记录
- **空值语义**：旧记录 `entry_signal=NULL` 表示"规则尚未实装"，与 `entry_signal=0`（实装后主动不通过）含义不同，回测时必须过滤 NULL 行
- **报告约束**：`accuracy-report` 必须包含 L3 买点层 section，且将 `NULL`、`0`、`1` 分开统计；样本不足时不得输出确定性结论

**候选信号（按实现难度排序）：**

| 信号 | 数据来源 | 难度 | 说明 |
|------|---------|------|------|
| 60/120日均线位置 | AKShare 日线 | 低 | 价格站上均线 = 趋势改善 |
| 成交量变化 | AKShare 日线 | 低 | 近5日量能 vs 20日均量 |
| 板块景气信号 | AKShare 行业指数 | 中 | 行业指数 30d 涨跌幅 |
| 预期差标记 | Gemini 辅助分析 | 高 | 市场是否已定价公司优势 |

**实施方案：**
1. `pipeline.py` 新增 `_compute_entry_signal(code, data)` 函数（纯计算，不写 DB）
2. `telegram_push.py` 新增 L3 过滤逻辑：推送条件从 `score >= 55` 改为 `score >= 55 AND entry_signal=1`
3. `predictions` 表新增 `entry_signal INT` 列（NULL/0/1）和 `entry_signal_version TEXT` 列
4. `get_db()` 建表 DDL 同步更新，INSERT 语句包含新列

**成功标准：** L3 过滤后，strong 信号从当前约 6/35 = 17% 降至 3-5%，精度提升

**项目文档：**
- Spec：`docs/specs/2026-05-30-phase5-l3-entry-signal-spec.md`
- Plan：`docs/plans/2026-05-30-phase5-l3-entry-signal-implementation-plan.md`

---

### Phase 6：多框架激活（L1 完备化）

**目标：** 为不同行业激活对应框架，解决"白酒用 A 框架不合适"的问题。

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
1. 在 `weights.json["frameworks"]` 下补充各框架 breakpoints（含新估值字段）
2. `scorer.py` 的 `SUPPORTED_FRAMEWORKS` 逐步添加新框架（B 先，D/C 次之，E/F 最后）
3. `config.py` 的 watchlist 条目增加 `framework` 字段（默认 "A"），覆盖自动推断
4. Framework B 最先重启（结构已有，历史数据已有 73 条，验证新旧评分连续性后加回 SUPPORTED_FRAMEWORKS）
5. 每个新框架上线前须通过完整测试用例（mock AKShare + 边界值覆盖）

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

---

## 六、版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-05-15 | 初始版本，基于 REPAIR-PLAN v2.0 完成后的系统状态建立基线 |
| v1.1 | 2026-05-15 | Codex 独立审查后修复 8 处问题：Phase 4 里程碑分层、L3 版本化约束、Phase 6 数据依赖表、Phase 7 前置条件补全、动态池退出规则、边界说明澄清 |
| v1.2 | 2026-05-30 | 记录多 AI 辩论结论：Phase 5 L3 买点层优先，Framework B 后置；确认 L3 字段、Telegram、日线窗口、accuracy-report 授权边界 |
