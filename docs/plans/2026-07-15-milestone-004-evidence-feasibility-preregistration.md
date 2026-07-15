# MILESTONE-004 evidence-feasibility audit preregistration

Protocol ID: `qualitative-v2-m4-prereg-v1`
Protocol version: `1.0.0`
Preregistration date: `2026-07-15`
Status: **locally frozen; audit execution not authorized**
Normative timezone: `Asia/Shanghai`
Parent specification: `docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md` (`REQ-059`, `REQ-060`, `REQ-065`, `REQ-066`, `AC-012`)

## 1. Purpose and authorization boundary

This protocol preregisters the sampling, evidence collection, independent review, adjudication, and statistical rules for the MILESTONE-004 evidence-feasibility audit. Its only estimand is the proportion of sampled A-share companies for which the frozen official corpus contains at least one finally accepted item of direct, company-specific, effective `competitive_moat` evidence.

Freezing this document does **not** authorize any audit execution. In particular, this stage does not:

- retrieve a real sampling frame or select companies;
- query or download real-company evidence;
- call Gemini, Codex Reviewer A, or AGY Reviewer B for real-company labeling;
- modify production code, dependencies, `tracker.db`, production tables, scores, weights, pipeline behavior, cron, Telegram, or Sheets;
- approve any candidate source for MILESTONE-005 real shadow or production use.

Real sampling and evidence collection require separate user authorization. Reviewer execution and every paid or credentialed external call must also remain inside the separately approved execution scope. Completion of MILESTONE-004 grants evidence-audit information only; it never grants the production/provider approval required by `REQ-012` and `REQ-047`.

Normative terms `MUST`, `MUST NOT`, `SHALL`, and `FAIL CLOSED` are binding. When an implementation detail is absent or ambiguous, execution stops and a new protocol version is required; the operator must not choose the interpretation after seeing audit results.

## 2. Frozen parameters

| Parameter | Frozen value |
|---|---|
| Sampling date | Most recent complete trading day at execution time |
| Industry taxonomy | SW2021 level 1, exactly 31 industries |
| Industry super-strata | Six groups defined in section 4 |
| Market-cap strata | Low/high half within each industry super-stratum |
| Sample per cell | 3 companies |
| Total sample | `6 × 2 × 3 = 36` companies |
| Seed | `qualitative-v2-m4-prereg-v1` |
| Hash input | UTF-8 bytes of `seed + "\x1f" + ts_code` |
| Hash output | SHA-256, exactly 64 lowercase hexadecimal characters |
| Evidence sources | CNINFO, corresponding stock exchange, CNIPA |
| Query order | `护城河`, `壁垒`, `专利`, `特许经营`, `独占`, `授权`, `核心技术` |
| Saved results per source-query | First 20 in the official UI's default order |
| Frozen candidate documents per company | At most 20 unique documents |
| Technical attempts | 3 attempts on 3 distinct Asia/Shanghai calendar dates |
| Industry pass threshold | `4/6` scorable companies |
| Market-cap pass threshold | `12/18` scorable companies |
| Wilson z | `1.959963984540054` |
| Multiplicity adjustment | None; intervals are descriptive only |

## 3. Sampling frame

### 3.1 Sampling date

At execution, `sampling_date` is the latest exchange trading day for which both Shanghai and Shenzhen regular A-share sessions have completed and all required frame inputs in section 3.2 are available. A day is complete only after both exchanges' regular close; a half trading day, a currently open day, or a day with a missing required snapshot is not complete. The date is serialized as ISO `YYYY-MM-DD`.

The implementation obtains the common open day from Tushare Pro `trade_cal` for `SSE` and `SZSE`. If the calendars disagree about whether the selected day is open, or either required calendar is unavailable, freezing the sampling frame is blocked.

### 3.2 Frame input contract

The sampling-frame snapshot uses the following Tushare Pro datasets because `ts_code`, `total_mv`, and SW2021 are the field vocabulary already selected by this protocol:

