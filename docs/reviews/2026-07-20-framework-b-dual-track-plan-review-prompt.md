# Framework B 双轨修复方案独立工程审查

你是 Claude Code，只执行只读计划审查，不修改任何文件，不执行数据库写入。

## 项目

- 仓库：`/home/lin/a-stock-tracker`
- 当前分支工作树应为 clean。
- 必须遵守仓库 `CLAUDE.md`、`AGENTS.md`、`docs/architecture.md`。

## 已确认根因

当前 `a_stock_tracker/reporting/framework_b_report.py` 的 prospective B label 跟踪对每只候选调用 `_latest_framework_a_outcome_row()`，SQL 按 `score_date DESC` 取最新 A 记录。daily 每天新增 A 记录，因此到期日持续后移，closed_count 可能永远为 0。当前候选只有 7 条，即使冻结也无法单批达到 20。

数据库另有 73 条真实历史 B 记录，日期为 2026-04-29 至 2026-05-11，全部 30d 已结案，`weights_hash=adb88515`。最近倒查 20 条全部来自 2026-05-11，不能视为 20 个独立时间样本。

## 待审查方案

### 轨道 A：legacy retrospective

在 accuracy-report 中新增独立节，只读取 `framework='B'`、`outcome_30d IS NOT NULL`、与当前 B 权重一致的历史记录。输出样本数、股票数、日期数、30d 超额命中率、平均 alpha、按评分分位和 score_date 拆分。使用全部合格记录，不挑 20 条。必须标注 legacy-only，不计入 prospective 生产门禁。

### 轨道 B：冻结 prospective cohort

在 SQLite 新增 `framework_b_label_cohorts` 研究跟踪表，冻结：

- cohort_week / label_date
- code / name / industry
- source_a_prediction_id / source_a_score_date
- score_a / score_b / delta
- label / rule_name
- strong/moderate/light 阈值快照
- weights_hash / created_at

唯一约束：`cohort_week + code + rule_name + weights_hash`。accuracy-report 同周重复运行幂等；每周最多入组一次。只写该研究表，不写 B predictions，不修改历史预测。outcome 必须始终通过 `source_a_prediction_id` 读取，不再切换到最新 A 记录。

### Phase 6 门禁

- prospective 已结案 cohort ≥20；
- 至少覆盖 3 个 cohort_week；
- overdue outcome=0；
- legacy 不计入 20；
- 达标只允许“开始人工审阅”，不自动启用 B 生产写入。

### 执行与安全

实施前备份 `tracker.db`。测试使用 `tmp_path` 隔离。迁移真实数据库后运行一次 accuracy-report 写入首批 cohort。禁止修改 cron、`SUPPORTED_FRAMEWORKS`、weights、已有 predictions 不可变字段。

## 请重点审查

1. 方案是否真正修复滚动 latest-A 根因。
2. accuracy-report 从只读报告变为研究表写入是否合理；是否应拆为显式命令或采用其他持久化边界。
3. cohort 的主键、版本字段、阈值快照和 provenance 是否足够，是否缺少候选规则版本、输入快照或状态字段。
4. 每周重复同一股票是否造成伪样本；`20 条 + 3周` 门禁是否足够，应该怎样限制或披露相关性。
5. legacy 73 条与当前规则/阈值不可比时，允许输出哪些统计，禁止哪些结论。
6. SQLite 向后兼容迁移、事务、幂等、并发、备份和回滚风险。
7. 测试矩阵是否缺少关键失败路径。
8. 是否存在更小、更安全、可维护性更好的实现路径。

## 期望输出格式

- Verdict: `APPROVE | APPROVE_WITH_REVISIONS | REQUEST_CHANGES`
- Blocking findings: 编号列表，包含文件/函数锚点与具体修订要求；没有则写“无”
- Important notes: 编号列表
- Revised implementation contract: 可直接执行的修订后合同，明确 scope/non-scope、表结构/命令边界、门禁、测试、迁移/回滚
- Recommended execution order: 依赖顺序

不要写泛泛建议。请实际阅读相关源码、测试和项目约定后给出结论。
