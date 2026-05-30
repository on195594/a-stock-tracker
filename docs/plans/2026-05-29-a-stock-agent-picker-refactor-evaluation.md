# A 股选股 Agent 应用重构评估与初创计划

创建时间：2026-05-29 21:00
目标项目：`/home/lin/a-stock-tracker`
阶段：初创讨论 / 只读评估 + 规划；未改业务代码

## 0. 改写后的请求

把原请求收敛为：

> 基于文章《Most AI Agents Fail in Production Because They’re Built Backwards》的工程原则，评估 `/home/lin/a-stock-tracker` 是否适合作为“只用于 A 股选股”的 Agent 应用底座；先只做现状审计、可复用资产识别、弃用清单、重构 vs 新建决策和分阶段计划。不得改历史预测数据，不做自动交易，不扩大到持仓管理/Google Sheets/多市场/泛投研。若继续实施，应先形成项目内 spec，再按小阶段验证。

改动点：把“重构”拆成可验证的边界、非边界、复用/弃用判断和 stop 条件，避免一上来写代码。

## 1. 当前基线证据

### 1.1 仓库与工作区

- Git 分支：`master`
- 当前工作区已有未提交改动：
  - 修改：`CLAUDE.md`, `accuracy_report.txt`, `docs/impl-plan.md`, `lib/cache.py`, `lib/fetcher.py`, `pipeline.py`, `weights.json`
  - 新增未跟踪：`.gstack/`, `REPAIR-PLAN.md`, `docs/evolution-roadmap.md`, `docs/lessons-learned.md`, `tests/test_fetcher.py`
- 最近提交：`217e238 feat: PB百分位因子 + Gemini漂移检测 + Framework B进度显示`

### 1.2 验证结果

- `python3 -m pytest tests/ -q`：`68 passed, 1 skipped in 16.50s`
- 系统 Python 下 `ruff` / `mypy` 不可用；项目 `.venv` 下可运行但有现存问题：
  - Ruff：16 个问题，主要是 E402、未使用 import、单行多 import。
  - Mypy：14 个问题，主要是 akshare/pandas stub、动态 import、若干类型不匹配。

### 1.3 代码规模与复杂度

- `pipeline.py`：880 行；`cmd_accuracy_report` 192 行，`cmd_daily` 135 行，`cmd_outcome_update` 100 行。
- `lib/cache.py`：851 行；混合了 stock fundamentals、analysis_results、holdings、predictions、index_prices、qualitative_scores、CLI 命令。
- `lib/fetcher.py`：538 行；`cmd_fetch` 179 行，直接承担多源抓取、字段解析、打印、人机 CLI。
- `scorer.py`：127 行，边界清晰，可高价值复用。
- `gemini_scorer.py`：136 行，职责较小，但目前 prompt 与 API 调用硬耦合。

### 1.4 当前数据资产

从 `tracker.db` 只读查询：

- `predictions`: 676 条
- `stock_fundamentals`: 38 条
- `qualitative_scores`: 35 条
- `index_prices`: 25 条
- 预测日期范围：`2026-04-21` 到 `2026-05-29`
- Framework 分布：A=603，B=73
- `outcome_30d is not null`: 110 条

注意：`accuracy_report.txt` 当前内容显示 0 条结案记录，和数据库只读统计不一致；后续实施前必须先把报告口径与 DB 查询口径对齐，不能从旧报告直接推断有效性。

## 2. 目标边界：只用于 A 股选股的 Agent 应用

### 2.1 核心产品循环

1. 定义 A 股候选宇宙：先支持静态 watchlist，后续再讨论动态候选池。
2. 拉取并缓存基础数据：基本面、估值、价格、指数基准。
3. 运行确定性评分/过滤：质量、估值、安全边际、买点过滤。
4. Agent 只做边界内决策辅助：解释候选、发现缺失数据、生成审查意见、提出下一步人工确认，而不是直接编排所有工具。
5. 记录每次选股快照与理由：可回放、可比较、不可改写历史。
6. 后验验证：30/60/90 天 outcome、相对沪深300、分层统计。
7. 输出候选清单和审计报告：Telegram/HTML/Markdown 均为展示层，不做真相来源。

### 2.2 非目标

- 不做自动下单。
- 不做持仓/交易账户管理。
- 不做 Google Sheets 作为数据层或守门人。
- 不做泛金融市场；仅 A 股。
- 不把 LLM 当总调度器，不允许 LLM 直接决定写 DB、删记录、改权重。
- 不改写已有 `predictions.total_score`、`weights_hash`、历史 outcome。
- 初期不做全市场高频扫描；动态池需先评估 AKShare 限流和缓存预算。

## 3. 用文章思路得到的架构原则

文章里的“built backwards / model-as-orchestrator”反模式，对本项目的落地解释是：

- 错误做法：从一个万能 Agent 开始，让它自己决定抓数据、评分、写库、发通知、解释结果。
- 正确做法：先建确定性系统边界，再把 Agent 放在窄接口上。

