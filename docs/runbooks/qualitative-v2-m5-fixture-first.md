# M5 Fixture-first Bounded Shadow

## Current boundary

The M5 batch layer consumes an already-built static bundle and the fixed 36-company M4 sample. It does not retrieve evidence, load `.env`, read `tracker.db`, import `pipeline.py`, use production caches, or contact Telegram/cron. The checked sample SHA-256 is:

```text
b278a7b00b71fd54e34519dead098602a8748635f1350414e1b78538a3d7635d
```

That hash identifies only the M4 sample/frame metadata. Every context and the bundle manifest have independent SHA-256 values; no real bundle hash exists until a real static bundle is separately approved and built.

This implementation's model commands execute synthetic bundles only, through deterministic fake Claude/Gemini transports. A structurally complete real bundle can be built and previewed offline, but every model execution command rejects it before a call. Enabling real Claude/Gemini transports requires a separate approval covering the exact sample SHA, final bundle SHA, full Claude model ID, fixed `gemini-2.5-flash`, call limits, credentials, and cost.

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

## Data authorization preflight

Before any D1 source or read-only database operation, preview the machine boundary:

```bash
.venv/bin/python scripts/prepare_qualitative_v2_m5_data_authorization.py preview \
  --sample artifacts/milestone-004/lite/20260718T151349602626+0800-8e319618/derived/sample.csv \
  --authorization-id m5-data-readiness-20260718-01 \
  --data-run-id m5-data-20260719-01 \
  --not-before 2026-07-19T00:00:00+08:00 \
  --not-after 2026-08-01T23:59:59+08:00
```

`preview` prints the canonical authorization and SHA-256 but creates nothing. After the user approves that exact hash,
`seal` creates one authorization/checksum pair; `preflight --require-active` revalidates the fixed sample, M4 protocol,
756-group matrix, host/method limits, D/D+1/D+2 schedule, 4,536 extreme HTTP-attempt ceiling, 8 GiB artifact ceiling,
read-only database field list, and all model/Reviewer/production prohibitions. Preflight does not open a socket, database,
`.env`, credential variable, or artifact root.

After the authorization is approved, sealed, and active, create the single allowed local fundamentals snapshot:

```bash
.venv/bin/python scripts/build_qualitative_v2_m5_fundamentals_snapshot.py \
  --sample artifacts/milestone-004/lite/20260718T151349602626+0800-8e319618/derived/sample.csv \
  --authorization reviews/milestone-005/<authorization>.json \
  --checksum reviews/milestone-005/<authorization>.sha256 \
  --output artifacts/milestone-005/data/<data-run-id>/fundamentals-snapshot.json \
  --execute-read-only
```

The snapshot CLI accepts only the authorization-bound output and project-root `tracker.db`. It opens SQLite with
`mode=ro`, enforces `PRAGMA query_only=ON`, uses one transaction, selects only the 36 sample codes and approved fields,
and checks the main database file identity before/after. Missing rows and unusable identity/data/report-period rows are
recorded explicitly and emit no evidence values. The create-only snapshot is mode `0600` under a `0700` data-run root.
Tests use a temporary SQLite fixture; implementation and test verification do not open the real database.

Record the frozen official front doors once per allowed local date:

```bash
.venv/bin/python scripts/capture_qualitative_v2_m5_source_frontdoors.py \
  --sample artifacts/milestone-004/lite/20260718T151349602626+0800-8e319618/derived/sample.csv \
  --authorization reviews/milestone-005/<authorization>.json \
  --checksum reviews/milestone-005/<authorization>.sha256 \
  --execute-network
```

The capture uses only the four frozen official HTTPS front pages, verified TLS, exact-host redirects, a 60-second
timeout, and the 64 MiB response ceiling. It writes one create-only date root and records successful raw bytes or a
sanitized technical error; rerunning the same date is rejected before a request. Actual requests are charged to the
authorization's search HTTP budget.

If successful pages explicitly reference the frozen CNINFO/SSE query-controller assets, capture those exact assets and
bind them to the front-door manifest:

```bash
.venv/bin/python scripts/capture_qualitative_v2_m5_source_ui_assets.py \
  --sample artifacts/milestone-004/lite/20260718T151349602626+0800-8e319618/derived/sample.csv \
  --authorization reviews/milestone-005/<authorization>.json \
  --checksum reviews/milestone-005/<authorization>.sha256 \
  --execute-network
```

The asset stage re-hashes the parent manifest and front-page bytes and proves each asset URL appears in its official
parent page before making a request. A missing full-text control, changed source semantics, or unavailable front door
is a protocol stop condition; the operator must not substitute a title-only endpoint, search engine, or alternate
source.

