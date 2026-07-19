# Claude independent production review

You are the independent production reviewer for the `a-stock-tracker` repository. Work read-only: do not edit files, do not run network calls, do not invoke Gemini/CNINFO, do not write `tracker.db`, and do not run `pipeline.py daily`.

Review the production-canary change range:

- base: `ac228e002e1aa464704f643bde9cd5e533e4fa6d`
- head: `0e2adc0e334138dc0a77468967f4975dec6484dc`
- implementation commits: `9d60067`, `ebab8aa`
- activation documentation commit: `0e2adc0`

The intended current state is deliberately fallback-only:

- local production `.env` sets `QUALITATIVE_V2_MODE=canary` but must never be printed or committed;
- exactly five configured canary stocks are eligible for the v2 read path;
- `qualitative_scores_v2` currently contains zero rows, so all five must use the legacy v1 score;
- `QUALITATIVE_V2_MODE=off` must provide immediate rollback;
- `daily` must never call the v2 Gemini client or collect evidence;
- the CNINFO authorization is exhausted at 45/45 HTTP attempts and must not be reused;
- no historical prediction may be changed;
- no source-grounded v2 score is claimed to be adopted yet.

Inspect the entire diff and relevant surrounding code/tests, especially:

- `qualitative_v2_production.py`
- `qualitative_v2_production_contexts.py`
- `scripts/run_qualitative_v2_production.py`
- `scripts/collect_qualitative_v2_production_contexts.py`
- `pipeline.py`, `lib/cache.py`, `config.py`
- production tests, runbook, execution report, and configuration loading

Evaluate these production properties:

1. Fail-closed per-stock fallback under missing, stale, invalid, corrupted, or identity-drifted v2 data.
2. Whether any unvalidated/partially validated record can enter or be consumed from `qualitative_scores_v2`.
3. Artifact sealing, create-only behavior, replay/duplicate handling, transactions, concurrency, and partial failures.
4. Credential timing and leakage, network-call limits, authorization reuse, path/symlink/traversal boundaries, and database isolation.
5. Correctness of canary/off/on selection and the real pipeline integration, including whether an invalid mode can break the whole daily run.
6. Whether the activation claims are supported by code and observable local state without reading secret values.
7. Test gaps that could hide a production-impacting defect.

Do not focus on style unless it creates operational risk. Do not require the research M4/M5 gates as a prerequisite for this fallback-only canary seam.

Return exactly this structure:

```text
VERDICT: PASS | CHANGES_REQUIRED

FINDINGS
- [P0|P1|P2|P3] <title> — <file:line>
  Impact: ...
  Evidence: ...
  Required fix: ...

VALIDATED GUARANTEES
- ...

VERIFICATION RECOMMENDATIONS
- ...
```

List only actionable findings introduced or exposed by this change range. If there are no findings, write `- None` under `FINDINGS`.
