# Market Data Boundary 中期工程改造计划

创建时间：2026-06-05
目标项目：`/home/lin/a-stock-tracker`
状态：已实施并由后续 provider 替代方案承接（见 `CLAUDE.md`「行情数据源」章节及 `a_stock_tracker/data/market_data.py`）
执行原则：先文档化工程方案；后续实现必须 TDD、小步提交、外部 API 全部 mock。

## 0. 改写后的请求

原请求：

> 请按中期工程改造这个方案，帮我写项目详细改造计划，注意先写计划文档，不改代码。

改写为更可执行的提示：

> 在 `/home/lin/a-stock-tracker` 中，新增一份中期工程改造计划文档，目标是把行情获取从 `pipeline.py` 的业务流程中抽离为明确的数据服务边界。计划需要覆盖 `MarketDataProvider`、`DataQualityLayer`、`ScoringPipeline`、`SignalPipeline`、`NotificationLayer` 的职责拆分；设计 `MarketDataResult`、`daily_bars` 持久化表、主备数据源 fallback、L3 `entry_signal` 状态/原因审计、数据源 registry 约束、报告覆盖率展示；并给出按阶段可执行的文件改动、测试、验证命令、回滚方式和 Stop 条件。本次只写计划文档，不修改业务代码。

改动说明：把“工程改造”拆成可执行的模块边界、数据模型、schema、测试和验收标准；明确本轮只新增计划文档，避免误改代码或 DB。

后续审查修订请求：

> 根据独立工程审查结论修订本计划，把行情口径归一化、`price_at_score` freshness SLA、缓存刷新服务、审计持久化、旧库迁移测试、same-day rerun 兼容和 AKShare 直连验收写成硬性要求。

改动说明：把审查中的关键风险从建议项提升为执行约束，避免后续实现只移动网络调用位置，却没有解决结果可解释和可追溯问题。

## 1. 背景与问题定义

当前 `cmd_daily()` 同时承担以下职责：

- 主评分价格获取：逐股调用 `ak.stock_zh_a_hist_tx` 获取 `price_at_score`。
- 基本面读取：读取 `stock_fundamentals` 缓存。
- 实时 PB 分位计算：用当日价格覆盖 `pb_percentile_10y`。
- L3 日线拉取：逐股调用 `ak.stock_zh_a_hist` 获取 120+ 日窗口。
- 评分：调用 `score_stock()`。
- 写库：写入 `predictions`。
- 推送：结束后触发 Telegram daily signals。

这个结构导致外部行情源失败时，失败语义混在一起：

- 腾讯日线失败可能影响 `price_at_score`、PB 分位、预测写入。
- 东财/AKShare 日线失败主要影响 L3 `entry_signal`。
- `_retry()` 的日志没有稳定上下文，无法快速区分数据源、用途和股票。
- L3 失败会变成 `entry_signal=NULL/v1`，但缺少结构化原因和覆盖率入口。
- 用户看到“无推送”时，无法区分“确实无买点”和“L3 数据不可计算”。
- 回测和 `accuracy-report` 可能因 L3 样本缺失而出现解释偏差。

中期改造目标不是保证 AKShare 永不失败，而是让外部源失败变成可审计、可统计、可恢复的降级状态。

## 2. 总体目标

### 2.1 工程目标

1. `pipeline.py` 不再直接知道 `ak.stock_zh_a_hist` / `ak.stock_zh_a_hist_tx` 的调用细节。
2. 行情获取统一通过 `MarketDataProvider`，输出标准化 `MarketDataResult`。
3. L3 买点层从本地标准化 `daily_bars` 读取数据，不在评分循环内逐股实时拉 120+ 日窗口。
4. 主评分、L3 信号、结果更新、推送分别消费标准化数据和结构化质量状态。
5. 任意 fallback、失败、旧值、不可计算原因都能写入 DB 或报告，不只留在日志中。

### 2.2 用户价值目标

