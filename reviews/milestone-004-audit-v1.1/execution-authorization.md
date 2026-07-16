# MILESTONE-004 evidence-feasibility audit execution authorization

Authorization ID: `qualitative-v2-m4-audit-authorization-2026-07-15-01`
Authorized at: `2026-07-15` (`Asia/Shanghai`)
Protocol: `qualitative-v2-m4-prereg-v1.1`
Status: **authorized for static-input preparation; awaiting dataset provenance**

## User authorization

> 批准启动 MILESTONE-004 真实 evidence feasibility audit。仅使用带 provenance 的只读静态数据集，
> 不读取或修改生产 tracker.db，不接入生产 pipeline；先完成 frame、sample 和 corpus 冻结，
> 真实 Reviewer 执行需再次单独申请授权。

## Authorized scope

- Load and validate a user-approved, read-only static sampling-frame dataset with provenance.
- Freeze the validated frame, deterministic 36-company sample, and complete candidate corpus under protocol v1.1.
- Store only audit-local blobs below `artifacts/milestone-004/v1.1/` and immutable manifests below this directory.
- Run offline integrity checks against the frozen frame, sample, corpus, and blob chain.

## Explicitly not authorized

- Reading or writing production `tracker.db`.
- Calling a live market-data, company-data, search, exchange, patent, or model provider to manufacture the required static dataset.
- Running Codex, AGY, Gemini, or any other Reviewer/model subprocess.
- Connecting the audit to production pipeline, cron, Telegram, Sheets, scoring weights, cache schema, or cutover logic.
- Freezing MILESTONE-005 artifacts or treating this authorization as MILESTONE-005 approval.

## Required provenance before frame freeze

The frame dataset must identify its publisher/provider, immutable snapshot or export identifier, acquisition time and
timezone, sampling trade date, original source location or transfer record, byte count, SHA-256, format, and the exact
field mapping for `ts_code`, `name`, `industry_code`, `industry_name`, unrounded `total_mv`, `trade_date`, and
`exchange`. The dataset must cover the full eligible A-share frame used by the provider, not the production watchlist.

No frame stage may be frozen until those fields and the dataset bytes are present and verify successfully. A missing,
mutable, synthetic, watchlist-only, or unverifiable dataset is a blocking input error and must not be silently replaced.

## Required provenance before corpus freeze

For every sampled company, the static corpus package must contain all 3 sources × 7 queries, including explicit
successful zero-result groups; official default-order result ranks; immutable raw document bytes; document URLs and
identifiers; one company-bound relationship report; and a complete technical-attempt ledger. Every file must have an
auditable origin, byte count, SHA-256, MIME declaration, and capture timestamp.

The candidate corpus remains blocked until the package satisfies protocol v1.1. Reviewer execution requires a new,
separate authorization bound to the eventual protocol hash, corpus-manifest hash, reviewer, bundle, prompt, backend,
model, command, and isolated environment.

## Supplemental one-time frame export authorization

Authorization ID: `qualitative-v2-m4-tushare-frame-export-2026-07-15-01`
Authorized at: `2026-07-15` (`Asia/Shanghai`)
Status: **attempted; blocked by provider account frequency; no package published**

> 批准使用项目现有 TUSHARE_TOKEN 对 Tushare 执行一次 MILESTONE-004 sampling-frame 只读导出，仅调用
> stock_basic、trade_cal、daily_basic、index_classify 和 index_member_all。不得输出或记录 token，不得读取
> 或修改 tracker.db，不得接入生产 pipeline。导出结果立即写入 gitignored incoming 目录并生成 provenance、
> SHA-256 和只读静态 frame 数据包；Reviewer 仍不授权。

This supplemental authorization overrides only the earlier prohibition on calling a live provider, and only for the
five named Tushare APIs during this single frame-export operation. It does not authorize any evidence retrieval,
candidate-corpus network collection, database access, production integration, or Reviewer/model execution. The API
token may be read from the process environment or local `.env` solely for request authentication and must never be
written to output, logs, exceptions, provenance, command manifests, or versioned files.

## Supplemental one-time AKShare frame export authorization

Authorization ID: `qualitative-v2-m4-akshare-frame-export-2026-07-15-01`
Authorized at: `2026-07-15` (`Asia/Shanghai`)
Status: **attempted; blocked by SZSE HTTPS transport; no package published**

> 批准使用现有 AKShare 执行一次 MILESTONE-004 audit-only frame 导出，仅调用 index_component_sw、
> stock_zh_a_spot_em、stock_sse_summary 和 stock_szse_summary。允许申万宏源行业成分与东方财富总市值仅用于
> 本次静态 feasibility audit；不得恢复生产行情入口，不读取或修改 tracker.db，不接入 pipeline，不运行
> Reviewer。必须保存 provenance、原始快照和 SHA-256，并在来源日期或完整性不一致时 fail closed。

This authorization overrides the live-provider prohibition only for one audit-only exporter execution and the four
named AKShare functions. The exporter forced HTTPS, enabled certificate verification, disabled redirects, and applied
an exact host allowlist. It stopped at `stock_szse_summary` when the approved SZSE adapter could not complete its HTTPS
request. No HTTP downgrade or second real execution was attempted, and no partial frame package was published.