| Dataset | Frozen use |
|---|---|
| `trade_cal` | Determine the latest complete common trading day |
| `stock_basic` | `ts_code`, symbol, name, exchange, market, list status, listing date |
| `daily_basic` | Unrounded `total_mv` on `sampling_date` |
| `index_classify` with `src=SW2021`, level 1 | Authoritative list of SW2021 level-1 industry codes and names |
| `index_member_all` with `is_new=Y` | Current SW2021 membership and level-1 code/name |

The execution snapshot records provider name, documented endpoint name, request fields, request timestamp, response-row count, and SHA-256 of the exact raw response artifact for every dataset. It must not silently combine a current row from one provider with a historical row from another. If the provider changes its documented fields or semantics, execution stops pending a new preregistration version.

The frame includes a security only when all of the following are true at `sampling_date`:

1. `exchange` is `SSE` or `SZSE`, `list_status=L`, and `ts_code` retains its `.SH` or `.SZ` suffix.
2. It is an ordinary RMB A share on the Shanghai main board, STAR Market, Shenzhen main board, or ChiNext. Beijing Stock Exchange, B shares, CDRs, funds, bonds, preferred shares, and delisting-consolidation securities are excluded. An ambiguous security type fails closed.
3. Its official security name at the snapshot does not carry `ST` or `*ST`, case-insensitively after Unicode normalization and trimming. An unavailable or ambiguous risk-warning flag fails closed.
4. It has been listed for at least 36 calendar months. This means the 36-month anniversary of `list_date` is on or before `sampling_date`; when the target month lacks the original day, the anniversary is the target month's last calendar day.
5. Exactly one active SW2021 level-1 membership is present, and both its industry code and name match the frozen 31-industry dictionary in section 4.
6. `daily_basic.trade_date` equals `sampling_date` and `total_mv` is present, finite, and strictly positive. The provider's original numeric value and scale are preserved without rounding or unit conversion for ordering.

Suspension after a company is frozen is not an exclusion criterion. A missing announcement, patent, or accepted item is never an exclusion criterion.

### 3.3 Frame validation and snapshot

Before any sample is selected, the implementation validates and records:

- unique `ts_code` rows;
- exact `sampling_date` alignment for `total_mv`;
- no missing/non-finite/non-positive market cap;
- exactly one active SW2021 level-1 classification per included security;
- all 31 frozen SW2021 names are present in the mapping dictionary exactly once, with no unknown or duplicate mapping;
- an inclusion/exclusion reason for every raw security row.

Any unknown industry, duplicate membership, duplicate security row, unresolved security type, missing required field, or mapping-count failure blocks the sampling-frame freeze. The snapshot is then serialized deterministically with UTF-8, LF line endings, stable field order, and rows ordered by `ts_code`. Its manifest includes the preregistration SHA-256 as `parent_prereg_sha256`.

## 4. Frozen SW2021 mapping

The following dictionary is exhaustive and normative:

| Super-stratum | SW2021 level-1 industries | Count |
|---|---|---:|
| 金融地产 | 银行、非银金融、房地产 | 3 |
| 能源材料 | 石油石化、煤炭、有色金属、钢铁、基础化工 | 5 |
| 工业基础设施 | 建筑装饰、建筑材料、电力设备、机械设备、国防军工、交通运输、综合 | 7 |
| 科技通信 | 电子、计算机、传媒、通信 | 4 |
| 消费 | 汽车、家用电器、轻工制造、纺织服饰、商贸零售、社会服务、食品饮料、美容护理、农林牧渔 | 9 |
| 医疗公用事业 | 医药生物、公用事业、环保 | 3 |

Validation must prove `3 + 5 + 7 + 4 + 9 + 3 = 31`, every dictionary key appears exactly once, and no provider category falls outside the dictionary. Aliases, fuzzy matching, renamed categories, and operator corrections are forbidden in version 1; any such need requires a new version before sampling.

## 5. Deterministic stratification and selection

### 5.1 Market-cap split

For each of the six super-strata independently:

1. Let `N` be the number of eligible companies in that super-stratum.
2. Sort companies ascending by the tuple `(total_mv, ts_code)`, comparing the unrounded provider value numerically and `ts_code` by Unicode code-point order.
3. Assign the first `ceil(N / 2)` companies to `low`; assign the remainder to `high`.

