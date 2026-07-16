# MILESTONE-004 frame-source capability preregistration v1.2

Protocol ID: `qualitative-v2-m4-prereg-v1.2`
Protocol version: `1.2.0`
Preregistration date: `2026-07-16`
Status: **locally frozen; one externally authorized capability probe only**
Normative timezone: `Asia/Shanghai`
Supersedes SHA-256: `6990c12859da94ec5648fc26c3c3969282226a4c703d2cb4c189990ba1f2529f`

## 1. Scope

This protocol changes only the sampling-frame source gate. The v1.1 estimand, SW2021 code/name dictionary, six
super-strata, low/high market-cap split, three companies per cell, 36-company total, sampling seed, evidence sources,
queries, candidate cap, technical-attempt ledger, independent Reviewer rules, adjudication, coverage gates, and
Wilson calculation remain frozen without modification.

The v1.1 acquisition route is technically closed. v1.2 permits one bounded Tushare capability probe. Probe bytes test
access, schema, bulk capacity, and the ability to populate the frozen 12 sampling cells. They are always
`non-adoptable`: they cannot be used to assemble, resume, repair, or freeze a formal frame.

## 2. Exact capability matrix

The probe trade date is supplied by and bound into the external authorization. It is not selected by the code and
does not assert freshness. The only authorized matrix, in order, is:

| Ordinal | API | Parameters | Fields |
|---:|---|---|---|
| 1 | `index_classify` | `level=L1`, `src=SW2021` | `index_code,industry_name,level,src` |
| 2 | `index_member_all` | `is_new=Y`; no `l1_code` | `l1_code,l1_name,ts_code,name,is_new` |
| 3 | `daily_basic` | `trade_date=<authorization probe_trade_date as YYYYMMDD>` | `ts_code,trade_date,total_mv` |

Each matrix item may be called at most once. There is no automatic retry, pagination, supplemental request, date
fallback, or alternate parameter. Transport is one body-only `POST` to `https://api.tushare.pro`, with TLS
verification and redirects disabled.

## 3. Authorization and secret boundary

Execution requires a pre-existing canonical JSON authorization plus a matching SHA-256 manifest. The authorization
must bind its ID, attempt ID, this protocol SHA-256, exact output root, exact matrix, probe trade date, and inclusive
`not_before`/`not_after` window. The execution tool may only read and validate it; the tool has no operation that
creates, edits, renews, or extends an authorization.

Canonical authorization, SHA-256, a timezone-aware real clock, protocol hash, attempt, output root, date, and full
matrix are revalidated before every call. A missing, not-yet-valid, expired, malformed, changed, or mismatched
authorization prevents the next network call. Authorization, security, and process-control failures stop the matrix
immediately. `KeyboardInterrupt`, `SystemExit`, and `GeneratorExit` are propagated unchanged after any in-progress
attempt is sealed incomplete.

The Tushare token may be read only after initial authorization validation. It exists only in memory and the HTTPS
request body. Token bytes, request bodies, request headers, cookies, and raw exception objects are never persisted.
Before any provider response is written, its bytes are scanned for the exact token; an echo is blocked and the
attempt is sealed incomplete without those bytes.

## 4. Raw-first and diagnostic completion

Every received response is published raw-first as a content-addressed, create-only blob, followed by a sanitized
receipt. Receipts contain only the call ID, fixed credential-free request description, response status/category,
capture time, content type, byte count, blob reference, and finite parse/error codes. HTTP and transport failures map
to a finite sanitized error vocabulary. Stdout, stderr, receipts, manifests, and blobs must not contain token bytes.

Ordinary transport, HTTP, provider, JSON, schema, or data-quality errors are recorded and the next unused matrix item
is still attempted while authorization remains valid. `complete=true` means all three diagnostic ordinals produced a
terminal receipt. It does not mean the source is qualified. An immediate authorization, security, or process-control
stop produces `complete=false` and records the unattempted calls.

## 5. Capability gates

`capability_pass=true` requires every condition below:

1. `index_classify` returns exactly the 31 frozen SW2021 level-1 code/name pairs, with no duplicate, missing, unknown,
   or crossed mapping.
2. The single bulk `index_member_all(is_new=Y)` response covers all 31 frozen industries and contains at least 4,000
   unique valid `.SH`/`.SZ` stock codes. Every accepted code has exactly one row, one non-empty name, and one valid
   frozen L1 membership.
3. The bulk response has no `has_more`, count mismatch, pagination, or truncation indication. A response whose row
   count reaches the known 5,000-row service limit fails without independent completeness proof; this probe provides
   no such proof.
4. `daily_basic` contains at least 4,000 unique valid stock rows, all bound to the authorized date, with finite,
   strictly positive `total_mv`, no duplicate code, and no pagination residue. A response at the known 6,000-row
   service limit fails without independent completeness proof.
5. The unique valid membership/daily intersection can populate three companies in every one of the six
   super-strata × low/high market-cap cells under the unchanged v1.1 decimal median split and sampling rules.

Any failed condition yields `complete=true, capability_pass=false` when the diagnostic matrix completed normally.
The terminal project state is then `NO_QUALIFIED_FRAME_SOURCE`; there is no fallback to SWS, an old response, a
production database, or another provider.

## 6. Offline verification and forward decision

The sealed manifest binds the authorization hash, protocol hash, exact receipt/blob hashes, code hash, expected and
completed calls, `complete`, `capability_pass`, gate results, and `non_adoptable=true`. Offline verification rejects
unknown files, path traversal, symlinks, non-canonical JSON, hash/size drift, receipt/manifest disagreement, and
adoptability drift. Publication and attempt locking are create-only and deterministic.

If and only if the probe passes, the next permitted action is to propose a new authorization for a complete formal
capture with a new attempt ID. Probe bytes remain forbidden as capture inputs. If it fails, M4 remains research-only
at `NO_QUALIFIED_FRAME_SOURCE`; continuing requires a separately preregistered v1.3 candidate-source protocol.
