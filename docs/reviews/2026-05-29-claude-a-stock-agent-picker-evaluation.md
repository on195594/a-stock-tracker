我没有 Write 工具可用于写计划文件，所以将评审结果直接在此输出作为我的最终计划。

---

## 独立工程评审：A 股选股 Agent 重构评估

---

### Verdict：**APPROVE_WITH_REVISIONS**

---

### 总体判断

文档完成了合格的现状审计，非目标边界和复用/弃用分层有价值，值得保留作为后续决策基准。但"新建绿地项目"的结论是从正确的问题诊断（pipeline.py/lib/cache.py 混杂）跳到了偏重的解决方案，而现有系统已有一份清晰可执行的演化路线图（evolution-roadmap.md Phase 4→7），无需通过新项目才能收敛到目标架构。文档自己识别出的最高优先级问题（`accuracy_report.txt` 显示 0 条结案但 DB 有 110 条）被低估了严重性，它是所有架构判断的前提条件，必须先修复才能做有意义的有效性验证。Phase 0-5 的新项目路径要经过 5 个阶段的基础设施建设才有 MVP 输出，这段时间旧系统必须继续并行运行，最终形成两套并行维护的代价。

---

### 支持原分析的证据

| 证据 | 位置 | 支持哪个观点 |
|------|------|-------------|
| `pipeline.py` 880 行，`cmd_accuracy_report` 192 行，`cmd_daily` 135 行 | `pipeline.py:505-695` | 原地继续堆代码风险高 |
| `lib/cache.py` 851 行，混合 holdings/analysis_results/flags/predictions/index_prices/qualitative_scores/CLI | `lib/cache.py:1-852` | 旧 skill 遗留代码确实与选股 Agent MVP 混杂 |
| `scorer.py` 127 行、职责单一、有完整测试覆盖 | `scorer.py:1-128` | 是最值得复用的模块 |
| `sheets_sync.py` 在 CLAUDE.md 中明确标为"展示层，失败只记 WARNING" | `CLAUDE.md` §硬性规则 | 弃用/隔离判断正确 |
| `weights.json` 有版本化 hash 体系，history 已有 676 条 | `lib/cache.py:135-138`, `weights.json` | 历史数据保护约束正确 |
| `gemini_scorer.py` FALLBACK、_validate、TTL 已有 all-or-nothing 模式 | `gemini_scorer.py:56-64, 118-136` | "Phase 4 Agent reviewer 窄接口"思路有现成可参照的模式 |
| `SUPPORTED_FRAMEWORKS = {"A"}` 已暂停 Framework B | `scorer.py:19` | 多框架冻结判断正确 |

---

### 需要修正或补充的点

#### Blocking（实施前必须处理）

**B1. accuracy_report.txt 与 DB 不一致是 P0 bug，不是"后续澄清"**

- 证据：`accuracy_report.txt:6` 显示"0 条已结案记录"，但文档 §1.4 提到 DB 有 `outcome_30d is not null: 110 条`
- 影响：所有"命中率"判断、Phase 4 里程碑检测、Framework B 重启门槛进度，全部基于这个命令的输出，而它目前是坏的
- 怀疑原因：`cmd_accuracy_report` 在 `pipeline.py:559-573` 的 `_tier_query` 里 `WHERE outcome_30d IS NOT NULL AND framework = 'A'`，同时 `_w = _load_weights().get("thresholds", {})` 里的阈值可能影响 `total_closed` 与 tier query 条件不一致
- **在做任何架构决策之前，必须先运行 `pipeline.py accuracy-report` 并 debug 为何显示 0 条**

**B2. `_compute_daily_pb_percentile` 在 `pipeline.py` 不在 `scorer.py`，文档迁移清单有遗漏**

- 证据：`pipeline.py:100-110`
- 如果 Phase 2 只迁移 `scorer.py`，这个每日 PB 分位计算逻辑不会被带走，导致新项目的评分结果与旧项目不一致
- 修正：复用清单需要明确包含此函数，并说明迁移到 `scorer.py` 还是新项目的哪个模块

**B3. 工作区有未提交改动，复制到新项目会带入半成品**

- 证据：git status 显示 `lib/cache.py, lib/fetcher.py, pipeline.py, weights.json` 均有未提交修改
- 如果在当前状态做"绿地骨架 + 选择性移植"，必须先确认这些改动是否已稳定，不能直接复制 working directory

#### Important（不 blocking 但需修正）

**I1. "新建项目"结论过重——核心目标可在现有项目内达到**

- 文档目标的"Agent reviewer 窄接口"（Phase 4）与 `gemini_scorer.py` 结构几乎同构：固定输入 schema、_validate、all-or-nothing fallback、缓存。只需在现有项目中新增 `agent_reviewer.py` 模块，无需新项目。
- `evolution-roadmap.md` 的 Phase 5（买点层）、Phase 6（多框架）路径已经是正确的演化方向，新项目的 Phase 0-5 反而是重走一遍基础设施，到 Phase 5 才得到与现有系统等价的功能
- 建议修正：把"新建项目"降级为"当 Phase 5/6 引入的复杂度超出现有 pipeline.py 可维护范围时，再考虑拆包"

