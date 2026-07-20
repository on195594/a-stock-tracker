# Framework B 双轨修复 — Claude 只读代码审查

## Verdict: APPROVE_WITH_REVISIONS

核心根因（rolling latest-A 污染 prospective 门禁、accuracy-report 隐式写入）已消除，plan review 的
4 条 blocking finding 均已在代码中兑现且有测试锚定。剩余问题集中在事务健壮性与失败路径测试空白，
不阻塞当前 `2026-W30` 只读观察阶段,但在下一次 `framework-b-cohort-freeze` 批量写入（尤其未来跨
weights_hash 重跑）前应先补齐。

## Blocking findings

无。（原 plan review 的 4 条 blocking 均已核实修复，见"已验证的关键不变量"。）

## Important notes（非阻塞，建议合并前后补齐）

1. **`insert_freeze_payloads` 无显式事务边界，依赖“外层 close() 隐式回滚”而非显式 rollback**
   `a_stock_tracker/reporting/framework_b_cohort.py:149-193`：循环内逐条 `INSERT OR IGNORE`，只在
   循环结束后统一 `conn.commit()`，没有 `try/except: conn.rollback()`。
   - `UNIQUE` 冲突本身不会抛异常（`INSERT OR IGNORE` 静默跳过），真正会中途抛错的是非 UNIQUE 类
     错误（例如某个 payload 字段类型异常导致 `NOT NULL`/类型校验失败）。
   - 当前唯一调用方 `cmd_framework_b_cohort_freeze`（`cli.py:930-942`）用 `try/finally: db.close()`
     包裹，SQLite 连接在未 `COMMIT` 前关闭时，未提交的隐式事务不会持久化，因此当前入口**实际上不会
     残留部分写入**。但这是依赖 SQLite/`sqlite3` 模块“未提交事务随连接关闭而作废”的隐式行为，
     并非代码显式保证——如果未来新增第二个调用方复用已打开的长生命周期连接（例如测试或未来批处理
     脚本一次性处理多个 label_date 且不在每次调用后关闭连接），中途异常会让本次循环里已插入但未
     commit 的行滞留在同一事务里，被下一次调用的 `commit()` 意外提交。
   - 建议：`insert_freeze_payloads` 内部显式 `try: ... except Exception: conn.rollback(); raise`，
     不依赖调用方连接管理习惯。

2. **`PRAGMA foreign_keys=ON` 未设置，`source_a_prediction_id REFERENCES predictions(id)` 形同虚设**
   全仓 `a_stock_tracker/data/cache.py` 未见任何 `PRAGMA foreign_keys` 语句，`get_db()` 也未开启。
   SQLite 默认关闭外键约束强制执行，因此 schema 里的 `REFERENCES predictions(id)`
   （`cache.py:74-88`）目前只是文档性声明，允许插入指向不存在 `predictions.id` 的
   `source_a_prediction_id`。
   - 影响有限：`summarize_cohort_outcomes` 用 `LEFT JOIN predictions` 且 `COUNT(p.id)==0` 时标记
     `missing_source`（`framework_b_cohort.py:245-247`），门禁 `ready_for_manual_review` 要求
     `missing==0`（`framework_b_cohort.py:265`），所以即使外键不生效，"source 丢失"的场景在统计层
     仍会被拦截,不会静默通过门禁。但建表阶段本身没有数据库层兜底,是纵深防御缺口。
   - 建议：`get_db()` 内对该连接执行一次 `conn.execute("PRAGMA foreign_keys=ON")`，并补一条测试验证
     插入不存在的 `source_a_prediction_id` 时是数据库层拒绝而不仅是统计层标记。

3. **`get_fundamentals` 使用独立 `get_db()` 连接，与 dry-run 只读连接无关，但未被验证**
   `_candidate_input_snapshot`（`framework_b_cohort.py:71-83`）调用 `get_fundamentals(code)`
   （`cache.py:452-467`），该函数内部自己 `get_db()` 打开一个新的读写连接，与 CLI dry-run 传入的
   `mode=ro` 连接完全独立（`cli.py:933-935`）。已确认 `get_fundamentals` 本身只读（`SELECT` +
   `conn.close()`，无 `INSERT`/`UPDATE`），过期缓存直接返回 `None` 而不触发抓取或网络请求，因此
   dry-run 场景下不存在“只读连接下意外写入报错”的风险——此前探索报告中的担忧已排除，仅记录以避免
   重复排查。

