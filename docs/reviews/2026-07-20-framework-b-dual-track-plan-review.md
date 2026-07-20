# Framework B 双轨修复方案 — 独立只读审查

## Context
`a_stock_tracker/reporting/framework_b_report.py` 的 B label outcome 追踪
(`_latest_framework_a_outcome_row`, `_append_framework_b_label_outcome_tracking`)
按 `score_date DESC` 取每只候选股票**最新** A 记录的 outcome，而不是绑定某次特定
打分。daily 每天新增 A 记录，`due_date = score_date + 30d` 随之逐日后移，
`closed_count` 永远接近 0，Phase 6 门禁（≥20 已结案）事实上不可达。历史遗留
73 条真实 B predictions（2026-04-29～05-11，`weights_hash=adb88515`）全部已
结案但集中于 2026-05-11 单日，不能当作 20 个独立时间样本。用户提出的方案分两轨：
A) legacy retrospective 只读报告；B) 新增 `framework_b_label_cohorts` 表冻结
prospective cohort，outcome 通过 `source_a_prediction_id` 固定读取。本审查评估
该方案是否真正修复根因、写入边界是否合理、schema/门禁/测试是否足够。

## Verdict: APPROVE_WITH_REVISIONS

## Blocking findings

1. **`accuracy-report` 从纯读命令变成隐式写入命令，违反最小惊讶原则，且与仓库
   "SQLite 是真相来源" 的红线摩擦** — `a_stock_tracker/reporting/accuracy_report.py:449-456`
   `_append_framework_b_sections` 当前是 100% 只读路径（被
   `append_framework_b_dry_run` / `append_framework_b_quality_expansion` /
   `append_phase6_readiness` 调用，均只 `SELECT`）。若直接在这条路径里插入
   `INSERT INTO framework_b_label_cohorts`，`pipeline.py accuracy-report`
   会从一个可以随意重跑、无副作用的诊断命令，变成一个会修改数据库状态、
   且**幂等性依赖新表唯一约束**的命令。这类命令语义变化必须显式（新增独立
   子命令），不能靠读同一入口的人自己发现"这次调用会写库"。
   **修订要求**：新增独立 CLI 子命令（如 `pipeline.py framework-b-cohort-freeze`），
   `accuracy-report` 保持纯读；cohort 入库逻辑放在
   `a_stock_tracker/reporting/framework_b_cohort.py`（新文件），不复用
   `append_framework_b_quality_expansion` 内部状态之外的隐式触发。

2. **`_latest_framework_a_outcome_row` 的滚动查询逻辑未被替换，只是被绕开**
   — 方案里"outcome 必须始终通过 `source_a_prediction_id` 读取"只约束了新的
   cohort 表读取路径，但 `framework_b_report.py:445-453`
   （`_latest_framework_a_outcome_row`）和它在
   `_append_framework_b_label_outcome_tracking:549`（`latest = _latest_framework_a_outcome_row(db, row["code"])`）
   的调用点原样保留，继续用于**同一份报告**里的 "B provisional label outcome
   tracking" 小节。也就是说双轨修复后，accuracy-report 输出里会同时存在：
   一段用滚动 latest-A（根因未修）的旧统计，和一段用冻结 cohort
   （根因已修）的新统计，二者字段名/口径接近但含义不同，极易被后续读者
   误用旧的当新的。
   **修订要求**：轨道 B 上线后，`_append_framework_b_label_outcome_tracking`
   要么删除，要么明确改造为"仅展示尚未入 cohort 的候选、并注明其 outcome
   口径仍是滚动 latest-A、不可解释命中率"，标题和字段名必须与冻结 cohort
   的输出物理区分（例如前缀 `[UNFROZEN-PREVIEW]`），不能共用
   "B provisional label outcome tracking" 这个标题。

3. **cohort 表 schema 缺少候选规则版本、阈值来源的可追溯字段，`rule_name`
   不足以复现** — 方案列的字段里 `rule_name`（conservative/base/loose）只是
   `FRAMEWORK_B_QUALITY_RULES` 的 key，而这个字典的阈值本身可能被后续改动
   （`framework_b_report.py:44-63`）。如果未来有人调整 `base` 规则的
   `roe_3y_avg_min`，旧 cohort 行的 `rule_name="base"` 会和新代码里的 `base`
   语义不一致，且冻结表里没有存当时的实际阈值数值，只有"阈值快照"字样但
   规格里没定义具体列。
   **修订要求**：新增 `rule_snapshot_json`（存 `FRAMEWORK_B_QUALITY_RULES[rule_name]`
   在写入当下的完整字典）而不是只存 `rule_name` 字符串；`label` 判定用的
   strong/moderate/light 数值阈值（`_framework_b_provisional_threshold_data`
   的输出）也要整体快照，不能只存最终 label 字符串。

