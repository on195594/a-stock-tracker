# CLAUDE.md — a-stock-tracker

## 项目简介
A 股自动评分管道。每日盘后对 watchlist 运行 Framework A 定量评分（SQLite），
追踪 30/60/90 天收益率及 benchmark 相对 alpha。

**文档指针**（详细逻辑优先读这里，不要靠 CLAUDE.md）：
- 架构 / 数据模型 → `docs/design.md`
- 已知陷阱 / 历史修复 → `docs/lessons-learned.md`
- 演进路线图 → `docs/evolution-roadmap.md`
- 测试计划 → `docs/test-plan.md`

---

## 路径与运行

```bash
cd ~/a-stock-tracker          # ← 项目在 home 根，不在 ~/code/project/work/
source .venv/bin/activate

python pipeline.py init           # 首次初始化（预填 watchlist 数据）
python pipeline.py daily          # 每日手动触发（cron: 工作日 16:30）
python pipeline.py outcome-update # 更新到期预测（cron: 工作日 17:00）
python pipeline.py accuracy-report

pytest tests/ -v                  # 修改前必须全通过
```

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `pipeline.py` | 主编排器（init / daily / outcome-update / accuracy-report）|
| `scorer.py` | 评分引擎（breakpoints 线性插值，不调 AKShare）|
| `gemini_scorer.py` | Phase 3：Gemini 定性评分（30天缓存，退避重试，过期缓存降级，all-or-nothing fallback）|
| `telegram_push.py` | 每日分层推送（主推：≥44 分 AND `l3_v2_signal=1`；v2 0/NULL 的高分股进入候补）|
| `weights.json` | 模型权重（阈值 buy_strong=44/moderate=35/light=26）|
| `config.py` | watchlist / DB_PATH / LOG_DIR（禁止硬编码股票代码或路径）|
| `.env` | GEMINI_API_KEY / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID |
| `lib/fetcher.py` | AKShare 封装，仅用于基本面/估值/财报缓存（来自 a-stock-research skill，独立演进）|
| `lib/market_data.py` | 行情数据 provider 兼容入口；协议原语和 Tushare/BaoStock 实现来自 `a-stock-lib==0.2.0`，AKShare/东方财富行情入口已禁用（`SOURCE_DISABLED`） |
| `a_stock_lib.providers.tushare_quotes` | 行情主源（需 `TUSHARE_TOKEN`），probe 通过后启用 |
| `a_stock_lib.providers.baostock_quotes` | 行情 degraded fallback，仅 Tushare 失败后或显式 backfill 使用 |
| `lib/cache.py` | SQLite 管理（predictions / index_prices / qualitative_scores 表）|

---

## 安全红线（数据完整性）

### 禁止行为
- **禁止直接写 `alpha_*d` 列** — SQLite Generated Column，写入会报错
- **禁止 UPDATE 已有 predictions 的 `total_score` / `weights_hash`** — 破坏实验数据可比性
- **禁止修改 lib/cache.py 的 DB_PATH** 使其指向 `~/.claude/skills/a-stock-research/cache.db`
- **禁止在 `scorer.py` 对 `invert: true` 字段做额外变换** — breakpoints 已按"高值→低分"排列，插值逻辑统一
- **禁止测试中发起真实 AKShare 网络请求** — 所有 AKShare 调用必须 Mock
- **禁止让 Sheets sync 失败阻断 daily cron** — SQLite 是真相来源，Sheets 是展示层，失败只记 WARNING

### 必须行为
- **修改 weights.json 后**：hash 会变，若当日已有记录需手动删除或等次日
- **新增字段到 predictions 表**：必须同步更新 `get_db()` DDL 和 INSERT 语句
- **新增测试**：用 `tmp_path` fixture 隔离，不使用真实 `tracker.db`
- **pipeline 启动**：保留 `assert sqlite3.sqlite_version_info >= (3, 31, 0)`（Generated Column 依赖）
- **accuracy-report**：记录 < 100 条时头部必须有样本不足警告 + 选择性偏差免责声明
- **新增 watchlist 股票**：必须先 `pipeline.py init`，`cmd_batch()` 不会自动抓新股票

---

## 关键实现约定

**weights_hash**：`md5(json.dumps(weights["frameworks"], sort_keys=True))[:8]`
— 只对 `frameworks` 子树求 hash，`updated_at` / `version` 不影响 hash。

**outcome 单位**：始终是百分比，如 `+5.2` = 涨 5.2%，不是小数 0.052，不是绝对价差。

**pb_percentile_10y**：日度实时计算（current_pb = 当日收盘价 / bps，ranked in pb_hist_monthly），
每日随股价变化，纯内存，不触碰 TTL，不需额外 API 调用。

**跨期评分比较注意**：2026-05-14 前（gross_margin/pb_percentile 旧算法）vs 2026-05-15 后
avg_score 有约 4-5 分系统性偏移，Phase 4 optimizer 训练需按 score_date 分层。详见 `docs/lessons-learned.md`。

