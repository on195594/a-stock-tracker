---
title: L3 v2 Entry Signal Spec
status: draft
created: 2026-07-08
updated: 2026-07-08
owner: lin / Hermes
risk_tier: spec-first
review_source: docs/reviews/agy-l3-v2-evidence/stdout.md
---

# L3 v2 买点层 Spec

## 1. 请求改写

Original request:
> 请根据 agy 的审查结论帮我写 v2 spec。

Clear prompt:
> 基于 AGY 对 L3 v2 买点规则的 `REQUEST_CHANGES` 审查结论，为 `a-stock-tracker` 编写一份项目内正式 spec。该 spec 只定义 L3 v2 的目标、非目标、数据契约、状态语义、规则候选、离线回测口径、Telegram 文案边界、测试验收、回滚和停止条件；不得直接修改生产 L3 代码、生产 DB、cron 或 Telegram 推送逻辑。

What changed:
- 明确本次只写 spec，不实现代码。
- 明确 v2 必须先解决 AGY 提出的 qfq/未来函数/大盘过滤边界问题。
- 明确成功标准是形成可审查、可回测、可 TDD 派生的开发契约。

## 2. 背景与问题定义

L3 v1 当前作为 `total_score >= buy_strong AND entry_signal = 1` 的 Telegram 推送过滤层，规则为：

```text
close > MA60
AND close > MA120
AND volume_5d_avg > volume_20d_avg
```

v1 工程设计上合理：它不污染 `total_score`，有 `entry_signal_version='v1'`，并能过滤掉部分均线未修复的高分股票。

但 v1 不足以称为“强买点”：

1. 它只判断趋势是否站上均线，不判断是否低吸、突破、追高或临近压力位。
2. 量能门槛过弱，`vol5 > vol20` 可能只高出 1% 也触发。
3. 使用 `daily_bars.adjusted='none'` 的不复权价格，可能扭曲长江电力等高股息股票除权后的 MA60/MA120。
4. 已结案 30d 后验观察显示 v1 pass 样本表现较差，但样本存在连续日期重复，不能直接当独立样本定罪。
5. Telegram 文案“买点触发”过强，容易把趋势过滤误读为高赔率买点。

AGY 审查结论为 `REQUEST_CHANGES`：**不得立即修改生产代码；必须先写 spec 并做离线回测。**

## 3. 目标

### G1. 把 L3 从二值趋势过滤升级为强弱分层信号

L3 v2 应区分：

- 强买点：趋势、量能、突破/位置和市场环境同时较好。
- 弱信号：趋势基本修复，但量能、位置、市场或数据质量不足以强推。
- 拒绝：趋势或关键规则不满足。
- 不可用：数据不足、过期、缺字段或数据契约不满足。

### G2. 保持评分体系可比性

L3 v2 不得改变：

- `total_score`
- `quant_score`
- `weights_hash`
- `framework`
- 历史 outcome 字段
- Framework A/B/C 等框架生产边界

### G3. 先验证再上线

v2 必须先以离线回测 / report-only 形式验证，不能直接替代 v1 控制 Telegram 主推。

### G4. 修正文案语义

Telegram 和报告必须避免把 `pass_weak` 或未经回测验证的 v2 信号写成“强买点”。

## 4. 非目标

本 spec 不授权以下事项：

1. 自动交易、下单、账户或持仓写入。
2. 直接修改生产 cron、systemd、Telegram bot 配置或外部服务。
3. 直接批量 `UPDATE predictions` 历史评分、收益、`total_score` 或 `weights_hash`。
4. 未经 spec/回测/TDD 直接修改 `lib/entry_signal.py` 生产路径。
5. 为 v2 引入新第三方依赖；如确有必要，需单独讨论并更新 `requirements.txt`。
6. 启用 Framework B/C/D/E/F 生产写入。
7. 把 v2 的任一规则反向并入 L1/L2 评分。
8. 把一次 AGY 审查或本 spec 本身视为生产上线批准。