1. 用户能明确知道今日 L3 覆盖率，而不是只看到“无推送”。
2. 高分股票因 L3 数据失败未推送时，报告能列出不可计算原因。
3. 主评分准确性和 L3 信号准确性的影响范围分离。
4. 回测分母可解释，`entry_signal=NULL` 不污染 L3 命中率解读。
5. 外部源短时失败后，系统可通过本地历史行情缓存继续计算大部分信号。

## 3. 非目标

本计划不包含：

- 不引入自动交易、持仓、账户、下单或调仓。
- 不改真实历史 `tracker.db` 数据。
- 不启用 cron，不改变生产调度。
- 不进行真实 Telegram、Google Sheets、Gemini 或 AKShare 网络测试。
- 不新增依赖，除非后续单独批准。
- 不重写评分模型权重或 L3 v1 规则语义。
- 不把 `entry_signal=NULL` 当作 `0`。

## 4. 目标架构

### 4.1 模块职责

```text
MarketDataProvider
  fetch_score_price(code, date)
  fetch_l3_bars(code, end_date, window)
  fetch_outcome_price(code, target_date)

DataQualityLayer
  normalize result status
  record source/freshness/fallback_reason/error_code
  expose scoring/signal/report quality contracts

MarketDataCacheService
  refresh_daily_bars(codes, end_date, window)
  write standardized bars and audit rows
  return per-code results and coverage summary

ScoringPipeline
  consume fundamentals + score price + quality
  compute daily PB percentile
  call score_stock()
  write predictions

SignalPipeline
  consume standardized daily_bars
  call compute_entry_signal()
  write signal/status/reason

NotificationLayer
  consume score + signal + quality
  push only when score passes and L3 signal passes
  report skipped high-score rows caused by L3 unavailable
```

### 4.2 关键边界

- `MarketDataProvider` 是唯一允许直接调用 AKShare 的业务入口。
- `MarketDataCacheService` 是唯一负责批量刷新/增量刷新 `daily_bars` 的入口。
- `compute_entry_signal()` 保持纯函数，只处理标准化 `date/close/volume` 输入。
- `score_stock()` 保持确定性评分函数，不发网络、不写 DB。
- `cmd_daily()` 只编排流程，不处理具体行情源 schema，也不直接调用任何 `ak.*` API。
- `SignalPipeline` 只能读取 `daily_bars`，不得触发网络请求。
- `accuracy-report` 读取结构化质量字段，不从日志推断失败原因。
- 第一版不急于实现独立 `DataQualityLayer` service；数据质量先收敛为 `MarketDataResult`、`daily_bars`、`market_data_audit` 和 registry 的契约。

## 5. 核心数据结构

### 5.1 MarketDataResult

建议新增 `lib/market_data.py`，定义：

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Literal, TypeVar

T = TypeVar("T")

MarketDataStatus = Literal["ok", "degraded", "failed"]

@dataclass(frozen=True)
class MarketDataResult(Generic[T]):
    value: T | None
    status: MarketDataStatus
    source: str
    fetched_at: str
    fallback_source: str | None = None
    fallback_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    freshness_days: int | None = None
