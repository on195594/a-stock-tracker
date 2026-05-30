# A 股选股 Agent 工程治理 Spec

创建时间：2026-05-29
目标项目：`/home/lin/a-stock-tracker`
阶段：下一阶段治理 Spec；不进入实现；不改业务代码
依据：文章《Most AI Agents Fail in Production Because They’re Built Backwards》、本项目现状审计、Claude 前置评审与 `docs/reviews/2026-05-29-claude-agent-engineering-governance-spec-review.md`

## 0. 改写后的请求

把原请求收敛为：

> 在 `/home/lin/a-stock-tracker` 中，把“不要倒着构建 AI Agent”的工程原则落成项目治理总原则，并为下一阶段写一份可执行 Spec。下一阶段优先级为：先治理数据源与数据质量，再做代码结构优化，最后只在确定性核心之上接入窄接口 Agent reviewer。产品边界严格限定为 A 股选股，不做自动交易、持仓管理、多市场泛投研、Google Sheets 数据层或 LLM 总调度器。

改动点：

- 将“基于文章原则”转化为项目内可验证的工程治理规则。
- 将“数据准确性”改成可执行的数据源可用性、字段口径、回放校验、异常报告与审计闭环。
- 将“代码结构优化”排在数据治理之后，避免先重构再发现数据基线不可信。
- 明确本阶段只写 Spec，不授权改业务代码、cron、DB、credentials 或通知通道。

## 1. 方向判断

结论：方向基本正确，但必须调整顺序。

本 Spec 采纳“原地演化”策略（参见 Claude 评审对新建项目 vs 原地演化分歧的收口建议），弃用背景评估文档中的“新建绿地项目”默认路径；只有当后续证据证明原项目不可安全演进时，才重新讨论迁移。

正确点：

- 数据源治理应优先于 Agent 能力。
- 代码结构需要优化，但不能为了“像 Agent 项目”而重构。
- “只做选股”是必要边界，能挡住自动交易、持仓、泛投研和多市场扩张。

需要制止的错误路径：

- 不应先做 Agent 外壳、提示词编排器或万能 orchestrator。
- 不应先做大规模代码拆包，尤其当前工作区仍有未提交改动。
- 不应把 LLM 输出作为评分、阈值、权重或 DB 写入的权威来源。
- 不应因为数据源偶发失败就让 Agent 自己找替代渠道并写入历史记录；数据源 fallback 必须是确定性 adapter 规则。
- 不应把“数据准确性”理解成一次性清洗，而应视为持续的口径、来源、校验、回放和报告一致性治理。

## 2. 当前基线

### 2.1 项目状态

- Repo：`/home/lin/a-stock-tracker`
- Branch：`master`
- 工作区：已有多处未提交改动，包括 `pipeline.py`、`lib/cache.py`、`lib/fetcher.py`、`weights.json`、`tests/test_pipeline.py` 等。
- 最新关键修复：`accuracy-report` 的样本不足判断已从全局结案数修正为 Framework A 结案数，验证结果为 `69 passed, 1 skipped`。

### 2.2 已知架构事实

- `scorer.py` 边界较清晰，是确定性评分核心。
- `pipeline.py` 聚合了 daily、outcome-update、accuracy-report、通知、里程碑等职责。
- `lib/cache.py` 混合了选股核心数据、旧 skill 遗留表、CLI 与 schema 迁移逻辑。
- `lib/fetcher.py` 同时承担数据 adapter、字段解析和 CLI 输出。
- `gemini_scorer.py` 已有窄接口雏形：schema 校验、all-or-nothing fallback、缓存 TTL。

### 2.3 已知数据治理风险

- 多 framework 并行后，全局统计容易污染 Framework A 的有效性判断。
- 数据源包括 AKShare / 腾讯 fallback / 百度估值接口 / spot 快照 / 沪深300指数缓存，任何单点失败都会影响评分或 outcome。
- `gross_margin`、`pb_percentile_10y` 曾发生口径修复，pre-fix 与 post-fix 分数存在系统性偏移，不可混用解释。
- `weights_hash`、`report_period`、`price_at_score`、`outcome_*d`、`benchmark_*d` 是可回放与审计的关键字段。

