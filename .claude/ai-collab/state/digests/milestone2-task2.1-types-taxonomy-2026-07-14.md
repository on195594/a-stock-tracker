# Digest: milestone2-task2.1-types-taxonomy (2026-07-14)

- Task: MILESTONE-002任务2.1 — qualitative_v2_types.py + qualitative_v2_taxonomy.py
- Commits: 84343cb(初版实现)→75b93d5(PM自查补input_hash版本敏感性测试)→ec5d2e9(codex复核后修复3处：ScoringResult缺dimensions包装/directness registry误禁context/effective_until presence-vs-null未文档化)
- PRE_HEAD: 9338f44 → Final HEAD: ec5d2e9
- QA: codex-self-review，0 Critical/3 Important/2 Minor，全部核实为真并修复，verdict reliable
- Verdict: DELIVERED（intent rubric，codex原判PARTIAL已因修复升级）
- Test count: 324 passed（较基线+25），mypy/ruff check/format全过
- Standing note: Evidence.effective_until的"字段缺失 vs 显式null"区分依赖task2.2 validator在原始dict/JSON上检查，不能等Evidence对象构造完再判断——已写入dataclass docstring提醒task2.2实现时注意