```

状态语义：

- `ok`：主数据源成功，数据满足 freshness 与 schema 要求。
- `degraded`：fallback 成功、使用旧值、估算值或部分质量缺失，但仍可消费。
- `failed`：没有可用值，消费者必须显式处理。

### 5.2 行情错误码

建议固定错误码，便于报告聚合：

```text
REMOTE_DISCONNECTED
TIMEOUT
RATE_LIMITED
EMPTY_RESPONSE
SCHEMA_CHANGED
MISSING_COLUMNS
INSUFFICIENT_WINDOW
SOURCE_STALE
MIXED_SOURCE_VOLUME_UNSAFE
UNKNOWN_ERROR
```

### 5.3 L3 信号状态

现有字段：

- `entry_signal INTEGER NULL`
- `entry_signal_version TEXT NULL`

建议新增字段：

- `entry_signal_status TEXT`
- `entry_signal_reason TEXT`
- `entry_signal_source TEXT`
- `entry_signal_fetched_at TEXT`

语义：

```text
entry_signal=1,    status=pass,          reason=PASS
entry_signal=0,    status=reject,        reason=BELOW_MA60 / BELOW_MA120 / LOW_VOLUME
entry_signal=NULL, status=unavailable,   reason=FETCH_FAILED / SOURCE_STALE / SCHEMA_CHANGED
entry_signal=NULL, status=insufficient,  reason=INSUFFICIENT_WINDOW / MISSING_VOLUME / MIXED_SOURCE_VOLUME_UNSAFE
```

兼容规则：

- 旧记录 `entry_signal IS NULL AND entry_signal_version IS NULL` 继续表示 pre-L3。
- 新记录只要 L3 v1 执行过，`entry_signal_version` 必须为 `v1`。
- `entry_signal_status` 缺失时，报告按旧逻辑兼容展示。

## 6. daily_bars 持久化表

### 6.1 建议 schema

建议在 `lib/cache.py` 中新增 `daily_bars`：

```sql
CREATE TABLE IF NOT EXISTS daily_bars (
    code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL NOT NULL,
    volume REAL,
    source TEXT NOT NULL,
    adjusted TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    error_code TEXT,
    PRIMARY KEY (code, trade_date, adjusted)
)
```

索引：

```sql
CREATE INDEX IF NOT EXISTS idx_daily_bars_code_date
ON daily_bars(code, trade_date DESC);
```

### 6.2 写入策略

- 每天先尝试增量更新 watchlist 的最近 N 日行情。
- N 初始建议为 220 日历日，确保覆盖 120 交易日 L3 窗口。
- 对同一 `code/trade_date/adjusted` 使用 upsert。
- 主源成功写 `quality_status=ok`。
- fallback 成功写 `quality_status=degraded`，并记录 `source`。
- 不把失败行伪装成行情行；失败写入独立审计表或返回 `MarketDataResult.failed`。
- `volume` 必须归一到统一单位后写入；若源字段单位无法确认，该源不得进入 L3 计算窗口。
- 同一 `code/end_date/window/adjusted` 的 L3 计算窗口原则上不得混合未验证源；若混源且没有测试证明 volume ratio 不变，输出 `entry_signal=NULL/v1`，reason 为 `MIXED_SOURCE_VOLUME_UNSAFE`。

### 6.3 读取策略

L3 读取：

```text
SELECT trade_date, close, volume
FROM daily_bars
WHERE code=? AND adjusted=''
ORDER BY trade_date DESC
LIMIT 120
```

然后按日期升序传给 `compute_entry_signal()`。

如果不足 120 条或缺 volume：

- 不再发网络请求补救。
- 输出 `entry_signal=NULL/v1`，reason 为 `INSUFFICIENT_WINDOW` 或 `MISSING_VOLUME`。
- 报告中计入 L3 不可计算。
- 如果 120 条窗口内存在未验证的混合 source，输出 `entry_signal=NULL/v1`，reason 为 `MIXED_SOURCE_VOLUME_UNSAFE`。

## 7. 数据源策略

### 7.1 优先级

L3 日线建议使用：

```text
primary: akshare.stock_zh_a_hist_tx
fallback: akshare.stock_zh_a_hist
cache: daily_bars
```

主评分价格建议使用：

```text
primary: daily_bars 最新 close
fallback: akshare.stock_zh_a_hist_tx 最近 5 日 close
failure: skip prediction write
```

freshness contract：

- `fetch_score_price(code, score_date)` 必须命中 `score_date` 或最近一个交易日。
- 最近交易日距 `score_date` 的自然日差不得超过 5 天；超过则返回 `failed/SOURCE_STALE`，默认不写 prediction。
- 若后续允许 `degraded/SOURCE_STALE` 继续写 prediction，必须同时写入 `price_at_score` 来源和 stale reason，并在 daily 汇总中展示。
- 周末/节假日运行时，最近交易日价格可视为 `ok`，但必须在测试中固定日期场景。

outcome 价格建议使用：

```text
primary: daily_bars target_date close
fallback: akshare.stock_zh_a_hist_tx target_date or nearest within 10 calendar days
failure: leave outcome null
```

### 7.2 Tencent 与 Eastmoney schema 归一化

`MarketDataProvider` 必须负责 schema 归一化：

- 腾讯源列名统一为 `date/open/high/low/close/volume`。
- 东财源中文列名统一为 `date/open/high/low/close/volume`。
- `close/open/high/low` 必须统一为未复权或计划指定的同一复权口径，初版固定 `adjusted=''`。
- `volume` 必须统一为同一单位；单位不确定时返回 `failed/MISSING_COLUMNS` 或 `failed/SCHEMA_CHANGED`，不得静默写入。
- 如果 volume 缺失，返回 `status=degraded` 或 `failed`，不能让 `compute_entry_signal()` 猜源 schema。

### 7.3 限速与重试

建议新增 provider 内部 retry：

- 每个 provider 调用必须带 `context`：`purpose/code/source/date/window`。
- 重试退避采用 jitter：例如 `1-2s`、`3-5s`、`8-13s`。
- 同一股票同一用途失败应短 TTL 缓存，避免一次 daily 重复打同一失败源。
- L3 数据失败不打任务级 `ERROR`，只记录 `WARNING` 或 structured degraded event。

## 8. 数据质量治理

### 8.1 registry 扩展

`docs/data-source-registry.yaml` 需要新增或扩展字段：

```yaml
  - field: daily_bars
    owner: market_data_cache
    requirement: required_for_signal
    source_primary: akshare.stock_zh_a_hist_tx
    source_fallback: akshare.stock_zh_a_hist
    cache: daily_bars
    refresh: daily_incremental
    freshness_sla: latest_trade_day_or_previous_trade_day
    affects_scoring: true
    affects_signal: true
    affects_outcome: true
    failure_behavior: degraded_or_failed_with_structured_reason
    user_visible_impact: l3_coverage_drop_or_score_price_missing

  - field: entry_signal
    owner: signal_input
    requirement: degradable
    source_primary: daily_bars
    source_fallback: none_during_signal_compute
    cache: predictions.entry_signal
    refresh: per_score
    freshness_sla: same_as_score_date_daily_bars
    affects_scoring: false
    affects_signal: true
    affects_outcome: false
    failure_behavior: write_null_v1_with_reason
    user_visible_impact: high_score_candidate_may_not_push
