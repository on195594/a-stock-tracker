# outcome QFQ total-return shadow 规格（outcome shadow Phase 3）

日期：2026-07-27
版本：v3（阶段 A/B 已实施并回填实测结果）
状态：**implemented（阶段 A/B）**；阶段 C 经用户决定不执行
算法版本：`qfq_total_return_v1`
生产迁移：**未执行**，见 §13

## 实施结果（2026-07-27）

| 阶段 | 状态 | 证据 |
|---|---|---|
| A 隔离构建 | ✅ 完成 | run `outcome-shadow-e85f830d9b2d850fc2657560` |
| B 独立验证 | ✅ 完成 | 生产库 predictions 2,069 行与既有 1 run/1,776 results 均未改动 |
| C 生产 append | ❌ 不执行 | 迁移工具仅支持单 run 导入，见 §13 |

构建产出：1,842 事件（30d 1,306 / 60d 536），1,836 computed，6 `missing_stock_entry`，
**0 calendar mismatch**。产物在 `artifacts/qfq-shadow/`（该目录已被 `.gitignore`，
不入版本库）。

### 核心结论：复权修正后 Framework A 仍无选股能力

Framework A / 30d，1,230 条可比样本：

```
分位      样本    α30d(旧/未复权)   α30d(新/双边总收益)
Q5(高)    246        -4.11            -3.02
Q4        246        -7.75            -7.08
Q3        246        -4.05            -3.43
Q2        246        -0.43            -0.64
Q1(低)    246        -4.96            -3.06

超额命中率   旧 0.320  →  新 0.319
```

口径修正幅度实测 **+0.813pt**，与 §A.1 预测的 +0.857pt 相差 0.044pt（误差 5%），
预测方法得到端到端验证。

**但修正后五分位仍全负、仍无单调性、命中率几乎不变。** 这证实 §A.3 的判断：复权是
真实缺陷，但不是 Framework A 表现的根因。2026-07-12 倒置诊断的定量数值需按本表修正
（Q5 −4.11 → −3.02），定性结论不变。

剩余约 −3.4pt 属 watchlist 相对基准的系统性跑输，与评分无关，仍是 §3.1 非目标。

## 修订说明（v1 → v2）

v1 经 codex 独立审查提出 1 Critical / 9 Important / 3 Minor，作者逐条独立核对，
**全部属实**（一处举证数字有误：codex 称当前 predictions 为 2,034 行，实测为
2,069 行，其引用的是 project-status.md 记录值而非实测；结论方向正确）。

同期作者对 v1 §4.3 标注的待验证项完成实测，推翻了 v1 的两处自有结论。

变更来源在下文以 `[审查]` 与 `[实测]` 标注。

## 1. 背景

### 1.1 现状

生产 `predictions` 的收益率按**未复权价**计算：

- `a_stock_tracker/cli.py:760` — `outcome_val = (outcome_price / price_at_score - 1) * 100`
- 两端价格均来自 `a_stock_lib/providers/tushare_quotes.py:277` 的 `adjusted="none"`
- `a_stock_tracker/data/outcome_shadow.py:465,474` 的 Phase 1/2 shadow 同样锁定
  `adjusted='none'`

Phase 1 spec（`docs/specs/2026-07-23-outcome-shadow-phase1.md` 第 3 节）明确将
"不把 QFQ、现金分红或 total return 混入 `tushare_raw_price_return_v1`"列为非目标。
**这是刻意的口径定义，不是实现缺陷。**

### 1.2 真正的问题

问题在于**口径与用途不匹配**：`accuracy_report.py` 把这套 raw-price alpha 直接
用作投资有效性指标——五分位单调性检验、超额命中率、Phase 4 optimizer 启动门槛
（`hit_rate_vs_300 > 55%`）。对 30/60 天持有期的投资判断而言，跨越除权除息日的
现金分红是持有人的真实收益，记为下跌会系统性低估收益。

watchlist 35 只标的以银行、煤炭、电力等高股息品种为主，该偏差非随机分布。

### 1.3 实测量级

见附录 A。

## 2. 目标

1. 以独立算法版本 `qfq_total_return_v1` 构建并行 outcome shadow，量化复权口径
   对 Framework A 有效性结论的影响。
