# P1 repair record — MILESTONE-004 v1.3 final candidate

Repair date: `2026-07-17`
Source review: `reviews/milestone-004-preregistration-v1.3/agy-review.md`, finding `P1-1`
Assessment: `reviews/milestone-004-preregistration-v1.3/agy-review-assessment.md`
Repaired protocol: `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md`
Disposition: **P1-1 addressed; pending independent Claude final review**

## Resolution

The target population remains exactly SSE/SZSE as frozen in v1.1. The repair does not add BSE to the frame and does
not silently discard provider rows.

- Membership and daily responses recognize only `.SH`, `.SZ`, and `.BJ`; malformed or unknown suffixes fail.
- Exact `.SH`/`.SZ` rows alone form the eligible membership and daily sets.
- Structurally valid `.BJ` rows remain in raw blobs and enter a canonical exclusion ledger with
  `exchange_out_of_scope_bse`.
- Extra `.SH`/`.SZ` daily rows without a frozen SW2021 membership remain in raw and enter the ledger with
  `not_in_frozen_sw2021_membership`.
- Ledger entries bind source and provider ordinals, target fields, finite reason codes, and a canonical typed-cell row
  hash; ordering, counts, and ledger hash are deterministic and manifest-bound.
- Offline verification re-derives the eligible sets and complete exclusion ledger from raw blobs. Missing, extra,
  reordered, reclassified, or changed exclusions fail.
- The 4,000-row, 31-industry, daily-coverage, frame, and 12-cell gates apply only to the unchanged eligible `.SH`/`.SZ`
  target set. `.BJ` rows cannot satisfy a gate or create a daily-coverage obligation.
- The 6,000 ceiling applies to total raw daily items across all recognized suffixes, so `.BJ` rows cannot mask
  truncation.

The protocol is now version `1.3.0` with conditional `FINAL CANDIDATE` status. Final-review approval may freeze only
the exact reviewed bytes by publishing their SHA-256; it does not authorize implementation or network execution.
