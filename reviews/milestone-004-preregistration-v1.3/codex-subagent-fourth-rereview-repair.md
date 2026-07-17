# Fourth Codex rereview repair record — MILESTONE-004 v1.3

Repair date: `2026-07-17` (`Asia/Shanghai`)
Source review: `reviews/milestone-004-preregistration-v1.3/codex-subagent-third-rereview.md`
Repaired candidate SHA-256: `0ad8c6b3d5175af96409e570bdc17839ac7cf266227410f2e9e9ea756a7a12df`
Disposition: **P1-1 and P3-1 addressed; pending a fourth fresh independent Codex final review**

- Replaced the stale “33 frame calls” reference with the normative 36-call count.
- Preserved date-evidence semantic validity through the next common day, but added a stricter formal-capture rule for
  undated current endpoints: capture authorization, all calls, blobs, receipts, gates, and PASS manifest must occur
  on `sampling_date` from 18:00:00 through 23:59:59 Asia/Shanghai.
- Added this same-date snapshot deadline to frame-authorization cross-fields and `execution_deadline` derivation.
- Explicitly treats same-date `index_member_all(is_new=Y)` and `stock_basic(list_status=L)` responses as current
  authoritative snapshots only for that civil date; next-day reuse or historical reconstruction is forbidden.
- Added transition tests for membership addition/removal, listing-status changes, and ST/delisting name changes at
  midnight, including rejection while calendar evidence alone remains semantically valid.

No approval or execution authority is granted by this repair record.
