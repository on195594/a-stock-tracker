# TODOS

## 当前有效计划来源（2026-07-28）

当前阶段、门槛和边界以 `docs/evolution-roadmap.md` 为准。`docs/impl-plan.md` 已归档为
Phase 1-3.6 历史实施记录，不再作为后续计划来源。

产品目标已澄清为：建设 A 股选股与买入决策支持系统，提高获得较高风险调整后收益的
概率。阶段总结见
`docs/reviews/2026-07-28-product-direction-and-engineering-subtraction-review.md`。

当前按路线图“三阶段收敛路线”的阶段一执行：

1. **P0 生产运维：** 保持 TuShare、QFQ、daily、acceptance、outcome 和 cron 自然运行；
   刷新已 stale 的 market-data capability probe。两套 readiness 独立，不伪造 PASS。
2. **P1 Framework A 有效性：** 比较 watchlist 等权组合与沪深 300，拆出固定股票池
   本身的相对表现，再评估评分在池内的排序能力；如需讨论市场 beta，必须另行按收益
   序列估计，不得把两条累计收益之差直接称为 beta。同步完成 QFQ 总收益 30/60/90 日分位、
   按 `score_date` 计算的截面 Spearman IC 及其时间汇总、Q5−Q1 spread、回撤和时间批次
   验证。在结果前不得调整权重或阈值。
3. **P1 决策语义（2026-07-28 已完成）：** Telegram 已删除
   `total_score - quant_score` 的“择时佳/择时弱”解释和旧 L3 v1 展示，只显示实际决定
   主推的 v2 风险门禁；主推/候补标题不再把当前规则称为已验证买点。
4. **P1 L3 v2 证据：** 在 strategy report 增加 `NULL/0/1`、状态分布以及通过/拒绝后的
   收益与回撤。当前 v2 更接近极端下跌风险门禁；若拒绝样本长期接近零，应直接判定为
   “当前规则无实质选择性”，不得无限等待样本或宣称“买点已解决”。
5. **P1 outcome 真相：** 规划未来版本化 QFQ 总收益与全收益 benchmark；不原地改写
   历史 prediction，不为此继续扩展已有 shadow 多 run migration。
6. **P2 Phase 6：** Framework B cohort 继续自动冻结并等待 W30/W31/W32 自然结案；
   达到 20 条/3 周/无 overdue 后也只进入人工 review，不直接生产化。

**HOLD：** M4/M5 新授权和模型 Sprint、outcome shadow 阶段 C、多框架生产化、
Framework C/D/E/F、Phase 7 生产动态池、新治理/Reviewer/seal/authorization 基础设施。
恢复任一项前必须说明它验证的 alpha、买入时点或回撤假设。


## outcome QFQ total-return shadow（2026-07-27 阶段 A/B 完成）

**结论：复权是真实缺陷，但不是 Framework A 表现的根因。**

生产 outcome/alpha 全程按未复权价计算，分红被记为下跌。以 `qfq_total_return_v1`
（qfq 个股 + 沪深300全收益指数基准）重算后：

```
分位      样本    α30d(旧/未复权)   α30d(新/双边总收益)
Q5(高)    246        -4.11            -3.02
Q4        246        -7.75            -7.08
Q3        246        -4.05            -3.43
Q2        246        -0.43            -0.64
Q1(低)    246        -4.96            -3.06
超额命中率   旧 0.320  →  新 0.319
```

净修正 +0.813pt（预测 +0.857pt，误差 5%）。**修正后仍全负、仍无单调性。**

- spec：`docs/specs/2026-07-27-outcome-qfq-total-return-shadow-spec.md`（v3，含 §13 阶段 C
  未执行原因与 §14 六次同类缺陷记录）
- 产物：`artifacts/qfq-shadow/`（已 gitignored，**无 git 保护**，磁盘清理时注意）
- 阶段 C（导入生产 shadow 表）已放弃：迁移工具仅支持单 run 导入，改造需另开 spec

**Next：** 拆解剩余 −3.4pt 中固定 watchlist 股票池效应的占比（见上方 P1）。

## TuShare 估值/财务/分红三域强切（2026-07-21 已完成）

