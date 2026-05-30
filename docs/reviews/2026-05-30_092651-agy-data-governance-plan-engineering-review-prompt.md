# AGY 工程视角只读审查请求

请以资深工程实施审查者视角，只读审查下面这份 implementation plan。不要修改文件，不要执行项目命令；只输出审查结论。

## 审查对象

- Repo: `/home/lin/a-stock-tracker`
- Plan: `docs/plans/2026-05-30-data-governance-structure-boundary-execution-plan.md`
- Source spec: `docs/specs/2026-05-29-agent-engineering-governance-spec.md`

## 审查目标

判断这份 plan 是否足够让 Hermes 自动执行 Phase B-F，且不需要人工干预，除非触发 plan/spec 中的 Stop 条件。

## 必须输出格式

1. Verdict：只能是以下之一：
   - `APPROVE_LANDING`
   - `REQUEST_BOUNDED_FIXES`
   - `BLOCKED_NEEDS_USER_DECISION`
   - `NO_ACTION_CLOSE`
2. Blocking findings：每条包含 problem / evidence location / why it matters / suggested fix。
3. Important findings：每条包含 problem / evidence location / why it matters / suggested fix。
4. Minor / nice-to-have。
5. Engineering design points already correct。
6. Minimal patch directions：如果需要修 plan，请给出最小可执行补丁方向，不要建议扩大产品范围。

## 审查重点

- Phase 顺序是否严格遵守 spec：Phase B/C/D 先于 Phase E/F。
- 每个 Phase 是否有足够具体的文件路径、补丁形状、命令、预期结果、回滚、提交边界。
- 命令是否真的可执行，是否依赖未声明 shell 状态或不存在文件。
- Python 代码片段是否与本项目 Python 3.13 / pytest / stdlib-only 约束兼容。
- `data_quality.py` 的 dataclass/StrEnum/JSON 输出是否存在可执行性问题。
- `agent_reviewer.py` 是否真的 schema-only，不会允许覆盖 deterministic score/decision。
- registry 测试是否可能假阳性，是否真的覆盖 scorer/report 字段。
- accuracy-report SQL anchor 测试是否足够稳定，不会被文案轻微变化打断。
- 是否有隐藏 DB/cron/credentials/Telegram/Google Sheets/Gemini/API/交易/持仓副作用。
- 是否存在会卡住 Hermes 自动执行的歧义。

## Source spec

```markdown
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

```

## Plan under review

