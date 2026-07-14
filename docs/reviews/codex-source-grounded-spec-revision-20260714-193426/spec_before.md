---
title: Source-Grounded Structured Qualitative Scoring Spec
status: draft
created: 2026-07-14
updated: 2026-07-14
owner: lin / Hermes
risk_tier: spec-first
source_article: https://www.kdnuggets.com/structured-language-model-generation-with-outlines
---

# 来源约束的结构化定性评分 Spec

## 1. 请求改写

Original request:
> 请根据以上建议帮我写spec，把文章方法落到 a-stock-tracker。

Clear prompt:
> 为 `a-stock-tracker` 编写一份项目内正式 spec，把 Outlines 文章中的“生成阶段结构约束”思想应用到 Gemini 定性评分，但不直接引入 Outlines。先解决 `gemini_scorer.py` 仅凭股票代码和名称评分、缺少可核验证据的根因；再使用 Gemini 2.5 Flash 原生 JSON Schema 固定响应结构，并保留应用端语义校验、缓存和 fallback。所有新行为必须先走 project-local fixture 与 shadow/report-only 验证，不得直接改变生产评分、历史 predictions、权重、推送或 cron。

What changed:
- 将“提升 Agent 准确性”拆成结构正确性、证据一致性、评分一致性和投资预测有效性四个不同目标。
- 明确 Outlines 不是当前首选依赖；复用其 constrained generation 思路，使用现有 Gemini REST 接口的原生结构化输出能力。
- 将生产切换拆成 shadow/report-only 与显式 cutover 两个阶段。
- 将“有合法 JSON”从成功条件降为必要条件；没有可引用证据时必须 fail closed。

## 2. 背景与问题定义

当前 `gemini_scorer.py` 调用 Gemini 2.5 Flash，为以下三个字段生成整数：

- `moat`: 1-10
- `market_pos`: 1-5
- `sentiment`: 1-5

当前 Prompt 只提供：

```text
股票：{name}（{code}）
```

但要求模型判断护城河、市场地位和近期消息面。当前链路已有 JSON 解析、值域校验、30 天缓存、429/5xx 退避重试、过期缓存降级和 all-or-nothing fallback；这些机制能控制格式和可用性，不能证明评分有事实依据。

当前主要风险：

1. **Grounding 缺失**：模型没有收到财务、竞争地位、公告或新闻证据。
2. **格式只由 Prompt 约束**：依赖“只返回 JSON”，仍需处理 Markdown 包装和解析失败。
3. **合法值不等于正确值**：`{"moat": 8, "market_pos": 5, "sentiment": 4}` 即使完全合法，也可能没有证据。
4. **缓存放大静默错误**：无依据的合法结果可能被缓存 30 天并注入 `total_score`。
5. **版本不可区分**：现有 `qualitative_scores` 只保存三个整数和日期，无法区分评分方法、输入证据或 schema 版本。

本 spec 的核心判断：

> 结构约束负责“输出可解析”，证据包和应用端验证负责“结论可追溯”；两者都不能单独证明投资预测有效。

## 3. 目标

### G1. 让模型只能基于显式输入证据评分

每个评分维度必须引用输入证据包中的稳定 `evidence_id`。模型不得使用未提供的公司事实、市场份额、新闻或时间敏感信息。

### G2. 用 Gemini 原生 JSON Schema 约束输出

在不新增第三方依赖的前提下，使用 Gemini `generateContent` REST API 的结构化输出能力，约束字段类型、必填字段、枚举和值域。

### G3. 保留应用端语义和业务校验

Schema 合法不能绕过本地验证。本地验证必须检查证据引用、状态与分数一致性、日期、新鲜度、值域和禁止额外字段。

### G4. Fail closed，不把信息不足包装成评分

证据不足时返回 `insufficient_data`，不得让模型猜分。生产适配器沿用“新鲜可信结果 → 过期可信缓存 → 固定 fallback”的顺序，不允许将部分新分数与部分 fallback 混用。

### G5. 先 shadow，再决定是否切换生产

新方法首先写入独立 evaluation 表或文件型报告，不影响现有 `qualitative_scores`、`predictions.total_score`、Telegram 或 cron。生产切换必须另有实施计划、DB 变更确认和明确批准。

### G6. 可分别评估四种准确性