## 5. 当前基线

- Project root: `/home/lin/a-stock-tracker`
- v1 spec: `docs/specs/2026-05-30-phase5-l3-entry-signal-spec.md`
- v1 implementation: `lib/entry_signal.py`
- DB schema owner: `lib/cache.py`
- pipeline owner: `pipeline.py`
- Telegram owner: `telegram_push.py`
- report owner: `pipeline.py accuracy-report` 相关逻辑
- AGY review evidence: `docs/reviews/agy-l3-v2-evidence/stdout.md`

Known constraints:

- `predictions.entry_signal` 当前为 `INTEGER NULL`。
- `predictions.entry_signal_version` 当前用于区分 v1/v2 规则语义。
- `entry_signal=NULL AND entry_signal_version IS NULL` 表示 pre-L3 历史记录。
- `entry_signal=NULL AND entry_signal_version='v1'` 表示 v1 已运行但不可计算。
- 任何 v2 改动必须保留版本化和历史可解释性。
- 测试不得发真实 AKShare / Telegram / Gemini / Google Sheets 网络请求。

## 6. Requirements

### 6.1 状态语义

- REQ-001: L3 v2 必须定义 `pass_strong`、`pass_weak`、`reject`、`unavailable` 四类状态。
- REQ-002: L3 v2 必须写入或派生 `entry_signal_version='v2'`，不得与 v1 样本混算。
- REQ-003: v2 的强/弱语义必须能在 DB、accuracy-report 和 Telegram 文案中被区分。
- REQ-004: v2 必须保留 `entry_signal IS NULL` 表示不可计算，而不是把数据缺失默默记为拒绝。

### 6.2 数据契约

- REQ-005: v2 回测和任何 `pass_strong` 生产候选必须使用前复权 qfq 价格；qfq 初始方案为“从 Tushare 读取 `daily` + `adj_factor`，在离线脚本或 provider 边界内计算 qfq close/high/low/open”，不得假设现有 `a_stock_lib==0.2.0` 已支持 qfq。
- REQ-006: v2 不得在不复权 `adjusted='none'` 价格上输出 `pass_strong`。若 qfq 不可用，最多输出 `pass_weak` 并附 reason=`QFQ_UNAVAILABLE`；除非单股窗口经 corporate-action 检查证明无除权/送转/拆细影响且回测报告显式批准该降级路径。
- REQ-007: v2 必须保留 120 个交易日以上窗口要求；若引入 20 日前高、MA 斜率、ATR/波动率等指标，必须定义各自最小窗口。`high_60d_prev` 不进入 v2 最小规则，除非后续回测证明需要。
- REQ-008: v2 必须明确所有滚动窗口是否包含当日；突破/压力位判断必须使用“不含今日”的历史高点，避免未来函数或二义性。
- REQ-009: 大盘状态必须由 pipeline/回测边界层每日计算一次并作为输入传入规则，不允许 `compute_entry_signal` 纯函数自行查 DB 或调用网络。纯函数候选签名应为 `compute_entry_signal_v2(price_panel, market_state, data_contract)`，其中 `market_state` 为显式枚举值，`price_panel` / `data_contract` 必须携带 `source`、`adjusted`、`volume_unit`、freshness/stale 状态和不可用原因。
- REQ-010: 大盘状态缺失不得导致全市场 L3 `unavailable`；默认写为 `market_unknown`，不硬阻断，但必须在 reason 中标记。
- REQ-010A: v2 初始大盘状态使用沪深 300（内部代码需在实现计划中映射为当前 provider 支持的 index code），候选公式固定为：`market_bullish` 当且仅当指数 qfq/普通收盘价 `close > MA20` 且 `MA20 >= MA20_5d_ago`；`market_weak` 当 `close <= MA20` 或 `MA20 < MA20_5d_ago`；指数窗口不足或数据 stale 时为 `market_unknown`。该公式为回测候选，生产前可由回测修订。

