# Framework B 双轨修复 — Claude 只读代码审查

你是独立代码审查者。只读审查当前 `/home/lin/a-stock-tracker` 工作树，不修改文件、不执行数据库写入、不运行会改变项目状态的命令。

## 背景与目标

旧版 B label outcome tracking 每次按代码选择最新 A prediction，daily 持续新增记录导致 due date 每天后移、closed_count 长期为 0。修复目标是：

1. 轨道 A：全部真实历史 B 记录只作 legacy retrospective，不计入 prospective 门禁。
2. 轨道 B：显式按周冻结 prospective cohort，固定绑定 `source_a_prediction_id`，后续只读该 prediction 的 outcome。
3. `accuracy-report` 不隐式创建 cohort 表、不插入 cohort；写入只能通过显式 CLI `framework-b-cohort-freeze`。
4. 门禁要求去重后 closed≥20、closed cohort weeks≥3、overdue=0、missing source=0；达标只允许人工审阅，不自动启用 B 生产写入。

## 当前实际状态

- 真实 DB 已备份，备份与写入前 DB SHA-256 一致，完整性 `ok`。
- 首批 `2026-W30` cohort 已显式写入 7 条，7 个不同 `source_a_prediction_id`，status 均 active。
- 正式命令同周重跑：`inserted=0, skipped_existing=7`。
- predictions 全表 dump SHA-256 写入前后完全一致；行数保持 1894。
- accuracy-report 当前：legacy 73 条；prospective 7 条，closed 0/20，closed weeks 0/3，future 7，overdue 0，最早 2026-08-19。
- 本地验证：957 tests passed；Ruff、format、mypy、`git diff --check` 均通过。

## 必须阅读的文件

- `CLAUDE.md`
- `AGENTS.md`
- `docs/architecture.md`
- `a_stock_tracker/data/cache.py`
- `a_stock_tracker/reporting/framework_b_cohort.py`
- `a_stock_tracker/reporting/framework_b_report.py`
- `a_stock_tracker/reporting/accuracy_report.py`
- `a_stock_tracker/cli.py`
- `tests/test_framework_b_cohort.py`
- `tests/test_framework_b_report.py`
- `tests/test_pipeline.py` 中 Framework B / Phase 6 相关测试
- `docs/evolution-roadmap.md` 中 Phase 6
- `docs/lessons-learned.md` 中 C-4/C-5
- `docs/reviews/2026-07-20-framework-b-dual-track-plan-review.md`

## 重点检查

1. 根因是否真正消除：Phase 6 门禁是否完全不再依赖 rolling latest-A。
2. dry-run 是否真正不建表、不写 DB；正式 freeze 是否事务安全、幂等、失败时不会留下部分 cohort。
3. schema 唯一约束、source 去重、weights/rule/threshold/input/score provenance 是否正确。
4. 同一股票跨周重复入组和同一 source 复用的相关性/约束是否合理。
5. `source_a_prediction_id` 丢失、outcome overdue、invalid date、表不存在、空 cohort 等失败路径。
6. legacy 73 条统计是否存在错误可比性、选择偏差或过度结论。
7. Phase 6 readiness 文案是否存在逻辑漏洞，例如满足 20 条但不足 3 周、overdue、missing source 时误解锁。
8. CLI 参数、日期/ISO week 边界、SQLite 并发和 FK 行为。
9. 测试是否真正覆盖核心失败路径，是否有测试只验证实现而未验证合同。
10. 是否违反项目结构、日志、类型注解、异常和数据库安全红线。

## 输出格式

- **Verdict:** `APPROVE | APPROVE_WITH_REVISIONS | REQUEST_CHANGES`
- **Blocking findings:** 按严重度列出，每项包含文件:行/函数、故障场景、修复要求；没有写“无”
- **Important notes:** 非阻塞项
- **Verified strengths:** 已确认正确的关键不变量
- **Required test additions:** 明确测试名和断言
- **Recommended patch order:** 最小依赖顺序

不要泛泛总结。所有结论必须锚定当前文件内容；区分“确定缺陷”和“可选优化”。
