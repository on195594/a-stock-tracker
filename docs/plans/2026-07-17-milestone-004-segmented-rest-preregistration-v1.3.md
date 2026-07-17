# MILESTONE-004 segmented REST frame-source preregistration v1.3

Protocol ID: `qualitative-v2-m4-prereg-v1.3`
Protocol version: `1.3.0`
Protocol date: `2026-07-17`
Status: **FINAL CANDIDATE — becomes frozen only when an exact-byte SHA-256 approval manifest is published; no network execution or authorization is permitted**
Normative timezone: `Asia/Shanghai`
Supersedes SHA-256: `b711b9b81d8732b48f49c25b419dd9cf51eeae8aa4d2f7bbcd0a662a16782c23`

## 1. Scope and unchanged estimand

This protocol changes only the Tushare sampling-frame source protocol. It replaces the impossible v1.2 single bulk
membership request with one request for each frozen SW2021 level-1 industry. It does not modify the v1.1 estimand,
the exact 31-industry code/name dictionary, six super-strata, low/high market-cap split, three companies per cell,
36-company total, sampling seed, evidence sources, queries, candidate cap, technical-attempt ledger, independent
Reviewer rules, adjudication, coverage gates, or Wilson calculation.

The frozen v1.2 attempt remains complete and failed at `NO_QUALIFIED_FRAME_SOURCE`. Its authorization is consumed,
its artifacts remain `non_adoptable`, and neither its protocol nor its hash is edited. This v1.3 protocol grants no
authority to call Tushare, read a token, create an authorization, use a production database, assemble a frame, run a
Reviewer, or change project status. Final-review approval freezes only this protocol; it does not approve an
implementation or a real provider attempt.

Tushare's current official documentation states that `index_member_all` returns at most 2,000 rows per request while
total rows are unlimited when the request is partitioned. The v1.2 requirement for at least 4,000 rows in one
`index_member_all(is_new=Y)` response therefore cannot pass. Version 1.3 uses all 31 frozen L1 codes as an exhaustive,
non-overlapping request partition and fails any partition that reaches the documented 2,000-row ceiling.

## 2. Two separately authorized modes

The protocol has two modes. They use the same exact 36-call matrix but never share an attempt, authorization, output
directory, receipt, or blob.

### 2.1 Capability mode

Capability mode tests account access, response schema, per-partition capacity, market-wide completeness, and the
ability to populate the 12 frozen sampling cells. Every capability byte is permanently `non_adoptable` and cannot be
used to assemble, resume, repair, seed, or freeze a formal frame.

There may be at most one real capability attempt under a frozen v1.3 protocol. A PASS permits only a proposal for a
new formal-capture authorization. A failure closes this v1.3 candidate at `NO_QUALIFIED_FRAME_SOURCE`; continuing
would require a new linked protocol version.

### 2.2 Capture mode

Capture mode is unavailable unless a separately frozen capability attempt passed offline verification. It requires a
new canonical authorization, attempt ID, output root, time window, and trade date. Capability bytes must not be
copied, linked, resumed, or otherwise adopted.

Capture bytes become eligible as formal frame inputs only after all 36 calls complete, every gate in this protocol
passes, and token-free offline verification reproduces the sealed manifest. Frame and sample assembly is then wholly
offline and must continue to use the unchanged v1.1 validation and deterministic sampling rules. A failed capture
publishes no frame or sample and grants no automatic retry.

## 3. Exact 36-call REST matrix

The trade date is supplied by the external authorization. Capability mode calls it `probe_trade_date` and makes no
freshness claim. Capture mode calls it `sampling_date` and additionally binds a pre-existing date-selection evidence
artifact proving that it is the latest complete common SSE/SZSE trading day under v1.1. The executor never chooses,
advances, rolls back, or substitutes a date.

All calls use an HTTPS `POST` to exactly `https://api.tushare.pro`. The matrix order is normative:

| Ordinal | API | Parameters | Frozen purpose |
|---:|---|---|---|
| 1 | `index_classify` | `level=L1`, `src=SW2021` | Validate the exact frozen taxonomy |
| 2 | `index_member_all` | `l1_code=801010.SI`, `is_new=Y` | 农林牧渔 |
| 3 | `index_member_all` | `l1_code=801030.SI`, `is_new=Y` | 基础化工 |
| 4 | `index_member_all` | `l1_code=801040.SI`, `is_new=Y` | 钢铁 |
| 5 | `index_member_all` | `l1_code=801050.SI`, `is_new=Y` | 有色金属 |
| 6 | `index_member_all` | `l1_code=801080.SI`, `is_new=Y` | 电子 |
| 7 | `index_member_all` | `l1_code=801110.SI`, `is_new=Y` | 家用电器 |
| 8 | `index_member_all` | `l1_code=801120.SI`, `is_new=Y` | 食品饮料 |
| 9 | `index_member_all` | `l1_code=801130.SI`, `is_new=Y` | 纺织服饰 |
| 10 | `index_member_all` | `l1_code=801140.SI`, `is_new=Y` | 轻工制造 |
| 11 | `index_member_all` | `l1_code=801150.SI`, `is_new=Y` | 医药生物 |
| 12 | `index_member_all` | `l1_code=801160.SI`, `is_new=Y` | 公用事业 |
| 13 | `index_member_all` | `l1_code=801170.SI`, `is_new=Y` | 交通运输 |
| 14 | `index_member_all` | `l1_code=801180.SI`, `is_new=Y` | 房地产 |
| 15 | `index_member_all` | `l1_code=801200.SI`, `is_new=Y` | 商贸零售 |
| 16 | `index_member_all` | `l1_code=801210.SI`, `is_new=Y` | 社会服务 |
| 17 | `index_member_all` | `l1_code=801230.SI`, `is_new=Y` | 综合 |
| 18 | `index_member_all` | `l1_code=801710.SI`, `is_new=Y` | 建筑材料 |
| 19 | `index_member_all` | `l1_code=801720.SI`, `is_new=Y` | 建筑装饰 |
| 20 | `index_member_all` | `l1_code=801730.SI`, `is_new=Y` | 电力设备 |
| 21 | `index_member_all` | `l1_code=801740.SI`, `is_new=Y` | 国防军工 |
| 22 | `index_member_all` | `l1_code=801750.SI`, `is_new=Y` | 计算机 |
| 23 | `index_member_all` | `l1_code=801760.SI`, `is_new=Y` | 传媒 |
| 24 | `index_member_all` | `l1_code=801770.SI`, `is_new=Y` | 通信 |
| 25 | `index_member_all` | `l1_code=801780.SI`, `is_new=Y` | 银行 |
| 26 | `index_member_all` | `l1_code=801790.SI`, `is_new=Y` | 非银金融 |
| 27 | `index_member_all` | `l1_code=801880.SI`, `is_new=Y` | 汽车 |
| 28 | `index_member_all` | `l1_code=801890.SI`, `is_new=Y` | 机械设备 |
| 29 | `index_member_all` | `l1_code=801950.SI`, `is_new=Y` | 煤炭 |
| 30 | `index_member_all` | `l1_code=801960.SI`, `is_new=Y` | 石油石化 |
| 31 | `index_member_all` | `l1_code=801970.SI`, `is_new=Y` | 环保 |
| 32 | `index_member_all` | `l1_code=801980.SI`, `is_new=Y` | 美容护理 |
| 33 | `stock_basic` | `exchange=SSE`, `list_status=L` | SSE listed-security eligibility |
| 34 | `stock_basic` | `exchange=SZSE`, `list_status=L` | SZSE listed-security eligibility |
| 35 | `stock_st` | `trade_date=<authorization date as YYYYMMDD>` | Historical date-aligned ST status |
| 36 | `daily_basic` | `trade_date=<authorization date as YYYYMMDD>` | Date-aligned total market value |

The exact requested fields, in order, are:

- `index_classify`: `index_code,industry_name,level,src`
- every `index_member_all`: `l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,ts_code,name,in_date,out_date,is_new`
- both `stock_basic`: `ts_code,name,market,exchange,curr_type,list_status,list_date`
- `stock_st`: `ts_code,name,trade_date,type,type_name`
- `daily_basic`: `ts_code,trade_date,total_mv`

