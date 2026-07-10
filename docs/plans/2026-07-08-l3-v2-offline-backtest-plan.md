# L3 v2 离线回测实施计划

**日期：** 2026-07-08
**状态：** implemented / NEED_QFQ
**Spec：** `docs/specs/2026-07-08-l3-v2-entry-signal-spec.md`
**Review evidence：**

- `docs/reviews/agy-l3-v2-spec-engineering-review/agy_stdout.txt`
- `docs/reviews/agy-l3-v2-spec-investment-review/agy_stdout.txt`
- `docs/reviews/agy-l3-v2-backtest-review/stdout.md`

**Implementation outcome（2026-07-10）：**

- 已新增 `scripts/offline_l3_v2_backtest.py`，只读打开 `tracker.db`，不写 DB、不触发 Telegram/cron/Gemini/Sheets。
- 已输出 `docs/reviews/2026-07-08-l3-v2-backtest-report.md` 和 `docs/reviews/l3-v2-backtest-artifacts/`。
- 已完成 AGY 独立复审，最终 verdict 为 `APPROVE`。
- 当前决策为 `NEED_QFQ`，原因是 Tushare `adj_factor` 返回 `1次/分钟` 限频，qfq panels `0/38`，buy_strong qfq issue `766`。
- 后续不得直接进入 TDD；必须先设计 qfq 限速/缓存/分批方案并重跑只读报告。

---

## 0. 请求改写与执行边界

用户请求：

> 下一步不该写生产代码；应该写 离线回测实施计划，只读 tracker.db，先验证 qfq 派生、v1/v2 对比、去重冷却和交易成本影响。

执行化改写：

> 为 `a-stock-tracker` 编写一个只读离线回测实施计划，目标是在不改生产代码、不写 `tracker.db`、不触发 Telegram/cron 的前提下，设计脚本与报告来验证 L3 v2：qfq 派生是否可行、v1/v2 信号差异、连续触发去重/20 交易日冷却、交易成本对 30d alpha 的影响，以及是否值得进入后续 TDD 实装。

改写点：

- 明确本阶段只写计划，不执行回测、不改代码。
- 明确 `tracker.db` 只读，输出只允许落到项目内报告路径。
- 明确验收标准是形成可执行的离线回测包，而不是生产上线。

执行边界：

- 本计划不授权修改 `lib/entry_signal.py`、`pipeline.py`、`telegram_push.py`、`lib/cache.py` 的生产逻辑。
- 本计划不授权写入 `tracker.db`、cron、Telegram、Google Sheets、Gemini 或任何外部状态。
- 本计划不授权新增依赖；如 qfq 或统计需要新依赖，先改计划并请求确认。
- 本计划允许后续在用户批准后新增只读脚本与报告文件：`scripts/offline_l3_v2_backtest.py`、`docs/reviews/2026-07-08-l3-v2-backtest-report.md`。

---

## 1. Phase Ledger

| Phase | 状态 | 目标 | 最小落地改动 |
|---|---|---|---|
| P0 | completed | 回测脚本骨架与只读保护 | 新增脚本，能 `--help`，默认 dry-run，不写 DB |
| P1 | completed / NEED_QFQ | QFQ 派生 feasibility | 从只读数据源/本地数据构造 qfq OHLC，输出诊断，不写库 |
| P2 | completed | v1 replay 与 v2 candidate 计算 | 同一输入上产生 v1/v2 日频信号对比 CSV/JSON |
| P3 | completed | 去重冷却与交易成本模型 | 生成原始日频与首发+20日冷却两套样本 |
| P4 | completed | 报告生成与人工审查 | 输出 Markdown 报告，列出是否进入 TDD 的门槛 |
| P5 | completed | 独立 review gate | AGY/Codex 只读审查回测报告和脚本 |

---

## 2. P0 — 回测脚本骨架与只读保护

### 目标

建立一个默认安全、只读、可复现的离线回测入口。

### 最小落地改动

新增：

```text
scripts/offline_l3_v2_backtest.py
```

建议 CLI：

```bash
python3 scripts/offline_l3_v2_backtest.py \
  --db tracker.db \
  --start 2025-01-01 \
  --end 2026-07-08 \
  --output docs/reviews/2026-07-08-l3-v2-backtest-report.md \
  --artifacts-dir docs/reviews/l3-v2-backtest-artifacts \
  --dry-run
```