必须分开报告：

1. Schema validity：输出是否符合结构。
2. Evidence validity：每个结论是否能回指提供的证据。
3. Rubric agreement：评分是否与预先定义的评分锚点一致。
4. Predictive validity：评分是否改善后续 30/60/90 日 alpha；该项只能通过自然结案验证，不能由本 spec 或一次模型运行证明。

## 4. 非目标

本 spec 不授权：

1. 直接安装或引入 `outlines`、Pydantic、Google GenAI SDK 或其他新依赖。
2. 修改 `weights.json`、`buy_strong` 等阈值或 Framework A/B 生产边界。
3. 重写历史 `predictions.total_score`、`weights_hash`、outcome 或 alpha 字段。
4. 自动交易、下单、持仓写入或资金相关操作。
5. 新建独立舆情/NLP 数据源，或默认开启 Gemini Google Search/URL Context。
6. 把股价技术信号冒充公司护城河或市场地位证据。
7. 将 JSON Schema 合法率称为投资评分准确率。
8. 未经确认修改生产 DB schema、cron、Telegram 推送或真实 Gemini 调用频率。
9. 在 shadow 阶段让新评分进入 `score_stock()` 或 `predictions`。
10. 删除现有 `_validate()`、stale cache 或固定 fallback。

## 5. 当前基线

- Project root: `/home/lin/a-stock-tracker`
- Production scorer: `gemini_scorer.py`
- Pipeline injection: `pipeline.py` 中 `get_qualitative_score()` → `moat_fixed` / `market_pos_fixed` / `sentiment_fixed`
- DB schema owner: `lib/cache.py`
- Existing cache table: `qualitative_scores(code, moat, market_pos, sentiment, scored_date)`
- Existing tests: `tests/test_gemini_scorer.py`
- Existing test contract: 所有 Gemini/AKShare/Telegram 网络调用必须 mock
- Current model: `gemini-2.5-flash`
- Current API: `v1beta/models/{model}:generateContent`
- Current fallback order: fresh cache → Gemini → stale cache → fixed `FALLBACK`

Relevant documentation:

- Source article: <https://www.kdnuggets.com/structured-language-model-generation-with-outlines>
- Gemini `generateContent` structured output: <https://ai.google.dev/gemini-api/docs/generate-content/structured-output>

Official-doc constraints recorded for this spec:

- Gemini 2.5 Flash supports structured output.
- JSON Schema support is a subset, not the full specification.
- Schema can constrain object/array/string/integer/enum/minimum/maximum 等字段。
- Structured output guarantees syntactically valid schema-shaped JSON，不保证值在业务语义上正确；应用仍必须校验。
- `generateContent` 已被官方标为上一代 API；本次为最小改动继续使用现有接口，是否迁移 Interactions API 属于独立任务。

## 6. 术语和状态

### 6.1 Evidence packet

送入模型的唯一事实来源。每条证据必须包含：

```json
{
  "evidence_id": "fundamentals.roe_3y_avg",
  "dimension": "moat",
  "value": 14.2,
  "unit": "percent",
  "source": "local_fundamentals_cache",
  "source_date": "2026-03-31",
  "freshness_status": "fresh"
}
```

### 6.2 Dimension status

每个维度只能使用：

- `scored`: 证据足够，可给分。
- `insufficient_data`: 证据不足，不给分。

不得使用 `unknown`、`probably` 等模糊状态。

### 6.3 Overall status

- `scored`: 三个维度全部通过本地验证。
- `insufficient_data`: 至少一个维度证据不足。
- `invalid`: 仅由本地代码产生，表示模型输出虽返回但违反应用端契约；模型不得自行返回该状态。

### 6.4 Confidence

模型输出只允许：`low | medium | high`。Confidence 是证据充分度说明，不是收益概率，不得进入 `total_score`。

## 7. Requirements

### 7.1 输入证据契约

