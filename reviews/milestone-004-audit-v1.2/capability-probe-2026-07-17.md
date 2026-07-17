# MILESTONE-004 v1.2 capability-probe final record

Date: 2026-07-17 (`Asia/Shanghai`)
Authorization ID: `qualitative-v2-m4-v1.2-capability-20260716-01`
Attempt ID: `m4-v1.2-capability-20260716-01`
Protocol SHA-256: `b711b9b81d8732b48f49c25b419dd9cf51eeae8aa4d2f7bbcd0a662a16782c23`
Authorization SHA-256: `6db2039f6f948d364cd1929918927e01581c6dfa3c1cae48156bb380a243693b`
Manifest SHA-256: `70f6d73a4b62f9725b3fe983cbdcf212ded5d84cbdedbe777088793b0c33957a`
Status: **COMPLETE — CAPABILITY FAIL**
Terminal state: **`NO_QUALIFIED_FRAME_SOURCE`**

## Execution result

The one authorized real probe ran inside the fixed window, from `2026-07-17T09:29:32.039063+08:00` through
`2026-07-17T09:29:34.831589+08:00`. It made exactly the three preregistered calls in order, once each, with no retry,
pagination, supplemental request, or fallback.

| Ordinal | Call | Sanitized outcome | Raw blob SHA-256 | Receipt SHA-256 |
|---:|---|---|---|---|
| 1 | `index_classify(level=L1, src=SW2021)` | Tushare `40203`: account lacks API access | `8e806dd0f99d491638540b6d4edbf656fc3b1a21e2f39482e1b1b60660924be1` | `00a4975424a7780742d6b9414c4aad6eec2225b8b19588f5bf388f73d6ecff79` |
| 2 | `index_member_all(is_new=Y)` | Tushare `40203`: account lacks API access | `af885233d5b680f899ed63856bcaaed57c10224ecf64c46adb0db3fc4ff63820` | `d3eb965599f2b6dc2bfd3893b646c2e8759b1aee9bbeb7a6049bfb24e460c140` |
| 3 | `daily_basic(trade_date=20260715)` | Tushare `40203`: `5 requests/day` frequency limit | `2d98778b47273278931513419fdf51436467bcac98ab64dded93fad4564f5a7c` | `70c5e59c824fe777abd1605135835cf1680676583dad7b6795fd03f6fb396af3` |

All three responses were raw-first published as content-addressed blobs before parsing receipts. The manifest records
no missing call and no stop code. Ordinary provider errors are terminal receipts but do not interrupt the diagnostic
matrix, so probe completion and source qualification correctly remain separate:

- `complete=true`
- `capability_pass=false`
- classification: `classification_unavailable`
- members: `members_unavailable`
- daily: `daily_unavailable`
- intersection: `intersection_inputs_unqualified`

## Offline verification

The token-free `verify` command reproduced the same manifest SHA-256 and terminal state with exit code 1:

```text
manifest_sha256=70f6d73a4b62f9725b3fe983cbdcf212ded5d84cbdedbe777088793b0c33957a
complete=true
capability_pass=false
terminal_state=NO_QUALIFIED_FRAME_SOURCE
```

The sealed local package is
`artifacts/milestone-004/v1.2/capability-probes/probe-m4-v1.2-capability-20260716-01`.
Every byte remains `non_adoptable`; it must not enter assemble, resume, a formal frame, or a later capture.

## Decision

The fixed authorization and attempt are consumed. No retry is permitted. Tushare is closed as the v1.2 candidate,
and the project must not fall back to SWS, old responses, a production database, or another unregistered provider.
M4 remains research-only at `NO_QUALIFIED_FRAME_SOURCE`. Any continuation requires a separately preregistered v1.3
candidate-source protocol; this result does not authorize one.
