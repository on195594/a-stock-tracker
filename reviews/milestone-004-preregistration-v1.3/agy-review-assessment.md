# Assessment of AGY review — MILESTONE-004 v1.3 draft

Assessment date: `2026-07-17`
AGY review: `reviews/milestone-004-preregistration-v1.3/agy-review.md`
Draft assessed: `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md`
Assessment disposition: **CHANGES REQUIRED BEFORE FREEZE**

## P1-1 — accepted as a blocking scope-handling ambiguity

The v1.1 target frame is explicitly limited to `.SH` and `.SZ`, while Tushare's code system also defines `.BJ` for
Beijing Stock Exchange securities. The public `index_member_all` documentation does not state that an L1 query
excludes `.BJ` rows and exposes no exchange filter. AGY's stronger factual claim that every real response will contain
`.BJ` rows was not established by the cited local files or the public interface documentation, but the protocol must
still define deterministic handling if an otherwise valid out-of-scope suffix is returned.

The current v1.3 draft makes every non-`.SH`/`.SZ` raw row fatal. That can turn an out-of-scope provider row into a
source-wide failure even though excluding BSE preserves, rather than changes, the frozen SSE/SZSE target population.
Before freeze, the draft should distinguish:

- raw membership rows, which are always retained and validated for structural integrity;
- recognized but out-of-scope `.BJ` rows, which are excluded before the eligible union and recorded in a deterministic
  exclusion ledger with counts and row hashes; and
- unknown/malformed suffixes, which remain fatal.

The eligible union minimum, uniqueness, daily-subset requirement, frame, and 12-cell gates must continue to apply only
to the `.SH`/`.SZ` target set. No raw row may be silently discarded.

Relevant provider references:

- Tushare code convention, including BSE `.BJ`: `https://tushare.pro/document/1?doc_id=14`
- `index_member_all` parameters and 2,000-row limit: `https://tushare.pro/document/2?doc_id=335`

## P2-1 — not accepted

The draft already implements AGY's proposed correction: it requires fewer than 6,000 rows and explicitly treats
exactly 6,000, the documented service ceiling, as possible truncation. A response above 6,000 cannot be obtained from
the frozen single-call provider contract. If the eligible market eventually reaches that ceiling, failing closed is
the intended outcome; a new protocol would need a documented partition strategy rather than accepting an
unprovably truncated response.

No v1.3 text change is required for this finding. The existing test requirement for exactly 6,000 rows remains
correct.

Relevant provider reference:

- `daily_basic` 6,000-row maximum: `https://tushare.pro/document/2?doc_id=32`

## Result

AGY's overall `VERDICT: FAIL` remains operationally controlling because P1-1 identifies a real unresolved protocol
case, even though its assertion about actual `.BJ` presence was stronger than the available evidence and P2-1 repeats
the existing rule. The draft must not be frozen unchanged. This assessment does not authorize editing, freezing,
implementation, or execution.
