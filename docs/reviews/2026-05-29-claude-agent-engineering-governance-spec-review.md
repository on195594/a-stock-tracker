Plan 文件已写入。以下是审查正文，供直接阅读：

---

## 独立工程审查结论

**已检查：**
- `docs/specs/2026-05-29-agent-engineering-governance-spec.md`（全文，主审查对象）
- `docs/plans/2026-05-29-a-stock-agent-picker-refactor-evaluation.md`（全文，背景）
- `docs/reviews/2026-05-29-claude-a-stock-agent-picker-evaluation.md`（全文，前置评审）
- `accuracy_report.txt`（前 40 行，验证 P0 bug 状态）
- `pipeline.py`（前 30 行）
- `CLAUDE.md`（via system-reminder）

---

### 总结论：`APPROVE_WITH_REVISIONS`

Spec 方向正确，Phase 顺序工程上合理，"只做选股"边界足够硬，Agent reviewer 放在最后（Phase F）。修复下面两个 Blocking 问题后可进入 Phase B。

`★ Insight ─────────────────────────────────────`
一份治理 Spec 的质量取决于"验收标准能否独立于 Spec 本身被验证"。自指式 AC（"文档包含 GP/R/AC 条款"→读文档即通过）和缺少具体 SQL anchor 的 smoke check，是最常见的让 Spec 沦为愿望清单的两种模式。这里两者都出现了，值得专门修正。
`─────────────────────────────────────────────────`

---

### Blocking（进入 Phase B 之前必须处理）

**B1 — Phase A 的 minimum landing change 未要求提交稳定基线**
- 证据：Section 7 Phase A + git status（`pipeline.py`、`lib/cache.py`、`lib/fetcher.py`、`weights.json`、`tests/test_pipeline.py`、`accuracy_report.txt` 均未提交）
- 重要发现：`accuracy_report.txt` 已修复（现显示"Framework A 75 条已结案"，而非前置 Claude 评审时的 0 条），但改动未提交。
- 影响：Phase B–F 建立在未提交状态之上，一旦任何改动被回滚，后续验收条件与实际代码脱节。
- **建议**：Phase A minimum landing change 末尾加："将当前已通过测试的改动提交为 clean baseline commit（`chore: Phase A baseline — accuracy-report fix + governance spec`）后，才开始 Phase B。"

**B2 — Phase C 验收标准缺少具体 SQL anchor**
- 证据：Section 7 Phase C "python3 pipeline.py accuracy-report smoke 输出与只读 SQL 对齐"
- 影响：前次 accuracy-report P0 bug 的根因正是查询条件不一致（全局结案数 vs Framework A 结案数）。"与只读 SQL 对齐"缺少具体 SQL，等于把防线再次交给主观判断。
- **建议**：在 R3 或 Phase C 中嵌入参考查询，例如：
  ```sql
  SELECT COUNT(*) FROM predictions
  WHERE outcome_30d IS NOT NULL AND framework = 'A'
  ```
  并要求报告对应数字必须等于此查询结果，将检查加入测试套件（`assert_report_matches_db`）。

---

### Important

**I1 — Phase B registry 格式留有 Markdown 逃生出口**
- 证据：Section 7 Phase B "新增数据源 registry 文档**或** YAML/JSON 草案"；验证："reviewer 或人工检查"
- 影响：落地为 Markdown 则 R1 的"可回答某字段来自哪里"退化为"有人写了段话"，无法被 smoke test 引用。
- **建议**：改为"新增 `docs/data-source-registry.yaml`，禁止仅以 Markdown 替代"；验证改为"有 smoke 脚本断言 registry 覆盖所有 scorer 输入字段"。

**I2 — Phase A / AC1 验收标准自指循环**
- 证据：Section 8 AC1 "本 Spec 必须包含…" + Phase A 验证 "文档包含 GP/R/AC/Stop 条款"
- 影响：读了 spec 就"通过"，没有独立检查机制。
- **建议**：新增 `tests/test_spec_structure.py`，用 grep 断言 spec 文件存在 "GP1"、"R1"、"AC1"、"Stop 条件" 等必需节标题，使 AC1 可被 `pytest` 机器验证。

**I3 — 各 Phase 之间无明确过渡门禁**
- 证据：Section 7 各 Phase 的"验证"条款列出输出物，但没有"Phase X 完成的充要条件"。
- 影响：Phase B 可以在 registry 未覆盖 benchmark 来源的情况下声称完成。
- **建议**：每个 Phase 末尾添加"Exit Gate"行（一句话二元判断），例如 Phase B："smoke 脚本输出 `registry covers all scored fields: OK` 后视为完成。"

**I4 — 三份文档的"新建项目 vs 原地演化"分歧未在 Spec 中显式收口**
- 证据：背景评估推荐新建项目；Claude 评审推荐原地演化；本 Spec Section 4 写"不新建项目作为默认路径"（正确），但没说明依据哪个评审。
- **建议**：Section 1 末尾加一句："本 Spec 采纳'原地演化'策略（参见 Claude 评审 §I1），弃用背景评估文档的'新建绿地项目'路径。"

---

### Nice-to-have

- **N1**：AC4 "单独批准"机制未定义（由谁、填什么、格式如何）。
- **N2**：Phase E `_compute_daily_pb_percentile` 的最终归属应在 Spec 中明确写 `scorer.py`，避免实施时再做设计决策。
- **N3**：Phase B 验证用语 "deterministic text assertion" 含义模糊，改为"pytest 断言"或"grep 断言"更具体。

---

### "不要倒着构建 AI Agent"贯彻检查

| 维度 | 结论 |
|------|------|
| Agent reviewer 放在确定性核心之后 | ✅ Phase F 是最后阶段 |
| 数据治理早于代码结构优化 | ✅ Phase B/C/D 在 Phase E 之前 |
| GP3 reviewer 权限足够硬 | ✅ 明确禁止输出：新分数、新权重、DB 写入、买卖指令 |
| "只做选股"边界 | ✅ R7 + GP5 + Section 9 三层覆盖，无泄漏 |
| 数据源治理可转化为 registry 和测试 | ⚠️ R1/R4 定义清晰，但 Phase B 格式允许 Markdown 逃生（见 I1） |
| 验收标准可执行 | ⚠️ Phase A 自指（I2）、Phase C SQL anchor 缺失（B2）；其余基本可执行 |
| 隐藏副作用授权 | ✅ 各 Phase 非目标明确排除 cron/LLM/Telegram/DB 迁移/Sheets |

---

### 建议的下一步最小 landing change（5 条）

1. **立即**：将当前已通过测试的工作区改动（accuracy-report 修复 + 本 spec + test_pipeline.py）提交为 clean baseline commit，这是 Phase B 的硬性前置条件。
2. **Phase B**：将 registry 格式约束为 YAML/JSON，删除 Markdown 逃生出口，验证改为机器检查。
3. **Phase C**：在 R3 或 Phase C 中嵌入参考 SQL，要求报告数字与此 SQL 一致，并加入测试套件。
4. **Phase E**：Spec 中明确 `_compute_daily_pb_percentile` 迁移目标 = `scorer.py`，避免实施时再决策。
5. **Phase A**：新增 `tests/test_spec_structure.py` 用 grep 断言 spec 必需章节，使 AC1 独立可验证。
