# M5 source capability-first redesign v1.2

Status: **capability probe complete; evidence-quality pilot and collection protocol intentionally unfrozen**

Normative timezone: `Asia/Shanghai`

## Objective

Prevent one source-specific failure from silently changing the evidence contract or stopping unrelated work. v1.2
separates three questions that v1.1 coupled into one global gate:

1. **Transport availability** — can the exact official resource be accessed with the approved HTTPS, redirect, size,
   and credential boundary?
2. **Full-text semantics** — do sealed official page/controller bytes demonstrate that the keyword applies to document
   content rather than titles or client-side presentation only?
3. **Evidence quality** — do a bounded set of returned official documents contain usable, company-specific, direct,
   as-of evidence with complete provenance?

Passing one axis never implies passing another. In particular, HTTP 200 is not evidence quality, and a controller label
is not a substitute for inspecting candidate documents.

## Capability probe contract

The probe is offline and deterministic. It consumes only:

- the exact D1 authorization and checksum;
- the sealed four-source front-door manifest and verified raw bytes;
- the sealed UI-asset manifest, its parent hash, and verified controller bytes.

It performs zero network, database, credential, Reviewer, or model calls. Every manifest must be canonical and match the
authorization/sample/data-run identity; every referenced raw file is path-, size-, and SHA-256-verified.

Per source it emits independent `transport`, `full_text_semantics`, and `evidence_quality` objects. The only full-text
eligibility state is `transport=available` plus `full_text_semantics=demonstrated`. Without candidate documents,
`evidence_quality` must remain `not_evaluated`.

## Aggregation policy

Source-local failures are visible degradations, not automatic project-wide failures. The capability stage is globally
blocked only if there is no official full-text disclosure route at all, or if input identity/integrity validation fails.

This does not authorize silent source substitution:

- `not_demonstrated` is never treated as title-only fallback;
- a transport-failed source is excluded only from the candidate route being proposed;
- excluded source roles and resulting inference limitations remain visible in every later report;
- evidence quality requires its own bounded pilot;
- the final collection protocol and call budget still require user approval.

The stopped v1.1 run cannot resume under this policy. v1.2 must use a new protocol identity, authorization SHA, and
create-only data-run root.

## Observed capability result

The report sealed from the 2026-07-19 D1 artifacts has SHA-256
`792743898f6805599f17b99061588eb1dab5d08d02d765b317a676be0ebca6fc`:

| Source | Transport | Full-text semantics | Evidence quality | Candidate route |
|---|---|---|---|---|
| CNINFO | available | demonstrated | not evaluated | eligible |
| SSE | available | not demonstrated | not evaluated | degraded |
| SZSE | technical error | not evaluated | not evaluated | degraded |
| CNIPA | technical error | not evaluated | not evaluated | degraded |

Because CNINFO is a verified official full-text route, `global_capability_blocked=false`. Because no candidate document
quality has been evaluated, `collection_protocol_frozen=false` and `protocol_freeze_ready=false`.

## Next bounded evidence-quality pilot

The zero-call authorization proposal is implemented, validated, **approved at its exact SHA, and sealed**. Network
execution remains fail-closed until the first authorized local date:

- authorization ID: `m5-evidence-quality-pilot-20260719-01`;
- proposed authorization SHA-256: `c565f6bf8af84d057f250c99faef5ab5aab9dffa58b95251189f06c7ba21801e`;
- pilot run ID: `m5-evidence-quality-pilot-20260720-01`;
- capability report SHA-256: `792743898f6805599f17b99061588eb1dab5d08d02d765b317a676be0ebca6fc`;
- evidence window: `2025-07-18` through the frozen `2026-07-17` as-of date;
- execution dates: `2026-07-20`, `2026-07-21`, and `2026-07-22` (one attempt per operation per local date);
- exact query-matrix SHA-256: `6b0edfcd5526bf1506f3db2c8633cde820801e66087dbcddc012201fe8447f85`.

The proposal validator parses the capability report rather than trusting a self-asserted flag. It requires CNINFO
transport `available`, full-text semantics `demonstrated`, evidence quality `not_evaluated`, the route to be eligible,
the global capability gate to remain open, and all external-call counters to be zero. Both the report content and its
exact SHA are bound into the proposal.

Before freezing the 36-company collection protocol, prepare a separate, exact authorization for this small pilot:

- source: CNINFO only, because it is the only currently demonstrated route;
- companies: the first frozen sample row in each super-stratum — `002807.SZ`, `301057.SZ`, `603507.SH`, `300531.SZ`,
  `002084.SZ`, and `002412.SZ`;
- query: `核心技术` only;
- results: official default-order ranks 1–3 per company;
- scale: 6 query groups and at most 18 unique document downloads;
- attempts: at most one per local date on D/D+1/D+2; no same-day retry or fallback;
- maximum logical HTTP attempts: 18 query attempts plus 54 document attempts = 72;
- same verified HTTPS, same-origin redirect, provenance, raw-byte hash, and create-only artifact rules;
- no production database, Reviewer, Claude, Gemini, Telegram, pipeline, or cache access.

The authorization permits only structural collection. It explicitly sets `semantic_quality_authorized=false` and
`collection_protocol_frozen=false`; therefore even a structurally successful pilot cannot freeze the 36-company
protocol or claim semantic evidence quality.

The pilot report separates structural quality from semantic quality:

- structural: query completion, default rank, document retrieval, MIME, as-of date, locator, and provenance completeness;
- semantic: company specificity, directness, effective-state support, and outside-inference requirement.

Semantic quality must be supplied through a separately approved bounded review or explicit user adjudication; it is not
inferred from keyword counts. The pilot is descriptive and does not silently set a universal pass threshold.

## Collection-protocol freeze conditions

After the pilot, freeze a full protocol only when it records:

1. exact included source roles and every degraded/excluded source;
2. pilot structural and semantic quality results;
3. the resulting inference limitations, especially patent-specific coverage when CNIPA is unavailable;
4. exact 36-company/query/result/document budgets and retry schedule;
5. source-local stop behavior and the small set of genuine global blockers;
6. a new protocol SHA-256, authorization ID/SHA-256, and create-only data-run ID.

The final protocol may proceed with one viable source only if the user explicitly accepts the narrower source coverage.
That decision does not claim equivalence to the original three-source estimand.
