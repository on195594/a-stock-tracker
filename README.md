# a-stock-tracker

A 股选股与买入决策支持项目。系统以提高风险调整后收益的决策胜率为目标，不承诺收益：每日对固定 watchlist 股票评分，写入 SQLite，追踪 30/60/90 天收益率，并用 `accuracy-report` 评估相对沪深 300 的表现。

> 当前阶段目标是验证 Framework A 的排序能力和 L3 风险过滤的增量价值。项目不做自动交易、下单、持仓账户管理或投资建议。

## 当前状态

- 主分支：`master`
- 当前阶段：三阶段路线的阶段一，先证明现有系统；Framework B 保持 report-only，M4/M5 冻结
- 生产框架：`SUPPORTED_FRAMEWORKS = {"A"}`；Framework B 历史数据保留，未经独立验证和明确批准不得启用生产写入
- watchlist：35 只，维护在 `a_stock_tracker/config.py`
- 评分阈值：`buy_strong=44`、`buy_moderate=35`、`buy_light=26`
- L3 主推条件：`total_score >= buy_strong AND l3_v2_signal = 1`；v2 为 `0/NULL` 的高分股票进入候补而不是主推
- 数据库：`tracker.db`，核心表为 `stock_fundamentals`、`predictions`、`index_prices`、`qualitative_scores`
- 最新顶层路线图：`docs/evolution-roadmap.md`

本轮方向复盘见
[`阶段性总结报告`](docs/reviews/2026-07-28-product-direction-and-engineering-subtraction-review.md)，
后续工作以 [`演进路线图`](docs/evolution-roadmap.md) 和
[`项目状态`](docs/project-status.md) 为准。

已落地能力：

- Framework A：ROE、净利润增速、负债率、毛利率、PB 分位和定性项的 80 分制评分。
- Gemini 定性评分：`moat`、`market_pos`、`sentiment`，30 天缓存，失败时 all-or-nothing fallback 到固定值。
- Outcome 追踪：记录 30/60/90 天收益、沪深 300 benchmark 和 generated `alpha_*d`。
- L3 风险过滤层：v1 保留用于历史审计；生产 daily 优先读取 QFQ 日线计算 `l3_v2_signal`，Telegram 主推已切换到 v2；买点有效性仍待阶段一验证。
- 定性评分 v2 研究链路：MILESTONE-002 合同、MILESTONE-003 文件型 shadow seam 已完成；M4 v1.3.2 的 authorization、freeze、golden、oracle、attestation 和多角色审批实现已退役，仍可从 Git 历史恢复；轻量 builder 已完成本地 frame/sample。研究链路与下述生产 canary 保持隔离。
- 定性评分 v2 生产读路径：`.env` 已切换为 `QUALITATIVE_V2_MODE=on`，35 股全部进入 v2 选择器，结果存储与 v1 隔离；合法 partial output 可按维度采用 v2、缺证据维度回退 v1并记录为 `hybrid_v2`。当前 `qualitative_scores_v2` 为 6 行：603606 及 000963/002050/600036/600900/601088 使用 moat/market_pos v2、sentiment v1；其余 29 股无合法 v2 行时逐股回退 v1。
- M5 fixture-first：已提供固定 36 股 sample 的 synthetic bundle 校验、批量 blind-reference/shadow/support-audit 编排和聚合 gate；该历史研究能力现已冻结，不推进真实 bundle 或外部调用。
- Telegram 推送：日报分为主推、候补和雷达；只有强分且 L3 v2 通过的股票进入主推，发送失败不阻断 daily。
- Google Sheets 同步：展示层能力，失败只记录 warning，不是数据真相来源。
- 数据治理：`docs/data-source-registry.yaml` 记录字段来源、缓存、刷新、fallback 和失败语义。
- TuShare 三域生产主源：估值/市值、通用财务指标和分红事实已于 2026-07-21 强切完成；运行时为 `a-stock-lib==0.4.1`，35 股由独立 shadow/readiness 单事务物化。生产入口、日志与回滚见 `docs/runbooks/tushare-primary-production.md`。
- 只读 reviewer schema：`a_stock_tracker/integrations/agent_reviewer.py` 限制 reviewer 只能输出 commentary，不能覆盖分数、阈值、交易动作或 DB 写入。

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
# 新部署安全默认 off；当前生产实际为 on。复制此模板会让所有股票走 v1，决定取值前请阅读 CLAUDE.md“Phase 状态快照”中的“定性评分 v2 生产读路径”。
QUALITATIVE_V2_MODE=off
```

说明：

- 以上配置均可选，但未配置 `TUSHARE_TOKEN` 时行情 provider 保持 disabled，`daily` 不会写入新的评分记录。
- 未配置 Gemini 时，定性评分使用固定 fallback。
- 未配置 Telegram 时，推送静默跳过，不影响评分和写库。
- 启用 Tushare 前先运行 `python3 scripts/probe_tushare_market_data.py`，确认 `daily`、`index_daily`、`trade_cal` 权限和字段可用。
- 行情 provider 为 TuShare-only；缺 token、权限不足或请求失败时 fail-closed，不使用第二行情源补写。
- `QUALITATIVE_V2_MODE` 仅接受 `off`、`canary`、`on`。当前生产使用 `on`；合法 partial v2 记录逐维回退 v1，整行不存在、过期、损坏或全维不足时逐股回退 v1。
- `.env` 必须保持在 git 外，不要提交真实密钥。

## 常用命令

```bash
# 首次初始化或新增股票后补齐基本面缓存
python3 pipeline.py init