- **REQ-001:** 新评分入口必须接收显式 `QualitativeContext`，不得只接收 `code` 和 `name` 后让模型自由补全事实。
- **REQ-002:** `QualitativeContext` 必须至少包含 `code`、`name`、`industry`、`as_of_date`、`schema_version` 和 `evidence[]`。
- **REQ-003:** Evidence packet 只能包含本地已获取并标注来源/日期的数据；Prompt 必须明确“不得使用输入外知识”。
- **REQ-004:** 现有基本面缓存可提供候选证据：`roe_3y_avg`、`roe_latest`、`net_profit_growth`、`debt_ratio`、`gross_margin`、`report_period`。`pb_percentile_10y` 属于估值证据，不得单独用于证明护城河或市场地位。
- **REQ-005:** `moat` 要进入 `scored`，除财务持续性证据外，必须至少有一条直接竞争优势证据，例如品牌/成本/渠道/转换成本/网络效应/监管牌照；如果当前数据源没有这类证据，必须返回 `insufficient_data`。
- **REQ-006:** `market_pos` 要进入 `scored`，必须至少有一条行业地位证据，例如市场份额、行业排名、核心业务规模相对同行或明确的细分龙头证据；公司自身财务指标不能单独证明行业地位。
- **REQ-007:** `sentiment` 要进入 `scored`，必须有带日期且在配置的新鲜度窗口内的公告/新闻/一致预期等证据。仅有财报指标、PB 分位或价格趋势时必须返回 `insufficient_data`。
- **REQ-008:** 本 spec 初始 shadow 允许因为缺少 REQ-005~007 所需证据而大量返回 `insufficient_data`；这属于揭示数据缺口，不是可用固定分数掩盖的失败。
- **REQ-009:** 输入构建器必须输出稳定排序并计算 `input_hash`；相同输入内容必须得到相同 hash，日期或证据变化必须改变 hash。
- **REQ-010:** Prompt、日志和 evaluation 记录不得包含 API key、Telegram token、环境变量值或其他凭证。

### 7.2 结构化输出契约

- **REQ-011:** 不新增 Outlines 依赖；使用现有标准库 HTTP 路径和 Gemini 原生 JSON Schema。
- **REQ-012:** Gemini 请求必须在 `generationConfig` 中声明 JSON MIME 类型和 schema；具体字段名以实施时再次读取的官方 `generateContent` 文档为准，不得依赖训练记忆。
- **REQ-013:** 顶层输出必须包含且只包含 `schema_version`、`overall_status`、`as_of_date`、`dimensions`。
- **REQ-014:** `dimensions` 必须且只包含 `moat`、`market_pos`、`sentiment`。
- **REQ-015:** 每个维度必须包含 `status`、`score`、`confidence`、`evidence_ids`、`rationale`。
- **REQ-016:** `score` 的类型为 `integer | null`；`moat` 范围 1-10，`market_pos` 和 `sentiment` 范围 1-5。
- **REQ-017:** `status=scored` 时 `score` 必须为整数且 `evidence_ids` 非空；`status=insufficient_data` 时 `score` 必须为 `null`，`evidence_ids` 可以为空，`rationale` 必须说明缺少什么证据。
- **REQ-018:** `rationale` 只允许解释输入证据与评分锚点之间的关系，不得引入输入中没有的新事实；长度必须有上限，避免把自由文本重新变成无法验证的主要载体。
- **REQ-019:** Schema 应使用 `required`、`additionalProperties=false`、`enum`、`minimum`、`maximum` 和数组数量限制等官方支持的约束；若 API 忽略某项约束，本地验证器必须补上。
- **REQ-020:** 返回 JSON 的 key 顺序、空白或换行不得成为业务逻辑；只能按解析后的对象判断。

参考输出：

```json
{
  "schema_version": "qualitative-score-v2",
  "overall_status": "insufficient_data",
  "as_of_date": "2026-07-14",
  "dimensions": {
    "moat": {
      "status": "insufficient_data",
      "score": null,
      "confidence": "low",
      "evidence_ids": ["fundamentals.roe_3y_avg"],
      "rationale": "财务持续性数据存在，但缺少直接竞争优势证据。"
    },
    "market_pos": {
      "status": "insufficient_data",
      "score": null,
      "confidence": "low",
      "evidence_ids": [],
      "rationale": "缺少市场份额、行业排名或同行对比证据。"
    },
    "sentiment": {
      "status": "insufficient_data",
      "score": null,
      "confidence": "low",
      "evidence_ids": [],
      "rationale": "缺少新鲜且带日期的公告或新闻证据。"
    }
  }
}
```

### 7.3 本地验证契约