The low stratum deliberately receives the middle observation when `N` is odd. No quantile library, interpolation method, rounded display value, or locale collation may replace this rule.

### 5.2 Sampling key

For every eligible company, compute:

```text
message = "qualitative-v2-m4-prereg-v1" + U+001F + ts_code
sampling_hash = lowercase_hex(SHA256(UTF8(message)))
```

`U+001F` is the single ASCII Unit Separator byte `0x1f` after UTF-8 encoding. `ts_code` is used exactly as frozen, including its uppercase exchange suffix, with no whitespace or Unicode normalization at hashing time. The result must match `^[0-9a-f]{64}$`; otherwise execution stops.

Within each of the 12 super-stratum × market-cap cells, sort by `(sampling_hash, ts_code)` ascending and select the first three eligible companies. Every cell must contain at least three companies; otherwise sample freezing is blocked. The resulting manifest must contain exactly 36 unique `ts_code` values, six companies per industry super-stratum, 18 low-cap companies, and 18 high-cap companies.

### 5.3 Pre-freeze correction and post-freeze no-replacement rule

Before the sample manifest is frozen, a selected row may be passed over only if a documented validation proves that it did not satisfy the section 3 frame on `sampling_date`. The operator records the rejected `ts_code`, original cell, hash, failed predicate, evidence artifact, detection timestamp, and the next company selected in the same cell's already-frozen hash order. The cell assignment and ordering are not manually changed. A discovery that invalidates the frame or stratum calculation itself requires regenerating the entire downstream snapshot as a new version, not an ad hoc replacement.

After the sample manifest is frozen, replacement is forbidden. Suspension, search failure, no announcements, no patents, no accepted evidence, inconvenient identity resolution, reviewer disagreement, or technical failure must not change the 36 companies or any denominator. An ineligible company discovered after freeze invalidates the audit version and requires a new version retaining the old artifacts.

## 6. Official evidence collection protocol

### 6.1 Source and query priority

Each company is queried against sources in this fixed order:

1. CNINFO;
2. the company's corresponding exchange: SSE for `.SH`, SZSE for `.SZ`;
3. CNIPA Patent Search and Analysis System.

Within each source, queries are executed in this fixed order:

1. `护城河`
2. `壁垒`
3. `专利`
4. `特许经营`
5. `独占`
6. `授权`
7. `核心技术`

The operator must use the public official query pages in Appendix A and may not call an undocumented/private endpoint observed in browser traffic. Login is permitted only where the official public service requires it, but credentials, cookies, headers, and tokens must never be included in an artifact or manifest.

Once a separate execution authorization exists, these three sources are approved only as candidate sources for this feasibility audit. Their inclusion here does not approve them for MILESTONE-005 real shadow or production.

### 6.2 Entity constraint and date range

For CNINFO and the corresponding exchange, each query combines the exact sampled `ts_code`/official company identity with exactly one query term, searches title plus full text where the public UI offers that choice, keeps all announcement types, and uses publication dates from `list_date` through `sampling_date`, inclusive. Results after `sampling_date` are forbidden.

For CNIPA, each query is limited to Chinese patents whose applicant/patentee is either the sampled company itself or a subsidiary eligible under section 8.2, combines that exact entity with exactly one query term over the public UI's bibliographic/full-text search capability, and includes all publication dates up to and including `sampling_date`. No lower date cutoff is imposed because an older granted patent may remain effective. The legal-status record must be evaluated as of `sampling_date`; a later status must not be back-projected.

Before any CNIPA query, retrieve exactly one relationship-support document: the latest full annual report published by the corresponding exchange on or before `sampling_date`. Preserve its official raw bytes and hash under the same technical-failure rules as candidate documents. This report is outside the 20 candidate-document cap solely because it may be used only to freeze the applicant/patentee entity list and prove consolidation scope; it cannot support a moat label unless the same document is independently selected by section 6.4, in which case the raw blob is reused and it counts as one candidate. If no qualifying annual report or unambiguous consolidated-subsidiary list can be obtained, CNIPA collection is blocked as `technical_error` rather than broadening the entity set.