# 每周刷新 TuShare 财务/分红并物化
python3 -m scripts.run_tushare_primary_production_cycle weekly

# 工作日采集当日估值并物化；默认使用本地日期
python3 -m scripts.run_tushare_primary_production_cycle daily

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

# 生产 Fast Lane：先对真实 watchlist canary 做零凭证/零 artifact 预检
python3 scripts/run_qualitative_v2_production.py preview \
  --contexts path/to/exact-five-contexts \
  --scope canary

# 只读生产验收：PASS=exit 0，ROLLBACK=exit 2；不读取模型凭证、不写 .env/DB/artifact
python3 scripts/check_qualitative_v2_production.py

# 删除某只股票在 predictions 和 stock_fundamentals 表中的记录
python3 pipeline.py remove 601857
```

需要按某个交易日复验 qualitative v2 自然运行时，可在验收命令后增加
`--require-score-date YYYY-MM-DD`，日期必须替换为实际已完成的 `score_date`。
历史 CNINFO canary 授权已经过期，其真实采集命令不再作为常用操作或授权模板。

M4/M5 已冻结为历史研究能力，因此不再把其 probe、bundle 或模型命令列为常用操作。
既有 frame/sample、失败记录和 sealed identifier 继续保留，详见
[`轻量运行简报`](docs/reviews/2026-07-18-m4-frame-lite-run.md)。

`accuracy-report` 默认写入 `config.ACCURACY_REPORT_PATH`，生产路径为被 Git 忽略的 `artifacts/reports/accuracy-report.txt`。测试会把该路径隔离到临时目录，运行报告不会产生 tracked 文件变更。

当前报告包含：

- Framework A 总体分层表现和五分位单调性检验。
- 2026-05-15 后 post-fix 样本专区，避免新旧数据口径混合解释。
- L3 历史买点层统计；阶段一需补充 v2 风险门禁的状态、收益与回撤对照。
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

- 每周六 10:00：运行 TuShare financial/dividend weekly cycle
- 每周一 09:30：运行 Phase 6 weekly PM loop
- 工作日 16:00：采集 QFQ 日线
- 工作日 17:15：运行 TuShare valuation daily cycle
- 工作日 17:30：运行 `daily`
- 工作日 17:45：只读运行 qualitative-v2 production acceptance；`ROLLBACK` 触发 Telegram 告警
- 工作日 18:00：运行 `outcome-update`

`cron-setup.sh` 会独立安装 TuShare primary daily/weekly cycle。旧行情
`scripts/check_market_data_readiness.py --scope cron` 仍负责 daily/index/calendar/close 门禁：READY 时安装评分链；报告 stale/HOLD 时不据此新启用旧行情能力，但若评分链在运行前已存在则保留并迁移到新时序。两套 readiness 不可互相替代。

cron、Telegram、Gemini、Google Sheets 都不应在测试中真实触发。

## Watchlist

添加股票：

1. 编辑 `a_stock_tracker/config.py` 中的 `WATCHLIST`。
2. 运行：

```bash
python3 pipeline.py init
```

删除股票：

- 如果只是停止后续跟踪，从 `WATCHLIST` 删除即可，历史记录保留。
- 如果要删除 `predictions` 和 `stock_fundamentals` 中该股票的记录，运行 `python3 pipeline.py remove <股票代码>`；其他表不受影响，执行前需要确认目标代码。

## 核心模块

- `pipeline.py`：向后兼容的薄入口；主编排实现在 `a_stock_tracker/cli.py`。
- `a_stock_tracker/scoring.py`：Framework A 确定性评分，breakpoints 线性插值，PB 分位纯计算。
- `a_stock_tracker/data/`：SQLite schema、行情 provider、TuShare shadow/ingestion/readiness/materialization 与 production cycle。
- `a_stock_tracker/signals/`：L3 v1/v2 纯计算与生产包装层。
- `a_stock_tracker/integrations/`：Gemini 定性评分与只读 reviewer 适配器。
- `a_stock_tracker/reporting/`：Telegram、Google Sheets 和 Framework B report-only 输出。
- `a_stock_tracker/qualitative/`：定性评分 v2 合同、校验、生产路径及 M4/M5 研究工作流。
- `a_stock_lib.providers.tushare_quotes`：Tushare Pro 行情 provider，覆盖评分价、L3 日线、outcome 和沪深 300 指数日线。
- `scripts/fetch_qfq_daily_bars_tushare.py`：TuShare `pro_bar(adj="qfq")` QFQ 采集入口。
- `config/weights.json`：评分权重与阈值。
- `config/qualitative/`：生产授权账本与只读验收基线。
- `docs/data-source-registry.yaml`：字段级数据源 registry。
- `docs/architecture.md`：完整目录约定和模块依赖方向。
- `AGENTS.md` / `tests/test_project_structure.py`：仓库级结构约束及其自动回归门禁。

## 数据语义

- `predictions` 是审计数据；历史 `total_score`、`weights_hash`、`outcome_*d`、`benchmark_*d` 不应手动改写。
- `alpha_*d` 是 SQLite generated column，禁止在 INSERT/UPDATE 中直接写入。
- `weights_hash` 只对 `config/weights.json["frameworks"]` 子树计算，`updated_at` 和 `version` 不影响 hash。
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
- 报告测试必须把输出路径隔离到临时目录，不得在项目根目录创建报告文件。

## 设计文档

- `docs/evolution-roadmap.md`：当前顶层路线图，后续 Phase 以此为基线。
- `docs/specs/2026-05-29-agent-engineering-governance-spec.md`：Agent 工程治理和边界 Spec。
- `docs/plans/2026-05-30-data-governance-structure-boundary-execution-plan.md`：数据治理与结构边界执行计划。
- `docs/specs/2026-05-30-phase5-l3-entry-signal-spec.md`：L3 买点层 Spec。
- `docs/plans/2026-05-30-phase5-l3-entry-signal-implementation-plan.md`：L3 买点层实施计划。
- `docs/data-source-registry.yaml`：字段、数据源、缓存和 fallback registry。
- `docs/runbooks/market-data-provider-recovery.md`：行情 provider 恢复 daily/outcome cron 的运行手册。
- `docs/specs/2026-07-20-tushare-primary-data-governance.md`：TuShare 主源治理、PIT、shadow 与 consumer 边界。
- `docs/specs/2026-07-21-tushare-three-domain-forced-cutover.md`：估值/财务/分红三域生产强切合同与实施结果。
- `docs/runbooks/tushare-primary-production.md`：TuShare daily/weekly cycle、readiness、诊断与回滚手册。
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