Every request body is a UTF-8 compact JSON object containing exactly `api_name`, `token`, `params`, and `fields`.
`params` contains exactly the matrix parameters for that ordinal; `fields` is the corresponding comma-separated
field sequence above, constructed as the ASCII-comma join of the authorization call object's `fields` array with no
spaces. Extra keys, empty compatibility parameters, `offset`, `limit`, page controls, or SDK-added
arguments are forbidden. The per-response ceiling is 32 MiB and the whole-attempt response ceiling is 128 MiB.

Each ordinal may be attempted at most once. There is no automatic retry, bulk fallback, pagination, supplemental
request, alternate parameter, date fallback, SDK call, MCP call, SWS call, AKShare call, or production-database read.
An implementation may not parallelize calls; the fixed order makes authorization expiry, quota use, receipts, and
failure location deterministic.

### 3.1 Canonical capture-date evidence

Capability mode needs no date-freshness evidence: its authorized `probe_trade_date` tests access and capacity only.
Capture mode is different. Before a capture authorization may be created, a separate, pre-existing, separately
authorized date-evidence attempt must establish `sampling_date`. This auxiliary attempt is not a frame-source mode,
does not make any of the 36 frame calls, and its bytes are `date_selection_only=true`; they can never supply a frame
row, market value, membership, resume point, or substitute capture response.

The date-evidence authorization binds the frozen v1.3 protocol hash, its own authorization/attempt IDs and output
root, a proposed sampling date, an inclusive execution window, 4 MiB per-response and 8 MiB total ceilings, and
exactly these two sequential HTTPS POST calls, each at most once and without retry or pagination:

1. `trade_cal(exchange="SSE", start_date=<D-62>, end_date=<D+62>)`;
2. `trade_cal(exchange="SZSE", start_date=<D-62>, end_date=<D+62>)`.

Here `D` is the proposed sampling date and `D-62`/`D+62` are Gregorian calendar-date arithmetic, formatted
`YYYYMMDD`. The authorization contains those two resulting dates and the exact matrix; the executor recomputes and
compares them before both calls. Requested fields are exactly
`exchange,cal_date,is_open,pretrade_date`. The authoritative inputs are the exact raw Tushare `trade_cal` response
blobs from both exchanges, not an operator assertion, local holiday package, production database, or current wall
date. The transport, secret boundary, authorization revalidation, raw-first publication, ordinal state machine, and
offline restrictions in Sections 4–6 and 8 apply identically to this two-call attempt.

Each successful calendar blob must contain exactly one row for every Gregorian date in the inclusive 125-day range,
no date outside it, the requested exchange in every row, `is_open` exactly integer `0` or `1`, and
`pretrade_date` either an eight-digit date or JSON null. Duplicate/missing dates, pagination residue, positive count
mismatch, malformed fields, or a range that does not contain both the derived date and its next common open day fails.
The first source is SSE and the second is SZSE. Offline derivation is exactly:

1. Parse both raw blobs with JSON numbers preserved as exact lexemes and validate the preceding complete-row rules.
2. Define `common_open` as the ordered intersection of dates whose SSE and SZSE rows both have `is_open=1`.
3. Let `sealed_at` be a real-clock, timezone-aware instant with whole-second precision and explicit `+08:00`, no
   earlier than either receipt's `captured_at` and inside the date-evidence authorization window.
4. For each `d` in `common_open`, define its completion cutoff as `d 18:00:00+08:00`. Derive `sampling_date` as the
   greatest `d` whose cutoff is not later than `sealed_at`. It must equal proposed `D`.
5. Derive `next_common_open_date` as the least common-open date greater than `sampling_date`; absence fails.
6. Set `valid_from` to `sampling_date 18:00:00+08:00` and `valid_until` to
   `next_common_open_date 17:59:59+08:00`, both inclusive.

The conservative 18:00 cutoff is a frozen protocol assumption for completion of that day's `daily_basic`; the
prior common trading day remains the latest complete day through 17:59:59 on the next common trading day, and that
next day becomes complete exactly at 18:00. A capture authorization must have the same `sampling_date`,
`not_before >= sealed_at`, `not_before >= valid_from`, and `not_after <= valid_until`. Thus stale evidence cannot
authorize a later capture window. Expiry requires a new date-evidence authorization and attempt; it never rolls the
date automatically.

Formal capture has an additional, stricter current-snapshot boundary because `index_member_all(is_new=Y)` and
`stock_basic(list_status=L)` have no historical date parameter. Its authorization must satisfy
`not_before >= sampling_date 18:00:00+08:00` and `not_after <= sampling_date 23:59:59+08:00`; all 36 calls, blobs,
receipts, gates, and PASS manifest must seal inside that same civil-date interval. Date evidence may remain
semantically valid on the next common day, but it cannot then authorize these undated current snapshots. There is
therefore no calendar-date interval in which membership, listing status, or official stock name can change between
the authorized `sampling_date` and capture. Missing the same-day window fails closed and requires new date evidence
for a later sampling date; no historical reconstruction or next-day reuse is allowed.

The create-only canonical date-evidence document has exactly these top-level properties and types (no extensions):

```text
schema_version:              string, exactly "m4-date-selection-evidence-v1"
protocol_sha256:             string, 64 lowercase hexadecimal characters
authorization_id:            non-empty string
attempt_id:                  non-empty string
sealed_at:                   RFC 3339 whole-second string with explicit +08:00
calendar_start:              string, YYYY-MM-DD
calendar_end:                string, YYYY-MM-DD
sampling_date:               string, YYYY-MM-DD
next_common_open_date:       string, YYYY-MM-DD
valid_from:                  RFC 3339 whole-second string with explicit +08:00
valid_until:                 RFC 3339 whole-second string with explicit +08:00
date_selection_only:         boolean, exactly true
sources:                     array of exactly two source objects, SSE then SZSE
```

Each source object has exactly `exchange`, `call_id`, `blob_relative_path`, `blob_sha256`, `byte_count`,
`receipt_relative_path`, and `receipt_sha256`. `exchange` is respectively `SSE` or `SZSE`; `call_id` is respectively
`tushare:trade_cal:SSE` or `tushare:trade_cal:SZSE`; paths are non-empty normalized relative paths without `..`;
hashes are 64 lowercase hexadecimal strings; and `byte_count` is a non-negative JSON integer. The document uses the
canonical JSON byte encoding defined in Section 7. Its SHA-256 is over those complete bytes, including the terminal
LF. Capture authorization binds both its relative path and hash. Token-free verification must reopen its two raw
blobs and receipts, re-run every row check and steps 1–6, reproduce the exact canonical document bytes, and recheck
the capture-window inequalities; checking only stored dates or hashes is insufficient.

## 4. Transport and secret boundary

The only permitted network destination is DNS host `api.tushare.pro` on TCP port 443 using certificate-verified TLS.
The URL has no query, fragment, userinfo, or token-bearing path. Redirects, cookies, all proxy use, alternate IP
literals, and cross-origin connections are forbidden. HTTP method and body shape are
fixed to the documented Tushare Pro JSON POST contract.

The Tushare token is supplied only through a designated environment variable after initial authorization validation.
It exists only in process memory and the HTTPS request body. It must never appear in a command line, MCP URL,
authorization, request description, request log, header dump, exception object, stdout, stderr, receipt, manifest,
blob name, or versioned file. The implementation must not accept a token through a CLI option or persisted config.
The authorized variable must exist, be readable, and contain exactly 64 lowercase ASCII hexadecimal characters with
no whitespace or line ending. Missing/unreadable is `credential_unavailable`; empty or malformed is
`credential_invalid`. Both occur before transport, produce no ordinal receipt or blob, leave the next ordinal
unattempted, and propagate through the stop record and incomplete manifest without preserving the value.

Before any provider bytes are published, they are scanned for the exact token. A token echo is a security failure:
the response is not written, later calls are not attempted, and an incomplete sanitized attempt is sealed. All
published files and captured stdout/stderr are scanned again before final sealing.

HTTP and transport exceptions map to a finite credential-free error vocabulary. Raw exception objects, request
bodies, request headers, cookies, environment values, and tracebacks containing request internals are never
persisted.

## 5. External authorization and attempt control

No executable governed by this protocol may create, edit, renew, extend, or backdate an authorization. Execution
requires a pre-existing canonical JSON authorization and a matching SHA-256 file. Authorization objects reject every
unknown or missing property. Their canonical encoding is Section 7 JSON. The checksum file is exactly one lowercase
SHA-256, two ASCII spaces, the authorization's project-relative normalized path, and LF.

