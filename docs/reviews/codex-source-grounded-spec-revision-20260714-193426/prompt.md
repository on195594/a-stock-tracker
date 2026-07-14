# Codex task: bounded revision of qualitative-scoring Spec

Workdir: `/home/lin/a-stock-tracker`

## Goal

Revise exactly one file:

`docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md`

Apply the accepted findings from the first AGY engineering review while preserving the grounding-first, fail-closed, shadow-first architecture.

## Allowed write scope

Only the target Spec above may be modified.

## Forbidden writes and side effects

Do not modify or create any other project file, including:

- `docs/project-status.md`
- review evidence under `docs/reviews/`
- `gemini_scorer.py`, `pipeline.py`, `lib/cache.py`
- tests, requirements, `.env`, SQLite DB, weights, logs, cron, Telegram files
- git index, commits, branches, stash or remotes

Do not run real Gemini/API/network calls. Do not install dependencies. Do not run commands that write cache, logs, DBs, generated artifacts or formatting changes outside the target Spec. Preserve all unrelated dirty worktree changes exactly.

## Source evidence to read

Read these files before editing:

1. Target Spec:
   `docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md`
2. First AGY review:
   `docs/reviews/agy-source-grounded-qualitative-spec-20260714-192539/agy_stdout.txt`
3. Review prompt/snapshot if needed:
   `docs/reviews/agy-source-grounded-qualitative-spec-20260714-192539/review-snapshot.md`
4. Project rules:
   `CLAUDE.md`

## Required revisions

### 1. Preserve `integer | null`

Do not accept AGY's claim that Gemini 2.5 Flash cannot express nullable JSON Schema fields. Parent verification against the current official Gemini `generateContent` structured-output documentation confirmed:

- Gemini 2.5 Flash supports structured output.
- `null` is supported by including `"null"` in the type array, e.g. `{"type": ["integer", "null"]}`.

Keep the stable object shape where `score` is always present and is `null` for `insufficient_data`.

Add an explicit official documentation citation and record this as an implementation-time doc recheck/smoke assumption, not an eternal API guarantee:

`https://ai.google.dev/gemini-api/docs/generate-content/structured-output`

Do not migrate to Interactions API in this Spec.

### 2. Add explicit Evidence taxonomy

Extend the evidence contract with machine-checkable fields at minimum:

- `evidence_type`
- `allowed_dimensions`
- `directness`
- `freshness_policy`

Keep `evidence_id`, value/unit/source/source_date/freshness status as appropriate.

Define explicit enums or bounded vocabularies sufficient for fixture-first implementation. Include:

- evidence ID namespace conventions for readability, such as `fundamentals.*`, `valuation.*`, `competitive_advantage.*`, `industry.*`, `announcement.*`, `news.*`;
- validator logic based primarily on structured taxonomy fields, not string-prefix parsing;
- allowed dimension names (`moat`, `market_pos`, `sentiment`);
- directness values and semantics;
- freshness policy representation and how deterministic validation evaluates it;
- representative evidence-type → allowed-dimension rules;
- rejection of unknown evidence types, unknown dimensions, contradictory taxonomy, and out-of-policy freshness.

Avoid pretending subjective evidence sufficiency is fully deterministic. Separate deterministic contract validation from rubric/human judgment.

### 3. Clarify fixture versus real shadow

State explicitly:

- fixture-first may use synthetic or manually curated evidence packets, including complete positive cases and insufficient-data cases;
- fixture evidence must be local, deterministic, credential-free and carry the same taxonomy as future real packets;
- fixture results prove parser/validator/rubric behavior, not real-data availability or investment accuracy;
- real shadow must wait for a separately approved evidence source/provider or an approved static evidence dataset with provenance;
- do not weaken moat/market_pos/sentiment evidence requirements merely to make real shadow pass;
- missing real evidence is an explicit real-shadow blocker, not a fixture-first blocker.

### 4. Reclassify cache versioning as cutover blocker

Keep the current production table mismatch visible, but state clearly:

- it blocks production cutover, not fixture-first or file-artifact shadow;
- fixture-first must not choose or implement a production migration;
- production cutover must choose one explicit strategy in a later approved plan: a new versioned production table is the preferred candidate; ALTER of the legacy table remains an alternative requiring justification;
- old and new scores must not become indistinguishable;
- the future plan must define migration, indexes, cache priority, backward compatibility, backup/readback/rollback and version-layered reporting.

Do not authorize or perform any DB change now.

### 5. Add an explicit test matrix

Add a concise but implementation-ready matrix covering at least:

- complete scored result;
- each dimension insufficient-data branch;
- nullable score behavior;
- unknown/duplicate evidence ID;
- unknown evidence type/dimension/directness/freshness policy;
- evidence allowed-dimension mismatch;
- stale and boundary-date evidence;
- schema-valid but semantically invalid result;
- extra/missing fields and range failures;
- API timeout, 401/403, 429, 5xx and malformed response classification;
- no retry for auth/schema/semantic errors;
- bounded retry for 429/5xx;
- fresh validated same-version cache;
- stale same-version cache;
- legacy-cache migration fallback;
- invalid/insufficient result not overwriting trusted cache;
- all-or-nothing behavior;
- schema/rubric/input-hash version mismatch;
- backward compatibility with current v1 production path;
- shadow isolation from production table, pipeline, predictions and Telegram.

Classify tests by fixture-first, later shadow, and production-cutover stages so the first implementation is not blocked by future infrastructure.

### 6. Consolidate duplicate requirements carefully

Reduce obvious duplication without weakening safety. In particular consolidate where practical:

- input-only evidence rules;
- credential/redaction rules;
- shadow physical-isolation rules;
- repeated statements that schema/rubric agreement does not prove alpha.

You may renumber Requirements and Acceptance Criteria. If renumbering:

- make REQ IDs contiguous and unique;
- make AC IDs contiguous and unique;
- update every REQ/AC range and milestone mapping;
- preserve traceability;
- do not delete a safety boundary simply because it was repeated.

## Additional accepted review note

Define shadow/evaluation failure classifications for API and validation outcomes, such as bounded statuses for API timeout/auth/rate-limit/server error, malformed response, schema invalid, evidence invalid and insufficient data. Do not store stack traces, secrets, raw headers or API keys in `failure_reason`; store a redacted bounded reason/code.

A separate v2 entrypoint is a preferred future implementation seam, but keep exact file/function patch details for the implementation plan rather than over-specifying code in this Spec.

## Non-goals

- No production code implementation.
- No DB migration.
- No evidence provider implementation.
- No real shadow/API call.
- No weights, thresholds, predictions, cron or Telegram change.
- No broad rewrite unrelated to the six requested revisions.

## Validation before finishing

Perform read-only/mechanical checks only:

1. Verify only the target Spec changed relative to your start state.
2. Verify REQ IDs are unique and contiguous.
3. Verify AC IDs are unique and contiguous.
4. Verify no trailing whitespace.
5. Verify `git diff --check` passes.
6. Confirm the Spec still says implementation, production DB, real Gemini shadow and cutover approvals are pending.

Do not commit.

## Final response

Report:

- exact file changed;
- concise summary of revisions;
- mechanical checks and outcomes;
- unresolved user decisions;
- explicit statement that no code, DB, tests, API, cron, Telegram, weights or historical records were modified.
