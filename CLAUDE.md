# CLAUDE.md — a-stock-tracker

## 项目简介

A 股自动评分管道。每日盘后对 watchlist 股票运行 Framework A 定量评分，
记录到 SQLite predictions 表，追踪 30/60/90 天收益率，输出 benchmark 相对命中率报告。

**设计文档：** `docs/design.md`
**测试计划：** `docs/test-plan.md`
**实施计划：** `docs/impl-plan.md`
**进化路线图：** `docs/evolution-roadmap.md`
**踩坑知识库：** `docs/lessons-learned.md`
**旧版设计（存档）：** `docs/design-archive-20260410.md`

---

## 环境与运行

```bash
cd ~/code/project/work/a-stock-tracker
source .venv/bin/activate

# 首次初始化（预填 watchlist 数据）
python pipeline.py init

# 每日手动触发（cron 自动运行：工作日 16:30）
python pipeline.py daily

# 更新到期预测结果（cron：工作日 17:00）
python pipeline.py outcome-update

# 查看准确率报告
python pipeline.py accuracy-report

# 全量测试
pytest tests/ -v

# 单模块测试
pytest tests/test_scorer.py -v
pytest tests/test_pipeline.py -v
```

---

## 文件结构

```
a-stock-tracker/
├── scorer.py          # 评分引擎（breakpoints 线性插值，不调 AKShare）
├── pipeline.py        # 主编排器（init / daily / weekly / outcome-update / accuracy-report）
├── gemini_scorer.py   # Phase 3：Gemini 定性评分（30天缓存，all-or-nothing fallback）
├── telegram_push.py   # Phase 3：Telegram 每日信号推送（≥55分触发）
├── weights.json       # 模型权重（Phase3阈值：buy_strong=55，buy_moderate=45，buy_light=35）
├── config.py          # watchlist、DB_PATH、LOG_DIR
├── .env               # API Keys（GEMINI_API_KEY / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID）
├── requirements.txt   # akshare, pytest
├── cron-setup.sh      # 一键配置 crontab
├── tests/
│   ├── test_scorer.py       # 11 个用例（含 Phase 3 固定字段覆盖测试）
│   ├── test_pipeline.py     # 15 个用例
│   └── test_gemini_scorer.py # 5 个用例（正常/超时/非JSON/越界/缓存命中）
└── lib/               # 从 ~/.claude/skills/a-stock-research/ 复制，独立演进
    ├── fetcher.py     # 扩展版：新增 report_period 字段存储
    └── cache.py       # 扩展版：新增 predictions + index_prices + qualitative_scores 表
```

**lib/ 来源：** 从 `~/.claude/skills/a-stock-research/` 复制而来，**不是** symlink，
原 skill 保持不变。如需同步上游改动，手动 diff 后选择性合并。

---

## 核心数据模型

### predictions 表（核心）

| 字段 | 说明 |
|------|------|
| `code / framework / score_date` | UNIQUE 约束，幂等写入（INSERT OR IGNORE）|
| `weights_hash` | `md5(json.dumps(weights["frameworks"], sort_keys=True))[:8]` — **只对 frameworks 子树求 hash**，不包含 updated_at / version |
| `report_period` | 评分时依赖的财报所属期（YYYY-MM-DD），从 lib/fetcher 缓存中读取 |
| `price_at_score` | 来自 spot_em 快照的 `最新价` 字段，daily 批量抓取后按 code 过滤 |
| `outcome_*d` | `(outcome_price / price_at_score - 1) * 100`，**百分比格式**（如 +5.2，不是绝对价格）|
| `benchmark_*d` | 沪深300同期涨跌幅%，取法相同 |
| `alpha_*d` | SQLite GENERATED COLUMN = outcome - benchmark，**不要直接写此列** |
| `estimate_flag` | 1 = outcome 用的是最近可用价（到期日停牌，10 日内找到替代价）|

### stock_fundamentals 表（重要字段说明）

| 字段（data JSON key） | 说明 |
|------|------|
| `bps` | 每股净资产（元），来自同花顺财务接口 `每股净资产` 列 |
| `pb_hist_monthly` | PB月度历史序列（list[float]，约731个点），weekly fetch 从百度估值接口获取 |
| `gross_margin` | 毛利率（%），`(营业收入-营业成本)/营业收入×100`，近3年年报均值，金融行业为NULL |

**日度 PB 分位计算**：`cmd_daily()` 在评分前执行 `_compute_daily_pb_percentile(price, data)`，
用当日收盘价÷bps=current_pb，ranked in pb_hist_monthly → `data["pb_percentile_10y"]`。
此操作纯内存，不写数据库，不触碰 TTL，不需要额外 API 调用。

### index_prices 表

