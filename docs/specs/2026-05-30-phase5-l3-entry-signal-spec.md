# Phase 5 L3 买点层 Spec

**日期：** 2026-05-30
**状态：** approved-for-planning
**优先级结论：** 先做 L3 买点层，Framework B 复活后置。
**对应计划：** `docs/plans/2026-05-30-phase5-l3-entry-signal-implementation-plan.md`

---

## 1. 背景与决策

多 AI 辩论围绕两条路线展开：

- **Framework B 复活**：解决银行/金融行业框架错配，但历史断层、数据契约和多框架解释风险较高。
- **L3 买点层**：在现有 Framework A 输出上增加入场过滤，不改 `total_score`，能更直接降低推送噪音。

最终治理结论：**Phase 5 优先 L3 买点层；Framework B 放入 Phase 6，等 L3 形成版本化信号与报告口径后再复活。**

原因：

1. 当前系统最大缺口是“合适买点”，不是更多评分框架。
2. L3 是过滤层，不污染 L1/L2 评分和 `weights_hash`。
3. `entry_signal` 可版本化，回测解释比直接复活 B 更清晰。
4. Telegram 推送降噪收益更直接。
5. Framework B 仍保留历史资产，但先补齐 L3 可降低后续多框架推送噪音。

---

## 2. 已确认授权

用户已明确确认以下副作用允许进入实施范围：

- 允许新增 `predictions` 字段。
- 允许修改 Telegram 推送逻辑。
- 允许增加日线历史窗口读取。
- 允许把 L3 report 纳入 `accuracy-report`。

这些授权仅覆盖 a-stock-tracker 项目内实现，不代表允许：

- 自动交易、下单或持仓账户操作。
- 改写历史 `predictions` 评分、收益或 `weights_hash`。
- 在测试中访问真实 AKShare、Telegram、Gemini、Google Sheets。
- 隐式启用 cron 或修改系统级任务。

---

## 3. 目标

### G1. 增加 L3 入场过滤

在现有 Framework A 评分结果之上，新增 `entry_signal`，用于表达“当前是否满足入场条件”。

### G2. 保持评分可比性

L3 不改变：

- `total_score`
- `quant_score`
- `weights_hash`
- `framework`
- 历史 outcome 字段

### G3. 推送降噪

Telegram 推送从“只看分数阈值”升级为“分数阈值 + L3 入场通过”。

### G4. 报告纳入

`accuracy-report` 增加 L3 report，至少展示：

- L3 规则版本。
- `entry_signal` 覆盖率。
- `entry_signal=1/0/NULL` 分布。
- 强信号中 L3 通过/拒绝数量。
- L3 子集的 30d 命中率观察，样本不足时必须提示。

---

## 4. 非目标

本阶段不做：

1. 自动下单、交易执行、持仓账户管理。
2. Framework B/C/D/E/F 复活或新增多框架生产写入。
3. 动态选股宇宙。
4. Gemini 预期差标记作为 L3 v1 输入。
5. 回写历史 `predictions` 补算 L3。
6. 修改 `weights.json` frameworks 子树。
7. 把 L3 结果反向并入 L1/L2 评分。

---

## 5. 数据模型要求

### R1. `predictions` 新增字段

新增字段：

- `entry_signal INTEGER NULL`
  - `NULL`：当前版本已运行但不可计算，或历史记录尚未实装 L3。
  - `0`：规则已实装且主动拒绝。
  - `1`：规则已实装且通过。
- `entry_signal_version TEXT NULL`
  - 历史 pre-L3 记录保持 `NULL`。
  - L3 v1 运行过的记录固定写入 `v1`，即使 `entry_signal=NULL`（例如窗口不足或缺列）。
  - 规则变更必须升级版本号，如 `v2`。

### R2. 历史语义不可混淆

`NULL` 不得被当作 `0`。`entry_signal_version` 用于区分两类 `NULL`：

- `entry_signal IS NULL AND entry_signal_version IS NULL`：pre-L3 历史记录，规则尚未实装。
- `entry_signal IS NULL AND entry_signal_version='v1'`：L3 v1 已运行但不可计算。
- `entry_signal=0 AND entry_signal_version='v1'`：L3 v1 已判断且主动拒绝。

报告和测试必须证明历史 `NULL/NULL` 行不参与 L3 v1 命中率分母，且不可计算的 `NULL/v1` 行会计入 v1 覆盖/不可计算统计。

### R3. 迁移兼容

`get_db()` 建表 DDL 和已有数据库迁移逻辑必须兼容：

- 新库建表时包含新字段。
- 旧库启动时能自动补列或明确失败并给出修复提示。
- 迁移不得改写历史评分字段。
- 对 `predictions` 只能使用 `ALTER TABLE ADD COLUMN` 类 additive migration；禁止 `DROP TABLE` / `CREATE TABLE AS` / 重建表迁移。

---

## 6. L3 v1 规则要求

### R4. 输入数据

L3 v1 允许增加日线历史窗口读取，最小需要：

- 最近至少 120 个交易日收盘价。
- 最近至少 20 个交易日成交量。

数据源固定为 `ak.stock_zh_a_hist(symbol=code, period="daily", start_date=..., end_date=..., adjust="")`，由 pipeline 边界层把 AKShare 中文列名归一化为纯计算 seam 所需 schema：

- `date`：交易日。
- `close`：收盘价，对应 AKShare `收盘`。
- `volume`：成交量，对应 AKShare `成交量`。

纯计算函数只接受归一化后的 `date/close/volume`，不得直接依赖 AKShare 原始中文列名。测试中必须 mock 该输入，不发真实网络请求。