脚本必须：

- 用 SQLite read-only URI 打开 DB，例如 `file:tracker.db?mode=ro`。
- 启动时检查 `PRAGMA query_only=ON` 或等价只读保护。
- 禁止执行 `INSERT/UPDATE/DELETE/CREATE/DROP/ALTER`。
- 默认只输出 stdout 摘要；只有显式传 `--output`/`--artifacts-dir` 才写报告文件。
- 不导入 Telegram/Gemini/Sheets 相关模块。
- 不调用 `pipeline.py daily`、`outcome-update`、`accuracy-report`。

### 代码形状

```python
@dataclass(frozen=True)
class BacktestConfig:
    db_path: Path
    start: date
    end: date
    preload_trading_days: int
    output: Path | None
    artifacts_dir: Path | None
    dry_run: bool
    codes: list[str] | None
    no_external_fetch: bool
    allow_tushare_fetch: bool
    buy_strong_threshold: float
    in_sample_end: date | None
    out_of_sample_start: date | None
    commission_bps: float
    slippage_bps: float
    stamp_tax_bps: float


def open_readonly_db(path: Path) -> sqlite3.Connection:
    ...


def run_backtest(config: BacktestConfig) -> BacktestResult:
    ...
```

### 回测样本域

脚本默认评估域必须以 `predictions` 表为准，而不是 watchlist 全量日频笛卡尔积：

```sql
SELECT code, name, score_date, total_score, outcome_30d, benchmark_30d
FROM predictions
WHERE framework='A'
  AND score_date BETWEEN :start AND :end
```

理由：L3 是 L1/L2 评分后的入场过滤层，实盘语境是“已有 Framework A 评分记录后再判断买点”。若直接对全 watchlist 每日全量计算，会混入未被评分或低分股票，导致回测与实盘推送环境不一致。

CLI 的 `--codes` 只能在上述 predictions 基准样本内做子集过滤，不能扩展为全量 watchlist 计算。

核心决策指标必须另行输出 `total_score >= buy_strong` 子集。默认 `buy_strong` 从 `weights.json` 读取；若脚本不读取配置，则 CLI 必须显式传入 `--buy-strong-threshold`，并在报告 metadata 中记录阈值。全 Framework A 样本只用于覆盖诊断和样本分布，不能替代真实推送语境下的 `buy_strong` 子集结论。

日线加载必须使用预取窗口：为了在 `start` 当天计算 MA60/MA120、MA60_5d_ago 与 `high_20d_prev`，脚本查询 `daily_bars` 或外部 qfq 数据时必须从 `start` 前至少 120 个交易日开始读取；指标统计和报告样本仍只包含 `start <= score_date <= end`。CLI 默认 `--preload-trading-days 120`，低于 120 时必须拒绝运行或降级为 `NEED_SPEC_FIX`。

### 验证命令

```bash
python3 scripts/offline_l3_v2_backtest.py --help
python3 scripts/offline_l3_v2_backtest.py --db tracker.db --start 2026-07-01 --end 2026-07-08 --dry-run --no-external-fetch
python3 -m py_compile scripts/offline_l3_v2_backtest.py
```

### Rollback

```bash
git restore -- scripts/offline_l3_v2_backtest.py
```

### Stop 条件

- 脚本需要写 `tracker.db` 才能跑。
- 脚本需要真实 Telegram/Gemini/Sheets 调用。
- 脚本无法在无新增依赖情况下完成最小 dry-run。

---

## 3. P1 — QFQ 派生 feasibility

### 目标

验证 qfq OHLC 是否能在离线阶段稳定派生，先不改 provider、不写 `daily_bars`。

### 数据源顺序

1. **本地只读优先**：读取 `daily_bars adjusted='none'` 作为 none 基线。
2. **Tushare qfq 派生候选**：若环境已有 `TUSHARE_TOKEN` 且用户批准可调用外部行情，读取 `daily` + `adj_factor` 并在内存中派生 qfq。
3. **无外部权限时降级**：报告 `QFQ_UNAVAILABLE`，只输出 none 基线与“不得产生 pass_strong”的诊断。

