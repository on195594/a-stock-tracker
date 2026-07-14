# Revised Spec review snapshot

## FILE: docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md

```text
1|---
2|title: Source-Grounded Structured Qualitative Scoring Spec
3|status: draft
4|created: 2026-07-14
5|updated: 2026-07-14
6|owner: lin / Hermes
7|risk_tier: spec-first
8|source_article: https://www.kdnuggets.com/structured-language-model-generation-with-outlines
9|---
10|
11|# 来源约束的结构化定性评分 Spec
12|
13|## 1. 请求改写
14|
15|Original request:
16|> 请根据以上建议帮我写spec，把文章方法落到 a-stock-tracker。
17|
18|Clear prompt:
19|> 为 `a-stock-tracker` 编写一份项目内正式 spec，把 Outlines 文章中的“生成阶段结构约束”思想应用到 Gemini 定性评分，但不直接引入 Outlines。先解决 `gemini_scorer.py` 仅凭股票代码和名称评分、缺少可核验证据的根因；再使用 Gemini 2.5 Flash 原生 JSON Schema 固定响应结构，并保留应用端语义校验、缓存和 fallback。所有新行为必须先走 project-local fixture 与 shadow/report-only 验证，不得直接改变生产评分、历史 predictions、权重、推送或 cron。
20|
21|What changed:
22|- 将“提升 Agent 准确性”拆成结构正确性、证据一致性、评分一致性和投资预测有效性四个不同目标。
23|- 明确 Outlines 不是当前首选依赖；复用其 constrained generation 思路，使用现有 Gemini REST 接口的原生结构化输出能力。
24|- 将生产切换拆成 shadow/report-only 与显式 cutover 两个阶段。
25|- 将“有合法 JSON”从成功条件降为必要条件；没有可引用证据时必须 fail closed。
26|
27|## 2. 背景与问题定义
28|
29|当前 `gemini_scorer.py` 调用 Gemini 2.5 Flash，为以下三个字段生成整数：
30|
31|- `moat`: 1-10
32|- `market_pos`: 1-5
33|- `sentiment`: 1-5
34|
35|当前 Prompt 只提供：
36|
37|```text
38|股票：{name}（{code}）
39|```
40|
41|但要求模型判断护城河、市场地位和近期消息面。当前链路已有 JSON 解析、值域校验、30 天缓存、429/5xx 退避重试、过期缓存降级和 all-or-nothing fallback；这些机制能控制格式和可用性，不能证明评分有事实依据。
42|
43|当前主要风险：
44|
45|1. **Grounding 缺失**：模型没有收到财务、竞争地位、公告或新闻证据。
46|2. **格式只由 Prompt 约束**：依赖“只返回 JSON”，仍需处理 Markdown 包装和解析失败。
47|3. **合法值不等于正确值**：`{"moat": 8, "market_pos": 5, "sentiment": 4}` 即使完全合法，也可能没有证据。
48|4. **缓存放大静默错误**：无依据的合法结果可能被缓存 30 天并注入 `total_score`。
49|5. **版本不可区分**：现有 `qualitative_scores` 只保存三个整数和日期，无法区分评分方法、输入证据或 schema 版本。
50|
51|本 spec 的核心判断：
52|
53|> 结构约束负责“输出可解析”，证据包和应用端验证负责“结论可追溯”；两者都不能单独证明投资预测有效。
54|
55|## 3. 目标
56|
57|### G1. 让模型只能基于显式输入证据评分
58|
59|每个评分维度必须引用输入证据包中的稳定 `evidence_id`。模型不得使用未提供的公司事实、市场份额、新闻或时间敏感信息。
60|
61|### G2. 用 Gemini 原生 JSON Schema 约束输出
62|
63|在不新增第三方依赖的前提下，使用 Gemini `generateContent` REST API 的结构化输出能力，约束字段类型、必填字段、枚举和值域。
64|
65|### G3. 保留应用端语义和业务校验
66|
67|Schema 合法不能绕过本地验证。本地验证必须检查证据引用、状态与分数一致性、日期、新鲜度、值域和禁止额外字段。
68|
69|### G4. Fail closed，不把信息不足包装成评分
70|
71|证据不足时返回 `insufficient_data`，不得让模型猜分。生产适配器沿用“新鲜可信结果 → 过期可信缓存 → 固定 fallback”的顺序，不允许将部分新分数与部分 fallback 混用。
72|
73|### G5. 先 shadow，再决定是否切换生产
74|
75|新方法首先写入独立 evaluation 表或文件型报告，不影响现有 `qualitative_scores`、`predictions.total_score`、Telegram 或 cron。生产切换必须另有实施计划、DB 变更确认和明确批准。
76|
77|### G6. 可分别评估四种准确性
78|
79|必须分开报告：
80|
81|1. Schema validity：输出是否符合结构。
82|2. Evidence validity：每个结论是否能回指提供的证据。
83|3. Rubric agreement：评分是否与预先定义的评分锚点一致。
84|4. Predictive validity：评分是否改善后续 30/60/90 日 alpha；该项只能通过自然结案验证，不能由本 spec 或一次模型运行证明。
85|
86|## 4. 非目标
87|
88|本 spec 不授权：
89|
90|1. 直接安装或引入 `outlines`、Pydantic、Google GenAI SDK 或其他新依赖。
91|2. 修改 `weights.json`、`buy_strong` 等阈值或 Framework A/B 生产边界。
92|3. 重写历史 `predictions.total_score`、`weights_hash`、outcome 或 alpha 字段。
93|4. 自动交易、下单、持仓写入或资金相关操作。
94|5. 新建独立舆情/NLP 数据源，或默认开启 Gemini Google Search/URL Context。
95|6. 把股价技术信号冒充公司护城河或市场地位证据。
96|7. 将 JSON Schema 合法率称为投资评分准确率。
97|8. 未经确认修改生产 DB schema、cron、Telegram 推送或真实 Gemini 调用频率。
98|9. 在 shadow 阶段让新评分进入 `score_stock()` 或 `predictions`。
99|10. 删除现有 `_validate()`、stale cache 或固定 fallback。
100|
101|## 5. 当前基线
102|
103|- Project root: `/home/lin/a-stock-tracker`
104|- Production scorer: `gemini_scorer.py`
105|- Pipeline injection: `pipeline.py` 中 `get_qualitative_score()` → `moat_fixed` / `market_pos_fixed` / `sentiment_fixed`
106|- DB schema owner: `lib/cache.py`
107|- Existing cache table: `qualitative_scores(code, moat, market_pos, sentiment, scored_date)`
108|- Existing tests: `tests/test_gemini_scorer.py`
109|- Existing test contract: 所有 Gemini/AKShare/Telegram 网络调用必须 mock
110|- Current model: `gemini-2.5-flash`
111|- Current API: `v1beta/models/{model}:generateContent`
112|- Current fallback order: fresh cache → Gemini → stale cache → fixed `FALLBACK`
113|
114|Relevant documentation:
115|
116|- Source article: <https://www.kdnuggets.com/structured-language-model-generation-with-outlines>
117|- Gemini `generateContent` structured output: <https://ai.google.dev/gemini-api/docs/generate-content/structured-output>
118|
119|Official-doc constraints recorded for this spec:
120|
121|- Gemini 2.5 Flash supports structured output.
122|- JSON Schema support is a subset, not the full specification.
123|- 当前官方 `generateContent` structured-output 文档明确允许在 type 数组中包含 `"null"`，例如 `{"type": ["integer", "null"]}`；因此本 spec 保留稳定对象形状，`score` 始终存在，证据不足时为 `null`。
124|- Schema can constrain object/array/string/integer/enum/minimum/maximum 等字段。
125|- Structured output guarantees syntactically valid schema-shaped JSON，不保证值在业务语义上正确；应用仍必须校验。
126|- 上述能力是 2026-07-14 的实施假设，不是永久 API 保证。Fixture-first 实施前必须重新读取[官方 structured-output 文档](https://ai.google.dev/gemini-api/docs/generate-content/structured-output)；mock tests 通过后，还必须经单独批准执行一次受控 smoke，再开始 real shadow。若字段或 nullable 支持变化则停止并修订计划。
127|- 本 spec 继续使用现有 `generateContent` 接口，不迁移 Interactions API；接口迁移属于独立任务。
128|
129|## 6. 术语和状态
130|
131|### 6.1 Evidence packet
132|
133|送入模型的唯一事实来源。Fixture 与未来真实 evidence packet 使用同一形状。每条证据至少包含：
134|
135|```json
136|{
137|  "evidence_id": "fundamentals.roe_3y_avg",
138|  "evidence_type": "financial_metric",
139|  "allowed_dimensions": ["moat"],
140|  "directness": "supporting",
141|  "freshness_policy": "max_age_550d",
142|  "value": 14.2,
143|  "unit": "percent",
144|  "source": "local_fundamentals_cache",
145|  "source_date": "2026-03-31",
146|  "freshness_status": "fresh"
147|}
148|```
149|
150|`evidence_id` 使用可读命名空间，但命名空间不是主要业务判断依据：
151|
152|- `fundamentals.*` → `financial_metric`
153|- `valuation.*` → `valuation_metric`
154|- `competitive_advantage.*` → `competitive_advantage`
155|- `industry.*` → `industry_position`
156|- `announcement.*` → `company_announcement`
157|- `news.*` → `news_report`
158|- `consensus.*` → `analyst_consensus`
159|
160|机器可检查词表：
161|
162|- `evidence_type`: `financial_metric | valuation_metric | competitive_advantage | industry_position | company_announcement | news_report | analyst_consensus`
163|- `allowed_dimensions`: 只能是 `moat | market_pos | sentiment` 的去重、稳定排序子集；允许空数组表示只能作为上下文、不能支撑任何维度。
164|- `directness`: `direct | supporting | context`。`direct` 直接测量或明确陈述待评分事实；`supporting` 只增强已有直接证据；`context` 不得单独或联合触发 `scored`。
165|- `freshness_policy`: `max_age_30d | max_age_90d | max_age_365d | max_age_550d`。
166|- `freshness_status`: `fresh | stale`，由本地验证器计算并与输入声明比对，不能由来源任意决定。
167|- `value` 只能是 JSON string/number/boolean scalar；`unit` 是非空 string 或显式 `null`，`source` 是非空 bounded string，`source_date` 是 ISO `YYYY-MM-DD`。
168|
169|第一版 taxonomy registry 的规范映射如下；实现可用常量或版本化 fixture 表达，但不得让输入自行放宽：
170|
171|| evidence_type | 命名空间 | 规范 allowed_dimensions | 允许 directness | freshness_policy |
172||---|---|---|---|---|
173|| `financial_metric` | `fundamentals.*` | `[moat]` | `supporting`, `context` | `max_age_550d` |
174|| `valuation_metric` | `valuation.*` | `[]` | `context` | `max_age_30d` |
175|| `competitive_advantage` | `competitive_advantage.*` | `[moat]` | `direct`, `supporting` | `max_age_365d` |
176|| `industry_position` | `industry.*` | `[market_pos]` | `direct`, `supporting` | `max_age_365d` |
177|| `company_announcement` | `announcement.*` | `[sentiment]` | `direct`, `supporting` | `max_age_30d` |
178|| `news_report` | `news.*` | `[sentiment]` | `direct`, `supporting` | `max_age_30d` |
179|| `analyst_consensus` | `consensus.*` | `[sentiment]` | `direct`, `supporting` | `max_age_90d` |
180|
181|验证器主要依据 `evidence_type`、`allowed_dimensions`、`directness` 和 `freshness_policy`；命名空间只做 registry 一致性校验和可读性检查，不得用字符串前缀替代 taxonomy。日期按 `as_of_date - source_date` 的自然日差确定：未来日期拒绝，`age <= max_age_days`（含边界日）为 `fresh`，超过为 `stale`。类型、维度、directness、policy 或 registry 组合未知/矛盾时拒绝整个输入。
182|
183|### 6.2 Dimension status
184|
185|每个维度只能使用：
186|
187|- `scored`: 证据足够，可给分。
188|- `insufficient_data`: 证据不足，不给分。
189|
190|不得使用 `unknown`、`probably` 等模糊状态。
191|
192|### 6.3 Overall status
193|
194|- `scored`: 三个维度全部通过本地验证。
195|- `insufficient_data`: 至少一个维度证据不足。
196|- `invalid`: 仅由本地代码产生，表示模型输出虽返回但违反应用端契约；模型不得自行返回该状态。
197|
198|### 6.4 Confidence
199|
200|模型输出只允许：`low | medium | high`。Confidence 是证据充分度说明，不是收益概率，不得进入 `total_score`。
201|
202|## 7. Requirements
203|
204|### 7.1 输入证据契约
205|
206|- **REQ-001:** 新评分入口必须接收显式 `QualitativeContext`；它必须至少包含 `code`、`name`、`industry`、`as_of_date`、`schema_version`、`rubric_version`、`taxonomy_version` 和 `evidence[]`。Evidence packet 是唯一事实来源，Prompt 必须禁止使用输入外知识。
207|- **REQ-002:** 输入构建器必须规范化证据顺序和 `allowed_dimensions` 顺序并计算 `input_hash`；相同版本和内容必须得到相同 hash，日期、版本或证据变化必须改变 hash。
208|- **REQ-003:** 每条 evidence 必须包含 6.1 定义的 `evidence_id`、`evidence_type`、`allowed_dimensions`、`directness`、`freshness_policy`、value/unit/source/source_date/freshness_status；缺失不适用的 unit 时必须显式使用 `null`，不得省略稳定字段。
209|- **REQ-004:** `evidence_id` 必须唯一并符合 6.1 命名空间；业务授权主要由结构化 taxonomy 字段决定，字符串前缀只验证 registry 一致性。
210|- **REQ-005:** Validator 必须拒绝重复 evidence ID，未知 type/dimension/directness/policy，未来日期，声明 freshness 与计算结果不一致，以及 type、namespace、allowed dimensions、directness、policy 之间的矛盾组合。
211|- **REQ-006:** 现有基本面缓存可提供候选 `financial_metric`：`roe_3y_avg`、`roe_latest`、`net_profit_growth`、`debt_ratio`、`gross_margin`、`report_period`。`pb_percentile_10y` 是 `valuation_metric`，只能作 context，不能证明 moat 或 market_pos。
212|- **REQ-007:** `moat=scored` 的机械最低门槛是至少一条 fresh、`direct`、允许 `moat` 的 `competitive_advantage`，并有至少一条 fresh `financial_metric` supporting evidence；缺任一类必须为 `insufficient_data`。
213|- **REQ-008:** `market_pos=scored` 的机械最低门槛是至少一条 fresh、`direct`、允许 `market_pos` 的 `industry_position`；公司自身财务或估值不能单独证明行业地位。
214|- **REQ-009:** `sentiment=scored` 的机械最低门槛是至少一条 fresh、`direct`、允许 `sentiment` 的 `company_announcement`、`news_report` 或 `analyst_consensus`；财报、估值或价格趋势不能替代。
215|- **REQ-010:** 机械门槛只判断合同资格，不宣称证据真实、相关、独立或足以支撑某个分数；这些语义质量与评分充分性仍由版本化 rubric 和盲审人工判断。
216|- **REQ-011:** 缺少 REQ-007~009 的证据必须显式产生 `insufficient_data`；不得用固定分数伪装成模型结果，也不得降低门槛以提高通过率。
217|- **REQ-012:** 所有输入只允许来自本地 fixture、已批准静态数据集或已批准 provider，并保留可审计的来源和日期；未经批准不得让模型自行搜索或补齐事实。
218|
219|### 7.2 结构化输出契约
220|
221|- **REQ-013:** 不新增 Outlines 依赖；使用现有标准库 HTTP 路径和 Gemini 2.5 Flash 原生 JSON Schema，不在本 spec 迁移 Interactions API。
222|- **REQ-014:** Gemini 请求必须在 `generationConfig` 中声明 JSON MIME 类型和 schema；字段名和 nullable 能力必须在实施时依据[官方文档](https://ai.google.dev/gemini-api/docs/generate-content/structured-output)重查并在批准的真实 shadow 前 smoke，不得把当前能力当永久保证。
223|- **REQ-015:** 顶层输出必须包含且只包含 `schema_version`、`overall_status`、`as_of_date`、`dimensions`。
224|- **REQ-016:** `dimensions` 必须且只包含 `moat`、`market_pos`、`sentiment`。
225|- **REQ-017:** 每个维度必须包含且只包含 `status`、`score`、`confidence`、`evidence_ids`、`rationale`；`score` 始终存在以保持稳定对象形状。
226|- **REQ-018:** `score` 的 JSON Schema 类型必须为 `{"type": ["integer", "null"]}`；`moat` 的整数范围为 1-10，`market_pos` 和 `sentiment` 为 1-5，本地 validator 必须再次执行范围检查。
227|- **REQ-019:** `status=scored` 时 `score` 必须为整数且 `evidence_ids` 非空；`status=insufficient_data` 时 `score` 必须为 `null`，`evidence_ids` 可为空，`rationale` 必须说明缺少的证据。
228|- **REQ-020:** `rationale` 只允许解释输入证据与评分锚点之间的关系，不得引入输入外事实，并设置固定字符上限。
229|- **REQ-021:** Schema 应使用官方当前支持的 `required`、`additionalProperties=false`、`enum`、`minimum`、`maximum` 和数组数量限制；API 未执行的约束由本地 validator 补齐。
230|- **REQ-022:** JSON key 顺序、空白或换行不得成为业务逻辑；只能按解析后的对象判断。
231|
232|参考输出：
233|
234|```json
235|{
236|  "schema_version": "qualitative-score-v2",
237|  "overall_status": "insufficient_data",
238|  "as_of_date": "2026-07-14",
239|  "dimensions": {
240|    "moat": {
241|      "status": "insufficient_data",
242|      "score": null,
243|      "confidence": "low",
244|      "evidence_ids": ["fundamentals.roe_3y_avg"],
245|      "rationale": "财务持续性数据存在，但缺少直接竞争优势证据。"
246|    },
247|    "market_pos": {
248|      "status": "insufficient_data",
249|      "score": null,
250|      "confidence": "low",
251|      "evidence_ids": [],
252|      "rationale": "缺少市场份额、行业排名或同行对比证据。"
253|    },
254|    "sentiment": {
255|      "status": "insufficient_data",
256|      "score": null,
257|      "confidence": "low",
258|      "evidence_ids": [],
259|      "rationale": "缺少新鲜且带日期的公告或新闻证据。"
260|    }
261|  }
262|}
263|```
264|
265|### 7.3 本地验证契约
266|
267|- **REQ-023:** 新 validator 必须独立于 HTTP，可接受普通 Python 对象和 `QualitativeContext` 做纯函数校验。
268|- **REQ-024:** Validator 必须拒绝缺失/额外字段、错误类型、越界分数、未知枚举和输出 `schema_version` 不匹配；evaluation/cache boundary 必须另行拒绝 envelope 中 `rubric_version`、`taxonomy_version` 或 `input_hash` 不匹配。
269|- **REQ-025:** Validator 必须拒绝未知或重复引用的 `evidence_id`，并拒绝任何引用证据不允许目标维度、不是所需 directness 或已 stale 的结果。
270|- **REQ-026:** Validator 必须拒绝 `scored + null`、`insufficient_data + integer`、`scored + empty evidence_ids`、维度与 overall status 不一致等语义矛盾。
271|- **REQ-027:** Validator 必须检查 `as_of_date` 与输入一致，并按 6.1 registry 重算 taxonomy 与 freshness；不能接受模型或 packet 自行放宽 policy。
272|- **REQ-028:** Schema-valid 只表示结构通过。确定性 validator 必须拒绝未满足 REQ-007~009 最低门槛的结果；rationale 是否引入输入外事实、证据是否真实相关等主观语义由同 rubric 的盲审或显式注入的 review outcome 判断，并分类为 evidence/semantic invalid。两类失败都不得变成可信分数。
273|- **REQ-029:** 任一维度 validation 失败时整个新结果为 `invalid`，不得只采用另两个维度；合法的 `insufficient_data` 则使 overall status 为 `insufficient_data`。
274|- **REQ-030:** 现有 v1 `_validate()` 与生产路径在 cutover 批准前不得删除或改变；未来兼容适配器必须保留整数范围二次检查。
275|
276|### 7.4 评分锚点
277|
278|- **REQ-031:** Prompt 和人工审查必须使用同一份版本化 rubric；rubric 变更必须更新 `rubric_version`，不允许同版本静默变更。
279|- **REQ-032:** `moat` 评分必须采用可解释锚点，而不是“凭感觉 1-10”：
280|  - 1-2：存在明显竞争劣势或证据不支持持续优势。
281|  - 3-4：优势弱、易复制或持续性证据不足。
282|  - 5-6：存在一项可识别优势，但持续性/强度证据一般。
283|  - 7-8：至少一项强且持续的竞争优势，有多条独立证据支持。
284|  - 9-10：多重强优势且长期持续；必须有高质量直接证据，不得仅凭高 ROE/高毛利给出。
285|- **REQ-033:** `market_pos` 锚点：
286|  - 1：边缘参与者或明确弱势。
287|  - 2：非头部、地位一般。
288|  - 3：主要参与者，但没有明确头部证据。
289|  - 4：细分或行业头部，有直接排名/份额证据。
290|  - 5：明确领导者，且有多条最新直接证据；不得只凭公司规模推断。
291|- **REQ-034:** `sentiment` 锚点：
292|  - 1：近期重大明确负面。
293|  - 2：偏负面。
294|  - 3：中性或多空抵消。
295|  - 4：偏正面。
296|  - 5：近期重大明确正面。
297|  - 无新鲜证据：`insufficient_data`，不是默认 3。
298|- **REQ-035:** Schema/evidence validity 与 rubric agreement 必须和 predictive validity 分开报告；前者改善不证明正 alpha 或投资准确性提升。
299|
300|### 7.5 缓存与 fallback
301|
302|- **REQ-036:** Fixture 和 shadow 必须与生产物理隔离：不得写现有 `qualitative_scores`、`predictions` 或真实 `tracker.db`，不得注入 pipeline、影响 predictions/Telegram/cron；只可写经当前阶段批准的独立文件 artifact，未来独立 evaluation 表仍需 DB 批准。
303|- **REQ-037:** Shadow evaluation 必须保存 `code`、`scored_date`、`schema_version`、`rubric_version`、`taxonomy_version`、`input_hash`、`model`、`overall_status`、`context_json`、`result_json`、`validation_status`、`failure_reason`、`created_at`。
304|- **REQ-038:** `validation_status` 使用有界枚举：`VALID_SCORED | VALID_INSUFFICIENT_DATA | API_TIMEOUT | API_AUTH_ERROR | API_RATE_LIMIT | API_SERVER_ERROR | MALFORMED_RESPONSE | SCHEMA_INVALID | EVIDENCE_INVALID | SEMANTIC_INVALID`。401/403 映射 auth，429 映射 rate limit，5xx 映射 server error。
305|- **REQ-039:** `failure_reason` 只能保存有界错误 code 和最多 256 字符的脱敏摘要；Prompt、日志、context/result、artifact/table 均不得保存 API key、token、环境变量值、原始 headers、stack trace 或其他凭证/秘密。
306|- **REQ-040:** 当前生产表 `qualitative_scores(code, moat, market_pos, sentiment, scored_date)` 缺少版本和 input hash，因而阻塞 production cutover；它不阻塞 fixture-first 或物理隔离的文件 artifact shadow。Fixture-first 不得选择或实施生产 migration。
307|- **REQ-041:** 后续获批 cutover plan 必须二选一：优先候选是新建版本化生产表；`ALTER` legacy 表是需说明风险与理由的替代方案。任何方案都不得让 v1/v2 分数不可区分，并必须定义 migration、indexes、cache priority、backward compatibility、backup、readback、rollback 和版本分层报告。
308|- **REQ-042:** Cutover 后缓存读取优先级必须为：schema/rubric/taxonomy/input-hash 均匹配的 fresh validated v2 → 同版本 stale validated v2 → legacy stale cache（仅迁移期）→ 固定 `FALLBACK`。
309|- **REQ-043:** `insufficient_data`、`invalid` 或 API failure 不得覆盖 trusted cache；不得混用 v2、v1 和 fallback 的不同维度，保持 all-or-nothing。
310|- **REQ-044:** 外部调用可对 timeout、429、5xx 做有上限、带抖动的 retry；401/403、malformed response、schema、evidence 或 semantic error 不重试。具体次数/退避值留给 implementation plan，但测试必须证明有界。
311|
312|候选 shadow 表（仅设计，不构成 DB 修改批准）：
313|
314|```sql
315|CREATE TABLE qualitative_score_evaluations (
316|    id INTEGER PRIMARY KEY AUTOINCREMENT,
317|    code TEXT NOT NULL,
318|    scored_date TEXT NOT NULL,
319|    schema_version TEXT NOT NULL,
320|    rubric_version TEXT NOT NULL,
321|    model TEXT NOT NULL,
322|    input_hash TEXT NOT NULL,
323|    taxonomy_version TEXT NOT NULL,
324|    overall_status TEXT,
325|    context_json TEXT NOT NULL,
326|    result_json TEXT,
327|    validation_status TEXT NOT NULL,
328|    failure_reason TEXT,
329|    created_at TEXT NOT NULL,
330|    UNIQUE(code, scored_date, schema_version, rubric_version, taxonomy_version, input_hash)
331|);
332|```
333|
334|### 7.6 Shadow/report-only gate
335|
336|- **REQ-045:** Fixture-first 只能使用 synthetic 或人工整理的本地 evidence packet，可同时包含完整 positive 与各维度 `insufficient_data` cases；fixture 必须确定性、免凭证、无网络，并携带与未来真实 packet 完全相同的 taxonomy。
337|- **REQ-046:** Fixture 结果只证明 parser/schema/validator/rubric 路径行为，不证明真实数据可得、事实正确或投资准确。
338|- **REQ-047:** Real shadow 必须等待单独批准的 evidence source/provider，或带 provenance 的已批准静态 evidence dataset。真实证据缺失是 real-shadow blocker，不是 fixture-first blocker；不得为通过 real shadow 降低 moat/market_pos/sentiment 门槛。
339|- **REQ-048:** 真实 shadow 属于付费外部调用和生产数据读取，执行前必须单独确认 provider/dataset、范围、股票样本、调用次数、凭证处理和输出位置；初始样本候选为按行业分层的 6 只代表股票。
340|- **REQ-049:** v2 独立入口是优先的未来隔离 seam；确切文件、函数和 patch 位置由 implementation plan 基于当时代码决定，本 spec 不预先授权改生产入口。
341|- **REQ-050:** 人工复核必须先在不知道 v1 分数和模型结果的情况下，基于同一 evidence packet 给出参考状态/评分，再比较 v2。
342|- **REQ-051:** Shadow pass gate：
343|  1. Schema validity = 100%。
344|  2. 未知 evidence ID 接受率 = 0%。
345|  3. 无依据事实数量 = 0。
346|  4. 所有证据不足 case 均 fail closed。
347|  5. 对人工判定可评分的维度，至少 90% 落在人工参考分 ±1 内；样本不足时只能标记 provisional。
348|- **REQ-052:** `provisional` 只能在后续获批 evaluation plan 预先定义并达到每维度最小可评分样本数、人工复核比例和行业覆盖，且扩展样本继续满足 REQ-051 后升级；具体样本门槛是 real-shadow plan 的待决策项，不能由运行后补定。通过 gate 仍不代表预测有效或自动批准 cutover。
349|
350|### 7.7 生产切换与后验评估
351|
352|- **REQ-053:** Production cutover 必须另写并批准 implementation plan，列出确切代码 seam、REQ-041 的 DB 策略、migration/indexes、测试、受控 smoke、部署和回滚；本 spec 不批准任何实现或 DB 变更。
353|- **REQ-054:** 修改生产 DB 前必须备份 `tracker.db`，验证备份可打开和关键表可读，并在 migration 后执行 readback；rollback 必须覆盖代码和物理 DB 恢复。
354|- **REQ-055:** Cutover 不得回写历史 predictions，只影响批准日期之后的新评分；v1 生产路径在兼容期必须继续按当前合同工作。
355|- **REQ-056:** Cutover 后必须记录评分方法版本，使 reporting/`accuracy-report` 按 schema/rubric/taxonomy 版本和 score_date 分层，禁止 v1/v2 混算。
356|- **REQ-057:** 后验投资验证至少分别报告 30/60/90 日 alpha、hit rate、样本数和置信限制；在自然结案样本不足前不得宣称准确性提升。
357|- **REQ-058:** Implementation、生产 DB、真实 Gemini shadow 和 production cutover 均保持 pending，必须分别批准；任何前一阶段通过都不隐含后一阶段授权。
358|
359|## 8. Test matrix
360|
361|以下是未来实现合同，不授权本次新增测试或运行真实 API。Fixture-first cases 使用纯本地对象、mock HTTP 和临时路径；later shadow 与 production-cutover cases 不阻塞第一阶段完成。
362|
363|| Stage | Case | Expected assertion |
364||---|---|---|
365|| Fixture-first | 完整可评分结果 | 三维均 scored，整数范围、evidence 引用和 overall status 全部通过 |
366|| Fixture-first | moat / market_pos / sentiment 各自缺证据 | 每个分支分别返回 `insufficient_data`，全局 fail closed |
367|| Fixture-first | nullable score | scored+integer 与 insufficient+null 通过；反向组合拒绝；`score` 不得缺失 |
368|| Fixture-first | 未知/重复 evidence ID | 输入重复、输出重复引用、输出未知引用均拒绝 |
369|| Fixture-first | 未知 taxonomy | 未知 evidence type、dimension、directness、freshness policy 各自拒绝 |
370|| Fixture-first | allowed-dimension mismatch | evidence 不允许目标维度或 registry 组合矛盾时拒绝 |
371|| Fixture-first | stale 与边界日期 | age 等于窗口上限为 fresh；多一天为 stale；未来日期拒绝；声明状态不一致拒绝 |
372|| Fixture-first | schema-valid 但语义无效 | 状态/overall 矛盾、最低门槛不足由 validator 拒绝；注入盲审判定的 unsupported rationale 时分类 semantic invalid |
373|| Fixture-first | shape/range failures | extra field、missing field、错误类型及各维度上下界外值拒绝 |
374|| Fixture-first | malformed response | 分类为 `MALFORMED_RESPONSE`，不产出 trusted result |
375|| Fixture-first | mocked API timeout / 401 / 403 / 429 / 5xx | 分别映射 timeout/auth/rate-limit/server bounded status，reason 脱敏 |
376|| Fixture-first | retry policy | auth/schema/evidence/semantic 不 retry；429/5xx 有界 retry；达到上限后 fallback |
377|| Fixture-first | all-or-nothing | 任一维度 invalid/insufficient 时不拼接 v2、v1、fallback 维度 |
378|| Fixture-first | 版本 mismatch | schema/rubric/taxonomy/input-hash 任一不符均不视为同版本 trusted result |
379|| Fixture-first | 不覆盖 trusted cache | 用 mock repository 证明 invalid/insufficient/API failure 不触发可信缓存覆盖 |
380|| Later shadow | provenance 与真实证据可用性 | 仅批准 provider/dataset 可运行；缺 moat/market_pos/sentiment 真实证据明确阻塞 |
381|| Later shadow | shadow 物理隔离 | artifact/evaluation 不写生产表、pipeline、predictions、Telegram、cron |
382|| Production cutover | fresh same-version cache | schema/rubric/taxonomy/input-hash 全匹配且 fresh 时优先读取 |
383|| Production cutover | stale same-version cache | 新调用失败后可用同版本 stale validated cache |
384|| Production cutover | legacy migration fallback | 仅兼容窗口按明确优先级读取 legacy cache，且来源/版本可区分 |
385|| Production cutover | v1 backward compatibility | 当前 v1 production path 在切换前及兼容窗口行为不变 |
386|| Production cutover | migration/readback/rollback/reporting | indexes、备份可读、迁移 readback、物理 rollback 和版本分层报告均验证 |
387|
388|## 9. 设计约束
389|
390|### Always do
391|
392|- 新增函数必须有参数和返回值类型注解。
393|- 使用 `logger = logging.getLogger(__name__)`，禁止生产 `print`。
394|- 数据结构优先使用 `@dataclass`，不要让跨层契约长期依赖裸字典。
395|- 外部 API 错误必须按 REQ-038~044 分类、脱敏并遵守 retry/fallback 边界。
396|- 测试使用 `tmp_path`，不得访问真实 `tracker.db`。
397|- 所有网络调用必须 mock；真实 Gemini shadow 需要单独批准。
398|- 每次实现必须先写失败测试，再改代码。
399|- 实施前重新核对 Gemini 当前官方文档及 nullable schema，因为结构化输出字段可能变化。
400|
401|### Ask first
402|
403|- 添加或升级依赖。
404|- 修改 `lib/cache.py` DDL 或真实 SQLite schema。
405|- 执行真实 Gemini shadow/smoke。
406|- 修改 production pipeline 注入、缓存优先级、cron 或 Telegram。
407|- 增加公告、新闻、搜索或其他外部证据源。
408|- 改变定性分数权重、评分阈值或历史数据。
409|
410|### Never do
411|
412|- 把 Schema 合法当成语义正确。
413|- 让模型引用未提供的 evidence ID。
414|- 用模型训练记忆补齐公司事实。
415|- 把 `insufficient_data` 静默转成中性分后称为模型结果。
416|- 混用新评分、旧评分和 fallback 三个维度。
417|- 删除或覆盖无关工作区改动。
418|- 提交 `.env`、API key、token 或真实私密 headers。
419|- 未经批准改写生产 `tracker.db`、predictions 或 cron。
420|
421|## 10. 方案选择与被拒绝方案
422|
423|### 10.1 选择方案
424|
425|```text
426|Local evidence sources
427|  → QualitativeContext / evidence IDs / input_hash
428|  → Gemini 2.5 Flash native JSON Schema
429|  → JSON parse
430|  → deterministic schema + evidence + rubric validation
431|  → shadow evaluation artifact/table
432|  → human rubric review
433|  → separately approved production adapter
434|  → natural 30/60/90d outcome validation
435|```
436|
437|### 10.2 被拒绝方案
438|
439|- **直接安装 Outlines**：当前只有一个 Gemini REST 后端，原生结构化输出已覆盖核心需求；Outlines会新增依赖和适配层，不能解决 grounding。
440|- **只给当前 Prompt 加 JSON Schema**：会得到更稳定的无依据评分，降低可见错误但保留静默语义风险。
441|- **让 Gemini 自动搜索互联网并评分**：来源、时点、引用、成本和稳定性边界未定义；不属于本次最小改造。
442|- **把现有基本面指标直接当 moat/market_pos 证据**：盈利质量不等于竞争壁垒，规模也不等于行业领导地位。
443|- **sentiment 缺数据时自动给 3 分并标为模型结果**：掩盖数据缺口。固定 3 只能继续作为系统 fallback，并必须与模型评分区分。
444|- **直接扩展现有 qualitative_scores 后立刻切换**：无法在不影响生产的前提下比较 v1/v2，且 schema 变更需要额外批准。
445|- **部分字段采用新模型结果**：破坏现有 all-or-nothing 安全边界，使评分来源难以解释。
446|- **为跑通 real shadow 放宽 evidence taxonomy**：把数据缺口转化为无依据分数；缺少批准的真实证据应阻塞 real shadow，而非削弱合同。
447|- **fixture-first 决定生产表 migration**：本地合同测试不需要生产 schema；版本表或 legacy ALTER 必须在后续 cutover plan 中选择。
448|
449|## 11. Acceptance criteria
450|
451|- **AC-001 maps to REQ-001~012:** Evidence packet 有显式、版本化、机器可检查 taxonomy，未知/矛盾/过期证据 fail closed，主验证不依赖前缀推断。
452|- **AC-002 maps to REQ-013~022:** Gemini 原生 JSON Schema 保持必填 `integer | null` score，并把当前 nullable 支持记录为实施时重查/smoke 的官方文档假设。
453|- **AC-003 maps to REQ-023~030:** 独立 validator 覆盖 shape、版本、evidence、freshness、状态语义和 all-or-nothing invalidation。
454|- **AC-004 maps to REQ-031~035:** 三维使用版本化 rubric，结构/evidence/rubric 指标与 predictive validity 分离。
455|- **AC-005 maps to REQ-036~044:** Shadow 物理隔离、脱敏 bounded failure 分类、retry、cache 版本和 cutover-only migration blocker 均明确。
456|- **AC-006 maps to REQ-045~052:** Fixture 与 real shadow 分阶段，真实证据缺失只阻塞 real shadow，gate/provisional 升级不弱化证据门槛。
457|- **AC-007 maps to REQ-053~058:** Cutover plan 必须覆盖版本化 DB 策略、备份/readback/rollback、v1 兼容和分层后验报告，且所有批准仍 pending。
458|- **AC-008:** 第 8 节 test matrix 覆盖要求的成功、失败、retry、cache、版本、兼容与隔离 cases，并标记 Fixture-first/Later shadow/Production cutover。
459|- **AC-009:** 本次只修改本 Spec，不修改代码、DB、测试、依赖、weights、历史记录、cron 或 Telegram。
460|- **AC-010:** REQ 与 AC 编号唯一连续、range/milestone mapping 一致，无 trailing whitespace，`git diff --check` 通过。
461|- **AC-011:** 用户/Reviewer 明确决定第 15 节开放项；未决定不阻塞 fixture-first，但阻塞对应 real-shadow 或 cutover 阶段。
462|
463|## 12. Milestones and plan handoff
464|
465|### MILESTONE-001: Spec review
466|
467|Maps to: AC-001~011
468|
469|Outcome:
470|- 用户与工程 reviewer 审查本 spec。
471|- 接受 taxonomy、nullable schema 假设和阶段边界，或仅留下非阻塞修订。
472|
473|Non-scope:
474|- 不改代码、DB、测试或生产行为。
475|
476|Verification signal:
477|- Spec 状态从 `draft` 改为 `approved`，或 reviewer 只剩非阻塞建议。
478|
479|### MILESTONE-002: Fixture-first contract implementation
480|
481|Maps to: REQ-001~035, REQ-045~046, AC-001~004, AC-008
482|
483|Outcome:
484|- 实现 dataclass、schema builder、Prompt builder 和纯本地 validator。
485|- 使用 synthetic/curated 本地 packet 完成第 8 节 Fixture-first tests；不调用真实 Gemini。
486|
487|Non-scope:
488|- 不写真实 DB，不接 pipeline，不影响生产评分。
489|
490|Verification signal:
491|- 原有测试全通过；新增结构、证据、状态负例测试全通过。
492|
493|### MILESTONE-003: Shadow persistence
494|
495|Maps to: REQ-036~039, REQ-045~046, AC-005~006, AC-008
496|
497|Outcome:
498|- 经单独批准后使用物理隔离文件 artifact；若要独立 evaluation 表，仍需 DB schema 批准。
499|- 生产读路径保持不变。
500|
501|Verification signal:
502|- `tmp_path` migration/CRUD 测试通过；真实 `tracker.db` 未改动。
503|
504|### MILESTONE-004: Bounded real shadow review
505|
506|Maps to: REQ-047~052, AC-006, AC-008
507|
508|Outcome:
509|- 只有批准的 provider 或带 provenance 静态数据集就绪后，才可按获批样本运行固定次数 shadow。
510|- 生成结构、证据、rubric agreement 报告。
511|
512|Non-scope:
513|- 不进入 `score_stock()`，不触发 Telegram。
514|
515|Verification signal:
516|- Pass gate 有实际证据；未通过则停止并修订 spec/rubric/evidence source。
517|
518|### MILESTONE-005: Separately approved production cutover
519|
520|Maps to: REQ-040~044, REQ-053~058, AC-005, AC-007~008
521|
522|Outcome:
523|- 单独 plan 选择版本表（优先候选）或有理由的 legacy ALTER，并经确认后才允许进入 production cache/pipeline。
524|
525|Verification signal:
526|- 备份、migration、全量测试、受控 smoke 和回滚演练均通过。
527|
528|## 13. Validation contract
529|
530|### 13.1 本次 spec 验证
531|
532|```bash
533|cd /home/lin/a-stock-tracker
534|git diff --check
535|git diff -- docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md
536|```
537|
538|应证明：
539|
540|1. 相对本次任务开始状态，仅目标 Spec 改变；保留所有既有 dirty worktree 内容。
541|2. REQ 与 AC 编号完整、唯一且可追踪。
542|3. 明确“Outlines 思想、Gemini 原生 Schema、不新增依赖”的架构决策。
543|4. 明确 nullable score、taxonomy、“缺证据 fail closed”、fixture/shadow/cutover 边界。
544|5. 无 trailing whitespace，且 implementation、生产 DB、real shadow、cutover approvals 仍 pending。
545|
546|### 13.2 未来实现最低验证
547|
548|实施计划至少应包含：
549|
550|```bash
551|.venv/bin/python -m pytest tests/test_gemini_scorer.py -q
552|.venv/bin/python -m pytest -q
553|.venv/bin/python -m ruff check gemini_scorer.py lib/cache.py tests/test_gemini_scorer.py
554|.venv/bin/python -m ruff format --check gemini_scorer.py lib/cache.py tests/test_gemini_scorer.py
555|git diff --check
556|```
557|
558|若项目当时已有类型检查基线，再加入项目实际使用的 type checker；不得为满足本 spec 临时引入新工具。
559|
560|### 13.3 必须报告的证据
561|
562|- Changed files
563|- 测试、lint、format 命令与退出码
564|- Mock fixture 的正反例结果
565|- Shadow 调用次数、模型、schema/rubric version
566|- Schema validity、evidence validity、rubric agreement
567|- 未满足条件、数据缺口和 deferred 项
568|- 生产 DB、cron、Telegram、weights、历史 predictions 是否保持未触碰
569|
570|## 14. Rollback and stop conditions
571|
572|### Rollback
573|
574|Spec-only 阶段只允许人工撤销本 Spec 的本次 patch，不得触碰其他 dirty files。未来代码阶段必须在 implementation plan 中给出精确的代码 revert 和 DB 备份恢复/readback 命令；不得复用未验证的通用命令。
575|
576|### Stop if
577|
578|- 官方 API 当前字段与 spec 假设不一致。
579|- Gemini 2.5 Flash 不再支持目标 structured output 能力。
580|- 需要新增依赖但尚未获批。
581|- Fixture 无法表达 taxonomy 合同，或已批准真实 evidence source/dataset 无法满足 real-shadow 最低证据门槛；后者不阻塞 fixture-first。
582|- 模型生成未知 evidence ID 或输入外事实，且修复后仍复现。
583|- Shadow gate 未通过。
584|- 真实 DB 发生未批准写入。
585|- 发现会改写历史 predictions、weights_hash 或 outcome。
586|- 全量测试出现不能归因于本范围的失败。
587|- 工作区无关改动会被覆盖、格式化或误提交。
588|
589|## 15. 风险与开放问题
590|
591|### 已决策
592|
593|1. 当前不引入 Outlines。
594|2. 当前不迁移 Gemini Interactions API。
595|3. 使用原生 JSON Schema + 本地语义校验双层边界。
596|4. 数据不足必须显式暴露，不允许模型猜分。
597|5. 新方法先 shadow，不直接进入生产。
598|6. `score` 保持必填 `integer | null`，实施时以当前官方文档重查和 smoke 为前置。
599|7. 生产 cache schema mismatch 是 cutover blocker，不是 fixture-first 或文件 shadow blocker。
600|
601|### 待用户/Reviewer确认
602|
603|1. **真实 evidence source**：直接竞争优势、行业地位和 sentiment 后续使用可审计 provider，还是批准带 provenance 的静态 dataset？Fixture-first 不需要此决定。
604|2. **sentiment 数据边界**：项目路线图当前不建设独立舆情层；real shadow 是否批准静态 evidence dataset，或继续让真实 sentiment unavailable？
605|3. **Shadow 存储**：先用 JSONL/Markdown artifact，还是经确认新增独立 SQLite evaluation 表？
606|4. **真实 shadow 计划**：6 只股票 × 单次调用是否只作 provisional 首轮，以及升级 gate 的每维度样本数、人工复核比例和行业覆盖是多少？
607|5. **生产 cache 策略**：未来 cutover plan 是否采用优先候选的新版本表，还是提出充分理由 ALTER legacy 表？
608|6. **生产适用范围**：若 sentiment 长期 unavailable，是否继续 all-or-nothing，还是另写 spec 将其移出 LLM 职责？本 spec 不允许静默放宽。
609|
610|## 16. Approval state
611|
612|- Spec approval: pending
613|- Implementation approval: pending
614|- Production DB schema approval: pending
615|- Real Gemini shadow approval: pending
616|- Production cutover approval: pending
617|
618|本 spec 是开发契约草案，不构成任何生产、数据库、付费调用、cron、Telegram 或权重修改授权。
```

