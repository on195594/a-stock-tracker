# a-stock-tracker

A 股选股与方法论验证项目。当前定位是 **Framework A 定量评分 + Gemini 定性补充 + L3 买点过滤 + 数据治理审计**：每日对固定 watchlist 股票评分，写入 SQLite，追踪 30/60/90 天收益率，并用 `accuracy-report` 评估相对沪深 300 的表现。

> 当前阶段目标是积累数据、验证 Framework A 和 L3 买点层的有效性。项目不做自动交易、下单、持仓账户管理或投资建议。

## 当前状态

- 主分支：`master`
- 当前阶段：Phase 5 L3 v2 已接入生产评分与推送；Phase 6 仍处于 report-only 观察期；来源约束的定性评分 v2 已完成 MILESTONE-002、MILESTONE-003 和 M4 capture-first 工具链；17:15 实执封存为 incomplete（Tushare 日限频 + SWS 严格 TLS `SSLError`），不可组装，下一次 live attempt、Reviewer 与 evidence shadow/cutover 均需新授权
- 生产框架：`SUPPORTED_FRAMEWORKS = {"A"}`；Framework B 历史数据保留，Phase 6 前不得启用生产写入
- watchlist：35 只，维护在 `config.py`
- 评分阈值：`buy_strong=44`、`buy_moderate=35`、`buy_light=26`
- L3 主推条件：`total_score >= buy_strong AND l3_v2_signal = 1`；v2 为 `0/NULL` 的高分股票进入候补而不是主推
- 数据库：`tracker.db`，核心表为 `stock_fundamentals`、`predictions`、`index_prices`、`qualitative_scores`
- 最新顶层路线图：`docs/evolution-roadmap.md`

已落地能力：

- Framework A：ROE、净利润增速、负债率、毛利率、PB 分位和定性项的 80 分制评分。
- Gemini 定性评分：`moat`、`market_pos`、`sentiment`，30 天缓存，失败时 all-or-nothing fallback 到固定值。
- Outcome 追踪：记录 30/60/90 天收益、沪深 300 benchmark 和 generated `alpha_*d`。
- L3 买点层：v1 保留用于历史审计；生产 daily 优先读取 QFQ 日线计算 `l3_v2_signal`，Telegram 主推已切换到 v2。
- 定性评分 v2：MILESTONE-002 合同、MILESTONE-003 文件型 shadow seam，以及 MILESTONE-004 离线审计 API/capture-first exporter 已完成；incoming 现有经 Playwright MCP 封存的 2026-07-15 SZSE XLSX、provenance 和哈希；2026-07-15 Tushare 总市值及申万成分尚未成功封存，下一次真实 capture 和 Reviewer 仍须分别授权。
- Telegram 推送：日报分为主推、候补和雷达；只有强分且 L3 v2 通过的股票进入主推，发送失败不阻断 daily。
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

# 仅验证定性评分 v2 context，不发网络请求、不创建 artifact
python3 scripts/run_qualitative_v2_shadow.py \
  --context tests/fixtures/qualitative_v2_empty_context.json

# 显式执行一次隔离 shadow；需要 GEMINI_API_KEY，输出仅写 JSONL artifact
python3 scripts/run_qualitative_v2_shadow.py \
  --context path/to/approved-context.json \
  --legacy-scores path/to/legacy-scores.json \
  --output artifacts/qualitative_v2_shadow.jsonl \
  --execute

# 删除某只股票的本地历史数据
python3 pipeline.py remove 601857
```

MILESTONE-004 的审计 API 仍保持离线；历史 frame 输入另提供独立的 `capture`、`recover`、`assemble` CLI。
其中只有 `capture` 可能联网，并且必须先读取机器可验证、绑定 exact 33-call matrix 的新授权；`assemble` 不读取 token
或实例化网络客户端。当前只有 SZSE 局部静态包，尚未创建真实 frame stage。13:34 的旧响应不可恢复，详见
[`docs/runbooks/milestone-004-capture-first.md`](docs/runbooks/milestone-004-capture-first.md)。Reviewer 子进程仍需后续单独授权；
工具链或既往输入阶段授权都不构成 MILESTONE-004 审计完成或 MILESTONE-005 批准。

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

- 每周六 10:00：运行 `weekly`
- 每周一 09:30：运行 Phase 6 weekly PM loop
- 工作日 16:00：采集 QFQ 日线
- 工作日 16:30：运行 `daily`
- 工作日 17:00：运行 `outcome-update`

`cron-setup.sh` 会先运行 `scripts/check_market_data_readiness.py --scope cron`。只有最新 Tushare probe 同时通过 daily 写入门禁与 index/calendar 能力门禁时，才新增 `daily` / `outcome-update`；否则只配置 weekly。

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
- `lib/market_data.py`：行情 provider 兼容入口；协议原语和 Tushare/BaoStock 实现来自 `a-stock-lib==0.2.0`，本地只保留环境门禁和 cache service。
- `a_stock_lib.providers.tushare_quotes`：Tushare Pro 行情 provider，覆盖评分价、L3 日线、outcome 和沪深 300 指数日线。
- `a_stock_lib.providers.baostock_quotes`：BaoStock 行情 provider，仅作为 Tushare fallback 或显式 backfill 源。
- `lib/fetcher.py`：AKShare、腾讯 fallback、百度估值等数据读取。
- `lib/data_quality.py`：required/degradable/derived 字段质量模型。
- `lib/entry_signal.py`：L3 v1 买点层纯计算 seam。
- `lib/l3_v2.py` / `lib/l3_v2_pipeline.py`：L3 v2 纯计算规则与 QFQ 优先的生产包装层。
- `qualitative_v2_contract.py` / `qualitative_v2_types.py` / `qualitative_v2_taxonomy.py` / `qualitative_v2_schema.py` / `qualitative_v2_prompt.py` / `qualitative_v2_validator.py`：定性评分 v2 的本地合同和 fail-closed 语义边界。
- `qualitative_v2_client.py` / `qualitative_v2_shadow.py` / `scripts/run_qualitative_v2_shadow.py`：物理隔离的 Gemini shadow transport、JSONL persistence 和显式 CLI；不被生产 pipeline 导入。
- `docs/runbooks/qualitative-v2-shadow.md`：shadow 输入、执行、artifact、去重和停止条件。
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
- `l3_v2_signal=1/0/NULL` 分别表示 v2 通过、拒绝、不可用；生产主推只接受 `1`，`NULL` fail-closed。
- `daily_bars.adjusted='none'` 是历史未复权数据，`'qfq'` 是 L3 v2 权威输入；`daily_bars.volume_unit` 必须有明确单位，unknown 或混合单位窗口会拒绝 L3 计算。
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
pytest tests/test_qualitative_v2_types.py tests/test_qualitative_v2_taxonomy.py tests/test_qualitative_v2_validator.py tests/test_qualitative_v2_schema.py tests/test_qualitative_v2_prompt.py -q
pytest tests/test_qualitative_v2_client.py tests/test_qualitative_v2_shadow.py -q
```

代码质量检查：

```bash
ruff check .
ruff format --check .
mypy
git diff --check
git status --short
```

提交前标准检查请优先使用项目虚拟环境，避免误用全局 Python：

```bash
source .venv/bin/activate
pytest tests/ -q
ruff check .
ruff format --check .
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
- 确认当天是否存在 `total_score >= buy_strong AND l3_v2_signal=1` 的记录；v2 为 `0/NULL` 时只进入候补。