For v1.2, derive the three-axis capability report offline before proposing a new collection protocol:

```bash
.venv/bin/python scripts/probe_qualitative_v2_m5_source_capability.py preview \
  --sample artifacts/milestone-004/lite/20260718T151349602626+0800-8e319618/derived/sample.csv \
  --authorization reviews/milestone-005/<authorization>.json \
  --checksum reviews/milestone-005/<authorization>.sha256 \
  --frontdoors artifacts/milestone-005/data/<data-run-id>/source-frontdoors/<date>/manifest.json \
  --ui-assets artifacts/milestone-005/data/<data-run-id>/source-ui-assets/<date>/manifest.json

.venv/bin/python scripts/probe_qualitative_v2_m5_source_capability.py seal \
  --sample artifacts/milestone-004/lite/20260718T151349602626+0800-8e319618/derived/sample.csv \
  --authorization reviews/milestone-005/<authorization>.json \
  --checksum reviews/milestone-005/<authorization>.sha256 \
  --frontdoors artifacts/milestone-005/data/<data-run-id>/source-frontdoors/<date>/manifest.json \
  --ui-assets artifacts/milestone-005/data/<data-run-id>/source-ui-assets/<date>/manifest.json \
  --output artifacts/milestone-005/data/<data-run-id>/capability-probe-v1.2/report.json
```

`preview` creates nothing. `seal` writes one `0600` report beneath a `0700` create-only directory. Both modes perform
zero external calls. The report never promotes transport success into full-text or evidence-quality success. A single
source failure is source-local when another official full-text route exists; collection still requires a bounded
quality pilot and a new user-approved protocol.

For the approved v1.2 structural-quality pilot, validate the sealed boundary before the execution window:

```bash
.venv/bin/python scripts/prepare_qualitative_v2_m5_quality_pilot.py preflight \
  --sample tests/fixtures/milestone005/sample.csv \
  --capability-report artifacts/milestone-005/data/m5-data-20260719-01/capability-probe-v1.2/report.json \
  --authorization reviews/milestone-005/m5-evidence-quality-pilot-20260719-01.json \
  --checksum reviews/milestone-005/m5-evidence-quality-pilot-20260719-01.sha256 \
  --require-active
```

On one of the exact authorized local dates (`2026-07-20`, `2026-07-21`, `2026-07-22`), execute at most one attempt
per pending operation:

```bash
.venv/bin/python scripts/run_qualitative_v2_m5_quality_pilot.py \
  --sample tests/fixtures/milestone005/sample.csv \
  --capability-report artifacts/milestone-005/data/m5-data-20260719-01/capability-probe-v1.2/report.json \
  --authorization reviews/milestone-005/m5-evidence-quality-pilot-20260719-01.json \
  --checksum reviews/milestone-005/m5-evidence-quality-pilot-20260719-01.sha256 \
  --execute-network
```

`PROVISIONAL` permits only the next authorized-date attempt. `COMPLETE`, `FAIL`, an existing same-day attempt, input
drift, or an expired window is terminal. To verify a terminal report without external access, replace
`--execute-network` with `--verify-report`. Verification rebuilds the report from the canonical attempt-manifest chain,
re-hashes every raw response, and compares the result with the report and checksum. This pilot evaluates structural
quality only and cannot authorize semantic review or freeze the 36-company protocol.

## Offline real-bundle builder

After source approval, all eight M4 coverage rows pass, and the 36 contexts/documents have been staged, build the final
static bundle without network or credentials:

```bash
.venv/bin/python scripts/build_qualitative_v2_m5_bundle.py \
  --sample artifacts/milestone-004/lite/20260718T151349602626+0800-8e319618/derived/sample.csv \
  --input artifacts/milestone-005/data/<data-run-id>/bundle-input.json \
  --output artifacts/milestone-005/bundles/<new-bundle-id>
```

The input manifest version is `m5-real-bundle-input-v1`. Its top level is exactly `input_version`, `as_of_date`,
`source_approval_id`, `source_scopes`, `coverage_report_path`, and `companies`. Each company contains exactly `code`,
`context_path`, and `evidence_provenance`; provenance uses the same fields as the final bundle and points to documents
relative to the input manifest.

The builder validates the fixed sample and actual coverage-report semantics, rejects unsafe/symlinked inputs and hash
drift, deduplicates copied documents by SHA-256, computes every context hash, and self-validates the complete real
manifest before an atomic publish. The output root is create-only mode `0700`, with directories `0700` and files
`0600`; a failed build removes only its unpublished temporary root. It does not retrieve evidence, inspect `.env`, read
`tracker.db`, or enable the real-model execution commands.

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
