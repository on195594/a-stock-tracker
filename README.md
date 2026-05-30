# a-stock-tracker

A 股选股与方法论验证项目。当前定位是 **Framework A 定量评分 + 数据治理 + 只读审查辅助**：每日对 watchlist 股票评分，记录到 SQLite，跟踪 30/60/90 天收益率，并用 accuracy-report 评估相对沪深 300 的命中情况。

> 当前阶段目标是数据积累与框架验证，不是自动交易、持仓管理或投资建议系统。

## 当前状态

- 主线分支：`master`
- 当前治理阶段：Phase A-F 已完成
- 最近验证：`pytest tests/ -q` → `92 passed, 1 skipped`
- 数据治理计划：`docs/plans/2026-05-30-data-governance-structure-boundary-execution-plan.md`
- 数据源 registry：`docs/data-source-registry.yaml`

已落地的治理能力：

- 数据源 registry：记录 scorer 输入、报告字段、缓存位置、刷新频率与失败语义。
- accuracy-report 合约测试：固定 Framework A 结案样本数必须与 DB anchor 一致，避免被全局样本污染。
- 数据质量模型：`lib/data_quality.py` 表达 required/degradable/derived 字段，以及 ok/missing/fallback/stale 状态。
- PB 分位 seam：`scorer.compute_daily_pb_percentile()` 负责 PB 分位纯计算，`pipeline.py` 只保留兼容 wrapper。
- 只读 reviewer schema：`lib/agent_reviewer.py` 限制 reviewer 只能输出说明性 commentary，不能覆盖 score、threshold、trade_action 或 DB write 指令。

## 安装

```bash
cd ~/a-stock-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

复制或创建 `.env`，按需填写：

```bash
cp .env.example .env  # 如无 .env.example，则手动创建 .env
```

`.env` 示例：

```text
GEMINI_API_KEY=你的_Gemini_API_Key
TELEGRAM_BOT_TOKEN=你的_Bot_Token
TELEGRAM_CHAT_ID=你的_Chat_ID
```

说明：

- 三项均可选。
- 未配置 Gemini 时，定性评分走固定 fallback。
- 未配置 Telegram 时，推送静默跳过，不影响评分流程。
- `.env` 必须保持在 git 外，不要提交真实密钥。

## 常用命令

### 初始化基本面缓存

```bash
python3 pipeline.py init
```

预计耗时约 15 分钟，取决于网络和 watchlist 规模。

### 手动运行每日评分

```bash
python3 pipeline.py daily
```

### 手动更新预测结果

```bash
python3 pipeline.py outcome-update
```

### 查看准确率报告

```bash
python3 pipeline.py accuracy-report
```

注意：当前测试/报告流程可能会更新 tracked 文件 `accuracy_report.txt`。如果只是验证代码，运行后确认是否需要恢复：

```bash
git status --short
git restore accuracy_report.txt
```

### 配置 cron

```bash
bash cron-setup.sh
```

默认规则：

- 工作日 16:30：运行 `daily`
- 工作日 17:00：运行 `outcome-update`

## Watchlist 维护

### 添加股票

1. 编辑 `config.py` 中的 `WATCHLIST`。
2. 运行初始化命令补齐新股票缓存：

```bash
python3 pipeline.py init
```

### 删除股票

如果只是停止后续跟踪，只从 `config.py` 删除即可，历史记录会保留。

如果要删除该股票的本地历史数据：

```bash
python3 pipeline.py remove <股票代码>
# 示例
python3 pipeline.py remove 601857
```

该命令会删除 `stock_fundamentals` 和 `predictions` 中对应股票记录。删除后不可恢复，执行前确认目标代码。

## 核心模块

- `pipeline.py`：主编排器，支持 `init` / `daily` / `outcome-update` / `accuracy-report`。
- `scorer.py`：Framework A 定量评分和 PB 分位纯计算。
- `gemini_scorer.py`：Gemini 定性评分；失败或未配置时 fallback。
- `telegram_push.py`：Telegram 每日信号推送。
- `lib/cache.py`：SQLite 缓存与 predictions 表管理。
- `lib/fetcher.py`：AKShare 等数据源读取。
- `lib/data_quality.py`：数据质量状态模型，纯逻辑，无 DB/API/文件写入。
- `lib/agent_reviewer.py`：schema-only/fake reviewer，限制 reviewer 只输出只读 commentary。
- `weights.json`：评分权重与阈值。
- `config.py`：watchlist、数据库路径、日志目录。
- `docs/data-source-registry.yaml`：数据源、缓存、刷新与失败语义登记。

## 数据治理边界

当前项目明确不做：

- 自动交易、下单、持仓账户管理。
- 修改真实 DB 历史记录来“修好”报告。
- cron、Telegram、Gemini、Google Sheets 的隐式启用。
- 把 reviewer/LLM 输出作为 score、threshold、trade_action 或 DB 写入指令。
- 在测试中发起真实 AKShare、Gemini、Telegram 或 Google Sheets 调用。

关键规则：

- `predictions` 中的历史评分、`weights_hash` 和收益结果是审计数据，不应手动改写。
- `accuracy-report` 解释 Framework A 时，样本 anchor 必须过滤 `framework = 'A'`。
- `pb_percentile_10y` 的日度可计算性依赖 `price_at_score`、`bps` 和至少 12 项 `pb_hist_monthly`。
- reviewer 只能补充解释、异议、缺失数据说明和人工问题，不能覆盖 deterministic score/decision。

## 测试

```bash
# 全部测试
pytest tests/ -q