If an official UI cannot express a frozen constraint, returns more than one security for an exact identifier, or changes its available fields, the source-query is a `technical_error`; the operator must not substitute a general web search.

### 6.3 Result capture

For every company × source × query combination, save metadata for the first 20 results in the official UI's default order. Do not select a different sort. The manifest records the visible default sort label, search timestamp, exact public page URL, UI parameters, total count as displayed, result rank, title, publication date, official document number if present, displayed company/entity, canonical document URL, and capture-artifact SHA-256.

Zero results is a successful, complete query and is recorded as such. More than 20 results does not permit pagination beyond rank 20. Fewer than 20 results means all displayed results are recorded. Collection must not stop when a plausible or accepted item first appears.

### 6.4 Deterministic round-robin and deduplication

After all source-query result lists for a company are complete, candidates are visited with rank as the outer loop:

```text
for rank in 1..20:
    for source in [CNINFO, corresponding_exchange, CNIPA]:
        for query in [护城河, 壁垒, 专利, 特许经营, 独占, 授权, 核心技术]:
            visit result[source, query, rank] if it exists
```

Take the visited candidate if it is not a duplicate. Continue until 20 unique documents are selected or the candidate matrix is exhausted. A document is a duplicate when the official document number matches, otherwise when the normalized canonical URL matches, otherwise when downloaded raw bytes have the same SHA-256. The earliest visited copy wins, while every skipped copy records `duplicate_of` and the matching rule. If byte-level duplication is discovered after download, continue the same visitation sequence so the company may still reach 20 unique documents.

The cap is exactly 20 unique frozen documents per company. It may not be expanded after viewing results, and retrieval may not stop early after finding evidence.

### 6.5 Authoritative corpus bytes

For every selected document, preserve the exact official response bytes (HTML, PDF, or another documented official response) as the authoritative input. The versioned manifest records at least:

- relative blob path beneath `artifacts/milestone-004/v1/`;
- source, source-query, result rank, and round-robin ordinal;
- retrieval timestamp in ISO 8601 with `+08:00` offset;
- canonical URL and final official URL after documented redirects;
- official document/publication/patent number when present;
- declared and detected MIME type;
- byte count and SHA-256;
- HTTP or browser-visible completion status without credentials;
- optional extracted-text path, extractor name/version/configuration, byte count, and SHA-256.

Extracted text is a deterministic convenience copy only. It never replaces the raw bytes. OCR, if required, must be versioned and reproducible, and reviewers must be able to inspect the raw page image. A blob that is absent, empty when the official document is non-empty, or mismatched against its manifest fails closed.

Large raw blobs remain only in the gitignored local `artifacts/` tree. Git contains versioned manifests and reports, not the blobs. A report cannot be generated on a machine lacking every referenced blob with a matching hash.

### 6.6 Technical failure policy

A failed query-page load, required-result capture, download, redirect validation, hash verification, MIME validation, decompression, password-free opening, deterministic extraction needed for review, or necessary page read is `technical_error`. It must never be classified as `insufficient`.

Each failed operation is attempted exactly three times on three distinct Asia/Shanghai calendar dates: first attempt on day `D`, second on `D+1`, and third on `D+2`, with at most one attempt for that operation per date. Every attempt records timestamps, sanitized error class, URL, and result. No substitute source, stock, document, or denominator is allowed. If the third attempt fails, the entire coverage report is blocked until a new authorized run/version resolves the error.

## 7. Frozen artifact chain

Stages freeze in this order and each manifest stores the preceding stage's manifest SHA-256:

1. preregistration protocol and `preregistration.sha256`;
2. sampling-frame snapshot and manifest;
3. 36-company sample manifest;
4. query-result matrices, candidate-corpus manifest, and raw blobs;
5. sealed Reviewer A and Reviewer B label manifests, each referencing the stage-4 corpus, followed by a review-stage index referencing the corpus and both seal hashes;
6. user adjudication manifest referencing the review-stage index, followed by the final coverage report.

Canonical JSON used for a manifest hash must be UTF-8, LF-terminated, sorted by field name recursively, use no insignificant whitespace, reject NaN/Infinity, and preserve strings exactly. Tabular snapshots use UTF-8 CSV with LF, RFC 4180 quoting, a frozen column order, and the row order specified by that stage. The manifest records the serialization contract and artifact hashes.