### 6.3 v2 最小规则候选

REQ-011: v2 最小可落地规则必须先定义为“候选规则”，经离线回测验证后才能进入生产实现。

初始候选如下：

```text
Base trend gate:
  close > MA60
  AND close > MA120

MA slope gate:
  MA60 >= MA60_5d_ago

Volume tiers:
  strong volume: vol5 >= vol20 * 1.15
  weak volume:   vol5 > vol20 AND vol5 < vol20 * 1.15
  reject volume: vol5 <= vol20

Historical resistance, excluding today:
  high_20d_prev = max(high over previous 20 trading days, excluding today)

Breakout/position tiers:
  breakout_20d = latest_close >= high_20d_prev
  near_20d_resistance = latest_close < high_20d_prev AND latest_close >= high_20d_prev * 0.98
  extended_from_ma60 = latest_close / MA60 - 1 > threshold

Market gate:
  market_bullish: no downgrade
  market_weak: pass_strong downgraded to pass_weak
  market_unknown: no hard block; attach reason
```

REQ-012: `pass_strong` 候选必须至少满足：

```text
Base trend gate
AND MA slope gate
AND strong volume
AND (breakout_20d OR not near_20d_resistance)
AND not extended_from_ma60
AND market_bullish or market_unknown
```

REQ-013: `pass_weak` 候选用于表达“趋势修复但买点质量不足”，包括但不限于：

```text
Base trend gate true
AND at least one soft defect:
  weak volume
  OR weak_ma60_slope: MA60 < MA60_5d_ago AND (MA60_5d_ago / MA60 - 1) <= 0.01
  OR near_20d_resistance without breakout
  OR extended_from_ma60
  OR market_weak
  OR qfq unavailable but none-adjusted data satisfies Base trend gate
```

REQ-014: `reject` 候选至少包括：

```text
close <= MA60
OR close <= MA120
OR vol5 <= vol20 AND no other override rule exists
OR severe data inconsistency that should not be unavailable
```

REQ-015: `unavailable` 候选至少包括：

```text
window insufficient
missing close/volume/date
source stale
mixed source/adjusted/volume_unit
required qfq data absent when qfq is mandatory for strong classification
invalid market state input when implementation elects to require it for report-only calculation
```

REQ-015A: `QFQ_UNAVAILABLE` 是数据源诊断状态，不得被当作 v2 `pass_strong` 不存在的有效回测结论。若回测窗口没有 qfq 覆盖，报告只能输出 `NEED_QFQ` / `NEED_SPEC_FIX`，不得输出 `GO_TDD`。

### 6.4 阈值与自适应

- REQ-016: 固定阈值（如 `vol5 >= vol20 * 1.15`、`near resistance 2%`、`extended_from_ma60 5%`）必须先在回测报告中做敏感性分析，不得直接硬编码为最终生产参数。
- REQ-017: spec 必须比较固定 5% 偏离阈值与波动率自适应阈值，例如 `ATR`、60 日收益率标准差或分行业/分 beta 阈值。
- REQ-018: 若 v2 初版暂不实现 ATR，必须把“固定阈值可能不适配高 beta/低 beta 股票”列入已知限制，并在 report 中分组观察。

### 6.5 回测与统计

- REQ-019: v2 回测必须同时输出 v1 pass、v2 pass_strong、v2 pass_weak、v2 reject 的 30d 结果。
- REQ-019A: 回测基础样本可以覆盖 `framework='A'` 的全部 `predictions` 记录用于诊断，但核心决策指标必须单独限制在真实推送语境：`total_score >= buy_strong` 的 Framework A 候选。全 A 样本指标不得替代 buy_strong 子集结论。
- REQ-020: v2 回测必须使用去重口径，不能把连续多日同股触发简单当独立样本。
- REQ-021: 回测必须至少提供两种口径：
  1. 原始日频样本，用于和历史 report 兼容。
  2. 去重样本：同股 `reject/unavailable -> pass_*` 首发触发，触发后 20 个交易日冷却。