```markdown
# Data Governance + Structure Boundary 执行计划

创建时间：2026-05-30 09:07
目标项目：`/home/lin/a-stock-tracker`
来源 Spec：`docs/specs/2026-05-29-agent-engineering-governance-spec.md`
执行方式：Hermes 自动执行；每个任务完成后提交一个小 commit；不需要人工干预，除非触发 Stop 条件。

## 0. 改写后的请求

原请求：

> 根据spec帮我写项目执行计划，计划尽可能详细，达到能够让hermes自动完成，无需人工干预。

改写为更可执行的提示：

> 在 `/home/lin/a-stock-tracker` 中，基于 `docs/specs/2026-05-29-agent-engineering-governance-spec.md`，写一份可由 Hermes 自动执行的分阶段 implementation plan。计划必须遵守 Phase 顺序：已完成 Phase A 后，先执行 Phase B/C/D，再执行 Phase E/F；每个任务包含明确文件路径、补丁形状、测试、验证命令、提交边界、回滚方式和 Stop 条件。计划不得授权 DB 历史写入、cron、真实通知、真实 Gemini 调用、credentials 或交易/持仓扩展。

改动说明：把“根据 spec”具体绑定到已存在的 spec 文件，把“自动完成”拆成任务级执行、验证、提交、回滚和 Stop 条件，并显式排除需要人工批准的副作用。

## 1. 当前基线证据

- 仓库：`/home/lin/a-stock-tracker`
- 分支：`master`
- Phase A baseline：已提交，HEAD 曾确认 `9c02b79 chore: 提交 Phase A 基线与治理 spec`
- 当前测试基线：`pytest tests/ -q` → `74 passed, 1 skipped in 16.10s`
- 当前 Spec 结构测试：`tests/test_spec_structure.py` 已存在
- 当前约束：不新增依赖；`requirements.txt` 只有 `akshare`、`pytest`、`ruff`、`mypy`、Google Sheets 依赖；因此 YAML 读取测试不能依赖 PyYAML。

## 2. 总目标

把 Spec 的下一阶段落成可执行变更：

1. 建立 `docs/data-source-registry.yaml`，并用 stdlib-only 测试验证 registry 覆盖 scorer 输入字段与关键报告字段。
2. 固化 `accuracy-report` 与 DB SQL anchor 的一致性，避免 Framework A 被全局样本污染。
3. 建立最小 `data_quality` 结果模型，能表达 required/degradable/derived、missing/fallback/ok 状态。
4. 小步迁移 `_compute_daily_pb_percentile` 到 `scorer.py`，用 golden-master 证明行为一致。
5. 定义 schema-only/fake `agent_reviewer.py`，证明 reviewer 不能覆盖 deterministic score/decision。

## 3. 全局边界

### 3.1 范围

允许修改：

- `docs/data-source-registry.yaml`
- `docs/plans/2026-05-30-data-governance-structure-boundary-execution-plan.md`
- `tests/test_data_source_registry.py`
- `tests/test_accuracy_report_contract.py` 或追加到 `tests/test_pipeline.py`
- `data_quality.py`
- `tests/test_data_quality.py`
- `scorer.py`
- `pipeline.py` 中 `_compute_daily_pb_percentile` 的兼容导入/调用边界
- `tests/test_scorer.py`
- `tests/test_pipeline.py`
- `agent_reviewer.py`
- `tests/test_agent_reviewer.py`
- 必要时小幅更新 `README.md` 或 `docs/impl-plan.md`，仅记录新模块入口，不改产品定位

### 3.2 非范围

禁止修改或执行：

- 不改 `tracker.db` 历史数据，不直接 UPDATE/DELETE `predictions`、`outcome_*d`、`weights_hash`
- 不新增 cron，不改 `cron-setup.sh`
- 不调用真实 Telegram/Google Sheets/Gemini，不新增 API key，不读取或提交 `.env`
- 不新增依赖，不更新 `requirements.txt`
- 不扩展到自动交易、持仓账户管理、多市场投研、Google Sheets 数据层、泛财经内容生成
- 不大规模重写 `pipeline.py` 或 `lib/cache.py`

### 3.3 Stop 条件

任一条件出现即停止执行、记录证据并请求用户确认：

- 需要写真实 DB 历史记录或改 schema 才能继续
- 需要真实外部 API/Telegram/Google Sheets/Gemini 调用才可验证
- 需要新增依赖才可通过测试
- 发现 spec 与现有代码冲突，且无法用小步兼容方式解决
- 测试无法恢复到全量通过
- 计划需要扩大到交易、持仓、账户、自动调权、cron 或凭据

## 4. 执行总规则

每个 Phase 按以下固定循环执行：

1. `git status --short`，确认只有预期改动；若有用户未提交改动，停止。
2. 写 RED 测试或结构断言。
3. 最小实现。
4. 运行定向测试。
5. 运行 `pytest tests/ -q`。
6. 运行 `git diff --check`。
7. 运行安全文本扫描：
   ```bash
   git diff --cached --name-only | xargs -r grep -nEi 'api[_-]?key|secret|token|password|BEGIN (RSA|OPENSSH)|shell=True|eval\(|pickle\.loads|DROP TABLE|DELETE FROM|UPDATE predictions' || true
   ```
   注意：若 `token/password` 只出现在文档的“禁止项”中，可人工标记为 false positive；若出现在代码或配置中，停止。
8. `git add` 仅 stage 本 Phase 文件。
9. `git diff --cached --check`。
10. `git commit -m "<类型>: <中文简述>"`。

提交建议：

- Phase B：`feat: 新增数据源 registry 与覆盖测试`
- Phase C：`test: 固化 accuracy-report 与 DB 一致性`
- Phase D：`feat: 新增数据质量结果模型`
- Phase E：`refactor: 迁移 PB 分位计算到 scorer`
- Phase F：`feat: 定义只读 agent reviewer schema`

## 5. Phase Ledger

- Phase A — 数据源治理 Spec 落地：`done`
- Phase B — 数据源 registry 与数据质量矩阵：`not_started`
- Phase C — 报告/DB 一致性测试加固：`not_started`
- Phase D — 数据质量结果模型：`not_started`
- Phase E — 代码 seam 小步拆分：`not_started`
- Phase F — Agent reviewer schema-only：`not_started`

进入任意 Phase 前，先确认前序 Phase 状态为 `done`。

---

## Phase B — 数据源 registry 与覆盖测试

类型：landing

### B0. 预检

命令：

```bash
cd /home/lin/a-stock-tracker
git status --short
pytest tests/test_spec_structure.py -q
pytest tests/ -q
```

期望：

- `git status --short` 无 tracked dirty 文件；ignored `.env/.venv/cache/tracker.db/logs` 不影响。
- `pytest tests/test_spec_structure.py -q` 通过。
- `pytest tests/ -q` 通过。

失败处理：

- 若 tracked dirty 不是本计划产生，停止。
- 若测试失败，先定位失败是否由环境缺包导致；不能直接跳过测试。

### B1. 新增 registry 文件

新增文件：`docs/data-source-registry.yaml`

格式要求：

- 使用人类可读 YAML 风格，但测试采用 stdlib 文本/正则解析，避免 PyYAML 依赖。
- 所有字段项使用固定块：
  ```yaml
  - field: roe_3y_avg
    owner: scorer_input
    requirement: required
    source_primary: akshare.stock_financial_abstract_ths
    source_fallback: none
    cache: stock_fundamentals.data.roe_3y_avg
    refresh: weekly
    affects_scoring: true
    affects_outcome: false
    failure_behavior: mark_missing; data_quality_degrades
  ```
- 每个条目必须包含这些 key：`field`、`owner`、`requirement`、`source_primary`、`source_fallback`、`cache`、`refresh`、`affects_scoring`、`affects_outcome`、`failure_behavior`。

必须覆盖字段：

scorer 输入字段：

- `roe_3y_avg`
- `net_profit_growth`
- `debt_ratio`
- `gross_margin`
- `pb_percentile_10y`
- `moat_fixed`
- `market_pos_fixed`
- `sentiment_fixed`

派生/支撑字段：

- `bps`
- `pb_hist_monthly`
- `report_period`
- `price_at_score`
- `benchmark_30d`
- `benchmark_60d`
- `benchmark_90d`
- `weights_hash`
- `framework`
- `outcome_30d`
- `outcome_60d`
- `outcome_90d`
- `gemini_qualitative_score`

建议内容要点：

- `roe_3y_avg` / `net_profit_growth` / `debt_ratio`：primary 为 AKShare 财务摘要，cache 为 `stock_fundamentals.data.*`，refresh 为 `weekly`，影响 scoring。
- `gross_margin`：primary 为新浪利润表近 3 年年报均值，fallback 为 `none`，失败时 missing。
- `pb_percentile_10y`：derived，由 `price_at_score + bps + pb_hist_monthly` 计算，失败时 missing。
- `price_at_score`：primary 为腾讯历史日线或 daily 收盘价路径，fallback 为回填路径；影响 outcome 和 daily PB 计算。
- benchmark：primary 为沪深300指数缓存/腾讯指数日线，影响 outcome/report。
- Gemini：本阶段只记录 fallback 常量/缓存行为，不允许真实 LLM 成为必需验证路径。

### B2. 新增 registry 结构测试

新增文件：`tests/test_data_source_registry.py`

测试形状：

```python
from __future__ import annotations