4. **门禁字典合并 `readiness_summary = dict(framework_b_summary); readiness_summary.update(cohort_summary)`
   （`accuracy_report.py:464-465`）未发现 key 覆盖 bug**
   `append_framework_b_dry_run` 返回的 `candidate_count`/`scored_count`（用于 `b_ready`）与
   `append_framework_b_cohort_report` 返回的 `b_label_*` 系列键名不重叠，`update()` 只是叠加而非
   覆盖，`append_phase6_readiness`（`framework_b_report.py:718-825`）读到的两组字段来源清晰可追溯。
   之前探索阶段标记为"待确认"的疑点已排除。

5. **跨 `weights_hash` 重复冻结同一 `source_a_prediction_id` 时的门禁语义未在文档/测试中显式说明**
   `UNIQUE(source_a_prediction_id, weights_hash)`（`cache.py:98`）允许同一 source 在权重变更后被
   再次冻结产生第二条 cohort 行；`summarize_cohort_outcomes` 按 `source_a_prediction_id` 单独
   `GROUP BY`（不含 `weights_hash`），`closed`/`overdue`/`missing` 计数天然按唯一 source 去重，不
   会因为多权重版本重复计数——这一点是对的，但目前没有测试验证"同一 source 在两个不同
   weights_hash 下各冻结一次"时 `summarize_cohort_outcomes` 仍只输出一行统计。建议补一条测试固化
   这个不变量，避免未来有人误以为需要在 SQL 里加 `weights_hash` 到 GROUP BY 而引入回归。

## Verified strengths（已确认正确的关键不变量）

- **Plan review 4 条 blocking finding 全部兑现**：
  - accuracy-report 不再隐式写入；写入路径完全收敛到独立 CLI 子命令
    `framework-b-cohort-freeze`（`cli.py:1094-1096, 1115-1116, 930-942`），`ensure_framework_b_cohort_schema`
    只被 `insert_freeze_payloads` 调用，`get_db()` 从不触发建表（`cache.py:68-73` docstring +
    实测未被 `get_db()` 引用）。
  - 滚动 latest-A 预览与冻结 cohort 门禁物理区分：预览小节标题带 `[UNFROZEN-PREVIEW]` 前缀
    （`framework_b_report.py:524`），并显式声明"不参与门禁"（`framework_b_report.py:576`）；门禁
    只读 `b_label_*` 字段（来自 cohort 汇总），不读预览函数返回值。
  - cohort schema 快照完整（`rule_snapshot_json`/`threshold_snapshot_json`/
    `candidate_input_json`/`score_snapshot_json` 四个 JSON 列，`cache.py:88-93`），比 plan review 要求
    更完整。
  - 门禁按 `source_a_prediction_id` 去重（`summarize_cohort_outcomes` GROUP BY，
    `framework_b_cohort.py:236`），并有 `UNIQUE(source_a_prediction_id, weights_hash)` 数据库层兜底。
- **legacy 73 条与 prospective cohort 统计口径无混淆**：两套查询、两个报告小节、两组免责声明文案
  完全独立；`append_framework_b_legacy_report` 的返回值未被传入 `readiness_summary`。
- **ISO week 计算正确**：`_cohort_week` 用 `date.isocalendar().year`（ISO 年）而非公历年
  （`framework_b_cohort.py:66-68`），正确处理跨年周边界。
- **dry-run 双重防误触发**：业务逻辑层 `dry_run=True` 时跳过 `insert_freeze_payloads`
  （不建表不写），CLI 层额外用 `mode=ro` URI 只读连接兜底（`cli.py:933-935`），即使逻辑有疏漏也会
  被 SQLite 拒绝写入。
- **幂等性**：重跑同一周同 weights_hash 时 `INSERT OR IGNORE` + 双 UNIQUE 约束保证
  `inserted=0, skipped_existing=N`，已有测试 `test_freeze_is_idempotent_and_preserves_source_prediction`
  （`tests/test_framework_b_cohort.py:96-125`）覆盖。
- **overdue/日期计算**：全部用真实 `date` 对象运算（`date.fromisoformat` + `timedelta`），未发现字符串
  比较导致的边界错误；`SELECT ... score_date >= ?` 处的字符串比较因格式统一为 `YYYY-MM-DD` 属安全用法。
