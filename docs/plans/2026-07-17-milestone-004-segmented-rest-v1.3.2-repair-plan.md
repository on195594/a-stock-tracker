# MILESTONE-004 segmented REST v1.3.2 no-stock-ST repair plan

Plan date: `2026-07-17` (`Asia/Shanghai`)

Status: **DRAFT REPAIR PLAN — NOT FROZEN — NOT AN AUTHORIZATION**

Planned protocol ID/version: `qualitative-v2-m4-prereg-v1.3.2` / `1.3.2`

Direct predecessor: frozen v1.3.1 SHA-256
`f494c1973f002536874782af41221d37a2ca618c329fa48a2ae650b544c88042`

## 1. User decision and scope

The project owner directed: `修改方案，不做st股票列表分析`.

This repair removes the independent Tushare `stock_st` request and every downstream dependency on that response. It
does not edit or reinterpret frozen v1.3.1 bytes. v1.3.1 remains a closed, unexecuted protocol lineage node whose
capability approval was deferred and then withdrawn.

The repair intentionally retains a local safety filter over the already-required `stock_basic.name`: after NFKC
normalization and outer trimming, names beginning case-insensitive `ST` or `*ST` remain excluded with reason
`st_risk_warning`. This is not an ST-list request or analysis. It prevents the removal of `stock_st` from silently
turning a provider-visible risk-warning name into an eligible frame row.

The reduced control has a documented limitation: if `stock_basic.name` does not carry a current `ST`/`*ST` marker, the
v1.3.2 frame verifier has no second ST-list source with which to detect that status. Approval of v1.3.2 must explicitly
accept this lower-assurance boundary.

## 2. Version and isolation repair

The implementation must first publish and independently approve a full v1.3.2 protocol with:

- `supersedes_sha256` equal to the exact frozen v1.3.1 hash above;
- isolated roots below `artifacts/milestone-004/v1.3.2/` for capability, date evidence, and capture;
- authorization, receipt, stop, ledger, date-evidence, manifest, and supervisor-commit schema identifiers upgraded
  from v2 to v3;
- no adoption or mixing of v1.3.1 or earlier artifacts; and
- new protocol/golden checksum files without modifying any historical protocol or checksum bytes.

To preserve the v1.3.1 implementation and generator provenance, v1.3.2 must use a separate module closure:

- `qualitative_v2_m4_segmented_rest_v132.py`
- `qualitative_v2_m4_segmented_core_v132.py`
- `qualitative_v2_m4_segmented_runtime_v132.py`
- `qualitative_v2_m4_segmented_verify_v132.py`
- unchanged shared `qualitative_v2_audit.py`

The v1.3.1 modules remain byte-for-byte unchanged. The v1.3.2 manifest allowlist contains exactly the five files above.

## 3. Exact request matrices

The v1.3.2 frame matrix has exactly 35 ordered calls:

1. `index_classify(level=L1,src=SW2021)`;
2–32. the same 31 ordered `index_member_all(l1_code=<code>,is_new=Y)` partitions;
33. `stock_basic(exchange=SSE,list_status=L)`;
34. `stock_basic(exchange=SZSE,list_status=L)`;
35. `daily_basic(trade_date=YYYYMMDD)`.

There is no `stock_st`, `st`, historical name, alternate risk-warning, SDK, MCP, pagination, retry, fallback,
supplemental, or preflight provider request. Capability and capture both use this exact 35-call matrix. Date evidence
remains exactly two ordered SSE/SZSE `trade_cal` calls.

Existing fixed response and attempt byte limits remain unchanged unless the independent protocol review identifies a
reason to reduce them. Fail-fast, serial execution, one request per ordinal, supervisor deadline, raw-first sealing,
closed-set publication, candidate verification, and token-free offline verification remain unchanged.

## 4. Gate and ledger changes

The v1.3.2 frame verifier must:

- remove the `stock_st` response parser, structural gate, manifest gate field, reproduction dependency, ordinal
  expectation, fixture family, and error classification;
- change the final `daily_basic` source ordinal from 36 to 35;
- retain stock-basic structural validation for unique SSE/SZSE listed CNY securities and the existing allowed
  exchange/market/code-prefix tuples;