### R5. 初版规则

`entry_signal=1` 的初版规则采用严格 AND 语义，三项必须全部满足：

1. 最新收盘价 `close > MA60`。
2. 最新收盘价 `close > MA120`。
3. `volume_5d_avg > volume_20d_avg`。

任一条件不满足时输出 `entry_signal=0, entry_signal_version='v1'`。若价格窗口不足或数据缺列：

- 输出 `entry_signal=NULL, entry_signal_version='v1'`，不能默默写 `0`，也不能写 `NULL/NULL`。
- 日志/报告中应能看出不可计算数量。

### R6. 纯计算 seam

L3 计算应先落为纯函数或清晰 seam，便于 TDD：

- 输入：已归一化为 `date/close/volume` 的日线记录或 DataFrame。
- 输出：`entry_signal`、`entry_signal_version`、拒绝/不可计算原因。
- 纯计算函数不得写 DB、不得发网络请求。

---

## 7. Telegram 推送要求

### R7. 推送条件升级

L3 启用后，Telegram 推送条件为：

```text
total_score >= buy_strong AND entry_signal = 1
```

其中 `buy_strong` 仍来自现有阈值配置。

### R8. 推送内容增加解释

推送文本应包含 L3 状态，例如：

- `L3: v1 通过`
- `L3: v1 未通过`（若在报告中展示）
- `L3: 未计算`（不应触发推送）

### R9. 失败不阻断评分

Telegram 发送失败仍不得阻断 daily 评分与 DB 写入。

---

## 8. accuracy-report 要求

### R10. L3 section

`accuracy-report` 增加 L3 section，标题固定包含：

```text
L3 买点层
```

### R11. 最小统计项

报告至少包含：

- `entry_signal_version='v1'` 的记录数。
- `entry_signal=1` 数量。
- `entry_signal=0` 数量。
- `entry_signal IS NULL` 数量。
- strong 候选中 L3 通过数量。
- strong 候选中 L3 拒绝数量。
- L3 v1 不可计算数量（`entry_signal IS NULL AND entry_signal_version='v1'`）。

### R12. L3 30d 命中率定义

L3 30d 命中率必须沿用 Framework A 既有口径：

- 分母：`framework='A' AND entry_signal=1 AND entry_signal_version='v1' AND outcome_30d IS NOT NULL`。
- 命中：`alpha_30d > 0`，即跑赢沪深 300。
- `entry_signal IS NULL` 和 `entry_signal=0` 不进入 L3 通过子集命中率分母，但可单独展示拒绝/不可计算样本的后验观察。

### R13. 样本不足提示

L3 30d 已结案样本 `< 30` 时，必须提示样本不足，不得输出确定性结论。

---

## 9. 测试与验收

### AC1. Schema 测试

测试证明新库和旧库迁移后都存在：

- `entry_signal`
- `entry_signal_version`

### AC2. L3 纯函数测试

至少覆盖：

- 价格站上 MA60/MA120 且量能放大 → `entry_signal=1`。
- 跌破 MA60 或 MA120 → `entry_signal=0, entry_signal_version='v1'`。
- 只站上 MA60 但未站上 MA120 → `entry_signal=0, entry_signal_version='v1'`，证明 AND 语义。
- 量能未放大 → `entry_signal=0, entry_signal_version='v1'`。
- 历史窗口不足 → `entry_signal=NULL, entry_signal_version='v1'`。
- 缺少必要列 → `entry_signal=NULL, entry_signal_version='v1'`。

### AC3. daily 写入测试

测试证明 `cmd_daily()` 写入 predictions 时包含 L3 字段，且不改写历史行。

### AC4. Telegram 测试

测试证明：

- `score >= buy_strong` 且 `entry_signal=1` 才推送。
- `score >= buy_strong` 但 `entry_signal=0/NULL` 不推送。
- Telegram 失败不阻断 daily。

### AC5. accuracy-report 测试

测试证明：

- 报告包含 `L3 买点层` section。
- `NULL/NULL`、`NULL/v1`、`0/v1`、`1/v1` 分开统计。
- L3 30d 命中率使用 `framework='A'`、`entry_signal=1`、`entry_signal_version='v1'`、`outcome_30d IS NOT NULL` 作为分母，使用 `alpha_30d > 0` 作为命中。
- 样本不足时有提示。
- L3 统计不污染 Framework A 原有 DB anchor。

### AC6. 全量门禁

实施完成后必须通过：

```bash
pytest tests/ -q
git diff --check
git status --short
```

---

## 10. Stop 条件

任一触发即暂停实现并回到 spec/plan：

1. 需要改写历史 `predictions`。
2. 需要修改 `weights.json` frameworks 子树。
3. 测试需要真实网络请求才能通过。
4. L3 统计会把 `NULL` 当作 `0`，或无法区分 `NULL/NULL` 与 `NULL/v1`。
5. Telegram 推送变更无法用 mock 测试证明。
6. 日线历史窗口读取显著改变 daily 的失败语义，但没有 fallback/日志策略。

---

## 11. Framework B 后置规则

Framework B 仍保留为 Phase 6 候选，但必须满足以下条件后再启动：

1. L3 v1 已形成稳定字段、报告和推送语义。
2. A 框 post-fix 30d 结案样本足够支撑对比。
3. B 的数据源 registry 与字段缺失策略补齐。
4. accuracy-report 已能同时解释 framework 分层与 L3 子集，不混算。

---

## 12. 执行提示

实施时必须采用 TDD：先写失败测试，再改生产代码。推荐顺序见实施计划。
