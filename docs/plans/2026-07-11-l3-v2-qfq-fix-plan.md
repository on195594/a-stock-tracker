# L3 v2 qfq 覆盖修复方案设计 spec

日期：2026-07-11  
状态：draft / implementation-ready  
范围：只修复 L3 v2 离线回测的 qfq 数据获取与文件缓存，不改变信号规则或决策门槛

## 1. 问题陈述

`scripts/offline_l3_v2_backtest.py --allow-tushare-fetch` 当前在每只股票的循环中依次调用 `pro.daily(...)` 和 `pro.adj_factor(...)`。38 只股票之间没有等待或配额协调，而当前 Tushare `adj_factor` 权限只允许 **1 次/分钟**，接口返回“频率超限(1次/分钟)”。脚本把异常分类为 `TUSHARE_RATE_LIMIT`，最终报告为：

- qfq panels：`0/38`；
- qfq reason counts：`{"TUSHARE_RATE_LIMIT": 38}`；
- Framework A buy_strong rows：`766`；
- buy_strong qfq issue rows：`766`；
- Decision：`NEED_QFQ`。

token 已能从项目 `.env` 读取，根因不是 token 缺失。由于 v2 `pass_strong` 必须使用 qfq panel，none-adjusted 结果只能作为 weak/reject 诊断，不能用于解锁 `GO_TDD`。

## 2. 目标与非目标

### 2.1 目标

1. 对 `adj_factor` 实施显式的单进程、跨重启限速，保证相邻请求开始时间至少间隔 61 秒。
2. 将 `daily + adj_factor` 的可复算原始数据保存到 `data/qfq_cache/`，按股票断点续跑。
3. 让离线回测优先读取文件缓存，在不访问外部 API 的情况下形成 38 只股票的 qfq panels。
4. 重跑报告，要求 buy_strong qfq issue 降为 0，再由现有 gate 判断是否满足 `GO_TDD`。

### 2.2 非目标和硬边界

- 不向 `tracker.db` 写入任何行情、复权因子、checkpoint 或状态；不得新增 SQLite cache。`scripts/offline_l3_v2_backtest.py` 必须继续通过 `file:...?...mode=ro` 打开 `tracker.db`，并保持 `PRAGMA query_only=ON` 校验。
- 不修改 `weights.json`，不修改 Framework B，不改 L1/L2 分值、L3 v2 规则或 Framework B 的 report-only 状态。
- 不修改 `decide()` 的 `GO_TDD / NEED_QFQ / NEED_SPEC_FIX / STOP` gate 逻辑，不以 none-adjusted 数据绕过 qfq gate。
- 不使用快速循环、紧密重试或并行调用 `adj_factor`。收到 rate limit 或其他远端错误时，本轮记录失败并停止或继续到下一个安全步骤；不在循环中立即重试同一请求。
- collector 不触发 Telegram、cron、Gemini 或 Sheets。

## 3. 推荐架构

把“慢速、可恢复的外部采集”和“可重复、只读的离线回测”拆开：

1. `scripts/collect_l3_v2_qfq_cache.py` 负责从 Tushare 采集，执行限速，并在每只股票成功后原子写入文件缓存。
2. `lib/l3_v2_qfq_cache.py` 提供缓存校验、原子读写、qfq 派生和 `adj_factor` rate limiter，供 collector 与离线回测共用。
3. `scripts/offline_l3_v2_backtest.py` 新增 `--qfq-cache-dir`，从文件加载 panel；推荐的报告重跑使用 `--no-external-fetch`，保证回测阶段无网络请求。

现有 `--allow-tushare-fetch` 不再作为全量报告的推荐入口。兼容期内若保留直接 fetch，`adj_factor` 调用也必须走同一个 limiter，不能保留当前无限速 call site；不得通过修改 gate 来掩盖 fetch 失败。

## 4. `adj_factor` 限速策略

### 4.1 固定间隔 limiter

首版采用可审计的 `time.sleep` 固定间隔，而不是复杂的多 token bucket：

```text
MIN_ADJ_FACTOR_INTERVAL_SECONDS = 61.0

acquire():
    elapsed = monotonic_now - last_request_started_monotonic
    wait = max(0, 61.0 - elapsed)
    if wait > 0:
        time.sleep(wait)
    actual_start = time.monotonic()       ← sleep 后重新采样，记录实际请求发送时间
    原子写入跨重启 rate state（使用 actual_start 对应的 UTC 时间）
    last_request_started_monotonic = actual_start
```

规则：