import re
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "docs" / "data-source-registry.yaml"
WEIGHTS_PATH = Path(__file__).resolve().parents[1] / "weights.json"

REQUIRED_KEYS = {
    "field", "owner", "requirement", "source_primary", "source_fallback",
    "cache", "refresh", "affects_scoring", "affects_outcome", "failure_behavior",
}


def _registry_text() -> str:
    return REGISTRY_PATH.read_text(encoding="utf-8")


def _fields() -> set[str]:
    return set(re.findall(r"^\s*-\s+field:\s*([a-zA-Z0-9_]+)\s*$", _registry_text(), re.M))


def _blocks() -> dict[str, str]:
    text = _registry_text()
    parts = re.split(r"(?m)^\s*-\s+field:\s*", text)
    blocks = {}
    for part in parts[1:]:
        first, _, rest = part.partition("\n")
        blocks[first.strip()] = first + "\n" + rest
    return blocks


def test_registry_file_exists() -> None:
    assert REGISTRY_PATH.exists()


def test_registry_blocks_have_required_keys() -> None:
    for field, block in _blocks().items():
        keys = {m.group(1) for m in re.finditer(r"^\s*([a-zA-Z0-9_]+):", block, re.M)}
        assert REQUIRED_KEYS <= keys, field


def test_registry_covers_framework_a_scored_fields() -> None:
    import json
    weights = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
    framework_a = weights["frameworks"]["A"]
    expected = set(framework_a["fundamental"]) | set(framework_a["valuation"])
    assert expected <= _fields()


