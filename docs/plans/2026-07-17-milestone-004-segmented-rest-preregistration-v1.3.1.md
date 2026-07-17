# MILESTONE-004 segmented REST frame-source preregistration v1.3.1

Protocol ID: `qualitative-v2-m4-prereg-v1.3.1`  
Protocol version: `1.3.1`  
Protocol date: `2026-07-17`  
Status: **FROZEN IMPLEMENTATION CONTRACT — this document does not authorize a provider attempt**  
Normative timezone: `Asia/Shanghai`  
Supersedes SHA-256: `0ad8c6b3d5175af96409e570bdc17839ac7cf266227410f2e9e9ea756a7a12df`

## 1. Scope and lineage

This is the direct repair successor to frozen v1.3. The v1.3 bytes and its
`0ad8c6b3d5175af96409e570bdc17839ac7cf266227410f2e9e9ea756a7a12df` hash remain unchanged. The v1.3 header
continues the chain to v1.2. Every v1.3.1 authorization and manifest records the direct-predecessor hash above in
`supersedes_sha256`. Artifacts are isolated below `artifacts/milestone-004/v1.3.1/`; no artifact from v1.3 or an
earlier version can be adopted or mixed into a v1.3.1 attempt.

This protocol covers capability, date-selection-evidence, and formal-capture attempts. It does not implement or
authorize frame/sample assembly, create an authorization, read a production database, read `.env`, or grant a real
Tushare request. Capability and date-selection bytes are permanently non-adoptable. Capture bytes are eligible only
after candidate verification and identical-byte post-publication verification both pass.

## 2. Schemas and roots

The exact v2 schema identifiers are:

- frame authorization: `m4-segmented-rest-frame-authorization-v2`;
- date authorization: `m4-date-selection-authorization-v2`;
- receipt: `m4-segmented-rest-receipt-v2`;
- stop record: `m4-attempt-stop-v2`;
- exclusion ledger: `m4-exclusion-ledger-v2`;
- date evidence: `m4-date-selection-evidence-v2`;
- attempt manifest: `m4-segmented-rest-attempt-manifest-v2`.

The only roots are:

- capability: `artifacts/milestone-004/v1.3.1/capability-probes/<attempt_id>`;
- capture: `artifacts/milestone-004/v1.3.1/captures/<attempt_id>`;
- date evidence: `artifacts/milestone-004/v1.3.1/date-selection-evidence/<attempt_id>`.

IDs match `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` and are neither `.` nor `..`. All paths are normalized
project-relative POSIX paths with no empty, absolute, `.` or `..` segment. Every hash is exactly 64 lowercase ASCII
hexadecimal characters. Canonical JSON is UTF-8, compact, recursively key-sorted, Unicode-preserving, terminated by
one LF, and rejects duplicate keys, NaN/Infinity, negative-zero normalization, and binary-float substitution.

## 3. Exact request matrices

Every request is a certificate-verified HTTPS `POST` to exactly `https://api.tushare.pro:443` (serialized origin
`https://api.tushare.pro`). There is no query, fragment, userinfo, redirect, proxy, cookie, alternate origin, SDK,
MCP, retry, pagination, fallback, parallelism, resume, or supplemental call. The body has exactly `api_name`,
`token`, `params`, and `fields`; `fields` is the ASCII-comma join of the authorized ordered field array.

The 36 frame calls are, in order:

1. `index_classify(level=L1,src=SW2021)`, fields
   `index_code,industry_name,level,src`;
2–32. one `index_member_all(l1_code=<code>,is_new=Y)` for each frozen v1.1 `SW2021_INDUSTRIES` mapping entry in
   insertion order, fields
   `l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,ts_code,name,in_date,out_date,is_new`;
33. `stock_basic(exchange=SSE,list_status=L)`;
34. `stock_basic(exchange=SZSE,list_status=L)`;
35. `stock_st(trade_date=YYYYMMDD)`;
36. `daily_basic(trade_date=YYYYMMDD)`.

The stock-basic fields are `ts_code,name,market,exchange,curr_type,list_status,list_date`; stock-ST fields are
`ts_code,name,trade_date,type,type_name`; daily fields are `ts_code,trade_date,total_mv`. The frozen industry order
and names are the exact `SW2021_INDUSTRIES` mapping in `qualitative_v2_audit.py`; its file hash is manifest-bound.