- **结构合规**：新文件 `framework_b_cohort.py` 落在既有 `reporting/` 目录下，未新增顶层目录/业务领域，
  未违反 `AGENTS.md`（组装根/依赖方向）与 `docs/architecture.md`（"reporting/ 不能成为真相来源"）的
  红线；`predictions` 表结构和 `SUPPORTED_FRAMEWORKS` 均未被本次改动触碰。

## Required test additions

1. `test_insert_freeze_payloads_rolls_back_on_mid_batch_failure`
   构造一批 payload，其中第 2 条故意违反非 UNIQUE 约束（如把某 NOT NULL 字段设为 `None`），断言
   异常抛出后表中**没有**残留第 1 条已插入的行（验证/固化 Important note 1 的修复）。
2. `test_freeze_rejects_dangling_source_prediction_id`（在补 `PRAGMA foreign_keys=ON` 后）
   直接调用 `insert_freeze_payloads` 传入不存在于 `predictions` 的 `source_a_prediction_id`，断言
   抛出 `sqlite3.IntegrityError` 而非静默插入成功。
3. `test_summarize_cohort_outcomes_marks_missing_source`
   冻结一条记录后物理删除对应 `predictions` 行，断言 `summarize_cohort_outcomes` 返回
   `missing_source_count==1` 且 `ready_for_manual_review is False`。
4. `test_summarize_cohort_outcomes_marks_overdue`
   构造 `source_a_score_date` 超过 30 天且 `outcome_30d IS NULL` 的记录，断言落入 `overdue` 分支
   （当前测试集完全没有覆盖这个真实存在的 `else: overdue += 1` 分支，
   `framework_b_cohort.py:253-256`）。
5. `test_cohort_report_handles_missing_table`
   在从未调用过 `framework-b-cohort-freeze` 的全新 db 上直接跑
   `append_framework_b_cohort_report`，断言输出"尚未创建 cohort 表"文案且不抛异常（该分支
   `cohort_table_exists() is False` 已被 `test_pipeline.py` 的
   `test_accuracy_report_separates_unfrozen_preview_from_prospective_gate` 间接覆盖，建议在
   `test_framework_b_cohort.py` 里补一条直接单测，减少对大型集成测试的依赖）。
6. `test_freeze_weekly_cohort_empty_candidates_returns_empty_result`
   `_build_freeze_payloads` 返回空列表（如 `threshold_data is None`）时，断言
   `freeze_weekly_cohort` 返回 `FreezeResult(candidate_count=0, inserted=0, skipped_existing=0)`
   而不是抛异常或建表。
7. `test_summarize_cohort_outcomes_dedupes_across_weights_hash_change`
   固化 Important note 5：同一 `source_a_prediction_id` 在两个不同 `weights_hash` 下各冻结一次后，
   断言 `summarize_cohort_outcomes` 仍只返回 1 行统计（而非 2 行）。

## Recommended patch order

1. Important note 1（`insert_freeze_payloads` 显式 `try/except: rollback`）——最小改动，直接消除
   事务假设上的脆弱性。
2. Required test 1（配套上一步的回归测试）。
3. Important note 2（`PRAGMA foreign_keys=ON`）+ Required test 2 ——需确认开启外键约束不会影响
   `predictions` 表既有的其它写入路径（写之前应跑一次全量 `pytest tests/ -v` 确认无隐藏的孤儿外键
   数据）。
4. Required test 3、4、5、6（失败路径测试补齐，无需改动生产代码，可独立并行进行）。
5. Required test 7 + 文档化 Important note 5（在 `docs/lessons-learned.md` 或
   `framework_b_cohort.py` docstring 里补一句"门禁按 source 去重，与 weights_hash 版本无关"的
   显式说明，防止未来误改 SQL）。

## 备注

- 本次审查为只读审查，未执行任何写入 DB 或修改文件的命令；`git status`/`pytest` 等验证命令均由
  执行方（非本次审查）自行运行并在协作记录中提供了 957 tests passed 的结果，本审查未重复运行。
- 未发现违反 `CLAUDE.md`（禁止裸 `raise Exception`、禁止 `print`、类型注解、`@dataclass` 优先等）
  编码规范的实例；`FreezeResult`/`FreezePayload` 均为 `@dataclass`（`framework_b_cohort.py` 顶部）。
