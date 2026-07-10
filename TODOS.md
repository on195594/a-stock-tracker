# TODOS

## 当前有效计划来源（2026-07-07）

当前阶段、门槛和边界以 `docs/evolution-roadmap.md` 为准。`docs/impl-plan.md` 已归档为
Phase 1-3.6 历史实施记录，不再作为后续计划来源。

当前工作重心：

1. 继续 daily/outcome-update，等待 post-fix Framework A 30d outcome 自然结案。
2. Phase 6 weekly PM loop 已自动化为每周一 09:30 cron + Telegram 摘要；下一步等首轮自然运行验证。
3. 先解决 L3 v2 qfq 覆盖问题，再讨论 v2 TDD 或生产化。
4. Framework B 只保持 report-only；生产化必须另写实施计划并获得明确授权。

## L3 v2 qfq 覆盖修复（当前 TODO）

**What:** 为 L3 v2 离线回测设计 qfq 获取方案，避开 Tushare `adj_factor` `1次/分钟` 限频。

**Why:** `scripts/offline_l3_v2_backtest.py --allow-tushare-fetch` 已能读取项目 `.env` token，但当前 qfq panels 为 `0/38`，buy_strong qfq issue 为 `766`，decision gate 正确保持 `NEED_QFQ`。没有 qfq 覆盖不得进入 `GO_TDD` 或 TDD 实装。

**Next:**
1. 写 qfq 限速/缓存/分批方案，明确是否使用项目内文件缓存，默认仍不写 `tracker.db`。
2. 支持断点续跑，逐步补齐 `daily + adj_factor` qfq panels。
3. 重跑 `docs/reviews/2026-07-08-l3-v2-backtest-report.md`，目标是 buy_strong qfq issue 降为 0。
4. 复审报告和 artifacts；只有 qfq 覆盖充分且 AGY/Codex 复审通过后，才讨论 L3 v2 TDD。

**Evidence:** `docs/reviews/2026-07-10-l3-v2-backtest-retro.md`。

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

**Next:** 等首轮自然 cron 摘要。若摘要为 WARN/FAIL，先修行情/cron/数据质量链路；不得把自动摘要作为启用 Framework B 生产写入的授权。

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

## L3 / market data boundary 后续工作

**What:** 继续执行 `docs/plans/2026-06-05-market-data-boundary-refactor-plan.md` 的小步改造。

**Current status:** 已有 `MarketDataProvider`、`daily_bars`、`market_data_audit`、L3 状态/原因字段。
2026-06-08 已补强：
- L3 refresh 的临时 AKShare 断连会在 provider 层重试。
- L3 窗口不足时会优先读取当天 `market_data_audit`，保留真实失败原因，例如 `REMOTE_DISCONNECTED`。
- 2026-06-08 当日 20 条 L3 metadata 已从笼统 `INSUFFICIENT_WINDOW` 修正为审计中的真实失败原因。

**Next:** 停止继续修补 AKShare/东方财富行情入口，按
`docs/plans/2026-06-09-market-data-provider-replacement-plan.md` 迁移到新的 provider。
推荐顺序：Tushare Pro 主源 → BaoStock fallback/历史预热 → 基本面 AKShare 迁移另开计划。

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