4. **唯一约束 `cohort_week + code + rule_name + weights_hash` 不能防止"同一
   source_a_prediction_id 重复入组"，也不能防止跨规则重复计入同一只股票**
   — 若某股票同时满足 conservative 和 base 两条规则，会以两条不同
   `rule_name` 的行入组，`total_closed`（跨 rule_name 汇总）会把同一只股票
   算两次；Phase 6 门禁"≥20 cohort 且覆盖 ≥3 cohort_week"若不按
   `source_a_prediction_id` 去重统计，就存在同一 A 记录被多条 cohort 行
   重复计数、虚增独立样本数的风险。
   **修订要求**：门禁计算 `closed_count` 时必须先按 `source_a_prediction_id`
   去重（或唯一约束里去掉 `rule_name`，改成"每个 source_a_prediction_id
   只能属于它当周入选的最高优先级一条规则"），并在 cohort 表加
   `status` 列（如 `active` / `superseded_by_rule_change`）为未来阈值调整后
   的失效标记留位置。

## Important notes

5. `每周最多入组一次` 的幂等靠唯一约束隐式保证，建议在写入函数里显式先
   `SELECT` 检查是否已存在同 `cohort_week+code+rule_name+weights_hash` 再插，
   而不是依赖 `INSERT` 抛 `IntegrityError` 后吞掉异常 —
   仓库红线禁止裸 `except Exception`，需要用
   `sqlite3.IntegrityError` 精确捕获或者用 `INSERT OR IGNORE`
   （后者更安全，但要确认没有其它列需要 upsert 更新）。

6. legacy 73 条历史记录（轨道 A）应明确标注："样本集中于单一交易日
   （2026-05-11），horizon 30d 之间存在高度相关的市场系统性风险敞口，
   不能视为 73 个独立样本"；报告里禁止直接给出"命中率 XX%"这种单一数字
   而不带这条免责声明，否则等同于制造虚假置信度，与仓库
   `accuracy-report：记录 < 100 条时头部必须有样本不足警告 + 选择性偏差
   免责声明` 的既有红线是同一类问题，需要复用/对齐该红线的措辞。

7. 测试矩阵目前完全没有覆盖 `_latest_framework_a_outcome_row` 滚动语义、
   `_outcome_status`、`_append_framework_b_label_outcome_tracking`
   （见 `tests/test_framework_b_report.py`，155 行全部只测
   `_has_roe_trend_warning` 和 `_score_framework_b_candidate`，未涉及 outcome
   tracking 分支）。新增 cohort 写入前必须先为现有 outcome 状态机
   （`_outcome_status` 的 4 个分支：closed / no_a_record /
   future_closeable / missing_after_due）补齐单测，否则无法验证"冻结后
   outcome 不再随 daily 新增记录漂移"这一核心修复目标是否真的生效。

8. 备份 `tracker.db` 属于"直接执行的破坏性/生产相关操作"，按仓库
   CLAUDE.md 需要用户在执行前显式确认时间点和备份路径，不能作为实现细节
   一笔带过；备份文件本身也要考虑是否落入 `.gitignore`（当前
   `tracker.db` 应该已被忽略，需要核实备份文件命名不会被误 `git add`）。

## Revised implementation contract

**Scope**
- 新文件 `a_stock_tracker/reporting/framework_b_cohort.py`：
  - `freeze_weekly_cohort(db, weights, cohort_week: str) -> dict`：读取当周
    quality candidates（复用 `_collect_framework_b_quality_candidates` /
    `_pick_framework_b_quality_rule` 等既有纯函数，不改动它们的签名和行为），
    对每个候选按 `INSERT OR IGNORE` 写入 `framework_b_label_cohorts`，
    显式先 `SELECT` 判断是否已存在同周同股同规则同 hash 记录再决定跳过，
    返回 `{inserted, skipped_existing}` 计数。
  - `report_cohort_outcomes(db) -> list[str]`：只读，按
    `source_a_prediction_id` join `predictions.id` 取 outcome_30d，
    输出去重后的 closed_count / cohort_week 覆盖数 / overdue 数，供
    Phase 6 readiness 使用。
- `a_stock_tracker/data/cache.py::get_db()` 新增
  `CREATE TABLE IF NOT EXISTS framework_b_label_cohorts`：
  ```sql
  CREATE TABLE IF NOT EXISTS framework_b_label_cohorts (
      id                      INTEGER PRIMARY KEY AUTOINCREMENT,
      cohort_week             TEXT NOT NULL,
      label_date              TEXT NOT NULL,
      code                    TEXT NOT NULL,
      name                    TEXT,
      industry                TEXT,
      source_a_prediction_id  INTEGER NOT NULL REFERENCES predictions(id),
      source_a_score_date     TEXT NOT NULL,
      score_a                 REAL,
      score_b                 REAL,
      delta                   REAL,
      label                   TEXT NOT NULL,
      rule_name               TEXT NOT NULL,
      rule_snapshot_json      TEXT NOT NULL,
      threshold_snapshot_json TEXT NOT NULL,
      weights_hash            TEXT NOT NULL,
      status                  TEXT NOT NULL DEFAULT 'active',
      created_at              TEXT NOT NULL,
      UNIQUE(cohort_week, code, rule_name, weights_hash)
  )
  ```
  （新增列全部通过 `_ensure_columns` 迁移路径，不破坏既有表；DDL 只
  `CREATE TABLE IF NOT EXISTS`，符合仓库现有迁移风格。）
