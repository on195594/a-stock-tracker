I will start by listing the directory structure of the repository `/home/lin/a-stock-tracker` to understand the layout of the project, including where tests, quantitative pipelines, and notebooks/scripts reside. This will help tailor a realistic and precise review and implementation plan.
I will list the contents of the `lib` directory to examine the other modules available, which will show how data is structured and if any indicator calculations or helpers can be reused for the L3 v2 rules.
I will read `lib/entry_signal.py` to understand the full context of how signals are defined, structured, and what imports or data types (e.g. `EntrySignalResult`) it uses.
I will perform a grep search to find references to `compute_entry_signal` and check how entry signals are used across the pipeline, database, reports, and Telegram push code. This is crucial for evaluating how changing the returned structure to support a multi-level signal (`pass_strong`, `pass_weak`, etc.) affects other modules.
I will view lines 180 to 220 of `pipeline.py` to examine how the pipeline invokes `compute_entry_signal`, how it maps the `EntrySignalResult` properties (`signal`, `version`, `reason`, `status`) to database records or telegram logic, and if it expects binary/boolean or string values.
I will search for references to `entry_signal` in `pipeline.py` to see how the result is persisted in the database and where database schemas or queries are defined. This will reveal if fields like `entry_signal` are defined as INTEGER or TEXT, and how status fields are managed.
I will perform a grep search on `telegram_push.py` to see how the Telegram notification module filters and displays results based on `entry_signal` and `entry_signal_status`. This will clarify how the system handles the transition from binary values to multi-level signals in notifications.
I will view `telegram_push.py` from line 60 to 160 to understand exactly how the stocks are selected for the Telegram push and how the message layout behaves based on `entry_signal == 1` and other flags. This will help us structure the v2 transition plan correctly.
I will search for `predictions` table definitions to understand the database schema and columns related to entry signals. This will help determine how we can store the multi-level v2 signal.
I will view `lib/cache.py` from line 60 to 120 to see the exact structure of the `predictions` table, specifically how `entry_signal`, `entry_signal_status`, and `entry_signal_reason` are defined. This details whether the DB schema needs to change for v2.
I will read `tests/test_l3_entry_signal.py` to inspect the test assertions, mocks, and how the test suite expects `compute_entry_signal` to behave. This is important for understanding our test coverage and validating the v2 plan.
I will view `CLAUDE.md` to see instructions on how tests are run in this codebase, which will guide us in specifying the testing phase of our plan.
I will perform a grep search in the `docs` folder for "L3" and "entry_signal" to find existing documents describing the L3 Buy Signal system. This is to understand the historical context, previous implementation details, and constraints.
I will view `docs/specs/2026-05-30-phase5-l3-entry-signal-spec.md` to understand the original specification of L3 v1, including assumptions, inputs, performance metrics, and testing criteria. This provides the baseline for proposing modifications in the v2 spec.
I will read the first 200 lines of `docs/plans/2026-05-30-phase5-l3-entry-signal-implementation-plan.md` to see the details of how L3 v1 was implemented, which will help us design the v2 migration plan with proper backwards compatibility and minimum friction.
I will view the `docs/reviews/2026-07-08-agy-l3-v2-review-prompt.md` file, which is likely the review prompt template or context file, to see if there is any other specific detail or context provided there.
I will view the first 300 lines of `docs/lessons-learned.md` to understand any historical pitfalls and lessons learned, especially regarding data sources, caching, and model versioning. This is to ensure the review respects historical fixes.
I will search for the index data caching and fetching mechanisms (e.g. `index_prices` table or code) in the codebase. This is to analyze whether the infrastructure already supports retrieving historical index data (like CSI 300) in daily runs or if adding market-wide indicators introduces major engineering risks and API dependencies.
I will view `pipeline.py` from line 130 to 180 to understand how daily bars are retrieved and whether price adjustment (复权) is applied before passing them to the entry signal logic. This is critical for assessing how dividend distributions or stock splits might cause false signals.
I will perform a grep search for `load_daily_bars` to see where it is defined. This will let us check if the loaded daily bars are adjusted (hfq/qfq) or unadjusted (none).
I will view lines 240 to 290 of `lib/cache.py` to examine the implementation of `load_daily_bars`. This will tell us what adjustment types are supported (e.g. hfq, qfq, none) and how they are handled.
I will search the codebase for references to `adjusted` and price types (like `qfq`, `hfq`, or `none`) to see if the database or market data providers already support adjusted prices, and if using unadjusted prices has been identified as a risk before.
I will view `lib/market_data.py` to check the price adjustment behavior during daily data fetching, which will confirm if we store adjusted prices in the database or if we only fetch unadjusted ones.
I will run a read-only query on the local `tracker.db` SQLite database to check the value of the `adjusted` column in the `daily_bars` table. This will confirm whether we are storing adjusted or unadjusted price data.
I will query the `predictions` table in the database for `600900` (长江电力) to see its recent scores and entry signal results. This will provide empirical evidence of how the L3 v1 rules behaved on specific dates.
I will query the daily prices and volumes of `600900` (长江电力) from the `daily_bars` table between 2026-06-15 and 2026-07-08 to see how the stock price and moving averages behaved, and to verify if any sharp price drop (such as due to dividends) occurred.
I will check if there are multiple adjusted types or sources for `600900` in the `daily_bars` table. This will clarify why only a few rows were returned.
I will query the maximum date and count of records in `daily_bars` for `600900` to find out what historical data is actually stored in the DB.
I will query the daily prices of `600900` starting from `2026-06-15` without an upper bound constraint to see if there are gaps in the daily bars cache.
I will count how many daily bars exist for `600900` after `2026-06-15` to see if there is data for late June and early July.
I will query the latest 5 rows in descending order starting from `2026-06-15` to see the actual values for early July (including July 8).
I will inspect the implementation of `TushareMarketDataProvider.fetch_l3_bars` from the `a_stock_lib` package to see if it supports price adjustments (复权) and how it is implemented under the hood. This will clarify how we can retrieve adjusted prices (such as `qfq`) in v2.
I will run the inspection command using the project's virtual environment python (`.venv/bin/python3`) to avoid the `ModuleNotFoundError` for `a_stock_lib`.
I will inspect the implementation of `TushareMarketDataProvider._fetch_daily` to see how it calls Tushare, what fields it requests, and whether it supports adjustment or if it only requests unadjusted daily data.
I will inspect the implementation of `IsolatedBaoStockMarketDataProvider` to see how it handles daily bars fetching and if it also defaults to unadjusted prices. This will confirm whether both providers in the composite setup only fetch unadjusted prices.
I will check the status of the background task inspecting the BaoStock provider.
I will query the daily prices of `600900` between `2026-06-22` and `2026-07-02` to see if there are any gaps or sudden price drops.
Based on a thorough read-only review of the codebase (including [lib/entry_signal.py](file:///home/lin/a-stock-tracker/lib/entry_signal.py), [pipeline.py](file:///home/lin/a-stock-tracker/pipeline.py), [telegram_push.py](file:///home/lin/a-stock-tracker/telegram_push.py), and [lib/cache.py](file:///home/lin/a-stock-tracker/lib/cache.py)), I have evaluated the Hermes L3 v2 buy signal proposal. 

Here is the independent code and quantitative rule review report.

---

# AGY L3 v2 Buy Signal Quantitative & Engineering Review Report

## 1. Verdict
**REQUEST_CHANGES**
*理由：* 目前不能直接修改生产代码。Hermes 提出的 v2 方向在**量能强度、斜率和均线偏离**上有合理的优化倾向，但在**价格复权数据源、压力位判定逻辑的未来函数（look-ahead bias）隐患、以及大盘过滤的数据稳定性**上存在严重的量化与工程设计缺陷。必须先编写完整的 Spec 并进行离线回测，验证数据质量和统计去重后，再行代码落地。

---

## 2. Blocking findings (阻塞性发现)

### 🔴 Finding 1: 缺少价格复权（Ex-Dividend Distortion）导致指标严重失真与追高买入
* **证据：** 数据库中 `daily_bars.adjusted` 字段目前全部为 `'none'`（即**不复权价格**）。[lib/cache.py](file:///home/lin/a-stock-tracker/lib/cache.py#L252) 的 `load_daily_bars` 也默认以 `adjusted="none"` 读取数据。
* **量化风险：** A 股高股息蓝筹股（如长江电力 `600900`）在每年 6-7 月进行分红派息（除权除息）。除权日股价会跳空低开（例如从 28.5 元降至 27.5 元）。
  * 若使用不复权数据，历史价格未向下修正，导致 MA60/MA120 在除权后 60-120 天内被**高估**。
  * 股价在除权后会**错误地触发 `BELOW_MA60` 拒绝信号**，而这往往是低吸的良机。
  * 当股价重新站上被扭曲的 MA60 时，股价通常已经历了大幅反弹、接近短期高点（如 2026-07-08 长江电力接近 20 日高点 `28.29` 才重新触发 `PASS`）。这与“低吸”初衷完全违背，变成了**被动追高**。
* **最小修复：** 必须修改行情获取与存储逻辑，支持获取 `'qfq'`（前复权）价格，或在计算前获取除权因子（`adj_factor`）并在内存中对 `daily_bars` 的 `close` 进行复权修正。

### 🔴 Finding 2: 压力位与突破判定存在“未来函数（Look-Ahead Bias）”或二义性定义
* **证据：** Hermes 建议：“接近 20/60 日高点，降级或标记 weak；若突破平台且量能足够，可 strong”。
* **设计隐患：** 如果计算 `20d_high` 时包含了“今天”的收盘价，那么在突破日，最新的 `close` 必然等于 `20d_high`。此时无法从数学上区分是“正处于突破中”还是“正接近前高（受阻）”。
* **最小修复：** 必须严格区分当前收盘价与**历史窗口（不含今日）**的相对关系：
  * 定义 `high_20d_prev = close.iloc[-21:-1].max()` (前 20 个交易日最高价，排除今日)。
  * **突破强买点 (pass_strong)：** `latest_close >= high_20d_prev` 且 `vol5 >= vol20 * 1.15`。
  * **接近压力位 (pass_weak)：** `latest_close < high_20d_prev` 且 `latest_close >= high_20d_prev * 0.98`（距离前高不足 2% 但未突破），若此时量能不足，存在双头受阻风险，必须降级为 `pass_weak`。

### 🔴 Finding 3: 大盘过滤（CSI 300）引发全局不可计算（Data Feed Reliability Risk）
* **证据：** 历史上东方财富指数接口不稳定，常返回空值（见 [lessons-learned.md](file:///home/lin/a-stock-tracker/docs/lessons-learned.md#L144)）。
* **工程风险：** 若将“大盘过滤”硬编码在个股的 `compute_entry_signal` 纯函数内，一旦指数数据获取失败，会导致**当日所有个股的 L3 信号因大盘数据缺失而全局降级/失效**（返回 `unavailable`）。
* **最小修复：**
  * `compute_entry_signal` 应维持个股纯函数，不直接查询数据库的 `index_prices` 表。
  * 应该在 [pipeline.py](file:///home/lin/a-stock-tracker/pipeline.py) 中每日仅计算一次大盘状态（例如：沪深 300 是否站上 MA20/MA60），然后将布尔值 `market_is_bullish: bool` 作为参数传入个股计算函数。
  * 当大盘数据因接口抖动缺失时，`market_is_bullish` 必须**默认 fallback 为 `True`（不阻断）**，保证个股逻辑的稳定性。

---

## 3. Important notes (重要建议)

### 🔸 Note 1: 30d 后验统计中的样本高度自相关（Consecutive Triggering Bias）
* **量化坑：** 观察到已结案 30d 样本中“长江电力”等股票在 6 月初连续数天均被判定为 `pass`。在目前的统计口径下，这被当作了 51 个独立样本。
* **分析：** 由于趋势和量能信号有持续性，一旦触发，通常会连续保持 5-10 个交易日。将这些连续交易日简单作为独立交易样本统计，会**严重失真**（如一次错误的连续买入信号会在分母里累加 10 次，导致命中率计算受单一极端交易影响极大）。
* **建议：** 回测与统计时必须引入**去重与冷却期机制**：
  1. **首发触发原则**：只在信号由 `0/NULL -> 1` 转变的当天计入买入样本。
  2. **持仓期冷却**：一旦某股触发入场，在接下来的 20 个交易日（约 1 个自然月）内，忽略其连续触发的信号。

### 🔸 Note 2: 均线偏离阈值（5%）对不同 Beta 股票的适配性问题
* **分析：** `close / MA60 - 1 > 5%` 的硬性阈值对于低波动防御股（如长江电力）是极高的偏离度，但对于高 Beta 股票（如科技成长股），5% 极其容易在突破初期就被动触发，从而错失主升浪。
* **建议：** 偏离度应考虑引入波动率自适应（如以 `2 * ATR` 或 $1.5 \times \text{std}(close, 60)$ 代替固定 5%），或至少在 Spec 阶段对 35 只 watchlist 股票进行分行业偏离度回测。

---

## 4. Safety boundary assessment (安全边界评估)

* **是否涉及敏感操作：** 本次 L3 v2 评估仅限逻辑和离线设计，不涉及任何 Credentials（API 凭证修改）、Cron 调度器的系统级改动或自动交易下单。
* **必须禁止的副作用：**
  * ❌ 禁止在未完成离线回测验证前直接修改 `lib/entry_signal.py` 主干代码。
  * ❌ 禁止在 `daily` cron 任务中跑未经验证的 v2 规则。
  * ❌ 禁止通过批量 `UPDATE` predictions 表历史数据来“强行对齐”v2 字段，这会破坏 L1/L2 定量定性评分的对照实验。
  * ❌ 禁止向测试/开发库以外的任何生产库（`tracker.db`）写入未经全面 TDD 测试的脏数据。

---

## 5. 修复计划 (Repair Plan)

### 阶段一：Spec 编写与架构设计（不改代码）
1. **Spec 存储位置：** 新建 `docs/specs/2026-07-08-l3-v2-entry-signal-spec.md`。
2. **数据模型升级方案（零 DDL 变更）：**
   * 复用 `predictions` 表中现有的 `entry_signal` (INTEGER) 和 `entry_signal_status` (TEXT) 字段。
   * `entry_signal` 映射定义：
     * `1`：`pass_strong`（**强买点通过**，保持对旧 Telegram 筛选条件的完全向后兼容）。
     * `2`：`pass_weak`（**弱买点通过**，如量能仅略高于均量或处于平台受阻位）。
     * `0`：`reject`（拒绝）。
     * `NULL`：`unavailable`（窗口不足或数据过期）。
   * `entry_signal_version` 统一写入 `'v2'`。
   * `entry_signal_status` 写入对应的字符串（`'pass_strong'`, `'pass_weak'`, `'reject'`）。
   * 通过这种定义，[telegram_push.py](file:///home/lin/a-stock-tracker/telegram_push.py#L99) 中的原始查询 `p.entry_signal = 1` 会**自然且安全地只推送 v2 强买点**，无需改动推送 SQL 即可上线！

### 阶段二：离线回测验证（Python 脚本/Notebook 离线运行）
1. 编写离线脚本 `scripts/offline_l3_v2_backtest.py`。
2. 抓取 `qfq`（前复权）历史 daily bars 覆盖 2025-01-01 至 2026-07-08。
3. 实现 v2 核心逻辑，并应用**冷却期去重算法**（20日冷却）。
4. 对比 L3 v1 (n=51) vs L3 v2 (strong/weak) 的结案 Alpha 及胜率。
5. 生成回测报告，作为 Phase 6 重启/演进的决策依据。

### 阶段三：TDD 代码编写与测试覆盖
1. 在 [tests/test_l3_entry_signal.py](file:///home/lin/a-stock-tracker/tests/test_l3_entry_signal.py) 中新增 v2 测试：
   * 覆盖前复权测试数据。
   * 覆盖“未破 20d 高点受阻”降级为 weak 的断言。
   * 覆盖“突破 20d 高点 + 放量”保持 strong 的断言。
   * 覆盖“大盘低于均线”时强信号降级为 weak 的断言。
2. 修改 [lib/entry_signal.py](file:///home/lin/a-stock-tracker/lib/entry_signal.py) 使得测试全部通过（Green）。

### 阶段四：Telegram 推送文案改造
* 在 [telegram_push.py](file:///home/lin/a-stock-tracker/telegram_push.py) 中，弱买点股票将自然落入 `backup` (🟡 候补) 部分。
* 修改 `_format_stock_line`，若是 v2 weak 信号，标签显示为 `等待L3` 或 `L3弱信号`；如果是 v2 strong 信号，显示 `✓ L3强买点(v2)`。

### 阶段五：回滚路径
* 若 v2 上线后发现信号质量退化或稳定性问题，直接在 `lib/entry_signal.py` 中将 `ENTRY_SIGNAL_VERSION` 切换回 `'v1'`，且把 `compute_entry_signal` 路由指向旧版的纯函数实现。

---

## 6. Recommended next step (建议的下一步)

> [!IMPORTANT]
> **请批准在 `/home/lin/a-stock-tracker/docs/specs/` 下新建 `2026-07-08-l3-v2-entry-signal-spec.md` 规格说明书。**
> 我将把前复权（QFQ）数据契约、大盘状态入参、冷却期回测口径以及 v2 状态映射逻辑整理为正式 Spec，供您确认后，再启动离线回测验证。