## Supplemental AKShare plus direct SZSE HTTPS static-package authorization

Authorization ID: `qualitative-v2-m4-akshare-szse-https-frame-export-2026-07-15-01`
Authorized at: `2026-07-15` (`Asia/Shanghai`)
Status: **attempted; blocked by direct SZSE HTTPS transport; no package published**

> 批准执行一次新的 MILESTONE-004 audit-only 离线静态包抓取：AKShare 仅调用 index_component_sw、
> stock_zh_a_spot_em 和 stock_sse_summary；深交所完整性数据改为直接读取 szse.cn 官方 HTTPS 市场总貌页面
> 或其同域只读数据接口。禁止 HTTP 降级、跨域重定向和其他数据源；保存全部原始响应、请求参数、采集时间、
> 来源日期、SHA-256 和 provenance。日期或完整性不一致时 fail closed。不得读取或修改 tracker.db，不接入
> pipeline，不运行 Reviewer。

This authorization permits one new bounded exporter execution. AKShare is restricted to the three named functions.
The direct SZSE client is restricted to `https://www.szse.cn/api/report/ShowReport`, certificate verification, no
redirects, and a date-bound market-overview XLSX request. It does not authorize HTTP fallback, another provider,
production integration, database access, evidence collection, or Reviewer/model execution.

The one bounded execution stopped at the direct SZSE HTTPS request with a transport `ConnectionError`. It did not
downgrade to HTTP, follow a redirect, call another source, or retry. No partial package was published.

## Supplemental historical hybrid frame export authorization

Authorization ID: `qualitative-v2-m4-historical-hybrid-frame-export-2026-07-16-01`
Authorized at: `2026-07-16` (`Asia/Shanghai`)
Status: **attempted; blocked by Tushare `daily_basic` frequency limit; no package published**

The user instructed the agent to execute the immediately preceding plan to keep the sampling date at `2026-07-15`.
That plan authorized reuse of the sealed official SZSE static package, one date-bound Tushare `daily_basic` read, the
official SSE summary, and 31 SWS `index_component_sw` reads. It retained the prohibitions on production database and
pipeline access, alternate providers, and Reviewer execution.

The bounded operation stopped before any SWS query. A successful Tushare response first exposed additive pagination
metadata and was rejected by the strict local schema; after an offline fix and one bounded schema-repair retry, Tushare
enforced its one-request-per-minute limit. No frame/sample package or frozen stage was published. Details are preserved
in `frame-export-attempt-historical-hybrid-2026-07-16.md`.

## Supplemental historical hybrid retry authorization

Authorization ID: `qualitative-v2-m4-historical-hybrid-frame-export-2026-07-16-02`
Authorized at: `2026-07-16` (`Asia/Shanghai`)
Status: **attempted at 10:42:15; blocked by one-request-per-hour `daily_basic` limit; no package published**

The user explicitly approved one retry with the same historical-hybrid boundaries: only
`daily_basic(trade_date=20260715)`, `stock_sse_summary`, 31 `index_component_sw` calls, and the sealed SZSE 2026-07-15
package. Production `tracker.db`, pipeline, and Reviewer access remained prohibited.

Tushare rejected the date-bound request with an account-specific one-request-per-hour limit before any SWS request.
This corrects the earlier one-minute assumption for `daily_basic`. No automatic retry or partial publication occurred;
details are preserved in `frame-export-attempt-historical-hybrid-retry-2026-07-16.md`.

## Supplemental historical hybrid pagination attempt

Authorization ID: `qualitative-v2-m4-historical-hybrid-frame-export-2026-07-16-03`
Authorized at: `2026-07-16` (`Asia/Shanghai`)
Status: **attempted at 12:05:42; blocked by strict pagination-count validation; no package published**

The user's instruction to continue was treated as one same-scope execution after the confirmed hourly window. The
request passed frequency enforcement and returned data, but the additive `count` metadata differed from the returned
item count. The client stopped before SWS collection and published nothing.

Offline validation was corrected to reject `has_more=true`, invalid or negative counts, and counts smaller than the
current page, while allowing a total count larger than a complete page and recording both metadata values. No second
live call was made under this authorization. Details are preserved in
`frame-export-attempt-historical-hybrid-pagination-2026-07-16.md`.

## Supplemental historical hybrid count-sentinel attempt

Authorization ID: `qualitative-v2-m4-historical-hybrid-frame-export-2026-07-16-04`
Authorized at: `2026-07-16` (`Asia/Shanghai`)
Status: **attempted at 13:34:00; blocked on observed `count=0` sentinel; no package published**

The user's explicit continuation instruction authorized one unchanged-scope execution after the hourly window. Tushare
returned 5,525 rows with `count=0`; the strict parser stopped before SWS collection because zero had not yet been modeled
as an unknown-count sentinel.

Offline validation now accepts `count=0` or a nonzero count no smaller than the returned page, continues to require
`has_more=false`, and records both values in provenance. No retry occurred under this authorization. Details are
preserved in `frame-export-attempt-historical-hybrid-count-sentinel-2026-07-16.md`.