缓存沪深300（000300）历史日收盘价。outcome-update 启动时增量更新，已有日期跳过。
**不要直接操作此表**，由 pipeline.py outcome-update 管理。

---

## 硬性规则

### 禁止行为

- **禁止直接写入 `alpha_*d` 列** — 这是 SQLite Generated Column，写入会报错
- **禁止修改 lib/cache.py 的 DB_PATH** 使其指回 `~/.claude/skills/a-stock-research/cache.db`
- **禁止在 predictions 表中 UPDATE 已有记录的 `total_score` 或 `weights_hash`** — 用于实验追踪，改了就破坏了数据可比性
- **禁止在 scorer.py 中对 `invert: true` 字段做额外数值变换** — breakpoints 已按"高值→低分"排列，解释器对所有字段用相同的插值逻辑，不需要也不允许加特殊处理
- **禁止在测试中发起真实 AKShare 网络请求** — 所有 AKShare 调用必须 Mock
- **禁止硬编码股票代码或路径** — watchlist 在 config.py，路径在 config.py
- **禁止让 Sheets sync 失败阻断 daily cron** — Google Sheets 是展示层，SQLite 是真相来源。sheets_sync.py 失败只记 WARNING，不影响评分写入。Sheets 永远不能成为数据层的守门人。

### 必须行为

- **修改 weights.json 后**：下次 daily 运行前必须知晓 weights_hash 会变。若当日已有 predictions 记录，pipeline 会检测到 hash 冲突并退出。需手动删除当日记录或等次日运行。
- **新增字段到 predictions 表**：必须同步更新 `get_db()` 建表 DDL，并验证 INSERT 语句也包含该字段
- **新增测试用例**：必须用 `tmp_path` fixture 隔离数据库，不使用真实 `tracker.db`
- **pipeline 启动时**：必须保留 `assert sqlite3.sqlite_version_info >= (3, 31, 0)` 检查（Generated Column 依赖）
- **accuracy-report 输出**：记录数 < 100 时头部必须显示样本不足警告；必须包含选择性偏差免责声明
- **评分上限系统性偏移警告（2026-05-11 确认，2026-05-12 部分修复，2026-05-15 全修复）**：
  - `gross_margin`：2026-05-15 起改用新浪利润表 `(营业收入-营业成本)/营业收入` 自动计算，金融行业（银行/保险/证券等）仍返回 NULL（毛利率对其无意义）。2026-05-14 及以前记录 gross_margin=NULL，不可跨期比较。
  - `pb_percentile_10y`：2026-05-15 起改为**日度实时计算**（`current_pb = 当日收盘价 / bps`，ranked in `pb_hist_monthly`），每日随股价变化。weekly fetch 存储 `bps` 和 `pb_hist_monthly`（约731点），daily 纯内存计算无额外API调用。
  - `pe_percentile_10y` 已于 2026-05-12 被 `pb_percentile_10y` 替代，weights_hash 同步变更。
  - **跨期比较注意**：2026-05-14 前（gross_margin=NULL，pb_percentile 月度静态）vs 2026-05-15 后（两字段均已修复）存在系统性分数差，avg_score 约上升 4-5 分。Phase 4 optimizer 训练时需按 score_date 分层处理。

---

## 评分引擎规范

### Breakpoints 插值规则

```python
# scorer.py 中的插值逻辑（所有字段统一，包括 invert 字段）
def interpolate(value, breakpoints):
    if value <= breakpoints[0][0]:
        return breakpoints[0][1]       # 低于最小值 → 取最小 score
    if value >= breakpoints[-1][0]:
        return breakpoints[-1][1]      # 高于最大值 → 取最大 score
    for i in range(len(breakpoints) - 1):
        x0, y0 = breakpoints[i]
        x1, y1 = breakpoints[i + 1]
        if x0 <= value <= x1:
            return y0 + (value - x0) / (x1 - x0) * (y1 - y0)
```

`invert: true` 字段（`debt_ratio`, `pb_percentile_10y`）的 breakpoints 已经按
"metric 值从小到大，score 从大到小"排列。**不需要对输入值做任何变换，直接传入插值函数**。

### data_quality 计算

- 分母 = **非固定字段数**（Phase 1 = 5：roe_3y_avg / net_profit_growth / debt_ratio / gross_margin / pb_percentile_10y）
- 固定字段（moat_fixed / market_pos_fixed / sentiment_fixed）不计入分母
- `data_quality < 0.5` → 抛出 `InsufficientDataError`（pipeline 跳过该股，记日志）

### weights_hash 计算（关键）

```python
import hashlib, json
weights_hash = hashlib.md5(
    json.dumps(weights["frameworks"], sort_keys=True).encode()
).hexdigest()[:8]
# ⚠️ 只对 weights["frameworks"] 子树求 hash，不包含 weights["updated_at"] 或 weights["version"]
```