- REQ-021A: 若未来生产推送采用 v2，必须同步实现同股 20 个交易日冷却或等价去重机制；否则回测报告必须单独标注“回测去重与实盘每日推送不一致”，且不得据此宣称可上线。
- REQ-022: 回测必须按 signal state 输出：样本数、平均 30d 收益、平均 benchmark、平均 alpha、hit rate、中位 alpha、最大单笔亏损、最大回撤、换手/触发频率，并在可行时输出 Sharpe 或同类风险调整指标。
- REQ-022A: 回测必须包含交易摩擦模型，至少参数化展示佣金、滑点、卖出印花税（默认可先用 0.05%）对 30d alpha 的影响。
- REQ-023: 样本数不足时必须标注“样本不足”，不得给确定性结论。
- REQ-023A: 回测报告必须区分 in-sample / out-of-sample。若 watchlist 样本不足，至少用时间切分作为 OOS；默认建议 In-Sample=`2025-01-01` 至 `2025-12-31`、Out-of-Sample=`2026-01-01` 至回测结束日，若样本窗口不同必须在报告 metadata 中说明切分依据。如引入更大股票池，需要先确认数据源与成本。
- REQ-024: 回测必须单独列出高股息/除权敏感股票 qfq vs none 的信号差异，至少覆盖长江电力等高股息样本。
- REQ-024A: 为避免回测窗口起点 MA60/MA120 和 `high_20d_prev` 集中不可用，日线加载必须从 `start` 前至少 120 个交易日开始预取；统计输出仍只计算 `start <= score_date <= end` 的样本。
- REQ-024B: 若使用 Tushare `daily + adj_factor` 内存派生 qfq，必须按交易日对齐价格序列与复权因子序列；任一股票出现缺失因子、重复日期或长度不匹配时，该股票 qfq 面板必须标记为 `QFQ_ALIGNMENT_FAILED`，不得静默计算 `pass_strong`。

### 6.6 Telegram 与报告

- REQ-025: Telegram 主推只能展示 `pass_strong`，不得把 `pass_weak` 写成“买点触发”。
- REQ-026: `pass_weak` 如展示，只能进入候补或单独弱信号区，文案应为 `△ L3弱信号(v2)` 或等价措辞。
- REQ-027: v2 未上线前，若仍由 v1 控制推送，文案应避免“强买点”，推荐改为 `L3趋势过滤通过(v1)`。
- REQ-028: accuracy-report 必须把 v1/v2 分开展示，且 v2 strong/weak 分开展示。

## 7. 数据模型与 report-only 隔离决策

AGY 工程审查指出：现有 `predictions` 唯一约束为 `UNIQUE(code, framework, score_date)`，Telegram 当前直接扫描 `entry_signal = 1`，因此 v2 **不得**在 report-only 阶段写入 Framework A 的 `predictions` 行，否则会覆盖或污染 v1 推送路径。

### 7.1 Phase 1/2 决策：离线回测不写生产 DB

- REQ-029: v2 离线回测阶段只读 `tracker.db`，输出文件型报告，不写 `predictions`、不写 `daily_bars`，不触发 Telegram。
- REQ-030: 离线报告输出路径限定为 `docs/reviews/` 或后续计划指定的项目内 run 目录。

### 7.2 Phase 3 report-only 持久化候选：新增独立表，禁止复用 predictions

若离线回测后需要把 v2 report-only 接入 daily，优先方案为新增独立表，而不是复用 `predictions`：

```sql
CREATE TABLE entry_signal_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    framework TEXT NOT NULL,
    score_date TEXT NOT NULL,
    signal_version TEXT NOT NULL,
    signal INTEGER,
    signal_status TEXT,
    signal_reason TEXT,
    signal_strength TEXT,
    source TEXT,
    adjusted TEXT,
    market_state TEXT,
    fetched_at TEXT,
    created_at TEXT,
    UNIQUE(code, framework, score_date, signal_version)
)
```

