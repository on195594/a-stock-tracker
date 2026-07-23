# Historical outcome shadow Phase 1 independent review

日期：2026-07-23
最终结论：`APPROVE_LANDING`
生产迁移：未执行、未授权

## 审查范围

- `a_stock_tracker/data/outcome_shadow.py`
- `a_stock_tracker/cli.py`
- `tests/test_outcome_shadow.py`
- `docs/specs/2026-07-23-outcome-shadow-phase1.md`
- 隔离证据：`backups/outcome-shadow-phase1-20260723-231345/`

最终有效证据为 `candidate-v4.db`、`candidate-v4-replay.db`、`reconciliation-v4.json/.md`、`manifest-v4.json`、`baseline-v2.json` 与 `benchmark-000300.json`；较早版本均已在 manifest 中标记为 superseded。

## 独立 Codex 复审

### 首轮完整复审

Verdict：`passed=true`

- P0：无
- P1：无
- P2：3项
  1. dry-run 应拒绝会被忽略的 candidate/benchmark 参数；
  2. report output 应限定 `.json`；
  3. benchmark snapshot 异常输入需补 fail-closed 测试。

以上三项均已修复。

### 修复后聚焦复审

Verdict：`passed=true`

- P0：无
- P1：无
- P2：1项——snapshot 测试应精确覆盖“备份后源库变化”。

该测试已修订为：先备份，再修改源库，断言 candidate prediction 与 shadow 仍保持备份时刻值；定向测试通过。

## 最终实现边界

- dry-run 仅通过 SQLite `mode=ro` 检查源库，并拒绝 artifact 参数。
- build 先创建一致 SQLite candidate snapshot，再从 candidate 读取 cohort、prediction 与股票行情。
- benchmark snapshot 严格校验 schema、source、symbol、fetched_at、rows、row、date、close、duplicate 和 relevant coverage；异常删除候选并停止。
- report 只读候选数据库，仅接受 `.json` 输出，并同时生成同名 `.md`。
- 三张 shadow 表均有 `BEFORE UPDATE/DELETE` immutable triggers。
- 不修改 `predictions`，不切换现有报告，不引入 QFQ/总回报，不执行 Phase 2。

## v4 候选证据

- Run：`outcome-shadow-a38b36b1092589bf7b0a6326`
- Manifest：`a38b36b1092589bf7b0a632651eaf6f1c5bb5277a2ec68e3ab4dba0f4005510b`
- Frozen events：1,776
- `computed_aligned`：1,767
- `missing_stock_entry`：9
- Observations：2,520
- Immutable triggers：6
- `PRAGMA quick_check`：`ok`
- v4/replay run、result hash、observation hash：全部一致
- Source impurity：0

## 生产零漂移

`tracker.db` 执行前后 SHA-256 均为：

```text
9cd881338248e8dd1e66070aadded789995974682fa085e66bebc067d0dc7c25
```

Phase 1 未向生产库创建 shadow 表，也未修改生产 outcome。

## 最终门禁

```text
pytest tests/test_outcome_shadow.py -q: 26 passed
pytest -q: 1095 passed
ruff check .: passed
ruff format --check .: 163 files already formatted
mypy: success, 153 source files
pip check: no broken requirements
git ls-files '*.sh' | bash -n: passed
git diff --check: passed
credential pattern scan: 0 findings
```

结论：Phase 1 满足 spec、隔离、不可变、可重放和生产零漂移要求，可本地提交；Phase 2 仍需单独授权。