Every call object has exactly `ordinal` (positive integer), `api_name` (string), `params` (object whose exact string
keys/values are fixed by the applicable matrix), and `fields` (ordered array of exact field-name strings). Every
reference object has exactly `relative_path`, `sha256`, and `attempt_id`; the date-evidence reference additionally
has `authorization_id`. Paths are normalized project-relative strings without empty, absolute, `.` or `..` segments;
hashes are lowercase 64-hex strings. Authorization and attempt IDs match exactly
`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` and cannot be `.` or `..`.

### 5.1 Exact frame-authorization schema

A frame authorization has exactly these properties and no others:

```text
schema_version:                 exactly "m4-segmented-rest-frame-authorization-v1"
authorization_id:               non-empty string
attempt_id:                     non-empty string
mode:                           "capability" | "capture"
protocol_path:                  exactly this protocol's normalized project-relative path
protocol_sha256:                lowercase 64-hex string matching frozen bytes
supersedes_sha256:              exactly the v1.2 SHA-256 in this protocol header
origin:                         exactly "https://api.tushare.pro"
credential_env_var:             exactly "TUSHARE_TOKEN"
attempt_output_dir:             exact permitted root plus attempt_id
response_byte_limit:            exactly 33554432
attempt_byte_limit:             exactly 134217728
max_attempts_per_ordinal:       exactly 1
trade_date_kind:                "probe_trade_date" for capability; "sampling_date" for capture
trade_date:                     YYYY-MM-DD string
not_before:                     RFC 3339 whole-second string with explicit +08:00
not_after:                      same format, not earlier than not_before
calls:                          exact ordered 36-call matrix
capability_manifest_ref:        JSON null for capability; reference object for capture
date_evidence_ref:              JSON null for capability; date-evidence reference object for capture
```

For capability, `attempt_output_dir` is under the capability root and both references are null. For capture it is
under the capture root, both references are non-null, the capability manifest semantically verifies PASS, and every
date/reference/window equality and same-sampling-date snapshot boundary in Section 3.1 holds. The daily call date is
exactly `trade_date` without hyphens.
The stock-ST call uses that identical hyphen-free value.

The normative non-executable capability-authorization vector uses the exact 36 calls/fields in Section 3, all-zero
`protocol_sha256`, `authorization_id=auth-example`, `attempt_id=cap-example`, `mode=capability`, the protocol path in
this document, the header's supersedes hash, fixed origin/credential variable/limits above,
`attempt_output_dir=artifacts/milestone-004/v1.3/capability-probes/cap-example`,
`trade_date_kind=probe_trade_date`, `trade_date=2026-07-15`, `not_before=2026-07-17T00:00:00+08:00`,
`not_after=2026-07-17T00:10:00+08:00`, and both references null. Its canonical byte count is 8,066 and SHA-256,
including LF, is `5dee6f7f3f9f791b0210c5f30ff70b07662170b5c01b093a30038e68eb347c9d`.

### 5.2 Exact date-evidence-authorization schema

A date-evidence authorization has exactly these properties and no others:

```text
schema_version:                    exactly "m4-date-selection-authorization-v1"
authorization_id:                  non-empty string
attempt_id:                        non-empty string
purpose:                           exactly "date_selection_evidence"
protocol_path:                     exactly this protocol's normalized project-relative path
protocol_sha256:                   lowercase 64-hex string matching frozen bytes
supersedes_sha256:                 exactly the v1.2 SHA-256 in this protocol header
origin:                            exactly "https://api.tushare.pro"
credential_env_var:                exactly "TUSHARE_TOKEN"
attempt_output_dir:                exact date-evidence root plus attempt_id
response_byte_limit:               exactly 4194304
attempt_byte_limit:                exactly 8388608
max_attempts_per_ordinal:          exactly 1
proposed_sampling_date:            YYYY-MM-DD string
calendar_start:                    YYYY-MM-DD string, exactly proposed date minus 62 days
calendar_end:                      YYYY-MM-DD string, exactly proposed date plus 62 days
not_before:                        RFC 3339 whole-second string with explicit +08:00
not_after:                         same format, not earlier than not_before
calls:                             exact ordered two-call matrix in Section 3.1
capability_manifest_ref:           non-null reference object that semantically verifies PASS
```

The executor validates all exact properties and cross-field rules before token access, then repeats the validation
before every call. A syntactically valid but semantically mismatched property fails closed; there are no defaults.

This normative serialization vector is deliberately non-executable because `protocol_sha256` is all zeroes. It shows
canonical date-authorization bytes without the final LF; the LF is included in the hash:

```json
{"attempt_byte_limit":8388608,"attempt_id":"date-example","attempt_output_dir":"artifacts/milestone-004/v1.3/date-selection-evidence/date-example","authorization_id":"auth-example","calendar_end":"2026-09-15","calendar_start":"2026-05-14","calls":[{"api_name":"trade_cal","fields":["exchange","cal_date","is_open","pretrade_date"],"ordinal":1,"params":{"end_date":"20260915","exchange":"SSE","start_date":"20260514"}},{"api_name":"trade_cal","fields":["exchange","cal_date","is_open","pretrade_date"],"ordinal":2,"params":{"end_date":"20260915","exchange":"SZSE","start_date":"20260514"}}],"capability_manifest_ref":{"attempt_id":"cap-example","relative_path":"artifacts/milestone-004/v1.3/capability-probes/cap-example/attempt-manifest.json","sha256":"1111111111111111111111111111111111111111111111111111111111111111"},"credential_env_var":"TUSHARE_TOKEN","max_attempts_per_ordinal":1,"not_after":"2026-07-17T00:10:00+08:00","not_before":"2026-07-17T00:00:00+08:00","origin":"https://api.tushare.pro","proposed_sampling_date":"2026-07-15","protocol_path":"docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md","protocol_sha256":"0000000000000000000000000000000000000000000000000000000000000000","purpose":"date_selection_evidence","response_byte_limit":4194304,"schema_version":"m4-date-selection-authorization-v1","supersedes_sha256":"b711b9b81d8732b48f49c25b419dd9cf51eeae8aa4d2f7bbcd0a662a16782c23"}
```

Its SHA-256 is `965af6eedfca64b0eb4860c5e2c18c0d4cbe7067816e29ab819fd734a5e03ca4`.

Before every network call, the executor re-reads and verifies canonical authorization bytes, its checksum, the
protocol file and hash, mode or purpose, attempt, output root, real timezone-aware clock, applicable date values,
complete applicable matrix, next ordinal, and current closed artifact set. A mismatch or expired window prevents
that call.

`execution_deadline` is the authorization `not_after`; capture also recomputes it as
`min(not_after,date_evidence.valid_until,sampling_date 23:59:59+08:00)` and requires equality because capture
`not_after` cannot exceed either bound. Before transport, real time must be strictly earlier than the deadline. DNS, connect, TLS, request write,
response headers, and complete body read share one hard monotonic timeout equal to the remaining real-time interval;
no byte may be accepted after the deadline. Token scan, blob fsync, parsing, receipt fsync, gate evaluation, and PASS-
manifest fsync must also finish no later than the inclusive deadline. Every post-fsync time is sampled from the real
timezone-aware clock and manifest-bound. An overrun at any phase stops all later calls and makes
`authorization_window=false`; no capability/capture/date PASS or adoptability is then possible. Sanitized failure
artifacts may be sealed after expiry only with a failure disposition and the actual later timestamp.

An attempt ID and output directory are create-only. A lock is acquired before token access or transport construction.
Concurrent execution, an existing attempt, a symlink, path traversal, or an output root outside the authorized root
blocks before networking.

The only permitted roots relative to the project are:

- capability: `artifacts/milestone-004/v1.3/capability-probes/`
- capture: `artifacts/milestone-004/v1.3/captures/`
- date evidence: `artifacts/milestone-004/v1.3/date-selection-evidence/`

The authorization binds the appropriate exact root, and the executor creates only one child named from the validated
attempt ID. Absolute-path aliases and paths that resolve through a symlink are forbidden. All three root classes are
pairwise non-overlapping and bytes may not be copied, hard-linked, or resumed across them.
The concurrency lock and all publication temporary files live outside the attempt child; they are never part of its
closed file set. They are removed only after the child is sealed, and stale locks fail closed rather than being
silently broken.

## 6. Raw-first capture and failure behavior

Every matrix ordinal has exactly one of four terminal states:

- `success_blob`: transport completed, the exact token-safe response fit both byte ceilings, the raw blob was
  fsync-sealed before parsing, and HTTP/provider/base-schema validation succeeded;
