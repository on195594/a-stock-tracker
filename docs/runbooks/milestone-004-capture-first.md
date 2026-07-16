# MILESTONE-004 capture-first runbook

Status: implementation ready; no real capture authorized by this document.

The historical-frame exporter now has three separate operations:

- `capture` is the only network-capable operation. It requires a canonical, pre-existing authorization JSON and a matching SHA-256 manifest bound to one attempt ID, the `2026-07-15` sampling date, an active time window, and the exact frozen 33-call matrix.
- `recover` only verifies and seals a crash-left `.in-progress-<attempt-id>` directory as incomplete. A recovered staging directory can never become complete or be assembled directly.
- `assemble` accepts only a sealed complete attempt, revalidates authorization/resume lineage, receipts, blobs, code hashes, and the static SZSE package, then creates `frame.csv`, `excluded.csv`, the 36-company `sample-manifest.json`, `frame-provenance.json`, and `SHA256SUMS` without reading a token or constructing a network client.

The earlier Tushare, AKShare, AKShare+SZSE, and combined historical export APIs/CLIs are permanently disabled and fail before token loading, runner/session construction, output creation, or transport. They are retained only as compatibility stubs and parsing/transport support for the authorized capture implementation; they are not alternate execution paths.

The tool does not create authorization records. An external approval record must be converted into canonical JSON using schema `m4-capture-authorization-v1`; its `allowed_calls` must preserve the exporter’s deterministic order. The separate checksum file contains `<sha256>  <authorization filename>`.

After a new, machine-verifiable authorization exists, the bounded commands are:

```bash
python3 scripts/export_m4_sampling_frame_historical_hybrid.py capture \
  --attempt-id <approved-attempt-id> \
  --authorization <authorization.json> \
  --authorization-sha256 <authorization.sha256> \
  --szse-package artifacts/milestone-004/incoming/manual-szse

python3 scripts/export_m4_sampling_frame_historical_hybrid.py recover \
  --attempt-id <crashed-attempt-id> \
  --szse-package artifacts/milestone-004/incoming/manual-szse

python3 scripts/export_m4_sampling_frame_historical_hybrid.py assemble \
  --attempt-dir <sealed-complete-attempt-dir>
```

The 2026-07-16 13:34 response cannot be recovered: the previous exporter retained its raw response only in memory and removed the temporary directory after schema failure. No raw bytes, receipt, or content hash from that response exist in the repository. It must not be reconstructed from logs or adopted into a future attempt.

The first authorized real capture-first attempt ran once at 2026-07-16 17:15. It retained a rate-limited Tushare
response and a valid SSE response, then sealed incomplete when strict TLS verification failed before the first SWS
response. The attempt has 31 missing calls and cannot be assembled. Its authorization is consumed; any diagnostic,
retry, or resume requires a new explicit authorization. Reviewer execution remains separately unauthorized.

Authorization validity is checked against a live timezone-aware clock before every provider call and again at raw-first publication. Receipts use the actual response-capture timestamp. `KeyboardInterrupt`, `SystemExit`, and `GeneratorExit` seal any already published bytes as incomplete and are then re-raised; they never authorize continuing to another provider call.
