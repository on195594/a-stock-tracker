# MILESTONE-004 v1.2 capability-probe record

Date: 2026-07-16 (`Asia/Shanghai`)
Authorization ID: `qualitative-v2-m4-v1.2-capability-20260716-01`
Attempt ID: `m4-v1.2-capability-20260716-01`
Protocol SHA-256: `b711b9b81d8732b48f49c25b419dd9cf51eeae8aa4d2f7bbcd0a662a16782c23`
Authorization SHA-256: `6db2039f6f948d364cd1929918927e01581c6dfa3c1cae48156bb380a243693b`
Status: **NOT EXECUTED — `authorization_not_yet_valid`**

## Preflight result

At `2026-07-16T19:34:40+08:00`, the v1.2 CLI was invoked with the fixed authorization, checksum, and attempt ID. It
returned exit code 2 with the finite sanitized result:

```text
M4_CAPABILITY_NOT_EXECUTED: authorization_not_yet_valid
```

The authorization starts at `2026-07-17T00:00:00+08:00`. The authorization gate therefore stopped before token
loading, backend construction, output-root creation, or any provider call. A postcondition check at
`2026-07-16T19:34:58+08:00` confirmed that
`artifacts/milestone-004/v1.2/capability-probes` did not exist.

## Audit interpretation

- Real capability-probe execution count: **0**.
- Tushare call count under this authorization: **0**.
- Receipt, blob, manifest, or other probe artifact count: **0**.
- `complete`: not applicable; no attempt was opened.
- `capability_pass`: not evaluated.
- Terminal source state: not yet determined; neither `ELIGIBLE_FOR_FULL_CAPTURE_AUTHORIZATION` nor
  `NO_QUALIFIED_FRAME_SOURCE` may be asserted from this preflight.

The authorization and attempt remain unconsumed. The only permitted next action is one execution of the same exact
command during the fixed window ending at `2026-07-18T00:00:00+08:00`. If that window expires, the tool must record
`authorization_expired` without networking and M4 remains research-only; the authorization must not be extended or
rewritten by the execution tool.