def test_registry_covers_report_contract_fields() -> None:
    expected = {
        "framework", "weights_hash", "report_period", "price_at_score",
        "outcome_30d", "outcome_60d", "outcome_90d",
        "benchmark_30d", "benchmark_60d", "benchmark_90d",
    }
    assert expected <= _fields()


def test_registry_includes_fallback_and_failure_language() -> None:
    text = _registry_text()
    for needle in ["source_fallback:", "failure_behavior:", "fallback", "missing"]:
        assert needle in text
```

如果实际实现发现 `weights.json` 的 Framework B 仍有字段，不要求 Phase B 覆盖 B；Spec 下一阶段以 Framework A 为主。不要扩大范围。

### B3. 运行验证

命令：

```bash
pytest tests/test_data_source_registry.py -q
pytest tests/test_spec_structure.py -q
pytest tests/ -q
git diff --check
```

期望：全部通过。

### B4. Commit

命令：

```bash
git add docs/data-source-registry.yaml tests/test_data_source_registry.py
git diff --cached --check
git commit -m "feat: 新增数据源 registry 与覆盖测试"
```

Rollback：

```bash
git reset --hard HEAD~1
```

Exit Gate：`tests/test_data_source_registry.py` 能证明 registry 覆盖 Framework A scorer 字段和关键报告字段。

---

## Phase C — 报告/DB 一致性测试加固

类型：landing

### C0. 预检

命令：

```bash
git status --short
pytest tests/test_data_source_registry.py -q
pytest tests/ -q
```

期望：Phase B 已提交，工作区干净。

### C1. 新增 report contract helper

优先追加到 `tests/test_pipeline.py`，避免引入过多测试文件重复 fixture。

新增 helper：

```python
def _framework_a_closed_30d_count() -> int:
    db = cache_mod.get_db()
    count = db.execute(
        """SELECT COUNT(*) FROM predictions
           WHERE outcome_30d IS NOT NULL AND framework = 'A'"""
    ).fetchone()[0]
    db.close()
    return int(count)


def assert_report_matches_db(output: str) -> None:
    expected = _framework_a_closed_30d_count()
    assert f"Framework A {expected} 条已结案记录" in output
```

注意：helper 名称必须精确包含 `assert_report_matches_db`，满足 Spec R3 anchor。

### C2. 新增/加固测试

在 `tests/test_pipeline.py` 增加测试：

```python
def test_accuracy_report_contract_matches_framework_a_sql_anchor(tmp_db, capsys):
    score_date = (date.today() - timedelta(days=30)).isoformat()
    db = cache_mod.get_db()
    # A 框 2 条已结案
    for idx in range(2):
        db.execute(
            """INSERT INTO predictions
               (code, name, framework, score_date, price_at_score,
                quant_score, total_score, weights_hash, report_period,
                outcome_30d, benchmark_30d, created_at)
               VALUES (?, ?, 'A', ?, 10, 40, 50, 'hashA', '2024-09-30', 0.10, 0.02, ?)""",
            (f"60003{idx}", f"A{idx}", score_date, score_date + "T15:00:00"),
        )
    # B 框 3 条已结案：不得污染 A 框样本数
    for idx in range(3):
        db.execute(
            """INSERT INTO predictions
               (code, name, framework, score_date, price_at_score,
                quant_score, total_score, weights_hash, report_period,
                outcome_30d, benchmark_30d, created_at)
               VALUES (?, ?, 'B', ?, 10, 40, 50, 'hashB', '2024-09-30', 0.10, 0.02, ?)""",
            (f"00000{idx}", f"B{idx}", score_date, score_date + "T15:00:00"),
        )
    db.commit()
    db.close()

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out

    assert _framework_a_closed_30d_count() == 2
    assert_report_matches_db(out)
    assert "Framework A 2 条已结案记录" in out