- `terminal_failure_blob`: the exact token-safe response fit both ceilings and was fsync-sealed raw-first, but an
  HTTP, provider, malformed-JSON, or base-schema failure was then diagnosed, or process control arrived after blob
  fsync but before a success receipt was sealed;
- `terminal_failure_no_blob`: transport began but no safe complete response can be published because of transport
  failure, token echo, per-response/attempt-total size breach, or process control before blob fsync completed;
- `unattempted`: transport for this ordinal never began; it has neither blob nor receipt.

There is exactly one create-only receipt for each attempted ordinal (`success_blob`, `terminal_failure_blob`, or
`terminal_failure_no_blob`) and no receipt for `unattempted`. A fail-fast attempt has at most one terminal-failure
ordinal. All later ordinals are `unattempted`; successful earlier blobs and receipts remain sealed. A failure found
only by the later data-quality gates does not rewrite ordinal states: all API-success ordinals remain
`success_blob`, `complete=true`, and the separate gate/disposition is FAIL.

A receipt is canonical JSON and contains exactly these properties:

```text
schema_version:       string, exactly "m4-segmented-rest-receipt-v1"
ordinal:              positive JSON integer
call_id:              non-empty string fixed by the authorized matrix
state:                "success_blob" | "terminal_failure_blob" | "terminal_failure_no_blob"
authorization_id:     non-empty string
attempt_id:           non-empty string
captured_at:          RFC 3339 whole-second string with explicit +08:00
request_description:  exact credential-free request-description object below
status_category:      "success" | "http" | "provider" | "parse" | "schema" | "transport" | "size" | "security" | "authorization" | "process_control"
error_code:           string or JSON null
provider_code:        JSON integer or JSON null
http_status:          JSON integer or JSON null
content_type:         string or JSON null
byte_count:           non-negative JSON integer or JSON null
blob_relative_path:   normalized relative-path string or JSON null
blob_sha256:          64-character lowercase hexadecimal string or JSON null
```

The request-description object has exactly `origin`, `method`, `api_name`, `params`, and `fields`: origin is
`https://api.tushare.pro`, method is `POST`, API/parameters are the exact authorized call, and `fields` is an array
of field-name strings in requested order. It has no token, headers, body dump, environment data, or extensions.
Receipt canonicalization and hash use the Section 7 JSON rule, including terminal LF.

Receipt `call_id` is derived, not operator-selected: `tushare:index_classify`,
`tushare:index_member_all:<l1_code>`, `tushare:stock_basic:SSE`, `tushare:stock_basic:SZSE`, or
`tushare:stock_st:<YYYYMMDD>`, or `tushare:daily_basic:<YYYYMMDD>` for the frame matrix, and the two IDs in
Section 3.1 for date evidence.

For `success_blob`, `status_category="success"`, `error_code=null`, `http_status=200`, `provider_code=0`, and all
four blob metadata fields (`content_type`, `byte_count`, `blob_relative_path`, `blob_sha256`) are non-null and match
the sealed bytes. For `terminal_failure_blob`, those four blob fields are likewise non-null and matching,
`error_code` is exactly one of `http_error`, `provider_error`, `malformed_json`, `schema_error`, or
`process_control_interrupt_after_blob` or `authorization_window_overrun_after_blob`, and
`status_category` matches it; unavailable HTTP/provider values are null. For `terminal_failure_no_blob`, all four
blob metadata fields are null, `error_code` is exactly one of `transport_error`, `response_too_large`,
`attempt_total_too_large`, `token_echo`, `process_control_interrupt_before_blob`, or
`authorization_window_overrun_before_blob`, and `status_category` matches it;
`http_status` and `provider_code` are null unless a credential-free value was safely obtained before the failure.
The exact error/category map is `http_error→http`, `provider_error→provider`, `malformed_json→parse`,
`schema_error→schema`, both process-control codes to `process_control`, `transport_error→transport`, both size codes
to `size`, `token_echo→security`, and both window-overrun codes to `authorization`. No partial or token-bearing bytes
are published.

If authorization, clock, lock, protocol, output-set, or process-control validation fails before transport for the
next ordinal begins, that ordinal remains `unattempted` and has no receipt. Best-effort sealing instead creates one
canonical attempt-level stop record with exactly `schema_version="m4-attempt-stop-v1"`, `authorization_id`,
`attempt_id`, `next_ordinal` (the positive matrix ordinal that remained unattempted, or JSON null only after the final
success receipt), `stop_code`, and `stopped_at`.
`stop_code` is exactly one of `authorization_invalid`, `authorization_not_yet_valid`, `authorization_expired`,
`authorization_drift`, `protocol_drift`, `clock_invalid`, `lock_lost`, `output_drift`, `credential_unavailable`,
`credential_invalid`, or `process_control_interrupt`; `stopped_at` follows the receipt timestamp format. For a stop
after the final success receipt, `next_ordinal` is JSON null; otherwise it is the positive next unattempted ordinal.
No stop record is created for an ordinal having a terminal-failure receipt.

The manifest partitions the authorized matrix into three disjoint, ascending integer arrays named exactly
`successful_ordinals`, `terminal_failed_ordinals`, and `unattempted_ordinals`; their ordered union must equal every
expected ordinal. `terminal_failed_ordinals` has length zero or one. `failed_ordinal` is JSON null when it is empty
and otherwise equals its sole element. The attempted sequence is the matrix-order merge of the successful and
terminal-failed arrays. `complete=true` if and only if every expected ordinal is `success_blob`, with empty failed
and unattempted arrays. For frame modes there are 36 expected ordinals; for date evidence there are two.

Any terminal failure stops the remaining matrix without retry. `KeyboardInterrupt`, `SystemExit`, and
`GeneratorExit` are re-raised unchanged after best-effort sealing. The exact process-control phase rule is: before
transport, create a stop record and no receipt; from transport start through the instant before blob fsync completes,
create `terminal_failure_no_blob`; after blob fsync through the instant before a success receipt fsync completes,
create `terminal_failure_blob`; after a success receipt fsync completes, retain `success_blob` and create a stop
record for the next ordinal (or null after the final ordinal). Gate results not yet evaluated remain null and cannot
PASS. Artifact writes use a signal-deferred atomic create-only publication critical section, so a canonical receipt,
stop record, and manifest is either absent or complete—never a partial closed-set file. Completion remains
independent of capability PASS, capture eligibility, or any later data-quality result.

The same publication boundaries apply to deadline overrun: before blob fsync it creates the no-blob overrun receipt;
after blob fsync but before success-receipt fsync it creates the blob-bearing overrun receipt; after success-receipt
fsync it preserves success, seals `authorization_expired` for the next ordinal (or null after the final ordinal), and
sets the manifest authorization-window gate false. If process control and expiry are observed together, expiry takes
precedence whenever the sampled real clock is later than the deadline. A failure manifest may document a post-window
seal, but a PASS manifest and every referenced success blob/receipt post-fsync timestamp must all be at or before
`execution_deadline`. For manifest publication, `sealed_at` is the last real-clock sample
immediately before its atomic link; the link plus directory fsync is covered by the same hard watchdog and must return
by the deadline. A timeout leaves the PASS candidate outside the attempt closed set and permits only a failure
manifest; it cannot expose the candidate at `attempt-manifest.json`.

## 7. Response and completeness gates

All JSON numbers are parsed without binary floating-point conversion. `total_mv` is retained as its exact decimal
lexeme and converted to a finite positive `Decimal` only for validation and sampling. Provider success requires
`code=0`, `msg` absent, JSON null, or the empty string, a `data.fields` sequence exactly matching the requested
fields, and row widths matching that sequence.

Unless a raw provider blob is explicitly being preserved byte-for-byte, every canonical JSON artifact in this
protocol is UTF-8 without BOM, recursively key-sorted, compact (`','` and `':'` separators), Unicode-preserving
(`ensure_ascii=false`), and terminated by exactly one LF byte. Duplicate object keys, NaN, infinity, negative zero
normalization, or binary-float substitution are rejected. SHA-256 is over the complete bytes including that LF.

### 7.1 Classification gate

The classification response must contain exactly the 31 frozen `index_code`/`industry_name` pairs. Every row has
`level=L1` and `src=SW2021`. Duplicate, missing, unknown, aliased, fuzzy-matched, or crossed code/name pairs fail.

### 7.2 Per-industry membership gates

Each of the 31 responses must satisfy all of the following:

1. It contains at least one row and strictly fewer than 2,000 rows. Exactly 2,000 rows is treated as possible
   truncation and fails without an independent completeness proof; this protocol defines no override.
2. Every row has the requested frozen `l1_code` and matching frozen `l1_name`, `is_new=Y`, and `out_date` equal to
   JSON null or the empty string. `in_date` is a real Gregorian `YYYYMMDD` not later than the authorized trade date;
   a current row that cannot be proven active on that date fails.
3. Every raw `ts_code` matches exactly `^[0-9]{6}\.(SH|SZ|BJ)$`; any other suffix or malformed code fails. Every
   company name is non-empty after Unicode-preserving outer-whitespace removal.
4. A recognized `ts_code` occurs exactly once in its response and in exactly one of the 31 responses. Duplicate L1
   membership, including a duplicate `.BJ` row and even with identical name or industry values, fails closed.
5. Any positive provider count equals the number of returned rows. An absent count or documented zero unknown-total
   sentinel is not completeness evidence but is permitted because the exhaustive L1 partition is below the service
   ceiling. `has_more` must be absent or false, and cursor, next-page, offset, or pagination residue must be absent.

The offline verifier derives two disjoint sets without changing raw blobs:

- `exchange_memberships`: only exact `.SH` and `.SZ` rows, the pre-eligibility SSE/SZSE membership population;
- `out_of_scope_memberships`: structurally valid `.BJ` rows, which are recognized as BSE and excluded before the
  exchange-membership union, daily-coverage check, frame, and sampling gates.

Every exclusion is written to one create-only canonical ledger with exactly five top-level properties:
`schema_version` (exactly `m4-exclusion-ledger-v1`), `authorization_id` (non-empty string), `attempt_id` (non-empty
string), `mode` (exactly `capability` or `capture`), and `entries` (array). No extension properties are permitted.
Each entry has exactly these properties and types:

```text
canonical_row_sha256: 64-character lowercase hexadecimal string
l1_code:               frozen L1-code string, or JSON null as source rules require
l1_name:               frozen L1-name string, or JSON null as source rules require
name:                  exact raw provider string, or JSON null only for a daily row
provider_row_ordinal:  positive JSON integer, one-based index in the original data.items array
reason_code:           finite reason code defined below
source_kind:           "membership" | "stock_basic" | "stock_st" | "daily"
source_ordinal:        positive JSON integer, one-based ordinal in the 36-call frame matrix
ts_code:               exact raw provider string
```

Membership entries must use source ordinals 2–32, non-null matching frozen L1 fields, and only
`exchange_out_of_scope_bse` or `not_in_frame_eligible_membership`. Stock-basic entries use ordinal 33 or 34; their L1 fields are the matching membership
pair when one exists and otherwise null. Stock-ST entries use ordinal 35 and the same joined/null L1 rule. Daily
entries use ordinal 36 and always have `l1_code=null`, `l1_name=null`,
and `name=null` because `daily_basic` has no name field. The finite reason codes are exactly
`exchange_out_of_scope_bse`, `not_in_frozen_sw2021_membership`, `security_type_out_of_scope`, `st_risk_warning`,
`delisting_consolidation`, `listing_age_below_36_months`, and `not_in_frame_eligible_membership`. Membership permits
only BSE or frame-ineligible; stock-basic permits only nonmembership plus the four stock-eligibility reasons;
stock-ST permits only BSE, nonmembership, or ST risk warning; daily permits only BSE,
nonmembership, or frame-ineligible membership. A non-null `name` preserves every raw Unicode code point
and outer whitespace; the separately required non-empty-name check uses Unicode-preserving outer-whitespace removal
only as a predicate and never changes ledger bytes.

For every excluded row, `canonical_row_sha256` hashes a canonical object with exactly `fields` and `values`.
`fields` is the response's exact requested field-name array in order. `values` has the same length and contains
objects with exactly `type` and `value`: a JSON string cell uses `{"type":"string","value":<exact raw string>}`;
a number uses `{"type":"number","value":<exact JSON numeric lexeme as a string>}`; a boolean uses
`{"type":"boolean","value":<JSON boolean>}`; and null uses `{"type":"null","value":null}`. Its canonical
bytes and row hash follow the Section 7 rule, including terminal LF.

Entries are sorted by the exact tuple `(source_kind_rank, ts_code, l1_code_or_empty, canonical_row_sha256,
source_ordinal, provider_row_ordinal)`, comparing strings by Unicode scalar-value sequence and integers numerically.
`source_kind_rank` is derived only for sorting (`membership=0`, `stock_basic=1`, `stock_st=2`, `daily=3`) and is not serialized;
`l1_code_or_empty` is likewise derived (`l1_code` when non-null, otherwise the empty string) and is not serialized.
The complete ledger SHA-256 hashes the canonical five-property outer object, including terminal LF. Ledger counts
and hash are bound into the attempt manifest and every byte is recomputed from raw blobs during offline verification.

The following normative golden vector shows canonical bytes without the final LF for display; the LF is present in
both hash preimages. For a membership response whose fields are the requested sequence and whose first raw row is
`["801010.SI","农林牧渔",null,null,null,null,"920001.BJ","示例北交所公司 ","20240102",null,"Y"]`, the row bytes are:

```json
{"fields":["l1_code","l1_name","l2_code","l2_name","l3_code","l3_name","ts_code","name","in_date","out_date","is_new"],"values":[{"type":"string","value":"801010.SI"},{"type":"string","value":"农林牧渔"},{"type":"null","value":null},{"type":"null","value":null},{"type":"null","value":null},{"type":"null","value":null},{"type":"string","value":"920001.BJ"},{"type":"string","value":"示例北交所公司 "},{"type":"string","value":"20240102"},{"type":"null","value":null},{"type":"string","value":"Y"}]}
```

Its SHA-256 is `dffd74abfdd8d08e9476e162f9db943e2116b79d091e9b9bfcee157a32be4986`. With authorization
`auth-example`, attempt `attempt-example`, capability mode, source ordinal 2, and provider row ordinal 1, the complete
one-entry ledger bytes are:

```json
{"attempt_id":"attempt-example","authorization_id":"auth-example","entries":[{"canonical_row_sha256":"dffd74abfdd8d08e9476e162f9db943e2116b79d091e9b9bfcee157a32be4986","l1_code":"801010.SI","l1_name":"农林牧渔","name":"示例北交所公司 ","provider_row_ordinal":1,"reason_code":"exchange_out_of_scope_bse","source_kind":"membership","source_ordinal":2,"ts_code":"920001.BJ"}],"mode":"capability","schema_version":"m4-exclusion-ledger-v1"}
```

Its SHA-256 is `24fedaf7bad7145e00ccbf0e5a4fb62630600baebed0d7a339f119446203bb97`.

For a daily response whose fields are `ts_code,trade_date,total_mv` and whose first raw row is
`["920001.BJ","20260715",123.4500]`, the numeric lexeme and absent name produce these canonical row bytes (again
shown without the hashed LF):

```json
{"fields":["ts_code","trade_date","total_mv"],"values":[{"type":"string","value":"920001.BJ"},{"type":"string","value":"20260715"},{"type":"number","value":"123.4500"}]}
```

The row SHA-256 is `72242aacb24b33de57135e82bae4ab1917a173d67c4ad521dd5da394e5f8023e`. Its complete
one-entry capability ledger is:

```json
{"attempt_id":"attempt-example","authorization_id":"auth-example","entries":[{"canonical_row_sha256":"72242aacb24b33de57135e82bae4ab1917a173d67c4ad521dd5da394e5f8023e","l1_code":null,"l1_name":null,"name":null,"provider_row_ordinal":1,"reason_code":"exchange_out_of_scope_bse","source_kind":"daily","source_ordinal":36,"ts_code":"920001.BJ"}],"mode":"capability","schema_version":"m4-exclusion-ledger-v1"}
```

Its SHA-256 is `22b4e058f38d819d6e1103920c422f1350a33d109968a8dd78b0f47887093ae2`.

After the exchange-membership union, there must be at least 4,000 unique valid `.SH`/`.SZ` stocks and all 31 frozen
industries must remain represented by exchange-membership rows. `.BJ` rows never contribute to either gate. No raw row is silently
discarded, repaired, or treated as an unknown exchange.

### 7.3 Listed-security eligibility gate

