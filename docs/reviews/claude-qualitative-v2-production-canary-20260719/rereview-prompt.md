# Claude production-canary fix rereview

Work read-only. Do not edit files, access the network, write any database, or run `pipeline.py daily`.

Rereview the uncommitted working-tree fixes made in response to your prior findings in `independent-review.md`:

- the P2 retired-authorization durability gap;
- the P3 unexpected-exception per-stock fallback gap.

Inspect the full working-tree diff from `HEAD` (`0e2adc0`) and the relevant surrounding implementation/tests. In particular, verify:

1. `qualitative_v2_production_authorizations.json` is a durable fail-closed control for the already exhausted authorization even if ignored artifacts are absent.
2. The collector checks the ledger before artifact creation, database opening, or CNINFO activity, while future active grants remain exact in ID/scope/attempt limit.
3. The widened exception boundary preserves v1 fallback without swallowing `BaseException` process controls or legacy-getter failures.
4. The new code does not introduce a bypass, contract drift, or production regression.

Available verification evidence from the primary agent:

- retired-ID CLI smoke: exit 2; requested artifact root absent;
- targeted tests: 16 passed;
- full tests: 907 passed;
- Ruff: all checks passed and 120 files formatted;
- mypy: no issues in 114 source files;
- live read-only smoke: mode=canary, canary_count=5, v2_rows=0, all_fallback=true;
- rollback smoke: mode=off.

Return exactly:

```text
VERDICT: PASS | CHANGES_REQUIRED

RESOLUTION CHECK
- P2: RESOLVED | UNRESOLVED — evidence
- P3: RESOLVED | UNRESOLVED — evidence

NEW FINDINGS
- [P0|P1|P2|P3] title — file:line
  Impact: ...
  Required fix: ...

RELEASE ASSESSMENT
- ...
```

If there are no new findings, write `- None`.