```

若当前 `cmd_accuracy_report()` 文案不直接包含该字符串，但已包含等价样本不足提示，则优先小改测试 helper 解析实际输出；不要为了文案大改 renderer。

### C3. 可选只读 smoke

仅当 `tracker.db` 存在且命令不写 DB 时执行：

```bash
python3 pipeline.py accuracy-report > /tmp/a_stock_accuracy_report.out
python3 - <<'PY'
import sqlite3
from pathlib import Path
import config
out = Path('/tmp/a_stock_accuracy_report.out').read_text(encoding='utf-8')
conn = sqlite3.connect(config.DB_PATH)
count = conn.execute("SELECT COUNT(*) FROM predictions WHERE outcome_30d IS NOT NULL AND framework = 'A'").fetchone()[0]
conn.close()
needle = f"Framework A {count} 条已结案记录"
print({"expected": count, "needle_found": needle in out})
raise SystemExit(0 if needle in out else 1)
PY
```

如果 smoke 因真实环境缺 AKShare 或 DB 文件不存在失败，但单元测试通过，则记录为环境限制，不阻塞 Phase C。不得写真实 DB 来制造数据。

### C4. 验证与提交

命令：

```bash
pytest tests/test_pipeline.py -q
pytest tests/ -q
git diff --check
git add tests/test_pipeline.py
git diff --cached --check
git commit -m "test: 固化 accuracy-report 与 DB 一致性"
```

Rollback：

```bash
git reset --hard HEAD~1
```

Exit Gate：测试证明报告输出中的 Framework A 结案数等于 R3 SQL anchor。

---

## Phase D — 数据质量结果模型

类型：landing

### D0. 预检

```bash
git status --short
pytest tests/test_data_source_registry.py tests/test_pipeline.py -q
```

### D1. 新增 `data_quality.py`

新增文件：`data_quality.py`

目标：只定义模型和纯函数，不接入 daily 主流程，不写 DB。

代码形状：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FieldRequirement(StrEnum):
    REQUIRED = "required"
    DEGRADABLE = "degradable"
    DERIVED = "derived"
    FIXED = "fixed"


class FieldStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    FALLBACK = "fallback"
    STALE = "stale"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class DataQualityField:
    name: str
    requirement: FieldRequirement
    status: FieldStatus
    source: str
    reason: str = ""


@dataclass(frozen=True)
class DataQualityResult:
    code: str
    fields: tuple[DataQualityField, ...]
    missing_required: tuple[str, ...] = field(default_factory=tuple)
    fallback_fields: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_acceptable(self) -> bool:
        return not self.missing_required

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "is_acceptable": self.is_acceptable,
            "missing_required": list(self.missing_required),
            "fallback_fields": list(self.fallback_fields),
            "fields": [field.__dict__ for field in self.fields],
        }


_REQUIRED_FIELDS = ("roe_3y_avg", "net_profit_growth", "debt_ratio", "gross_margin", "pb_percentile_10y")
_SUPPORT_FIELDS = ("report_period", "price_at_score")
_DERIVED_SUPPORT = ("bps", "pb_hist_monthly")


def evaluate_data_quality(code: str, data: dict[str, Any], fallback_sources: dict[str, str] | None = None) -> DataQualityResult:
    fallback_sources = fallback_sources or {}
    fields: list[DataQualityField] = []

    def add(name: str, requirement: FieldRequirement) -> None:
        if name in fallback_sources:
            status = FieldStatus.FALLBACK
            reason = fallback_sources[name]
        elif data.get(name) is None:
            status = FieldStatus.MISSING
            reason = "missing"
        else:
            status = FieldStatus.OK
            reason = ""
        fields.append(DataQualityField(name, requirement, status, source=name, reason=reason))

    for name in _REQUIRED_FIELDS:
        add(name, FieldRequirement.REQUIRED)
    for name in _SUPPORT_FIELDS:
        add(name, FieldRequirement.DEGRADABLE)
    for name in _DERIVED_SUPPORT:
        add(name, FieldRequirement.DERIVED)

    missing_required = tuple(
        item.name for item in fields
        if item.requirement == FieldRequirement.REQUIRED and item.status == FieldStatus.MISSING
    )
    fallback_fields = tuple(item.name for item in fields if item.status == FieldStatus.FALLBACK)
    return DataQualityResult(code=code, fields=tuple(fields), missing_required=missing_required, fallback_fields=fallback_fields)
```

实现时可按测试微调，但保持：类型注解、dataclass、纯函数、无外部副作用。

### D2. 新增测试

新增文件：`tests/test_data_quality.py`

必须覆盖：

1. 完整字段 → `is_acceptable is True`
2. 缺失 `price_at_score` → 可降级字段 missing，但不进入 `missing_required`
3. 缺失 `pb_percentile_10y` → required missing，`is_acceptable is False`
4. fallback 使用状态可见：`fallback_sources={"price_at_score": "tencent_hist"}` → `fallback_fields` 包含 `price_at_score`
5. `as_dict()` 输出能被 JSON 序列化

