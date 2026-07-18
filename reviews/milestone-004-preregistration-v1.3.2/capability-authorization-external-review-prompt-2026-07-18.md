# Fresh Claude capability external approval prompt — MILESTONE-004 v1.3.2

Act as a new, independent, read-only capability external approver for MILESTONE-004 v1.3.2. This role is separate
from the author/fixer, both protocol reviewers, and the prior Claude Opus implementation reviewer. Do not claim or
reuse any of those identities.

Review these exact local inputs:

- `reviews/milestone-004-preregistration-v1.3.2/capability-approval-packet-2026-07-18-01.sha256`
- `reviews/milestone-004-preregistration-v1.3.2/capability-authorization-approval-request.md`
- `reviews/milestone-004-preregistration-v1.3.2/capability-authorization-input-2026-07-18-01.md`
- `reviews/milestone-004-preregistration-v1.3.2/capability-authorization-decision-template.md`
- `reviews/milestone-004-preregistration-v1.3.2/capability-authorization-user-input-2026-07-18-02.md`
- `reviews/milestone-004-preregistration-v1.3.2/freeze-attestation.json`
- `reviews/milestone-004-preregistration-v1.3.2/claude-independent-implementation-review.md`

Independently verify packet hashes, freeze status, role separation, the proposed exact values, and whether every
mandatory evidence row permits APPROVE. The user has explicitly stated that no current account-side entitlement or
remaining-quota evidence exists for the four API families and selected continuation as DEFER after being told that
this blocks approval. Do not invent or infer account state from points or public documentation. Do not use a live
preflight to fill the gap.

This is an offline, read-only review. Do not edit or create files. Do not read `.env`, process-environment secrets,
token/key values, `tracker.db`, production logs, or credential stores. Do not use web/browser/network/provider,
pipeline/cron, Telegram, Gemini, Reviewer, or model-service tools other than the Claude session carrying out this
review. Do not create authorization JSON/checksum, attempt, lock, journal, or publication artifacts.

Return one self-contained Markdown external decision record suitable for exact archival. It must include:

- `APPROVER_IDENTITY:` beginning with `Claude` and naming the model if known;
- `APPROVER_ROLE: fresh independent capability external approver`;
- packet checksum file SHA-256 and verification result;
- exact proposed authorization/attempt IDs, probe date, window, and paths;
- a mandatory-evidence table completed from the available record without inventing evidence;
- exactly one selected outcome among APPROVE, REJECT, and DEFER;
- explicit blocking evidence/reason;
- decision timestamp in `Asia/Shanghai`;
- `DECISION: APPROVE|REJECT|DEFER` and `EXECUTION_AUTHORITY: GRANTED|NOT_GRANTED`;
- an attestation that no file was modified, no credential/token was read, no provider/network call was made, and no
  authorization or attempt artifact was created.

APPROVE is invalid unless all mandatory evidence is present. If evidence is missing, select DEFER or REJECT based on
whether the gap is curable. This decision cannot alter protocol freeze or implementation acceptance.
