# Claude Opus independent production review

## Verdict

`PASS`

Claude completed a read-only review of `ac228e0..0e2adc0` through the local official Claude CLI. It did not edit files, access CNINFO/Gemini, write the database, or run `pipeline.py daily`.

## Findings

### P2 — CNINFO authorization exhaustion depends on retained local artifacts

- Location: `qualitative_v2_production_contexts.py:load_prior_http_attempts()` and `scripts/collect_qualitative_v2_production_contexts.py`.
- Impact: the 45/45 budget was reconstructed only from ignored artifact manifests. Deleting those run directories could reset the apparent usage to zero and permit reuse of the exhausted authorization ID.
- Required fix: maintain a repository-tracked retired-authorization control that is checked before artifact creation, database access, or network activity.

### P3 — Unexpected v2 read exceptions could escape the per-stock fallback boundary

- Location: `qualitative_v2_production.py:get_production_qualitative_score()`.
- Impact: the boundary caught only `ProductionV2Error` and `sqlite3.Error`. A future validator or decoder exception of another `Exception` subtype could abort `daily` instead of falling back to v1 for that stock.
- Required fix: catch unexpected ordinary exceptions at the v2 adapter boundary and add a regression test. Process-control exceptions such as `KeyboardInterrupt` and `SystemExit` must remain uncaught.

## Validated guarantees

- Stored v2 context and result payloads are fully revalidated on every production read.
- Missing, stale, future-dated, insufficient, corrupted, or identity-drifted v2 rows fall back per stock.
- Only validated fixed-model records can enter the isolated `qualitative_scores_v2` table.
- Artifact creation is private, create-only, symlink-checked, and checksum-bound.
- `preview` does not read credentials or create artifacts; `daily` cannot call the v2 Gemini client or CNINFO collector.
- `off`, `canary`, and `on` selection is exact; invalid mode values fail closed to v1.
- Legacy qualitative rows and historical predictions are not modified by this production-canary change.

## Reviewer limitation

Claude's harness could not query the live SQLite database. The primary agent separately performed that read-only check and observed zero v2 rows and 5/5 canary fallback.
