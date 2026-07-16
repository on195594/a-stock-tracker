# MILESTONE-004 v1.1 acquisition-route closeout

Date: 2026-07-16 (`Asia/Shanghai`)
Protocol: `qualitative-v2-m4-prereg-v1.1`
Status: **closed as technically infeasible**
Evidence-coverage verdict: **not reached**

## Decision

The v1.1 acquisition route is closed. This is a technical feasibility decision, not an evidence-coverage `FAIL`.
No complete sampling frame, deterministic sample, corpus, Reviewer output, or coverage report was produced, so the
frozen coverage estimand was never evaluated.

The retained capture-first attempt is incomplete and remains non-assemblable. It must not be repaired, resumed,
promoted, or interpreted as a partial frame. No further Tushare, SWS, AKShare, exchange, production-database, or
alternate-source request is authorized by this closeout.

## Technical record

- Tushare `daily_basic(trade_date=20260715)` was repeatedly constrained by account-specific frequency and daily-call
  limits. The sealed capture-first response was a provider error and is `raw_only`; it does not contain adoptable
  market-cap rows.
- The approved SWS route failed strict TLS verification because the server omitted the required intermediate
  certificate. A separately authorized handshake reproduced verification code 20. The one authorized AIA fetch also
  failed during TLS negotiation. TLS verification was not disabled and no HTTP downgrade was attempted.
- The sealed attempt contains only the Tushare error response and the valid SSE summary. All 31 frozen SWS component
  calls are missing. It is therefore incomplete by construction and cannot enter offline assembly.
- Earlier exporter responses that were not raw-first published were not reconstructed from logs or memory. All legacy
  exporter entry points remain disabled and their historical diagnostics remain preserved.

The authoritative attempt details remain in
`capture-attempt-m4-capture-20260716-01.md`, and the transport diagnosis remains in
`sws-tls-diagnostic-20260716-01.md`. Their bytes and all prior v1/v1.1 protocol, authorization, receipt, blob, and
manifest artifacts are unchanged by this closeout.

## Forward boundary

M4 remains research-only. v1.1 has no qualified frame source and no execution path to retry. A later protocol may
evaluate a new source only through a separately preregistered capability gate that preserves the SW2021 dictionary,
seed, 36-stock stratification, coverage gates, Reviewer rules, and evidence rules.

Failure of every source admitted by that later gate terminates in `NO_QUALIFIED_FRAME_SOURCE`. Continuing after that
state requires a new v1.3 candidate-source protocol; it must not fall back to SWS, a production database, or an
unregistered provider.