No later stage may rewrite, touch, or regenerate an earlier frozen input. Any input-byte, parameter, code-version, model-version, prompt-version, label, or adjudication change creates a new version that retains the old version and links to it with `supersedes`. The implementation test suite must prove that changing any input changes the corresponding stage hash and therefore every downstream parent reference.

## 8. Independent review and adjudication

### 8.1 Reviewer isolation and sealing

Codex Reviewer A and AGY Reviewer B receive the same hash-verified frozen corpus and the same frozen label instructions. They run in separate processes, work directories, session identifiers, and writable cache namespaces. Neither may read the other's prompt transcript, labels, rationale, writable cache, or intermediate files before sealing.

“Offline” means that evidence selection and labeling may use only the frozen local corpus: no web search, retrieval tool, production database, provider call, shared conversation, or outside factual memory may add evidence. Model-service transport, if required by the separately authorized reviewer command, does not turn an external source into admissible evidence. This protocol describes **dual-agent independent review**; it does not claim a security sandbox, human double-blind validation, or statistical independence of model errors.

Each reviewer writes into its own initially empty output directory. The coordinator validates schema, then copies the final output into a read-only sealed location and records byte count plus SHA-256. Only after both seal hashes exist may the coordinator compare results. A reviewer crash, malformed label file, changed corpus hash, or shared-cache violation is `technical_error` and blocks reporting.

### 8.2 Direct competitive-moat acceptance rule

An item is accepted as `direct competitive_moat` only when official text itself proves a company-specific competitive restriction mechanism that is effective at `sampling_date`, such as:

- an effective exclusive right or concession;
- a granted, still-effective patent explicitly tied to a core commercial product or production process;
- an official contract or licence confirming an exclusive right.

For patent evidence, the registered applicant/patentee must be the sampled company itself or a controlled subsidiary within the consolidation scope confirmed by the latest annual report published on or before `sampling_date`. The label records the full legal entity names, annual-report document identifier/date, relationship excerpt/page, and the sampled company's relationship to the right holder. If that as-of relationship cannot be established from the frozen corpus, the patent is rejected. A current relationship learned after `sampling_date` cannot cure it.

The following are always rejected as direct evidence: patent counts alone; pending applications without an effective granted right; generic technical advancement; “leading” or “core technology” language without the restriction mechanism; market rank; financial performance; management assertions; expired/terminated/revoked rights; evidence about an unconsolidated affiliate; and any conclusion that depends on outside inference rather than official text.

Each reviewer labels every candidate document, not merely the first plausible item, with `accept` or `reject`, a frozen reason code, quoted locator/page (short excerpt only), right holder/contract party, state/effective-until basis, relationship basis where applicable, and rationale grounded only in the corpus. Missing required fields fail schema validation.

### 8.3 Company outcome and disagreement handling

A company is `scorable/covered` only if at least one item is finally accepted. It is `insufficient` only when:

1. all collection steps completed without technical error;
2. every selected raw blob exists and matches its hash;
3. both reviewer outputs are valid and sealed;
4. all disagreements have been adjudicated; and
5. no item is finally accepted.

Any A/B difference in item acceptance, reason, effectiveness, right-holder identity, subsidiary relationship, or company outcome is a disagreement. The coordinator presents each disagreement to the user without silently choosing a side. The user records one of `accept`, `reject`, or `rerun_new_version` with rationale. Unresolved disagreements block all coverage conclusions. Adjudication edits neither reviewer file; it is a new hash-linked artifact.

## 9. Coverage statistics and decision rules

### 9.1 Fixed denominators and gates

The final report has eight primary rows:

- six industry super-strata, each with fixed `n=6` and PASS iff `scorable >= 4`;
- low market cap and high market cap, each with fixed `n=18` and PASS iff `scorable >= 12`.

For every row report `scorable`, `insufficient`, fixed denominator, `scorable / denominator`, PASS/FAIL, and the 95% Wilson interval. The gates are exactly two thirds as integer counts; the display may round the percentage to `67%`, but `67%` is not used for computation. Technical errors and unresolved disagreements block the whole report rather than becoming a third denominator category.