In capture mode, every membership and stock-basic request/response/blob/receipt is constrained by Section 3.1 to the
authorized sampling date after the 18:00 completion cutoff and before midnight. Those provider-current responses are
therefore the authoritative same-date membership, listing-status, market/type, official-name, and listing-date
snapshots; they are never carried into a later civil date or used to reconstruct a prior date. Capability mode makes
no formal date-effective frame claim.

Each `stock_basic` response must contain at least one and strictly fewer than 6,000 rows. Exactly 6,000 is possible
truncation and fails without an independent completeness proof. Every row must have exact requested fields, a unique
code matching `^[0-9]{6}\.(SH|SZ)$` and the requested exchange suffix, `exchange` equal to the request, exactly
`list_status=L`, a non-empty raw name, `curr_type` and `market` strings, and a real Gregorian `list_date` in
`YYYYMMDD`. Positive count must equal row count; `has_more`, cursor, offset, next page, or pagination residue fails.

The date-bound `stock_st` response may contain zero rows and must contain strictly fewer than 1,000; exactly 1,000 is
possible truncation and fails. Every row has exact requested fields, a unique code matching
`^[0-9]{6}\.(SH|SZ|BJ)$`, a non-empty raw name, the exact authorized date, and non-empty string `type` and
`type_name`. Positive count must equal row count and any pagination residue fails. `.BJ` rows are ledgered as BSE;
extra `.SH`/`.SZ` rows outside the membership union are ledgered as nonmembers; joined `.SH`/`.SZ` rows are
authoritative date-aligned ST exclusions and receive `st_risk_warning` stock-ST ledger entries.

Every exchange-membership code must join exactly one stock row; a missing or duplicate join fails. Every extra stock
row not in the frozen membership union is ledgered as `not_in_frozen_sw2021_membership`. For joined rows, the exact
inherited v1 eligibility predicates are evaluated in this order, and the first failure is the sole ledger reason:

1. `curr_type` is exactly `CNY`, and `(exchange, market, first three code digits)` is exactly one of
   `(SSE,主板,600|601|603|605)`, `(SSE,科创板,688)`, `(SZSE,主板,000|001|002|003)`, or
   `(SZSE,创业板,300|301)`; otherwise `security_type_out_of_scope`. Together with the official `stock_basic`
   A-share contract, this fail-closed tuple explicitly rejects the CDR-associated `689` family and excludes B shares,
   other CDRs, funds, bonds, preferred shares, BSE, crossed board/exchange values, and unknown security
   representations. A new valid code family requires a new protocol.
2. Apply Unicode NFKC to raw `name`, remove only Unicode outer whitespace, and call the result `normalized_name`.
   If it begins exactly `退市` or ends exactly `退`, the reason is `delisting_consolidation`. No substring, fuzzy, or
   operator-maintained list is used.
3. Unicode-casefold `normalized_name`. If it begins exactly `ST` or `*ST`, or the code occurs in the validated
   date-bound `stock_st` response, the reason is `st_risk_warning`.
4. Compute the 36-month anniversary of `list_date` by adding 36 calendar months while retaining the day, or using
   the target month's last day when that day does not exist. If the anniversary is after the authorized trade date,
   the reason is `listing_age_below_36_months`.

Rows passing all four rules form `frame_eligible_memberships`. Every raw stock row is therefore either joined into
that set or represented once in the ledger. The verifier re-derives the join, priority reason, normalized ST
and delisting predicates, historical ST join, and anniversary from raw stock, stock-ST, and membership blobs. No name from membership, local security master,
database, or operator correction may replace the stock response name; the frame's company name is the exact raw
stock response name. Every exchange-membership row excluded by a joined stock row also receives its own membership-
source ledger entry with `not_in_frame_eligible_membership`, preserving that raw membership-row hash separately from
the stock row's first-priority reason. If `stock_st` is the first failing predicate, the joined stock-basic row also
receives `st_risk_warning`, so every raw stock row excluded from the frame remains accounted for.

### 7.4 Daily market-value gate

The single `daily_basic` response must contain at least 4,000 unique valid `.SH`/`.SZ` rows, while the total provider
item count across all recognized suffixes must be strictly fewer than 6,000. Exactly 6,000 total items is possible
truncation and fails without an independent completeness proof. Every raw `ts_code` must match exactly
`^[0-9]{6}\.(SH|SZ|BJ)$`; unknown or malformed codes fail. Every row has the exact authorized date and a finite,
strictly positive `total_mv`; duplicate codes across any recognized suffix, invalid dates, zero, negative, NaN,
infinity, rounded binary-float substitution, pagination residue, or positive count mismatch fail.

Every unique exchange-membership code must have exactly one valid daily row. A `.BJ` membership does not
create a daily-coverage obligation. Structurally valid `.BJ` daily rows are retained in raw and added to the same
canonical exclusion ledger with reason `exchange_out_of_scope_bse`. Extra `.SH`/`.SZ` daily rows not present in the
exchange-membership union are retained in raw and added with reason `not_in_frozen_sw2021_membership`; daily rows
that join an exchange membership excluded by Section 7.3 are added with `not_in_frame_eligible_membership`. They
cannot introduce an industry assignment. Every daily entry uses `name=null`, JSON-null L1 fields, and an empty
`l1_code_or_empty` sort key because the response has neither name nor industry fields. A missing or invalid daily row
for any exchange-membership code fails rather than creating an operator exclusion or silent frame reduction.

### 7.5 Frame and sampling-cell gate

The exact `frame_eligible_memberships`/daily intersection is validated with the unchanged v1.1 frame rules. It must
retain one exact active L1 membership per stock, all listed-security predicates in Section 7.3, date-aligned finite
positive unrounded `Decimal total_mv`, and a valid `.SH`/`.SZ` suffix. Within each of the six super-strata, the
unchanged numeric `(total_mv, ts_code)` ordering, median split, seed, hash message, and sampling order must populate
at least three companies in each low/high cell.

Capability PASS requires all 12 cells to be fillable but publishes no frame or sample. Capture PASS permits offline
publication of exactly 36 unique sampled companies, six per super-stratum and 18 per cap layer, only after the full
frame and deterministic sample independently verify against v1.1.

## 8. Manifest, offline verification, and adoptability

The manifest is canonical JSON with exactly the following properties and no extensions:

```text
schema_version:             exactly "m4-segmented-rest-attempt-manifest-v1"
authorization_relative_path: normalized project-relative string
authorization_sha256:       lowercase 64-hex string
authorization_id:           non-empty string
attempt_id:                 non-empty string
attempt_kind:               "capability" | "capture" | "date_selection_evidence"
protocol_path:              exact normalized path of this protocol
protocol_sha256:            lowercase 64-hex hash of frozen protocol bytes
supersedes_sha256:          exact v1.2 hash in this protocol header
generator_files:            non-empty array of generator-reference objects
expected_ordinals:          exact ascending matrix ordinal array
successful_ordinals:        ascending integer array
terminal_failed_ordinals:   ascending integer array of length zero or one
unattempted_ordinals:       ascending integer array
failed_ordinal:             sole terminal-failed ordinal or JSON null
receipt_refs:               ascending-by-ordinal array of receipt-reference objects
blob_refs:                  ascending-by-ordinal array of blob-reference objects
stop_record_ref:            artifact-file reference object or JSON null
response_byte_total:        non-negative JSON integer
exclusion_ledger_ref:       artifact-file reference object or JSON null
exclusion_counts:           exact exclusion-count object below or JSON null
gate_results:               exact gate-results object below
complete:                   JSON boolean satisfying Section 6
disposition:                finite disposition string below
non_adoptable:              JSON boolean
capture_input_eligible:     JSON boolean
date_selection_only:        JSON boolean
artifact_files:             array binding the complete closed file set except this manifest
execution_deadline:         RFC 3339 whole-second string with explicit +08:00
sealed_at:                  RFC 3339 whole-second string with explicit +08:00
```