- **REQ-021:** 新验证器必须独立于 HTTP 调用，可接受普通 Python 对象和 `QualitativeContext` 做纯函数校验。
- **REQ-022:** 本地验证必须拒绝缺字段、额外字段、错误类型、越界分数、未知枚举和错误 `schema_version`。
- **REQ-023:** 本地验证必须拒绝任何不在输入 evidence packet 中的 `evidence_id`。
- **REQ-024:** 本地验证必须拒绝 `scored + null score`、`insufficient_data + integer score`、`scored + empty evidence_ids` 等状态矛盾。
- **REQ-025:** 本地验证必须检查 `as_of_date` 与输入一致，不能接受模型自行生成其他日期。
- **REQ-026:** 本地验证必须检查维度证据类型：估值证据不能单独支持 moat/market_pos，过期公告或新闻不能支持 sentiment。
- **REQ-027:** 任一维度校验失败时，整个新结果为 `invalid`；不得只采用其他两个维度。
- **REQ-028:** 现有 `_validate()` 在生产 cutover 前不得删除；实施可将其演进为兼容旧 dict 与 v2 结果的边界适配器，但必须保留整数范围的二次检查。

### 7.4 评分锚点

- **REQ-029:** Prompt 和人工审查必须使用同一份版本化 rubric；rubric 变更必须更新 `schema_version` 或独立 `rubric_version`。
- **REQ-030:** `moat` 评分必须采用可解释锚点，而不是“凭感觉 1-10”：
  - 1-2：存在明显竞争劣势或证据不支持持续优势。
  - 3-4：优势弱、易复制或持续性证据不足。
  - 5-6：存在一项可识别优势，但持续性/强度证据一般。
  - 7-8：至少一项强且持续的竞争优势，有多条独立证据支持。
  - 9-10：多重强优势且长期持续；必须有高质量直接证据，不得仅凭高 ROE/高毛利给出。
- **REQ-031:** `market_pos` 锚点：
  - 1：边缘参与者或明确弱势。
  - 2：非头部、地位一般。
  - 3：主要参与者，但没有明确头部证据。
  - 4：细分或行业头部，有直接排名/份额证据。
  - 5：明确领导者，且有多条最新直接证据；不得只凭公司规模推断。
- **REQ-032:** `sentiment` 锚点：
  - 1：近期重大明确负面。
  - 2：偏负面。
  - 3：中性或多空抵消。
  - 4：偏正面。
  - 5：近期重大明确正面。
  - 无新鲜证据：`insufficient_data`，不是默认 3。
- **REQ-033:** Rubric 只约束模型评分一致性，不得宣称高分能预测正 alpha。

### 7.5 缓存与 fallback

- **REQ-034:** Shadow 阶段不得写现有 `qualitative_scores`，优先写独立 `qualitative_score_evaluations` 或项目内文件型 artifact。
- **REQ-035:** Shadow evaluation 必须保存 `code`、`scored_date`、`schema_version`、`rubric_version`、`model`、`input_hash`、`overall_status`、`context_json`、`result_json`、`validation_status`、`failure_reason`、`created_at`。
- **REQ-036:** Shadow evaluation 不得包含 API key 或原始 HTTP headers；`context_json` 只保存已允许的来源数据。
- **REQ-037:** 生产 cutover 后的读取优先级必须为：同版本 fresh validated result → 同版本 stale validated result → 旧版 stale cache（仅迁移期）→ 固定 `FALLBACK`。
- **REQ-038:** `insufficient_data` 或 `invalid` 结果不得写入现有生产分数缓存，也不得覆盖先前可信分数。
- **REQ-039:** 不允许 moat 使用 v2、market_pos 使用旧缓存、sentiment 使用 fixed fallback 的混合结果；保持 all-or-nothing。
- **REQ-040:** 新方法必须版本化，不能让旧版和新版定性分数在报告中无法区分。

候选 shadow 表（仅设计，不构成 DB 修改批准）：

```sql
CREATE TABLE qualitative_score_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    scored_date TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    model TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    overall_status TEXT NOT NULL,
    context_json TEXT NOT NULL,
    result_json TEXT,
    validation_status TEXT NOT NULL,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(code, scored_date, schema_version, input_hash)
);
```

### 7.6 Shadow/report-only gate