## 3. 治理总原则

### GP1. Deterministic core first

先保证确定性数据管道、评分、过滤、报告口径可信，再考虑 Agent reviewer。任何 Agent 能力不得成为数据获取、评分、写库、阈值调整或外部通知的唯一执行者。

### GP2. Data provenance over model fluency

每个进入评分的字段必须能追溯来源、采集时间、财报期、fallback 路径和缺失原因。报告必须优先暴露数据质量，不用流畅解释掩盖数据不完整。

### GP3. Narrow reviewer, not orchestrator

Agent 只允许读取结构化输入并输出结构化审查意见：解释、风险提示、缺失字段说明、人工确认问题。Agent 不允许直接写 DB、改分数、改权重、改阈值、触发下单、触发 cron 或绕过 deterministic gates。

### GP4. Historical records are evidence, not workspace

历史 `predictions`、`outcome`、`weights_hash`、`price_at_score`、`report_period` 默认不可改。任何历史数据修复必须独立审批、先备份、可回滚、有 before/after 统计和审查记录。

### GP5. One product boundary: A 股选股

本项目只服务 A 股选股候选生成与审查，不做自动交易、持仓账户管理、Google Sheets 数据层、多市场扩张、泛投研报告平台或自动调权系统。

### GP6. Improve structure only around proven seams

代码结构优化只围绕已证实的 seam 做：data adapters、typed state、scoring core、persistence/reporting、agent reviewer。禁止为了抽象而抽象，禁止一次性大重构 `pipeline.py` / `lib/cache.py`。

### GP7. Reports are contracts

`accuracy-report`、daily log、outcome update summary 不是展示文字，而是治理合约。报告中的样本数、framework、weights_hash、时间窗口、缺失统计必须能被 DB 查询复现。

## 4. 下一阶段目标

下一阶段命名：`Data Governance + Structure Boundary Phase`

目标：

1. 建立数据源治理基线，确保数据获取渠道可用、fallback 明确、字段口径可追溯、报告与 DB 一致。
2. 在不破坏现有运行路径的前提下，识别并逐步抽出稳定边界，避免继续向 `pipeline.py` 和 `lib/cache.py` 堆职责。
3. 固化“只做选股”的产品边界，为后续 `agent_reviewer.py` 提供窄接口输入契约，但本阶段不要求接入真实 LLM reviewer。

非目标：

- 不新建项目作为默认路径。
- 不重写全量 pipeline。
- 不迁移或改写旧 DB。
- 不接管 cron。
- 不新增真实通知副作用。
- 不接入自动交易、持仓管理、账户数据或 Google Sheets 数据层。
- 不启动 optimizer 或自动调权。
- 不让 LLM 作为数据源 fallback 的自由搜索者。

## 5. 需求

### R1. 数据源清单与所有权

必须建立数据源清单，覆盖至少：

- 基本面数据来源。
- 财报期字段来源。
- 当日价格 `price_at_score` 来源。
- PB 历史序列与 BPS 来源。
- 沪深300 benchmark 来源。
- Gemini 定性评分来源及 fallback 常量。

每个数据源必须记录：主路径、fallback 路径、字段映射、失败表现、缓存位置、刷新时机、是否影响评分、是否影响 outcome。

验收：必须存在 `docs/data-source-registry.yaml`；禁止仅用 Markdown 替代。后续 smoke/pytest 必须能读取该 YAML，并断言 registry 覆盖所有 scorer 输入字段与关键报告字段，能够机器回答“某字段来自哪里、什么时候抓、失败后怎么办”。

### R2. 数据质量门禁

每次评分前必须形成 per-stock 数据质量结果，至少包括：