The two date-evidence calls are SSE then SZSE
`trade_cal(exchange=<exchange>,start_date=D-62,end_date=D+62)`, using Gregorian arithmetic and fields
`exchange,cal_date,is_open,pretrade_date`. Each calendar response contains exactly one row for all 125 dates,
`is_open` integer 0 or 1, and an eight-digit or null `pretrade_date`.

Frame limits are 32 MiB per response and 128 MiB total. Calendar limits are 4 MiB per response and 8 MiB total.
Every ordinal is attempted at most once and ordinary HTTP/provider/parse/schema/transport failures fail fast.

## 4. Authorization contracts

A frame authorization contains exactly: `schema_version`, `authorization_id`, `attempt_id`, `mode`,
`protocol_path`, `protocol_sha256`, `supersedes_sha256`, `origin`, `credential_env_var`, `attempt_output_dir`,
`response_byte_limit`, `attempt_byte_limit`, `max_attempts_per_ordinal`, `trade_date_kind`, `trade_date`,
`not_before`, `not_after`, `calls`, `capability_manifest_ref`, and `date_evidence_ref`.

`mode` is only `capability|capture`. Capability uses `probe_trade_date` and two null refs. Capture uses
`sampling_date`, a verified-PASS capability-manifest ref, and a verified valid date-evidence ref. Capture's entire
window is within sampling-date `18:00:00–23:59:59+08:00`, begins no earlier than evidence `sealed_at`/`valid_from`,
ends no later than `valid_until`, and uses the same sampling date.

A date authorization contains exactly: `schema_version`, `authorization_id`, `attempt_id`, `purpose`,
`protocol_path`, `protocol_sha256`, `supersedes_sha256`, `origin`, `credential_env_var`, `attempt_output_dir`,
`response_byte_limit`, `attempt_byte_limit`, `max_attempts_per_ordinal`, `proposed_sampling_date`, `calendar_start`,
`calendar_end`, `not_before`, `not_after`, `calls`, and `capability_manifest_ref`. Purpose is exactly
`date_selection_evidence`; the capability ref verifies PASS; range endpoints are exactly D−62/D+62.

Authorization JSON is canonical and its checksum file is exactly
`<sha256>  <authorization project-relative path>\n`. Validation covers the schema, frozen protocol/checksum,
lineage, IDs, root, origin, limits, dates, matrix, refs, and cross-field inequalities before token access and again
before each call.

## 5. Secret, transport, and deadline boundary

After acquiring a create-only attempt lock, the worker reads only `TUSHARE_TOKEN`. It must be exactly 64 lowercase
ASCII hexadecimal characters with no whitespace. Missing/unreadable yields `credential_unavailable`; malformed or
empty yields `credential_invalid`. `.env` is never read. The token is memory/request-body only and cannot appear in
paths, arguments, logs, exceptions, receipts, manifests, artifacts, stdout, or stderr. Response bytes are scanned
before publication; an echo is `token_echo` and publishes no blob.

The public executor is a parent supervisor. A controlled worker performs one attempt. The supervisor derives a
monotonic deadline no later than authorization `not_after`; the worker cannot extend it. A phase journal outside the
attempt child is fsync-published with ordinal and one of `before_transport`, `transport_started`, `blob_sealed`,
`receipt_sealed`, `gates_complete`, or `manifest_candidate`. Deadline kills the worker immediately and the
supervisor seals the unique state from journal plus closed files. Deadline wins artifact classification over a
simultaneous cancellation; after sealing, the original cancellation is re-raised. Stale lock/orphan state fails
closed with no automatic repair, lock breaking, cleanup, or network resume.

A PASS is never inferred from absence of those mutable controls. Candidate manifest bytes pre-bind the exact
`supervisor-commit.json` bytes and a 256-bit nonce. Candidate verification permits only that prospective file to be
absent. After manifest link/directory fsync, the parent runs the same candidate verifier again and observes its
monotonic deadline. Only an on-time PASS may receive the create-only commit; a late PASS remains permanently
non-adoptable even if external controls are later removed. FAIL commits may be published during controlled recovery.
The final offline verifier requires the exact commit and `require_capture_input_eligible()` therefore relies on
positive immutable commit evidence.

## 6. Publication and ordinal states