- limiter 只包围 `pro.adj_factor(...)`；`pro.daily(...)` 不消费该接口的 1 次/分钟额度。
- 第一个 `adj_factor` 请求可立即发送；之后相邻请求开始时间至少间隔 61 秒，留 1 秒时钟和服务端窗口余量。
- 使用 `time.monotonic()` 计算当前进程等待，避免系统时间回拨；同时将最近一次请求开始的 UTC 时间原子写入 `data/qfq_cache/.adj_factor_rate_state.json`，重启后按 wall-clock 补足剩余等待。
- collector 启动后持有 `data/qfq_cache/.collector.lock` 的独占文件锁，禁止两个进程同时消耗同一 token 的额度。
- rate state 必须在发出请求前写入，避免请求已到达服务端但进程崩溃后立即重发。
- 捕获 `TUSHARE_RATE_LIMIT` 时不 tight-loop retry；输出股票代码和可恢复提示后以非零状态结束。本次已经成功原子落盘的股票保留，下次运行从缓存继续。

如果未来改成 token bucket，容量必须为 1、refill period 必须不短于 61 秒，且仍需持久化最近一次 token 消耗时间；行为不得比上述固定间隔更激进。

## 5. 文件缓存设计

### 5.1 目录、格式与 key

缓存根目录固定为 `data/qfq_cache/`，可由 CLI 显式覆盖用于测试。每个股票代码是独立 key：

```text
data/qfq_cache/
  000001.csv
  000001.meta.json
  600900.csv
  600900.meta.json
  .adj_factor_rate_state.json
  .collector.lock
```

选择 UTF-8 CSV，避免为了 Parquet 新增 `pyarrow` 运行时依赖。文件名只接受 `^[0-9]{6}$` 股票代码，Tushare suffix 在请求时由既有 `to_tushare_code()` 规则派生，不能把用户输入直接拼接为路径。

### 5.2 CSV 数据契约

`{code}.csv` 保存可复算 qfq 的合并原始行，而不是只保存一次性派生结果：

```text
code,ts_code,trade_date,open,high,low,close,vol,adj_factor,source,fetched_at
```

约束：

- `trade_date` 为 `YYYYMMDD`，升序且唯一；
- `daily` 与 `adj_factor` 日期集合必须完全一致；
- OHLC、`vol`、`adj_factor` 必须可解析为数值，`adj_factor > 0`；
- `source` 固定为 `tushare.daily+adj_factor`；
- 文件内只能包含一个 code。

保存原始 OHLCV 和 factor 的原因是 qfq 基准依赖请求窗口内最新交易日的 factor。回测读取时先按 `[preload_start, end]` 截取，再以该窗口最新交易日的 `adj_factor_latest` 计算：

```text
price_qfq_t = price_raw_t * adj_factor_t / adj_factor_latest
volume_qfq_t = volume_raw_t / (adj_factor_t / adj_factor_latest)
```

这样当报告的 `end` 延后时，可用扩展后的原始缓存重新派生整个窗口，避免复用旧基准造成价格序列不一致。

### 5.3 metadata 与原子性

`{code}.meta.json` 至少包含：

- `schema_version`；
- `code`、`ts_code`；
- `requested_start`、`requested_end`；
- `min_trade_date`、`max_trade_date`、`row_count`；
- `source`、`fetched_at`；
- `status: "complete"`；
- CSV 的 SHA-256。

写入顺序为：同目录临时 CSV → flush/fsync → `os.replace` 正式 CSV → 临时 metadata → flush/fsync → `os.replace` 正式 metadata。只有 metadata 为 `complete` 且 checksum、schema 和内容校验全部通过时，缓存才有效；崩溃留下的临时文件不算 checkpoint。

缓存是隔离的离线文件，不是生产行情事实源，也绝不回写 `tracker.db`。

## 6. 断点续跑与缓存命中

collector 按排序后的 code 逐只执行以下逻辑：

1. 查找 `{code}.csv` 和 `{code}.meta.json`。
2. 两个文件都存在时，校验 schema、checksum、code、唯一日期、daily/factor 对齐，以及 metadata 的 `requested_start <= 本次 preload_start`、`requested_end >= 本次 end`。
3. 校验通过则打印 `SKIP cached complete: {code}`，不调用 `daily`，也不调用 `adj_factor`。
4. 文件缺失、损坏或覆盖窗口不足时，将该 code 视为 pending；对它重新获取完整目标窗口，成功后原子替换该股票的两个文件。
5. 单股在 `daily`、`adj_factor`、对齐或写盘阶段失败时，不生成 `complete` metadata。已完成股票仍可在下一次运行时被跳过。

不需要在 `tracker.db` 维护 checkpoint。每只股票的有效 CSV + `status=complete` metadata 就是 checkpoint；`.adj_factor_rate_state.json` 只负责配额安全，不代表数据完成。

推荐 collector CLI：

```bash
python3 scripts/collect_l3_v2_qfq_cache.py \
  --db tracker.db \
  --start 2025-01-01 \
  --end 2026-07-08 \
  --preload-trading-days 120 \
  --cache-dir data/qfq_cache
```

