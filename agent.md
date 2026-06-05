# agent.md

本文件给后续 coding agent 使用。项目权威文档优先级为：

1. `docs/evolution-roadmap.md`
2. `docs/specs/2026-05-30-phase5-l3-entry-signal-spec.md`
3. `docs/specs/2026-05-29-agent-engineering-governance-spec.md`
4. `docs/data-source-registry.yaml`
5. `README.md`

`CLAUDE.md` 可作为历史上下文参考，但若与代码或上述文档冲突，以代码和上述文档为准。

## 项目定位

`a-stock-tracker` 是 A 股选股与评分验证管道，不是交易系统。当前生产路径是：

```text
AKShare / fallback data
  -> data quality
  -> Framework A deterministic scoring
  -> Gemini qualitative cache/fallback
  -> L3 entry signal filter
  -> SQLite predictions/outcomes
  -> accuracy-report / Telegram / Sheets presentation
```

当前只支持 Framework A。Framework B/C/D/E/F 不要擅自启用；Framework B 放在 Phase 6。Phase 6 当前仍是 report-only 准备期，允许增强报告和 dry-run 解释，但不得写 B predictions、不得修改 `weights.json`、不得把 B 加回生产框架。

## 工作前检查

```bash
cd ~/a-stock-tracker
git status --short
rg --files
```

如果工作区已有未提交修改，先判断是否与当前任务相关。不要回滚、覆盖或格式化用户已有改动。

## 常用命令

```bash
source .venv/bin/activate
python3 pipeline.py init
python3 pipeline.py weekly
python3 pipeline.py daily
python3 pipeline.py outcome-update
python3 pipeline.py accuracy-report
pytest tests/ -q
ruff check .
mypy
git diff --check
```

## 核心约束

- 不做自动交易、下单、仓位、账户或持仓管理。
- 不让 LLM/agent 输出覆盖 deterministic score、threshold、weights、trade_action 或 DB write。
- 不在测试中发起真实 AKShare、Gemini、Telegram 或 Google Sheets 调用。
- 不直接写 `alpha_30d`、`alpha_60d`、`alpha_90d`，它们是 SQLite generated columns。
- 不手动改写历史 `predictions.total_score`、`weights_hash`、`outcome_*d`、`benchmark_*d`。
- 不把 Framework B report-only readiness 当作生产化授权；B 生产写入必须另有明确计划和用户授权。
- 不把 `entry_signal=NULL` 当作 `0`。
- 不修改 `lib/cache.py` 的 DB 路径指向旧 skill 目录。
- 不把 Google Sheets 当作数据真相来源；SQLite 是 source of truth。

## 数据和评分语义

- 阈值来自 `weights.json["thresholds"]`，当前为 `44/35/26`。
- `weights_hash` 只对 `weights["frameworks"]` 子树计算。
- `outcome_*d` 和 `benchmark_*d` 是百分比，不是小数或价格差。
- `entry_signal_version` 当前为 `v1`。
- L3 v1 规则是严格 AND：最新收盘价高于 MA60、最新收盘价高于 MA120、5 日均量高于 20 日均量。
- L3 是过滤层，只影响推送和报告分层，不得反向修改 L1/L2 `total_score`。
- 2026-05-14 前后存在毛利率和 PB 分位口径修复，跨期统计必须按日期分层。

## 修改指南

修改评分逻辑时：

- 先读 `scorer.py`、`weights.json` 和相关测试。
- 如果改 `weights.json["frameworks"]`，会改变 `weights_hash`，当天已有记录时 daily 会冲突退出。
- `invert: true` 只是语义标记；breakpoints 已按业务含义排列，不要对输入额外取反。

修改 pipeline 或 DB schema 时：

- 同步更新 `lib/cache.py` DDL、迁移逻辑、INSERT/UPDATE 语句和测试。
- 迁移只能 additive，禁止 drop/recreate `predictions` 来修历史表。
- 新增 report 数字时，要能用明确 SQL 从 DB 复现。

修改 L3 时：

- 先更新 spec/roadmap 中的版本和语义。
- 升级 `ENTRY_SIGNAL_VERSION`，不要复用 `v1` 表达新规则。
- 保持 `lib/entry_signal.py` 为纯计算 seam，不写 DB，不发网络请求。

修改外部集成时：

- 外部 API 失败不得阻断 daily 主路径，除非该字段是明确的评分门禁。
- 新测试必须 mock 网络调用。
- fallback 必须可观测，不能静默改变字段单位或含义。

## 推荐测试范围

文档-only 改动：

```bash
git diff --check
```

评分或权重改动：

```bash
pytest tests/test_scorer.py tests/test_pipeline.py -q
```

L3 改动：

```bash
pytest tests/test_l3_entry_signal.py tests/test_pipeline.py tests/test_telegram_push.py -q
```

数据治理或 registry 改动：

```bash
pytest tests/test_data_source_registry.py tests/test_data_quality.py tests/test_pipeline.py -q
```

外部展示层改动：

```bash
pytest tests/test_telegram_push.py tests/test_sheets_sync.py -q
```

提交前优先运行：

```bash
pytest tests/ -q
ruff check .
mypy
git diff --check
```

`ruff` / `mypy` 依赖来自 `requirements.txt`，运行口径来自 `pyproject.toml`。如果当前环境提示命令或模块不存在，先安装/更新虚拟环境依赖。
