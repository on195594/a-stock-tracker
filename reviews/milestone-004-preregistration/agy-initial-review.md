# MILESTONE-004 preregistration — AGY initial review record

Record date: `2026-07-15`
Record type: reconstructed review summary
Disposition: findings addressed before freeze

## Provenance

This repository did not contain the raw AGY prompt, stdout, stderr, timestamps, or invocation metadata for the preregistration review when implementation began in the fresh context. This record therefore preserves only the findings explicitly supplied in the user-approved implementation plan. It is not presented as a verbatim AGY transcript and must not be used to infer wording, counts, or findings absent from that plan.

## Material findings preserved from the approved plan

The initial review identified P1/P2 gaps in these areas:

1. **Industry mapping:** the six super-strata needed an exhaustive, unique mapping of all 31 SW2021 level-1 industries, with unknown/duplicate categories failing closed.
2. **Sampling determinism:** market-cap boundary assignment, unrounded `total_mv`, `ts_code` tie-breaking, exact UTF-8 hash input, exchange suffix retention, fixed seed, cell size, and the 36-company invariant needed to be mechanical.
3. **Sample replacement:** the protocol needed to distinguish documented pre-freeze ineligibility pass-overs from the absolute post-freeze ban on replacement.
4. **Retrieval completeness:** official public query pages, source/query order, date constraints, first-20 result capture, deterministic round-robin, deduplication, corpus cap, and no early stopping needed to be frozen.
5. **Technical failures:** download, read, extraction, and hash failures could not be collapsed into `insufficient`; retry timing and report-wide blocking needed to be explicit.
6. **Offline independent labeling:** Reviewer A and Reviewer B needed separate processes, workdirs, sessions, and writable cache namespaces, sealed outputs before comparison, and user adjudication of every disagreement.
7. **Evidence directness and entity scope:** acceptance needed to require an effective, company-specific restriction mechanism and an as-of annual-report basis for consolidated-subsidiary patent ownership.
8. **Statistical reproducibility:** fixed integer gates, the exact Wilson formula/z value, descriptive-only intervals, and reproducible reference vectors needed to be preregistered.
9. **Artifact immutability:** stage ordering, parent hashes, version-on-change, local-only large blobs, and fail-closed blob verification needed to be specified.

## Resolution target

The formal protocol was required to resolve all items above without authorizing real sampling, evidence retrieval, Gemini/reviewer execution, production code changes, or database writes.