```

### 8.2 质量状态记录

必须新增最小 `market_data_audit` 表。`daily_bars` 只保存成功或可消费的标准化行情行；失败、fallback、stale、schema 异常必须写审计记录，否则报告无法解释覆盖率下降原因。

最小审计表 schema：

```sql
CREATE TABLE IF NOT EXISTS market_data_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    code TEXT NOT NULL,
    purpose TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    fallback_source TEXT,
    fallback_reason TEXT,
    error_code TEXT,
    error_message TEXT,
    fetched_at TEXT NOT NULL
)
```

用途枚举：

```text
score_price
l3_bars
outcome_price
benchmark_price
```

写入规则：

- 每次 provider/cache service 尝试获取行情，都至少写一条 audit。
- 成功主源：`status=ok`。
- fallback 成功：`status=degraded`，写 `fallback_source/fallback_reason`。
- 失败：`status=failed`，写 `error_code/error_message`。
- `market_data_audit` 可按 run_date 保留历史，不要求反写 `predictions` 历史行。

### 8.3 报告展示

`accuracy-report` 与 daily 日志至少展示：

```text
L3 覆盖率：28/38 = 73.7%
L3 不可计算：10
不可计算原因：FETCH_FAILED=8, INSUFFICIENT_WINDOW=2
strong 候选中 L3 不可计算：5
高分但未推送：5（L3 unavailable）
```

## 9. 分阶段实施计划

### Phase 0 — 文档与边界确认

目标：只提交本计划文档，不改代码。

允许修改：

- `docs/plans/2026-06-05-market-data-boundary-refactor-plan.md`

验证：

```bash
git diff -- docs/plans/2026-06-05-market-data-boundary-refactor-plan.md
```

完成标准：

- 文档包含目标架构、数据模型、阶段计划、测试、Stop 条件。
- 没有修改业务代码。

### Phase 1 — 先补观测能力，不改变行为

目标：在不改变现有 daily 结果的前提下，让日志和报告能解释 L3 失败。

建议文件：

- `pipeline.py`
- `tests/test_pipeline.py`
- `docs/data-source-registry.yaml`

改动：

1. `_retry()` 支持 `context` 参数，日志包含 `purpose/code/source/attempt`。
2. L3 获取失败降级为 structured warning，不作为 daily 任务级 error。
3. `accuracy-report` 显示 `NULL/v1` 不可计算数量。
4. registry 增加 `entry_signal` 的失败行为说明。

测试：

- L3 fetch 失败时仍写 prediction。
- 日志包含 `purpose=l3_bars code=... source=...`。
- `accuracy-report` 展示 `NULL/v1 不可计算`。

验证命令：

```bash
pytest tests/test_pipeline.py -q
pytest tests/ -q
```

回滚：

```bash
git restore pipeline.py tests/test_pipeline.py docs/data-source-registry.yaml
```

### Phase 2 — 增加 L3 状态/原因字段

目标：把 L3 不可计算原因从日志迁移到 DB 字段。

建议文件：

- `lib/cache.py`
- `lib/entry_signal.py`
- `pipeline.py`
- `tests/test_pipeline.py`
- `tests/test_l3_entry_signal.py`

schema：

- `predictions.entry_signal_status TEXT`
- `predictions.entry_signal_reason TEXT`
- `predictions.entry_signal_source TEXT`
- `predictions.entry_signal_fetched_at TEXT`

改动：

1. `EntrySignalResult` 扩展 status/source/fetched_at，或通过 pipeline 边界组装。
2. 新库建表包含新增字段。
3. 旧库只用 `ALTER TABLE ADD COLUMN` 补列，不改历史行。
4. `cmd_daily()` 写入 L3 状态字段。
5. `accuracy-report` 按 reason 聚合。
6. 明确 same-day rerun 规则：当天已有旧格式记录时，允许受控更新同一天、同 code、同 framework、同 `entry_signal_version='v1'` 的 `entry_signal_*` 审计字段；禁止更新早于 today 的历史记录。

测试：

- 新库 schema 包含字段。
- 旧库迁移后旧记录保持 `NULL`，新增 `tests/test_schema_migrations.py` 用旧 schema fixture 覆盖 `_ensure_columns`。
- L3 通过、拒绝、不可计算分别写入正确 status/reason。
- same-day rerun 不因 `INSERT OR IGNORE` 遗漏新审计字段。
- generated columns、unique constraint、重复 INSERT 兼容路径保持有效。

验证命令：

```bash
pytest tests/test_l3_entry_signal.py tests/test_pipeline.py -q
pytest tests/test_schema_migrations.py -q
pytest tests/ -q
```

回滚：

```bash
git restore lib/cache.py lib/entry_signal.py pipeline.py tests/test_pipeline.py tests/test_l3_entry_signal.py
```

Stop 条件：

- 需要 UPDATE 历史 predictions 才能通过测试。
- 新字段破坏 legacy schema 迁移。

### Phase 3 — 新增 MarketDataResult 与 Provider seam

目标：先建立 provider 抽象和 mockable seam，但暂不大改 `cmd_daily()`。

建议文件：

- `lib/market_data.py`
- `tests/test_market_data.py`
- `pipeline.py`

接口：

```python
class MarketDataProvider:
    def fetch_score_price(self, code: str, score_date: str) -> MarketDataResult[float]:
        ...

    def fetch_l3_bars(self, code: str, end_date: str, window: int) -> MarketDataResult[pd.DataFrame]:
        ...

    def fetch_outcome_price(self, code: str, target_date: str) -> MarketDataResult[float]:
        ...
