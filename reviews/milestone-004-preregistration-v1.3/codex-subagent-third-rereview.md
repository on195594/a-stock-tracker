# Third independent Codex final rereview — MILESTONE-004 v1.3

Review date: `2026-07-17` (`Asia/Shanghai`)
Reviewer: Codex subagent `Nietzsche`
Agent ID: `019f6e24-6755-7ca1-878f-0513abfc2c28`
Prompt: `reviews/milestone-004-preregistration-v1.3/codex-subagent-third-rereview-prompt.md`
Candidate SHA-256: `5bde2a84c2642f6c659aa5d52b8efa04b9e0569097d50284b82c40d6fb675961`
Disposition: **FAIL — CHANGES REQUIRED — NOT APPROVED**

## Verbatim result

VERDICT: FAIL

P0: NONE

P1: P1-1 — `docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration.md:71-78` requires
eligibility at `sampling_date`, but
`docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md:98-109,471-473,573-612` substitutes
undated current `index_member_all(is_new=Y)` membership and `stock_basic(list_status=L)` status/name; a membership,
listing-status, or name change between the sampling date and capture can silently omit a date-eligible security or
apply post-date ST/delisting status, changing the frame and sample. Correction: bind complete date-effective
membership, listing-status, and official-name inputs, rederive all predicates as of the authorized date, and add
synthetic membership/status/name transition tests spanning the date-to-capture interval.

P2: NONE

P3: P3-1 — `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md:124-128` says date evidence
makes none of “33 frame calls,” contradicting the normative 36-call matrix at lines 55-101; this stale count can
misdirect implementation and tests. Correction: replace `33` with `36`.

FREEZE: CHANGES_REQUIRED

## Gate decision

The strict approval condition was not met. No v1.3 approval SHA-256 or approval record was published.