- retain `退市...` / `...退` delisting-name exclusion;
- retain local NFKC/trim/casefold `ST` and `*ST` prefix exclusion using only `stock_basic.name`;
- retain the 36-calendar-month listing-age exclusion, daily coverage, frame validation, and 12 sampling-cell gates;
- reduce ledger `source_kind` to exactly `membership|stock_basic|daily`;
- retain `st_risk_warning` only for a rejected `stock_basic` row whose normalized name has the prefix above; and
- remove all `stock_st`-sourced ledger rows and counts.

Overall frame PASS becomes the conjunction of authorization-window, classification, membership, stock-basic, daily,
frame, and sampling-cell gates. Complete/PASS separation and all failure-manifest rules remain unchanged.

## 5. Authorization and public interface

The v1.3.2 frame authorization fixes the 35-call matrix and uses the new v3 schema. Capability still uses
`probe_trade_date` and null capability/date refs; capture still requires independently verified capability PASS and
date evidence and remains restricted to the sampling-date capture window.

Public type/function names and CLI shapes remain equivalent but live in the v1.3.2 module. No CLI flag may restore or
request `stock_st`. The only live provider origin remains `https://api.tushare.pro:443`; token rules and all secret
handling remain unchanged.

The user's current 2000-point evidence is sufficient only as a candidate entitlement basis for the four remaining API
families. A real v1.3.2 capability attempt still requires a new external approval bound to:

- the final frozen v1.3.2 protocol and implementation commit;
- new unique v1.3.2 authorization/attempt IDs and paths;
- a completed probe trade date and a fresh future `+08:00` window;
- the named accountable authority, publisher, and operator;
- 35-call quota/cost/terms acceptance; and
- the explicit lower-assurance, name-only risk-warning exclusion described in Section 1.

The unexecuted v1.3.1 candidate IDs, paths, and window are retired and cannot be copied into v1.3.2.

## 6. Verification and tests

Directed synthetic coverage must include:

- exact 35-call frame and two-call calendar matrices, with no `stock_st` call or authorization field anywhere;
- daily ordinal 35 across call IDs, receipts, stop recovery, manifests, deadlines, and closed-set verification;
- `stock_basic.name` cases for `ST`, `st`, `*ST`, full-width/NFKC variants, outer whitespace, `退市` prefix, `退`
  suffix, and ordinary names containing `st` away from the prefix;
- proof that `st_risk_warning` can originate only from `stock_basic` and that ledger source counts exclude
  `stock_st`;
- explicit regression that a code formerly present only in a synthetic stock-ST list is not excluded unless its
  stock-basic name independently triggers the retained rule;
- v3 schema rejection of v1.3.1 authorization/artifact bytes and legacy assembler rejection of v1.3.2 attempts;
- capability/date/capture reference chains, candidate/post-publication verification, deadline/cancellation phases,
  tampering, permissions, links, path attacks, generator drift, and offline no-token/no-network verification; and
- all existing v1–v1.3.1 hashes, golden vectors, tests, and generator files unchanged.

Acceptance uses directed v1.3.2 pytest, all `tests/milestone004`, the full repository suite, Ruff lint/F401/format,
existing mypy with explicit new module files, all historical and v1.3.2 checksum checks, and `git diff --check`.

## 7. Ordered governance sequence

1. Review this repair plan and resolve any P0–P3 findings.
2. Draft the full v1.3.2 protocol and deterministic golden vectors.
3. Obtain an independent exact-byte protocol review and freeze only after strict PASS/P0–P3 NONE.
4. Implement the isolated v1.3.2 module closure and tests without modifying v1.3.1 generator files.
5. Run all acceptance gates and an independent implementation review to strict PASS.
6. Prepare a new external 35-call capability approval packet with fresh IDs/window and explicit name-only ST-risk
   acceptance.
7. Only after an external APPROVE may a canonical v1.3.2 authorization/checksum be published and invoked once.

This plan creates no protocol approval, authorization, credential access, network attempt, frame/sample assembly,
Reviewer execution, database access, production integration, or MILESTONE-005 authority.