- 生产运行时已切换为 `a-stock-lib==0.4.1`。
- 35/35 shadow readiness、单事务 materialization、来源审计、评分 smoke 与 SQLite quick check 均通过。
- `predictions` 保持 1,894 行，切换前后 canonical hash 不变。
- production cycle 已收敛为 `python -m scripts.run_tushare_primary_production_cycle daily|weekly`。
- cron 已迁移到 17:15 primary、17:30 daily、17:45 acceptance、18:00 outcome；周六 10:00 财务/分红。
- 两次中间失败均真实恢复 DB、crontab 和 0.2.0 依赖，最终回滚错误为 0。
- 复盘：`docs/reviews/2026-07-21-tushare-three-domain-cutover-retrospective.md`。
- 运行手册：`docs/runbooks/tushare-primary-production.md`。

## 2026-07-15 运维与质量基线修复（已完成）

- 当天 Tushare probe 的 daily/index/calendar/close cross-check 全部 PASS，readiness 恢复 `READY_CRON`，managed cron 已重新安装并确认五项任务齐全。
- weekly PM loop 不再把中文“失败 0 只”判为失败；降级 `WARNING ... fallback失败` 归为 WARN，明确 ERROR/非零失败仍为 FAIL。真实 dry-run 从错误的 FAIL 恢复为符合现状的 WARN。
- mypy 从 24 errors 修复后保持零错误；Ruff format 基线持续有效。
- 当时质量基线为 `1010 passed`，Ruff lint/format、mypy、结构/registry、Markdown 链接和 `git diff --check` 全部通过；当前基线以后续状态页和实际校验结果为准。

## 定性评分 v2 MILESTONE-002~003（已完成）

**已完成：**

- task 2.1：`qualitative_v2_types.py` + `qualitative_v2_taxonomy.py`，含 dataclass、版本化 input hash、两层 taxonomy；最终提交 `ec5d2e9`。
- task 2.2：`qualitative_v2_validator.py`，含 shape/evidence/freshness/rubric/all-or-nothing 本地校验；最终提交 `1cbaf0a`。
- task 2.3 及后续 P1/P2 边界加固已完成；MILESTONE-002 最终本地合同测试与独立复核通过。
- MILESTONE-003 文件型 shadow seam 已完成；受控空 evidence packet Gemini smoke 通过，但不构成真实 evidence shadow。

## 定性评分 v2 MILESTONE-004~005（冻结的发布后研究）

**已完成：**

- 冻结 v1.1 完整协议、31 个 SW2021 一级行业映射、原 sampling seed 和 source-aware URL 归一化。
- 实现纯 Python frame/sample/corpus、technical ledger、Wilson coverage、create-only artifact hash chain。
- 实现默认禁用、显式 authorization、独立 HOME/cache/state/tmp、bounded stdout/stderr/timeout 的 Reviewer A/B。
- 实现 schema-valid seal、review index、逐字段 disagreement、完整 adjudication 和报告时原子 lineage 复验。
- 轻量 builder 已生成 4,694 行 frame、1,170 行 exclusions 和固定 36 股 sample；sample SHA-256 为 `b278a7b00b71fd54e34519dead098602a8748635f1350414e1b78538a3d7635d`。
- M5 fixture-first 批处理、artifact seal、聚合 gate、review finding 修复、real bundle 离线 builder、D1 authorization/preflight 和严格只读 fundamentals snapshot builder 已完成；模型命令仍只执行 synthetic fake transport。

**冻结状态：**

- 2026-07-19 的六公司结构质量 pilot 已批准并封存，但授权执行窗口已于
  2026-07-22 结束；不得把它继续写成“待审批”，也不得复用过期授权。
- 当前没有活动 M5 真实采集、Reviewer、Claude 或 Gemini 执行授权。
- M4/M5 不再是生产读取、阶段一策略验证或 Framework B cohort 的前置条件。
- 仅在阶段一/二发现一个明确需要定性证据验证的收益假设时，才讨论新的有界研究授权；
  不为恢复旧路线本身续期。

**边界：** 冻结不表示删除。tracked 代码、测试、文档、hash 和 sealed identifier 保持
可追溯；gitignored artifacts 只具备本地留存，不得描述为受 Git 保护。不得搬移或改写
frozen M4/M5 证据。生产 cache/schema、pipeline、cron、Telegram 和权重不因研究冻结而改变。

## L3 v2 qfq 覆盖修复（2026-07-12 已完成，采集源已于 2026-07-22 切换）

