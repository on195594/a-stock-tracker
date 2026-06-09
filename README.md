# a-stock-tracker

A 股选股与方法论验证项目。当前定位是 **Framework A 定量评分 + Gemini 定性补充 + L3 买点过滤 + 数据治理审计**：每日对固定 watchlist 股票评分，写入 SQLite，追踪 30/60/90 天收益率，并用 `accuracy-report` 评估相对沪深 300 的表现。

> 当前阶段目标是积累数据、验证 Framework A 和 L3 买点层的有效性。项目不做自动交易、下单、持仓账户管理或投资建议。

## 当前状态

- 主分支：`master`
- 当前阶段：数据治理 Phase A-F 已完成；Phase 5 L3 买点层 v1 已实现；Phase 6 仍处于 report-only 准备期
- 生产框架：`SUPPORTED_FRAMEWORKS = {"A"}`；Framework B 历史数据保留，Phase 6 前不得启用生产写入
- watchlist：35 只，维护在 `config.py`
- 评分阈值：`buy_strong=44`、`buy_moderate=35`、`buy_light=26`
- L3 推送条件：`total_score >= buy_strong AND entry_signal = 1`
- 数据库：`tracker.db`，核心表为 `stock_fundamentals`、`predictions`、`index_prices`、`qualitative_scores`
- 最新顶层路线图：`docs/evolution-roadmap.md`

已落地能力：

- Framework A：ROE、净利润增速、负债率、毛利率、PB 分位和定性项的 80 分制评分。
- Gemini 定性评分：`moat`、`market_pos`、`sentiment`，30 天缓存，失败时 all-or-nothing fallback 到固定值。
- Outcome 追踪：记录 30/60/90 天收益、沪深 300 benchmark 和 generated `alpha_*d`。
- L3 买点层：`lib/entry_signal.py` 用 MA60、MA120、5/20 日量能计算 `entry_signal`，版本为 `v1`。
- Telegram 推送：只推送强信号且 L3 通过的股票；失败不阻断 daily。
- Google Sheets 同步：展示层能力，失败只记录 warning，不是数据真相来源。
- 数据治理：`docs/data-source-registry.yaml` 记录字段来源、缓存、刷新、fallback 和失败语义。
- 只读 reviewer schema：`lib/agent_reviewer.py` 限制 reviewer 只能输出 commentary，不能覆盖分数、阈值、交易动作或 DB 写入。

## 安装

```bash
cd ~/a-stock-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

创建 `.env`，按需填写：

```text
GEMINI_API_KEY=你的_Gemini_API_Key
TELEGRAM_BOT_TOKEN=你的_Bot_Token
TELEGRAM_CHAT_ID=你的_Chat_ID
TUSHARE_TOKEN=你的_Tushare_Pro_Token
MARKET_DATA_ALLOW_BAOSTOCK_ONLY=0
```

说明：

- 以上配置均可选，但未配置 `TUSHARE_TOKEN` 时行情 provider 保持 disabled，`daily` 不会写入新的评分记录。
- 未配置 Gemini 时，定性评分使用固定 fallback。
- 未配置 Telegram 时，推送静默跳过，不影响评分和写库。
- 启用 Tushare 前先运行 `python3 scripts/probe_tushare_market_data.py`，确认 `daily`、`index_daily`、`trade_cal` 权限和字段可用。
- `MARKET_DATA_ALLOW_BAOSTOCK_ONLY=1` 只允许 `market-data-backfill` 使用 BaoStock；不会让 `daily` 写入新评分。
- `.env` 必须保持在 git 外，不要提交真实密钥。

## 常用命令

```bash
# 首次初始化或新增股票后补齐基本面缓存
python3 pipeline.py init

# 每周刷新基本面缓存
python3 pipeline.py weekly

# 手动运行每日评分
python3 pipeline.py daily

# 更新到期预测的 30/60/90 天 outcome
python3 pipeline.py outcome-update

# 预热行情日线并重算已有 L3 metadata，不重算历史总分
python3 pipeline.py market-data-backfill --start 2025-01-01 --end 2026-06-09