> 注意：本计划不默认调用外部 API。若执行时要用 Tushare 拉新数据，必须先确认，因为这超出纯本地只读。

若回测窗口没有 qfq 覆盖，脚本必须把决策状态写为 `NEED_QFQ`（或 `NEED_SPEC_FIX`），不得输出 `GO_TDD`。`--no-external-fetch` 默认路径只能用于只读 smoke、覆盖诊断和 none-vs-qfq 缺口说明；不能用于证明 v2 strong 有效或无效。

### qfq 派生规则候选

对同一股票：

```text
qfq_factor_t = adj_factor_t / adj_factor_latest
qfq_close_t = close_t * qfq_factor_t
qfq_open_t  = open_t  * qfq_factor_t
qfq_high_t  = high_t  * qfq_factor_t
qfq_low_t   = low_t   * qfq_factor_t
```

派生前必须按交易日 inner join / exact match 对齐 `daily` 与 `adj_factor`。若任一股票存在缺失复权因子、重复交易日、价格序列与因子序列长度不匹配，或 latest factor 不在价格窗口内，脚本必须将该股票 qfq 面板标记为 `QFQ_ALIGNMENT_FAILED`，输出 warning 级诊断，并禁止该股票输出 `pass_strong`。

脚本必须输出：

- 有 qfq 数据的股票数。
- qfq 覆盖天数。
- qfq 对齐失败股票数与失败原因分布。
- none vs qfq 在长江电力等高股息股票上的 MA60/MA120 差异。
- qfq 不可用股票列表与原因。

### 最小落地改动

在脚本内新增：

```python
@dataclass(frozen=True)
class PricePanel:
    code: str
    adjusted: Literal["none", "qfq"]
    bars: list[DailyBar]
    source: str
    volume_unit: str
    preload_start: date
    stale_reason: str | None
    limitation: str | None


@dataclass(frozen=True)
class DataContractState:
    adjusted: Literal["none", "qfq"]
    source: str
    volume_unit: str
    is_stale: bool
    stale_reason: str | None
    unavailable_reason: str | None
    alignment_reason: str | None
```

### 验证命令

本地只读验证：

```bash
python3 scripts/offline_l3_v2_backtest.py \
  --db tracker.db \
  --start 2026-06-01 \
  --end 2026-07-08 \
  --codes 600900 \
  --no-external-fetch \
  --dry-run
```

可选外部 qfq 验证（需用户确认后才执行）：

```bash
python3 scripts/offline_l3_v2_backtest.py \
  --db tracker.db \
  --start 2025-01-01 \
  --end 2026-07-08 \
  --codes 600900 \
  --allow-tushare-fetch \
  --dry-run
```

### Rollback

```bash
git restore -- scripts/offline_l3_v2_backtest.py
```

### Stop 条件

- qfq 派生需要新增依赖。
- Tushare 权限不足且脚本不能优雅降级为 `QFQ_UNAVAILABLE`。
- qfq 与 none 混入同一窗口且没有显式标签。
- qfq `daily` 与 `adj_factor` 无法按交易日对齐且脚本仍尝试输出 `pass_strong`。

---

## 4. P2 — v1 replay 与 v2 candidate 计算

### 目标

在同一日线输入上重放 v1，并计算 v2 candidate，输出日频对比，不改生产 `entry_signal.py`。

### 最小落地改动

脚本内实现独立离线函数，避免误用生产路径：

```python
def compute_l3_v1_replay(bars: Sequence[DailyBar]) -> SignalResult:
    ...


def compute_l3_v2_candidate(
    price_panel: PricePanel,
    market_state: MarketState,
    data_contract: DataContractState,
) -> SignalResult:
    ...
```

v2 candidate 固定使用 spec 当前候选：

- `close > MA60`
- `close > MA120`
- `MA60 >= MA60_5d_ago`
- `vol5 >= vol20 * 1.15` 为 strong volume
- `vol5 > vol20` 且 `< 1.15x` 为 weak volume
- `high_20d_prev = max(high over previous 20 trading days, excluding today)`
- `market_state`：从 `index_prices` 查询沪深 300，symbol 固定为 `'000300'`；`close > MA20 AND MA20 >= MA20_5d_ago` 为 bullish，否则 weak；缺失、窗口不足或 stale 为 unknown。