# 数据源 registry
pytest tests/test_data_source_registry.py -q

# 数据质量模型
pytest tests/test_data_quality.py -q

# scorer 与 PB 分位计算
pytest tests/test_scorer.py -q

# pipeline / accuracy-report 合约
pytest tests/test_pipeline.py -q

# reviewer schema
pytest tests/test_agent_reviewer.py -q

# spec / plan 结构检查
pytest tests/test_spec_structure.py -q
```

当前全量结果：

```text
92 passed, 1 skipped
```

## 代码质量

```bash
source .venv/bin/activate
ruff check . --exclude .venv
mypy pipeline.py scorer.py --ignore-missing-imports
```

提交前建议至少运行：

```bash
pytest tests/ -q
git diff --check
git status --short
```

如果 `accuracy_report.txt` 只是由测试生成的非目标变更，提交前恢复它。

## 设计与计划文档

- `docs/specs/2026-05-29-agent-engineering-governance-spec.md`：数据治理与结构边界 Spec。
- `docs/plans/2026-05-30-data-governance-structure-boundary-execution-plan.md`：Phase B-F 执行计划与 ledger。
- `docs/data-source-registry.yaml`：字段级数据源 registry。
- `docs/reviews/`：计划或实现审查记录。
- `docs/design.md`：早期整体架构说明。
- `docs/test-plan.md`：测试计划。
- `docs/impl-plan.md`：早期实施计划。

## 问题排查

### daily 执行超时

原因通常是 watchlist 过大或网络慢。先减少 watchlist，或改为手动分批运行。

### outcome-update 查询失败

通常是 16:30-17:00 间网络波动。可手动重跑：

```bash
python3 pipeline.py outcome-update
```

### predictions 表出现重复记录

正常情况下不会发生，`code/framework/score_date` 有唯一约束和 `INSERT OR IGNORE` 保护。检查命令：

```bash
sqlite3 tracker.db "SELECT COUNT(*), code, framework, score_date FROM predictions GROUP BY code, framework, score_date HAVING COUNT(*) > 1;"
```

### accuracy-report 样本不足

样本数小于 100 时只能作为观察，不应解释为统计显著结论。重点看相对沪深 300 的表现，而不是绝对收益命中率。

### Gemini 全部使用 fallback

可能原因：

- 未配置 `GEMINI_API_KEY`
- 模型不可用或 API 返回 404
- quota 超限
- 网络不可达

先确认 `.env` 存在且 key 有值，再用只读方式检查当前配置；不要把真实 key 打印到日志或提交到 git。

### Telegram 未收到推送

排查顺序：

1. 确认 `.env` 中 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID` 已填写。
2. 确认当日是否有达到推送阈值的信号。
3. 查看 `logs/` 下的运行日志。

低于阈值时不推送是正常行为。