推荐分层：

```text
Data adapters      AKShare / 指数 / 财报 / Gemini补充信息
      ↓
Typed state        StockSnapshot / FactorSet / ScoreResult / CandidateDecision
      ↓
Deterministic core 数据质量门禁、评分、过滤、版本化、写入审计
      ↓
Agent reviewer     解释、异议、缺失字段建议、候选摘要；只能输出结构化建议
      ↓
Presentation       Telegram / HTML / Markdown / CLI
```

Agent 的权限应是：读结构化快照 + 输出解释/建议；不能直接调任意 API、不能绕过 scorer、不能修改历史数据。

## 4. 复用 / 弃用判断

### 4.1 应复用

- `scorer.py`
  - 价值：纯函数评分、测试覆盖好、边界小。
  - 复用方式：抽成 `src/stock_picker/scoring/`，保持 golden-master 测试。
- `weights.json`
  - 价值：已有 Framework A/B 权重、阈值和历史 hash 体系。
  - 复用方式：迁移为 versioned policy 文件；先只启用 A，B 作为历史/后续实验。
- `tracker.db` 的历史数据
  - 价值：真实运行记录，适合 golden-master、回放、报告口径校验。
  - 复用方式：只读迁移/导入，不原地改写。
- `tests/test_scorer.py`、部分 `tests/test_pipeline.py`
  - 价值：已经覆盖核心 scoring 和 outcome 语义。
  - 复用方式：迁移为新架构的回归测试。
- `_compute_daily_pb_percentile`、weights_hash、Generated Column 禁写规则
  - 价值：是已踩坑后的稳定业务约束。

### 4.2 可部分复用但需重写接口

- `pipeline.py`
  - 保留业务流程经验；不建议直接继续堆代码。
  - 拆成 `collect -> score -> decide -> persist -> report` 五个命令/服务。
- `lib/fetcher.py`
  - 保留字段提取逻辑与接口经验。
  - 重写为 adapter 层，避免 `cmd_fetch` 继续承担 179 行混合职责。
- `gemini_scorer.py`
  - 保留 all-or-nothing fallback、缓存 TTL、JSON 校验思想。
  - 重写为 `agent/reviewer.py` 或 `qualitative_provider.py`，temperature、schema、prompt、缓存策略显式配置。
- Telegram 推送
  - 保留为展示层；不得影响评分写入和候选判定。

### 4.3 建议弃用或隔离

- `sheets_sync.py`：不符合“只用于 A 股选股 Agent 应用”的核心闭环，先移出核心路径。
- `lib/cache.py` 中 holdings / analysis_results / flags 相关 CLI：来自旧投研 skill，和选股 Agent MVP 混杂。
- `cron-setup.sh`：先不复用；新项目应先有手动 CLI 和 fake/offline smoke，再谈 cron。
- 旧 Phase 路线里“自动调权 / optimizer / 多框架全量扩展”：先冻结，等新 MVP 稳定后再重开。
- 任何会修改真实历史预测的修复脚本：必须单独审批。

## 5. 重构 vs 新建结论

推荐：**新建项目更有利，但不是从零重写业务；采用“绿地骨架 + 选择性移植”的方式。**

理由：

1. 当前仓库已是可运行历史系统，包含真实 DB、cron、日志、未提交改动；原地大改容易破坏历史记录和日常任务。
2. 现有代码把数据抓取、缓存建表、旧 CLI、评分、报告、推送、里程碑提醒混在一起；如果目标是 Agent 应用，继续原地抽象成本高。
3. 新目标是“只用于 A 股选股”，边界比当前仓库更窄；新骨架能把非目标直接挡在外面。
4. 现有项目最有价值的是规则、数据、测试和踩坑约束，而不是文件结构。
5. 新项目可以先离线/fake 数据跑通完整 Agent 应用闭环，再只读导入旧 DB 验证一致性，风险更低。

不建议：直接在 `/home/lin/a-stock-tracker` 上改成 Agent 应用主线。

建议路径：

- 保留 `/home/lin/a-stock-tracker` 为 legacy baseline 和数据/规则来源。
- 新建候选项目，例如 `/home/lin/a-stock-agent-picker`。
- 从旧项目复制/迁移：scorer、weights、测试、schema 约束、只读 DB adapter。
- 当新项目验证通过后，再决定是否归档旧项目或让旧项目只承担数据采集。

## 6. Phase 0：真正开工前必须澄清的问题

如果以下任一问题答案会改变架构，应先暂停：

1. 候选宇宙：初期是否只用现有 35/38 只 watchlist？还是一开始就要全市场粗筛？
2. Agent 权限：Agent 是否只做解释/审查/建议？还是允许它触发数据刷新、写入候选决策？建议初期只允许前者。
3. 输出形式：首个 MVP 是 CLI/Markdown，还是 Telegram 每日推送？建议先 CLI + Markdown。
4. Gemini 使用：定性评分继续用 Gemini，还是先全部 fake/offline，等确定性闭环稳定后再接？建议先支持 fake provider。
5. 历史数据：旧 `tracker.db` 只读导入，还是允许迁移生成新 DB？建议只读导入 + 新 DB 写新记录。
6. 是否保留原 daily cron：建议重构期间不动原 cron，避免丢数据。