`compute_l3_v2_candidate` 必须从 `data_contract` 判断 `adjusted`、`source`、`volume_unit`、stale 和 unavailable reason；不得只用 `qfq_available: bool` 代替数据契约。若 `adjusted='none'` 或 qfq 缺失，最多输出 `pass_weak` 或 `unavailable`，并带 `QFQ_UNAVAILABLE` / `ADJUSTED_NONE` 等 reason。

### 输出 artifact

```text
docs/reviews/l3-v2-backtest-artifacts/signals_daily.csv
```

字段建议：

```text
score_date,code,name,total_score,
is_buy_strong_candidate,buy_strong_threshold,
v1_signal,v1_status,v1_reason,
v2_signal_status,v2_reason,v2_adjusted,
source,volume_unit,stale_reason,unavailable_reason,
preload_start,alignment_reason,
close,ma60,ma120,ma60_5d_ago,
vol5,vol20,high_20d_prev,market_state
```

### 验证命令

```bash
python3 scripts/offline_l3_v2_backtest.py \
  --db tracker.db \
  --start 2026-06-01 \
  --end 2026-07-08 \
  --codes 600900 \
  --artifacts-dir docs/reviews/l3-v2-backtest-artifacts \
  --no-external-fetch
```

### Rollback

```bash
git restore -- scripts/offline_l3_v2_backtest.py
rm -rf docs/reviews/l3-v2-backtest-artifacts
```

### Stop 条件

- v2 函数需要改生产 `lib/entry_signal.py` 才能跑。
- `high_20d_prev` 含今日。
- market_state 缺失导致全样本 unavailable。

---

## 5. P3 — 去重冷却与交易成本模型

### 未结案样本处理

计算 alpha、hit rate、Sharpe、MaxDD 等收益类指标时，必须排除 `outcome_30d IS NULL` 或 `benchmark_30d IS NULL` 的未结案样本；但报告要单独列出未结案样本数量，避免误把缺失数据当作 0 收益。

报告必须包含提示：

```text
本报告中的去重胜率依赖 20d 冷却。如果实盘推送未装配同股冷却机制，请勿使用该去重胜率作为上线依据。
```


### 目标

生成两套结果：

1. 原始日频样本：用于和当前 accuracy-report 兼容。
2. 首发+20 交易日冷却样本：用于更接近一次真实买入决策。
3. In-Sample / Out-of-Sample 样本：默认时间切分为 IS=`2025-01-01` 至 `2025-12-31`，OOS=`2026-01-01` 至 `end`；若 `start` 晚于默认 IS 起点，则按实际窗口重算并在报告 metadata 中说明。

### 去重规则

对每个 `code`：

```text
eligible day = v2_status in {pass_strong, pass_weak}
new event = previous 20 trading days for this code have no counted event
cooldown = 20 trading days after counted event
```

v1 对比也必须用同样冷却口径生成一份结果，避免 v1/v2 比较不公平。

### 成本模型

默认参数：

```text
commission_bps = 2.5
slippage_bps = 5.0
stamp_tax_bps = 5.0  # 卖出侧 0.05%
round_trip_cost_bps = commission_bps * 2 + slippage_bps * 2 + stamp_tax_bps
```

输出：

```text
raw_alpha_30d
net_alpha_30d = raw_alpha_30d - round_trip_cost_bps / 100
```

参数必须 CLI 可覆盖：

```bash
--commission-bps 2.5 --slippage-bps 5 --stamp-tax-bps 5
```

### 输出 artifact

```text
events_raw_daily.csv
events_dedup_20d.csv
metrics_summary.json
```

### 验证命令

```bash
python3 scripts/offline_l3_v2_backtest.py \
  --db tracker.db \
  --start 2025-01-01 \
  --end 2026-07-08 \
  --artifacts-dir docs/reviews/l3-v2-backtest-artifacts \
  --no-external-fetch
```

### Rollback

```bash
git restore -- scripts/offline_l3_v2_backtest.py
rm -rf docs/reviews/l3-v2-backtest-artifacts
```

### Stop 条件

- outcome/benchmark 缺失导致样本无法解释且脚本未标注。
- 去重结果没有保留原始日频对照。
- 成本模型无法关闭或无法参数化。
- OOS 切分缺失，或报告没有说明 IS/OOS 日期边界。

