# AGY independent review — MILESTONE-004 v1.3 draft

Review date: `2026-07-17`
Reviewer: `AGY`
Mode: non-interactive, sandboxed local-file review
Prompt: `reviews/milestone-004-preregistration-v1.3/agy-review-prompt.md`
Primary draft: `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md`
Disposition: **FAIL**

The first invocation returned only a proposed read plan and no verdict. The second invocation could not read local
files because headless command permission was denied. The third invocation retained the sandbox and prompt's
read-only/no-network/no-credential restrictions while auto-approving local tool requests, and produced the review
below. This record preserves the successful response verbatim except for removing terminal transport metadata.

## AGY response

VERDICT: FAIL

### P0: NONE

### P1-1

* **File & Line Numbers:**
  [2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md](../../docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md#L201-L202)
* **Failure Mode:** Section 7.2, condition 3 requires that "Every `ts_code` matches exactly
  `^[0-9]{6}\.(SH|SZ)$`" for all rows returned in each of the 31 SW2021 level-1 industry responses. However, SW2021
  level-1 membership queries on Tushare return all classified A-shares, which includes Beijing Stock Exchange (BSE)
  listed stocks ending with `.BJ`. Since these `.BJ` stocks will be present in the raw API response, this strict gate
  will fail closed on every real run, preventing any successful frame acquisition.
* **Protocol-level Correction:** Revise Section 7.2, condition 3, to allow non-SH/SZ stock codes in the raw responses
  but explicitly filter them out from the eligible frame membership set, or restrict the regex matching requirement
  to only the filtered/eligible frame rows rather than the entire raw response. For example: "Every `ts_code` in the
  eligible frame membership set (after filtering out non-SH/SZ codes) matches exactly `^[0-9]{6}\.(SH|SZ)$`; raw `.BJ`
  or other exchange codes are skipped/ignored rather than failing the gate."

### P2-1

* **File & Line Numbers:**
  [2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md](../../docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md#L213-L218)
* **Failure Mode:** Section 7.3 requires the single `daily_basic` response to contain "strictly fewer than 6,000
  unique valid `.SH`/`.SZ` rows." While the current SSE/SZSE active market size is under 6,000, it is highly likely
  that the number of active listed companies will exceed 6,000 in the medium term. When this occurs, a complete
  (non-truncated) response will contain >= 6,000 rows, causing this gate to unconditionally fail closed.
* **Protocol-level Correction:** Modify the truncation detection rule in Section 7.3 to check if the response size is
  exactly equal to the provider's maximum query limit (which is 6,000 for `daily_basic`), rather than enforcing a hard
  cap of strictly fewer than 6,000 rows. For example: "Exactly 6,000 rows (or the documented provider query ceiling)
  is treated as possible truncation and fails..."

### P3: NONE

### Freeze recommendation

The draft **cannot** be frozen unchanged. The protocol-level corrections for the P1-1 validation bug (which would
block all real Tushare frame acquisitions due to Beijing Stock Exchange `.BJ` stocks) and the P2-1 market size boundary
limit must be resolved in a revised draft before freezing.

***

### Summary of Work

1. **Inspected local files**: Used tools to read the primary draft
   [2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md](../../docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.md),
   its normative predecessors
   [2026-07-15-milestone-004-evidence-feasibility-preregistration-v1.1.md](../../docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration-v1.1.md)
   and
   [2026-07-16-milestone-004-frame-source-capability-preregistration-v1.2.md](../../docs/plans/2026-07-16-milestone-004-frame-source-capability-preregistration-v1.2.md),
   the historical capability probe log
   [capability-probe-2026-07-17.md](../milestone-004-audit-v1.2/capability-probe-2026-07-17.md), and frozen
   mappings/rules in [qualitative_v2_audit.py](../../qualitative_v2_audit.py).
2. **Evaluated constraints**: Checked alignment of estimands, industry mappings, seed values, and the correctness of
   the new 33-call segmented REST matrix.
3. **Identified bugs**: Discovered a critical gate failure mode where Beijing Stock Exchange (`.BJ`) stocks returned
   by Tushare will block all runs under the current strict membership suffix regex validation, and highlighted a
   future-proofing limitation with the 6,000-row `daily_basic` gate.
