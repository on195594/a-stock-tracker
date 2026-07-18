# M4 lightweight frame run — 2026-07-18

- Trade date: `20260717`
- Acquisition time: `2026-07-18T13:31:34+08:00`
- Result: **FAIL** at probe call 1; no retry and no build
- Requests: 1 attempted of the 35-call maximum; `index_classify=1`, all other APIs `=0`
- API rows: `index_classify=31`; provider `count=0`, `has_more=false`, HTTP 200, provider code 0
- Failure gate: provider `count` did not equal response rows
- Exclusions: not computed
- Frame/sample: not produced
- Derived SHA-256: `frame.csv=N/A`, `sample.csv=N/A`, `excluded.csv=N/A`
- Safety check: no token in raw/summary; no `derived/` or `derived.tmp/`; no production, shadow, Reviewer, or MILESTONE-005 access

The failed ignored run remains the local diagnostic record. The repository records only these aggregate facts.
