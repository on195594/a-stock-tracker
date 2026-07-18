# M5 two-sprint execution control

Status: **in progress; awaiting bounded data-source authorization**

Owner: user + codex

Normative timezone: `Asia/Shanghai`

## Objective

Advance qualitative scoring v2 through two delivery sprints without treating shadow results as production approval:

1. **Data readiness Sprint** — produce a complete M4 coverage report, approve the source scopes, and build one static
   36-company real bundle.
2. **Model execution Sprint** — run the sealed Claude blind reference, fixed Gemini shadow, and revealed Claude support
   audit against that exact bundle.

The sprints retain only three hard gates: real-input readiness, exact external-model execution authorization, and a
later independent M6/production decision. Documentation refreshes, refactors, and extra reviews are not standalone
gates.

## Frozen starting point

| Item | Frozen value | Current evidence |
|---|---|---|
| Sample | exactly 36 companies, 12 cells x 3 | `artifacts/milestone-004/lite/20260718T151349602626+0800-8e319618/derived/sample.csv` |
| Sample SHA-256 | `b278a7b00b71fd54e34519dead098602a8748635f1350414e1b78538a3d7635d` | recomputed 2026-07-18; byte-identical test fixture exists |
| Sampling/as-of date | `2026-07-17` | fixed sample `trade_date=20260717` |
| Frame | 4,694 eligible rows | M4 lightweight Run 4 |
| Coverage report | not generated | blocking |
| Real bundle SHA-256 | not generated | blocking |
| M5 implementation | orchestration, offline bundle builder, D1 preflight, and read-only fundamentals snapshot builder complete | local implementation; 866-test baseline |

The sample hash identifies sample/frame metadata only. It is not a coverage, corpus, context, or bundle hash.

## Sprint 1 — data readiness

### D1. Bounded source authorization — pending user approval

A zero-call machine preview now freezes this proposal as:

- authorization ID: `m5-data-readiness-20260718-01`;
- data run ID: `m5-data-20260719-01`;
- window: `2026-07-19T00:00:00+08:00` through `2026-08-01T23:59:59+08:00`;
- authorization SHA-256: `93c471fb070376d7d2004b5df8898fc7c7628658c8a82906be9242f847dc0cf1`;
- search-matrix SHA-256: `46e82996ecb2a6760f4aad31555487f8154ce2459d248182dc06a1d978a81463`.

The authorization and checksum remain unsealed until the user approves that exact proposal. Preview validation reads
only the fixed sample and frozen protocol; it performs zero network/model/database calls and creates no artifact.
The authorization-bound fundamentals snapshot CLI is implemented and tested only against temporary SQLite fixtures;
the real `tracker.db` remains unopened until approval, seal, and active preflight.

The proposed authorization is limited to the frozen 36-company sample and `as_of_date=2026-07-17`:

- use official CNINFO, corresponding SSE/SZSE, and CNIPA material for the frozen M4 source/query matrix;
- restrict official hosts to `cninfo.com.cn`, `sse.com.cn`, `szse.cn`, `cnipa.gov.cn`, and
  `cponline.cnipa.gov.cn` (including their subdomains), over verified HTTPS with no cross-domain redirect;
- retain official raw document bytes, URL/document identifier, capture time, MIME, byte count, SHA-256, and locator in
  the Git-ignored M4 artifact root;
- use a read-only local fundamentals snapshot only for the final M5 contexts, limited to the same 36 codes and fields
  already present in `stock_fundamentals`; no production row may be changed;
- preserve the v1.1 result limits, candidate cap, directness rules, technical ledger, and coverage thresholds;
- do not access pipeline execution, predictions, qualitative production cache, Telegram, Sheets, cron, or weights;
- do not call Claude, Gemini, Codex reviewer subprocesses, or AGY reviewer subprocesses under this authorization;
- stop on unknown source, source/date drift, missing provenance, technical failure, or sample/layer drift; do not replace a
  sampled company.

The frozen scale is explicit: `36 x 3 x 7 = 756` source/query groups, at most one attempt on each of D/D+1/D+2
(`2,268` search attempts only in the all-technical-failure extreme), at most 20 unique candidate documents per company
(`720` total), and exactly one relationship report per company (`36` total). Document/relationship operations retain
the same three-date technical-attempt ceiling. There is no same-day automatic retry or fallback source. The local
fundamentals snapshot uses one read-only SQLite transaction and is limited to the 36 sample codes; it must not open the
database in write mode.

This authorization may populate and validate the static corpus only. It does not approve a reviewer or M5 model call.

### D2. M4 coverage — pending D1 and an exact-corpus reviewer authorization

After corpus freeze, publish its exact manifest SHA-256 and prepare the existing isolated Reviewer A/B invocation
previews. Reviewer execution requires one approval bound to those hashes and commands. Complete both seals, resolve
every disagreement through user adjudication, and generate the eight-row coverage report.

If any of the six super-strata or two cap rows fails, stop M5. A failed layer is not replaced; it must be explicitly
re-audited under a new version or excluded, and exclusion stops this fixed 36-company run.

### D3. Static real bundle — pending all eight coverage rows passing

Build exactly 36 contexts using only approved scopes. Each context must pass `validate_context_dict()` and bind every
evidence ID to an official document SHA-256 and locator. Run the credential-free M5 `preview`, then freeze:

- sample SHA-256;
- coverage-report SHA-256 and lineage;
- source approval identifier;
- 36 context SHA-256 values;
- final bundle-manifest SHA-256.

Sprint 1 exits only when `preview` validates the real bundle and no layer is excluded. A bundle-content change creates
a new bundle SHA-256.

## Sprint 2 — model execution

Sprint 2 cannot be authorized until D3 supplies the final bundle SHA-256. The single execution approval must bind:

- the sample SHA-256 and final bundle SHA-256;
- one full immutable Claude model ID (moving aliases such as `sonnet` are forbidden);
- Gemini model `gemini-2.5-flash`;
- at most 72 Claude logical calls and 36 Gemini logical calls (at most 108 Gemini HTTP attempts);
- credential source and a cost ceiling;
- one new create-only run root.

Execution order is fixed: 36/36 Claude blind references and seal verification, then serial Gemini shadow with first-error
stop, then Claude support audit only after 36/36 valid Gemini outputs. Any failure is terminal and the same run root is
not resumed. PASS or PROVISIONAL remains research-only and does not authorize M6 or production cutover.

## Current next action

The next executable action is D1. A concise approval response is sufficient:

> 批准 `m5-data-readiness-20260718-01`，按本文件 D1 的来源、36 股、日期、只读数据库和隔离边界执行。

This response approves authorization SHA-256
`93c471fb070376d7d2004b5df8898fc7c7628658c8a82906be9242f847dc0cf1`, including the explicit `4,536` extreme HTTP
attempt ceiling and `8 GiB` artifact ceiling. If approval arrives outside the frozen window, do not silently extend it;
prepare a new proposal and SHA-256.

Without that approval, the repository can validate synthetic fixtures and existing hashes but cannot manufacture the
missing real corpus, coverage report, or bundle.