2. 在 `accuracy-report` 中以并行 section 展示两种口径的五分位与命中率对比。
3. 修正引用链：后续引用 Framework A 有效性结论（含 2026-07-12 倒置诊断）时
   必须标注口径。

## 3. 非目标与授权边界

### 3.1 非目标

- 不修改 `predictions` 的任何列，不回填、不改写、不新增列。
- 不改变 `accuracy-report` 既有 section 的默认口径与数值。
- 不切换 Sheets、Telegram、outcome-update、cron 的口径。
- 不修改 `config/weights.json`，不调整阈值，不重排 watchlist。
- 不解释「全分位 alpha 为负」中剩余的约 −2.6pt（watchlist 相对基准的系统性
  跑输）。该问题与本 spec 正交，另开范围。
- 不据本 shadow 结果宣称 Framework A 有效或无效。

### 3.2 三阶段授权边界 `[审查]`

v1 把构建、验证、生产写入混在一起描述。本版明确拆分，**每阶段需独立授权**：

| 阶段 | 内容 | 本 spec 授权范围 |
|---|---|---|
| A 隔离构建 | 在隔离 candidate 文件中构建 run，不触碰生产库 | ✅ 本 spec 批准后即可执行 |
| B 独立验证 | 只读 verifier 复验 candidate | ✅ 本 spec 批准后即可执行 |
| C 生产 append | 将 run 导入生产 shadow 表 | ❌ 需单独授权，沿用 Phase 2 流程 |

阶段 C 未获授权前，产出只以 candidate 文件与报告形式存在。

## 4. 核心方法论约束：口径对称性

**本节是本 spec 最重要的约束，实现时不得简化。**

### 4.1 不对称风险

`daily_bars.adjusted='qfq'`（`tushare.pro_bar(adj="qfq")`）把现金分红视为再投资，
个股收益因此是 **total return**。而 benchmark 使用的 `000300.SH` 是**价格指数**，
不含成分股股息。

设 `α_true = 个股 total return − 基准 total return`，实际计算
`α_mixed = 个股 total return − 基准 price return`，则：

```
α_mixed − α_true = 基准 total return − 基准 price return  > 0
```

即 alpha 被系统性高估，幅度等于基准在该窗口的股息贡献。

**实测偏差（非估算）** `[实测]`：在真实 cohort 上（n=1303，30d 窗口，两端同日期）

```
基准全收益 − 基准价格 = mean +0.4513pt  median +0.4706pt  max +0.6092pt
```

v1 曾按年均股息率 2.5% 线性推算为"约 0.2pt"，**低估一倍以上**，原因见 §4.3。

### 4.2 要求

`qfq_total_return_v1` 的 benchmark 必须使用**沪深300全收益指数**，与个股
total return 口径对齐。

### 4.3 全收益指数：已验证 `[实测]`

v1 将此列为待验证项，现已完成：

- 官方文档确认 `.CSI` 后缀为中证指数公司指数，`index_daily` 支持
  （https://tushare.pro/document/2?doc_id=95，需 2000 积分）
- 实际调用 `pro.index_daily(ts_code="H00300.CSI", ...)` 返回 65 行，
  区间 20260421~20260724，**账户积分充足**

**结论：§4.4 降级路径不再是主路径。**

#### 4.3.1 v1 的校验判据是错的，已替换

v1 要求"年化差额落在 1.5%~3.5%，超出即判定代码取错，fail closed"。实测：

```
同端点区间（20260421~20260724，65 交易日）
  价格   -2.492%      全收益 -1.322%      差额 +1.169pt
  折年化 ≈ 4.371%   →  落在 1.5%~3.5% 之外，按 v1 判据会 FAIL
```

**代码是对的，判据是错的。** 月度拆解显示原因是样本期覆盖 A 股分红季：

```
202604 +0.0924%   202605 +0.1707%
202606 +0.5217%   202607 +0.4025%   ← 除息高峰
```

短窗口折年化必然高于年均股息率。`[审查]` 亦独立指出该门禁"无来源依据且未考虑
6-7 月季节性"。

**替换为两条与季节性无关的判据：**

1. `ratio = 全收益close / 价格close` 在共同交易日上单调不减，
   **容差 0.05 bp**
2. 任意共同区间满足 `全收益收益率 >= 价格收益率`