Required boundaries:

- REQ-031: v2 report-only 写入如需持久化，必须写 `entry_signal_evaluations` 或同等隔离表，不得覆盖 Framework A 的 v1 `predictions` 行。
- REQ-032: Telegram 在用户明确批准前只能读取 `predictions.entry_signal` 的 v1 生产字段，不得读取 v2 report-only 表。
- REQ-033: 若未来 v2 替代 v1 控制推送，必须单独修改 Telegram SQL，使其显式过滤 `signal_version='v2' AND signal_status='pass_strong'`，并同步实现推送冷却机制。
- REQ-034: 不得借用 Framework B 作为 v2 dry-run 命名空间；Framework B 是多框架路线资产，不能混入 L3 版本实验。

### 7.3 被拒绝方案

- Reject Option A: 在 `predictions.entry_signal` 用 `2` 表示 weak。原因：二值语义破坏，且 report-only 会污染当前唯一行。
- Reject Option B: 在 `predictions` 新增 `entry_signal_strength` 直接承载 v2。原因：仍与同一 Framework A/score_date 行绑定，无法同时保留 v1/v2。
- Reject Option C: `entry_signal=1` 表示所有 pass，status 区分 strong/weak。原因：Telegram 误推 weak 的风险最高。

## 8. 设计约束

### Always do

- L3 v2 必须保持版本化。
- 纯计算函数必须可 TDD，不发网络请求、不写 DB。
- Pipeline 边界负责数据加载、freshness、source/adjusted/volume_unit 校验和 market_state 注入。
- 回测脚本必须默认只读生产 DB；如需写报告，只写 `docs/reviews/` 或项目内明确输出路径。
- 所有 v2 report 必须标注样本去重口径。

### Ask first

- 添加依赖。
- 修改生产 DB schema。
- 批量重算或回填生产 `tracker.db`。
- 修改 cron、Telegram 配置、Gemini、Google Sheets 或外部服务。
- 让 v2 替代 v1 控制 Telegram 主推。

### Never do

- 不经回测直接上线 v2。
- 修改历史 `total_score` / `weights_hash` / outcome 字段。
- 把 qfq 与 none 价格混在同一 L3 窗口中计算。
- 在指标中使用包含未来数据的窗口。
- 把 AGY 自述当作验证结果；父级必须读文件、查 diff、跑测试。

## 9. Acceptance criteria

- AC-001 maps to REQ-001~004: spec 明确定义四类状态，并说明如何映射到 DB 或待决策项。
- AC-002 maps to REQ-005~010: spec 明确 qfq、窗口、不含今日、market_state 注入和缺失 fallback 边界。
- AC-003 maps to REQ-011~018: spec 给出 v2 最小候选规则，并标记哪些阈值必须通过回测确认。
- AC-004 maps to REQ-019~024: 离线回测计划必须包含去重、冷却、v1/v2 对比和 qfq vs none 差异分析。
- AC-005 maps to REQ-025~028: Telegram/report 文案边界明确，weak 不得主推。
- AC-006 maps to REQ-029~034: report-only 物理隔离明确；离线阶段不写 DB，持久化阶段不得复用 `predictions`。
- AC-007: 本 spec 写入后，`git diff -- docs/specs/2026-07-08-l3-v2-entry-signal-spec.md` 应只显示该 spec 新增内容。

## 10. Milestones and plan handoff

### MILESTONE-001: Spec review

Maps to: AC-001~AC-006

Outcome:
- 本 spec 经用户和至少一个独立 reviewer 审查。

Non-scope:
- 不改代码。
- 不改 DB。
- 不改 Telegram。

Verification signal:
- 用户确认 spec 方向，或 reviewer 只剩非阻塞建议。

### MILESTONE-002: Offline backtest design and implementation

Maps to: REQ-019~024