原 `NEED_QFQ` 最初由 BaoStock QFQ 采集与 `daily_bars.adjusted='qfq'` 缓存方案解除。
当前 35/35 代码有 QFQ 覆盖，工作日 16:00 cron 已安装，daily 与 Telegram 主推已使用
`l3_v2_signal`。历史离线 gate 和限频问题保留在
`docs/reviews/2026-07-10-l3-v2-backtest-retro.md` 供追溯，不再是当前 TODO。

**2026-07-23 更新：** 底层采集源已强切为 TuShare-only
（`scripts/fetch_qfq_daily_bars_tushare.py`），`daily_bars.adjusted='qfq'` 的表结构、字段和
下游消费方式不变。旧 BaoStock QFQ 活动采集入口及双源对账入口已删除；provider 失败即
关闭，不再自动 fallback。详见 `CHANGELOG.md` 2026-07-22/23 条目。

## Phase 6 生产化门槛（当前有效）

满足以下条件后，才允许讨论 Framework B 生产写入：

1. post-fix Framework A 30d 结案样本 ≥ 100。
2. watchlist 数据质量门槛通过：cache 无缺失、required 字段可接受、cache_report_period 无缺失。
3. Framework B 金融候选 dry-run 全覆盖：`scored_count == candidate_count` 且候选数 > 0。
4. B label 非金融质量候选自然结案样本 ≥ 20，且无 overdue outcome 风险。
5. 上述条件满足后，仍先进入人工 report-only 审阅；生产写入需单独计划、测试和回滚方案。

## Phase 6 weekly PM loop 自动化（2026-07-02 已完成）

**What:** 每周一 09:30 自动运行 `scripts/weekly_pm_loop.py`，检查 weekly/daily/outcome 日志、`READY_CRON` 和 `accuracy-report`，生成本地摘要并通过 Telegram bot 通知。

**Status:** 已实现并安装 crontab，commit `f181010`。实现前已写 `docs/specs/2026-07-02-weekly-pm-loop-automation-spec.md`，并经 agy 独立审查 PASS。验证基线：`211 passed, 1 skipped`。

**Status update（2026-07-15）：** failure-marker 已修复并补 5 个中文零失败/正失败/降级 WARNING 回归场景。真实 dry-run 返回 WARN：weekly 的降级 fallback warning 被保留为 WARN，readiness 为 READY，Phase 6 仍因 B label 样本不足保持 WARN。不得把自动摘要作为启用 Framework B 生产写入的授权。

**How to apply:**

```bash
cd /home/lin/a-stock-tracker
python3 scripts/weekly_pm_loop.py --dry-run --no-telegram
tail -n 120 logs/weekly-pm-loop.log
cat logs/weekly-pm-loop-summary.txt
```

## Framework B dry-run ROE 趋势预警标注（2026-07-02 已完成）

**What:** Framework B（report-only）打分依赖 `roe_3y_avg`（3年均值），可能掩盖最近一个完整财年才出现的 ROE 结构性下滑。新增 `roe_latest`（最新单年ROE）字段抓取，`roe_3y_avg` 显著高于 `roe_latest`（差值>3pts）时给 dry-run 报告候选行追加 `⚠️ROE趋势预警` 文本标注。

**Status:** 已实现并提交，commit `92eada5`。**纯报告层展示，不改变任何打分数值/权重/排序**——A/B 两次 `score_stock` 调用均用原始未修改的 data，不碰 `weights.json`，符合本项目"不修改 weights.json"、"不顺手调评分权重"的治理红线（见 `docs/project-status.md` 禁止事项）。codex 独立审查 0 Critical/Important。验证：`223 passed`。

**Context:** 同步 a-stock-research v2.5.0"历史分位极低须做反向解读检验"教训（源自招商银行案例的 agy 审查发现）到本项目结构相似的场景。数据粒度限制：tracker 财务数据为同花顺年度报表，只能做"最新完整财年 vs 3年均值"比较，不是季度级粒度。

**How to apply:** `python3 pipeline.py accuracy-report` 后查看 "Framework B 金融候选 dry-run" / "Framework B 非金融质量候选" 章节，候选行末尾出现 `⚠️ROE趋势预警` 即为触发。

## L3 / market data boundary（历史迁移记录）