#### 4.3.2 容差依据 `[实测]`

零容差检查报 3 处下降，逐一核对全部为舍入噪声：

```
20260422->20260423: -0.0001 bp
20260424->20260427: -0.0002 bp
20260506->20260507: -0.0001 bp
```

收盘价 0.01 精度对 ratio 的量级影响 ≈ **0.0356 bp**，比观测"下降"大两个数量级。
容差取 0.05 bp（≈1.4 倍价格精度影响）。**零容差判据会误报。**

### 4.4 若全收益指数不可得：fail closed `[审查]`

v1 的方案 B/C 均不成立，已废弃：

- **方案 C**（保持混合口径 + 标记 bias）只披露偏差、不修复公式，产出的不是
  §4.2 要求的 calibrated alpha。
- **方案 B**（排除个股分红）得到的是 price alpha，口径虽对称但已不衡量本 spec
  的目标；且"后复权 + 分红加回"是否重复计算未经证实。

**替换规则：** 全收益指数不可得时 **fail closed**，不产出任何名为
`qfq_total_return_v1` 的 alpha。降级只允许输出两个**独立且各自口径自洽**的指标：

1. price alpha（个股 price return − 基准 price return）
2. dividend contribution（个股股息贡献，单列，不并入 alpha）

二者不得相加后当作 total-return alpha 使用。

## 5. 算法定义 `qfq_total_return_v1`

### 5.1 Frozen cohort `[审查]`

v1 缺 `as_of_date` 与到期谓词，导致未到期事件会被误判为数据缺失、且 §9 验收
行数不可确定。本版固定：

- **`as_of_date = 2026-07-24`** —— 取全收益指数最新可得交易日（非当日），
  使 benchmark 可得性内生满足，避免 §6.3 的 T+1 缺口
- **到期谓词**：`date(score_date, '+{window} days') <= as_of_date`
- **窗口**：仅 30d 与 60d。90d 到期仅 4 条，样本不足，本 run 排除 `[审查]`

实测 cohort 规模（固定值，实现须逐一比对）：

| 窗口 | 到期事件数 | Framework A | Framework B |
|---|---|---|---|
| 30d | 1,306 | 1,233 | 73 |
| 60d | 536 | 463 | 73 |
| **合计 event_count** | **1,842** | 1,696 | 146 |

⚠️ **本表已于 2026-07-27 订正。** v2 初稿写的是 1,233 / 463 / 1,696，那是只统计
`framework='A'` 的结果；但 `_load_frozen_events` **不按 framework 过滤**，Phase 1 的
1,776 条同样含 B。实现与 Phase 1 一致，错的是初稿的统计口径。该错误由 A3 实际执行
时的 inspection 输出暴露（实测 30d 1306 / 60d 536），fixture 测试无法发现。

Framework B 仍为 report-only，纳入 shadow 只是保持与 Phase 1 相同的 cohort 定义；
报告层须按 framework 分组，不得把 B 的结果混入 Framework A 的有效性结论。

后续新增 prediction 不进入本 run。

### 5.2 计算

对每个 `(prediction_id, window_days)` 事件：

- **entry**：`daily_bars` 中 `adjusted='qfq'`、不晚于 `score_date` 且向前最多
  10 个自然日内的最近交易日收盘价
- **target**：同一规则应用于 `score_date + window_days`
- **outcome**：`(target_close / entry_close - 1) * 100`
- **benchmark**：全收益指数对 entry/target anchor 各自独立执行同样规则
- **alpha**：`outcome - benchmark`

### 5.3 `stored_entry_shadow_outcome` 置 NULL `[审查]`

v1 保留该字段并标注"仅诊断用途"。**本版改为不计算，写 NULL。**

理由：Phase 1 中该字段成立的前提是两端均为未复权价；在 QFQ 算法下，
`price_at_score`（未复权）与 QFQ target 价格尺度不一致，其差值无可解释的数学
语义，标注"诊断用途"不能赋予它意义。字段允许 NULL，无需 DDL 变更。

`stored_entry_shadow_alpha` 同样置 NULL。

## 6. 数据源与日期合同

### 6.1 个股来源纯度合同 `[审查]`

v1 只约束 `adjusted`，不能防止不同 provider 混入。本版对齐 Phase 1 的严格度：