**I2. `lib/cache.py` 的 qualitative_scores PK 迁移逻辑未被识别为迁移风险**

- 证据：`lib/cache.py:158-171`，有 DROP TABLE 重建逻辑用于 PK 结构升级
- 新项目 `LegacyTrackerReader` 若用只读连接打开旧 DB，这段迁移代码会因为权限问题 silent fail

**I3. Gemini FALLBACK 值必须跨项目保持一致**

- `gemini_scorer.py:18`：`FALLBACK = {"moat": 5, "market_pos": 2, "sentiment": 3}`
- 这些值是 2026-04-21 前 9 条存量记录的历史评分基准。新项目若修改 fallback，会破坏与历史记录的可比性

**I4. 系统性分数偏移未纳入 Phase 2 golden-master 验证条件**

- CLAUDE.md 明确：2026-05-14 前（pre-fix）vs 2026-05-15 后（post-fix）存在 4-5 分的系统性偏移
- Phase 2 "新旧 scorer 输出一致"的验证条件需要限定为"使用同等 gross_margin 和 pb_percentile_10y 输入"，不能直接用 pre-fix 历史数据验证 post-fix scorer

#### Nice-to-have

**N1. Phase 0 三文档中 mission/requirements 的内容与分析文档高度重叠**，可以直接引用而不是再写一遍

**N2. Phase 1 没有 cron 集成计划**：新项目 MVP 期间，每日 16:30 的数据采集靠谁运行？文档没有说明是继续用旧项目 cron 还是新项目接管

---

### 对"重构 vs 新建"的最终建议

**选：原地演化**（不新建项目）

理由：
1. 核心代码问题（`lib/cache.py` 混杂旧 skill 代码）可以通过"只新增，不修改现有接口"的方式绕开，不需要新项目
2. "Agent reviewer 窄接口"=`gemini_scorer.py` 的模式扩展，在现有项目新建 `agent_reviewer.py` 即可
3. 历史 DB 在原地不需要额外 `LegacyTrackerReader` 抽象层
4. 避免两套并行系统、cron 竞争、数据积累中断的风险
5. `evolution-roadmap.md` 的路径（Phase 4→5→6→7）已经是正确的演化方向，直接继续

如果 6 个月后 pipeline.py 超过 1500 行或出现多人协作需求，再重新评估拆包的必要性。

---

### 最小下一步（≤5 条，可直接执行）

1. **立即**：debug `accuracy_report.txt` 显示 0 条的 bug。在 `tracker.db` 执行 `SELECT COUNT(*), MIN(score_date), MAX(score_date) FROM predictions WHERE outcome_30d IS NOT NULL` 确认真实行数，然后逐步 trace `cmd_accuracy_report` 的查询路径找出不一致原因。
2. **本周**：将 `_compute_daily_pb_percentile`（`pipeline.py:100-110`）移到 `scorer.py`，与 `score_stock` 同模块，方便后续任何新模块复用。
3. **下周**：新建 `agent_reviewer.py`，照 `gemini_scorer.py` 的模式实现 `get_review(code, score_result) -> ReviewOutput`，`ReviewOutput` 包含 `explanation`, `risk_flags`, `missing_fields_comment`。Gemini 超时/非 JSON → 返回空 ReviewOutput 不 fallback，确定性评分不依赖它。
4. **等 accuracy-report 修复后**：评估 Phase 4 的 outcome 数据积累进度，决定是否启动 Phase 5（买点层）—— 按 `evolution-roadmap.md` 方案实施，不需要新项目。
5. **可推迟**：如果确实需要新项目路径，Phase 0 文档工作可以推迟到 Phase 4 数据积累完成（`outcome_30d ≥ 100` 且评分有效性有结论）之后再决定，不需要现在锁死架构。

---

### 检查过的文件范围

- `docs/plans/2026-05-29-a-stock-agent-picker-refactor-evaluation.md`（主评审对象，全文）
- `docs/evolution-roadmap.md`（全文）
- `pipeline.py`（全文，880 行）
- `scorer.py`（全文，128 行）
- `gemini_scorer.py`（全文，137 行）
- `lib/cache.py`（全文，852 行）
- `lib/fetcher.py`（全文，539 行）
- `weights.json`（全文）
- `tests/test_scorer.py`（前 60 行）
- `accuracy_report.txt`（前 30 行，确认 0 条不一致）
- `CLAUDE.md`（通过 system-reminder 上下文）

`★ Insight ─────────────────────────────────────`
评审中最有价值的发现通常来自 "文档说 X，代码说 Y" 的不一致。这里的典型案例是：`accuracy_report.txt` 显示 0 条结案（是命令的输出），但 DB 直接查询有 110 条（是数据库的状态）。这类不一致往往暴露的不是数据问题，而是查询条件的逻辑 bug——在本例中，两者都声称反映同一事实，所以必有一个是错的。先找到这个 bug，才能建立可信的有效性基准。
`─────────────────────────────────────────────────`