Outcome:
- 新增只读离线回测脚本和报告。

Non-scope:
- 不写 production `predictions`。
- 不触发 cron。

Verification signal:
- 回测报告同时包含日频和去重口径，明确样本数和局限。

### MILESTONE-003: Report-only persistence design

Maps to: REQ-029~034

Outcome:
- 若离线回测后需要 daily report-only，设计独立 `entry_signal_evaluations` 表或同等隔离物理存储。

Verification signal:
- v1 `predictions` 行不会被 v2 覆盖；Telegram 不会扫描 v2 report-only 数据；回滚路径明确。

### MILESTONE-004: TDD implementation plan

Maps to: REQ-001~030

Outcome:
- 从已批准 spec 派生 TDD 计划。

Verification signal:
- 测试列表覆盖状态、qfq、窗口、market_state、Telegram、report 和回滚。

### MILESTONE-005: Report-only rollout before production promotion

Maps to: G3, REQ-025~028

Outcome:
- v2 先以 report-only/dry-run 形式观察。

Verification signal:
- v1 继续控制生产 Telegram，v2 输出只读观察结果。

## 11. Validation contract

Validation must prove:

1. v2 规则没有未来函数：所有高点/压力窗口排除当日。
2. v2 不在 `adjusted='none'` 的除权敏感窗口上输出未说明的 `pass_strong`。
3. market_state 缺失不会导致全市场不可计算。
4. 连续日期重复触发不会被当作独立交易样本用于核心命中率。
5. `pass_weak` 不会进入 Telegram 主推。
6. v1/v2 统计不会混算。
7. v2 report-only 不会写入或覆盖 Framework A 的 v1 `predictions` 行。
8. 回测去重假设与未来推送冷却机制之间的差异被显式报告。

Evidence to report in later implementation:

- Changed files。
- 回测命令、退出码和报告路径。
- 测试命令、退出码和关键失败/通过摘要。
- `git diff --check`。
- 数据模型决策理由。
- 未满足条件和 deferred non-goals。

## 12. Rollback and stop conditions

Rollback for this spec-only change:

```bash
git restore -- docs/specs/2026-07-08-l3-v2-entry-signal-spec.md
```

Rollback for future v2 implementation must include:

- 保留 v1 纯函数路径。
- 能通过配置或代码路由回 `ENTRY_SIGNAL_VERSION='v1'`。
- 不删除 v1 历史解释字段。
- 不批量覆盖旧 predictions。

Stop if:

- 回测需要真实凭证或外部付费 API，且用户未确认。
- qfq 数据无法稳定获取，且没有安全降级策略。
- reviewer 发现未来函数、样本泄漏、SQL 混算或 Telegram weak 主推风险。
- 实现需要 DB schema 变更但未获用户确认。
- 测试要求真实 Telegram/Gemini/Sheets 网络调用。
- 发现当前工作区存在无关变更会被混入同一提交。

## 13. Open questions

- OQ-001: `entry_signal_evaluations` 是否作为 report-only 持久化的最终表名和 schema？离线回测阶段不回答该问题，也不写 DB。
- OQ-002: qfq 数据源优先验证 Tushare `daily + adj_factor` 内存派生；若 Tushare 权限或稳定性不足，再评估 BaoStock qfq 或 provider 升级。
- OQ-003: `extended_from_ma60` 使用固定 5%、ATR、60 日波动率，还是按行业/beta 分层？需通过敏感性分析决定。
- OQ-004: market_state v2 初版固定用沪深 300 作为降级因子；是否引入全 A、中证 800、行业指数或多个状态合成，留给回测报告比较。
- OQ-005: v2 report-only 观察期多长？建议至少覆盖 30d outcome 的自然结案窗口后再考虑替代 v1。

## 14. Approval state

- Spec approval: pending
- Implementation approval: pending
- DB schema mutation approval: pending
- Telegram production behavior change approval: pending
- Cron/runtime mutation approval: not requested / not authorized