- 必需字段是否缺失。
- 可降级字段是否使用 fallback。
- `report_period` 是否存在。
- `price_at_score` 是否存在。
- `pb_percentile_10y` 是否由当日价格实时计算，还是使用缓存旧值。
- `data_quality` 是否低于 scorer 门槛。

验收：fake 或测试 DB 样本能产生结构化 data-quality 结果；缺失字段不会被 Agent 文案掩盖。

### R3. 报告与 DB 一致性

`accuracy-report` 的关键数字必须能由明确 SQL 复现：

- Framework A 结案数。
- 分 framework 总记录 / 30d 结案数。
- 各信号层级样本数、hit rate、alpha。
- weights_hash 分布，至少在审计视图中可查。
- NULL outcome 数量与原因分类的后续设计。

参考 SQL anchor（Framework A 30d 结案数）：

```sql
SELECT COUNT(*) FROM predictions
WHERE outcome_30d IS NOT NULL AND framework = 'A';
```

验收：新增或保留测试覆盖“全局样本数不污染 Framework A 判断”；报告 smoke 输出中的关键数字必须等于上述只读 SQL 查询结果，并加入测试套件 helper（建议命名 `assert_report_matches_db`）。

### R4. 数据源 fallback 可观测

任何 fallback 不得静默吞掉数据质量风险。

要求：

- 主路径失败、fallback 成功、fallback 失败三种状态可区分。
- fallback 来源写入 log 或结构化结果。
- fallback 不得改变字段单位。
- fallback 不得绕过缓存/限流策略。

验收：mock 测试覆盖主路径失败和 fallback 成功；报告或日志能看到 fallback 发生。

### R5. 代码结构 seam

下一阶段只能围绕以下 seam 做小步优化：

- `data_sources` / adapters：外部数据获取与字段映射。
- `data_quality`：字段完整性、fallback、口径状态。
- `scoring`：确定性评分与 hash。
- `reporting`：报告生成与 DB 查询一致性。
- `agent_reviewer`：未来窄接口，只读 structured input。

验收：任何新增模块必须有单元测试；任何移动函数必须有 golden-master 或等价回归测试。

### R6. Agent reviewer 边界

本阶段只定义接口，不接入真实 Agent 执行。

允许的 reviewer 输入：

- `code`, `name`
- `score_result`
- `data_quality_result`
- `policy_version` / `weights_hash`
- `missing_fields`
- `report_period`
- `risk_flags`

允许的 reviewer 输出：

- `explanation`
- `objections`
- `missing_data_comment`
- `human_questions`
- `confidence_note`

禁止的 reviewer 输出：

- 新分数。
- 新阈值。
- 新权重。
- DB 写入指令。
- 买入/卖出/仓位指令。
- 数据源自由抓取指令。

验收：接口 schema 文档存在；后续实现必须测试 reviewer 无法覆盖 deterministic decision。

### R7. 只做选股边界

代码、文档和报告中不得把本项目扩展为：

- 自动下单。
- 持仓和账户管理。
- 交易执行系统。
- 多市场投研平台。
- 泛财经内容生成平台。
- Google Sheets 驱动的数据系统。

验收：下一阶段 plan 必须包含 boundary checklist；发现上述扩展即 stop 并请用户重新确认。

## 6. 分层目标架构

目标方向：

```text
Data adapters
  -> Data quality / typed field state
  -> Deterministic scoring / filtering
  -> Persistence + report queries
  -> Agent reviewer schema（只读）
  -> Presentation（CLI / Markdown / Telegram）
```

当前阶段不要求一次性落成完整分层。推荐顺序：

1. 先建立数据源 registry 与质量报告。
2. 再把 `accuracy-report` 查询口径固化为可测试函数或明确 SQL contract。
3. 再把 `_compute_daily_pb_percentile` 迁移目标固定为 `scorer.py`，迁移前用 golden-master 测试锁定输出一致性。
4. 最后定义 `agent_reviewer.py` 的 schema-only 或 fake reviewer。

## 7. Phase 计划

### Phase A — 数据源治理 Spec 落地

类型：landing

Minimum landing change：

