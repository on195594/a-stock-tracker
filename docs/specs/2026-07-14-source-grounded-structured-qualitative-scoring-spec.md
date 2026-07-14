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
- 当前官方 `generateContent` structured-output 文档明确允许在 type 数组中包含 `"null"`，例如 `{"type": ["integer", "null"]}`；因此本 spec 保留稳定对象形状，`score` 始终存在，证据不足时为 `null`。
- Schema can constrain object/array/string/integer/enum/minimum/maximum 等字段。
- Structured output guarantees syntactically valid schema-shaped JSON，不保证值在业务语义上正确；应用仍必须校验。
- 上述能力是 2026-07-14 的实施假设，不是永久 API 保证。Fixture-first 实施前必须重新读取[官方 structured-output 文档](https://ai.google.dev/gemini-api/docs/generate-content/structured-output)；mock tests 通过后，还必须经单独批准执行一次受控 smoke，再开始 real shadow。若字段或 nullable 支持变化则停止并修订计划。
- 本 spec 继续使用现有 `generateContent` 接口，不迁移 Interactions API；接口迁移属于独立任务。

## 6. 术语和状态

### 6.1 Evidence packet

送入模型的唯一事实来源。Fixture 与未来真实 evidence packet 使用同一形状。每条证据至少包含：

```json
{
  "evidence_id": "fundamentals.roe_3y_avg",
  "evidence_type": "financial_metric",
  "allowed_dimensions": ["moat"],
  "directness": "supporting",
  "freshness_policy": "max_age_550d",
  "value": 14.2,
  "unit": "percent",
  "source": "local_fundamentals_cache",
  "source_date": "2026-03-31",
  "freshness_status": "fresh"
}
```

`evidence_id` 使用可读命名空间，但命名空间不是主要业务判断依据：

- `fundamentals.*` → `financial_metric`
- `valuation.*` → `valuation_metric`
- `competitive_advantage.*` → `competitive_advantage`
- `industry.*` → `industry_position`
- `announcement.*` → `company_announcement`
- `news.*` → `news_report`
- `consensus.*` → `analyst_consensus`

机器可检查词表：

- `evidence_type`: `financial_metric | valuation_metric | competitive_advantage | industry_position | company_announcement | news_report | analyst_consensus`
- `allowed_dimensions`: 只能是 `moat | market_pos | sentiment` 的去重、稳定排序子集；允许空数组表示只能作为上下文、不能支撑任何维度。
- `directness`: `direct | supporting | context`。`direct` 直接测量或明确陈述待评分事实；`supporting` 只增强已有直接证据；`context` 不得单独或联合触发 `scored`。
- `freshness_policy`: `max_age_30d | max_age_90d | max_age_365d | max_age_550d`。
- `freshness_status`: `fresh | stale`，由本地验证器计算并与输入声明比对，不能由来源任意决定。
- `value` 只能是 JSON string/number/boolean scalar；`unit` 是非空 string 或显式 `null`，`source` 是非空 bounded string，`source_date` 是 ISO `YYYY-MM-DD`。

第一版 taxonomy registry 的规范映射如下；实现可用常量或版本化 fixture 表达，但不得让输入自行放宽：

| evidence_type | 命名空间 | 规范 allowed_dimensions | 允许 directness | freshness_policy |
|---|---|---|---|---|
| `financial_metric` | `fundamentals.*` | `[moat]` | `supporting`, `context` | `max_age_550d` |
| `valuation_metric` | `valuation.*` | `[]` | `context` | `max_age_30d` |
| `competitive_advantage` | `competitive_advantage.*` | `[moat]` | `direct`, `supporting` | `max_age_365d` |
| `industry_position` | `industry.*` | `[market_pos]` | `direct`, `supporting` | `max_age_365d` |
| `company_announcement` | `announcement.*` | `[sentiment]` | `direct`, `supporting` | `max_age_30d` |
| `news_report` | `news.*` | `[sentiment]` | `direct`, `supporting` | `max_age_30d` |
| `analyst_consensus` | `consensus.*` | `[sentiment]` | `direct`, `supporting` | `max_age_90d` |

验证器主要依据 `evidence_type`、`allowed_dimensions`、`directness` 和 `freshness_policy`；命名空间只做 registry 一致性校验和可读性检查，不得用字符串前缀替代 taxonomy。日期按 `as_of_date - source_date` 的自然日差确定：未来日期拒绝，`age <= max_age_days`（含边界日）为 `fresh`，超过为 `stale`。类型、维度、directness、policy 或 registry 组合未知/矛盾时拒绝整个输入。

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

- **REQ-001:** 新评分入口必须接收显式 `QualitativeContext`；它必须至少包含 `code`、`name`、`industry`、`as_of_date`、`schema_version`、`rubric_version`、`taxonomy_version` 和 `evidence[]`。Evidence packet 是唯一事实来源，Prompt 必须禁止使用输入外知识。
- **REQ-002:** 输入构建器必须规范化证据顺序和 `allowed_dimensions` 顺序并计算 `input_hash`；相同版本和内容必须得到相同 hash，日期、版本或证据变化必须改变 hash。
- **REQ-003:** 每条 evidence 必须包含 6.1 定义的 `evidence_id`、`evidence_type`、`allowed_dimensions`、`directness`、`freshness_policy`、value/unit/source/source_date/freshness_status；缺失不适用的 unit 时必须显式使用 `null`，不得省略稳定字段。
- **REQ-004:** `evidence_id` 必须唯一并符合 6.1 命名空间；业务授权主要由结构化 taxonomy 字段决定，字符串前缀只验证 registry 一致性。
- **REQ-005:** Validator 必须拒绝重复 evidence ID，未知 type/dimension/directness/policy，未来日期，声明 freshness 与计算结果不一致，以及 type、namespace、allowed dimensions、directness、policy 之间的矛盾组合。
- **REQ-006:** 现有基本面缓存可提供候选 `financial_metric`：`roe_3y_avg`、`roe_latest`、`net_profit_growth`、`debt_ratio`、`gross_margin`、`report_period`。`pb_percentile_10y` 是 `valuation_metric`，只能作 context，不能证明 moat 或 market_pos。
- **REQ-007:** `moat=scored` 的机械最低门槛是模型该维度输出 `evidence_ids` 中**实际引用**至少一条 fresh、`direct`、允许 `moat` 的 `competitive_advantage`，并**实际引用**至少一条 fresh `financial_metric` supporting evidence；缺任一类必须为 `insufficient_data`。证据包中存在合格证据但模型未在 `evidence_ids` 中引用，不满足本门槛。
- **REQ-008:** `market_pos=scored` 的机械最低门槛是模型该维度输出 `evidence_ids` 中**实际引用**至少一条 fresh、`direct`、允许 `market_pos` 的 `industry_position`；公司自身财务或估值不能单独证明行业地位。证据包中存在合格证据但模型未引用，不满足本门槛。
- **REQ-009:** `sentiment=scored` 的机械最低门槛是模型该维度输出 `evidence_ids` 中**实际引用**至少一条 fresh、`direct`、允许 `sentiment` 的 `company_announcement`、`news_report` 或 `analyst_consensus`；财报、估值或价格趋势不能替代。证据包中存在合格证据但模型未引用，不满足本门槛。
- **REQ-010:** 机械门槛只判断合同资格，不宣称证据真实、相关、独立或足以支撑某个分数；这些语义质量与评分充分性仍由版本化 rubric 和盲审人工判断。
- **REQ-011:** 缺少 REQ-007~009 的证据必须显式产生 `insufficient_data`；不得用固定分数伪装成模型结果，也不得降低门槛以提高通过率。
- **REQ-012:** 所有输入只允许来自本地 fixture、已批准静态数据集或已批准 provider，并保留可审计的来源和日期；未经批准不得让模型自行搜索或补齐事实。

### 7.2 结构化输出契约

- **REQ-013:** 不新增 Outlines 依赖；使用现有标准库 HTTP 路径和 Gemini 2.5 Flash 原生 JSON Schema，不在本 spec 迁移 Interactions API。
- **REQ-014:** Gemini 请求必须在 `generationConfig` 中声明 JSON MIME 类型和 schema；字段名和 nullable 能力必须在实施时依据[官方文档](https://ai.google.dev/gemini-api/docs/generate-content/structured-output)重查并在批准的真实 shadow 前 smoke，不得把当前能力当永久保证。
- **REQ-015:** 顶层输出必须包含且只包含 `schema_version`、`overall_status`、`as_of_date`、`dimensions`。
- **REQ-016:** `dimensions` 必须且只包含 `moat`、`market_pos`、`sentiment`。
- **REQ-017:** 每个维度必须包含且只包含 `status`、`score`、`confidence`、`evidence_ids`、`rationale`；`score` 始终存在以保持稳定对象形状。
- **REQ-018:** `score` 的 JSON Schema 类型必须为 `{"type": ["integer", "null"]}`；`moat` 的整数范围为 1-10，`market_pos` 和 `sentiment` 为 1-5，本地 validator 必须再次执行范围检查。
- **REQ-019:** `status=scored` 时 `score` 必须为整数且 `evidence_ids` 非空；`status=insufficient_data` 时 `score` 必须为 `null`，`evidence_ids` 可为空，`rationale` 必须说明缺少的证据。
- **REQ-020:** `rationale` 只允许解释输入证据与评分锚点之间的关系，不得引入输入外事实，并设置固定字符上限。
- **REQ-021:** Schema 应使用官方当前支持的 `required`、`additionalProperties=false`、`enum`、`minimum`、`maximum` 和数组数量限制；API 未执行的约束由本地 validator 补齐。
- **REQ-022:** JSON key 顺序、空白或换行不得成为业务逻辑；只能按解析后的对象判断。

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

- **REQ-023:** 新 validator 必须独立于 HTTP，可接受普通 Python 对象和 `QualitativeContext` 做纯函数校验。
- **REQ-024:** Validator 必须拒绝缺失/额外字段、错误类型、越界分数、未知枚举和输出 `schema_version` 不匹配；evaluation/cache boundary 必须另行拒绝 envelope 中 `rubric_version`、`taxonomy_version` 或 `input_hash` 不匹配。
- **REQ-025:** Validator 必须拒绝未知或重复引用的 `evidence_id`，并拒绝任何引用证据不允许目标维度、不是所需 directness 或已 stale 的结果。
- **REQ-026:** Validator 必须拒绝 `scored + null`、`insufficient_data + integer`、`scored + empty evidence_ids`、维度与 overall status 不一致等语义矛盾。
- **REQ-027:** Validator 必须检查 `as_of_date` 与输入一致，并按 6.1 registry 重算 taxonomy 与 freshness；不能接受模型或 packet 自行放宽 policy。
- **REQ-028:** Schema-valid 只表示结构通过。确定性 validator 必须拒绝未满足 REQ-007~009 最低门槛的结果——门槛检查对象是**模型该维度输出 `evidence_ids` 实际引用的证据集合**，不是输入证据包中是否存在合格证据；即使证据包内存在合格证据，若模型未将其列入该维度的 `evidence_ids`，也不得判定为 `scored`（详见 REQ-007~009）。rationale 是否引入输入外事实、证据是否真实相关等主观语义由同 rubric 的盲审或显式注入的 review outcome 判断，并分类为 evidence/semantic invalid。两类失败都不得变成可信分数。
- **REQ-029:** 任一维度 validation 失败时整个新结果为 `invalid`，不得只采用另两个维度；合法的 `insufficient_data` 则使 overall status 为 `insufficient_data`。
- **REQ-030:** 现有 v1 `_validate()` 与生产路径在 cutover 批准前不得删除或改变；未来兼容适配器必须保留整数范围二次检查。

### 7.4 评分锚点

- **REQ-031:** Prompt 和人工审查必须使用同一份版本化 rubric；rubric 变更必须更新 `rubric_version`，不允许同版本静默变更。
- **REQ-032:** `moat` 评分必须采用可解释锚点，而不是“凭感觉 1-10”：
  - 1-2：存在明显竞争劣势或证据不支持持续优势。
  - 3-4：优势弱、易复制或持续性证据不足。
  - 5-6：存在一项可识别优势，但持续性/强度证据一般。
  - 7-8：至少一项强且持续的竞争优势，有多条独立证据支持。
  - 9-10：多重强优势且长期持续；必须有高质量直接证据，不得仅凭高 ROE/高毛利给出。
- **REQ-033:** `market_pos` 锚点：
  - 1：边缘参与者或明确弱势。
  - 2：非头部、地位一般。
  - 3：主要参与者，但没有明确头部证据。
  - 4：细分或行业头部，有直接排名/份额证据。
  - 5：明确领导者，且有多条最新直接证据；不得只凭公司规模推断。
- **REQ-034:** `sentiment` 锚点：
  - 1：近期重大明确负面。
  - 2：偏负面。
  - 3：中性或多空抵消。
  - 4：偏正面。
  - 5：近期重大明确正面。
  - 无新鲜证据：`insufficient_data`，不是默认 3。
- **REQ-035:** Schema/evidence validity 与 rubric agreement 必须和 predictive validity 分开报告；前者改善不证明正 alpha 或投资准确性提升。

### 7.5 缓存与 fallback

- **REQ-036:** Fixture 和 shadow 必须与生产物理隔离：不得写现有 `qualitative_scores`、`predictions` 或真实 `tracker.db`，不得注入 pipeline、影响 predictions/Telegram/cron；只可写经当前阶段批准的独立文件 artifact，未来独立 evaluation 表仍需 DB 批准。
- **REQ-037:** Shadow evaluation 必须保存 `code`、`scored_date`、`schema_version`、`rubric_version`、`taxonomy_version`、`input_hash`、`model`、`overall_status`、`context_json`、`result_json`、`validation_status`、`failure_reason`、`created_at`。
- **REQ-038:** `validation_status` 使用有界枚举：`VALID_SCORED | VALID_INSUFFICIENT_DATA | API_TIMEOUT | API_AUTH_ERROR | API_RATE_LIMIT | API_SERVER_ERROR | MALFORMED_RESPONSE | SCHEMA_INVALID | EVIDENCE_INVALID | SEMANTIC_INVALID`。401/403 映射 auth，429 映射 rate limit，5xx 映射 server error。
- **REQ-039:** `failure_reason` 只能保存有界错误 code 和最多 256 字符的脱敏摘要；Prompt、日志、context/result、artifact/table 均不得保存 API key、token、环境变量值、原始 headers、stack trace 或其他凭证/秘密。
- **REQ-040:** 当前生产表 `qualitative_scores(code, moat, market_pos, sentiment, scored_date)` 缺少版本和 input hash，因而阻塞 production cutover；它不阻塞 fixture-first 或物理隔离的文件 artifact shadow。Fixture-first 不得选择或实施生产 migration。
- **REQ-041:** 后续获批 cutover plan 必须二选一：优先候选是新建版本化生产表；`ALTER` legacy 表是需说明风险与理由的替代方案。任何方案都不得让 v1/v2 分数不可区分，并必须定义 migration、indexes、cache priority、backward compatibility、backup、readback、rollback 和版本分层报告。
- **REQ-042:** Cutover 后缓存读取优先级必须为：schema/rubric/taxonomy/input-hash 均匹配的 fresh validated v2 → 同版本 stale validated v2 → legacy stale cache（仅迁移期）→ 固定 `FALLBACK`。
- **REQ-043:** `insufficient_data`、`invalid` 或 API failure 不得覆盖 trusted cache；不得混用 v2、v1 和 fallback 的不同维度，保持 all-or-nothing。
- **REQ-044:** 外部调用可对 timeout、429、5xx 做有上限、带抖动的 retry；401/403、malformed response、schema、evidence 或 semantic error 不重试。具体次数/退避值留给 implementation plan，但测试必须证明有界。

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
    taxonomy_version TEXT NOT NULL,
    overall_status TEXT,
    context_json TEXT NOT NULL,
    result_json TEXT,
    validation_status TEXT NOT NULL,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(code, scored_date, schema_version, rubric_version, taxonomy_version, input_hash)
);
```

### 7.6 Shadow/report-only gate

- **REQ-045:** Fixture-first 只能使用 synthetic 或人工整理的本地 evidence packet，可同时包含完整 positive 与各维度 `insufficient_data` cases；fixture 必须确定性、免凭证、无网络，并携带与未来真实 packet 完全相同的 taxonomy。
- **REQ-046:** Fixture 结果只证明 parser/schema/validator/rubric 路径行为，不证明真实数据可得、事实正确或投资准确。
- **REQ-047:** Real shadow 必须等待单独批准的 evidence source/provider，或带 provenance 的已批准静态 evidence dataset。真实证据缺失是 real-shadow blocker，不是 fixture-first blocker；不得为通过 real shadow 降低 moat/market_pos/sentiment 门槛。
- **REQ-048:** 真实 shadow 属于付费外部调用和生产数据读取，执行前必须单独确认 provider/dataset、范围、股票样本、调用次数、凭证处理和输出位置；初始样本候选为按行业分层的 6 只代表股票。
- **REQ-049:** v2 独立入口是优先的未来隔离 seam；确切文件、函数和 patch 位置由 implementation plan 基于当时代码决定，本 spec 不预先授权改生产入口。
- **REQ-050:** 人工复核必须先在不知道 v1 分数和模型结果的情况下，基于同一 evidence packet 给出参考状态/评分，再比较 v2。
- **REQ-051:** Shadow pass gate：
  1. Schema validity = 100%。
  2. 未知 evidence ID 接受率 = 0%。
  3. 无依据事实数量 = 0。
  4. 所有证据不足 case 均 fail closed。
  5. 对人工判定可评分的维度，至少 90% 落在人工参考分 ±1 内；样本不足时只能标记 provisional。
- **REQ-052:** `provisional` 只能在后续获批 evaluation plan 预先定义并达到每维度最小可评分样本数、人工复核比例和行业覆盖，且扩展样本继续满足 REQ-051 后升级；具体样本门槛是 real-shadow plan 的待决策项，不能由运行后补定。通过 gate 仍不代表预测有效或自动批准 cutover。

### 7.7 生产切换与后验评估

- **REQ-053:** Production cutover 必须另写并批准 implementation plan，列出确切代码 seam、REQ-041 的 DB 策略、migration/indexes、测试、受控 smoke、部署和回滚；本 spec 不批准任何实现或 DB 变更。
- **REQ-054:** 修改生产 DB 前必须备份 `tracker.db`，验证备份可打开和关键表可读，并在 migration 后执行 readback；rollback 必须覆盖代码和物理 DB 恢复。
- **REQ-055:** Cutover 不得回写历史 predictions，只影响批准日期之后的新评分；v1 生产路径在兼容期必须继续按当前合同工作。
- **REQ-056:** Cutover 后必须记录评分方法版本，使 reporting/`accuracy-report` 按 schema/rubric/taxonomy 版本和 score_date 分层，禁止 v1/v2 混算。
- **REQ-057:** 后验投资验证至少分别报告 30/60/90 日 alpha、hit rate、样本数和置信限制；在自然结案样本不足前不得宣称准确性提升。
- **REQ-058:** Implementation、生产 DB、真实 Gemini shadow 和 production cutover 均保持 pending，必须分别批准；任何前一阶段通过都不隐含后一阶段授权。

## 8. Test matrix

以下是未来实现合同，不授权本次新增测试或运行真实 API。Fixture-first cases 使用纯本地对象、mock HTTP 和临时路径；later shadow 与 production-cutover cases 不阻塞第一阶段完成。

| Stage | Case | Expected assertion |
|---|---|---|
| Fixture-first | 完整可评分结果 | 三维均 scored，整数范围、evidence 引用和 overall status 全部通过 |
| Fixture-first | moat / market_pos / sentiment 各自缺证据 | 每个分支分别返回 `insufficient_data`，全局 fail closed |
| Fixture-first | nullable score | scored+integer 与 insufficient+null 通过；反向组合拒绝；`score` 不得缺失 |
| Fixture-first | 未知/重复 evidence ID | 输入重复、输出重复引用、输出未知引用均拒绝 |
| Fixture-first | 未知 taxonomy | 未知 evidence type、dimension、directness、freshness policy 各自拒绝 |
| Fixture-first | allowed-dimension mismatch | evidence 不允许目标维度或 registry 组合矛盾时拒绝 |
| Fixture-first | stale 与边界日期 | age 等于窗口上限为 fresh；多一天为 stale；未来日期拒绝；声明状态不一致拒绝 |
| Fixture-first | schema-valid 但语义无效 | 状态/overall 矛盾、最低门槛不足由 validator 拒绝；注入盲审判定的 unsupported rationale 时分类 semantic invalid |
| Fixture-first | 证据包含合格证据但模型未引用 | fixture 输入包含满足 REQ-007~009 门槛的合格证据，但模型输出该维度 `evidence_ids` 未包含它（只引用了不合格或不足的证据）时，仍必须拒绝为 `scored`，不得因证据包本身合格而放行（验证 REQ-007~009/028 检查的是引用集合而非包可用性）|
| Fixture-first | shape/range failures | extra field、missing field、错误类型及各维度上下界外值拒绝 |
| Fixture-first | malformed response | 分类为 `MALFORMED_RESPONSE`，不产出 trusted result |
| Fixture-first | mocked API timeout / 401 / 403 / 429 / 5xx | 分别映射 timeout/auth/rate-limit/server bounded status，reason 脱敏 |
| Fixture-first | retry policy | auth/schema/evidence/semantic 不 retry；429/5xx 有界 retry；达到上限后 fallback |
| Fixture-first | all-or-nothing | 任一维度 invalid/insufficient 时不拼接 v2、v1、fallback 维度 |
| Fixture-first | 版本 mismatch | schema/rubric/taxonomy/input-hash 任一不符均不视为同版本 trusted result |
| Fixture-first | 不覆盖 trusted cache | 用 mock repository 证明 invalid/insufficient/API failure 不触发可信缓存覆盖 |
| Later shadow | provenance 与真实证据可用性 | 仅批准 provider/dataset 可运行；缺 moat/market_pos/sentiment 真实证据明确阻塞 |
| Later shadow | shadow 物理隔离 | artifact/evaluation 不写生产表、pipeline、predictions、Telegram、cron |
| Production cutover | fresh same-version cache | schema/rubric/taxonomy/input-hash 全匹配且 fresh 时优先读取 |
| Production cutover | stale same-version cache | 新调用失败后可用同版本 stale validated cache |
| Production cutover | legacy migration fallback | 仅兼容窗口按明确优先级读取 legacy cache，且来源/版本可区分 |
| Production cutover | v1 backward compatibility | 当前 v1 production path 在切换前及兼容窗口行为不变 |
| Production cutover | migration/readback/rollback/reporting | indexes、备份可读、迁移 readback、物理 rollback 和版本分层报告均验证 |

## 9. 设计约束

### Always do

- 新增函数必须有参数和返回值类型注解。
- 使用 `logger = logging.getLogger(__name__)`，禁止生产 `print`。
- 数据结构优先使用 `@dataclass`，不要让跨层契约长期依赖裸字典。
- 外部 API 错误必须按 REQ-038~044 分类、脱敏并遵守 retry/fallback 边界。
- 测试使用 `tmp_path`，不得访问真实 `tracker.db`。
- 所有网络调用必须 mock；真实 Gemini shadow 需要单独批准。
- 每次实现必须先写失败测试，再改代码。
- 实施前重新核对 Gemini 当前官方文档及 nullable schema，因为结构化输出字段可能变化。

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

## 10. 方案选择与被拒绝方案

### 10.1 选择方案

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

### 10.2 被拒绝方案

- **直接安装 Outlines**：当前只有一个 Gemini REST 后端，原生结构化输出已覆盖核心需求；Outlines会新增依赖和适配层，不能解决 grounding。
- **只给当前 Prompt 加 JSON Schema**：会得到更稳定的无依据评分，降低可见错误但保留静默语义风险。
- **让 Gemini 自动搜索互联网并评分**：来源、时点、引用、成本和稳定性边界未定义；不属于本次最小改造。
- **把现有基本面指标直接当 moat/market_pos 证据**：盈利质量不等于竞争壁垒，规模也不等于行业领导地位。
- **sentiment 缺数据时自动给 3 分并标为模型结果**：掩盖数据缺口。固定 3 只能继续作为系统 fallback，并必须与模型评分区分。
- **直接扩展现有 qualitative_scores 后立刻切换**：无法在不影响生产的前提下比较 v1/v2，且 schema 变更需要额外批准。
- **部分字段采用新模型结果**：破坏现有 all-or-nothing 安全边界，使评分来源难以解释。
- **为跑通 real shadow 放宽 evidence taxonomy**：把数据缺口转化为无依据分数；缺少批准的真实证据应阻塞 real shadow，而非削弱合同。
- **fixture-first 决定生产表 migration**：本地合同测试不需要生产 schema；版本表或 legacy ALTER 必须在后续 cutover plan 中选择。

## 11. Acceptance criteria

- **AC-001 maps to REQ-001~012:** Evidence packet 有显式、版本化、机器可检查 taxonomy，未知/矛盾/过期证据 fail closed，主验证不依赖前缀推断。
- **AC-002 maps to REQ-013~022:** Gemini 原生 JSON Schema 保持必填 `integer | null` score，并把当前 nullable 支持记录为实施时重查/smoke 的官方文档假设。
- **AC-003 maps to REQ-023~030:** 独立 validator 覆盖 shape、版本、evidence、freshness、状态语义和 all-or-nothing invalidation。
- **AC-004 maps to REQ-031~035:** 三维使用版本化 rubric，结构/evidence/rubric 指标与 predictive validity 分离。
- **AC-005 maps to REQ-036~044:** Shadow 物理隔离、脱敏 bounded failure 分类、retry、cache 版本和 cutover-only migration blocker 均明确。
- **AC-006 maps to REQ-045~052:** Fixture 与 real shadow 分阶段，真实证据缺失只阻塞 real shadow，gate/provisional 升级不弱化证据门槛。
- **AC-007 maps to REQ-053~058:** Cutover plan 必须覆盖版本化 DB 策略、备份/readback/rollback、v1 兼容和分层后验报告，且所有批准仍 pending。
- **AC-008:** 第 8 节 test matrix 覆盖要求的成功、失败、retry、cache、版本、兼容与隔离 cases，并标记 Fixture-first/Later shadow/Production cutover。
- **AC-009:** 本次只修改本 Spec，不修改代码、DB、测试、依赖、weights、历史记录、cron 或 Telegram。
- **AC-010:** REQ 与 AC 编号唯一连续、range/milestone mapping 一致，无 trailing whitespace，`git diff --check` 通过。
- **AC-011:** 用户/Reviewer 明确决定第 15 节开放项；未决定不阻塞 fixture-first，但阻塞对应 real-shadow 或 cutover 阶段。

## 12. Milestones and plan handoff

### MILESTONE-001: Spec review

Maps to: AC-001~011

Outcome:
- 用户与工程 reviewer 审查本 spec。
- 接受 taxonomy、nullable schema 假设和阶段边界，或仅留下非阻塞修订。

Non-scope:
- 不改代码、DB、测试或生产行为。

Verification signal:
- Spec 状态从 `draft` 改为 `approved`，或 reviewer 只剩非阻塞建议。

### MILESTONE-002: Fixture-first contract implementation

Maps to: REQ-001~035, REQ-045~046, AC-001~004, AC-008

Outcome:
- 实现 dataclass、schema builder、Prompt builder 和纯本地 validator。
- 使用 synthetic/curated 本地 packet 完成第 8 节 Fixture-first tests；不调用真实 Gemini。

Non-scope:
- 不写真实 DB，不接 pipeline，不影响生产评分。

Verification signal:
- 原有测试全通过；新增结构、证据、状态负例测试全通过。

### MILESTONE-003: Shadow persistence

Maps to: REQ-036~039, REQ-045~046, AC-005~006, AC-008

Outcome:
- 经单独批准后使用物理隔离文件 artifact；若要独立 evaluation 表，仍需 DB schema 批准。
- 生产读路径保持不变。

Verification signal:
- `tmp_path` migration/CRUD 测试通过；真实 `tracker.db` 未改动。

### MILESTONE-004: Bounded real shadow review

Maps to: REQ-047~052, AC-006, AC-008

Outcome:
- 只有批准的 provider 或带 provenance 静态数据集就绪后，才可按获批样本运行固定次数 shadow。
- 生成结构、证据、rubric agreement 报告。

Non-scope:
- 不进入 `score_stock()`，不触发 Telegram。

Verification signal:
- Pass gate 有实际证据；未通过则停止并修订 spec/rubric/evidence source。

### MILESTONE-005: Separately approved production cutover

Maps to: REQ-040~044, REQ-053~058, AC-005, AC-007~008

Outcome:
- 单独 plan 选择版本表（优先候选）或有理由的 legacy ALTER，并经确认后才允许进入 production cache/pipeline。

Verification signal:
- 备份、migration、全量测试、受控 smoke 和回滚演练均通过。

## 13. Validation contract

### 13.1 本次 spec 验证

```bash
cd /home/lin/a-stock-tracker
git diff --check
git diff -- docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md
```

应证明：

1. 相对本次任务开始状态，仅目标 Spec 改变；保留所有既有 dirty worktree 内容。
2. REQ 与 AC 编号完整、唯一且可追踪。
3. 明确“Outlines 思想、Gemini 原生 Schema、不新增依赖”的架构决策。
4. 明确 nullable score、taxonomy、“缺证据 fail closed”、fixture/shadow/cutover 边界。
5. 无 trailing whitespace，且 implementation、生产 DB、real shadow、cutover approvals 仍 pending。

### 13.2 未来实现最低验证

实施计划至少应包含：

```bash
.venv/bin/python -m pytest tests/test_gemini_scorer.py -q
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check gemini_scorer.py lib/cache.py tests/test_gemini_scorer.py
.venv/bin/python -m ruff format --check gemini_scorer.py lib/cache.py tests/test_gemini_scorer.py
git diff --check
```

若项目当时已有类型检查基线，再加入项目实际使用的 type checker；不得为满足本 spec 临时引入新工具。

### 13.3 必须报告的证据

- Changed files
- 测试、lint、format 命令与退出码
- Mock fixture 的正反例结果
- Shadow 调用次数、模型、schema/rubric version
- Schema validity、evidence validity、rubric agreement
- 未满足条件、数据缺口和 deferred 项
- 生产 DB、cron、Telegram、weights、历史 predictions 是否保持未触碰

## 14. Rollback and stop conditions

### Rollback

Spec-only 阶段只允许人工撤销本 Spec 的本次 patch，不得触碰其他 dirty files。未来代码阶段必须在 implementation plan 中给出精确的代码 revert 和 DB 备份恢复/readback 命令；不得复用未验证的通用命令。

### Stop if

- 官方 API 当前字段与 spec 假设不一致。
- Gemini 2.5 Flash 不再支持目标 structured output 能力。
- 需要新增依赖但尚未获批。
- Fixture 无法表达 taxonomy 合同，或已批准真实 evidence source/dataset 无法满足 real-shadow 最低证据门槛；后者不阻塞 fixture-first。
- 模型生成未知 evidence ID 或输入外事实，且修复后仍复现。
- Shadow gate 未通过。
- 真实 DB 发生未批准写入。
- 发现会改写历史 predictions、weights_hash 或 outcome。
- 全量测试出现不能归因于本范围的失败。
- 工作区无关改动会被覆盖、格式化或误提交。

## 15. 风险与开放问题

### 已决策

1. 当前不引入 Outlines。
2. 当前不迁移 Gemini Interactions API。
3. 使用原生 JSON Schema + 本地语义校验双层边界。
4. 数据不足必须显式暴露，不允许模型猜分。
5. 新方法先 shadow，不直接进入生产。
6. `score` 保持必填 `integer | null`，实施时以当前官方文档重查和 smoke 为前置。
7. 生产 cache schema mismatch 是 cutover blocker，不是 fixture-first 或文件 shadow blocker。

### 待用户/Reviewer确认

1. **真实 evidence source**：直接竞争优势、行业地位和 sentiment 后续使用可审计 provider，还是批准带 provenance 的静态 dataset？Fixture-first 不需要此决定。
2. **sentiment 数据边界**：项目路线图当前不建设独立舆情层；real shadow 是否批准静态 evidence dataset，或继续让真实 sentiment unavailable？
3. **Shadow 存储**：先用 JSONL/Markdown artifact，还是经确认新增独立 SQLite evaluation 表？
4. **真实 shadow 计划**：6 只股票 × 单次调用是否只作 provisional 首轮，以及升级 gate 的每维度样本数、人工复核比例和行业覆盖是多少？
5. **生产 cache 策略**：未来 cutover plan 是否采用优先候选的新版本表，还是提出充分理由 ALTER legacy 表？
6. **生产适用范围**：若 sentiment 长期 unavailable，是否继续 all-or-nothing，还是另写 spec 将其移出 LLM 职责？本 spec 不允许静默放宽。

## 16. Approval state

- Spec approval: pending
- Implementation approval: pending
- Production DB schema approval: pending
- Real Gemini shadow approval: pending
- Production cutover approval: pending

本 spec 是开发契约草案，不构成任何生产、数据库、付费调用、cron、Telegram 或权重修改授权。