If any primary row fails, the only permitted actions are to exclude that entire row from the scope proposed for later stages or create and authorize a new audit version. The directness, validity, relationship, completeness, or other evidence rules must not be weakened to obtain a pass.

### 9.2 Wilson interval

For `x` scorable companies among fixed denominator `n`, let `p̂ = x/n` and `z = 1.959963984540054`. Without continuity correction:

```text
denominator = 1 + z²/n
center      = (p̂ + z²/(2n)) / denominator
half_width  = z/denominator × sqrt(p̂(1-p̂)/n + z²/(4n²))
interval    = [center - half_width, center + half_width]
```

The interval describes sampling uncertainty and does not participate in PASS/FAIL. Computation uses IEEE-754 binary64 through the final value, clips only final floating-point noise to `[0, 1]`, and displays six decimal places with round-half-even. Frozen implementation vectors before six-place formatting are:

| x/n | Lower | Upper | Gate |
|---:|---:|---:|---|
| 0/6 | 0.000000000000 | 0.390334287902 | FAIL |
| 3/6 | 0.187616306483 | 0.812383693517 | FAIL |
| 4/6 | 0.299993315138 | 0.903228588894 | PASS |
| 6/6 | 0.609665712098 | 1.000000000000 | PASS |
| 0/18 | 0.000000000000 | 0.175879223647 | FAIL |
| 11/18 | 0.386190416022 | 0.796947534278 | FAIL |
| 12/18 | 0.437494672959 | 0.837212252492 | PASS |
| 18/18 | 0.824120776353 | 1.000000000000 | PASS |

## 10. Required future implementation tests

Before execution, local tests must cover at least:

1. completeness and one-to-one uniqueness of all 31 SW2021 mappings, including unknown and duplicate fail-closed cases;
2. odd/even market-cap boundaries, exact unrounded ordering, `ts_code` tie-breaks, UTF-8/U+001F hash encoding, lowercase 64-hex output, 12 cells, and deterministic 36-company selection;
3. pre-freeze pass-over logging and absolute post-freeze no-replacement behavior;
4. source/query/rank round-robin order, each deduplication rule, exhaustion, and the 20-document cap;
5. preservation and verification of raw bytes, MIME, size, URL, document number, extracted-text derivation, and missing/mismatched blob failure;
6. three-date technical attempts and report-wide blocking after the third failure;
7. reviewers receiving identical corpus hashes while using separate workdirs, sessions, and cache namespaces, with comparison impossible before both seals exist;
8. as-of patent effectiveness and latest-published-annual-report subsidiary relationship, including rejection of outside entities;
9. exact `4/6` and `12/18` gates plus every Wilson vector in section 9.2;
10. hash propagation: any input change alters that stage hash and all downstream parent references.

All tests use synthetic/local fixtures. Test implementation must not fetch real companies or call real reviewer/model services.

## 11. Stop conditions

The run stops without a coverage result if any of the following occurs:

- a prerequisite user authorization is absent;
- the 31-industry dictionary does not match provider categories exactly;
- a required sampling cell has fewer than three eligible companies;
- frame fields, public query UI, or documented provider semantics drift from this protocol;
- a frozen artifact is missing, mutable, or hash-mismatched;
- a technical operation still fails on the third scheduled date;
- reviewer isolation/sealing validation fails;
- an A/B disagreement remains unadjudicated;
- a post-freeze ineligibility or protocol ambiguity is discovered.

Stopping preserves all completed immutable artifacts and does not authorize a substitute company, source, query, evidence rule, or denominator.

## Appendix A. Frozen public query pages and UI parameters

The URLs below are public front-door pages, not undocumented endpoints. At execution, redirects and visible UI versions are recorded before the first company query. If a page is unavailable or no longer exposes the listed constraint, section 6.6 applies.

### A.1 CNINFO