collector 只从 `tracker.db` 的只读连接取得 code 集合和有效 preload start；DB 仍以 `file:...?...mode=ro` + `PRAGMA query_only=ON` 打开。也可提供 `--codes` 进行人工分批，但所有批次共享同一 lock 和持久化 limiter state。

## 7. 离线回测集成

为 `scripts/offline_l3_v2_backtest.py` 增加：

```text
--qfq-cache-dir data/qfq_cache
```

加载流程：

1. 仍先通过现有 `open_readonly_db()` 打开 `tracker.db`，不得改变 `file:...?mode=ro` 和 `query_only` 校验。
2. 根据 predictions 得到 38 个 code，并为每个 code 读取、校验缓存。
3. 在内存中按本次 `[preload_start, end]` 截取原始行，调用共享 qfq 派生函数形成 `PricePanel(adjusted="qfq")`。
4. 缓存缺失或损坏时生成明确的 `QFQ_CACHE_MISSING` / `QFQ_CACHE_INVALID:*` contract；不得悄悄将 none panel 标成 qfq。
5. `--no-external-fetch` 只禁止网络，不禁止读取文件缓存。因此报告重跑应同时使用 `--qfq-cache-dir` 和 `--no-external-fetch`，确保结果可复现。

推荐报告重跑命令：

```bash
python3 scripts/offline_l3_v2_backtest.py \
  --db tracker.db \
  --start 2025-01-01 \
  --end 2026-07-08 \
  --output docs/reviews/2026-07-08-l3-v2-backtest-report.md \
  --artifacts-dir docs/reviews/l3-v2-backtest-artifacts \
  --qfq-cache-dir data/qfq_cache \
  --no-external-fetch \
  --preload-trading-days 120
```

## 8. 实施步骤

1. **创建 `lib/l3_v2_qfq_cache.py`**：实现 CSV/metadata 数据契约、原子读写、checksum、文件锁、固定间隔 rate limiter，以及从 raw daily + factor 派生 qfq OHLCV 的纯函数。
2. **创建 `scripts/collect_l3_v2_qfq_cache.py`**：只读打开 `tracker.db` 获取 code/preload 范围，读取 `.env` token，逐股命中检查、限速调用 `daily + adj_factor`、对齐校验和原子 checkpoint；禁止立即重试。
3. **修改 `scripts/offline_l3_v2_backtest.py`**：增加 `--qfq-cache-dir`，优先加载文件 cache；确保任何保留的 `--allow-tushare-fetch` `adj_factor` call site 都使用共享 limiter；保持 DB URI `mode=ro` 和 `PRAGMA query_only=ON`；不改变 `decide()`。
4. **创建 `tests/test_l3_v2_qfq_cache.py`**：覆盖缓存契约、qfq 价量计算、损坏缓存、窗口不足、路径校验、原子写入和 rate limiter。
5. **创建 `tests/test_collect_l3_v2_qfq_cache.py`**：覆盖已完成 code 跳过、部分完成后续跑、远端异常不写 complete metadata、跨重启 rate state 和单实例 lock。
6. **修改 `tests/test_offline_l3_v2_backtest.py`**：覆盖 `--qfq-cache-dir + --no-external-fetch` 能加载 qfq、缺失 cache 保持 `NEED_QFQ`、DB 仍为 read-only，以及 gate 逻辑未变化。
7. **生成 `data/qfq_cache/` 运行时缓存**：执行 collector 约 38–40 分钟；该目录是离线数据产物，应按仓库现有数据治理决定是否 `.gitignore`，不得把 token、lock、临时文件或 rate state 提交进 Git。
8. **重跑并更新 `docs/reviews/2026-07-08-l3-v2-backtest-report.md` 与 `docs/reviews/l3-v2-backtest-artifacts/`**，随后做独立 AGY/Codex 复审。只有现有 gate 自身输出 `GO_TDD` 才可进入下一阶段；本计划不授权生产 TDD 或上线。

## 9. 测试策略

所有自动化测试禁止访问真实 Tushare API，使用 `pytest` 的 `monkeypatch`/mock 和 `tmp_path`：

### 9.1 rate limiter

- mock `time.monotonic()`、wall clock 和 `time.sleep()`；验证首个请求不等待、第二个请求等待到 61 秒边界、已超过边界不等待。
- 在 `tmp_path` 写入 rate state，构造“进程重启”，验证新实例会补足剩余等待。
- mock sleep 只记录参数，不真实等待；断言没有零间隔循环或自动 retry。
- 模拟 lock 已被占用，断言第二个 collector fail fast，不发送 API 请求。

### 9.2 cache 与 resume

