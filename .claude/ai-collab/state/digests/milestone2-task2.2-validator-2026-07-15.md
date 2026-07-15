# Digest: milestone2-task2.2-validator (2026-07-15)

- Task: MILESTONE-002任务2.2 — qualitative_v2_validator.py（本地HTTP无关validator）
- Commits: 46d33c6(初版实现)→1cbaf0a(codex复核后修复1 Critical+3 Important+2 Minor)
- PRE_HEAD: 5394fc0 → Final HEAD: 1cbaf0a
- QA: codex-self-review，1 Critical(freshness_policy未与claim_category规范值比对，可让市场情绪等30天窗口证据伪装成550天窗口从而"合法地"过期未被拒绝)/3 Important(evidence_id类型未强校验/多处frozenset成员测试对unhashable值抛TypeError而非fail-closed/validate_model_output信任未经validate_context_dict校验的手工构造context)/2 Minor(rationale空字符串通过/unit空字符串通过)，全部核实为真并修复，verdict reliable
- Verdict: DELIVERED（intent rubric，codex原判PARTIAL已因修复升级）
- Test count: 367 passed（较基线+43），mypy/ruff check/format全过
- Standing note: _recompute_is_fresh()是本次新增的纵深防御设计——citation时永远用canonical policy重新计算新鲜度，不信任Evidence.freshness_status/freshness_policy存储值，同时关闭了"声明宽松policy"和"绕过validate_context_dict直接构造context"两个攻击面