仅接受 `daily_bars.adjusted='qfq' AND source='tushare.pro_bar.qfq'`。出现任何
其他 `source` 或 `adjusted` 值即整批 fail closed。

⚠️ **两个条件缺一不可**（2026-07-27 实测）：

```
adjusted              source                 rows
none                  tushare.daily          18950
qfq                   tushare.pro_bar.qfq     4935   ← 本 run 唯一合法来源
qfq_tushare_shadow    tushare.pro_bar.qfq     4620   ← 同 source，必须靠 adjusted 排除
```

生产 QFQ 与 `qfq_tushare_shadow` **共享同一 `source` 值**，仅靠 source 过滤会
混入 4,620 行 shadow 数据。这与 `[审查]` Important 8 担心的"不同 provider 混入"
机制不同，但后果更直接。

**覆盖情况**（2026-07-27 实测）：4,935 行，35/35 代码，`2025-12-24 ~ 2026-07-27`；
Framework A `predictions` 的 `score_date` 范围为 `2026-04-21 ~ 2026-07-27`。

⚠️ `[审查]` v1 据此声称"完整覆盖、不存在需要排除的历史断层"，**该结论强于证据**：
总行数与日期范围不能证明每个事件的 entry/target anchor 都有可用记录，且与 §6.2
自相矛盾。本版仅声称：**QFQ 序列的日期区间包含全部 predictions 的 score_date
区间**，逐事件可得性由 §6.2 规则处理并计入 run 统计。

### 6.2 已知缺口

40 条已结案 prediction 在其 `score_date` 当日无 QFQ bar（停牌或非交易日），
走 §5.2 的向前 10 自然日规则；仍无解者标记 `missing_stock_entry`，计入 run
但不进入 aligned 汇总。

### 6.3 Benchmark 数据与 T+1 延迟 `[实测]`

生产 `index_prices` 仅 66 行 `000300`（`2026-04-21 ~ 2026-07-27`）且无全收益
指数。本 run 必须**新获取并冻结**全收益指数快照到
`outcome_shadow_observations`，不读取、不写入、不修改 `index_prices`。

⚠️ **实测发现全收益指数发布滞后价格指数 1 个交易日**：

| 指数 | 行数 | 末日 |
|---|---|---|
| 000300.SH（价格） | 66 | 20260727 |
| H00300.CSI（全收益） | 65 | 20260724 |

v1 完全未覆盖此情况。本版通过 §5.1 将 `as_of_date` 固定为 20260724 从根本上
规避。此外强制：benchmark anchor 无可用全收益数据时标记
`missing_benchmark_target`，**严禁回退到价格指数**——那会重新引入 §4.1 的
口径不对称。

benchmark 快照须与 Phase 1 同等严格：记录 symbol、source、抓取时间、输入 hash、
anchor 与实际交易日，并做重复行与覆盖度校验 `[审查]`。

### 6.4 日历一致性 `[审查]`

v1 只写 entry 对齐。经核对 `a_stock_tracker/data/outcome_shadow.py:602-611`，
现有 runner 实际同时检查 entry 与 target：

```python
and entry[0] == benchmark_entry[0]
and target[0] == benchmark_target[0]
```

本版对齐：个股与 benchmark 的 **entry 或 target** 任一实际交易日不一致时，
标记 `computed_calendar_mismatch`，不进入主要 aligned-alpha 汇总。

## 7. Schema：主路径零变更（条件性结论）`[审查]`

已核实（2026-07-27）：

- `outcome_shadow_runs.algorithm_version TEXT NOT NULL` 已存在
- `outcome_shadow_results` 含 `benchmark_source` / `adjusted` / `status` /
  `reason_code` / `aligned_alpha_eligible`，且缺数据字段允许 NULL
- 三张 shadow 表已具备 6 个 immutable 触发器

**结论：在 §4.2 主路径（全收益指数可得，已由 §4.3 验证）下，新增算法版本只需
产生新 `run_id`，零 DDL、零迁移、不触碰生产 `predictions`。**

⚠️ 该结论**条件性成立**。v1 同时保留方案 C 却声称零 DDL，二者冲突：
`outcome_shadow_runs` 无 metadata/benchmark-convention 列，run 级
`benchmark_dividend_bias` 标记无处存放。§4.4 废弃方案 C 后该冲突消解。

