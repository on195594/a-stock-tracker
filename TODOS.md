# TODOS

## 当前有效计划来源（2026-06-08）

当前阶段、门槛和边界以 `docs/evolution-roadmap.md` 为准。`docs/impl-plan.md` 已归档为
Phase 1-3.6 历史实施记录，不再作为后续计划来源。

当前工作重心：

1. 继续 daily/outcome-update，等待 post-fix Framework A 30d outcome 自然结案。
2. 先修 L3/market data boundary 的覆盖率和审计可解释性，再讨论 Framework B 生产写入。
3. Framework B 只保持 report-only；生产化必须另写实施计划并获得明确授权。

## Phase 6 生产化门槛（当前有效）

满足以下条件后，才允许讨论 Framework B 生产写入：

1. post-fix Framework A 30d 结案样本 ≥ 100。
2. watchlist 数据质量门槛通过：cache 无缺失、required 字段可接受、cache_report_period 无缺失。
3. Framework B 金融候选 dry-run 全覆盖：`scored_count == candidate_count` 且候选数 > 0。
4. B label 非金融质量候选自然结案样本 ≥ 20，且无 overdue outcome 风险。
5. 上述条件满足后，仍先进入人工 report-only 审阅；生产写入需单独计划、测试和回滚方案。

## L3 / market data boundary 后续工作

**What:** 继续执行 `docs/plans/2026-06-05-market-data-boundary-refactor-plan.md` 的小步改造。

**Current status:** 已有 `MarketDataProvider`、`daily_bars`、`market_data_audit`、L3 状态/原因字段。
2026-06-08 已补强：
- L3 refresh 的临时 AKShare 断连会在 provider 层重试。
- L3 窗口不足时会优先读取当天 `market_data_audit`，保留真实失败原因，例如 `REMOTE_DISCONNECTED`。
- 2026-06-08 当日 20 条 L3 metadata 已从笼统 `INSUFFICIENT_WINDOW` 修正为审计中的真实失败原因。

**Next:** 若 L3 覆盖率持续低于 80%，继续补 daily_bars 历史预热/独立刷新命令和按代码的失败重跑能力。

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
