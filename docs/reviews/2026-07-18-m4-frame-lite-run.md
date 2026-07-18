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

Both failed ignored runs remain local diagnostic records. The repository records only these aggregate facts.