---

## outcome-update 逻辑摘要

```
到期判断：score_date + 30 自然日 ≤ today → 30d 到期
价格查找：先查 index_prices / spot_em 精确到期日价格
停牌处理：向前找最近 10 自然日内可用价 → estimate_flag=1（覆盖黄金周 7 天停牌）
超出 10 日：outcome 置 NULL（estimate_flag 保持 0）
benchmark 失败：outcome 正常写入，benchmark_*d 留 NULL（不中断）
60d/90d：未到期的跳过，不覆盖
```

**outcome 单位**：始终是百分比，如 `+5.2` 表示涨 5.2%，`-3.1` 表示跌 3.1%。
**不是绝对价格差**，不是小数（0.052），就是百分比数字。

---

## 常见陷阱

| 陷阱 | 正确做法 |
|------|---------|
| `cmd_batch()` 不会自动抓新股票 | 新增 watchlist 股票后必须先 `pipeline.py init` |
| 修改 `updated_at` 触发 hash 变更 | weights_hash 只对 `frameworks` 子树求 hash，`updated_at` 不影响 |
| accuracy-report 的 AVG 跳过 NULL | SQLite `AVG()` 自动忽略 NULL，60d/90d 未到期列不需要额外 WHERE |
| 重新运行 daily 新增了行 | 检查 UNIQUE(code, framework, score_date) 约束是否生效 |
| `alpha_30d` 查询为 NULL | 检查 `outcome_30d` 和 `benchmark_30d` 是否都有值（VIRTUAL 列任一为 NULL → 结果 NULL）|
| Phase 1 strong 信号 < 5 条 | 检查 `accuracy-report` 分层统计；阈值已于 2026-05-12 永久校准为 44/35/26，`threshold_adjusted` 字段保留但恒为 0（`_adjusted` key 已废弃）|

---

## Phase 3 说明（2026-04-26 上线）

### 定性评分升级（Gemini 接入）

- `gemini_scorer.get_qualitative_score(code, name)` 替代 phase1_fixed 硬编码值
- 返回格式：`{"moat": int, "market_pos": int, "sentiment": int}`
- 30 天缓存，缓存存储于 `qualitative_scores` 表
- **all-or-nothing fallback**：任何字段失败 → 全部用 phase1_fixed（moat=5, market_pos=2, sentiment=3）
- 值域：moat 1-10，market_pos 1-5，sentiment 1-5（与 weights.json max_score 一致）

### 评分可比性注意事项

- 2026-04-21 存量记录（9条）：定性分为固定值（moat=5, market_pos=2, sentiment=3）
- Phase 3 后新记录：定性分由 Gemini 填写（可能 3-9 不等）
- **weights_hash 不变**（weights.json frameworks 子树未修改）
- accuracy-report 跨时期比较需注意"定性升级"造成的系统性偏移

### benchmark 修复（P0-B）

- `_ensure_index_prices` 新增腾讯 fallback：`ak.stock_zh_index_daily_tx(symbol="sh000300")`
- 腾讯接口列名：`date`, `close`（已验证）；代码内有 assert 保护，列名变更会立即 raise

### Telegram 推送

- `telegram_push.push_daily_signals()` 在 cmd_daily 末尾调用
- 评分 ≥ buy_strong（当前 44）触发推送
- 需在 .env 中配置 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID

## 扩展约定（Phase 4 预留）

- 新增 Framework B/C/D/E/F：在 `weights.json["frameworks"]` 下新增 key，`scorer.py` 中 `SUPPORTED_FRAMEWORKS` 集合加入新 framework
- 评分变化告警（D5）：当定性分变化 >10 时推送，需 2-3 周历史基线后实现
- optimizer.py：**明确启动门槛（2026-05-12 CEO review）：** ① Framework A 30d 结案 ≥ 100 条 AND ② accuracy-report 任一信号层级 `hit_rate_vs_300 > 55%` 且样本 ≥ 20。预期 2026-06/07。
- **Framework B 当前暂停（2026-05-12）**：已有 73 条历史记录保留，SUPPORTED_FRAMEWORKS={"A"} 不产生新记录。重启：在 scorer.py 加回 "B"。

---

## Phase 1 统计声明（必须了解）

1. **样本不足**：20 只股票 × 3 个月 ≈ 60 条 30d 结案记录，不具备统计显著性
2. **选择性偏差**：watchlist 是手动维护的已知标的，命中率不代表框架泛化能力
3. **牛市通胀**：`hit_rate_30d_abs > 50%` 不代表框架有效，看 `hit_rate_30d_vs_300`
4. **Phase 1 目标**：数据积累，不是得出结论

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