- 新 CLI 子命令（`a_stock_tracker/cli.py` 或 `pipeline.py` 视现有子命令
  注册方式）：`framework-b-cohort-freeze`，显式调用
  `freeze_weekly_cohort`；**不放进 `accuracy-report` 的默认执行路径**。
- `accuracy_report.py::_append_framework_b_sections`：新增只读小节
  （调用 `report_cohort_outcomes`），标题明确区分于旧的
  "B provisional label outcome tracking"（finding 2）。

**Non-scope（本次禁止触碰）**
- `_latest_framework_a_outcome_row` 的滚动查询语义可以保留用于
  "未入 cohort 的候选预览"，但不得再被当作 Phase 6 门禁数据来源。
- 不修改 `SUPPORTED_FRAMEWORKS`、`weights.json`、cron 配置。
- 不写入 `predictions` 表，不修改任何已有 predictions 行。
- `score_stock` / `scoring.py` 打分逻辑不变。

**门禁（Phase 6 "可以开始人工审阅"）**
- 按 `source_a_prediction_id` 去重后 closed cohort ≥ 20；
- 覆盖 ≥3 个不同 `cohort_week`；
- overdue（到期未结案）cohort = 0；
- legacy 73 条永远不计入这个 20，且轨道 A 报告必须带 finding 6 的免责声明；
- 达标只解锁"开始人工审阅"文案，不解锁生产写入，不新增自动触发逻辑。

**测试**
- `tests/test_framework_b_cohort.py`（新文件，`tmp_path` 隔离数据库，
  仿照 `tests/test_framework_b_report.py` 现有风格）：
  1. 首次 freeze 插入预期行数，字段值正确（含 `rule_snapshot_json` 可
     反序列化回原字典）。
  2. 同周同规则重复调用 `freeze_weekly_cohort` → `inserted=0`，
     `skipped_existing=候选数`（幂等）。
  3. `weights_hash` 变化后再次同周 freeze → 允许新增（不同 hash 不冲突）。
  4. `report_cohort_outcomes` 对同一 `source_a_prediction_id` 出现在
     两条不同 `rule_name` cohort 行时，closed_count 去重后只计 1。
  5. `_outcome_status` 四分支（closed/no_a_record/future_closeable/
     missing_after_due）单测（当前完全缺失，见 finding 7）。
  6. overdue cohort 存在时门禁判定为 WAIT，且给出具体清单（复用现有
     `overdue_risks` 展示逻辑思路）。
  7. legacy 轨道报告断言：73 条记录聚合到单一交易日时输出中必须包含
     免责声明关键字（防止 regression 悄悄删除该文案）。
- 迁移测试：对一个已存在旧 schema（无 `framework_b_label_cohorts` 表）
  的 `tmp_path` sqlite 文件调用 `get_db()`，断言新表被正确创建且不影响
  已有 `predictions` 数据。

**迁移 / 回滚**
- 迁移前：用户显式确认后，`cp tracker.db tracker.db.bak-$(date +%Y%m%d)`
  （人工执行，不写入自动化脚本里静默做）。
- 首次生产 freeze：先在 `--dry-run` 模式下打印将要插入的行数和内容，
  用户确认后再执行真实 `INSERT`。
- 回滚：`DROP TABLE framework_b_label_cohorts` 是安全回滚（不触碰
  `predictions`），但仍需用户显式确认后执行，且执行前再次备份当前
  `tracker.db`。

## Recommended execution order

1. 先补 finding 7 的单测基线（`_outcome_status` 等纯函数），验证现状
   行为，作为后续改动的回归基准。
2. `cache.py` 新增 `framework_b_label_cohorts` DDL + 迁移测试。
3. `framework_b_cohort.py` 实现 `freeze_weekly_cohort` /
   `report_cohort_outcomes` + 单测（含幂等、去重、rule_snapshot 三项）。
4. 新增独立 CLI 子命令，接入 `--dry-run`。
5. `accuracy_report.py` 接入只读 cohort outcome 小节，同时按 finding 2
   改造/隔离旧的 "B provisional label outcome tracking" 小节。
6. legacy 轨道 A 报告 + finding 6 免责声明。
7. 人工确认后备份数据库，执行首次真实 freeze，观察一个 cohort_week
   周期后再评估 Phase 6 门禁进度。
