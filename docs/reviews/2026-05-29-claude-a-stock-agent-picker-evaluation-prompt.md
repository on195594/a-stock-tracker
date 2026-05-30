# Claude 只读评审 Prompt：A 股选股 Agent 重构评估

你是独立工程评审。请只读评估 `/home/lin/a-stock-tracker` 中的分析文档：

- 主要评审对象：`docs/plans/2026-05-29-a-stock-agent-picker-refactor-evaluation.md`
- 可读上下文：`README.md`, `CLAUDE.md`, `docs/design.md`, `docs/evolution-roadmap.md`, `pipeline.py`, `scorer.py`, `gemini_scorer.py`, `lib/cache.py`, `lib/fetcher.py`, `weights.json`, `tests/`

## 只读边界

- 不要创建、修改、删除任何文件。
- 不要安装依赖。
- 不要运行会访问网络、写数据库、发通知、改 cron、改 `.env` 或 credentials 的命令。
- 可以阅读文件、搜索代码、基于已有内容推理。

## 评审目标

请评估这份分析是否成立，尤其是：

1. “不建议在 `/home/lin/a-stock-tracker` 原地大重构；建议新建绿地项目并只读复用旧项目”的结论是否合理？
2. 复用/弃用清单是否准确？有没有误判：例如哪些代码其实应继续原地演进，或哪些被建议复用的部分风险太大？
3. 是否遗漏关键风险：投资决策支持、数据口径、历史 DB、AKShare 限流、Gemini/LLM 边界、测试/类型/cron/通知、未提交工作区等。
4. Phase 0-5 的计划是否可执行、是否过度工程化、是否符合“只用于 A 股选股 Agent 应用”的边界？
5. 如果要继续，你建议的下一步是什么？只给最小可落地路径，不要泛泛列选项。

## 输出格式

请用中文输出，结构如下：

- Verdict：APPROVE / APPROVE_WITH_REVISIONS / REJECT
- 总体判断：3-6 句
- 支持原分析的证据：列出带路径/函数/文档位置的证据
- 需要修正或补充的点：按 Blocking / Important / Nice-to-have 分级
- 对“重构 vs 新建”的最终建议：明确选一个
- 最小下一步：不超过 5 条，必须可执行
- 你检查过的文件/范围

请引用具体路径和函数/章节名；不要只给抽象建议。