**What:** 继续执行 `docs/plans/2026-06-05-market-data-boundary-refactor-plan.md` 的小步改造。

**Current status:** 已有 `MarketDataProvider`、`daily_bars`、`market_data_audit`、L3 状态/原因字段。
2026-06-08 已补强：
- L3 refresh 的临时 AKShare 断连会在 provider 层重试。
- L3 窗口不足时会优先读取当天 `market_data_audit`，保留真实失败原因，例如 `REMOTE_DISCONNECTED`。
- 2026-06-08 当日 20 条 L3 metadata 已从笼统 `INSUFFICIENT_WINDOW` 修正为审计中的真实失败原因。

**Status:** 该节是历史迁移记录。2026-07-23 活动行情链路已进一步强切为
TuShare-only、失败即关闭，AKShare/东方财富/BaoStock 均不再是活动行情 fallback；
本节不再是当前 TODO。

## Phase 4 启动门槛（历史记录）

> 历史说明：本节是 2026-05-12 的早期 Phase 4/optimizer 门槛，保留用于追溯。
> 当前 Phase 6 readiness 和 Framework B 生产化判断不再以本节为准。

**触发条件（2026-05-12 确认）：** 当以下两个条件同时满足时，开始建设 `optimizer.py`：
1. `predictions` 表 30d 结案记录 ≥ 100 条（Framework A）
2. `pipeline.py accuracy-report` 显示任一信号层级：`hit_rate_vs_300 > 55%` 且 `样本 ≥ 20`

预期时间：2026-06 至 07 月（取决于 daily cron 运行频率）。

**Why:** 在没有结案数据之前建设 optimizer 没有意义；门槛不明确会导致拖延或过早建设。

**How to apply:** 每月运行一次 `pipeline.py accuracy-report` 检查是否达到门槛。

---

## sheets_sync: Incremental append for predictions_detail

**What:** Replace full-table rewrite in `push_predictions()` with incremental append (only new rows).

**Why:** `ws.update()` rewrites all rows every cron run. Fine at <800 rows (~12 months). Beyond that, the write takes 3-5s and approaches Sheets API quotas (300 write requests/min/project). The full rewrite also flashes existing data briefly during the update.

**Trigger:** When `len(rows)` in `push_predictions()` consistently exceeds 800 (check via `pipeline.py accuracy-report` output row counts).

**Approach:** 
1. Read the last `score_date` already in the tab (first non-header row after sorting by score_date DESC).
2. Filter SQLite query to `score_date > last_synced`.
3. Append new rows below existing data via `ws.append_rows()`.
4. Handle schema changes: if `_PRED_HEADERS` changes, full rewrite is still needed (detect via header row comparison).

**Depends on:** Nothing blocking. Can be done independently of Phase 4.

**Context:** Deferred in the sheets_sync redesign (2026-04-28). The current full-rewrite approach was a deliberate simplification. Flagged by /plan-eng-review 2026-05-11.

---

## lib/fetcher: gross_margin & pe_percentile_10y 系统性 NULL（已过期）

> 2026-06-08 状态：该问题已不再按原描述成立。当前报告显示 watchlist
> `PB 日度可计算：35/35`，金融行业 `gross_margin` 已按不适用处理，required 字段
> `35/35` 可接受。保留本节作为历史问题记录。

**What:** `gross_margin`（source=web）和 `pe_percentile_10y`（source=computed，依赖 pe_ttm）对所有 38 只股票均为 NULL，造成 data_quality 恒为 3/5=0.6，评分上限实际为 55/80（31.2% 分值永久缺失）。

**Root cause:**
- `gross_margin`：设计为 'web' 来源，需要 WebSearch 手动补充，fetcher 不负责。
- `pe_percentile_10y`：需要 `pe_ttm > 0`，但 spot_em 快照的 `市盈率-动态` 对亏损股（PE 负值）返回负数或 NULL，导致 38 只股中只有 3 只有效 pe_ttm。

**Impact:** buy_strong 阈值 55 = max achievable score，信号触发条件实际上是"所有已填字段均近满分"，可能过于严格。

**Fix options:**
1. `gross_margin`：接入同花顺财务摘要接口（ths_financial_indicator）补充毛利率字段。
2. `pe_percentile_10y`：对亏损股改用 PB 百分位替代（`pb_percentile_10y`），权重迁移。
3. 短期：accuracy-report 增加 avg_data_quality 统计，明示分值缺失比例。