- Official page: `https://www.cninfo.com.cn/new/fulltextSearch`
- Identity field: exact `ts_code` or official security selection from the UI suggestion; record both displayed code and name.
- Query field: exactly one frozen query term.
- Scope: `标题+全文`.
- Board: exact sampled security's Shanghai/Shenzhen board; no HK, fund, bond, or Beijing result.
- Date: custom `[list_date, sampling_date]`, inclusive.
- Sort: leave the UI's initial/default result order unchanged and record its visible label.
- Result limit: ranks 1–20 only.

### A.2 Shanghai Stock Exchange (`.SH` only)

- Official page: `https://www.sse.com.cn/disclosure/listedinfo/announcement/`
- Security field: exact six-digit code/official short name, confirmed against `ts_code`.
- Market: the sampled company's exact board.
- Keyword: exactly one frozen query term.
- Body option: enable `只看公告正文`/title-plus-body capability where shown so the keyword is applied to official announcement content.
- Announcement type: all.
- Date: custom `[list_date, sampling_date]`, inclusive.
- Sort: leave the UI's initial/default result order unchanged and record its visible label.
- Result limit: ranks 1–20 only.

### A.3 Shenzhen Stock Exchange (`.SZ` only)

- Official page: `https://www.szse.cn/disclosure/listed/bulletin/index.html`
- Security field: exact six-digit code/official short name, confirmed against `ts_code`.
- Board/category: the sampled company's exact board; listed-company announcements only.
- Keyword: exactly one frozen query term, using title-plus-body/full-text when the public UI exposes it.
- Announcement type: all.
- Date: custom `[list_date, sampling_date]`, inclusive.
- Sort: leave the UI's initial/default result order unchanged and record its visible label.
- Result limit: ranks 1–20 only.

If the public SZSE page at execution does not expose full-text keyword search for listed-company announcements, that source-query is a `technical_error`; CNINFO results do not silently replace the exchange-source cell.

### A.4 CNIPA

- Official system: `https://pss-system.cponline.cnipa.gov.cn/conventionalSearch`
- Official system description: `https://www.cnipa.gov.cn/art/2023/2/13/art_3166_182074.html`
- Country/region: China.
- Applicant/patentee: one disjunctive exact-name filter containing the sampled company's full legal name plus every eligible consolidated subsidiary; serialize the sampled company first and subsidiaries by full legal name ascending.
- Query field: exactly one frozen term in the documented conventional/full-text search fields.
- Publication date: all available history through `sampling_date`, inclusive; no lower bound.
- Legal status: do not pre-filter away results; capture the official legal-status record and decide effectiveness as of `sampling_date` during labeling.
- Patent type: all.
- Sort: leave the UI's initial/default result order unchanged and record its visible label.
- Result limit: ranks 1–20 from that single official result list.

The subsidiary list itself comes only from the latest annual report published on or before `sampling_date`; it is frozen and hash-linked before CNIPA queries begin.

If the public UI cannot express the complete disjunctive exact-name filter in one query, the source-query is `technical_error`; splitting the entity set into result lists that cannot share one official default order is forbidden in version 1.

### A.5 Relationship-support annual report

- Official page: the same corresponding-exchange disclosure page in A.2 or A.3.
- Security field: exact six-digit code/official short name.
- Announcement type: annual report/full annual report; summaries, corrections without the full report, quarterly reports, and semiannual reports do not qualify.
- Date: all publications through `sampling_date`, inclusive.
- Selection: publication date descending, then official document number/canonical URL ascending if more than one full report has the same publication timestamp; take exactly the first.
- Permitted use: consolidation-scope identity only, unless section 6.4 independently selects the same document as a candidate.

## Appendix B. Minimum manifest fields

Every versioned manifest includes:

- `protocol_id`, `protocol_version`, and stage name/version;
- creation timestamp and normative timezone;
- operator/tool versions and git commit, if applicable;
- parent manifest path and SHA-256;
- deterministic serialization contract;
- every input/output relative path, byte count, MIME where relevant, and SHA-256;
- status from `complete`, `blocked_technical_error`, `blocked_disagreement`, or `superseded`;
- no credential, cookie, authorization header, token, or unsanitized stack trace.

The final report additionally links the sample, corpus, both reviewer seals, and adjudication hashes. A missing link is a report-generation error.