**行情数据源（2026-06-09 起迁移，与基本面数据源分离）**：AKShare/东方财富**行情**入口已禁用，
`lib/market_data.py` 默认 provider 返回 `SOURCE_DISABLED`。当前行情主源是 `a-stock-lib==0.2.0`
中的 Tushare provider（需 `TUSHARE_TOKEN`），失败后降级到同包 BaoStock provider（degraded）。`_ensure_index_prices` 走同一套 provider，不再直连
新浪/腾讯接口。成组恢复 `daily` / `outcome-update` 前必须 `python3 scripts/check_market_data_readiness.py --scope cron` 返回 `READY_CRON`
（最新探测报告见 `docs/reviews/*-tushare-capability-probe.md`）。详见
`docs/runbooks/market-data-provider-recovery.md` 和
`docs/plans/2026-06-09-market-data-provider-replacement-plan.md`。
基本面/估值/财报抓取（`lib/fetcher.py`）仍用 AKShare，未受此次迁移影响。

---

## 常见陷阱

| 陷阱 | 正确做法 |
|------|---------|
| `alpha_30d` 查询为 NULL | 检查 `outcome_30d` 和 `benchmark_30d` 是否都有值（任一 NULL → 结果 NULL）|
| 修改 `updated_at` 触发 hash 变更 | weights_hash 只对 `frameworks` 子树求 hash |
| 重新运行 daily 新增了行 | 检查 UNIQUE(code, framework, score_date) 约束是否生效 |
| Phase 1 strong 信号 < 5 条 | 阈值已于 2026-05-12 永久校准为 44/35/26，`threshold_adjusted` 恒为 0 |

---

## Phase 状态快照（2026-07-15）

| Phase | 状态 | 说明 |
|-------|------|------|
| Phase 3 Gemini/Telegram | ✅ 上线 | v1 定性评分保持生产；主推由 ≥44 分 AND `l3_v2_signal=1` 触发 |
| Phase 4 验证基础 | ✅ 完成，持续观察 | A框架 30d 结案 953 条，其中 post-fix 735 条；hit_rate 待验证 |
| Phase 5 L3 买点层 v1 | ✅ 完成，保留审计 | 最新 tracked report 中 v1 pass 的 30d 已结案 71 条、命中率 2.8%；不再作为生产主推门禁 |
| Phase 5 L3 v2（QFQ）| ✅ Phase 2+3 完成 | Phase 2: QFQ 35/35×130 行回填，pass_strong 激活，cron 16:00；Phase 3: 推送触发切换至 l3_v2_signal=1（commit e080f15） |
| 定性评分 v2 MILESTONE-002 | ✅ fixture-first 完成 | contract/types/taxonomy/schema/prompt/validator 与 145 项本地合同测试已完成，AGY 边界加固复审 PASS |
| 定性评分 v2 MILESTONE-003 | ✅ 文件 shadow seam 完成 | 独立 client/CLI、JSONL artifact、错误分类、重试和同 hash 去重已完成，AGY 最终只读审查 PASS；该研究 shadow 与后续生产 canary 物理隔离 |
| 定性评分 v2 生产 canary | ✅ 读路径上线，v1 fallback | 5 股 canary 已由 `.env` 启用；独立 v2 表当前 0 行，5/5 保持 v1。CNINFO 全文片段路线 45/45 attempts 无直接证据，未调用 Gemini |
| 定性评分 v2 MILESTONE-004 | ⛔ `NO_QUALIFIED_FRAME_SOURCE` | v1.1 技术关闭；v1.2 三调用完整执行但均为 Tushare `40203`，capability FAIL；attempt 已消费，禁止重试/回退，继续须另开 v1.3 |
| Phase 6 多框架激活 | 🔶 report-only | Framework B 仍不写生产；B label 0/20 自然结案 |
| Phase 7 选股宇宙 | ⏸ 未启动 | 待 Phase 6 完成或明确降级策略 |

optimizer.py 启动门槛：Framework A 30d 结案 ≥ 100（已满足） AND `hit_rate_vs_300 > 55%`（待验证）。  
Framework B 重启：在 scorer.py 加回 "B"，另写生产化 spec 并经独立审查。

运维门禁是动态状态，不以本快照替代实时检查。恢复或重装 `daily` / `outcome-update` cron 前必须重新运行 `scripts/check_market_data_readiness.py --scope cron`；2026-07-15 当天 probe 的 daily/index/calendar/close cross-check 全部 PASS，当前为 `READY_CRON`，managed cron 已重新安装。

---

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool.

- 投研分析 / 个股研究 → invoke /a-stock-research
- 监控 / 持仓追踪 → invoke /a-stock-monitor
- 产品方向 / 功能讨论 → invoke /office-hours
- 架构 / 工程审查 → invoke /plan-eng-review
- Bug / 错误排查 → invoke /investigate
- Code review → invoke /review
- 部署 / PR → invoke /ship


<!-- ai-collab:routing -->
## AI协作模式（ai-collab）
本项目采用 ai-collab 三方协作模式（Claude=PM/架构师，codex=执行，QA工具=审查）。
新会话只要看到本项目有 `.claude/ai-collab/config.yaml`，对多步骤编码任务默认
调用 collab-pipeline skill 执行"实现→审查→提交"循环；完成一个完整plan或一批
任务后调用 collab-retro skill 复盘。配置与历史记录见 `.claude/ai-collab/`。
<!-- /ai-collab:routing -->