# 生成准确率报告
python3 pipeline.py accuracy-report

# 删除某只股票的本地历史数据
python3 pipeline.py remove 601857
```

`accuracy-report` 默认写入 `config.ACCURACY_REPORT_PATH`，生产路径为项目根目录的 `accuracy_report.txt`。测试会把该路径隔离到临时目录；手动运行报告可能改写 tracked 文件，提交前需要确认是否属于目标变更。

当前报告包含：

- Framework A 总体分层表现和五分位单调性检验。
- 2026-05-15 后 post-fix 样本专区，避免新旧数据口径混合解释。
- L3 买点层统计。
- Gemini 评分稳定性。
- Framework B 重启门槛进度。
- watchlist 数据质量审计，区分 `cache_report_period` 和 `prediction_report_period` 缺失；金融行业 `gross_margin` 计为不适用。
- Framework B dry-run 对比；该部分只读、report-only，不写入 `predictions`。
- Phase 6 readiness 结论、生产化阻塞项和下一步；只有 post-fix outcome、数据质量、B dry-run 覆盖和 B label 自然结案同时满足时，才可讨论 Phase 6 生产化，且仍需保持不写 B predictions 的审阅流程。

## Cron

```bash
bash cron-setup.sh
```

默认规则：

- 工作日 16:30：运行 `daily`
- 工作日 17:00：运行 `outcome-update`

`cron-setup.sh` 会先运行 `scripts/check_market_data_readiness.py`。若 Tushare probe 尚未通过，只配置 weekly，不新增 `daily` / `outcome-update`。

cron、Telegram、Gemini、Google Sheets 都不应在测试中真实触发。

## Watchlist

添加股票：

1. 编辑 `config.py` 中的 `WATCHLIST`。
2. 运行：

```bash
python3 pipeline.py init
```

删除股票：

- 如果只是停止后续跟踪，从 `WATCHLIST` 删除即可，历史记录保留。
- 如果要删除本地历史数据，运行 `python3 pipeline.py remove <股票代码>`。该命令会删除 `stock_fundamentals` 和 `predictions` 中对应记录，执行前需要确认目标代码。

## 核心模块

- `pipeline.py`：主编排器，支持 `init` / `weekly` / `daily` / `outcome-update` / `accuracy-report` / `remove`。
- `scorer.py`：Framework A 确定性评分，breakpoints 线性插值，PB 分位纯计算。
- `gemini_scorer.py`：Gemini 定性评分、缓存、校验和 fallback。
- `telegram_push.py`：Telegram 信号推送，筛选 `buy_strong` 且 L3 通过的记录。
- `sheets_sync.py`：Google Sheets 展示层同步。
- `lib/cache.py`：SQLite schema、迁移和缓存管理。
- `lib/market_data.py`：行情 provider 边界；无 `TUSHARE_TOKEN` 时默认 disabled。
- `lib/tushare_provider.py`：Tushare Pro 行情 provider，覆盖评分价、L3 日线、outcome 和沪深 300 指数日线。
- `lib/baostock_provider.py`：BaoStock 行情 provider，仅作为 Tushare fallback 或显式 backfill 源。
- `lib/fetcher.py`：AKShare、腾讯 fallback、百度估值等数据读取。
- `lib/data_quality.py`：required/degradable/derived 字段质量模型。
- `lib/entry_signal.py`：L3 v1 买点层纯计算 seam。
- `lib/agent_reviewer.py`：只读 reviewer schema/fake reviewer。
- `weights.json`：评分权重与阈值。
- `docs/data-source-registry.yaml`：字段级数据源 registry。

## 数据语义

- `predictions` 是审计数据；历史 `total_score`、`weights_hash`、`outcome_*d`、`benchmark_*d` 不应手动改写。
- `alpha_*d` 是 SQLite generated column，禁止在 INSERT/UPDATE 中直接写入。
- `weights_hash` 只对 `weights.json["frameworks"]` 子树计算，`updated_at` 和 `version` 不影响 hash。
- `outcome_*d` 和 `benchmark_*d` 单位是百分比，例如 `5.2` 表示上涨 5.2%。
- `entry_signal=NULL AND entry_signal_version IS NULL` 表示 pre-L3 历史记录。
- `entry_signal=NULL AND entry_signal_version='v1'` 表示 L3 v1 已运行但不可计算。
- `entry_signal=0/1 AND entry_signal_version='v1'` 表示 L3 v1 已判断并拒绝/通过。
- `daily_bars.adjusted` 第一版统一写 `none`；`daily_bars.volume_unit` 必须有明确单位，unknown 或混合单位窗口会拒绝 L3 计算。
- `pb_percentile_10y` 日度可计算性依赖 `price_at_score`、`bps` 和足够的 `pb_hist_monthly`。
- 2026-05-14 前后存在毛利率和 PB 分位口径修复，跨期评分比较必须按 `score_date` 分层。

## 测试

```bash
# 全部测试
pytest tests/ -q