**Trigger:** Phase 4 optimizer 依赖完整字段训练。在此之前可先实现 option 3（可见性）。

**Context:** 发现于 /plan-eng-review 2026-05-11。

---

## LLM Lift：Gemini 贡献度量化实验（CT3 延期）

**What:** 量化 Gemini 定性评分对 alpha 命中率的净贡献。
初始构想：对比 quant_score 分层 vs total_score 分层的超额命中率。

**Why deferred:** 分层对比存在内生性问题 — Gemini 评分本身参与了 total_score 分层边界的确定。
高 total_score 股票本就是 Gemini 给了高分的股票，无法分离"Gemini 提升了准确率"和"Gemini 恰好给高分的股票质量更好"。

**Clean experiment design（需要时再实施）：**
1. A/B 分组：同期新增预测分为两组，一组用 Gemini，另一组全部用 fallback 固定值
2. 反事实重算：对现有记录，将 quant_score 用相同阈值分层，仅在结案记录上比较（样本 ≥ 20/层）
3. 更简单的信号验证：定期（季度）手工抽 10 只高 moat 股票验证 Gemini 打分是否符合自己判断

**Trigger:** Framework A 30d 结案 ≥ 100 条后，有足够样本支撑分层统计。

**Context:** 2026-05-12 CEO review (CT3)，Codex 外部审查发现统计弱点，用户选择延期。

---

## CP3：行业集中度分析（延期）

**What:** 分析 watchlist 的行业分布，检测集中度偏差（例：银行股占比 >30% 时 hit_rate 可能受行业 beta 而非框架质量驱动）。
配套：按行业拆分 accuracy-report，检验不同行业的信号质量差异。

**Why deferred:** 没有行业映射数据的情况下，分析结果质量低。30d outcome 数据不足时行业维度的样本量更少。

**Depends on:** 至少 100 条 30d 结案记录 + 行业字段（可从 AKShare `stock_individual_info_em` 获取 `行业` 字段）。

**Bonus trigger:** PB 行业分层展示（CT2）也依赖此数据，一并实施。

**Context:** 2026-05-12 CEO review (CP3)，用户主动延期。

---

## gemini_scorer: Gemini 持续失败时每日重试开销

**What:** `get_qualitative_score` fallback 不写缓存。若 Gemini API 持续不可用（key 失效、限流、网络），每日 daily cron 仍对 35 只股票各发起一次 Gemini 请求（30s timeout），最坏情况使 daily 运行时间增加 ~17 分钟。

**Fix:** `_write_cache` 增加可选 `ttl_hours` 参数，fallback 时写入 TTL=24h 的占位记录（避免次日重试），Gemini 正常时写入 TTL=720h（30天）。

**Trigger:** 当 Gemini 出现连续失败告警（daily_log.txt 中连续 3 天出现 fallback 日志）时再实施。

**Context:** 发现于 /plan-eng-review 2026-05-11。

## agy 工程审查修复完成（2026-07-04）

**What:** agy 对 a-stock-tracker 进行工程视角独立审查，发现 2 Critical + 6 Important 问题，
通过 collab-pipeline Batch A + B 全部修复（11 commits，743e195 → 37a646e）。

**已修复：**
- A1: DB per-stock SAVEPOINT 隔离 + 崩溃幂等重跑（06f3d88/b3f1c4c）
- A2: cmd_init/cmd_weekly 测试覆盖（3a72a47/63e3eb4）
- B1: spot_em 改重试计数器，连续 3 次失败才今日锁定（23bd764/e056e93）
- B2+B5: Gemini 退避重试（429/5xx）+ 过期缓存降级（4058688）
- B3: subprocess TimeoutExpired stderr 转发（1e8b401）
- B4a: cache._ensure_columns 列名正则白名单（ab9fdb9）
- B4b: outcome window SQL 常量防注入（1ccc3da）
- B6: agent_reviewer 接入真实 Gemini，失败回退 fallback（df765cb/37a646e）
- B7: cmd_outcome_update Telegram mock 测试（5e44ec2/165aa1b）

**验证基线：** `225 passed, 1 skipped`

**Next:** 无新 TODO 由本次修复产生；继续等待 Framework A 30d outcome 结案样本 ≥ 100。
