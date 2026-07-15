# AGY note-to-fix mapping

| Note | Resolution | Disposition |
|---|---|---|
| Seed continuity | v1.1 protocol and `SAMPLING_SEED` retain `qualitative-v2-m4-prereg-v1`; hash input remains UTF-8 seed + `0x1f` + exact `ts_code`. | Adopted |
| URL normalization | v1.1 section 5 and `normalize_url()` freeze official domains, HTTPS, path case/segments/encoding, tracking removal, query preservation/sorting, and trailing-slash rules. | Adopted |
| Reviewer contract | v1.1 sections 8–10 and `qualitative_v2_audit_review.py` freeze workspace, command preview/authorization, environment isolation, process group, limits, schema, seal/index, comparison, and adjudication. | Adopted |
| Wilson arithmetic | Kept Python IEEE-754 binary64 as preregistered; Decimal is used only for six-place half-even display. | Not changed |
| Process groups | Uses `start_new_session=True`; no `preexec_fn=os.setpgrp`. | Adopted |

All three P2 notes are closed by v1.1. This closure does not authorize real reviewer execution or audit collection.