# 常用定向测试
pytest tests/test_scorer.py -q
pytest tests/test_pipeline.py -q
pytest tests/test_l3_entry_signal.py -q
pytest tests/test_telegram_push.py -q
pytest tests/test_data_source_registry.py -q
pytest tests/test_data_quality.py -q
pytest tests/test_agent_reviewer.py -q
pytest tests/test_sheets_sync.py -q
```

代码质量检查：

```bash
ruff check .
mypy
git diff --check
git status --short
```

提交前标准检查请优先使用项目虚拟环境，避免误用全局 Python：

```bash
source .venv/bin/activate
pytest tests/ -q
ruff check .
mypy
git diff --check
```

`ruff` 和 `mypy` 已列在 `requirements.txt`，配置集中在 `pyproject.toml`。如果命令不可用，先确认已激活虚拟环境并执行过 `pip install -r requirements.txt`。

测试约束：

- 所有 AKShare、Gemini、Telegram、Google Sheets 调用必须 mock。
- 测试数据库必须用 `tmp_path` 或 monkeypatch 隔离，不能写真实 `tracker.db`。
- 报告测试不应改写项目根目录的 tracked `accuracy_report.txt`。

## 设计文档

- `docs/evolution-roadmap.md`：当前顶层路线图，后续 Phase 以此为基线。
- `docs/specs/2026-05-29-agent-engineering-governance-spec.md`：Agent 工程治理和边界 Spec。
- `docs/plans/2026-05-30-data-governance-structure-boundary-execution-plan.md`：数据治理与结构边界执行计划。
- `docs/specs/2026-05-30-phase5-l3-entry-signal-spec.md`：L3 买点层 Spec。
- `docs/plans/2026-05-30-phase5-l3-entry-signal-implementation-plan.md`：L3 买点层实施计划。
- `docs/data-source-registry.yaml`：字段、数据源、缓存和 fallback registry。
- `docs/runbooks/market-data-provider-recovery.md`：行情 provider 恢复 daily/outcome cron 的运行手册。
- `docs/lessons-learned.md`：历史修复、陷阱和跨期解释注意事项。
- `docs/reviews/`：计划和实现审查记录。
- `docs/design.md`、`docs/impl-plan.md`、`docs/test-plan.md`：早期架构、实施和测试计划。

## 排查

daily 超时：

- 常见原因是 watchlist 较大或网络慢。可先减少 watchlist 或分批手动运行。

outcome-update 查询失败：

- 通常是网络或数据源波动，可手动重跑 `python3 pipeline.py outcome-update`。

accuracy-report 样本不足：

- 样本数小于报告阈值时只能观察趋势，不应解释为统计显著结论。重点看相对沪深 300 的 `alpha_30d` 和分层样本数。

Gemini 全部 fallback：

- 检查 `GEMINI_API_KEY`、模型可用性、quota 和网络。

Telegram 无推送：

- 检查 `.env` 中的 token/chat id。
- 确认当天是否存在 `total_score >= buy_strong AND entry_signal=1` 的记录。