测试形状：

```python
import json

from data_quality import evaluate_data_quality


def _complete_data() -> dict:
    return {
        "roe_3y_avg": 16.5,
        "net_profit_growth": 5.5,
        "debt_ratio": 45.0,
        "gross_margin": 56.8,
        "pb_percentile_10y": 18.0,
        "report_period": "2024-09-30",
        "price_at_score": 35.2,
        "bps": 12.0,
        "pb_hist_monthly": [1.0] * 24,
    }


def test_complete_data_quality_is_acceptable() -> None:
    result = evaluate_data_quality("600036", _complete_data())
    assert result.is_acceptable is True
    assert result.missing_required == ()


def test_missing_price_is_visible_but_degradable() -> None:
    data = _complete_data()
    data["price_at_score"] = None
    result = evaluate_data_quality("600036", data)
    assert result.is_acceptable is True
    assert "price_at_score" not in result.missing_required
    assert any(field.name == "price_at_score" and field.status == "missing" for field in result.fields)


def test_missing_pb_percentile_blocks_acceptability() -> None:
    data = _complete_data()
    data["pb_percentile_10y"] = None
    result = evaluate_data_quality("600036", data)
    assert result.is_acceptable is False
    assert result.missing_required == ("pb_percentile_10y",)


def test_fallback_source_is_visible() -> None:
    result = evaluate_data_quality("600036", _complete_data(), {"price_at_score": "tencent_hist"})
    assert result.fallback_fields == ("price_at_score",)
    assert any(field.reason == "tencent_hist" for field in result.fields)


def test_result_is_json_serializable() -> None:
    json.dumps(evaluate_data_quality("600036", _complete_data()).as_dict(), ensure_ascii=False)
```

### D3. 验证与提交

```bash
pytest tests/test_data_quality.py -q
pytest tests/ -q
git diff --check
git add data_quality.py tests/test_data_quality.py
git diff --cached --check
git commit -m "feat: 新增数据质量结果模型"
```

Rollback：

```bash
git reset --hard HEAD~1
```

Exit Gate：数据质量模型能区分 required/degradable/derived 与 missing/fallback/ok，且未接入真实 DB 或外部 API。

---

## Phase E — `_compute_daily_pb_percentile` seam 迁移到 `scorer.py`

类型：landing

### E0. 预检

```bash
git status --short
pytest tests/test_scorer.py tests/test_pipeline.py -q
```

### E1. 先写 scorer golden-master 测试

修改：`tests/test_scorer.py`

新增测试：

```python
def test_compute_daily_pb_percentile_matches_pipeline_contract() -> None:
    from scorer import compute_daily_pb_percentile

    hist = [0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0]
    data = {"bps": 10.0, "pb_hist_monthly": hist}

    assert compute_daily_pb_percentile(20.0, data) == 50.0
    assert compute_daily_pb_percentile(0, data) is None
    assert compute_daily_pb_percentile(20.0, {"bps": 0, "pb_hist_monthly": hist}) is None
    assert compute_daily_pb_percentile(20.0, {"bps": 10.0, "pb_hist_monthly": hist[:3]}) is None
```

如果现有 `tests/test_scorer.py` 已有相似 fixtures，复用现有风格。

### E2. 在 `scorer.py` 新增函数

新增函数名：`compute_daily_pb_percentile`

代码直接迁移自 `pipeline.py`，保持行为：

```python
def compute_daily_pb_percentile(price: float, data: dict[str, Any]) -> float | None:
    """用当日价格 + 缓存 BPS/PB 历史序列计算实时 PB 历史分位。纯函数，不写 DB。"""
    bps = data.get("bps")
    hist = data.get("pb_hist_monthly")
    if not bps or bps <= 0 or not hist or len(hist) < 12:
        return None
    current_pb = price / bps
    if current_pb <= 0:
        return None
    pct = sum(1 for x in hist if float(x) < current_pb) / len(hist) * 100
    return round(pct, 1)
```

### E3. 保持 pipeline 兼容边界

修改：`pipeline.py`

- import 改为：
  ```python
  from scorer import (
      SUPPORTED_FRAMEWORKS,
      InsufficientDataError,
      UnsupportedFrameworkError,
      compute_daily_pb_percentile,
      score_stock,
  )
  ```
