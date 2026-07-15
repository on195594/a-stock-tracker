# MILESTONE-004 evidence-feasibility audit preregistration v1.1

Protocol ID: `qualitative-v2-m4-prereg-v1.1`
Protocol version: `1.1.0`
Preregistration date: `2026-07-15`
Status: **locally frozen; offline tooling only; audit execution not authorized**
Normative timezone: `Asia/Shanghai`
Supersedes SHA-256: `669175cbd1bb0ff98bf0d63834ad9e25ce81f005836e5763503225f0c5af6eb6`

## 1. Scope and authorization

This is the complete v1.1 protocol for the MILESTONE-004 evidence-feasibility audit. It preserves the estimand and sample space of v1 while closing implementation ambiguities. Freezing or implementing it does not authorize a real sampling-frame read, real-company retrieval, Codex/AGY/Gemini execution, production database access, provider approval, score changes, or MILESTONE-005 approval.

The audit estimates the proportion of sampled companies whose frozen official corpus contains at least one finally accepted, direct, company-specific, effective competitive-moat item. An omitted or ambiguous rule fails closed and requires a new linked version.

## 2. Frozen parameters

| Parameter | Frozen value |
|---|---|
| Sampling date | Latest complete common SSE/SZSE trading day at separately authorized execution time |
| Taxonomy | SW2021 level 1, exact code/name dictionary below |
| Sample cells | Six industry super-strata × low/high market cap |
| Sample per cell | 3 |
| Total sample | 36 |
| Seed | `qualitative-v2-m4-prereg-v1` |
| Sampling message | UTF-8 bytes of `seed + U+001F + ts_code` |
| Sources | CNINFO, corresponding SSE/SZSE exchange, CNIPA |
| Queries | `护城河`, `壁垒`, `专利`, `特许经营`, `独占`, `授权`, `核心技术` |
| Result limit | Official default-order ranks 1–20 per source/query |
| Candidate cap | 20 unique documents per company |
| Attempts | D, D+1, D+2 in Asia/Shanghai; at most one per date |
| Industry gate | 4/6 |
| Market-cap gate | 12/18 |
| Wilson z | `1.959963984540054` |

The protocol-version change does not change the seed, hash message, ordering, or sample space.

## 3. Exact SW2021 code/name dictionary

| Code | Name | Super-stratum |
|---|---|---|
| 801780.SI | 银行 | 金融地产 |
| 801790.SI | 非银金融 | 金融地产 |
| 801180.SI | 房地产 | 金融地产 |
| 801960.SI | 石油石化 | 能源材料 |
| 801950.SI | 煤炭 | 能源材料 |
| 801050.SI | 有色金属 | 能源材料 |
| 801040.SI | 钢铁 | 能源材料 |
| 801030.SI | 基础化工 | 能源材料 |
| 801720.SI | 建筑装饰 | 工业基础设施 |
| 801710.SI | 建筑材料 | 工业基础设施 |
| 801730.SI | 电力设备 | 工业基础设施 |
| 801890.SI | 机械设备 | 工业基础设施 |
| 801740.SI | 国防军工 | 工业基础设施 |
| 801170.SI | 交通运输 | 工业基础设施 |
| 801230.SI | 综合 | 工业基础设施 |
| 801080.SI | 电子 | 科技通信 |
| 801750.SI | 计算机 | 科技通信 |
| 801760.SI | 传媒 | 科技通信 |
| 801770.SI | 通信 | 科技通信 |
| 801880.SI | 汽车 | 消费 |
| 801110.SI | 家用电器 | 消费 |
| 801140.SI | 轻工制造 | 消费 |
| 801130.SI | 纺织服饰 | 消费 |
| 801200.SI | 商贸零售 | 消费 |
| 801210.SI | 社会服务 | 消费 |
| 801120.SI | 食品饮料 | 消费 |
| 801980.SI | 美容护理 | 消费 |
| 801010.SI | 农林牧渔 | 消费 |
| 801150.SI | 医药生物 | 医疗公用事业 |
| 801160.SI | 公用事业 | 医疗公用事业 |
| 801970.SI | 环保 | 医疗公用事业 |