实现不得为本 run 新建表或新增列。若发现现有字段无法承载，必须停止并单独审批，
不得就地 ALTER。

## 8. Fail-closed 要求

任一条件触发整批停止，不得部分写入：

1. §4.3.1 的两条 benchmark 校验判据未通过
2. 个股数据出现 §6.1 约定之外的 `source` 或 `adjusted` 值
3. §5.1 的 event_count 与实测固定值（30d 1,306 / 60d 536 / 合计 1,842）不符
4. §8.1 的扩展保护 hash 在 run 前后发生变化
5. 单事务提交失败

### 8.1 保护 hash 必须扩展 `[审查]`

⚠️ **这是本次审查最重要的发现，已独立核实。**

`a_stock_tracker/data/outcome_shadow.py:20-31` 的 `PROTECTION_COLUMNS` 为：

```
id, code, framework, score_date, price_at_score,
quant_score, total_score, weights_hash, report_period, created_at
```

**不含任何 `outcome_*` / `benchmark_*` 列**（实测 grep 计数为 0）。现有保护
hash 防的是评分数据被改写，**防不住 outcome/benchmark 被改写**——而本 spec
的全部工作恰恰围绕 outcome 口径，这是一个正对着的安全缺口。

**要求：** 本 run 的前后校验使用**扩展保护 hash**，在 `PROTECTION_COLUMNS`
基础上额外覆盖 `outcome_30d/60d/90d` 与 `benchmark_30d/60d/90d`。

⚠️ 实现约束：**不得修改 `PROTECTION_COLUMNS` 常量本身**——那会使 Phase 1/2
已冻结 run 的 hash 无法复验。须新增独立的扩展校验函数。

### 8.2 基线值 `[审查]`

v1 引用 `05e8d556…0f1ce`（对应 1,999 行）**已过时**。实测当前
`predictions` 为 **2,069 行**（A 1,996 / B 73）。

（codex 审查称 2,034 行，系引用 project-status.md 记录值而非实测，数字不准，
但"已过时"的结论成立。）

基线以 run 开始时刻实测值为准并记录，不得沿用文档中的历史值。

## 9. 验证

1. 全量测试通过（当前基线 1,126 passed，实现后不得下降）
2. Ruff lint/format、mypy、`git diff --check` 全绿
3. run 完成后独立只读 verifier 复验：
   - 本 run 行数 == `event_count` == 1,842
   - 随机抽 10 个事件手工按 §5.2 重算并逐字段比对
   - `predictions` 行数与**扩展**保护 hash 未变
4. 报告层：`accuracy-report` 新增并行 section，既有 section 数值逐字节不变

### 9.1 小样本警告 `[审查]`

CLAUDE.md 明确要求"accuracy-report：记录 < 100 条时头部必须有样本不足警告 +
选择性偏差免责声明"。新增 section 同样受此约束：任何分层后样本 < 100 的单元
（含 60d 分五分位后每层约 93 条）必须显示该警告，不得因是"新 section"而豁免。

## 10. 回滚

shadow 表 append-only 且带 immutable 触发器，run 无法就地删除：

- 阶段 A/B 产出为 candidate 文件，直接删除即可
- 报告层变更可直接 revert commit
- 阶段 C（若后续授权）沿用 Phase 2 备份与副本 drop-only 演练流程

## 11. 交付物

1. 构建脚本（复用 `outcome_shadow.py` runner 骨架，新增算法分支）
2. 独立只读 verifier（含 §8.1 扩展保护 hash 校验）
3. `accuracy-report` 并行 section（含 §9.1 警告）
4. run 报告：两口径五分位、命中率、gap 分布、per-code gap
5. 本 spec 状态更新为 implemented
6. `docs/project-status.md` 与 `TODOS.md` 同步

---

## 附录 A：实测证据（2026-07-27）

只读脚本，`mode=ro` 打开 `tracker.db`，未写库。样本 1,263 条（Framework A，
`outcome_30d` 与 `benchmark_30d` 均非 NULL，且 `score_date` 当日有 QFQ bar）。

⚠️ 下表 benchmark 沿用 `predictions.benchmark_30d`（**价格指数**口径），
因此 QFQ 侧 alpha 尚未修正 §4.1 的不对称，系统性高估 0.451pt。