- **REQ-041:** 第一阶段只能使用 mock/fixture，不发真实 Gemini 请求，不写真实 `tracker.db`。
- **REQ-042:** Fixture 至少覆盖：完整证据、缺 moat 直接证据、缺 market_pos 证据、sentiment 过期、未知 evidence ID、schema 合法但状态矛盾、越界分数、额外字段。
- **REQ-043:** 真实 shadow run 属于付费外部调用和生产数据读取，执行前必须单独确认范围、股票样本、调用次数和输出位置。
- **REQ-044:** 初始真实 shadow 样本应按行业分层选择 6 只代表性股票；不得只选择最熟悉或最容易评分的公司。
- **REQ-045:** Shadow 输出不得注入 pipeline，不得改变 Telegram 候选，不得写 `predictions`。
- **REQ-046:** 人工复核必须在不知道旧版 Gemini 分数的情况下，先根据同一 evidence packet 给出参考状态/评分，再比较 v2，避免锚定偏差。
- **REQ-047:** Shadow pass gate：
  1. Schema validity = 100%。
  2. 未知 evidence ID 接受率 = 0%。
  3. 无依据事实数量 = 0。
  4. 所有证据不足 case 均 fail closed。
  5. 对人工判定可评分的维度，至少 90% 落在人工参考分 ±1 内；样本不足时只能标记 provisional。
- **REQ-048:** Shadow gate 通过只代表结构/证据/评分锚点可接受，不代表投资预测有效；生产切换仍需单独批准。

### 7.7 生产切换与后验评估

- **REQ-049:** 生产 cutover 必须另写 implementation plan，列出确切文件、DB 迁移、备份、回滚、测试和一次受控 smoke。
- **REQ-050:** 修改生产 DB schema 前必须备份 `tracker.db` 并验证备份可打开；不得在本 spec 阶段执行。
- **REQ-051:** Cutover 不得回写历史 predictions；只影响批准日期之后的新评分。
- **REQ-052:** Cutover 后必须记录评分方法版本，使 `accuracy-report` 能按版本和 score_date 分层，不能与旧版混算。
- **REQ-053:** 后验投资验证至少分别报告 30/60/90 日 alpha、hit rate、样本数和置信限制；在样本不足前不得宣称准确性提升。
- **REQ-054:** 若新方法降低结构错误但未改善人工 rubric agreement 或后验 alpha，应分别报告，不得用一个指标掩盖另一个指标。

## 8. 设计约束

### Always do

- 新增函数必须有参数和返回值类型注解。
- 使用 `logger = logging.getLogger(__name__)`，禁止生产 `print`。
- 数据结构优先使用 `@dataclass`，不要让跨层契约长期依赖裸字典。
- 外部 API 错误必须带上下文并按现有 retry/fallback 策略处理。
- 测试使用 `tmp_path`，不得访问真实 `tracker.db`。
- 所有网络调用必须 mock；真实 Gemini shadow 需要单独批准。
- 每次实现必须先写失败测试，再改代码。
- 实施前重新核对 Gemini 当前官方文档，因为结构化输出字段可能变化。

### Ask first

- 添加或升级依赖。
- 修改 `lib/cache.py` DDL 或真实 SQLite schema。
- 执行真实 Gemini shadow/smoke。
- 修改 production pipeline 注入、缓存优先级、cron 或 Telegram。
- 增加公告、新闻、搜索或其他外部证据源。
- 改变定性分数权重、评分阈值或历史数据。

### Never do

- 把 Schema 合法当成语义正确。
- 让模型引用未提供的 evidence ID。
- 用模型训练记忆补齐公司事实。
- 把 `insufficient_data` 静默转成中性分后称为模型结果。
- 混用新评分、旧评分和 fallback 三个维度。
- 删除或覆盖无关工作区改动。
- 提交 `.env`、API key、token 或真实私密 headers。
- 未经批准改写生产 `tracker.db`、predictions 或 cron。

## 9. 方案选择与被拒绝方案

### 9.1 选择方案

```text
Local evidence sources
  → QualitativeContext / evidence IDs / input_hash
  → Gemini 2.5 Flash native JSON Schema
  → JSON parse
  → deterministic schema + evidence + rubric validation
  → shadow evaluation artifact/table
  → human rubric review
  → separately approved production adapter
  → natural 30/60/90d outcome validation
```