Locks, journal, and temporary files remain outside the attempt child. Blobs publish first, after token scan, with
create-only link/rename semantics plus file and directory fsync, then parse. Signal-deferred receipts and supporting
artifacts follow. Exact paths are `blobs/NNNN-<sha>.bin`, `receipts/NNNN.json`, `exclusion-ledger.json`,
`date-selection-evidence.json`, `stop-record.json`, and `attempt-manifest.json`. Sealed files are 0444, directories
0555, regular, non-symlink, and link-count one. The parent-only `supervisor-commit.json` is the final closed-set file;
it contains schema/attempt/authorization/deadline/nonce/outcome and is manifest-bound before publication.

Ordinal states are `success_blob`, `terminal_failure_blob`, `terminal_failure_no_blob`, and `unattempted`. There is
one receipt for each attempted ordinal and none for unattempted ordinals; at most one terminal failure exists.
Complete means all expected ordinals are `success_blob`. A later data-gate failure leaves successful ordinals and
`complete=true` unchanged.

Receipts contain exactly `schema_version`, `ordinal`, `call_id`, `state`, `authorization_id`, `attempt_id`,
`captured_at`, `request_description`, `status_category`, `error_code`, `provider_code`, `http_status`, `content_type`,
`byte_count`, `blob_relative_path`, and `blob_sha256`. Blob-bearing states bind all blob metadata. No-blob states use
null blob metadata. Sanitized finite codes distinguish HTTP, provider, parse, schema, transport, size, security,
authorization-window, and process-control failures.

`captured_at` records the response/failure observation. Receipt and blob refs separately record `sealed_at`, sampled
after their create-only link and directory fsync; refs must be chronological by ordinal and no earlier than capture.
Manifest `sealed_at` is the candidate evaluation cutoff used by the data gates, not a claim that later publication
has completed. Final completion is represented by the positive commit record and, for PASS, can be issued only after
the post-publication candidate verification deadline check.

Pre-transport stops publish exactly `schema_version`, `authorization_id`, `attempt_id`, `next_ordinal`, `stop_code`,
and `stopped_at`. Codes are `authorization_invalid`, `authorization_not_yet_valid`, `authorization_expired`,
`authorization_drift`, `protocol_drift`, `clock_invalid`, `lock_lost`, `output_drift`, `credential_unavailable`,
`credential_invalid`, or `process_control_interrupt`.

## 7. Data gates and exact-number ledger

The verifier parses provider JSON from raw bytes while retaining every number lexeme. Provider success is HTTP 200,
`code=0`, null/absent/empty `msg`, exact requested fields and row widths, matching positive count if present, no
pagination residue, and no unknown structural extension that could signal truncation.

- Classification is exactly all 31 code/name pairs with `level=L1,src=SW2021`.
- Each membership partition has 1–1999 rows, exact L1 pair, active `is_new=Y`, null/empty `out_date`, valid
  `in_date<=trade_date`, nonempty raw name, one globally unique `^[0-9]{6}\.(SH|SZ|BJ)$` code. The SH/SZ union has
  at least 4,000 codes and represents all industries. BJ is excluded and ledgered.
- Stock basic has 1–5999 unique exchange-consistent listed rows. Each membership joins once. Eligibility accepts
  only CNY tuples `(SSE,主板,600|601|603|605)`, `(SSE,科创板,688)`, `(SZSE,主板,000|001|002|003)`, and
  `(SZSE,创业板,300|301)`; then rejects NFKC/outer-trim names beginning `退市` or ending `退`, names beginning
  case-folded `ST|*ST` or codes in stock-ST, then listings younger than 36 calendar months.
- Stock-ST has 0–999 unique date-aligned recognized rows. Daily has 4,000–5,999 recognized unique rows, exact date,
  finite positive exact-decimal `total_mv`, and covers every SH/SZ membership.
- The eligible membership/daily intersection passes unchanged v1.1 frame validation and fills all 12 super-stratum
  × low/high cells with at least three companies per cell.

The frame attempt always publishes a canonical v2 exclusion ledger, including when empty. Each entry binds
`canonical_row_sha256`, exact nullable L1/name fields, one-based provider/source ordinals, one of
`exchange_out_of_scope_bse`, `not_in_frozen_sw2021_membership`, `security_type_out_of_scope`, `st_risk_warning`,
`delisting_consolidation`, `listing_age_below_36_months`, or `not_in_frame_eligible_membership`, source kind, and raw
code. Row hashes type-tag null/boolean/string/exact-number lexeme. Entries sort by source rank, code, nullable L1 as
empty, row hash, source ordinal, and provider ordinal. Counts are recomputed by source and reason.

## 8. Date evidence failure state

