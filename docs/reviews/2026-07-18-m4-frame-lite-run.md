# M4 lightweight frame runs — 2026-07-18

Common scope: trade date `20260717`, no automatic retry, no production, shadow, Reviewer, or MILESTONE-005 access.

## Run 1 — strict count gate

- Acquisition time: `2026-07-18T13:31:34+08:00`
- Result: **FAIL** at probe call 1
- Requests: 1 attempted; `index_classify=1`, all other APIs `=0`
- API rows: `index_classify=31`; provider `count=0`, `has_more=false`, HTTP 200, provider code 0
- Failure gate: provider `count` did not equal response rows

The follow-up repair accepts nonempty `count=0` only as an unknown-count sentinel when `has_more=false`; positive count
mismatches and zero sentinels without explicit single-page evidence still fail.

## Run 2 — repaired probe and build

- Acquisition time: `2026-07-18T14:59:23+08:00`; build started `2026-07-18T14:59:47+08:00`
- Probe result: **PASS**, 5/5 captured responses
- Probe API rows: `index_classify=31`, first `index_member_all=126`, `stock_basic=5200`, `daily_basic=5522`
- Count semantics: all five nonempty probe responses recorded `unknown_zero_sentinel`
- Build result: **FAIL** at ordinal 6, the first supplemental `index_member_all`, due to transport failure before capture
- Requests: 5 successful responses plus 1 failed transport attempt; no retry and no remaining 29 supplemental calls
- Exclusions: not computed
- Frame/sample: not produced
- Derived SHA-256: `frame.csv=N/A`, `sample.csv=N/A`, `excluded.csv=N/A`
- Safety check: no token in raw/summary; no `derived/` or `derived.tmp/`

## Run 3 — member code drift

- Acquisition time: `2026-07-18T15:05:49+08:00`
- Probe result: **PASS**, 5/5 captured responses with the same aggregate row counts as run 2
- Build result: **FAIL** at ordinal 16, `index_member_all(l1_code=801170.SI)`
- Captures: 16 responses total; 15 fully validated, ordinal 16 retained as the failed captured response
- Failed response: provider code 0, 144 rows, `count=0`, `has_more=false`; one row used non-six-digit code `T00018.SH`
  for `上港集箱(退市)`
- Failure gate: member codes are limited to six digits plus `.SH`, `.SZ`, or `.BJ`; structural drift cannot become a
  single-stock exclusion
- Requests: no retry and no remaining 19 calls
- Exclusions/frame/sample: not produced
- Derived SHA-256: `frame.csv=N/A`, `sample.csv=N/A`, `excluded.csv=N/A`
- Safety check: no token in raw/summary; no `derived/` or `derived.tmp/`

All three failed ignored runs remain local diagnostic records. The repository records only these aggregate facts.

## Run 4 — successful frame build

- Acquisition time: `2026-07-18T15:13:49+08:00`; completed `2026-07-18T15:16:02+08:00`
- Result: **PASS**, 35/35 captured and validated responses; no retry
- API rows: `index_classify=31`, `index_member_all=5864`, `stock_basic=5200`, `daily_basic=5522`
- Count semantics: all 35 nonempty responses recorded `unknown_zero_sentinel`
- Exclusions: 1,170 total — `missing_stock=340`, `bj_exchange=325`, `listed_less_than_3_years=291`,
  `excluded_name=208`, `missing_daily=3`, `unsupported_board=2`, `invalid_member_code=1`
- Frame/sample: 4,694 frame rows; 36 unique sampled stocks; all 12 cells contain exactly 3 stocks
- `invalid_member_code`: `T00018.SH` appears only in `excluded.csv`
- Derived SHA-256:
  - `frame.csv`: `2480b8db24b098723b4252839bb4095bfdad2220e6dd21f52a1420886168b062`
  - `sample.csv`: `b278a7b00b71fd54e34519dead098602a8748635f1350414e1b78538a3d7635d`
  - `excluded.csv`: `3612b5bbd803c173f4acaa73eff343a61388afc7a4953820f51afe85df6695ab`
- Safety check: no token in raw, summary, or derived files; run/raw/derived directories are mode `0700`

This success produces a local research frame/sample only. It does not authorize MILESTONE-005, Reviewer, production DB,
pipeline, cron, Telegram, Gemini, or production adoption.
