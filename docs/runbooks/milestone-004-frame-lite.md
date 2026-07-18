# MILESTONE-004 lightweight frame builder

This path builds a local research frame and deterministic 36-stock sample. It is isolated from the production database,
pipeline, cron, Telegram, Gemini, Reviewer, and MILESTONE-005.

## Semantics

- The frame is the current SW2021 membership observed on 2026-07-18 joined to `daily_basic.total_mv` for 2026-07-17.
  It is a near-date current-membership snapshot, not a strictly reconstructable historical membership cross-section.
- The builder reuses the existing 31-industry mapping, six super-strata, and sampling seed only. It does not use the
  retired authorization, freeze, golden, oracle, attestation, or approval types.
- Raw responses and `run-summary.json` remain under the Git-ignored `artifacts/` tree. Only aggregate run facts are
  eligible for a repository report.

## Usage

Set `TUSHARE_TOKEN` in the process environment or the Git-ignored project `.env`, then run:

```bash
python scripts/build_m4_frame_lite.py probe --trade-date 20260717
python scripts/build_m4_frame_lite.py build \
  --from-probe artifacts/milestone-004/lite/<run-id>
```

`probe` makes five calls. A successful `build` revalidates those saved responses and makes only the other 30 industry
member calls, so the complete run uses 35 provider requests. Calls are serial with a minimum 1.3-second start interval;
there is no automatic retry. Any provider, HTTP, schema, truncation, mapping, or sampling-cell failure stops the run.

The run directory is mode `0700`. Successful builds atomically publish `derived/frame.csv`, `derived/sample.csv`, and
`derived/excluded.csv`; failed builds retain captured raw responses and a failure summary but publish no `derived/`.
