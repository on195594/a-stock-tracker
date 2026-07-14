# Bounded review snapshot

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
123|- Schema can constrain object/array/string/integer/enum/minimum/maximum 等字段。
124|- Structured output guarantees syntactically valid schema-shaped JSON，不保证值在业务语义上正确；应用仍必须校验。
125|- `generateContent` 已被官方标为上一代 API；本次为最小改动继续使用现有接口，是否迁移 Interactions API 属于独立任务。
126|
127|## 6. 术语和状态
128|
129|### 6.1 Evidence packet
130|
131|送入模型的唯一事实来源。每条证据必须包含：
132|
133|```json
134|{
135|  "evidence_id": "fundamentals.roe_3y_avg",
136|  "dimension": "moat",
137|  "value": 14.2,
138|  "unit": "percent",
139|  "source": "local_fundamentals_cache",
140|  "source_date": "2026-03-31",
141|  "freshness_status": "fresh"
142|}
143|```
144|
145|### 6.2 Dimension status
146|
147|每个维度只能使用：
148|
149|- `scored`: 证据足够，可给分。
150|- `insufficient_data`: 证据不足，不给分。
151|
152|不得使用 `unknown`、`probably` 等模糊状态。
153|
154|### 6.3 Overall status
155|
156|- `scored`: 三个维度全部通过本地验证。
157|- `insufficient_data`: 至少一个维度证据不足。
158|- `invalid`: 仅由本地代码产生，表示模型输出虽返回但违反应用端契约；模型不得自行返回该状态。
159|
160|### 6.4 Confidence
161|
162|模型输出只允许：`low | medium | high`。Confidence 是证据充分度说明，不是收益概率，不得进入 `total_score`。
163|
164|## 7. Requirements
165|
166|### 7.1 输入证据契约
167|
168|- **REQ-001:** 新评分入口必须接收显式 `QualitativeContext`，不得只接收 `code` 和 `name` 后让模型自由补全事实。
169|- **REQ-002:** `QualitativeContext` 必须至少包含 `code`、`name`、`industry`、`as_of_date`、`schema_version` 和 `evidence[]`。
170|- **REQ-003:** Evidence packet 只能包含本地已获取并标注来源/日期的数据；Prompt 必须明确“不得使用输入外知识”。
171|- **REQ-004:** 现有基本面缓存可提供候选证据：`roe_3y_avg`、`roe_latest`、`net_profit_growth`、`debt_ratio`、`gross_margin`、`report_period`。`pb_percentile_10y` 属于估值证据，不得单独用于证明护城河或市场地位。
172|- **REQ-005:** `moat` 要进入 `scored`，除财务持续性证据外，必须至少有一条直接竞争优势证据，例如品牌/成本/渠道/转换成本/网络效应/监管牌照；如果当前数据源没有这类证据，必须返回 `insufficient_data`。
173|- **REQ-006:** `market_pos` 要进入 `scored`，必须至少有一条行业地位证据，例如市场份额、行业排名、核心业务规模相对同行或明确的细分龙头证据；公司自身财务指标不能单独证明行业地位。
174|- **REQ-007:** `sentiment` 要进入 `scored`，必须有带日期且在配置的新鲜度窗口内的公告/新闻/一致预期等证据。仅有财报指标、PB 分位或价格趋势时必须返回 `insufficient_data`。
175|- **REQ-008:** 本 spec 初始 shadow 允许因为缺少 REQ-005~007 所需证据而大量返回 `insufficient_data`；这属于揭示数据缺口，不是可用固定分数掩盖的失败。
176|- **REQ-009:** 输入构建器必须输出稳定排序并计算 `input_hash`；相同输入内容必须得到相同 hash，日期或证据变化必须改变 hash。
177|- **REQ-010:** Prompt、日志和 evaluation 记录不得包含 API key、Telegram token、环境变量值或其他凭证。
178|
179|### 7.2 结构化输出契约
180|
181|- **REQ-011:** 不新增 Outlines 依赖；使用现有标准库 HTTP 路径和 Gemini 原生 JSON Schema。
182|- **REQ-012:** Gemini 请求必须在 `generationConfig` 中声明 JSON MIME 类型和 schema；具体字段名以实施时再次读取的官方 `generateContent` 文档为准，不得依赖训练记忆。
183|- **REQ-013:** 顶层输出必须包含且只包含 `schema_version`、`overall_status`、`as_of_date`、`dimensions`。
184|- **REQ-014:** `dimensions` 必须且只包含 `moat`、`market_pos`、`sentiment`。
185|- **REQ-015:** 每个维度必须包含 `status`、`score`、`confidence`、`evidence_ids`、`rationale`。
186|- **REQ-016:** `score` 的类型为 `integer | null`；`moat` 范围 1-10，`market_pos` 和 `sentiment` 范围 1-5。
187|- **REQ-017:** `status=scored` 时 `score` 必须为整数且 `evidence_ids` 非空；`status=insufficient_data` 时 `score` 必须为 `null`，`evidence_ids` 可以为空，`rationale` 必须说明缺少什么证据。
188|- **REQ-018:** `rationale` 只允许解释输入证据与评分锚点之间的关系，不得引入输入中没有的新事实；长度必须有上限，避免把自由文本重新变成无法验证的主要载体。
189|- **REQ-019:** Schema 应使用 `required`、`additionalProperties=false`、`enum`、`minimum`、`maximum` 和数组数量限制等官方支持的约束；若 API 忽略某项约束，本地验证器必须补上。
190|- **REQ-020:** 返回 JSON 的 key 顺序、空白或换行不得成为业务逻辑；只能按解析后的对象判断。
191|
192|参考输出：
193|
194|```json
195|{
196|  "schema_version": "qualitative-score-v2",
197|  "overall_status": "insufficient_data",
198|  "as_of_date": "2026-07-14",
199|  "dimensions": {
200|    "moat": {
201|      "status": "insufficient_data",
202|      "score": null,
203|      "confidence": "low",
204|      "evidence_ids": ["fundamentals.roe_3y_avg"],
205|      "rationale": "财务持续性数据存在，但缺少直接竞争优势证据。"
206|    },
207|    "market_pos": {
208|      "status": "insufficient_data",
209|      "score": null,
210|      "confidence": "low",
211|      "evidence_ids": [],
212|      "rationale": "缺少市场份额、行业排名或同行对比证据。"
213|    },
214|    "sentiment": {
215|      "status": "insufficient_data",
216|      "score": null,
217|      "confidence": "low",
218|      "evidence_ids": [],
219|      "rationale": "缺少新鲜且带日期的公告或新闻证据。"
220|    }
221|  }
222|}
223|```
224|
225|### 7.3 本地验证契约
226|
227|- **REQ-021:** 新验证器必须独立于 HTTP 调用，可接受普通 Python 对象和 `QualitativeContext` 做纯函数校验。
228|- **REQ-022:** 本地验证必须拒绝缺字段、额外字段、错误类型、越界分数、未知枚举和错误 `schema_version`。
229|- **REQ-023:** 本地验证必须拒绝任何不在输入 evidence packet 中的 `evidence_id`。
230|- **REQ-024:** 本地验证必须拒绝 `scored + null score`、`insufficient_data + integer score`、`scored + empty evidence_ids` 等状态矛盾。
231|- **REQ-025:** 本地验证必须检查 `as_of_date` 与输入一致，不能接受模型自行生成其他日期。
232|- **REQ-026:** 本地验证必须检查维度证据类型：估值证据不能单独支持 moat/market_pos，过期公告或新闻不能支持 sentiment。
233|- **REQ-027:** 任一维度校验失败时，整个新结果为 `invalid`；不得只采用其他两个维度。
234|- **REQ-028:** 现有 `_validate()` 在生产 cutover 前不得删除；实施可将其演进为兼容旧 dict 与 v2 结果的边界适配器，但必须保留整数范围的二次检查。
235|
236|### 7.4 评分锚点
237|
238|- **REQ-029:** Prompt 和人工审查必须使用同一份版本化 rubric；rubric 变更必须更新 `schema_version` 或独立 `rubric_version`。
239|- **REQ-030:** `moat` 评分必须采用可解释锚点，而不是“凭感觉 1-10”：
240|  - 1-2：存在明显竞争劣势或证据不支持持续优势。
241|  - 3-4：优势弱、易复制或持续性证据不足。
242|  - 5-6：存在一项可识别优势，但持续性/强度证据一般。
243|  - 7-8：至少一项强且持续的竞争优势，有多条独立证据支持。
244|  - 9-10：多重强优势且长期持续；必须有高质量直接证据，不得仅凭高 ROE/高毛利给出。
245|- **REQ-031:** `market_pos` 锚点：
246|  - 1：边缘参与者或明确弱势。
247|  - 2：非头部、地位一般。
248|  - 3：主要参与者，但没有明确头部证据。
249|  - 4：细分或行业头部，有直接排名/份额证据。
250|  - 5：明确领导者，且有多条最新直接证据；不得只凭公司规模推断。
251|- **REQ-032:** `sentiment` 锚点：
252|  - 1：近期重大明确负面。
253|  - 2：偏负面。
254|  - 3：中性或多空抵消。
255|  - 4：偏正面。
256|  - 5：近期重大明确正面。
257|  - 无新鲜证据：`insufficient_data`，不是默认 3。
258|- **REQ-033:** Rubric 只约束模型评分一致性，不得宣称高分能预测正 alpha。
259|
260|### 7.5 缓存与 fallback
261|
262|- **REQ-034:** Shadow 阶段不得写现有 `qualitative_scores`，优先写独立 `qualitative_score_evaluations` 或项目内文件型 artifact。
263|- **REQ-035:** Shadow evaluation 必须保存 `code`、`scored_date`、`schema_version`、`rubric_version`、`model`、`input_hash`、`overall_status`、`context_json`、`result_json`、`validation_status`、`failure_reason`、`created_at`。
264|- **REQ-036:** Shadow evaluation 不得包含 API key 或原始 HTTP headers；`context_json` 只保存已允许的来源数据。
265|- **REQ-037:** 生产 cutover 后的读取优先级必须为：同版本 fresh validated result → 同版本 stale validated result → 旧版 stale cache（仅迁移期）→ 固定 `FALLBACK`。
266|- **REQ-038:** `insufficient_data` 或 `invalid` 结果不得写入现有生产分数缓存，也不得覆盖先前可信分数。
267|- **REQ-039:** 不允许 moat 使用 v2、market_pos 使用旧缓存、sentiment 使用 fixed fallback 的混合结果；保持 all-or-nothing。
268|- **REQ-040:** 新方法必须版本化，不能让旧版和新版定性分数在报告中无法区分。
269|
270|候选 shadow 表（仅设计，不构成 DB 修改批准）：
271|
272|```sql
273|CREATE TABLE qualitative_score_evaluations (
274|    id INTEGER PRIMARY KEY AUTOINCREMENT,
275|    code TEXT NOT NULL,
276|    scored_date TEXT NOT NULL,
277|    schema_version TEXT NOT NULL,
278|    rubric_version TEXT NOT NULL,
279|    model TEXT NOT NULL,
280|    input_hash TEXT NOT NULL,
281|    overall_status TEXT NOT NULL,
282|    context_json TEXT NOT NULL,
283|    result_json TEXT,
284|    validation_status TEXT NOT NULL,
285|    failure_reason TEXT,
286|    created_at TEXT NOT NULL,
287|    UNIQUE(code, scored_date, schema_version, input_hash)
288|);
289|```
290|
291|### 7.6 Shadow/report-only gate
292|
293|- **REQ-041:** 第一阶段只能使用 mock/fixture，不发真实 Gemini 请求，不写真实 `tracker.db`。
294|- **REQ-042:** Fixture 至少覆盖：完整证据、缺 moat 直接证据、缺 market_pos 证据、sentiment 过期、未知 evidence ID、schema 合法但状态矛盾、越界分数、额外字段。
295|- **REQ-043:** 真实 shadow run 属于付费外部调用和生产数据读取，执行前必须单独确认范围、股票样本、调用次数和输出位置。
296|- **REQ-044:** 初始真实 shadow 样本应按行业分层选择 6 只代表性股票；不得只选择最熟悉或最容易评分的公司。
297|- **REQ-045:** Shadow 输出不得注入 pipeline，不得改变 Telegram 候选，不得写 `predictions`。
298|- **REQ-046:** 人工复核必须在不知道旧版 Gemini 分数的情况下，先根据同一 evidence packet 给出参考状态/评分，再比较 v2，避免锚定偏差。
299|- **REQ-047:** Shadow pass gate：
300|  1. Schema validity = 100%。
301|  2. 未知 evidence ID 接受率 = 0%。
302|  3. 无依据事实数量 = 0。
303|  4. 所有证据不足 case 均 fail closed。
304|  5. 对人工判定可评分的维度，至少 90% 落在人工参考分 ±1 内；样本不足时只能标记 provisional。
305|- **REQ-048:** Shadow gate 通过只代表结构/证据/评分锚点可接受，不代表投资预测有效；生产切换仍需单独批准。
306|
307|### 7.7 生产切换与后验评估
308|
309|- **REQ-049:** 生产 cutover 必须另写 implementation plan，列出确切文件、DB 迁移、备份、回滚、测试和一次受控 smoke。
310|- **REQ-050:** 修改生产 DB schema 前必须备份 `tracker.db` 并验证备份可打开；不得在本 spec 阶段执行。
311|- **REQ-051:** Cutover 不得回写历史 predictions；只影响批准日期之后的新评分。
312|- **REQ-052:** Cutover 后必须记录评分方法版本，使 `accuracy-report` 能按版本和 score_date 分层，不能与旧版混算。
313|- **REQ-053:** 后验投资验证至少分别报告 30/60/90 日 alpha、hit rate、样本数和置信限制；在样本不足前不得宣称准确性提升。
314|- **REQ-054:** 若新方法降低结构错误但未改善人工 rubric agreement 或后验 alpha，应分别报告，不得用一个指标掩盖另一个指标。
315|
316|## 8. 设计约束
317|
318|### Always do
319|
320|- 新增函数必须有参数和返回值类型注解。
321|- 使用 `logger = logging.getLogger(__name__)`，禁止生产 `print`。
322|- 数据结构优先使用 `@dataclass`，不要让跨层契约长期依赖裸字典。
323|- 外部 API 错误必须带上下文并按现有 retry/fallback 策略处理。
324|- 测试使用 `tmp_path`，不得访问真实 `tracker.db`。
325|- 所有网络调用必须 mock；真实 Gemini shadow 需要单独批准。
326|- 每次实现必须先写失败测试，再改代码。
327|- 实施前重新核对 Gemini 当前官方文档，因为结构化输出字段可能变化。
328|
329|### Ask first
330|
331|- 添加或升级依赖。
332|- 修改 `lib/cache.py` DDL 或真实 SQLite schema。
333|- 执行真实 Gemini shadow/smoke。
334|- 修改 production pipeline 注入、缓存优先级、cron 或 Telegram。
335|- 增加公告、新闻、搜索或其他外部证据源。
336|- 改变定性分数权重、评分阈值或历史数据。
337|
338|### Never do
339|
340|- 把 Schema 合法当成语义正确。
341|- 让模型引用未提供的 evidence ID。
342|- 用模型训练记忆补齐公司事实。
343|- 把 `insufficient_data` 静默转成中性分后称为模型结果。
344|- 混用新评分、旧评分和 fallback 三个维度。
345|- 删除或覆盖无关工作区改动。
346|- 提交 `.env`、API key、token 或真实私密 headers。
347|- 未经批准改写生产 `tracker.db`、predictions 或 cron。
348|
349|## 9. 方案选择与被拒绝方案
350|
351|### 9.1 选择方案
352|
353|```text
354|Local evidence sources
355|  → QualitativeContext / evidence IDs / input_hash
356|  → Gemini 2.5 Flash native JSON Schema
357|  → JSON parse
358|  → deterministic schema + evidence + rubric validation
359|  → shadow evaluation artifact/table
360|  → human rubric review
361|  → separately approved production adapter
362|  → natural 30/60/90d outcome validation
363|```
364|
365|### 9.2 被拒绝方案
366|
367|- **直接安装 Outlines**：当前只有一个 Gemini REST 后端，原生结构化输出已覆盖核心需求；Outlines会新增依赖和适配层，不能解决 grounding。
368|- **只给当前 Prompt 加 JSON Schema**：会得到更稳定的无依据评分，降低可见错误但保留静默语义风险。
369|- **让 Gemini 自动搜索互联网并评分**：来源、时点、引用、成本和稳定性边界未定义；不属于本次最小改造。
370|- **把现有基本面指标直接当 moat/market_pos 证据**：盈利质量不等于竞争壁垒，规模也不等于行业领导地位。
371|- **sentiment 缺数据时自动给 3 分并标为模型结果**：掩盖数据缺口。固定 3 只能继续作为系统 fallback，并必须与模型评分区分。
372|- **直接扩展现有 qualitative_scores 后立刻切换**：无法在不影响生产的前提下比较 v1/v2，且 schema 变更需要额外批准。
373|- **部分字段采用新模型结果**：破坏现有 all-or-nothing 安全边界，使评分来源难以解释。
374|
375|## 10. Acceptance criteria
376|
377|- **AC-001 maps to REQ-001~010:** Spec 明确每个维度的最低证据门槛，并规定缺证据返回 `insufficient_data`。
378|- **AC-002 maps to REQ-011~020:** Spec 定义 Gemini 原生 JSON Schema 输出形状、范围、状态和禁止额外字段。
379|- **AC-003 maps to REQ-021~028:** Spec 定义应用端独立验证，包含 evidence ID、状态一致性、日期和维度证据类型检查。
380|- **AC-004 maps to REQ-029~033:** Spec 给出三个维度的版本化评分锚点，并明确不等于预测有效性。
381|- **AC-005 maps to REQ-034~040:** Spec 定义 shadow 隔离、版本、缓存和 all-or-nothing fallback。
382|- **AC-006 maps to REQ-041~048:** Spec 定义 fixture、真实 shadow 审批边界和量化 pass gate。
383|- **AC-007 maps to REQ-049~054:** Spec 定义 cutover、DB 备份、历史不可改写和 30/60/90 日后验验证。
384|- **AC-008:** 本次文档变更不修改 `gemini_scorer.py`、`pipeline.py`、`lib/cache.py`、`weights.json`、测试、cron 或真实 DB。
385|- **AC-009:** `git diff --check` 通过；spec 中 REQ 编号唯一，所有 AC 均能映射到 requirements。
386|- **AC-010:** 人工评审明确接受或修改以下判断点：证据门槛、shadow 样本、±1 rubric gate、生产 cutover 前置条件。
387|
388|## 11. Milestones and plan handoff
389|
390|### MILESTONE-001: Spec review
391|
392|Maps to: AC-001~010
393|
394|Outcome:
395|- 用户与工程 reviewer 审查本 spec。
396|- 决定 evidence packet 第一版允许哪些现有字段、哪些维度必然 insufficient。
397|
398|Non-scope:
399|- 不改代码、DB、测试或生产行为。
400|
401|Verification signal:
402|- Spec 状态从 `draft` 改为 `approved`，或 reviewer 只剩非阻塞建议。
403|
404|### MILESTONE-002: Fixture-first contract implementation
405|
406|Maps to: REQ-001~033, AC-001~004
407|
408|Outcome:
409|- 实现 dataclass、schema builder、Prompt builder 和纯本地 validator。
410|- 新增 mock/fixture 测试；不调用真实 Gemini。
411|
412|Non-scope:
413|- 不写真实 DB，不接 pipeline，不影响生产评分。
414|
415|Verification signal:
416|- 原有测试全通过；新增结构、证据、状态负例测试全通过。
417|
418|### MILESTONE-003: Shadow persistence
419|
420|Maps to: REQ-034~045, AC-005~006
421|
422|Outcome:
423|- 在明确批准 DB schema 后，增加隔离 evaluation 表；或先用项目内文件 artifact 替代。
424|- 生产读路径保持不变。
425|
426|Verification signal:
427|- `tmp_path` migration/CRUD 测试通过；真实 `tracker.db` 未改动。
428|
429|### MILESTONE-004: Bounded real shadow review
430|
431|Maps to: REQ-043~048
432|
433|Outcome:
434|- 经批准后对 6 只分行业代表股票运行固定次数 shadow。
435|- 生成结构、证据、rubric agreement 报告。
436|
437|Non-scope:
438|- 不进入 `score_stock()`，不触发 Telegram。
439|
440|Verification signal:
441|- Pass gate 有实际证据；未通过则停止并修订 spec/rubric/evidence source。
442|
443|### MILESTONE-005: Separately approved production cutover
444|
445|Maps to: REQ-049~054
446|
447|Outcome:
448|- 通过单独 implementation plan 和确认后，才允许新评分进入 production cache/pipeline。
449|
450|Verification signal:
451|- 备份、migration、全量测试、受控 smoke 和回滚演练均通过。
452|
453|## 12. Validation contract
454|
455|### 12.1 本次 spec 验证
456|
457|```bash
458|cd /home/lin/a-stock-tracker
459|git diff --check
460|git diff -- docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md docs/project-status.md
461|```
462|
463|应证明：
464|
465|1. 仅新增 spec，并在 PM ledger 中登记；无生产代码、DB、测试或配置变更。
466|2. REQ 与 AC 编号完整、唯一且可追踪。
467|3. 明确“Outlines 思想、Gemini 原生 Schema、不新增依赖”的架构决策。
468|4. 明确“缺证据 fail closed”和 shadow-first 边界。
469|
470|### 12.2 未来实现最低验证
471|
472|实施计划至少应包含：
473|
474|```bash
475|.venv/bin/python -m pytest tests/test_gemini_scorer.py -q
476|.venv/bin/python -m pytest -q
477|.venv/bin/python -m ruff check gemini_scorer.py lib/cache.py tests/test_gemini_scorer.py
478|.venv/bin/python -m ruff format --check gemini_scorer.py lib/cache.py tests/test_gemini_scorer.py
479|git diff --check
480|```
481|
482|若项目当时已有类型检查基线，再加入项目实际使用的 type checker；不得为满足本 spec 临时引入新工具。
483|
484|### 12.3 必须报告的证据
485|
486|- Changed files
487|- 测试、lint、format 命令与退出码
488|- Mock fixture 的正反例结果
489|- Shadow 调用次数、模型、schema/rubric version
490|- Schema validity、evidence validity、rubric agreement
491|- 未满足条件、数据缺口和 deferred 项
492|- 生产 DB、cron、Telegram、weights、历史 predictions 是否保持未触碰
493|
494|## 13. Rollback and stop conditions
495|
496|### Rollback
497|
498|Spec-only 阶段：
499|
500|```bash
501|git restore -- docs/project-status.md
502|rm docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md
503|```
504|
505|未来代码阶段必须在 implementation plan 中给出精确 `git restore`/commit revert 和 DB 备份恢复命令；不得复用未验证的通用命令。
506|
507|### Stop if
508|
509|- 官方 API 当前字段与 spec 假设不一致。
510|- Gemini 2.5 Flash 不再支持目标 structured output 能力。
511|- 需要新增依赖但尚未获批。
512|- Evidence packet 无法满足 moat/market_pos/sentiment 的最低证据门槛。
513|- 模型生成未知 evidence ID 或输入外事实，且修复后仍复现。
514|- Shadow gate 未通过。
515|- 真实 DB 发生未批准写入。
516|- 发现会改写历史 predictions、weights_hash 或 outcome。
517|- 全量测试出现不能归因于本范围的失败。
518|- 工作区无关改动会被覆盖、格式化或误提交。
519|
520|## 14. 风险与开放问题
521|
522|### 已决策
523|
524|1. 当前不引入 Outlines。
525|2. 当前不迁移 Gemini Interactions API。
526|3. 使用原生 JSON Schema + 本地语义校验双层边界。
527|4. 数据不足必须显式暴露，不允许模型猜分。
528|5. 新方法先 shadow，不直接进入生产。
529|
530|### 待用户/Reviewer确认
531|
532|1. **直接竞争优势证据来源**：现有 cache 不足；后续是扩展现有 fetcher、接入人工维护证据，还是保留 moat unavailable？
533|2. **行业地位证据来源**：是否允许人工/项目内静态 evidence packet，还是建设可审计 provider？
534|3. **sentiment 数据边界**：项目路线图当前明确不建设独立舆情层；是否继续让 sentiment 使用系统 fallback，而不让模型评分？
535|4. **Shadow 存储**：先用 JSONL/Markdown artifact，还是经确认新增独立 SQLite evaluation 表？
536|5. **真实 shadow 成本**：6 只股票 × 单次调用是否足够，还是需要第二轮反向边界样本？
537|6. **生产适用范围**：若只有 moat/market_pos 有足够证据，而 sentiment 长期 unavailable，是否继续坚持 all-or-nothing，还是将 sentiment 从 LLM 评分职责中移除？后者属于评分架构变更，需要单独 spec 决策。
538|
539|## 15. Approval state
540|
541|- Spec approval: pending
542|- Implementation approval: pending
543|- Production DB schema approval: pending
544|- Real Gemini shadow approval: pending
545|- Production cutover approval: pending
546|
547|本 spec 是开发契约草案，不构成任何生产、数据库、付费调用、cron、Telegram 或权重修改授权。
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
130|        error_code TEXT,
131|        PRIMARY KEY (code, trade_date, adjusted)
132|    )""")
133|    _ensure_columns(conn, "daily_bars", {
134|        "volume_unit": "TEXT NOT NULL DEFAULT 'unknown'",
135|    })
136|    conn.execute("""CREATE INDEX IF NOT EXISTS idx_daily_bars_code_date
137|        ON daily_bars(code, trade_date DESC)""")
138|    conn.execute("""CREATE TABLE IF NOT EXISTS market_data_audit (
139|        id INTEGER PRIMARY KEY AUTOINCREMENT,
140|        run_date TEXT NOT NULL,
141|        code TEXT NOT NULL,
142|        purpose TEXT NOT NULL,
143|        source TEXT NOT NULL,
144|        status TEXT NOT NULL,
145|        fallback_source TEXT,
146|        fallback_reason TEXT,
147|        error_code TEXT,
148|        error_message TEXT,
149|        fetched_at TEXT NOT NULL
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
195|    code: str,
196|    bars: Any,
197|    source: str,
198|    adjusted: str = "none",
199|    volume_unit: str = "unknown",
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

## FILE: docs/evolution-roadmap.md

```text
233|## 四、不做什么（边界）
234|
235|以下方向明确排除，不纳入演化计划：
236|
237|| 方向 | 排除原因 |
238||------|---------|
239|| 高频/日内交易 | 框架是基本面逻辑，日内信号与之矛盾 |
240|| 量化对冲策略 | 单账户个人投资，不需要对冲层 |
241|| 价格动量因子（纳入 L1/L2 评分） | 动量不影响公司价值评分。**例外**：均线、成交量可作为 L3 入场过滤信号（Phase 5），但不得修改 total_score |
242|| 独立舆情/NLP 数据源 | 当前阶段不引入独立 NLP 舆情层；Gemini 定性提供有限的情绪辅助，不是完整舆情覆盖 |
243|| 实盘自动下单 | 系统输出信号，人工执行，不做自动交易 |
244|
245|---
246|
247|## 五、跨阶段约束（所有 Phase 必须遵守）
248|
249|1. **predictions 表历史数据不可改写**：任何评分逻辑变更只影响新记录，旧记录保留原始值
250|2. **weights_hash 完整性**：weights.json 的 frameworks 子树变更必须被 pipeline 检测到（hash 变更会退出）
251|3. **alpha_*d 不直接写入**：Generated Column，任何 INSERT/UPDATE 都不能包含此列
252|4. **AKShare mock 原则**：所有测试用 monkeypatch，不发真实网络请求
253|5. **Sheets sync 不阻断 daily**：Google Sheets 是展示层，失败只记 WARNING
254|6. **文档先行**：Phase N 实施前，先在本文档中将对应 Phase 标记为 `[实施中]`，完成后标记为 `[已完成 YYYY-MM-DD]`
255|7. **PM 控制面**：`docs/project-status.md` 是当前 spec 台账；压缩上下文或跨会话交接时优先读取该文件确认 active phase、阻塞项和下一检查点。
256|
257|---
258|
259|## 六、版本历史
260|
261|| 版本 | 日期 | 变更 |
262||------|------|------|
263|| v1.0 | 2026-05-15 | 初始版本，基于 REPAIR-PLAN v2.0 完成后的系统状态建立基线 |
264|| v1.1 | 2026-05-15 | Codex 独立审查后修复 8 处问题：Phase 4 里程碑分层、L3 版本化约束、Phase 6 数据依赖表、Phase 7 前置条件补全、动态池退出规则、边界说明澄清 |
265|| v1.2 | 2026-05-30 | 记录多 AI 辩论结论：Phase 5 L3 买点层优先，Framework B 后置；确认 L3 字段、Telegram、日线窗口、accuracy-report 授权边界 |
266|| v1.3 | 2026-05-30 | Phase 5 L3 买点层实现完成：schema、纯计算 seam、daily 写入、Telegram 过滤、accuracy-report L3 section |
267|| v1.4 | 2026-06-05 | Phase 6 readiness 报告增强：明确生产化阻塞项与下一步，保持 Framework B report-only，不启用生产写入 |
268|| v1.5 | 2026-06-21 | 补记 2026-06-09~06-16 行情数据源迁移：AKShare/东方财富行情入口禁用，迁移到 Tushare 主源 + BaoStock degraded fallback（基本面/估值/财报抓取不受影响，仍用 AKShare）；2026-06-21 重新探测 `scripts/probe_tushare_market_data.py` 结果 PASS，`check_market_data_readiness.py` 转为 READY，此前 06-09 探测因 Tushare 限频(1次/小时)误报 FAIL 已更新为最新通过记录 |
269|| v1.6 | 2026-06-26 | 同步真实恢复状态：`a-stock-lib==0.1.2`、隔离 BaoStock fallback、真实 probe/backfill/daily、`READY_CRON`、managed cron block；Phase 4 标记完成并转持续观察，Phase 6 明确为 report-only 深化，不启用 Framework B 生产写入；新增 `docs/project-status.md` 作为 PM/spec 台账 |
270|| v1.7 | 2026-07-04 | agy 工程审查修复（Batch A+B，11 commits）：DB per-stock SAVEPOINT 隔离、spot_em 重试计数器（连续3次才今日锁定）、Gemini 退避重试+过期缓存降级、subprocess stderr 转发、cache._ensure_columns SQL identifier allowlist、_OUTCOME_WINDOWS frozenset、agent_reviewer 接入真实 Gemini REST API（_fake_review_fallback 降级）；新增 cmd_init/cmd_weekly/cmd_outcome_update 测试覆盖；测试基线 225 passed, 1 skipped（含 3 个新测试模块补丁） |
271|| v1.8 | 2026-07-12 | Phase 5 L3 v2 Phase 2+3 完成：QFQ 采集（35/35×130行，pass_strong 激活）+ 推送触发切换至 l3_v2_signal=1；Framework A 倒置诊断（Q5 avg_alpha=-9.91%，根因=截面校准偏差+11支伪复制，不调权重）；agy 投资视角审查（持有期错配+价值风格轮出；Priority 1=延伸60d/90d评估）；Tushare probe 刷新（上次 2026-07-02 已过期）；daily+outcome-update cron 正式恢复（market-data-backfill ok=35） |
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
72|| 首轮自动 weekly PM loop 待自然验证 | 需要确认新 cron 能按时发送 Telegram 摘要 | `weekly-pm-loop` 周一 09:30 自然运行并产生日志/摘要 | 下一周一 |
73|| Framework A strong 层级尚未证明优于基准 | 不宜调权重或宣称模型有效 | 另开权重复核 spec | 待更多样本与独立审查 |
74|| L3 v2 qfq 覆盖不足 | 阻止 GO_TDD 和 pass_strong 有效性结论 | 设计分批/缓存/限速的 qfq 数据获取方案，避开 Tushare `adj_factor` 1次/分钟限制，并重跑只读报告 | 待单独计划 |
75|| Framework A 60d 评估窗口未到期 | 无法验证"持有期错配"假说（agy Priority 1） | outcome-update 已恢复，首批 60d 2026-07-14 自动入库；入库后跑五分位对比 | 2026-07-14 后可执行 |
76|
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