A generator-reference object has exactly `relative_path` and `sha256`. An artifact-file reference has exactly
`relative_path`, `sha256`, `byte_count`, and `kind`, where kind is `blob`, `receipt`, `stop_record`,
`exclusion_ledger`, or `date_evidence`. A receipt reference has exactly `ordinal`, `state`, `relative_path`,
`sha256`, and `sealed_at`; state is one of the three receipt-bearing states. A blob reference has exactly `ordinal`,
`relative_path`, `sha256`, `byte_count`, and `sealed_at`. Reference `sealed_at` is the real-clock sample immediately
after that file's fsync. All paths are normalized relative paths with no empty, absolute, `.` or `..` segment;
all hashes are lowercase 64-hex; byte counts are non-negative integers. Arrays contain no duplicate path or ordinal.
`artifact_files` is sorted by path using Unicode scalar order and is exactly the set of regular files under the
attempt directory other than `attempt-manifest.json`; every specialized reference must match its projection there.
After sealing, files are mode 0444 and directories 0555; symlinks and hard-link counts other than one fail.
Paths are derived exactly: blob ordinal `n` is `blobs/<n as four digits>-<blob_sha256>.bin`; its receipt is
`receipts/<n as four digits>.json`; the optional stop record is `stop-record.json`; the frame ledger is
`exclusion-ledger.json`; the date-evidence document is `date-selection-evidence.json`; and the manifest is
`attempt-manifest.json`. No alternate extension, case, padding, or directory is accepted.
Authorization/protocol/generator hashes must match the referenced canonical bytes; receipt/blob arrays must exactly
match ordinal states; `response_byte_total` equals the sum of blob byte counts and respects the authorization ceiling;
and manifest `sealed_at` is not earlier than any receipt/blob-ref or stop timestamp. `execution_deadline` is exactly
the derived Section 5 deadline. An overall PASS requires every receipt/blob-ref seal and manifest seal at or before
that deadline; a later failure seal requires `authorization_window=false`. A date-evidence attempt has exactly one
`date_evidence` artifact-file entry; frame attempts have none.
`generator_files` is sorted by normalized path, contains every local executable module that can affect generation or
verification, and has no duplicate; a missing, extra, imported-but-unbound, or changed generator fails.

For frame attempts, `exclusion_counts` has exactly `total`, `by_source`, and `by_reason`. `by_source` has exactly
`membership`, `stock_basic`, `stock_st`, and `daily`; `by_reason` has exactly all seven finite ledger reason codes in Section 7.2.
Every value is a non-negative integer, sums agree, and the non-null ledger reference binds even an empty ledger. For
date evidence both exclusion properties are null. `gate_results` always has exactly `classification`, `membership`,
`stock_basic`, `stock_st`, `daily`, `frame`, `sampling_cells`, `calendar`, `date_derivation`,
`authorization_window`, and `overall_pass`. The first nine are boolean when evaluated and null when not evaluated;
`authorization_window` and `overall_pass` are always boolean. Overall PASS is true exactly when `complete=true`, the
window gate is true, and all gates required for that attempt kind are true. Frame attempts require the first seven
and set the calendar pair null; date evidence requires the calendar pair and sets the first seven null.
`authorization_window` is false exactly for not-yet-valid/expired pre-call stops, either window-overrun receipt, any
accepted byte or supporting-artifact fsync after the deadline, or a PASS-manifest publication that misses its hard
watchdog; otherwise it is true even when an unrelated credential/provider/data gate fails within the window.

`disposition` is exactly `ELIGIBLE_FOR_FULL_CAPTURE_AUTHORIZATION` or `NO_QUALIFIED_FRAME_SOURCE` for capability,
`FRAME_CAPTURE_ELIGIBLE` or `CAPTURE_FAILED_CLOSED` for capture, and `DATE_EVIDENCE_VALID` or
`DATE_EVIDENCE_FAILED` for date evidence. Only the first disposition of each pair is allowed when
`overall_pass=true`. `date_selection_only` is true only for date evidence. `capture_input_eligible` is true only for
a verified capture with `FRAME_CAPTURE_ELIGIBLE`; `non_adoptable` is its logical negation. Capability and date
evidence therefore always remain non-adoptable as frame bytes.

The following self-consistent canonical serialization vector represents a capability attempt stopped before ordinal
1 because its credential was unavailable. All-zero protocol identity makes it deliberately non-executable. The
supporting stop-record and empty-ledger bytes (shown without their hashed LF) are:

```json
{"attempt_id":"attempt-example","authorization_id":"auth-example","next_ordinal":1,"schema_version":"m4-attempt-stop-v1","stop_code":"credential_unavailable","stopped_at":"2026-07-17T00:00:00+08:00"}
```

```json
{"attempt_id":"attempt-example","authorization_id":"auth-example","entries":[],"mode":"capability","schema_version":"m4-exclusion-ledger-v1"}
```

Their byte counts/hashes are respectively
`200/f05bd9ef89cd49a6dbe8bc25a575fb04f4fe9eceea026a1072f36afd33291728` and
`142/b5ec13752d26a337b96539615d41685b00267ed8663e1a6820e914198fb96d70`. The resulting canonical manifest bytes
without final LF are:

```json
{"artifact_files":[{"byte_count":142,"kind":"exclusion_ledger","relative_path":"exclusion-ledger.json","sha256":"b5ec13752d26a337b96539615d41685b00267ed8663e1a6820e914198fb96d70"},{"byte_count":200,"kind":"stop_record","relative_path":"stop-record.json","sha256":"f05bd9ef89cd49a6dbe8bc25a575fb04f4fe9eceea026a1072f36afd33291728"}],"attempt_id":"attempt-example","attempt_kind":"capability","authorization_id":"auth-example","authorization_relative_path":"reviews/example/auth.json","authorization_sha256":"1111111111111111111111111111111111111111111111111111111111111111","blob_refs":[],"capture_input_eligible":false,"complete":false,"date_selection_only":false,"disposition":"NO_QUALIFIED_FRAME_SOURCE","exclusion_counts":{"by_reason":{"delisting_consolidation":0,"exchange_out_of_scope_bse":0,"listing_age_below_36_months":0,"not_in_frame_eligible_membership":0,"not_in_frozen_sw2021_membership":0,"security_type_out_of_scope":0,"st_risk_warning":0},"by_source":{"daily":0,"membership":0,"stock_basic":0,"stock_st":0},"total":0},"exclusion_ledger_ref":{"byte_count":142,"kind":"exclusion_ledger","relative_path":"exclusion-ledger.json","sha256":"b5ec13752d26a337b96539615d41685b00267ed8663e1a6820e914198fb96d70"},"execution_deadline":"2026-07-17T00:10:00+08:00","expected_ordinals":[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36],"failed_ordinal":null,"gate_results":{"authorization_window":true,"calendar":null,"classification":null,"daily":null,"date_derivation":null,"frame":null,"membership":null,"overall_pass":false,"sampling_cells":null,"stock_basic":null,"stock_st":null},"generator_files":[{"relative_path":"scripts/example.py","sha256":"2222222222222222222222222222222222222222222222222222222222222222"}],"non_adoptable":true,"protocol_path":"docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md","protocol_sha256":"0000000000000000000000000000000000000000000000000000000000000000","receipt_refs":[],"response_byte_total":0,"schema_version":"m4-segmented-rest-attempt-manifest-v1","sealed_at":"2026-07-17T00:00:01+08:00","stop_record_ref":{"byte_count":200,"kind":"stop_record","relative_path":"stop-record.json","sha256":"f05bd9ef89cd49a6dbe8bc25a575fb04f4fe9eceea026a1072f36afd33291728"},"successful_ordinals":[],"supersedes_sha256":"b711b9b81d8732b48f49c25b419dd9cf51eeae8aa4d2f7bbcd0a662a16782c23","terminal_failed_ordinals":[],"unattempted_ordinals":[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]}
```

Its SHA-256 including final LF is `f8e6d54877750049f767abd7a107e0a877f9fda527d0bcc2ab1b2061eaea8f8d`.

Token-free offline verification reconstructs the exact 36-call matrix and all gates directly from raw blobs. It
rejects unknown files, non-canonical JSON, hash/size drift, receipt or exclusion-ledger disagreement, missing or extra
calls, symlinks, path traversal, mutable permissions, authorization drift, protocol drift, generator drift, and
mode/adoptability drift. It re-derives every eligible row and exclusion entry from raw blobs and rejects an omitted,
extra, reordered, reclassified, or changed exclusion. Verification performs no DNS lookup, socket creation, provider
import with network side effects, token read, database access, or model execution.

Date-evidence verification applies the same closed-file-set and receipt/state checks to its exact two-call matrix,
then semantically reconstructs every calendar row and every derivation and capture-window inequality in Section 3.1.
Neither a date-evidence document alone nor matching stored hashes are sufficient.

Capability manifests always record `non_adoptable=true`. Capture manifests begin fail-closed and may record
`capture_input_eligible=true` only when `complete=true`, every gate passes, and offline verification reproduces the
same manifest identity. Only then may a separate offline assemble command publish a create-only frame/sample package
whose provenance links the capture manifest and unchanged v1.1 preregistration hash.