- 把原 `_compute_daily_pb_percentile` 替换为薄 wrapper，避免旧测试或内部调用断裂：
  ```python
  def _compute_daily_pb_percentile(price: float, data: dict) -> float | None:
      return compute_daily_pb_percentile(price, data)
  ```
- 不改 daily 流程其他逻辑。

### E4. 加 pipeline 兼容测试

在 `tests/test_pipeline.py` 增加或保留断言：

```python
def test_pipeline_pb_percentile_wrapper_uses_scorer() -> None:
    hist = [1.0] * 12
    assert pipeline._compute_daily_pb_percentile(20.0, {"bps": 10.0, "pb_hist_monthly": hist}) == 100.0
```

### E5. 验证与提交

```bash
pytest tests/test_scorer.py -q
pytest tests/test_pipeline.py -q
pytest tests/ -q
git diff --check
git diff -- pipeline.py scorer.py tests/test_scorer.py tests/test_pipeline.py
git add scorer.py pipeline.py tests/test_scorer.py tests/test_pipeline.py
git diff --cached --check
git commit -m "refactor: 迁移 PB 分位计算到 scorer"
```

Rollback：

```bash
git reset --hard HEAD~1
```

Exit Gate：`scorer.py` 拥有 PB 分位纯函数，`pipeline.py` 只有兼容 wrapper；全量测试通过；diff 不含 DB schema、cron、通知变更。

---

## Phase F — Agent reviewer schema-only

类型：landing

### F0. 预检

```bash
git status --short
pytest tests/ -q
```

### F1. 新增 `agent_reviewer.py`

目标：只读 schema + fake reviewer，不调用真实 LLM，不读 env，不写 DB。

代码形状：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


FORBIDDEN_OUTPUT_KEYS = {
    "score", "total_score", "quant_score", "threshold", "thresholds", "weights",
    "weights_hash", "db_write", "trade_action", "position", "data_fetch_instruction",
}


@dataclass(frozen=True)
class ReviewInput:
    code: str
    name: str
    score_result: dict[str, Any]
    data_quality_result: dict[str, Any]
    policy_version: str
    weights_hash: str
    missing_fields: tuple[str, ...] = field(default_factory=tuple)
    report_period: str | None = None
    risk_flags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReviewOutput:
    explanation: str
    objections: tuple[str, ...] = field(default_factory=tuple)
    missing_data_comment: str = ""
    human_questions: tuple[str, ...] = field(default_factory=tuple)
    confidence_note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "explanation": self.explanation,
            "objections": list(self.objections),
            "missing_data_comment": self.missing_data_comment,
            "human_questions": list(self.human_questions),
            "confidence_note": self.confidence_note,
        }


def validate_review_output(output: dict[str, Any]) -> None:
    forbidden = FORBIDDEN_OUTPUT_KEYS & set(output)
    if forbidden:
        raise ValueError(f"reviewer output contains forbidden keys: {sorted(forbidden)}")


def fake_review(input_data: ReviewInput) -> ReviewOutput:
    missing = tuple(input_data.missing_fields)
    missing_comment = "无缺失字段" if not missing else "缺失字段：" + ", ".join(missing)
    objections = tuple(f"缺失 {field}" for field in missing)
    return ReviewOutput(
        explanation=f"{input_data.code} {input_data.name} 已完成确定性评分；reviewer 仅提供只读说明。",
        objections=objections,
        missing_data_comment=missing_comment,
        human_questions=(),
        confidence_note="fake reviewer；未调用真实 LLM；不得覆盖 deterministic score/decision。",
    )
```

### F2. 新增测试

新增文件：`tests/test_agent_reviewer.py`

测试：

```python
import pytest

from agent_reviewer import ReviewInput, fake_review, validate_review_output


def _input() -> ReviewInput:
    return ReviewInput(
        code="600036",
        name="招商银行",
        score_result={"total_score": 50.0, "decision": "buy_moderate"},
        data_quality_result={"is_acceptable": True},
        policy_version="phase-f-schema-only",
        weights_hash="abc12345",
        missing_fields=("gross_margin",),
        report_period="2024-09-30",
        risk_flags=("sample_size_low",),
    )


def test_fake_review_is_read_only_commentary() -> None:
    output = fake_review(_input()).as_dict()
    assert "explanation" in output
    assert "gross_margin" in output["missing_data_comment"]
    assert "total_score" not in output
    assert "thresholds" not in output
    assert "trade_action" not in output