```
QFQ收益 − 未复权收益: mean=+1.308  median=+0.735  max=+29.53
受影响样本(gap>0.5pt): 677 / 1263 = 53.6%

分位      样本      分数范围        α30d(未复权)   α30d(QFQ, 未修正基准)
Q5(高)    252   [54.1 ~ 69.9]      -3.61        -1.87
Q4        253   [46.2 ~ 54.1]      -6.95        -5.72
Q3        252   [40.1 ~ 46.1]      -3.40        -2.29
Q2        253   [31.4 ~ 40.1]      -1.52        -1.22
Q1(低)    253   [11.9 ~ 31.4]      -4.34        -2.17

全样本超额命中率: 未复权 0.330 → QFQ 0.343

per-code gap (均值 pt, n=36):
  600785 +14.54   603606 +6.18   601225 +2.53   000786 +2.23
  600019 +2.13    601857 +2.03   002736 +1.93   000963 +1.58
```

### A.1 净修正幅度 `[实测]`

```
QFQ gap 名义值        +1.308pt
基准口径虚增 (§4.1)   −0.451pt
─────────────────────────────
真实修正幅度          ≈ +0.857pt
```

`[审查]` 指出 A 节将 QFQ/raw 差额直接归因于"现金分红"缺乏逐笔除权因子证据，
属未证实。本版据此弱化措辞：该差额是**前复权与未复权的价格序列差异**，其主要
成因为分红送转，但本 spec 未做逐笔分红对账，不宣称精确归因。§4.3.1 的月度
分布与 per-code 分布是间接佐证，非直接证据。

### A.2 对既有结论的影响

- `docs/reviews/2026-07-12-framework-a-inversion-diagnosis.md` 的
  "Q5 avg_alpha_30d = −9.91%" 建立在未复权口径上。当前样本重算后 Q5 为
  −1.87（QFQ，基准未修正）vs −3.61（未复权）。**该诊断的定量结论需标注口径。**
- 603606（东方电缆）在该诊断中被列为 Q5 表现最差第 3 名（−15.2%），其中
  约 6.18pt 来自复权口径差异。

### A.3 复权不是根因

修正口径后五分位仍全负、仍无单调性，超额命中率仅 0.330 → 0.343。剩余约
−2.6pt 为 watchlist 相对沪深300 的系统性跑输，与评分无关，属 §3.1 非目标。

### A.4 统计效力限制

1,263 条为**重叠窗口**样本：每交易日对同一 35 只标的打分，30d 窗口高度重叠，
标的集中于少数行业簇。有效独立样本数远小于表面值，2026-07-12 诊断记录的
"11 支伪复制"问题未解决。

**本 spec 的产出不足以支撑 Framework A 有效性的正面或负面结论，也不构成
调整权重的依据。**

---

## 附录 B：独立审查与核对记录

审查方：codex（独立 dispatch，只读，XML 分块 prompt + grounding_rules）
耗时 315s，未卡死。作者对全部 13 条发现逐条独立核对。

| 级别 | 发现 | 核对结果 | 处置 |
|---|---|---|---|
| Critical 1 | 方案 B/C 均不修复 §4.1 不对称 | ✅ 代数成立 | §4.4 重写为 fail closed |
| Important 1 | 方案 C 的 bias 标记无存储落点 | ✅ runs 表无 metadata 列 | §7 改为条件性结论 |
| Important 2 | `stored_entry_shadow_outcome` 无诊断语义 | ✅ 成立 | §5.3 改置 NULL |
| Important 3 | 缺 `as_of_date`/到期谓词/frozen cohort | ✅ v1 确实未写 | §5.1 新增，含实测行数 |
| Important 4 | §6.1 结论强于证据、与 §6.2 矛盾 | ✅ 成立 | §6.1 弱化措辞 |
| Important 5 | §4 精确量级未证实 | ⚠️ 审查时属实 | 已由作者实测证实，见 §4.1/§4.3 |
| Important 6 | 保护 hash 不完整且过时 | ✅ 不完整属实（grep 计数 0）；过时属实，但其举证 2,034 行有误，实测 2,069 | §8.1/§8.2 |
| Important 7 | 生产写入授权边界未划分 | ✅ 成立 | §3.2 新增三阶段表 |
| Important 8 | 来源纯度合同不完整 | ✅ 成立 | §6.1/§6.3 补 source 校验 |
| Important 9 | 遗漏项目强制的小样本警告 | ✅ CLAUDE.md 明确要求 | §9.1 新增 |
| Minor 1 | §6.4 漏 target 日历对齐 | ✅ 核对 `outcome_shadow.py:602-611` 属实 | §6.4 补齐 |
| Minor 2 | §4.3 年化门禁无依据、未考虑季节性 | ✅ 作者实测独立确认判据会误报 | §4.3.1 替换判据 |
| Minor 3 | A 节归因分红缺逐笔证据 | ✅ 严格成立 | A.1 弱化措辞 |