- 新增并修订 `docs/specs/2026-05-29-agent-engineering-governance-spec.md`。
- 新增 `tests/test_spec_structure.py`，用机器断言检查 Spec 存在 `GP1`、`R1`、`AC1`、`Stop 条件`、Phase Exit Gate、SQL anchor、`docs/data-source-registry.yaml` 等必要锚点。
- 将当前已通过测试的改动提交为 clean baseline commit（建议 message：`chore: Phase A baseline — accuracy-report fix + governance spec`）后，才开始 Phase B。

非目标：不改 Python 运行路径，不改 DB，不改 cron。

验证：

- `pytest tests/test_spec_structure.py -q` 通过。
- `git diff --check` 通过。
- `git status --short` 在 baseline commit 后为空，或只剩明确不属于 Phase A 的未跟踪本地文件。

Exit Gate：Spec 结构测试通过，baseline commit 已落地且工作区干净，才允许进入 Phase B。

### Phase B — 数据源 registry 与数据质量矩阵

类型：landing

Minimum landing change：

- 新增 `docs/data-source-registry.yaml`；禁止仅以 Markdown 作为 registry 替代。
- 列出字段、来源、fallback、缓存、刷新、失败策略。
- 为每个评分字段标记 `required` / `degradable` / `derived`。
- 新增 smoke/pytest 断言：registry 覆盖所有 scorer 输入字段与关键报告字段。

非目标：不接真实新数据源；不替换 AKShare。

验证：

- pytest 断言 registry 包含 price、benchmark、bps、pb_hist、gross_margin、Gemini fallback。
- pytest 断言 registry 覆盖当前 scoring 字段；人工 review 只用于补充字段语义，不作为唯一通过条件。

Exit Gate：smoke/pytest 输出 `registry covers all scored fields: OK` 或等价断言通过后，Phase B 才视为完成。

### Phase C — 报告/DB 一致性测试加固

类型：landing

Minimum landing change：

- 把 `accuracy-report` 已修复口径固化为更精确测试。
- 增加只读 SQL smoke 或测试 helper（建议 `assert_report_matches_db`），验证报告关键数字与 R3 SQL anchor 一致。
- 至少覆盖 Framework A 30d 结案数：`SELECT COUNT(*) FROM predictions WHERE outcome_30d IS NOT NULL AND framework = 'A';`。

非目标：不重写整个 report renderer。

验证：

- 定向测试通过。
- 全量 `pytest tests/ -q` 通过。
- `python3 pipeline.py accuracy-report` smoke 输出中的 Framework A 结案数与 R3 SQL anchor 返回值一致。

Exit Gate：测试和 smoke 都证明报告数字与明确 SQL anchor 一致后，Phase C 才视为完成。

### Phase D — 数据质量结果模型

类型：landing

Minimum landing change：

- 新增最小数据质量结构，可先是 dataclass 或纯函数返回值。
- 在测试中覆盖缺失 `price_at_score`、缺失 `bps/pb_hist_monthly`、fallback 使用状态。

非目标：不立刻把所有 pipeline 路径改成新模型。

验证：

- 新增单元测试 RED/GREEN。
- 不影响现有 daily/outcome-update/accuracy-report 测试。

Exit Gate：数据质量结构能区分 required/degradable/derived、missing/fallback/ok 状态，且相关测试通过后，Phase D 才视为完成。

### Phase E — 代码 seam 小步拆分

类型：landing

Minimum landing change：

- 只选择一个 seam：先处理 `_compute_daily_pb_percentile`，迁移目标固定为 `scorer.py`。
- 为迁移前后输出一致性写 golden-master 测试。
- 保持 public CLI 行为不变。

非目标：不拆整个 `pipeline.py`，不重写 `lib/cache.py`。

验证：

- 迁移函数定向测试通过。
- 全量测试通过。
- `git diff` 显示改动范围小且不含 DB schema 变更。

Exit Gate：`_compute_daily_pb_percentile` 已在 `scorer.py` 有明确归属，golden-master 证明迁移前后结果一致，且 diff 不含 DB schema/cron/通知变更后，Phase E 才视为完成。

