你是独立工程审查员。请只做只读审查，不要修改任何文件，不要运行会写入项目文件的命令，不要安装依赖，不要访问网络。

审查目标：
- `/home/lin/a-stock-tracker/docs/specs/2026-05-29-agent-engineering-governance-spec.md`

背景：
- 这是 A 股选股决策支持项目 `/home/lin/a-stock-tracker` 的下一阶段治理 Spec。
- Spec 试图把文章《Most AI Agents Fail in Production Because They’re Built Backwards》的原则落成工程治理：先确定性数据/评分/报告，再代码结构 seam，最后才是窄接口 Agent reviewer。
- 用户关心三个方向：
  1. 数据源治理：优先保证数据获取渠道畅通、数据准确性、字段口径可追溯。
  2. 代码结构优化：但不能为了 Agent 外壳而过度重构。
  3. 只做 A 股选股：不自动交易、不做持仓/账户、不做多市场或泛投研。
- 当前已知项目事实：
  - `pipeline.py` 聚合 daily/outcome-update/accuracy-report 等职责。
  - `scorer.py` 是相对清晰的确定性评分核心。
  - `lib/cache.py` / `lib/fetcher.py` 较混杂。
  - 最近已修复 `accuracy-report` 样本不足口径从全局结案数改为 Framework A 结案数。
  - 工作区有多处未提交改动，因此本 spec 不应授权大规模实现或真实副作用。

请你只读检查：
1. 目标 Spec 全文。
2. 必要时读取 `docs/plans/2026-05-29-a-stock-agent-picker-refactor-evaluation.md` 和 `docs/reviews/2026-05-29-claude-a-stock-agent-picker-evaluation.md` 作为背景。
3. 必要时读取 `CLAUDE.md` 中项目约束。

审查重点：
- 是否真正贯彻“不要倒着构建 AI Agent”：Agent 是否被放在确定性核心之后？
- 数据源治理是否足够具体，能转化为 registry、测试和验收？
- Phase 顺序是否工程上合理？是否应调整 Phase B/C/D/E/F？
- 验收标准是否可执行，还是过于口号化？
- 是否存在隐藏副作用授权：DB、cron、credentials、真实 Gemini、Telegram、Google Sheets、历史数据修复。
- “只做选股”的边界是否足够硬？是否还有自动交易/持仓/泛投研/多市场入口没有挡住？
- 从工程执行角度，下一步最小 landing change 应该是什么？

输出要求：
- 用中文。
- 先给结论：`APPROVE` / `APPROVE_WITH_REVISIONS` / `REQUEST_FIXES` / `BLOCKED`。
- 分成 Blocking / Important / Nice-to-have。
- 每条建议必须给出证据位置（章节名或可定位片段即可，不要虚构行号）、影响、建议修正。
- 最后给出“建议的下一步最小 landing change”，不超过 5 条。
- 请明确列出你检查了什么。