```

改动：

1. 新增 dataclass 和错误码常量。
2. 实现 `AkshareMarketDataProvider`，封装现有 AKShare 调用。
3. 保持现有行为，先让 `cmd_daily()` 通过 provider 拉 `score_price`。
4. 所有 provider 测试 mock AKShare，不发真实网络。

测试：

- 腾讯源成功返回 `MarketDataResult(status="ok")`。
- 空响应返回 `failed/EMPTY_RESPONSE`。
- 远端断开返回 `failed/REMOTE_DISCONNECTED`。
- schema 缺列返回 `failed/SCHEMA_CHANGED` 或 `MISSING_COLUMNS`。

验证命令：

```bash
pytest tests/test_market_data.py tests/test_pipeline.py -q
pytest tests/ -q
```

回滚：

```bash
git restore lib/market_data.py tests/test_market_data.py pipeline.py
```

### Phase 4 — 建 daily_bars 表、行情审计和缓存刷新服务

目标：建立本地标准化行情缓存、最小审计持久化和批量刷新服务，为 L3 从缓存计算做准备。

建议文件：

- `lib/cache.py`
- `lib/market_data.py`
- `tests/test_schema_migrations.py`
- `tests/test_cache.py` 或 `tests/test_pipeline.py`
- `tests/test_market_data.py`

改动：

1. 新增 `daily_bars` schema 和索引。
2. 新增 `market_data_audit` schema 和索引。
3. 新增写入 helper：`upsert_daily_bars(conn, code, bars, source, adjusted, quality_status)`。
4. 新增读取 helper：`load_daily_bars(conn, code, end_date, limit, adjusted="")`。
5. 新增审计 helper：`insert_market_data_audit(conn, result, purpose, code, run_date)`。
6. 新增 `MarketDataCacheService.refresh_daily_bars(codes, end_date, window)`，负责批量刷新、增量刷新、写 bars、写 audit、返回 coverage summary。
7. provider 成功拉取 l3 bars 后由 cache service 写入缓存。
8. 禁止失败时写伪行情；失败必须写 audit。

测试：

- 新库包含 `daily_bars`。
- 新库包含 `market_data_audit`。
- upsert 同一 `code/trade_date/adjusted` 不重复。
- 读取按日期倒序 limit 后可转升序传给 L3。
- fallback 源写入 `quality_status=degraded`。
- provider failed 时不写 `daily_bars`，但写 `market_data_audit(status=failed,error_code=...)`。
- `refresh_daily_bars()` 返回 per-code status 和覆盖率 summary。
- volume 单位归一化和混源窗口策略有测试覆盖。

验证命令：

```bash
pytest tests/test_market_data.py tests/test_pipeline.py tests/test_schema_migrations.py -q
pytest tests/ -q
```

回滚：

```bash
git restore lib/cache.py lib/market_data.py tests/test_market_data.py tests/test_pipeline.py
```

Stop 条件：

- 需要迁移或改写历史 predictions。
- upsert 设计需要新增依赖。
- 无法确认 provider volume 单位或复权口径。

### Phase 5 — L3 改为从 daily_bars 计算

目标：把实时网络请求从 L3 信号计算路径移出。

建议文件：

- `pipeline.py`
- `lib/market_data.py`
- `lib/cache.py`
- `tests/test_pipeline.py`
- `tests/test_l3_entry_signal.py`

改动：

1. daily 开始阶段先刷新 watchlist 的 `daily_bars`。
2. daily 只能调用 `MarketDataCacheService.refresh_daily_bars()`，不得在循环里散落 provider 网络调用。
3. `_compute_stock_entry_signal()` 改为读取本地 `daily_bars`。
4. 如果缓存不足，不在信号计算阶段发网络请求。
5. L3 缓存不足时写 `entry_signal=NULL/v1` 和 `reason=INSUFFICIENT_WINDOW`。
6. 网络失败只影响行情刷新阶段，报告为 coverage drop，并可从 `market_data_audit` 追溯原因。

测试：

- provider 网络失败但本地 bars 足够时，L3 仍可计算。
- provider 网络成功写缓存后，L3 从缓存计算。
- 本地 bars 不足时写 `NULL/v1/INSUFFICIENT_WINDOW`。
- 混源 volume unsafe 时写 `NULL/v1/MIXED_SOURCE_VOLUME_UNSAFE`。
- `compute_entry_signal()` 测试保持纯函数，不 mock AKShare。
- `SignalPipeline` 或 `_compute_stock_entry_signal()` 测试证明不会触发 provider 网络请求。

验证命令：

```bash
pytest tests/test_l3_entry_signal.py tests/test_market_data.py tests/test_pipeline.py -q
pytest tests/ -q
```

回滚：

```bash
git restore pipeline.py lib/market_data.py lib/cache.py tests/test_pipeline.py tests/test_l3_entry_signal.py
```

Stop 条件：

- 改动需要改变 L3 v1 规则。
- 无法保持 old predictions schema 兼容。

### Phase 6 — 主评分价格复用 daily_bars

目标：减少 `cmd_daily()` 中逐股价格请求，把 score price 与 L3 bars 合并到同一个行情缓存更新流程。

建议文件：

- `pipeline.py`
- `lib/market_data.py`
- `tests/test_pipeline.py`
- `tests/test_market_data.py`

改动：

1. daily 先刷新 `daily_bars` 最近 5 日或 220 日窗口。
2. `price_at_score` 优先从满足 freshness SLA 的 `daily_bars` close 读取。
3. 缓存没有最新价格时，再调用 provider fallback。
4. 全部 score price failed 或 `SOURCE_STALE` 超过 SLA 时仍中止 daily，不写今日 prediction。
5. 记录 `price_at_score` 数据质量来源。

测试：

- 本地缓存有今日或最近交易日 close 时，不调用 AKShare 获取 score price。
- 本地缓存最新 close 超过 freshness SLA 时，不写 prediction，并记录 `SOURCE_STALE`。
- 缓存缺失时调用 provider fallback。
- 所有 score price failed 时，不写 predictions。
- PB 分位仍使用当日或最近可用 `price_at_score`。

验证命令：

```bash
pytest tests/test_pipeline.py tests/test_market_data.py -q
pytest tests/ -q
```

回滚：

```bash
git restore pipeline.py lib/market_data.py tests/test_pipeline.py tests/test_market_data.py
```

### Phase 7 — outcome price 接入 provider/cache

目标：让 30/60/90 天 outcome 更新也复用同一行情边界。

建议文件：

- `pipeline.py`
- `lib/market_data.py`
- `tests/test_pipeline.py`
- `tests/test_market_data.py`

改动：

1. outcome target_date 先查 `daily_bars`。
2. 缓存缺失时调用 provider 获取目标日或 fallback 估算。
3. 保持现有 `estimate_flag` 语义。
4. 失败时保留 outcome NULL，不阻断其他记录。

测试：

- target_date 缓存命中时不调用 AKShare。
- 目标日无数据但近邻数据可用时写估算和 `estimate_flag`。
- 单只 outcome 失败不阻断其他记录。

验证命令：

```bash
pytest tests/test_pipeline.py tests/test_market_data.py -q
pytest tests/ -q
```

### Phase 8 — Notification 与 report 使用质量状态

目标：用户可见层明确区分“无买点”和“不可计算”。

建议文件：

- `telegram_push.py`
- `pipeline.py`
- `tests/test_telegram_push.py`
- `tests/test_pipeline.py`

改动：

1. Telegram 继续只推 `entry_signal=1`。
2. daily 完成日志增加：
   - L3 覆盖率
   - high-score unavailable 数量
   - 主要 unavailable reason
3. `accuracy-report` 增加 reason 聚合和 strong 候选不可计算数量。
4. 可选：Telegram 在无推送时不发送，但日志原因更准确。

测试：

- 高分 `entry_signal=NULL` 不推送。
- 报告显示 high-score unavailable。
- reason 聚合结果稳定。

验证命令：

```bash
pytest tests/test_telegram_push.py tests/test_pipeline.py -q
pytest tests/ -q
```

### Phase 9 — registry 约束测试

目标：数据源 registry 从文档变成可测试约束。

建议文件：

- `docs/data-source-registry.yaml`
- `tests/test_data_source_registry.py`

改动：

1. registry 覆盖 `daily_bars`、`entry_signal`、`entry_signal_status`、`entry_signal_reason`。
2. 测试断言所有 market-data 字段包含：
   - `owner`
   - `source_primary`
   - `source_fallback`
   - `cache`
   - `refresh`
   - `failure_behavior`
   - `user_visible_impact`
3. stdlib-only 解析，保持不新增 PyYAML。

验证命令：

```bash
pytest tests/test_data_source_registry.py -q
pytest tests/ -q
```

## 10. 验收标准

最终完成后应满足：

1. `pipeline.py` 不直接调用任何 AKShare API。
2. 所有 `ak.*` 调用只允许出现在 `lib/market_data.py` 或明确的 provider 模块中。
3. `compute_entry_signal()` 只消费标准化 bars。
4. L3 可在 provider 网络失败但本地 bars 足够时照常计算。
5. `entry_signal=NULL/v1` 必须有结构化 status/reason。
6. `accuracy-report` 展示 L3 覆盖率和不可计算原因。
7. `telegram_push.py` 不推 `entry_signal=0/NULL`，但报告能解释漏推原因。
8. registry 覆盖 market-data 与 signal 字段。
9. `price_at_score` 必须满足 freshness SLA，超期价格不能静默参与主评分。
10. L3 使用的 `daily_bars.volume` 必须单位一致；未验证混源窗口不能输出 pass/reject。
11. `market_data_audit` 能解释每次行情刷新失败、fallback 和 stale 原因。
12. 所有测试通过：

```bash
rg "ak\\." pipeline.py
pytest tests/ -q
```

## 11. Stop 条件

任何 Phase 出现以下情况，停止执行并请求人工确认：

- 需要直接 UPDATE/DELETE 历史 `predictions`。
- 需要真实外部网络调用才能验证。
- 需要新增依赖。
- 需要改变 L3 v1 规则定义。
- 需要改变评分权重或 `score_stock()` 语义。
- schema 迁移无法兼容旧库。
- daily 行为从“高分但 L3 不可计算不推送”变成“高分直接推送”。
- 需要用 stale price 静默写入主评分。
- 无法证明 volume 单位一致或混源窗口安全。
- 测试无法恢复全量通过。

## 12. 推荐提交边界

每个 Phase 一个小 commit：

```text
docs: 新增 market data boundary 改造计划
test: 加强 L3 降级观测断言
feat: 增加 entry signal 状态审计字段
feat: 新增 market data provider seam
feat: 新增 daily bars 行情缓存与行情审计
refactor: L3 信号改为读取 daily bars
refactor: 主评分价格复用行情缓存
refactor: outcome 更新接入行情 provider
feat: 报告展示 L3 覆盖率与不可计算原因
test: 固化 market data registry 约束
```

## 13. 风险与缓解

### 风险 1：`daily_bars` 与现有价格路径口径不一致

缓解：

- 明确 `adjusted=''` 作为 L3 v1 和 score price 默认口径。
- golden-master 测试固定同一输入下 score price 不变。
- registry 写清楚 source 与 adjusted 口径。
- `volume` 单位必须统一；未验证源不得进入 L3 计算。
- 混源窗口默认不可计算，除非测试证明 volume ratio 不受 source 影响。

### 风险 2：provider 抽象过早膨胀

缓解：

- 第一版只实现当前需要的三个方法。
- 不引入策略框架或插件系统。
- 不新增依赖。

### 风险 3：schema 字段过多导致迁移复杂

缓解：

- predictions 只加最小 L3 审计字段。
- 行情质量细节优先放 `daily_bars` 和必做的最小 `market_data_audit`。
- 旧库只 `ALTER TABLE ADD COLUMN`，不改历史行。
- same-day rerun 只允许更新当天审计字段，不允许改写历史评分。

### 风险 4：报告信息变多但用户仍看不懂

缓解：

- 报告只展示覆盖率、strong 候选不可计算、Top reasons。
- 不把每只股票的底层错误全部塞入主报告。
- 详细审计保留在 DB 或 debug 日志。

## 14. 目标完成状态

完成后系统应达到：

```text
外部源失败 != 评分链路混乱
外部源失败 != 用户误解为无机会
外部源失败 != 回测样本静默污染
外部源失败 = 明确降级、可统计、可追溯、可恢复
```

关键工程判断：

> L3 买点层不再在 daily 评分循环里逐股实时拉 120+ 日行情；系统先构建本地标准化日线缓存，再从缓存计算信号。