All 31 codes and all 31 names must occur exactly once. Unknowns, duplicates, omissions, aliases, fuzzy matches, or crossed code/name pairs block the frame.

## 4. Frame and deterministic sample

The frame is synthetic in this implementation phase. A future authorized frame must retain `.SH`/`.SZ`, have one active exact industry pair, an aligned sampling date, and a finite positive unrounded `Decimal total_mv`. Duplicate securities and exchange suffix mismatches block freezing.

Within each super-stratum, order by numeric `(total_mv, ts_code)`. The first `ceil(N/2)` form low; the rest form high. For every row calculate lowercase SHA-256 over the exact seed, byte `0x1f`, and exact `ts_code`; within each cell order by `(sampling_hash, ts_code)` and select three. The manifest must contain 36 unique companies, six per super-stratum, and 18 in each cap layer.

A documented frame-ineligible row may be passed over only before sample freeze using the already frozen cell order. There is no post-freeze replacement API. Any later ineligibility creates a new version with `supersedes` and preserves the old one.

## 5. Official-result matrix and URL normalization

Every company must have 3 × 7 completed search groups, including successful zero-result groups. Ranks, when present, are consecutive from 1 through at most 20 and remain in official default order. Round-robin visitation is rank → source (`CNINFO`, corresponding exchange, `CNIPA`) → query in section 2 order.

Only `http` and `https` URLs on CNINFO, SSE, SZSE, CNIPA official domains or their subdomains are accepted. Normalize as follows:

1. Lowercase scheme and host and force official origins to `https`.
2. Remove userinfo, fragment, and default ports; retain non-default ports.
3. Preserve path case. Resolve only `.` and `..` segments, normalize percent encoding, and remove a trailing slash except for root.
4. Remove query keys `utm_*`, `spm`, `timestamp`, and `_`, case-insensitively.
5. Preserve every other query pair, including CNIPA document identifiers; sort by `(key, value)` and re-encode.

During visitation deduplicate in order by official document number, canonical URL, then raw-byte SHA-256; the earliest copy wins. Continue until 20 unique documents or exhaustion. Exact official raw bytes remain authoritative.

## 6. Technical ledger

Query, capture, redirect, download, hash, MIME, decompression, open, extraction, or required-page failures are technical errors, never insufficient evidence. Fewer than three failed D/D+1/D+2 attempts is `pending_retry`; three failures is `blocked_technical_error`. Either blocks reporting. No substitute source, company, evidence rule, or denominator is allowed.

## 7. Canonical artifacts

Canonical JSON is UTF-8, LF-terminated, recursively key-sorted, compact (`(',', ':')`), `ensure_ascii=False`, and `allow_nan=False`. Decimal values serialize exactly as strings. The stage order is frame → sample → corpus → reviewer A/B seals and index → adjudication → report.

Default local bytes and workspaces live below gitignored `artifacts/milestone-004/v1.1/`; versioned manifests and reports live below `reviews/milestone-004-audit-v1.1/`. Tests must inject temporary roots.

Every freeze is create-only, uses a same-directory temporary file, flush/fsync, and atomic replace, and rejects an existing target. Every manifest carries the exact parent hash. Blob verification rejects path traversal, symlinks, absence, or size/hash/MIME drift. Verification raises a blocking error rather than returning an ignorable false value. Input, code, prompt/schema, model, label, or adjudication changes always create a new stage hash.

## 8. Independent reviewers

Each workspace has `input/`, `output/`, `cache/`, `state/`, and `tmp/`. Reviewer A and B receive byte-identical read-only `input/` bundles and the same bundle hash; raw corpus files are read-only verified hardlinks or verified copies across filesystems. Their workspaces and writable namespaces must differ.

Reviewer A is Codex and Reviewer B is AGY. The spec explicitly records reviewer, backend, model, executable, and timeout. Execution defaults off. A matching protocol/corpus/reviewer authorization is required to execute. Commands use `shell=False`, `start_new_session=True`, a 540-second maximum, separate TMPDIR/XDG cache/state, and no Gemini, Tushare, Telegram, Sheets, or unrelated model API keys. PATH, locale, certificate/proxy variables, HOME, and CODEX_HOME may remain. The command manifest stores only a prompt hash, never credentials.