**未采纳项**：无。

**审查方举证错误 1 处**：Important 6 中 predictions 行数称 2,034（实测 2,069），
系引用 project-status.md 记录值而非实测。结论方向不受影响。

---

## 13. 阶段 C 未执行的原因（2026-07-27 决定）

尝试阶段 C 时，只读 `inspect_shadow_import` 连续暴露三层问题。前两层已修复
（commit `7c43c7b`）：迁移工具按 run 的 `algorithm_version` 解析保护 hash 变体与
期望来源，未登记算法直接拒绝。

第三层是**设计边界，非缺陷**：`_apply_transaction` 有硬约束

```python
if _shadow_object_names(conn):
    raise MigrationContractError("PRODUCTION_STATE_CHANGED")
```

该工具的设计前提是"生产库尚无 shadow 表，导入后恰好存在一个 run"，不支持追加第二个
run。支持多 run 并存需改动六处核心语义：`_production_state`、`_load_payload`（生产侧
按 run_id 过滤）、`_apply_transaction`（表已存在时跳过建表）、
`_validate_connection_matches`、`_assert_*` 系列（需区分本 run 行与既有行）、
`revert_shadow_import`（只删本 run）。

该工具直接操作生产库、且库中已有带不可变触发器的既有数据，当初经独立审查
（`docs/reviews/2026-07-24-outcome-shadow-phase2-stage-a-review.md`，`APPROVE_LANDING`）
才用于生产。同等改造应另开 spec、独立审查与回滚演练。

**用户决定：阶段 C 放弃。** 本 run 的分析价值已由报告兑现，导入生产 shadow 表仅增加
可查询的审计留痕，不改变任何结论。若将来需要，从本节列出的六处语义开始设计。

## 14. 实施期间发现并修复的缺陷

同一类耦合缺陷在本轮出现六次——"某个选择必须在多处保持一致"，逐条记录以便后续改造
时优先检查：

| # | 位置 | 暴露方式 | commit |
|---|---|---|---|
| 1 | `_verify_candidate` 用旧保护函数 | 手动追调用链 | `889ca47` |
| 2 | 结果行 `stock_source`/`adjusted`/benchmark `instrument_code` 写死 | 单元测试断言 | `889ca47` |
| 3 | `inspect_frozen_cohort` 保护变体硬编码 | **独立审查** | `b99fc93` |
| 4 | `_create_schema` 未清除源库继承的 shadow 表 | **真实执行** | `4324b13` |
| 5 | 迁移工具保护变体与来源硬编码 | **真实执行（阶段 C）** | `7c43c7b` |
| 6 | 报告 caveats/标题固定为 Phase 1 语境 | 收尾核对产物 | `75f5bd7` |

值得注意的是暴露方式各不相同，且互相抓不到：单元测试抓不到 #1（build 与 verify 在
正常 API 下总是同步取值）与 #4（fixture 永远从空库建表）；审查抓不到 #4/#5（读的是
diff，不是生产库当前状态）；而 #6 程序完全正常，错的只有给人读的文字，任何自动化
手段都判断不了一句自然语言是否与代码行为矛盾。

**根因是架构层面的**：`ShadowAlgorithm` 收敛了算法差异的**定义**，但没有收敛其
**消费**——有的直接读字段，有的把值写入数据库再由另一模块读出比较（#5 这条依赖不
体现为函数调用，grep 调用点找不到），有的嵌进 SQL 字面量，还有的写进给人看的散文。
默认参数保住了向后兼容，代价是漏改时静默走旧路径而不报错。下一次改造前应先解决这个
收口问题。