## 7. 推荐 MVP Spec 草案

### R1. 输入

- 支持静态 watchlist：`code`, `name`, `framework` 可选，默认 A。
- 支持从 legacy DB 只读读取已有 fundamentals / predictions。
- 支持 fake 数据包用于无网络测试。

### R2. 输出

- 生成 `CandidateDecision`：`code`, `name`, `score`, `rank`, `decision`, `reasons`, `missing_fields`, `data_quality`, `policy_version`。
- 输出 Markdown/JSON 报告。
- 首期不推 Telegram，不写 Sheets。

### R3. 决策边界

- Scoring 必须是确定性函数。
- Agent reviewer 只能读取 `ScoreResult` 和 `CandidateDecision`，输出结构化解释。
- Agent 输出不能覆盖 score、threshold、weights_hash。

### R4. 数据安全

- 旧 DB 默认只读打开。
- 新 DB 只写新项目自己的 run/candidate 表。
- 禁止 UPDATE 旧 predictions 的 score/hash/outcome。

### R5. 验证

- 单元测试：scorer golden-master、schema、decision policy。
- 集成测试：fake 数据跑完整 `collect -> score -> decide -> report`。
- 回放测试：从旧 DB 读取一日样本，输出与旧 score 在允许误差内一致。

## 8. 分阶段计划

### Phase 0 — 决策冻结与 spec

Minimum landing change：

- 在新项目或当前仓库文档中保存正式 `mission.md` / `requirements.md` / `architecture.md`。
- 明确旧项目只读、Agent 权限、MVP 输出、是否新建项目路径。

验证：

- 文档中存在 R1-R5 和非目标。
- 用户明确批准后才开始建代码骨架。

### Phase 1 — 绿地骨架

Minimum landing change：

- 新建 Python 3.13 + uv 项目。
- 包结构：
  - `src/stock_picker/models.py`
  - `src/stock_picker/scoring.py`
  - `src/stock_picker/policy.py`
  - `src/stock_picker/adapters/fake.py`
  - `src/stock_picker/reporting.py`
  - `tests/test_import.py`
  - `tests/test_fake_pipeline.py`
- 先不接 AKShare、Gemini、Telegram。

验证：

- `uv run pytest`
- `uv run ruff check .`
- `uv run ty check` 或按项目约定替代。

### Phase 2 — 移植 scorer + weights golden-master

Minimum landing change：

- 把 `scorer.py` 逻辑迁移为纯模块。
- 迁移 `weights.json` 为 policy fixture。
- 把旧测试迁移为 golden-master。

验证：

- 同一输入下新旧 scorer 输出一致。
- weights_hash 与旧项目一致。

### Phase 3 — legacy DB 只读 adapter

Minimum landing change：

- 新增 `LegacyTrackerReader`，只读连接 `/home/lin/a-stock-tracker/tracker.db`。
- 支持读取指定日期/代码 fundamentals 和 predictions。
- 不写旧 DB。

验证：

- smoke 读取一日样本。
- 尝试写旧 DB 的路径不存在或测试确认失败。

### Phase 4 — Agent reviewer 窄接口

Minimum landing change：

- 定义 `ReviewInput` / `ReviewOutput` schema。
- fake reviewer 先通过；Gemini reviewer 后接。
- reviewer 只能返回解释、风险和人工确认问题。

验证：

- reviewer 不能修改 score/decision。
- 非 JSON / 超时 / 越界时 fail-closed 到“无 agent 解释，但保留确定性决策”。

### Phase 5 — 报告与人工使用闭环

Minimum landing change：

- 输出本地 Markdown/JSON 报告。
- 报告包含候选排名、理由、缺失字段、数据口径、免责声明。

验证：

- fake pipeline 生成报告。
- legacy replay 生成报告。

## 9. Stop / 喊停条件

应立即暂停并重新确认：

- 要求 Agent 自动下单或直接给“买入保证”。
- 要求改写旧 `predictions` 历史评分/收益。
- 要求一开始就全市场扫描但没有 API 预算/限流设计。
- 要求继续堆在 `pipeline.py`，而不是先拆确定性核心。
- Gemini/LLM 输出被要求作为唯一评分来源。
- 计划修改 `.env`、credentials、cron、真实推送、真实 DB 写入但没有明确批准和回滚方案。

## 10. 下一步建议

我建议先做 Phase 0，不写业务代码：

1. 你确认是否接受“新项目为主，旧项目只读复用”的方向。
2. 确认新项目路径，建议 `/home/lin/a-stock-agent-picker`。
3. 确认 MVP：静态 watchlist + fake/offline 数据 + scorer 迁移 + Markdown/JSON 输出。
4. 我再生成正式 spec，并在你批准后进入 Phase 1 骨架。