### Phase F — Agent reviewer schema-only

类型：landing

Minimum landing change：

- 定义 `ReviewInput` / `ReviewOutput` schema 或 dataclass。
- 实现 fake/no-op reviewer。
- 明确真实 Gemini reviewer 是后续阶段，需要单独审批。

非目标：不调用真实 LLM，不新增 API key，不写通知。

验证：

- 测试证明 reviewer 输出不能覆盖 score/decision。
- 非 JSON / 超时 / 空输出的真实 LLM 路径暂不实现。

Exit Gate：fake/no-op reviewer 只能产生只读审查意见，测试证明它不能改写 deterministic score/decision 后，Phase F 才视为完成。

## 8. 验收标准

### AC1. Spec 完整性

本 Spec 必须包含：治理原则、范围、非目标、需求、阶段计划、验证、stop 条件，并由 `tests/test_spec_structure.py` 机器断言验证，不以“人工读到本段文字”作为通过标准。

### AC2. 数据优先

任何后续 implementation plan 必须先处理 Phase B/C，再进入 Phase E/F。若计划先做 Agent reviewer 或大重构，应视为不符合本 Spec。

### AC3. 决策边界

后续代码不得让 LLM 直接影响以下字段或行为：`total_score`、`weights_hash`、`thresholds`、`predictions` 历史记录、`outcome_*d`、cron、真实通知、交易行为。

### AC4. 运行安全

所有涉及 DB 写入、历史数据修复、cron、credentials、Telegram、真实 Gemini 调用的步骤，都需要单独批准、备份、rollback 和 smoke 证据。

单独批准格式必须至少写明：`批准范围`、`目标文件/表/外部系统`、`允许的副作用`、`备份路径或备份命令`、`rollback 命令`、`smoke 命令`、`有效阶段`。缺少任一字段时，不得把聊天中的泛化同意解释为执行授权。

### AC5. 可回放

评分与报告必须保留足够元数据，使一个历史 run 能被解释：输入字段、来源状态、权重 hash、framework、report_period、price_at_score、benchmark 来源。

## 9. Stop 条件

出现以下任一情况必须暂停并重新确认：

- 用户要求自动下单、仓位建议或账户操作。
- 计划直接修改历史 `predictions` / `outcome` / `weights_hash`。
- 计划让 LLM 直接决定评分、阈值、权重或数据源 fallback。
- 计划绕过数据源治理先做 Agent reviewer。
- 计划一次性重构 `pipeline.py` / `lib/cache.py` 大块逻辑。
- 计划新增真实外部副作用：cron、Telegram、Gemini API、credentials、Google Sheets。
- 报告数字与 DB 查询再次不一致。
- 测试或 smoke 无法证明行为未退化。

## 10. 后续执行建议

推荐下一步不是写 Agent，而是创建数据源 registry：

1. 保存本 Spec，并新增/运行 `tests/test_spec_structure.py`。
2. 将当前已通过测试的工作区提交为 Phase A clean baseline commit。
3. 对当前 scoring / outcome / report 字段建立 `docs/data-source-registry.yaml`。
4. 用 registry 反查 `pipeline.py` / `lib/fetcher.py` / `lib/cache.py` 是否有遗漏字段。
5. 再写 Phase B/C 的 implementation plan。
6. Phase B/C/D 通过后，才进入 `_compute_daily_pb_percentile` → `scorer.py` 的 seam 迁移；Phase E 通过后才讨论 `agent_reviewer.py` schema。

## 11. 审查问题清单

请后续 reviewer 重点检查：

- 本 Spec 是否仍然把 Agent 放在确定性核心之后。
- 数据源治理是否足够具体，可转化为 registry 和测试。
- 是否有隐性扩展到交易、持仓、多市场或泛投研。
- 是否有隐藏 DB/cron/credentials/notification 副作用。
- Phase 顺序是否坚持数据治理优先。