---

## 6. P4 — 报告生成与人工审查

### 目标

输出一份 Markdown 报告，回答“是否值得进入 TDD 实装”。

### 最小落地改动

新增报告：

```text
docs/reviews/2026-07-08-l3-v2-backtest-report.md
```

报告结构：

```md
# L3 v2 Offline Backtest Report

## 1. Run metadata
- db path / sqlite readonly mode
- start/end
- preload trading days and preload_start policy
- qfq source status
- buy_strong threshold source/value
- IS/OOS split dates
- no-external-fetch or allow-tushare-fetch
- cost params

## 2. Data coverage
- stocks covered
- bars coverage
- qfq coverage
- unavailable reasons
- data contract coverage: source / adjusted / volume_unit / stale_reason / alignment_reason

## 3. Signal distribution
- v1 pass/reject/unavailable
- v2 strong/weak/reject/unavailable
- all Framework A sample vs buy_strong subset
- high dividend qfq vs none examples

## 4. Raw daily metrics
- n / avg return / avg benchmark / avg alpha / hit rate / median alpha / worst trade / MaxDD proxy

## 5. Dedup 20d metrics
- same metrics as raw daily

## 6. Cost-adjusted metrics
- net alpha after default cost
- sensitivity table

## 7. IS/OOS diagnostics
- in-sample metrics
- out-of-sample metrics
- sample count warnings

## 8. Decision gate
- GO_TDD / NEED_QFQ / NEED_SPEC_FIX / STOP
- reasons

## 9. Known limitations
```

### GO_TDD 建议门槛

只能作为初始门槛，非自动上线：

```text
- v2 pass_strong 去重样本 n >= 20，或报告明确样本不足但方向值得扩大样本池。
- v2 pass_strong net_alpha_30d > v1 pass net_alpha_30d。
- v2 pass_strong hit_rate > v1 pass hit_rate。
- 上述 pass_strong 指标必须基于 `total_score >= buy_strong` 子集；全 Framework A 样本只能作为诊断附录。
- qfq 覆盖足以计算 pass_strong；若 qfq 缺失或全部为 `QFQ_UNAVAILABLE`，决策只能是 `NEED_QFQ`，不得 `GO_TDD`。
- qfq `daily` 与 `adj_factor` 已按交易日对齐；若存在 `QFQ_ALIGNMENT_FAILED` 且影响 pass_strong 子集，决策不得 `GO_TDD`。
- OOS 子集不得为空；若 OOS 样本不足，报告必须降级为样本不足或扩大样本建议。
- high-dividend qfq vs none 差异解释清楚。
- 没有发现未来函数、样本泄漏、v1/v2 混算。
```

### 验证命令

```bash
python3 scripts/offline_l3_v2_backtest.py \
  --db tracker.db \
  --start 2025-01-01 \
  --end 2026-07-08 \
  --output docs/reviews/2026-07-08-l3-v2-backtest-report.md \
  --artifacts-dir docs/reviews/l3-v2-backtest-artifacts \
  --no-external-fetch
```

### Rollback

```bash
rm -f docs/reviews/2026-07-08-l3-v2-backtest-report.md
rm -rf docs/reviews/l3-v2-backtest-artifacts
```

### Stop 条件

- 报告不能区分 qfq unavailable 与真实 weak。
- 报告把样本不足解释为规则有效。
- 报告遗漏成本调整结果。

---

## 7. P5 — 独立 review gate

### 目标

在任何 TDD 实装前，让独立 reviewer 审查脚本与报告，避免把回测 bug 带入生产。

### 最小落地改动

新增证据目录：

```text
docs/reviews/agy-l3-v2-backtest-review/
```

Review prompt 要求：

- 脚本是否真正只读 DB。
- qfq 派生是否数学正确。
- 是否存在未来函数。
- v1/v2 对比是否公平。
- 去重冷却是否与报告描述一致。
- 成本模型是否正确。
- GO_TDD / STOP 结论是否被数据支持。

### 验证命令