### 9.2 被拒绝方案

- **直接安装 Outlines**：当前只有一个 Gemini REST 后端，原生结构化输出已覆盖核心需求；Outlines会新增依赖和适配层，不能解决 grounding。
- **只给当前 Prompt 加 JSON Schema**：会得到更稳定的无依据评分，降低可见错误但保留静默语义风险。
- **让 Gemini 自动搜索互联网并评分**：来源、时点、引用、成本和稳定性边界未定义；不属于本次最小改造。
- **把现有基本面指标直接当 moat/market_pos 证据**：盈利质量不等于竞争壁垒，规模也不等于行业领导地位。
- **sentiment 缺数据时自动给 3 分并标为模型结果**：掩盖数据缺口。固定 3 只能继续作为系统 fallback，并必须与模型评分区分。
- **直接扩展现有 qualitative_scores 后立刻切换**：无法在不影响生产的前提下比较 v1/v2，且 schema 变更需要额外批准。
- **部分字段采用新模型结果**：破坏现有 all-or-nothing 安全边界，使评分来源难以解释。

## 10. Acceptance criteria

- **AC-001 maps to REQ-001~010:** Spec 明确每个维度的最低证据门槛，并规定缺证据返回 `insufficient_data`。
- **AC-002 maps to REQ-011~020:** Spec 定义 Gemini 原生 JSON Schema 输出形状、范围、状态和禁止额外字段。
- **AC-003 maps to REQ-021~028:** Spec 定义应用端独立验证，包含 evidence ID、状态一致性、日期和维度证据类型检查。
- **AC-004 maps to REQ-029~033:** Spec 给出三个维度的版本化评分锚点，并明确不等于预测有效性。
- **AC-005 maps to REQ-034~040:** Spec 定义 shadow 隔离、版本、缓存和 all-or-nothing fallback。
- **AC-006 maps to REQ-041~048:** Spec 定义 fixture、真实 shadow 审批边界和量化 pass gate。
- **AC-007 maps to REQ-049~054:** Spec 定义 cutover、DB 备份、历史不可改写和 30/60/90 日后验验证。
- **AC-008:** 本次文档变更不修改 `gemini_scorer.py`、`pipeline.py`、`lib/cache.py`、`weights.json`、测试、cron 或真实 DB。
- **AC-009:** `git diff --check` 通过；spec 中 REQ 编号唯一，所有 AC 均能映射到 requirements。
- **AC-010:** 人工评审明确接受或修改以下判断点：证据门槛、shadow 样本、±1 rubric gate、生产 cutover 前置条件。

## 11. Milestones and plan handoff

### MILESTONE-001: Spec review

Maps to: AC-001~010

Outcome:
- 用户与工程 reviewer 审查本 spec。
- 决定 evidence packet 第一版允许哪些现有字段、哪些维度必然 insufficient。

Non-scope:
- 不改代码、DB、测试或生产行为。

Verification signal:
- Spec 状态从 `draft` 改为 `approved`，或 reviewer 只剩非阻塞建议。

### MILESTONE-002: Fixture-first contract implementation

Maps to: REQ-001~033, AC-001~004

Outcome:
- 实现 dataclass、schema builder、Prompt builder 和纯本地 validator。
- 新增 mock/fixture 测试；不调用真实 Gemini。

Non-scope:
- 不写真实 DB，不接 pipeline，不影响生产评分。

Verification signal:
- 原有测试全通过；新增结构、证据、状态负例测试全通过。

### MILESTONE-003: Shadow persistence

Maps to: REQ-034~045, AC-005~006

Outcome:
- 在明确批准 DB schema 后，增加隔离 evaluation 表；或先用项目内文件 artifact 替代。
- 生产读路径保持不变。

Verification signal:
- `tmp_path` migration/CRUD 测试通过；真实 `tracker.db` 未改动。

### MILESTONE-004: Bounded real shadow review

Maps to: REQ-043~048

Outcome:
- 经批准后对 6 只分行业代表股票运行固定次数 shadow。
- 生成结构、证据、rubric agreement 报告。

Non-scope:
- 不进入 `score_stock()`，不触发 Telegram。

