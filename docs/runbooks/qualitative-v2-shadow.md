# Qualitative v2 Shadow Runbook

## Purpose and boundary

This entrypoint evaluates an already-approved `QualitativeContext` without changing production scoring. It does not import `pipeline.py`, read or write `tracker.db`, update `qualitative_scores` or `predictions`, send Telegram messages, or change cron.

MILESTONE-003 authorizes the file-artifact seam only. MILESTONE-004 has produced the fixed 36-company frame/sample, but the evidence-feasibility coverage report is not complete. M5 fixture-first batch orchestration is implemented with synthetic evidence and fake transports only; see [`qualitative-v2-m5-fixture-first.md`](qualitative-v2-m5-fixture-first.md). Real company shadow remains blocked until MILESTONE-004 produces the coverage report, all failed layers are re-audited or excluded (exclusion stops this 36-company M5 sample), sources receive separate approval, a final static bundle is built, and external execution receives separate authorization.

## Input contract

The `--context` file must be one JSON object accepted by `validate_context_dict()`. It must carry the exact supported schema, rubric and taxonomy versions and no more than 64 validated evidence items. Never put credentials, tokens, prompts, environment values, or unapproved source data in the context.

An optional `--legacy-scores` JSON file may contain exactly:

```json
{"moat": 5, "market_pos": 2, "sentiment": 3}
```

The CLI does not query the legacy production cache. Supplying this file only adds an explicit comparison object to the isolated artifact.

## Preview without network

```bash
cd /home/lin/a-stock-tracker
.venv/bin/python scripts/run_qualitative_v2_shadow.py \
  --context tests/fixtures/qualitative_v2_empty_context.json \
  --output artifacts/qualitative_v2_shadow.jsonl
```

Without `--execute`, the command validates the context and prints the input hash. It does not load the API key, call Gemini, or create the artifact.

## Execute an approved call

```bash
.venv/bin/python scripts/run_qualitative_v2_shadow.py \
  --context path/to/approved-context.json \
  --legacy-scores path/to/legacy-scores.json \
  --output artifacts/qualitative_v2_shadow.jsonl \
  --execute
```

`GEMINI_API_KEY` is read from the process environment or project `.env` and sent in the `x-goog-api-key` header. It is never placed in the URL, prompt, log, or artifact. The output path must end in `.jsonl`; files are mode `0600`, append+fsync is lock-protected, corrupt existing JSONL fails closed, and artifacts larger than 64 MiB must be rotated.

The uniqueness key is `(code, scored_date, schema_version, rubric_version, taxonomy_version, input_hash)`. The lock is held across the bounded external call so concurrent or repeated runs do not issue duplicate paid requests. A duplicate prints `api_called=false` and `persisted=false`.

## Outcome classes

Artifacts use only the bounded statuses from REQ-038:

- `VALID_SCORED` or `VALID_INSUFFICIENT_DATA`
- `API_TIMEOUT`, `API_AUTH_ERROR`, `API_RATE_LIMIT`, `API_SERVER_ERROR`
- `MALFORMED_RESPONSE`, `SCHEMA_INVALID`, `EVIDENCE_INVALID`, `SEMANTIC_INVALID`

Timeout, 429, transport failures, and 5xx responses use at most three attempts with bounded backoff and jitter. Authentication, request/schema, malformed output, evidence, and semantic failures do not retry. Failure reasons are secret-scrubbed, single-line, and capped at 256 characters.

## Stop conditions

Stop and do not rerun with relaxed validation if any of these occurs:

- structured-output fields or nullable behavior differ from the current Google contract;
- the context contains unapproved evidence or any secret;
- local validation reports schema, evidence, or semantic invalidity;
- the artifact is corrupt or exceeds its size limit;
- any code path attempts to touch production DB, pipeline, cron, Telegram, weights, or historical predictions;
- the MILESTONE-004 coverage report and source approval are absent, or the real-company run has not received the separate MILESTONE-005 exact-model/budget/credential approval.