From both calendars, `common_open` is the ordered intersection where both `is_open=1`. `sealed_at` is whole-second
`+08:00`, no earlier than both receipts and within authorization. Sampling date is the greatest common-open date
whose 18:00 cutoff is not later than `sealed_at`; it equals proposed D. The next common open exists. `valid_from` is
D 18:00:00 and `valid_until` is next-common-open 17:59:59, inclusive.

The v2 manifest contains nullable `date_evidence_ref`. It is non-null and the unique
`date-selection-evidence.json` is in the closed set only when both ordinals are `success_blob` and calendar,
date-derivation, and authorization-window gates all PASS. If complete data gates fail, a terminal failure occurs, or
the attempt is incomplete, the ref is null, no date-evidence artifact exists, and disposition is
`DATE_EVIDENCE_FAILED`. Unevaluated gates are null; evaluated failed gates are false.

## 9. Manifest, verifier, and adoptability

The v2 manifest contains exactly authorization identity/path/hash, attempt/protocol/lineage identity, the exact five
generator refs, expected/success/terminal/unattempted ordinal partitions, failed ordinal, receipt/blob/stop refs,
response total, nullable ledger/counts, nullable date-evidence ref, all gates, complete, disposition,
non-adoptable/date-selection/capture-eligibility booleans, exact closed `artifact_files`, execution deadline, and
sealed time. It also contains the exact supervisor-commit ref/nonce. Specialized refs project exactly into the
sorted closed set. The closed set excludes only the manifest and candidate verification prospectively permits only
the pre-bound supervisor commit to be absent. Extra files or directories are forbidden.

The only generator closure, sorted in the manifest, is:

- `qualitative_v2_m4_segmented_rest.py`;
- `qualitative_v2_m4_segmented_core.py`;
- `qualitative_v2_m4_segmented_runtime.py`;
- `qualitative_v2_m4_segmented_verify.py`;
- `qualitative_v2_audit.py`.

Authorization, its checksum, this frozen protocol/checksum, and these files remain immutable project provenance and
are not copied into attempts. Missing or changed provenance is drift and verification FAIL.

The offline verifier reads no token/environment/database/network. From raw bytes it reconstructs matrix, request
descriptions, states, exact numeric lexemes, all gates, ledger/evidence bytes, manifest cross-fields, deadline,
permissions, links, paths, sizes, hashes, generator closure, authorization, and protocol. Current-checkout drift is
failure by design.

Capability PASS disposition is `ELIGIBLE_FOR_FULL_CAPTURE_AUTHORIZATION`; failure is
`NO_QUALIFIED_FRAME_SOURCE`. Capture PASS is `FRAME_CAPTURE_ELIGIBLE`; failure is `CAPTURE_FAILED_CLOSED`. Date PASS
is `DATE_EVIDENCE_VALID`; failure is `DATE_EVIDENCE_FAILED`. Capability/date always have `non_adoptable=true` and
`capture_input_eligible=false`. Capture candidate manifest bytes may declare eligibility only if the same verifier,
run against those exact candidate bytes and the sealed closed set, returns eligible. Those identical bytes publish
atomically and must pass again. `require_capture_input_eligible()` trusts only that post-publication verifier result.

All legacy v1/v1.1 assemblers and resume paths reject any v1.3.1 attempt. A future assembler must explicitly call
`require_capture_input_eligible()`.

## 10. Acceptance and authorization boundary

Synthetic tests cover both schemas, matrices, windows/refs, transport/secret limits, supervisor phases and process
control, all four states, fail-fast and complete-vs-PASS, date failure without evidence, all data gates, exact-number
ledger vectors, tampering, closed-set/permissions/link/path attacks, generator/authorization drift, legacy rejection,
and token-free/socket-free verification. Existing v1–v1.3 hashes and v1.2 tests remain unchanged.

Acceptance runs directed and full pytest, Ruff lint/F401/format, existing mypy, checksum verification, and
`git diff --check`. Tests do not read `tracker.db`, call a network, pipeline/cron, Telegram, Gemini, AGY, or Reviewer.
The same preregistration checksum sidecar also freezes
`reviews/milestone-004-preregistration-v1.3.1/golden-vectors.json`; its named deterministic test constructors must
reproduce every authorization, row, ledger, stop, evidence, and manifest byte count/hash.

This protocol creates no authorization and permits no real request. After implementation review, a real attempt
still requires a separately supplied canonical authorization and fresh external credential. Each authorization is
single-use; no failed attempt is automatically retried.
