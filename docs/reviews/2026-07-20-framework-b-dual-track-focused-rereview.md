# Framework B 双轨修复 — 聚焦只读复核结论

本次为只读复核，不修改任何文件、不写数据库。以下是复核结论，无需执行（无代码变更）。

## Verdict: APPROVE

原 2026-07-20 code review 中列出的两条 Important notes（事务无显式回滚、`PRAGMA foreign_keys=ON` 未设置）均已在当前代码中修复，且 7 条 Required test 全部已补齐并与实现契约吻合。未发现新的 blocking 问题。

## 已验证的关键不变量

1. **`insert_freeze_payloads` 原子性**（`framework_b_cohort.py:149-199`）
   - `BEGIN IMMEDIATE` 显式开启事务，循环内逐条 `INSERT ... ON CONFLICT DO NOTHING`，仅在全部成功后 `commit()`；`except (sqlite3.Error, TypeError, ValueError, OverflowError): conn.rollback(); raise`。
   - SQLite 的 `ON CONFLICT DO NOTHING`（未指定冲突目标）对该表两条 UNIQUE 约束（`cache.py:97-98`）均生效，冲突时 `rowcount=0`，不抛异常，因此**不会被误吞** —— 唯一冲突走的是"跳过"路径，不进 except 分支；真正会进入 except 的是非 UNIQUE 类错误（FK 错误 `sqlite3.IntegrityError`、序列化错误 `TypeError`/`ValueError`/`OverflowError`），这些都会被捕获→rollback→**重新抛出**，不会被静默吞掉。
   - `test_insert_freeze_payloads_rolls_back_on_mid_batch_failure`（`tests/test_framework_b_cohort.py:212-235`）用第二条 payload 的 `score_snapshot={"not_json_serializable": {1,2}}` 触发 `json.dumps` 的 `TypeError`，断言异常抛出后表中 0 行，验证了"回滚是真实的、第一条已插入但未提交的行会被撤销"，而不是仅统计层规避。

2. **`PRAGMA foreign_keys=ON` 时机与作用域**（`cache.py:68-108`）
   - `ensure_framework_b_cohort_schema` 先建表建索引并 `commit()`，再在同一连接上执行 `PRAGMA foreign_keys=ON`（无未提交事务时设置，符合 SQLite 要求该 pragma 需在事务外生效的约束），随后 `insert_freeze_payloads` 才 `BEGIN IMMEDIATE`——时序正确，FK 检查会覆盖本次写入事务。
   - 作用域上，该 pragma 仅在 cohort 写入专用连接上设置：调用链是 `cmd_framework_b_cohort_freeze`（`cli.py:930-942`）→ 非 dry-run 时用独立 `get_db()` 连接，dry-run 用独立只读 URI 连接，用完即 `db.close()`；`accuracy-report`（`cli.py:920-927`）走完全不同的 `get_db()` 连接实例，且从不调用 `ensure_framework_b_cohort_schema`，因此该 pragma **不会泄漏到 accuracy-report 连接**，也不影响 `predictions`/`daily_bars` 等无 FK 声明的表（PRAGMA 对无 REFERENCES 的表本就是 no-op）。
   - `test_freeze_rejects_dangling_source_prediction_id`（`tests/test_framework_b_cohort.py:238-248`）用不存在的 `source_a_prediction_id=999999` 断言抛出 `sqlite3.IntegrityError`，证明 FK 约束是数据库层真实生效，不只是统计层兜底。

3. **新增失败路径测试与合同的匹配度**（`tests/test_framework_b_cohort.py`）
   - `test_insert_freeze_payloads_rolls_back_on_mid_batch_failure`、`test_freeze_rejects_dangling_source_prediction_id`、`test_summarize_cohort_outcomes_marks_missing_source`、`test_summarize_cohort_outcomes_marks_overdue`、`test_cohort_report_handles_missing_table`、`test_freeze_weekly_cohort_empty_candidates_returns_empty_result`、`test_summarize_cohort_outcomes_dedupes_across_weights_hash_change` 七条测试与原 review 提出的 Required test 1-7 一一对应，断言内容（行数、字段、异常类型）与实现逻辑吻合，非表面覆盖。
   - `test_summarize_cohort_outcomes_marks_missing_source` 中在 `DELETE FROM predictions` 前显式 `PRAGMA foreign_keys=OFF`，说明测试作者清楚意识到 FK 已生效会阻止该删除，测试设计与实现认知一致。

4. **empty candidates 不建表**（`framework_b_cohort.py:211-213`）
   - `freeze_weekly_cohort` 在 `payloads` 为空时提前 `return`，从未调用 `insert_freeze_payloads`（即从未调用 `ensure_framework_b_cohort_schema`），`test_freeze_weekly_cohort_empty_candidates_returns_empty_result` 断言 `cohort_table_exists(conn) is False` 验证了这一点。

5. **跨 `weights_hash` 去重**（`framework_b_cohort.py:236-244`, `cache.py:98`）
   - `UNIQUE(source_a_prediction_id, weights_hash)` 允许同一 source 在不同权重版本下各插入一行，但 `summarize_cohort_outcomes` 按 `source_a_prediction_id` 单独 `GROUP BY`（不含 `weights_hash`），因此门禁统计天然按 source 去重。`test_summarize_cohort_outcomes_dedupes_across_weights_hash_change` 直接构造两个 hash 各冻结一次、验证表内 2 行但 `summary.sample_count==1`，固化了这个不变量，防止未来误改 SQL 引入回归。

## Important notes（非阻塞）

- 原 review note 3（`get_fundamentals` 用独立只读连接，dry-run 场景无风险）与 note 4（`readiness_summary` 字典合并无 key 覆盖）均已在原审查中确认无问题，本次未发现新证据推翻这两条结论。
- 执行方报告的 964 tests passed / Ruff / mypy / diff-check 通过，以及真实 DB 同周重跑 `inserted=0 skipped=7`、`predictions` hash 不变、`foreign_key_check`/`integrity_check` ok，与代码静态分析结论一致，本次复核未重复运行这些命令（保持只读）。

## 未发现新增 blocking 问题

无。原 review 的 2 条 Important notes 已通过代码修改 + 测试固化解决；未在本次聚焦复核范围内发现回归或新缺口。
