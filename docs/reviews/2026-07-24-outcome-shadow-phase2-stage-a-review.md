# Historical outcome shadow Phase 2 阶段 A 独立复审

日期：2026-07-24
结论：`APPROVE_LANDING`
生产迁移：未执行、未授权

## 范围

- `a_stock_tracker/data/outcome_shadow_migration.py`
- `a_stock_tracker/cli.py`
- `tests/test_outcome_shadow_migration.py`
- `docs/specs/2026-07-24-outcome-shadow-phase2.md`
- 演练证据：`backups/outcome-shadow-phase2-20260724-002120/`

## 计划审查

初始迁移计划结论为 `REQUEST_CHANGES`。采纳的有效修订包括：

- 迁移工具/spec 必须先提交，生产执行另行授权。
- SQLite online backup 以逻辑一致性为硬门禁，字节 SHA 仅作辅助证据。
- Candidate 与 production 使用独立连接；candidate 只读，production 单事务写入。
- 定义 absent/already_present/partial/diverged 四态。
- 三表六触发器必须完整，partial/diverged fail-closed。
- drop-only rollback 必须在副本完成演练并验证 non-shadow 零漂移。

“当前没有 Phase 2 实现”属于阶段状态而非既有代码缺陷，已通过本阶段实现闭环。

## 实现复审

首轮实现复审：`passed=true`，P0/P1 为零，提出两项 P2：

1. revert inspect 不应要求 evidence 路径；
2. 数据库提交后 evidence 落盘失败必须明确报告实际写入状态。

修复后聚焦复审：`passed=true`，无 P0/P1/P2，结论 `APPROVE_LANDING`。

最终行为：

- revert inspect 不校验、不创建 evidence 路径；仅 apply 要求 evidence。
- `MigrationEvidenceError` 携带 `result`、`evidence_path`、`write_performed`，错误文本包含 status/run/path。
- imported/reverted 的证据失败标记 `write_performed=true`；already_present 标记 false。
- 注入 evidence 磁盘失败后，inspect 可确认真实状态为 already_present。

## 原子性与备份

- Candidate 使用 SQLite `mode=ro`，验证 schema、run、manifest、计数、来源、保护 hash、result/observation hash 与读取前后文件 SHA。
- Apply 先生成并验证 online backup。
- Production 使用短 busy timeout、`BEGIN IMMEDIATE`、no retry。
- 获得写锁后再次将 production non-shadow 逻辑快照与 backup 比对；若备份后发生写入，返回 `PRODUCTION_CHANGED_AFTER_BACKUP`。
- 单事务创建三表六触发器，按 runs → observations → results 导入并事务内复验；失败整体 rollback。
- Drop-only revert 仅允许唯一 expected run/manifest，并在事务内验证 non-shadow 零漂移。

## 最终副本演练

证据目录：

```text
backups/outcome-shadow-phase2-20260724-002120/
```

状态序列：

```text
absent → imported → already_present → reverted
```

演练结果：

- Run：`outcome-shadow-a38b36b1092589bf7b0a6326`
- Manifest：`a38b36b1092589bf7b0a632651eaf6f1c5bb5277a2ec68e3ab4dba0f4005510b`
- Results：1,776
- Observations：2,520
- 回滚后 rehearsal：`quick_check=ok`、1999 predictions、0 shadow objects
- 生产：`quick_check=ok`、1999 predictions、0 shadow objects

## 最终门禁

```text
pytest tests/test_outcome_shadow_migration.py -q: 17 passed
pytest -q: 1112 passed
ruff check .: passed
ruff format --check .: 165 files already formatted
mypy: success, 155 source files
pip check: no broken requirements
shell syntax: passed
git diff --check: passed
credential scan: 0 findings
```

生产 `tracker.db` SHA-256 保持：

```text
9cd881338248e8dd1e66070aadded789995974682fa085e66bebc067d0dc7c25
```

## 结论

Phase 2 阶段 A 满足 spec、TDD、备份、原子导入、幂等、回滚演练和独立复审门禁，可以提交迁移工具。该提交不是生产迁移授权；阶段 B 必须再次获得用户明确批准。