def test_validate_review_output_rejects_score_override() -> None:
    with pytest.raises(ValueError):
        validate_review_output({"explanation": "x", "total_score": 99})


def test_validate_review_output_rejects_trade_or_db_instruction() -> None:
    with pytest.raises(ValueError):
        validate_review_output({"explanation": "x", "db_write": "UPDATE predictions"})
    with pytest.raises(ValueError):
        validate_review_output({"explanation": "x", "trade_action": "buy"})
```

### F3. 可选文档更新

若需要文档入口，只允许在 `README.md` 增加 3-5 行：

```markdown
### Agent reviewer boundary

`agent_reviewer.py` 当前仅提供 schema-only/fake reviewer。它只能输出解释、异议、缺失字段说明和人工问题；不得改分数、阈值、权重、DB、cron、通知或交易行为。真实 LLM reviewer 需要后续单独审批。
```

如 README 当前结构不适合，跳过文档更新，不阻塞。

### F4. 验证与提交

```bash
pytest tests/test_agent_reviewer.py -q
pytest tests/ -q
git diff --check
git add agent_reviewer.py tests/test_agent_reviewer.py README.md
git diff --cached --check
git commit -m "feat: 定义只读 agent reviewer schema"
```

如果 README 未改，`git add README.md` 会失败；实际执行时改为只 add 已变更文件：

```bash
git add agent_reviewer.py tests/test_agent_reviewer.py
```

Rollback：

```bash
git reset --hard HEAD~1
```

Exit Gate：fake reviewer 只能输出只读审查意见；测试证明 forbidden keys 会被拒绝；没有真实 LLM/API/DB/通知副作用。

---

## 6. 终局验证

全部 Phase 完成后运行：

```bash
cd /home/lin/a-stock-tracker
git status --short
pytest tests/test_spec_structure.py tests/test_data_source_registry.py tests/test_data_quality.py tests/test_agent_reviewer.py -q
pytest tests/ -q
git diff --check
python3 - <<'PY'
from pathlib import Path
required = [
    'docs/data-source-registry.yaml',
    'data_quality.py',
    'agent_reviewer.py',
    'tests/test_data_source_registry.py',
    'tests/test_data_quality.py',
    'tests/test_agent_reviewer.py',
]
missing = [p for p in required if not Path(p).exists()]
print({'missing': missing})
raise SystemExit(1 if missing else 0)
PY
```

期望：

- 所有测试通过。
- `git status --short` 只有 ignored 本地文件；tracked 工作区干净。
- 上述 required 文件全部存在。
- commit log 至少包含 Phase B-F 的 5 个 scoped commits。

查看 commit：

```bash
git log --oneline -6
```

## 7. Hermes 自动执行提示词

执行时给 Hermes/子代理使用以下提示：

```text
你在 /home/lin/a-stock-tracker 执行 docs/plans/2026-05-30-data-governance-structure-boundary-execution-plan.md。
严格按 Phase B → C → D → E → F 顺序执行。
每个 Phase 先检查 git status 和前序测试；只修改该 Phase 允许的文件；先写/补测试，再做最小实现；运行定向测试、全量 pytest、git diff --check；每个 Phase 一个 commit。
禁止改真实 tracker.db、cron、credentials、.env、Telegram、Google Sheets、真实 Gemini 调用、交易/持仓/自动调权逻辑。
若触发 Stop 条件，停止并汇报证据，不要自行扩大范围。
最终汇报每个 Phase 的 commit hash、验证命令和输出摘要。
```

## 8. 独立审查门

Phase B-F 全部完成后，运行一次只读审查，审查范围：

- 是否遵守 Phase 顺序
- 是否 registry 覆盖 scorer/report 字段
- 是否 data_quality 没有隐藏 DB/API 副作用
- 是否 PB 分位迁移保持行为一致
- 是否 reviewer 仍是 schema-only，不能覆盖 deterministic decision
- 是否没有引入交易/持仓/cron/通知/真实 LLM 副作用

审查不通过时，只 patch 审查指出的具体问题；不要借机重构邻近代码。

## 9. 回滚策略

单 Phase 回滚：

```bash
git reset --hard HEAD~1
pytest tests/ -q
```

全部回滚到 Phase A baseline：

```bash
git log --oneline --decorate -10
# 找到 Phase A baseline commit，例如 9c02b79
git reset --hard 9c02b79
pytest tests/ -q
```

不要删除 `.env`、`.venv`、`tracker.db`、logs；它们是 ignored 本地文件，不属于本计划。

```