Assemble and resume entry points must reject capability artifacts, failed or incomplete capture artifacts, manifest
or code drift, mixed attempts, and any probe/capture path overlap.

## 9. Terminal decisions

Capability mode has exactly two verified dispositions:

- `ELIGIBLE_FOR_FULL_CAPTURE_AUTHORIZATION`: all 36 calls complete and every gate passes. This authorizes only a
  proposal; it does not create or imply capture authority.
- `NO_QUALIFIED_FRAME_SOURCE`: any call or gate fails. No v1.3 retry, fallback provider, alternate parameter, MCP,
  SWS, old response, static package, or production database is permitted.

Capture mode has exactly two verified dispositions:

- `FRAME_CAPTURE_ELIGIBLE`: all calls and gates pass; only offline assembly under the frozen protocol may continue.
- `CAPTURE_FAILED_CLOSED`: any call or gate fails; no frame/sample is published and no automatic retry is permitted.

Only successful offline assembly may advance the project to `FRAME_AND_SAMPLE_FROZEN`. Reviewer execution and later
M4 stages remain separately authorized and outside this protocol.

## 10. Required synthetic tests and quality gates

Tests use injected transports and a non-loopback socket guard. They must cover at least:

- absent, malformed, non-canonical, expired, not-yet-valid, or changed authorization; mismatched mode, attempt,
  protocol hash, supersedes hash, output root, date, date evidence, capability manifest, matrix, ordinal, or fields;
  every missing/unknown property and both exact authorization schemas and authorization golden hashes;
- date evidence with a missing/duplicate/out-of-range calendar date, wrong exchange, invalid `is_open`, malformed
  `pretrade_date`, no next common open, wrong proposed/derived date, early `sealed_at`, stale capture window, altered
  calendar blob, stored-date-only verification, or wrong Gregorian 62-day endpoints; exact next-common-day evidence
  boundaries; capture acceptance only from sampling-date 18:00:00 through 23:59:59, next-day capture rejection even
  while evidence remains valid, and one valid cross-holiday case;
- illegal origin, scheme, port, path, query, method, redirect, proxy, cookie, extra parameter, extra call, reordering,
  parallel call, pagination, retry, fallback, and response-size overflow;
- hard-deadline overrun during DNS/connect/TLS/request/body read, token scan, blob fsync, parsing, receipt fsync, gate
  evaluation, and PASS-manifest fsync; start just before expiry, exact-deadline completion, one-second overrun,
  post-window failure sealing, receipt/blob reference times, and non-adoptability on every crossing;
- raw-first ordering, token echo before write, token scanning of stdout/stderr/artifacts, sanitized exceptions,
  create-only publication, concurrent locks, missing/unreadable/empty/malformed credential stop codes and no-receipt
  behavior, process-control propagation, and incomplete sealing;
- every ordinal transition among `success_blob`, `terminal_failure_blob`, `terminal_failure_no_blob`, and
  `unattempted`; exact receipt nullability for transport/token/size/process failures; pre-transport stop records;
  process control before transport, before blob fsync, after blob fsync, before/after success-receipt fsync, between
  calls, and after the final receipt; atomic publication with no partial closed-set file;
  disjoint ordered manifest partitions, at most one terminal failure, no receipt for unattempted calls, and a
  complete API matrix whose later data gate fails while `complete=true`;
- missing, duplicate, unknown, aliased, and crossed classification mappings;
- every per-industry zero-row, 2,000-row, wrong-code, wrong-name, inactive, non-empty `out_date`, empty name,
  duplicate-row, cross-industry duplicate, count mismatch, `has_more`, cursor, and pagination case; valid `.BJ`
  exclusion; duplicate `.BJ`; malformed and unknown suffix rejection;
- exchange-membership union below 4,000, `.BJ` not counting toward 4,000 or industry representation, missing eligible
  industry, deterministic exclusion ordering/hash, one-based row/source ordinals, raw name whitespace, JSON-null
  daily L1/name fields, typed numeric lexemes, all normative ledger golden hashes, and a valid exhaustive 31-partition
  union;
- each stock partition's zero row, exactly 6,000 rows, wrong exchange/suffix/status, malformed date, duplicate,
  count/pagination failure, missing membership join, and extra nonmember; exact CNY/board allowlist, B share/CDR/
  preferred/unknown and crossed exchange/board/code-family exclusion, explicit `689` rejection, NFKC/casefold/
  outer-whitespace ST and delisting-name boundaries, leap/month-end 36-month anniversaries;
  valid empty stock-ST list, exactly 1,000 rows, wrong date, malformed/duplicate code, empty type/name, count/pagination
  failure, `.BJ`, nonmember, and joined-member handling; stock-name versus date-bound-ST union and disagreement;
  membership addition/removal, list-status change, and official-name ST/delisting transition at the sampling-date/
  midnight boundary, proving that no next-date current snapshot can satisfy capture;
  deterministic first-reason priority, paired membership/stock exclusion entries, and stock ledger rederivation;
- daily eligible rows below 4,000, exactly 6,000 total raw items including `.BJ`, wrong date, duplicate or invalid
  code, valid `.BJ` exclusion, extra `.SH`/`.SZ` non-member exclusion, missing eligible membership code, zero/negative/
  NaN/infinite market value, binary-float coercion, count mismatch, and pagination residue;
- every undersized sampling cell and one valid 12-cell matrix;
- `complete` versus gate PASS separation; capability non-adoptability; capture eligibility only after verification;
- manifest, receipt, blob, exclusion-ledger, authorization, protocol, generator, and closed-file-set tampering;
  every manifest missing/unknown/type/cross-field error, manifest/supporting-file golden hashes, symlinks, hard links,
  permissions, path traversal, mixed attempts, capability/capture overlap, and deterministic offline re-verification.

Acceptance requires M4-directed pytest and full pytest, Ruff lint and format check, explicit F401 enforcement, mypy,
all retained v1/v1.1/v1.2 hashes, the new frozen v1.3 hash after review, retained static checksums, and
`git diff --check`. Tests and validation must not read `tracker.db`, run pipeline or cron, send Telegram messages,
invoke Gemini/AGY/Codex Reviewer, or make an unmocked network request.

## 11. Freeze and execution sequence

This final candidate may proceed only in this order:

1. Obtain an independent final-review `VERDICT: PASS` with no P0–P3 findings against these exact bytes.
2. Without editing this file, freeze the reviewed bytes and publish a SHA-256 approval manifest without changing
   historical files.
3. Implement immutable types, authorization verification, exact REST transport, raw-first capture, offline verifier,
   adoptability guard, and synthetic tests.
4. Run all offline quality gates and independently review the frozen protocol and implementation.
5. Confirm the previously exposed Tushare credential has been revoked; provision a fresh token outside the repository.
6. Obtain a new external capability authorization that binds the frozen v1.3 hash and a fixed execution window.
7. Execute at most one real capability attempt. Do not reuse any v1.2 authorization or attempt ID.
8. On verified capability PASS only, separately authorize and execute one two-call date-evidence attempt.
9. On verified, still-valid date evidence only, propose a full-capture authorization whose entire window satisfies
   Section 3.1; the date-evidence executor cannot create that authorization.
10. On verified capture PASS only, assemble and freeze the formal frame/sample offline.

No step in this protocol itself authorizes the next step.

## 12. Normative provider references

- Tushare Pro HTTP contract: `https://tushare.pro/document/1?doc_id=40`
- Tushare permissions: `https://tushare.pro/document/1?doc_id=108`
- SW2021 classification (`index_classify`): `https://tushare.pro/document/2?doc_id=181`
- Segmented SW membership (`index_member_all`, 2,000 rows per call):
  `https://tushare.pro/document/2?doc_id=335`
- Listed-security attributes (`stock_basic`, 6,000 rows per call): `https://tushare.pro/document/2?doc_id=25`
- Historical daily ST list (`stock_st`, 1,000 rows per call): `https://tushare.pro/document/2?doc_id=397`
- Daily market value (`daily_basic`, 6,000 rows per call): `https://tushare.pro/document/2?doc_id=32`
- SSE/SZSE trading calendars (`trade_cal`): `https://tushare.pro/document/2?doc_id=26`

These limits and schemas are frozen as protocol assumptions when v1.3 is finalized. A provider documentation change,
response-contract change, or observed limit change after freezing fails closed and requires a new linked version; it
does not silently amend v1.3.