```bash
sha256sum scripts/offline_l3_v2_backtest.py docs/reviews/2026-07-08-l3-v2-backtest-report.md > docs/reviews/agy-l3-v2-backtest-review/before-hashes.sha256
agy --print "$(cat docs/reviews/agy-l3-v2-backtest-review/prompt.txt)" --print-timeout=10m > docs/reviews/agy-l3-v2-backtest-review/stdout.md 2> docs/reviews/agy-l3-v2-backtest-review/stderr.log
sha256sum scripts/offline_l3_v2_backtest.py docs/reviews/2026-07-08-l3-v2-backtest-report.md > docs/reviews/agy-l3-v2-backtest-review/after-hashes.sha256
diff -u docs/reviews/agy-l3-v2-backtest-review/before-hashes.sha256 docs/reviews/agy-l3-v2-backtest-review/after-hashes.sha256
```

### Rollback

```bash
rm -rf docs/reviews/agy-l3-v2-backtest-review
```

### Stop 条件

- Reviewer 返回 `REQUEST_CHANGES` 且阻塞项未修。
- Hash drift 不是预期证据文件变化。
- Reviewer 指出只读边界破坏。

---

## 8. 总体验证命令

计划执行完成后，至少运行：

```bash
python3 -m py_compile scripts/offline_l3_v2_backtest.py
python3 scripts/offline_l3_v2_backtest.py --help
python3 scripts/offline_l3_v2_backtest.py --db tracker.db --start 2026-07-01 --end 2026-07-08 --dry-run --no-external-fetch
python3 scripts/offline_l3_v2_backtest.py --db tracker.db --start 2025-01-01 --end 2026-07-08 --dry-run --no-external-fetch --preload-trading-days 120
git diff --check -- scripts/offline_l3_v2_backtest.py docs/reviews/2026-07-08-l3-v2-backtest-report.md docs/plans/2026-07-08-l3-v2-offline-backtest-plan.md
git status --short
```

如后续脚本纳入测试套件，再补：

```bash
pytest tests/ -q
```

但本离线脚本阶段不要求跑全量测试作为唯一验收，除非生产代码被改动。

---

## 9. 回滚路径

计划文件回滚：

```bash
git restore -- docs/plans/2026-07-08-l3-v2-offline-backtest-plan.md
```

后续执行产物回滚：

```bash
git restore -- scripts/offline_l3_v2_backtest.py
rm -f docs/reviews/2026-07-08-l3-v2-backtest-report.md
rm -rf docs/reviews/l3-v2-backtest-artifacts
rm -rf docs/reviews/agy-l3-v2-backtest-review
```

DB 不需要回滚，因为计划要求全程只读 `tracker.db`。

---

## 10. 执行前批准点与实际执行

执行本计划前需要用户明确确认：

1. 是否允许新增 `scripts/offline_l3_v2_backtest.py`。
2. 是否允许写入 `docs/reviews/2026-07-08-l3-v2-backtest-report.md` 和 artifact 目录。
3. 是否允许调用 Tushare 拉取 `daily + adj_factor`。默认不允许；默认只做本地只读与 qfq unavailable 诊断。
4. 是否需要把回测范围扩展到 watchlist 外样本。默认不扩展，因为可能需要额外数据源与更大抓取成本。

实际执行结果：

- 用户已授权实现只读离线脚本、报告和 artifacts。
- 用户确认项目内存在 Tushare token；脚本已支持从 `.env` 读取。
- 未扩展到 watchlist 外样本。
- 未写 `tracker.db`，未修改生产路径。
- qfq 拉取被 Tushare `adj_factor` 限频阻塞，当前结论保持 `NEED_QFQ`。

---

## 11. Plan readiness self-audit

- [x] minimum_landing_change 已定义：先新增只读脚本骨架。
- [x] 不写生产 DB：使用 SQLite read-only URI 与 query-only 约束。
- [x] 不触发 Telegram/cron：明确禁止导入和调用相关路径。
- [x] qfq 外部数据调用需要单独确认。
- [x] 指标窗口预取 120 个交易日，避免回测起点窗口系统性不可用。
- [x] qfq 派生需要交易日级别对齐校验，失败时禁止 pass_strong。
- [x] IS/OOS 时间切分必须写入报告。
- [x] v1/v2 对比、去重冷却、交易成本均有明确产物。
- [x] rollback 命令明确。
- [x] 独立 review gate 明确。
