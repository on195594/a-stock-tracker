# M5 Fixture-first Bounded Shadow

## Current boundary

The M5 batch layer consumes an already-built static bundle and the fixed 36-company M4 sample. It does not retrieve evidence, load `.env`, read `tracker.db`, import `pipeline.py`, use production caches, or contact Telegram/cron. The checked sample SHA-256 is:

```text
b278a7b00b71fd54e34519dead098602a8748635f1350414e1b78538a3d7635d
```

That hash identifies only the M4 sample/frame metadata. Every context and the bundle manifest have independent SHA-256 values; no real bundle hash exists until a real static bundle is separately approved and built.

This implementation executes synthetic bundles only, through deterministic fake Claude/Gemini transports. A structurally complete real bundle can be previewed, but every execution command rejects it before a model call. Enabling real Claude/Gemini transports requires a separate approval covering the exact sample SHA, final bundle SHA, full Claude model ID, fixed `gemini-2.5-flash`, call limits, credentials, and cost.

## Commands

```bash
.venv/bin/python scripts/run_qualitative_v2_m5.py preview \
  --sample tests/fixtures/milestone005/sample.csv \
  --bundle path/to/synthetic/bundle-manifest.json

.venv/bin/python scripts/run_qualitative_v2_m5.py blind-review \
  --sample tests/fixtures/milestone005/sample.csv \
  --bundle path/to/synthetic/bundle-manifest.json \
  --run-root artifacts/milestone-005/<new-run-id> \
  --claude-model claude-test-20260718 \
  --execute-claude

.venv/bin/python scripts/run_qualitative_v2_m5.py shadow \
  --from-run artifacts/milestone-005/<new-run-id> \
  --execute-gemini

.venv/bin/python scripts/run_qualitative_v2_m5.py report \
  --from-run artifacts/milestone-005/<new-run-id>
```

`preview` validates and prints aggregate JSON only. It never reads credentials and never creates a run root. The execution flags currently select local deterministic fakes; their help text states that they do not authorize or perform real external calls.

## Bundle contract

The manifest version is `m5-bundle-v1`, with `m5-provenance-v1`. It contains exactly 36 companies in sample order and binds each company to:

- exact code, name, industry, super-stratum, and cap layer;
- one context file accepted by `validate_context_dict()` and its SHA-256;
- one provenance record per evidence ID, including official-source marker, fixed source scope, source-document path/SHA-256, and locator.

All paths are relative to the manifest. Absolute paths, `..`, symlinks, missing/non-regular files, hash drift, unknown source scopes, duplicate/missing companies, layer drift, as-of drift, and provenance/evidence mismatches fail closed.

A real manifest additionally requires a non-empty source approval ID, a hash-bound M4 coverage report with every layer approved, no excluded layer, and non-synthetic fixed source scopes. Validation parses the coverage report itself: `overall_passed` must be true, the M5 blocking flag must be false, the six industry and two cap rows must have their frozen denominators and passing dispositions, and the corpus/review/seal/adjudication lineage hashes must be present and valid. The manifest's `all_layers_approved` value is not accepted as a substitute for those checks. These fields only prove that an input is structurally ready for later approval; they do not enable execution.

## Run lifecycle

Run roots are create-only mode `0700`; files are `0600`. The blind-reference stage makes at most 36 one-attempt logical calls. Gemini cannot start until all 36 references are locally revalidated and hash-bound. Gemini is serial, fixed to `gemini-2.5-flash`, and stops on the first result outside `VALID_SCORED`/`VALID_INSUFFICIENT_DATA`; remaining companies are recorded as `not_attempted`. The revealed evidence-support audit runs only after Gemini reaches 36/36 valid results and makes at most 36 one-attempt logical calls.

Any failure is terminal. The same run root cannot resume or be overwritten. A retry requires a new run ID; any bundle content change produces a new bundle SHA.

Artifacts are `run-summary.json`, `claude-reference.jsonl`, `gemini-shadow.jsonl`, `claude-support-audit.jsonl`, and `aggregate-report.json`. Raw JSONL is create-only; the summary is atomically replaced. On stage completion the summary seals each JSONL SHA-256, and Gemini verifies the blind-reference seal before its first call. The aggregate report binds the run root and upstream artifact hashes; the summary seals the report SHA-256. `report` verifies those hashes and recomputes the expected report from the sealed artifacts before returning it, so edited or cross-run reports fail closed. Prompts and credentials are never artifacts.

## Interpretation

Strict gates are 100% scoring-schema validity, zero accepted unknown evidence IDs, zero unsupported facts, 100% fail-closed agreement where the machine blind reference says `insufficient_data`, and integer `ceil(0.9 × n)` agreement within ±1 for reference-scored dimensions.

A dimension is non-provisional only with at least 12 reference-scored companies and at least one in each of the six super-strata. Low/high are reported separately without an extra count threshold. A super-stratum or cap-layer scored ratio more than 20 percentage points below the dimension-wide ratio is reported as a selection-bias risk; it does not silently change the sample or rubric.

`machine_blind_reference` is a single Claude reviewer, not human review or independent investment judgment. Claude and Gemini may share model bias. PASS, PROVISIONAL, and FAIL do not authorize M6, production cutover, or predictive-validity claims.