## FILE: gemini_scorer.py

```text
1|"""
2|Gemini 定性评分模块。
3|对 moat / market_pos / sentiment 三个字段调用 Gemini 2.5 Flash，结果缓存 30 天。
4|任何字段缺失/越界/超时/非JSON → all-or-nothing fallback 到 phase1_fixed 值。
5|"""
6|import json
7|import logging
8|import os
9|import time
10|import urllib.request
11|import urllib.error
12|from datetime import date, timedelta
13|
14|from lib.cache import get_db
15|
16|logger = logging.getLogger(__name__)
17|
18|# phase1_fixed 兜底值（all-or-nothing fallback 时使用）
19|FALLBACK = {"moat": 5, "market_pos": 2, "sentiment": 3}
20|
21|# 每个字段的合法整数范围（与 weights.json max_score 保持一致）
22|VALID_RANGES = {"moat": (1, 10), "market_pos": (1, 5), "sentiment": (1, 5)}
23|
24|CACHE_TTL_DAYS = 30
25|GEMINI_TIMEOUT_S = 10
26|GEMINI_MODEL = "gemini-2.5-flash"
27|GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
28|MAX_GEMINI_RETRIES: int = 3
29|GEMINI_RETRY_DELAYS: tuple[int, ...] = (2, 4)
30|
31|
32|def _check_cache(code: str) -> dict | None:
33|    """查询 qualitative_scores 缓存，取最新一条，30天内有效则返回，否则 None。"""
34|    db = get_db()
35|    row = db.execute(
36|        "SELECT moat, market_pos, sentiment, scored_date FROM qualitative_scores"
37|        " WHERE code=? ORDER BY scored_date DESC LIMIT 1",
38|        (code,),
39|    ).fetchone()
40|    db.close()
41|    if row is None:
42|        return None
43|    moat, market_pos, sentiment, scored_date = row
44|    if date.today() - date.fromisoformat(scored_date) > timedelta(days=CACHE_TTL_DAYS):
45|        return None
46|    return {"moat": moat, "market_pos": market_pos, "sentiment": sentiment}
47|
48|
49|def _check_cache_stale(code: str) -> dict | None:
50|    """返回最新缓存值，无论是否过期。缓存不存在时返回 None。"""
51|    db = get_db()
52|    row = db.execute(
53|        "SELECT moat, market_pos, sentiment FROM qualitative_scores"
54|        " WHERE code=? ORDER BY scored_date DESC LIMIT 1",
55|        (code,),
56|    ).fetchone()
57|    db.close()
58|    if row is None:
59|        return None
60|    moat, market_pos, sentiment = row
61|    return {"moat": moat, "market_pos": market_pos, "sentiment": sentiment}
62|
63|
64|def _write_cache(code: str, scores: dict) -> None:
65|    db = get_db()
66|    db.execute(
67|        """INSERT OR IGNORE INTO qualitative_scores
68|           (code, moat, market_pos, sentiment, scored_date) VALUES (?,?,?,?,?)""",
69|        (code, scores["moat"], scores["market_pos"], scores["sentiment"], date.today().isoformat()),
70|    )
71|    db.commit()
72|    db.close()
73|
74|
75|def _validate(raw: dict) -> dict | None:
76|    """验证 Gemini 返回的三个字段。任一不合法返回 None（触发 all-or-nothing fallback）。"""
77|    for field, (lo, hi) in VALID_RANGES.items():
78|        val = raw.get(field)
79|        if not isinstance(val, int) or not (lo <= val <= hi):
80|            logger.warning(f"Gemini 字段 {field}={val!r} 不合法（期望 {lo}-{hi} 整数），触发 fallback")
81|            return None
82|    return {k: raw[k] for k in VALID_RANGES}
83|
84|
85|def _call_gemini(code: str, name: str) -> dict | None:
86|    """调用 Gemini API，返回验证通过的 dict 或 None（失败时）。"""
87|    api_key = os.environ.get("GEMINI_API_KEY", "")
88|    if not api_key:
89|        logger.error("GEMINI_API_KEY 未设置，跳过 Gemini 评分")
90|        return None
91|
92|    prompt = (
93|        f"你是一位 A 股研究员。请对以下股票的三个维度给出整数评分，返回纯 JSON，不要其他内容：\n"
94|        f"股票：{name}（{code}）\n"
95|        f"- moat（护城河，1-10整数）\n"
96|        f"- market_pos（市场地位，1-5整数）\n"
97|        f"- sentiment（市场情绪/近期消息面，1-5整数）\n"
98|        f"只返回 JSON，格式：{{\"moat\": 7, \"market_pos\": 4, \"sentiment\": 3}}"
99|    )
100|
101|    payload = json.dumps({
102|        "contents": [{"parts": [{"text": prompt}]}],
103|        "generationConfig": {"temperature": 1, "maxOutputTokens": 256, "thinkingConfig": {"thinkingBudget": 0}},
104|    }).encode("utf-8")
105|
106|    url = GEMINI_API_URL.format(model=GEMINI_MODEL, key=api_key)
107|    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
108|
109|    for attempt in range(MAX_GEMINI_RETRIES):
110|        try:
111|            with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT_S) as resp:
112|                body = json.loads(resp.read().decode("utf-8"))
113|            text = body["candidates"][0]["content"]["parts"][0]["text"].strip()
114|            # 去掉可能的 markdown 代码块包装
115|            if text.startswith("```"):
116|                text = text.split("```")[1]
117|                if text.startswith("json"):
118|                    text = text[4:]
119|            raw = json.loads(text)
120|            return _validate(raw)
121|        except TimeoutError:
122|            logger.warning(f"{code} Gemini 超时（>{GEMINI_TIMEOUT_S}s），使用 fallback")
123|            return None
124|        except urllib.error.HTTPError as e:
125|            if e.code in (401, 403):
126|                logger.error(f"{code} Gemini 鉴权失败（{e.code}），请更新 .env GEMINI_API_KEY")
127|                return None
128|            retryable = e.code == 429 or e.code >= 500
129|            delay = GEMINI_RETRY_DELAYS[min(attempt, len(GEMINI_RETRY_DELAYS) - 1)]
130|            if retryable and attempt < MAX_GEMINI_RETRIES - 1:
131|                logger.warning(f"{code} Gemini HTTP {e.code}，{delay}s 后重试（{attempt + 1}/{MAX_GEMINI_RETRIES}）")
132|                time.sleep(delay)
133|            else:
134|                logger.warning(f"{code} Gemini HTTP 错误 {e.code}，触发 fallback")
135|                return None
136|        except (json.JSONDecodeError, KeyError, IndexError) as e:
137|            logger.warning(f"{code} Gemini 响应解析失败：{e}，触发 fallback")
138|            return None
139|        except Exception as e:
140|            logger.warning(f"{code} Gemini 调用异常：{e}，触发 fallback")
141|            return None
142|    return None
143|
144|
145|def get_qualitative_score(code: str, name: str) -> dict:
146|    """
147|    获取定性评分。优先读 30 天缓存，缓存过期则调 Gemini API。
148|    任何失败均 fallback 到 phase1_fixed 值，保证始终返回合法 dict。
149|    返回格式：{"moat": int, "market_pos": int, "sentiment": int}
150|    """
151|    cached = _check_cache(code)
152|    if cached is not None:
153|        logger.debug(f"{code} 定性评分缓存命中：{cached}")
154|        return cached
155|
156|    result = _call_gemini(code, name)
157|    if result is not None:
158|        _write_cache(code, result)
159|        logger.info(f"{code} 定性评分 Gemini：{result}")
160|        return result
161|
162|    stale = _check_cache_stale(code)
163|    if stale is not None:
164|        logger.info(f"{code} Gemini失败，使用过期缓存：{stale}")
165|        return stale
166|
167|    logger.info(f"{code} 定性评分使用 fallback：{FALLBACK}")
168|    return dict(FALLBACK)
```

## FILE: lib/cache.py

```text
150|    )""")
151|    conn.execute("""CREATE INDEX IF NOT EXISTS idx_market_data_audit_run_purpose
152|        ON market_data_audit(run_date, purpose, status)""")
153|    conn.execute("""CREATE TABLE IF NOT EXISTS index_prices (
154|        symbol  TEXT NOT NULL,
155|        date    TEXT NOT NULL,
156|        close   REAL NOT NULL,
157|        PRIMARY KEY (symbol, date)
158|    )""")
159|    conn.execute("""CREATE TABLE IF NOT EXISTS qualitative_scores (
160|        code        TEXT NOT NULL,
161|        moat        INTEGER NOT NULL,
162|        market_pos  INTEGER NOT NULL,
163|        sentiment   INTEGER NOT NULL,
164|        scored_date TEXT NOT NULL,
165|        PRIMARY KEY (code, scored_date)
166|    )""")
167|
168|    # 兼容旧库：qualitative_scores 曾经用 code 单列主键，无法支持漂移检测。
169|    pk_cols = conn.execute(
170|        "SELECT COUNT(*) FROM pragma_table_info('qualitative_scores') WHERE pk > 0"
171|    ).fetchone()[0]
172|    if pk_cols == 1:
173|        conn.execute("DROP TABLE qualitative_scores")
174|        conn.execute("""CREATE TABLE qualitative_scores (
175|            code        TEXT NOT NULL,
176|            moat        INTEGER NOT NULL,
177|            market_pos  INTEGER NOT NULL,
178|            sentiment   INTEGER NOT NULL,
179|            scored_date TEXT NOT NULL,
180|            PRIMARY KEY (code, scored_date)
181|        )""")
182|
183|    conn.execute("""CREATE TABLE IF NOT EXISTS phase_milestones (
184|        phase        TEXT    NOT NULL,
185|        milestone    INTEGER NOT NULL,
186|        notified_at  TEXT    NOT NULL,
187|        PRIMARY KEY (phase, milestone)
188|    )""")
189|    conn.commit()
190|    return conn
191|
192|
193|def upsert_daily_bars(
194|    conn: sqlite3.Connection,
```

## FILE: pipeline.py

```text
425|            skipped: list[str] = []
426|            written = 0
427|
428|            for item in config.WATCHLIST:
429|                code = item["code"]
430|                name = item["name"]
431|                fundamentals = get_fundamentals(code)
432|                if not fundamentals:
433|                    logger.warning(f"  跳过 {code}：无基本面缓存（请先运行 init）")
434|                    skipped.append(code)
435|                    continue
436|
437|                placeholders = ",".join("?" * len(SUPPORTED_FRAMEWORKS))
438|                written_today = db.execute(
439|                    f"SELECT COUNT(DISTINCT framework) FROM predictions WHERE code=? AND score_date=? AND weights_hash=? AND framework IN ({placeholders})",
440|                    (code, today, weights_hash, *sorted(SUPPORTED_FRAMEWORKS)),
441|                ).fetchone()[0]
442|                if written_today == len(SUPPORTED_FRAMEWORKS):
443|                    logger.info(f"  检查点跳过 {code}：今日 {written_today}/{len(SUPPORTED_FRAMEWORKS)} 框架已完整写入")
444|                    continue
445|
446|                data = dict(fundamentals.get("data", fundamentals))
447|                report_period = data.get("report_period")
448|                price_at_score = score_prices.get(code)
449|
450|                # 注入日度实时 PB 分位（股价变化→分位变化→评分每日变化）
451|                if price_at_score:
452|                    daily_pct = _compute_daily_pb_percentile(price_at_score, data)
453|                    if daily_pct is not None:
454|                        data["pb_percentile_10y"] = daily_pct
455|                        logger.debug(f"  {code} 实时PB分位={daily_pct}%（价={price_at_score}, bps={data.get('bps')}）")
456|                    else:
457|                        logger.debug(f"  {code} 无法计算实时PB分位（bps/hist缺失），使用缓存值")
458|
459|                # 注入 Gemini 定性评分（覆盖 phase1_fixed，失败自动 fallback）
460|                qual = get_qualitative_score(code, name)
461|                data["moat_fixed"] = qual["moat"]
462|                data["market_pos_fixed"] = qual["market_pos"]
463|                data["sentiment_fixed"] = qual["sentiment"]
464|
465|                threshold_adjusted = 0
466|                entry_signal_result = _compute_stock_entry_signal(db, code, today)
467|                l3_v2_result = compute_l3_v2_from_daily_bars(db, code, today)
468|                l3_v2_fetched_at = _l3v2_now()
469|
470|                stock_written = 0
471|                savepoint_created = False
472|                try:
473|                    db.execute("SAVEPOINT sp_stock")
474|                    savepoint_created = True
475|                    for framework in sorted(SUPPORTED_FRAMEWORKS):
476|                        try:
477|                            result = score_stock(code, framework, data, weights=weights)
478|                        except InsufficientDataError as e:
479|                            logger.warning(f"  跳过 {code}/{framework}：{e}")
480|                            skipped.append(f"{code}/{framework}")
481|                            continue
482|                        except UnsupportedFrameworkError as e:
483|                            logger.error(f"  错误 {code}/{framework}：{e}")
484|                            skipped.append(f"{code}/{framework}")
485|                            continue
486|
487|                        cursor = db.execute(
488|                            """INSERT OR IGNORE INTO predictions
489|                               (code, name, framework, score_date, price_at_score,
490|                                quant_score, total_score, weights_hash, report_period,
491|                                threshold_adjusted, entry_signal, entry_signal_version,
492|                                entry_signal_status, entry_signal_reason, entry_signal_source,
493|                                entry_signal_fetched_at,
494|                                l3_v2_signal, l3_v2_version, l3_v2_status, l3_v2_reason,
495|                                l3_v2_fetched_at, created_at)
496|                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
497|                            (
498|                                code, name, framework, today, price_at_score,
499|                                result["quant_score"], result["total_score"],
500|                                weights_hash, report_period,
501|                                threshold_adjusted, entry_signal_result.signal,
502|                                entry_signal_result.version, entry_signal_result.status,
503|                                entry_signal_result.reason, entry_signal_result.source,
504|                                entry_signal_result.fetched_at,
505|                                l3_v2_result.signal, l3_v2_result.version,
506|                                l3_v2_result.status, l3_v2_result.reason,
507|                                l3_v2_fetched_at, datetime.now().isoformat(),
508|                            ),
509|                        )
510|                        if cursor.rowcount == 0:
511|                            db.execute(
512|                                """UPDATE predictions
513|                                   SET entry_signal=?,
514|                                       entry_signal_version=?,
515|                                       entry_signal_status=?,
516|                                       entry_signal_reason=?,
517|                                       entry_signal_source=?,
518|                                       entry_signal_fetched_at=?,
519|                                       l3_v2_signal=?,
520|                                       l3_v2_version=?,
521|                                       l3_v2_status=?,
522|                                       l3_v2_reason=?,
523|                                       l3_v2_fetched_at=?
524|                                   WHERE code=? AND framework=? AND score_date=?""",
```

## FILE: tests/test_gemini_scorer.py

```text
1|"""tests/test_gemini_scorer.py — gemini_scorer 的 5 个单元测试。
2|
3|所有测试 mock _call_gemini 或 _check_cache/_write_cache，不发起真实网络请求。
4|"""
5|import json
6|from unittest.mock import patch
7|
8|
9|# ---------------------------------------------------------------------------
10|# 1. 正常路径：Gemini 返回合法 JSON，写入缓存并返回
11|# ---------------------------------------------------------------------------
12|def test_normal_path():
13|    """Gemini 正常返回，应写缓存并返回解析后的 dict。"""
14|    import gemini_scorer
15|
16|    with patch.object(gemini_scorer, "_check_cache", return_value=None), \
17|         patch.object(gemini_scorer, "_call_gemini",
18|                      return_value={"moat": 7, "market_pos": 4, "sentiment": 3}), \
19|         patch.object(gemini_scorer, "_write_cache") as mock_write:
20|        result = gemini_scorer.get_qualitative_score("600036", "招商银行")
21|
22|    assert result == {"moat": 7, "market_pos": 4, "sentiment": 3}
23|    mock_write.assert_called_once_with("600036", {"moat": 7, "market_pos": 4, "sentiment": 3})
24|
25|
26|# ---------------------------------------------------------------------------
27|# 2. 超时：_call_gemini 返回 None，触发 all-or-nothing fallback
28|# ---------------------------------------------------------------------------
29|def test_timeout_fallback():
30|    """_call_gemini 返回 None（超时场景），应返回完整 FALLBACK 值且不写缓存。"""
31|    import gemini_scorer
32|
33|    with patch.object(gemini_scorer, "_check_cache", return_value=None), \
34|         patch.object(gemini_scorer, "_call_gemini", return_value=None), \
35|         patch.object(gemini_scorer, "_write_cache") as mock_write:
36|        result = gemini_scorer.get_qualitative_score("600519", "贵州茅台")
37|
38|    assert result == {"moat": 5, "market_pos": 2, "sentiment": 3}
39|    mock_write.assert_not_called()
40|
41|
42|# ---------------------------------------------------------------------------
43|# 3. 非 JSON 响应：urlopen 返回非 JSON 文本，_call_gemini 应捕获并返回 None
44|# ---------------------------------------------------------------------------
45|def test_invalid_json_response(monkeypatch):
46|    """Gemini API 返回非 JSON（如纯文本），_call_gemini 应捕获 JSONDecodeError 并 fallback。"""
47|    import gemini_scorer
48|
49|    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
50|
51|    fake_body = json.dumps({
52|        "candidates": [{"content": {"parts": [{"text": "对不起，我无法完成该请求。"}]}}]
53|    }).encode()
54|
55|    with patch("urllib.request.urlopen") as mock_urlopen, \
56|         patch.object(gemini_scorer, "_check_cache", return_value=None), \
57|         patch.object(gemini_scorer, "_write_cache") as mock_write:
58|        mock_urlopen.return_value.__enter__ = lambda s: s
59|        mock_urlopen.return_value.__exit__ = lambda s, *a: False
60|        mock_urlopen.return_value.read.return_value = fake_body
61|
62|        result = gemini_scorer.get_qualitative_score("000858", "五粮液")
63|
64|    assert result == gemini_scorer.FALLBACK
65|    mock_write.assert_not_called()
66|
67|
68|# ---------------------------------------------------------------------------
69|# 4. 值越界：_validate 应拒绝越界值
70|# ---------------------------------------------------------------------------
71|def test_validate_rejects_out_of_range():
72|    """_validate 对越界值应返回 None（触发 all-or-nothing fallback）。"""
73|    import gemini_scorer
74|
75|    # moat 超过 10
76|    assert gemini_scorer._validate({"moat": 11, "market_pos": 4, "sentiment": 3}) is None
77|    # market_pos 超过 5
78|    assert gemini_scorer._validate({"moat": 7, "market_pos": 8, "sentiment": 3}) is None
79|    # sentiment 为 0（低于下限 1）
80|    assert gemini_scorer._validate({"moat": 7, "market_pos": 4, "sentiment": 0}) is None
81|    # 非整数类型
82|    assert gemini_scorer._validate({"moat": 7.5, "market_pos": 4, "sentiment": 3}) is None
83|    # 合法值应通过
84|    assert gemini_scorer._validate({"moat": 7, "market_pos": 4, "sentiment": 3}) == \
85|           {"moat": 7, "market_pos": 4, "sentiment": 3}
86|
87|
88|# ---------------------------------------------------------------------------
89|# 5. 缓存命中：30 天内有记录时，不调用 Gemini API
90|# ---------------------------------------------------------------------------
91|def test_cache_hit_skips_api():
92|    """qualitative_scores 有 30 天内记录时，_call_gemini 不应被调用。"""
93|    import gemini_scorer
94|
95|    cached = {"moat": 6, "market_pos": 3, "sentiment": 4}
96|    with patch.object(gemini_scorer, "_check_cache", return_value=cached), \
97|         patch.object(gemini_scorer, "_call_gemini") as mock_api:
98|        result = gemini_scorer.get_qualitative_score("601318", "中国平安")
99|
100|    assert result == cached
101|    mock_api.assert_not_called()
102|
103|
104|# ---------------------------------------------------------------------------
105|# 6. Gemini 返回 markdown 包装的 JSON（如 ```json{...}```）应被正确剥离解析
106|# ---------------------------------------------------------------------------
107|def test_markdown_code_block_stripped(monkeypatch):
108|    """Gemini 偶尔以 ```json\n{...}\n``` 包装返回值，_call_gemini 应剥离 markdown 后正常解析。"""
109|    import gemini_scorer
110|
111|    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
112|
113|    inner = json.dumps({"moat": 8, "market_pos": 4, "sentiment": 4})
114|    wrapped = f"```json\n{inner}\n```"
115|    fake_body = json.dumps({
116|        "candidates": [{"content": {"parts": [{"text": wrapped}]}}]
117|    }).encode()
118|
119|    with patch("urllib.request.urlopen") as mock_urlopen:
120|        mock_urlopen.return_value.__enter__ = lambda s: s
121|        mock_urlopen.return_value.__exit__ = lambda s, *a: False
122|        mock_urlopen.return_value.read.return_value = fake_body
123|
124|        result = gemini_scorer._call_gemini("600036", "招商银行")
125|
126|    assert result == {"moat": 8, "market_pos": 4, "sentiment": 4}
127|
128|
129|# ---------------------------------------------------------------------------
130|# 7. _check_cache 多行时返回最新记录
131|# ---------------------------------------------------------------------------
132|def test_check_cache_multiple_rows_returns_latest(tmp_path, monkeypatch):
133|    """qualitative_scores 同一 code 有多行时，应返回 scored_date 最新的记录。"""
134|    import gemini_scorer
135|    from lib import cache as cache_mod
136|    from datetime import date, timedelta
137|
138|    db_path = str(tmp_path / "tracker.db")
139|    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
140|
141|    db = cache_mod.get_db()
142|    old_date = (date.today() - timedelta(days=25)).isoformat()
143|    new_date = (date.today() - timedelta(days=1)).isoformat()
144|    db.execute(
145|        "INSERT INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date)"
146|        " VALUES ('600036', 5, 2, 3, ?)", (old_date,)
147|    )
148|    db.execute(
149|        "INSERT INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date)"
150|        " VALUES ('600036', 8, 4, 4, ?)", (new_date,)
151|    )
152|    db.commit()
153|    db.close()
154|
155|    result = gemini_scorer._check_cache("600036")
156|    assert result is not None
157|    assert result["moat"] == 8  # 最新行（new_date）
158|
159|
160|# ---------------------------------------------------------------------------
161|# 8. _write_cache INSERT OR IGNORE：同日重复写入不覆盖原有记录
162|# ---------------------------------------------------------------------------
163|def test_write_cache_insert_ignore_same_date(tmp_path, monkeypatch):
164|    """同 code + scored_date 写入两次，INSERT OR IGNORE 应保留第一次的值。"""
165|    from lib import cache as cache_mod
166|
167|    db_path = str(tmp_path / "tracker.db")
168|    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
169|    cache_mod.get_db().close()  # 建表
170|
171|    # 直接操作 DB 写入两条相同 (code, scored_date) 记录，验证第二条被 IGNORE
172|    db = cache_mod.get_db()
173|    db.execute(
174|        "INSERT OR IGNORE INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date)"
175|        " VALUES ('600036', 7, 3, 4, '2026-05-12')"
176|    )
177|    db.execute(
178|        "INSERT OR IGNORE INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date)"
179|        " VALUES ('600036', 9, 5, 5, '2026-05-12')"  # 同日，应被 IGNORE
180|    )
181|    db.commit()
182|
183|    rows = db.execute(
184|        "SELECT moat FROM qualitative_scores WHERE code='600036'"
185|    ).fetchall()
186|    db.close()
187|    assert len(rows) == 1
188|    assert rows[0][0] == 7  # 保留第一次写入的值
189|
190|
191|# ---------------------------------------------------------------------------
192|# 9. _check_cache 过期测试
193|# ---------------------------------------------------------------------------
194|def test_check_cache_expired(tmp_path, monkeypatch):
195|    """超过 30 天的缓存应返回 None。"""
196|    import gemini_scorer
197|    from lib import cache as cache_mod
198|    from datetime import date, timedelta
199|
200|    db_path = str(tmp_path / "tracker.db")
201|    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
202|
203|    db = cache_mod.get_db()
204|    old_date = (date.today() - timedelta(days=31)).isoformat()
205|    db.execute(
206|        "INSERT INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date)"
207|        " VALUES ('600036', 5, 2, 3, ?)", (old_date,)
208|    )
209|    db.commit()
210|    db.close()
211|
212|    assert gemini_scorer._check_cache("600036") is None
213|
214|
215|# ---------------------------------------------------------------------------
216|# 10. _check_cache_stale 测试
217|# ---------------------------------------------------------------------------
218|def test_check_cache_stale(tmp_path, monkeypatch):
219|    """无论是否过期，_check_cache_stale 都应返回最新值，不存在时返回 None。"""
220|    import gemini_scorer
221|    from lib import cache as cache_mod
222|    from datetime import date, timedelta
223|
224|    db_path = str(tmp_path / "tracker.db")
225|    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
226|
227|    assert gemini_scorer._check_cache_stale("000001") is None
228|
229|    db = cache_mod.get_db()
230|    old_date = (date.today() - timedelta(days=50)).isoformat()
231|    db.execute(
232|        "INSERT INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date)"
233|        " VALUES ('000001', 9, 4, 4, ?)", (old_date,)
234|    )
235|    db.commit()
236|    db.close()
237|
238|    result = gemini_scorer._check_cache_stale("000001")
239|    assert result == {"moat": 9, "market_pos": 4, "sentiment": 4}
240|
241|
242|# ---------------------------------------------------------------------------
243|# 11. _call_gemini API Key 缺失
244|# ---------------------------------------------------------------------------
245|def test_call_gemini_empty_api_key(monkeypatch):
246|    monkeypatch.setenv("GEMINI_API_KEY", "")
247|    import gemini_scorer
248|    assert gemini_scorer._call_gemini("000001", "平安银行") is None
249|
250|
251|# ---------------------------------------------------------------------------
252|# 12. _call_gemini HTTP Errors (Retries & Auth)
253|# ---------------------------------------------------------------------------
254|def test_call_gemini_http_errors(monkeypatch):
255|    import gemini_scorer
256|    import urllib.error
257|    monkeypatch.setenv("GEMINI_API_KEY", "test")
258|    monkeypatch.setattr(gemini_scorer, "GEMINI_RETRY_DELAYS", (0, 0)) # Speed up
259|
260|    # 401 Auth error -> returns None immediately
261|    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 401, "Auth", {}, None)) as mock_auth:
262|        assert gemini_scorer._call_gemini("000001", "Bank") is None
263|        assert mock_auth.call_count == 1
264|
265|    # 500 Server error -> retries then returns None
266|    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 500, "Error", {}, None)) as mock_server_err:
267|        assert gemini_scorer._call_gemini("000001", "Bank") is None
268|        assert mock_server_err.call_count == gemini_scorer.MAX_GEMINI_RETRIES
269|
270|    # 400 Bad Request -> No retry, returns None
271|    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 400, "Bad Req", {}, None)) as mock_bad_req:
272|        assert gemini_scorer._call_gemini("000001", "Bank") is None
273|        assert mock_bad_req.call_count == 1
274|
275|
276|# ---------------------------------------------------------------------------
277|# 13. _call_gemini Generic Exception
278|# ---------------------------------------------------------------------------
279|def test_call_gemini_generic_exception(monkeypatch):
280|    import gemini_scorer
281|    monkeypatch.setenv("GEMINI_API_KEY", "test")
282|    with patch("urllib.request.urlopen", side_effect=Exception("Unknown Error")):
283|        assert gemini_scorer._call_gemini("000001", "Bank") is None
284|
285|
286|# ---------------------------------------------------------------------------
287|# 14. get_qualitative_score 兜底旧缓存
288|# ---------------------------------------------------------------------------
289|def test_get_qualitative_score_stale_fallback():
290|    import gemini_scorer
291|    stale_data = {"moat": 8, "market_pos": 3, "sentiment": 2}
292|    with patch.object(gemini_scorer, "_check_cache", return_value=None), \
293|         patch.object(gemini_scorer, "_call_gemini", return_value=None), \
294|         patch.object(gemini_scorer, "_check_cache_stale", return_value=stale_data):
295|
296|        result = gemini_scorer.get_qualitative_score("000001", "Bank")
297|        assert result == stale_data
298|
299|
300|# ---------------------------------------------------------------------------
301|# 15. _check_cache 空结果
302|# ---------------------------------------------------------------------------
303|def test_check_cache_empty(tmp_path, monkeypatch):
304|    import gemini_scorer
305|    from lib import cache as cache_mod
306|    db_path = str(tmp_path / "tracker.db")
307|    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
308|    assert gemini_scorer._check_cache("999999") is None
309|
310|
311|# ---------------------------------------------------------------------------
312|# 16. _write_cache 执行写入
313|# ---------------------------------------------------------------------------
314|def test_write_cache_execution(tmp_path, monkeypatch):
315|    import gemini_scorer
316|    from lib import cache as cache_mod
317|    db_path = str(tmp_path / "tracker.db")
318|    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
319|
320|    gemini_scorer._write_cache("000001", {"moat": 8, "market_pos": 4, "sentiment": 3})
321|
322|    db = cache_mod.get_db()
323|    row = db.execute("SELECT moat, market_pos, sentiment FROM qualitative_scores WHERE code='000001'").fetchone()
324|    db.close()
325|    assert row == (8, 4, 3)
326|
327|
328|# ---------------------------------------------------------------------------
329|# 17. _call_gemini TimeoutError
330|# ---------------------------------------------------------------------------
331|def test_call_gemini_timeout(monkeypatch):
332|    import gemini_scorer
333|    monkeypatch.setenv("GEMINI_API_KEY", "test")
334|    with patch("urllib.request.urlopen", side_effect=TimeoutError):
335|        assert gemini_scorer._call_gemini("000001", "Bank") is None
```

## FILE: CLAUDE.md

```text
1|# CLAUDE.md — a-stock-tracker
2|
3|## 项目简介
4|A 股自动评分管道。每日盘后对 watchlist 运行 Framework A 定量评分（SQLite），
5|追踪 30/60/90 天收益率及 benchmark 相对 alpha。
6|
7|**文档指针**（详细逻辑优先读这里，不要靠 CLAUDE.md）：
8|- 架构 / 数据模型 → `docs/design.md`
9|- 已知陷阱 / 历史修复 → `docs/lessons-learned.md`
10|- 演进路线图 → `docs/evolution-roadmap.md`
11|- 测试计划 → `docs/test-plan.md`
12|
13|---
14|
15|## 路径与运行
16|
17|```bash
18|cd ~/a-stock-tracker          # ← 项目在 home 根，不在 ~/code/project/work/
19|source .venv/bin/activate
20|
21|python pipeline.py init           # 首次初始化（预填 watchlist 数据）
22|python pipeline.py daily          # 每日手动触发（cron: 工作日 16:30）
23|python pipeline.py outcome-update # 更新到期预测（cron: 工作日 17:00）
24|python pipeline.py accuracy-report
25|
26|pytest tests/ -v                  # 修改前必须全通过
27|```
28|
29|---
30|
31|## 文件结构
32|
33|| 文件 | 职责 |
34||------|------|
35|| `pipeline.py` | 主编排器（init / daily / outcome-update / accuracy-report）|
36|| `scorer.py` | 评分引擎（breakpoints 线性插值，不调 AKShare）|
37|| `gemini_scorer.py` | Phase 3：Gemini 定性评分（30天缓存，退避重试，过期缓存降级，all-or-nothing fallback）|
38|| `telegram_push.py` | Phase 3：每日信号推送（≥44 分 AND L3=1 触发）|
39|| `weights.json` | 模型权重（阈值 buy_strong=44/moderate=35/light=26）|
40|| `config.py` | watchlist / DB_PATH / LOG_DIR（禁止硬编码股票代码或路径）|
41|| `.env` | GEMINI_API_KEY / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID |
42|| `lib/fetcher.py` | AKShare 封装，仅用于基本面/估值/财报缓存（来自 a-stock-research skill，独立演进）|
43|| `lib/market_data.py` | 行情数据 provider 兼容入口；协议原语和 Tushare/BaoStock 实现来自 `a-stock-lib==0.2.0`，AKShare/东方财富行情入口已禁用（`SOURCE_DISABLED`） |
44|| `a_stock_lib.providers.tushare_quotes` | 行情主源（需 `TUSHARE_TOKEN`），probe 通过后启用 |
45|| `a_stock_lib.providers.baostock_quotes` | 行情 degraded fallback，仅 Tushare 失败后或显式 backfill 使用 |
46|| `lib/cache.py` | SQLite 管理（predictions / index_prices / qualitative_scores 表）|
47|
48|---
49|
50|## 安全红线（数据完整性）
51|
52|### 禁止行为
53|- **禁止直接写 `alpha_*d` 列** — SQLite Generated Column，写入会报错
54|- **禁止 UPDATE 已有 predictions 的 `total_score` / `weights_hash`** — 破坏实验数据可比性
55|- **禁止修改 lib/cache.py 的 DB_PATH** 使其指向 `~/.claude/skills/a-stock-research/cache.db`
56|- **禁止在 `scorer.py` 对 `invert: true` 字段做额外变换** — breakpoints 已按"高值→低分"排列，插值逻辑统一
57|- **禁止测试中发起真实 AKShare 网络请求** — 所有 AKShare 调用必须 Mock
58|- **禁止让 Sheets sync 失败阻断 daily cron** — SQLite 是真相来源，Sheets 是展示层，失败只记 WARNING
59|
60|### 必须行为
61|- **修改 weights.json 后**：hash 会变，若当日已有记录需手动删除或等次日
62|- **新增字段到 predictions 表**：必须同步更新 `get_db()` DDL 和 INSERT 语句
63|- **新增测试**：用 `tmp_path` fixture 隔离，不使用真实 `tracker.db`
64|- **pipeline 启动**：保留 `assert sqlite3.sqlite_version_info >= (3, 31, 0)`（Generated Column 依赖）
65|- **accuracy-report**：记录 < 100 条时头部必须有样本不足警告 + 选择性偏差免责声明
66|- **新增 watchlist 股票**：必须先 `pipeline.py init`，`cmd_batch()` 不会自动抓新股票
67|
68|---
69|
70|## 关键实现约定
71|
72|**weights_hash**：`md5(json.dumps(weights["frameworks"], sort_keys=True))[:8]`
73|— 只对 `frameworks` 子树求 hash，`updated_at` / `version` 不影响 hash。
74|
75|**outcome 单位**：始终是百分比，如 `+5.2` = 涨 5.2%，不是小数 0.052，不是绝对价差。
76|
77|**pb_percentile_10y**：日度实时计算（current_pb = 当日收盘价 / bps，ranked in pb_hist_monthly），
78|每日随股价变化，纯内存，不触碰 TTL，不需额外 API 调用。
79|
80|**跨期评分比较注意**：2026-05-14 前（gross_margin/pb_percentile 旧算法）vs 2026-05-15 后
81|avg_score 有约 4-5 分系统性偏移，Phase 4 optimizer 训练需按 score_date 分层。详见 `docs/lessons-learned.md`。
82|
83|**行情数据源（2026-06-09 起迁移，与基本面数据源分离）**：AKShare/东方财富**行情**入口已禁用，
84|`lib/market_data.py` 默认 provider 返回 `SOURCE_DISABLED`。当前行情主源是 `a-stock-lib==0.2.0`
85|中的 Tushare provider（需 `TUSHARE_TOKEN`），失败后降级到同包 BaoStock provider（degraded）。`_ensure_index_prices` 走同一套 provider，不再直连
86|新浪/腾讯接口。成组恢复 `daily` / `outcome-update` 前必须 `python3 scripts/check_market_data_readiness.py --scope cron` 返回 `READY_CRON`
87|（最新探测报告见 `docs/reviews/*-tushare-capability-probe.md`）。详见
88|`docs/runbooks/market-data-provider-recovery.md` 和
89|`docs/plans/2026-06-09-market-data-provider-replacement-plan.md`。
90|基本面/估值/财报抓取（`lib/fetcher.py`）仍用 AKShare，未受此次迁移影响。
91|
92|---
93|
94|## 常见陷阱
95|
96|| 陷阱 | 正确做法 |
97||------|---------|
98|| `alpha_30d` 查询为 NULL | 检查 `outcome_30d` 和 `benchmark_30d` 是否都有值（任一 NULL → 结果 NULL）|
99|| 修改 `updated_at` 触发 hash 变更 | weights_hash 只对 `frameworks` 子树求 hash |
100|| 重新运行 daily 新增了行 | 检查 UNIQUE(code, framework, score_date) 约束是否生效 |
101|| Phase 1 strong 信号 < 5 条 | 阈值已于 2026-05-12 永久校准为 44/35/26，`threshold_adjusted` 恒为 0 |
102|
103|---
104|
105|## Phase 状态快照（2026-07-12）
106|
107|| Phase | 状态 | 说明 |
108||-------|------|------|
109|| Phase 3 Gemini/Telegram | ✅ 上线 | 定性评分（30天缓存，退避重试，过期缓存降级）；推送 ≥44 分 AND L3=1 触发 |
110|| Phase 4 验证基础 | ✅ 完成，持续观察 | A框架 30d 结案 883 条；hit_rate 待验证 |
111|| Phase 5 L3 买点层 v1 | ✅ 完成，持续观察 | L3 v1 接入 daily/推送/report；30d 样本不足 |
112|| Phase 5 L3 v2（QFQ）| ✅ Phase 2+3 完成 | Phase 2: QFQ 35/35×130 行回填，pass_strong 激活，cron 16:00；Phase 3: 推送触发切换至 l3_v2_signal=1（commit e080f15） |
113|| Phase 6 多框架激活 | 🔶 report-only | Framework B 仍不写生产；B label 0/20 自然结案 |
114|| Phase 7 选股宇宙 | ⏸ 未启动 | 待 Phase 6 完成或明确降级策略 |
115|
116|optimizer.py 启动门槛：Framework A 30d 结案 ≥ 100（已满足） AND `hit_rate_vs_300 > 55%`（待验证）。
117|Framework B 重启：在 scorer.py 加回 "B"，另写生产化 spec 并经独立审查。
118|
119|---
120|
121|## Skill routing
122|
123|When the user's request matches an available skill, invoke it via the Skill tool.
124|
125|- 投研分析 / 个股研究 → invoke /a-stock-research
126|- 监控 / 持仓追踪 → invoke /a-stock-monitor
127|- 产品方向 / 功能讨论 → invoke /office-hours
128|- 架构 / 工程审查 → invoke /plan-eng-review
129|- Bug / 错误排查 → invoke /investigate
130|- Code review → invoke /review
131|- 部署 / PR → invoke /ship
132|
133|
134|<!-- ai-collab:routing -->
135|## AI协作模式（ai-collab）
136|本项目采用 ai-collab 三方协作模式（Claude=PM/架构师，codex=执行，QA工具=审查）。
137|新会话只要看到本项目有 `.claude/ai-collab/config.yaml`，对多步骤编码任务默认
138|调用 collab-pipeline skill 执行"实现→审查→提交"循环；完成一个完整plan或一批
139|任务后调用 collab-retro skill 复盘。配置与历史记录见 `.claude/ai-collab/`。
140|<!-- /ai-collab:routing -->
```

## FILE: docs/project-status.md

```text
47|## Spec Ledger
48|
49|| Spec / Plan | 状态 | Owner | 下一动作 | Exit criteria |
50||---|---|---|---|---|
51|| `docs/evolution-roadmap.md` | v1.7 当前基线 | Hermes PM | 随 Phase 状态变化更新 | 和真实系统状态一致 |
52|| `docs/plans/2026-06-26-phase6-report-only-next-steps.md` | active | Hermes PM | 继续 P3-B 周度复核，并单独 harden weekly/fetcher timeout | B label review 前 report-only 流程稳定 |
53|| `docs/specs/2026-07-02-weekly-pm-loop-automation-spec.md` | implemented | Hermes PM + agy review | 等首轮自然 cron 摘要；异常先修行情/cron | 每周一自动 Telegram 摘要可用，不重复告警，不越权启用生产化 |
54|| `/home/lin/a-stock-lib/docs/plans/2026-07-01-three-project-next-work-plan.md` | active cross-project plan | Hermes PM | 按 P0/P1/P2 顺序推进共享包、tracker、research 联动事项 | 三项目版本/文档/任务边界一致 |
55|| `docs/runbooks/market-data-provider-recovery.md` | active | Hermes PM | 若 readiness/cron 语义变更则同步 | HOLD/READY 行为与 `cron-setup.sh` 一致 |
56|| `docs/reviews/2026-07-02-tushare-capability-probe.md` | latest readiness evidence | 系统探测 | 新 probe 覆盖旧证据 | 最新交易日 probe PASS |
57|| `accuracy_report.txt` | latest report | pipeline | 每周更新 | Phase 6 仍明确 report-only |
58|| `docs/specs/2026-07-08-l3-v2-entry-signal-spec.md` | draft，已被离线回测验证约束 | Hermes PM + agy review | 等 qfq 覆盖问题解决后再决定是否修订 spec/TDD | 无 qfq 覆盖不得 GO_TDD |
59|| `docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md` | draft，仅 spec，无生产变更 | Hermes PM | 审查 evidence 门槛、shadow gate 与 sentiment 职责；批准前不实施 | spec 获批后再派生 fixture-first implementation plan；真实 Gemini/DB/cutover 分别确认 |
60|| `docs/plans/2026-07-08-l3-v2-offline-backtest-plan.md` | implemented | Hermes PM + agy review | 进入 qfq 获取方案设计，不写生产 DB | 脚本只读 `tracker.db`，独立 review gate 通过 |
61|| `docs/reviews/2026-07-08-l3-v2-backtest-report.md` | latest L3 v2 offline evidence | offline script | qfq 限频解除/缓存方案完成后重跑覆盖报告 | buy_strong qfq issue 为 0，且 v2 pass_strong 有可评估样本 |
62|| `docs/reviews/2026-07-10-l3-v2-backtest-retro.md` | task retrospective | Hermes PM | 后续 qfq 方案前先读 | 防止重复踩 token/限频/去重/qfq volume 问题 |
63|| `docs/specs/phase3-l3-v2-push-trigger-switch.md` | implemented | Hermes PM + agy review | 已上线，下一步观察 l3_v2_signal 推送分布 | Phase 3 全量测试通过（298/298），可选 Phase 3.1 accuracy-report 信号分布 section |
64|| `docs/reviews/2026-07-12-framework-a-inversion-diagnosis.md` | final | Hermes PM + agy review | 等 60d/90d 数据到位后跑延伸评估 | 首批 60d 到期 2026-07-14，结案后可跑五分位 alpha 对比 |
65|
66|## Open Blockers
67|
68|| Blocker | Impact | Unblock condition | ETA |
69||---|---|---|---|
70|| B label 已结案样本不足 `0/20` | 阻止 Framework B 生产化 | 已结案 ≥20 且 overdue=0 | 最早 2026-08-02 后 |
71|| L3 30d 样本不足 `28/30` | 无法判断 L3 信号有效性 | L3 30d 已结案 ≥30 | 等自然结案 |
```

## FILE: docs/reviews/agy-source-grounded-qualitative-spec-20260714-192539/agy_stdout.txt

```text
1|# AGY 独立工程审查
2|
3|## Verdict
4|`REQUEST_CHANGES`
5|
6|---
7|
8|## Blocking findings
9|
10|### P0 级阻塞项（Blocker）
11|
12|1. **Gemini JSON Schema 无法约束 Null 字段类型与输出 (REQ-016 & REQ-017)**
13|   - **Spec 章节 / REQ / AC:** 7.2 / REQ-016, REQ-017 / AC-002
14|   - **Snapshot 对应位置:** [2026-07-14-source-grounded-structured-qualitative-scoring-spec.md](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L186-L187)
15|   - **阻塞原因:** Gemini 2.5 Flash 的原生结构化输出（JSON Schema）仅支持官方 `Type` 枚举的子集（`STRING`, `NUMBER`, `INTEGER`, `BOOLEAN`, `ARRAY`, `OBJECT`），不支持联合类型（如 `["integer", "null"]`）或原生 `null` 类型。如果强行在 Schema 中定义 `score` 为 `integer | null`，或者让模型在 `insufficient_data` 时输出 `"score": null`，会导致 Gemini API 编译 Schema 错误或接口报错。
16|   - **最小修复方向:** 在 JSON Schema 中将 `score` 定义为非必填（从 `required` 属性列表中移除 `score`）。当 `status` 为 `insufficient_data` 时，Prompt 约束模型直接在 JSON 中**省略** `score` 键；应用端 Validator 解析时，如果缺失该字段，则在 Python 对象中默认将其置为 `None`。
17|
18|2. **输入证据缺失与硬性校验规则冲突 (REQ-005, REQ-006, REQ-007 vs REQ-004 & REQ-039)**
19|   - **Spec 章节 / REQ / AC:** 7.1 & 7.5 / REQ-004, REQ-005, REQ-006, REQ-007, REQ-039 / AC-001, AC-005
20|   - **Snapshot 对应位置:** [2026-07-14-source-grounded-structured-qualitative-scoring-spec.md](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L170-L179)
21|   - **阻塞原因:** REQ-005~007 强制要求 moat 必须有竞争优势证据、market_pos 必须有行业地位证据、sentiment 必须有近期新闻舆情证据。然而，REQ-004 表明现有基本面缓存仅能提供 ROE、毛利等纯财务指标，且系统目前并无抓取直接竞争优势和新闻舆情的 fetcher。如果开启 real shadow，根据 REQ-039 的 all-or-nothing 机制，**所有真实股票都将 100% 评估为 `insufficient_data`**。这导致 shadow 阶段无法对真实的模型打分和语义验证逻辑进行有效测试。
22|   - **最小修复方向:** 明确允许在 shadow 阶段通过本地静态文件（如项目内 JSON/Markdown 字典）或 Mock 方式，为 6 只 shadow 股票手动注入所需的非结构化证据；或者在 shadow 阶段放宽证据类型限制，允许纯财务指标临时支持评分。
23|
24|### P1 级阻塞项（Blocker）
25|
26|3. **生产缓存表版本化字段缺失与读取优先级冲突 (REQ-037 & REQ-040)**
27|   - **Spec 章节 / REQ / AC:** 7.5 / REQ-037, REQ-040 / AC-005, AC-007
28|   - **Snapshot 对应位置:** [2026-07-14-source-grounded-structured-qualitative-scoring-spec.md](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L265-L266), [cache.py](file:///home/lin/a-stock-tracker/lib/cache.py#L159-L165)
29|   - **阻塞原因:** REQ-037 规定读取优先级包含“同版本 fresh validated result”，REQ-040 要求报告中新旧版分数可区分。但现有的 SQLite 生产缓存表 `qualitative_scores` 结构仅有 `(code, moat, market_pos, sentiment, scored_date)`，没有 `schema_version` 或 `input_hash`。在 cutover 时，若直接将新分数写入该表，将丢失版本和输入一致性校验信息，导致 pipeline 读出时无法区分版本。
30|   - **最小修复方向:** 确定 cutover 时数据库的变轨方案：是在 cutover 的 implementation plan 中对 `qualitative_scores` 表进行 `ALTER TABLE` 迁移（增加版本列），还是新建一张版本化的缓存表 `qualitative_scores_v2`，彻底弃用旧表。
31|
32|4. **应用端 Validator 校验规则在未定义 Evidence ID 时不可行 (REQ-023 & REQ-026)**
33|   - **Spec 章节 / REQ / AC:** 7.3 / REQ-023, REQ-026 / AC-003
34|   - **Snapshot 对应位置:** [2026-07-14-source-grounded-structured-qualitative-scoring-spec.md](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L229)
35|   - **阻塞原因:** REQ-026 要求 Validator 检查维度证据类型（例如：估值证据不能单独支持 moat/market_pos，过期公告不能支持 sentiment）。但在 Spec 中，除了 REQ-004 列出的基本面字段外，没有定义任何其他证据（如竞争壁垒、市场地位、新闻）的 `evidence_id` 命名规范或分类标签。Validator 无法单凭裸字典的 key 来推断哪些 `evidence_id` 属于“估值证据”或“过期新闻”。
36|   - **最小修复方向:** 为 `evidence_id` 引入命名空间前缀规范（例如 `valuation.*`、`fundamentals.*`、`news.*`、`industry.*`），并在 Spec 中明确 Validator 允许/禁止的命名空间映射表。
37|
38|---
39|
40|## Important notes
41|
42|- **API 错误与网络异常的 shadow 审计记录:** 当 Gemini API 发生 429/5xx、超时或鉴权失败时，虽然系统会触发 production fallback，但 Spec 未定义在 `qualitative_score_evaluations` 表中如何记录这些 API failure。建议在此表的 `validation_status` 中补充 `API_ERROR` 状态，并归档错误栈到 `failure_reason`，以供 shadow 审计。
43|- **“provisional” 临时标记的升级条件:** REQ-047 提到当样本不足时 shadow gate 只能标记为 `provisional`。Spec 应补充如何从 `provisional` 升级到正式通过（例如：需要追加多少个样本测试，或人工复核比例达到多少）。
44|- **Scorer 函数代码物理隔离:** 为了保证 shadow 阶段对 production pipeline 的零侵扰，建议在 [gemini_scorer.py](file:///home/lin/a-stock-tracker/gemini_scorer.py) 中新增一个显式的 `get_qualitative_score_v2(context)` 入口，而不是在原有的 `get_qualitative_score` 函数中做重构或注入适配器，避免因代码逻辑分支引发非预期变更。
45|
46|---
47|
48|## Safety boundary assessment
49|
50|该 Spec **基本保持**了核心安全红线，符合项目约束：
51|- **DB/Predictions/Weights 边界:** REQ-045 与 REQ-051 明确禁止在 shadow 阶段修改 `predictions`、`qualitative_scores` 及 weights，并禁止回写历史数据。
52|- **网络与依赖边界:** REQ-041 保证第一阶段使用纯 mock/fixture 测试，不进行网络调用，REQ-011 拒绝引入 Outlines 或 Pydantic 等新依赖。
53|- **不足与溢出风险:** Shadow evaluation 增加了 `UNIQUE(code, scored_date, schema_version, input_hash)`，限制了单日无意义的重复写入。`context_json` 的数据量按每天 35 只股票计算约为 150KB/天，数据存储增长风险在 SQLite 可接受范围内。
54|
55|---
56|
57|## Implementability assessment
58|
59|**结论:** 该 Spec **目前无法直接进入** fixture-first implementation plan。
60|- **可执行的里程碑:** `MILESTONE-001` (Spec 评审) 正在进行，`MILESTONE-002` 的 dataclass 定义和 Prompt 构建可以部分设计。
61|- **不可执行/必须等待的 Blocker:**
62|  1. 必须等待确立 Gemini JSON Schema 中可选字段（Optional）的表示方式；
63|  2. 必须等待确认 shadow 阶段的非结构化证据（竞争优势、新闻）是如何输入到 `QualitativeContext` 中（是静态 mock 还是改造 fetcher）；
64|  3. 必须确认本地 Validator 如何通过命名空间识别证据类型；
65|  4. 必须明确 `qualitative_scores` 缓存在 cutover 时的迁移或建新表方案。
66|
67|---
68|
69|## Requirement quality assessment
70|
71|Spec 的 54 条 Requirements 存在部分过度设计与内容重复，建议合并以下具体条款以简化实现逻辑：
72|- **合并 Shadow 隔离声明:** [REQ-034](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L262)（不写真实缓存，写 evaluations）与 [REQ-045](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L302)（Shadow 不注入 pipeline/Telegram）内容重合度高，建议合并为一条完整的“Shadow 物理隔离与写入策略”Requirement。
73|- **合并敏感信息过滤规则:** [REQ-010](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L182)（日志不带 key/token）与 [REQ-036](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L264)（Shadow evaluation 不存 key/headers）均属于凭证脱敏，应合并为通用的安全合规准则。
74|- **合并 Alpha 预测脱钩概念:** [REQ-033](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L258)、[REQ-048](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L305) 和 [REQ-054](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L318) 都重复阐述了“模型 rubric agreement/schema validity 提高不等于投资预测 alpha 提升”的免责说明，建议精简并合并。
75|- **合并输入证据纯洁性约束:** [REQ-001](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L168) 与 [REQ-003](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L170) 都要求只使用输入事实且 prompt 必须约束不使用外在知识，建议合并。
76|
77|---
78|
79|## Test and rollout assessment
80|
81|- **Fixture & Test Plan:** 覆盖了 happy path 和较多状态矛盾负例，但**缺失了缓存读取顺序与退避机制（REQ-037）的测试设计**。
82|- **Rollout & Cutover:** Spec 采用“另写实现计划”的形式合理隔离了上线风险，但在架构设计上未回答“cutover 时如何保持缓存版本化一致性”这一最核心的 SQLite 模式冲突问题。
83|- **Rollback:** Spec 提及的 `git restore` 仅适用于 spec 文档回滚，并未给出未来代码阶段由于 Schema 变化或数据污染发生时的回滚机制。建议在 Spec 的风险部分强约束：任何 cutover 计划必须包含表结构重建与备份还原的物理命令。
84|
85|---
86|
87|## Recommended next step
88|
89|修改 Spec 以解决上述 4 项阻碍（Gemini 可选 Schema 设计、Shadow 静态证据注入路径、缓存表版本化方案、Evidence ID 命名空间），合并冗余 Requirements，并在 Spec 通过二次审查后再启动 `MILESTONE-002` (Fixture-first 代码实现)。
90|
91|---
92|
93|## Evidence references
94|
95|- **目标 Spec 文件:** [2026-07-14-source-grounded-structured-qualitative-scoring-spec.md](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md)
96|  - **REQ-004 ~ REQ-007 (数据短缺冲突):** [L171-L179](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L171-L179)
97|  - **REQ-016 ~ REQ-017 (Null 类型约束):** [L186-L187](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L186-L187)
98|  - **REQ-023, REQ-026 (本地校验逻辑):** [L229](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L229), [L232](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L232)
99|  - **REQ-037, REQ-040 (版本化与缓存读取):** [L265-L266](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L265-L266), [L268](file:///home/lin/a-stock-tracker/docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md#L268)
100|- **生产缓存代码 (SQLite Schema 基线):** [cache.py:L159-165](file:///home/lin/a-stock-tracker/lib/cache.py#L159-L165)
101|- **生产 Pipeline 注入位置:** [pipeline.py:L841-844](file:///home/lin/a-stock-tracker/pipeline.py#L841-L844)
```