Verification signal:
- Pass gate 有实际证据；未通过则停止并修订 spec/rubric/evidence source。

### MILESTONE-005: Separately approved production cutover

Maps to: REQ-049~054

Outcome:
- 通过单独 implementation plan 和确认后，才允许新评分进入 production cache/pipeline。

Verification signal:
- 备份、migration、全量测试、受控 smoke 和回滚演练均通过。

## 12. Validation contract

### 12.1 本次 spec 验证

```bash
cd /home/lin/a-stock-tracker
git diff --check
git diff -- docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md docs/project-status.md
```

应证明：

1. 仅新增 spec，并在 PM ledger 中登记；无生产代码、DB、测试或配置变更。
2. REQ 与 AC 编号完整、唯一且可追踪。
3. 明确“Outlines 思想、Gemini 原生 Schema、不新增依赖”的架构决策。
4. 明确“缺证据 fail closed”和 shadow-first 边界。

### 12.2 未来实现最低验证

实施计划至少应包含：

```bash
.venv/bin/python -m pytest tests/test_gemini_scorer.py -q
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check gemini_scorer.py lib/cache.py tests/test_gemini_scorer.py
.venv/bin/python -m ruff format --check gemini_scorer.py lib/cache.py tests/test_gemini_scorer.py
git diff --check
```

若项目当时已有类型检查基线，再加入项目实际使用的 type checker；不得为满足本 spec 临时引入新工具。

### 12.3 必须报告的证据

- Changed files
- 测试、lint、format 命令与退出码
- Mock fixture 的正反例结果
- Shadow 调用次数、模型、schema/rubric version
- Schema validity、evidence validity、rubric agreement
- 未满足条件、数据缺口和 deferred 项
- 生产 DB、cron、Telegram、weights、历史 predictions 是否保持未触碰

## 13. Rollback and stop conditions

### Rollback

Spec-only 阶段：

```bash
git restore -- docs/project-status.md
rm docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md
```

未来代码阶段必须在 implementation plan 中给出精确 `git restore`/commit revert 和 DB 备份恢复命令；不得复用未验证的通用命令。

### Stop if

- 官方 API 当前字段与 spec 假设不一致。
- Gemini 2.5 Flash 不再支持目标 structured output 能力。
- 需要新增依赖但尚未获批。
- Evidence packet 无法满足 moat/market_pos/sentiment 的最低证据门槛。
- 模型生成未知 evidence ID 或输入外事实，且修复后仍复现。
- Shadow gate 未通过。
- 真实 DB 发生未批准写入。
- 发现会改写历史 predictions、weights_hash 或 outcome。
- 全量测试出现不能归因于本范围的失败。
- 工作区无关改动会被覆盖、格式化或误提交。

## 14. 风险与开放问题

### 已决策

1. 当前不引入 Outlines。
2. 当前不迁移 Gemini Interactions API。
3. 使用原生 JSON Schema + 本地语义校验双层边界。
4. 数据不足必须显式暴露，不允许模型猜分。
5. 新方法先 shadow，不直接进入生产。

### 待用户/Reviewer确认

1. **直接竞争优势证据来源**：现有 cache 不足；后续是扩展现有 fetcher、接入人工维护证据，还是保留 moat unavailable？
2. **行业地位证据来源**：是否允许人工/项目内静态 evidence packet，还是建设可审计 provider？
3. **sentiment 数据边界**：项目路线图当前明确不建设独立舆情层；是否继续让 sentiment 使用系统 fallback，而不让模型评分？
4. **Shadow 存储**：先用 JSONL/Markdown artifact，还是经确认新增独立 SQLite evaluation 表？
5. **真实 shadow 成本**：6 只股票 × 单次调用是否足够，还是需要第二轮反向边界样本？
6. **生产适用范围**：若只有 moat/market_pos 有足够证据，而 sentiment 长期 unavailable，是否继续坚持 all-or-nothing，还是将 sentiment 从 LLM 评分职责中移除？后者属于评分架构变更，需要单独 spec 决策。

## 15. Approval state

- Spec approval: pending
- Implementation approval: pending
- Production DB schema approval: pending
- Real Gemini shadow approval: pending
- Production cutover approval: pending

本 spec 是开发契约草案，不构成任何生产、数据库、付费调用、cron、Telegram 或权重修改授权。