- 用小型 fake DataFrame 构造 daily 和 adj_factor，写入 `tmp_path/qfq_cache` 后读回，断言 schema、checksum、日期顺序和 qfq OHLCV/反向 volume 计算。
- 预置两个 complete code、一个缺失 code，mock Tushare client；断言只为缺失 code 各调用一次 `daily` 和 `adj_factor`。
- 模拟 checksum 错误、重复日期、factor date mismatch、zero factor 和覆盖窗口不足，断言 cache 被拒绝并给出稳定 reason。
- 在写 metadata 前注入异常，断言没有有效 complete checkpoint；下一次运行会重新获取该 code。
- mock `adj_factor` 抛出 rate limit，断言不立即重试、不写 complete metadata，并保留之前成功 code。

### 9.3 backtest 集成

- 用 `tmp_path` 建最小只读 SQLite fixture 和 2 个有效 cache 文件，mock/禁止网络 client；运行 `--qfq-cache-dir ... --no-external-fetch`，断言 qfq panels 来自文件且数据库 `query_only=1`。
- 构造覆盖全部 buy_strong code 的 cache，断言 `buy_strong_qfq_issue_count == 0`。
- 构造缺一个 buy_strong code 的 cache，断言 decision 仍为 `NEED_QFQ`，证明没有改动或绕过 gate。

## 10. 验收与 GO_TDD 判定

### 10.1 qfq 覆盖修复验收

对与 2026-07-10 报告相同的 score window 和 38 只股票，报告必须同时满足：

- `qfq panels: 38`，且每个 panel 通过 daily/factor 日期对齐、正 factor 和 OHLCV 检查；
- `qfq reason counts` 只含 `OK: 38`；
- `buy_strong qfq issue rows: 0`；
- 766 个 buy_strong rows 全部满足 `v2_adjusted == "qfq"`、`alignment_reason is None`、`unavailable_reason is None`；
- report/artifacts 由 `--qfq-cache-dir ... --no-external-fetch` 可重复生成；
- `tracker.db` 在运行前后 hash 不变，运行时 `SQLite query_only: True`。

其中 **`buy_strong qfq issue rows == 0` 是解除 `NEED_QFQ` 覆盖阻塞的精确条件**。它只解锁现有 gate 的后续评估，不单独保证 `GO_TDD`。

### 10.2 不改变的现有 GO_TDD gate

必须由 `scripts/offline_l3_v2_backtest.py` 现有 `decide()` 自然输出 `Decision: GO_TDD`。除上述 qfq issue 为 0 外，仍必须同时满足：

1. buy_strong v2 `pass_strong` 样本数大于 0；
2. dedup 20d 的 v2 pass_strong `settled_n >= 20`；
3. OOS dedup buy_strong 的 v2 pass_strong `n > 0`；
4. v2 pass_strong `avg_net_alpha_30d` 严格大于 v1 pass；
5. v2 pass_strong `hit_rate` 严格大于 v1 pass；
6. 报告和 artifacts 经独立复审通过。

若 qfq 覆盖为 38/38 且 issue 为 0，但上述任何指标条件不满足，必须接受现有 gate 给出的 `NEED_SPEC_FIX`，不得改 gate 或宣称已解锁生产化。`GO_TDD` 也只代表允许讨论测试驱动实现，不是生产发布授权。

## 11. 时间估算

38 只股票、`adj_factor` 1 req/min、相邻请求使用 61 秒间隔时：第一个请求立即执行，之后需要 `37 × 61 = 2257` 秒，即约 **37 分 37 秒**的纯等待。加上 38 次 `daily`、38 次 `adj_factor` 的网络时间、校验和落盘，完整首次采集预计 **38–40 分钟**。

缓存命中后的重复回测不调用 Tushare，预计只需本地文件读取和既有回测计算时间。中断后的续跑只为未完成或窗口不足的股票消耗额度；例如剩余 10 只约需 9 分 9 秒纯等待，加网络开销约 10–12 分钟。

## 12. 风险与回滚

- **窗口扩展风险**：qfq 基准会随最新 factor 改变。通过保存 raw daily + factor 并在读取时重新派生解决，不拼接旧的派生 qfq 值。
- **部分文件风险**：通过临时文件、fsync、`os.replace` 和 complete metadata 避免半文件被当作缓存命中。
- **多进程超限风险**：通过 collector lock 和持久化 rate state 防止并发及重启后立即请求。
- **缓存污染风险**：任何 schema、checksum、日期对齐或 factor 校验失败都 fail closed，回测保持 `NEED_QFQ`。
- **回滚**：删除或移走 `data/qfq_cache/` 并不影响 `tracker.db`；不传 `--qfq-cache-dir` 即回到原离线行为。gate、weights 和 Framework B 无需回滚，因为本方案不修改它们。