Codex uses non-interactive `exec`, ephemeral mode, ignored project rule ingestion, read-only sandbox, never approval, skipped git check, and the output schema; prompt bytes go through stdin. AGY uses new-project plan mode, sandbox, nine-minute print timeout, an independent log, and the same instruction bytes.

Stdout is limited to 4 MiB and stderr to 1 MiB. Timeout, limit breach, nonzero exit, multiple/non-object JSON, or schema failure yields only `technical_error` and no label. Only schema-valid output can be atomically copied into a coordinator seal and made read-only.

## 9. Label schema and comparison

The top level is exactly `schema_version`, `protocol_sha256`, `corpus_manifest_sha256`, and `document_evaluations`; schema version is `m4-review-label-v1`. Every candidate occurs exactly once in `(ts_code, round_robin_ordinal)` order.

Every item contains `document_sha256`, `ts_code`, `decision`, `reason_code`, `locator`, `quoted_text`, `subject_entity`, `effective_until`, `effectiveness_basis`, `relationship_document_sha256`, `relationship_locator`, and `rationale`. Limits are entity 512, locator/quote 2048, and rationale 4096 characters.

Accept reasons are `exclusive_right`, `concession`, `core_active_patent`, and `exclusive_contract`. Reject reasons are `count_only`, `pending`, `no_restriction_mechanism`, `not_core_linked`, `assertion_or_rank`, `financial_only`, `not_effective_as_of`, `entity_out_of_scope`, `relationship_unproven`, and `outside_inference_required`. Accepted items require subject and effectiveness basis; accepted patents additionally require a frozen annual-report relationship document and locator.

The review index requires both unchanged seals, equal corpus and bundle hashes, different reviewers/workspaces/caches, and cannot be built early. Compare every label field and hash full deterministic difference records into disagreement IDs.

## 10. Adjudication and coverage

Adjudication carries a version, the parent review-index SHA-256, and exactly one decision for every disagreement. The only decisions are `accept`, `reject`, and `rerun_new_version`. Accept/reject require a complete final label plus user rationale. Missing, duplicate, or unknown IDs and changed seals block reporting.

A company is scorable when at least one final item is accepted; otherwise it is insufficient only after complete collection, matching blobs, two valid seals, and complete adjudication. The eight report rows are six `n=6` industries and two `n=18` cap layers. Each reports scorable, insufficient, proportion, integer PASS/FAIL, Wilson numeric and six-place display values.

Wilson uses Python binary64 through the final formula with `z=1.959963984540054`, then clips to `[0,1]`; only display formatting uses `Decimal.from_float(...).quantize(Decimal('0.000001'), ROUND_HALF_EVEN)`. Frozen vectors are v1's `0/6`, `3/6`, `4/6`, `6/6`, `0/18`, `11/18`, `12/18`, and `18/18` values unchanged.

Every passing row has `required_action=none` and `scope_disposition=not_required`. A failing row has `required_action=exclude_layer_or_reaudit` and defaults to `scope_disposition=pending_user_decision`; the user may set `excluded` or `re_audit_new_version`. Failure remains visible and pending disposition blocks MILESTONE-005 approval. The tool never chooses exclusion or re-audit.

## 11. Stop conditions and acceptance

Stop on mapping/frame drift, an undersized sample cell, incomplete matrix, unresolved technical status, missing/drifted artifact, reviewer isolation/schema failure, unresolved disagreement, or post-freeze invalidity. Preserve completed immutable artifacts.

Implementation acceptance uses synthetic companies and local blobs, blocks unmocked network/model calls, and exercises URL edge cases, three deduplication rules, artifact tamper propagation, process-group timeout/output limits, reviewer omissions/duplicates/hash/cache/seal failures, partial adjudication, integer gates, and all eight Wilson vectors. It adds no dependency, reads no real database, retrieves no real company, and runs no real reviewer.
