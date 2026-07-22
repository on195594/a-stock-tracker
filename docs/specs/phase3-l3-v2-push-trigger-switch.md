# Phase 3 Engineering Spec：L3 v2 推送触发条件切换

历史路径说明：本文是实施记录，正文保留当时的 `lib/l3_v2_pipeline.py` 引用；当前实际路径为 `a_stock_tracker/signals/l3_v2_pipeline.py`，仓库已不存在 `lib/` 目录。

## 1. 背景与现状

### 1.1 当前推送触发

`push_daily_signals(score_date, threshold=44.0, radar_min=35.0)` 位于 `telegram_push.py:86`。当前“主推”查询从 `predictions` 读取 `entry_signal` 与 `l3_v2_signal`（`telegram_push.py:95-107`），其精确过滤条件为：

```sql
WHERE p.score_date=? AND p.total_score >= ? AND p.entry_signal = 1
```

该条件位于 `telegram_push.py:104`，因此现在决定高分股票能否进入“🟢 主推”的仍是 L3 v1 `entry_signal=1`。高分但 v1 未通过的股票由候补查询的互补条件 `(p.entry_signal IS NULL OR p.entry_signal != 1)` 选出（`telegram_push.py:108-121`）。函数最终会组装主推、候补和雷达三层日报，并且即使三层都为空也发送“今日无推荐信号”（`telegram_push.py:137-166`）。因此本文中的“抑制推送”精确定义为**不得进入“🟢 主推”触发集合**，而不是禁止发送整份 Telegram 日报。

daily 流程在数据库写入结束后用当日 `today` 调用 `push_daily_signals(today, buy_threshold)`（`pipeline.py:567-573`），所以生产路径查询的 `score_date` 是本次运行日期。

### 1.2 `l3_v2_signal` 当前写入路径

daily 对每只股票调用 `compute_l3_v2_from_daily_bars(db, code, today)`（`pipeline.py:465-468`）。新记录 INSERT 已包含 `l3_v2_signal`、版本、状态、原因和获取时间（`pipeline.py:487-507`）；同日同框架记录已存在时，UPDATE 也只刷新 v1/v2 信号审计字段，其中包括 `l3_v2_signal`（`pipeline.py:510-540`）。因此 Phase 3 不需要补建 v2 计算或写入路径。

wrapper 会优先选满 120 条的 QFQ 日线，否则回退未复权日线（`lib/l3_v2_pipeline.py:35-60`），并将结果委托给 v2 计算器；无日线或异常时返回 `signal=None/status=unavailable`（`lib/l3_v2_pipeline.py:97-111`）。Phase 2 的验收事实是满 120 条 QFQ 的非 FREEFALL 数据产生 `signal=1/status=pass_strong`（`docs/specs/phase2-qfq-daily-bars-collector.md:16-22`），路线图也记录了 35/35 个代码完成 QFQ 回填、`pass_strong` 激活以及 v2 从下一工作日起写入生产（`docs/evolution-roadmap.md:129-136`）。

### 1.3 shadow mode 历史与事实修正

Phase 2 于 2026-07-12 完成；此前 Phase 2 rollout 明确要求在 shadow mode 观察 `pass_strong`、`pass_weak` 或 `unavailable`，且不得改写历史 predictions（`docs/specs/phase2-qfq-daily-bars-collector.md:337-352`）。因此不能把“Phase 2 前所有记录的 `l3_v2_signal` 都是 NULL”当作代码事实：当前 wrapper 的未复权回退可以产生非 NULL 信号（`lib/l3_v2_pipeline.py:40-60, 103-108`），只有旧 schema/尚未运行 v2 的历史记录或计算不可用记录才可能为 NULL（无数据和异常路径见 `lib/l3_v2_pipeline.py:103-111`）。

Phase 2 后的有效状态域是 `pass_strong`、`pass_weak`、`reject`、`unavailable`：QFQ 与 fallback 状态见 `tests/test_l3_v2_pipeline_qfq.py:53-87`，FREEFALL reject 见 `tests/test_l3_v2_pipeline.py:123-134`，不可用分支见 `lib/l3_v2_pipeline.py:103-111`。其中推送门禁只读取三值 `l3_v2_signal`：通过态为 `1`，FREEFALL reject 为 `0`，不可用为 `NULL`。本阶段不依据状态字符串建立第二套过滤规则。

## 2. Phase 3 目标

1. 将高分股票进入“🟢 主推”的权威触发条件从 `p.entry_signal = 1` 切换为 `p.l3_v2_signal = 1`，同时保留 `p.total_score >= ?` 分数门槛。
2. `l3_v2_signal=0`（FREEFALL reject）即使 `total_score >= threshold` 也不得进入主推。
3. `l3_v2_signal IS NULL` 采用 fail-closed 语义，不得进入主推。
4. 主推与候补仍须互斥且完整：高分记录中，v2 为 `1` 的进入主推，v2 为 `0` 或 `NULL` 的进入候补。候补展示不等于触发主推；整份分层日报仍按现有行为发送（当前分层和发送路径见 `telegram_push.py:137-166`）。
5. 不改变 L1/L2 分数。路线图将 L3 定义为不修改 `total_score` 的过滤层（`docs/evolution-roadmap.md:89-96`）。

## 3. NULL 值迁移分析

### 3.1 两种方案

**方案 A：硬切换（推荐）**

主推条件直接使用 `p.l3_v2_signal = 1`。SQLite 对 `NULL = 1` 的判断不为真，因此 NULL 自动 fail-closed；候补使用显式互补条件 `(p.l3_v2_signal IS NULL OR p.l3_v2_signal != 1)`。优点是门禁单一、可审计，不会在同一天混用 v1/v2，也满足不可用信号不能触发主推的安全语义。

**方案 B：过渡回退**

当 `l3_v2_signal IS NULL` 时回退 `entry_signal=1`。该方案能让旧记录保持 v1 行为，但会让 `NULL` 同时表示“旧记录”“计算不可用”和“允许用 v1”，削弱 fail-closed，并使同一日报混合两代规则。路线图要求版本和空值分开统计以维持可比性（`docs/evolution-roadmap.md:92-96`），所以不推荐。

### 3.2 历史日期风险

若人工调用 `push_daily_signals()` 查询旧日期，硬切换会使该日 `l3_v2_signal=NULL` 的高分记录不进入主推，而进入候补。这是有意的 fail-closed 行为，不做历史回填，也不回退 v1。

生产 daily 以本次运行的 `today` 写入并随后查询同一个 `today`（v2 计算见 `pipeline.py:465-468`，推送调用见 `pipeline.py:567-573`），不会自动重推历史日期。因此，历史 NULL 对常规生产运行不是现实风险，只影响人工显式传入旧 `score_date` 的诊断/重放；该场景接受硬切换结果。

## 4. 变更范围

### 4.1 `telegram_push.py`

只修改 `push_daily_signals()` 内两个高分分层 SQL 条件，不改 SELECT 列顺序、JOIN、阈值、雷达查询或发送逻辑：

1. 主推查询 `telegram_push.py:95-107`：

   ```sql
   -- before
   WHERE p.score_date=? AND p.total_score >= ? AND p.entry_signal = 1

   -- after
   WHERE p.score_date=? AND p.total_score >= ? AND p.l3_v2_signal = 1
   ```

2. 候补查询 `telegram_push.py:108-121` 必须同步改成主推条件的 v2 互补集，避免 `entry_signal` 与 `l3_v2_signal` 不一致时发生遗漏或重复：

   ```sql
   -- before
   WHERE p.score_date=? AND p.total_score >= ?
     AND (p.entry_signal IS NULL OR p.entry_signal != 1)

   -- after
   WHERE p.score_date=? AND p.total_score >= ?
     AND (p.l3_v2_signal IS NULL OR p.l3_v2_signal != 1)
   ```

当前候补条件精确位于 `telegram_push.py:117-119`。雷达仅按 `[radar_min, threshold)` 分数区间查询（`telegram_push.py:122-134`），不属于高分主推门禁，本阶段不改。

### 4.2 `pipeline.py`

无需修改。v2 已在评分循环前计算（`pipeline.py:465-468`），并在 INSERT 与同日记录 UPDATE 两条路径写入（`pipeline.py:487-540`）；daily 随后用同一个日期发起 Telegram 查询（`pipeline.py:567-573`）。

### 4.3 测试

更新 `tests/test_telegram_push.py`。现有 helper 只接收并插入 `entry_signal`（`tests/test_telegram_push.py:31-47`），应新增 `l3_v2_signal` 参数并将该既有 schema 列写入测试记录；`predictions` 已定义 `entry_signal` 与 `l3_v2_signal` 两列（`lib/cache.py:68-102`）。调整当前依赖 v1 分层的用例（例如 `tests/test_telegram_push.py:62-87, 129-143`），让预期分层由 v2 值决定。

至少新增或改造成以下三个独立用例，并通过捕获 `_send` 文本检查“🟢 主推”集合：

- `entry_signal=0, l3_v2_signal=1, total_score>=threshold`：股票必须出现在主推，证明触发器已经切到 v2。
- `entry_signal=1, l3_v2_signal=0, total_score>=threshold`：股票不得出现在主推、应出现在候补，证明 FREEFALL 能覆盖高分和 v1 pass。
- `entry_signal=1, l3_v2_signal=NULL, total_score>=threshold`：股票不得出现在主推、应出现在候补，证明 NULL fail-closed。

保留并运行 wrapper 的既有回归测试：QFQ `pass_strong` 与未复权 `pass_weak` 已在 `tests/test_l3_v2_pipeline_qfq.py:53-87` 覆盖，FREEFALL `signal=0/status=reject/reason=FREEFALL` 已在 `tests/test_l3_v2_pipeline.py:123-134` 覆盖；这些文件无需为 SQL 切换修改。

## 5. 验收标准

### 5.1 可执行单元验收

实现后的测试必须直接证明：

1. `l3_v2_signal=1` 触发主推，即使 `entry_signal=0`。
2. `l3_v2_signal=0` 抑制主推，即使分数达标且 `entry_signal=1`。
3. `l3_v2_signal=NULL` 抑制主推，即使分数达标且 `entry_signal=1`。
4. 三种情况下股票只出现于一个高分分层，不重复、不丢失；这里“抑制推送”按第 1 节定义为抑制主推，候补日报仍可展示。

执行：

```bash
cd /home/lin/a-stock-tracker
.venv/bin/python -m pytest tests/test_telegram_push.py -q
```

预期：进程退出码为 `0`，末行包含 `passed` 且不包含 `failed`；上述三个门禁用例全部通过。

### 5.2 集成检查

SQL、临时 SQLite schema、分层组装和 `_send` 边界由 Telegram 测试文件共同覆盖；再与 v2 wrapper 测试联跑：

```bash
cd /home/lin/a-stock-tracker
.venv/bin/python -m pytest \
  tests/test_telegram_push.py \
  tests/test_l3_v2_pipeline.py \
  tests/test_l3_v2_pipeline_qfq.py -q
```

预期：退出码为 `0`，末行包含 `passed`、不包含 `failed`；输出文本断言证明 v2 pass 进入“🟢 主推”，v2 reject/NULL 不进入“🟢 主推”。该检查使用隔离数据库，不调用真实 Telegram；现有测试正是用 `tmp_path` 替换数据库并 monkeypatch `_send`（`tests/test_telegram_push.py:17-28, 62-69`）。

最后必须运行全量回归：

```bash
cd /home/lin/a-stock-tracker
.venv/bin/python -m pytest -q
```

验收结果：退出码 `0`，无 `failed` 或 `error`，全量 pytest 通过。

## 6. 安全红线

以下约束在实现、测试、部署和回滚中均不可突破。

1. 直接遵守 `CLAUDE.md:54`：**“禁止 UPDATE 已有 predictions 的 `total_score` / `weights_hash` — 破坏实验数据可比性”**。本阶段只改 SELECT 过滤条件，不回填或改写这两列。
2. 不修改 `daily_bars` 或 `predictions` schema。`predictions.l3_v2_signal` 已存在（`lib/cache.py:68-102`），`daily_bars` 现有结构已支持 QFQ/未复权共存（`lib/cache.py:117-137`），没有迁移需求。若未来真的新增字段，`CLAUDE.md:62` 要求同步更新 DDL 和 INSERT；本阶段不触发该要求。
3. 不从数据库删除 `entry_signal` 及其审计列。当前 schema 同时保留 v1 `entry_signal` 系列和 v2 `l3_v2_signal` 系列（`lib/cache.py:90-100`）；路线图要求信号版本化并区分旧记录和不可计算记录（`docs/evolution-roadmap.md:92-96`），因此 v1 必须留作历史结果对照。
4. 新增/调整测试必须使用隔离数据库，不得使用真实 `tracker.db`；这是 `CLAUDE.md:63` 的直接要求。现有 Telegram 测试 fixture 已用 `tmp_path` 隔离 DB（`tests/test_telegram_push.py:17-22`）。

## 7. 回滚策略

一行逻辑回滚：将 `telegram_push.py` 主推条件从 `p.l3_v2_signal = 1` 恢复为 `p.entry_signal = 1`，并将候补互补条件同步恢复为 `(p.entry_signal IS NULL OR p.entry_signal != 1)`；这会完整恢复当前 v1 分层行为（当前两条条件见 `telegram_push.py:104, 117-119`）。不删除 v2 数据、不改 schema、不改历史分数。

## 8. 后续 Phase（预留）

### Phase 3.1：accuracy-report 的 L3 v2 section

新增独立的 L3 v2 报告区，至少按 `l3_v2_signal` 的 `NULL/0/1` 展示信号分布，并按 `l3_v2_status` 区分 `pass_strong/pass_weak/reject/unavailable`；不得与既有 v1 统计混合。现有路线图已要求 L3 报告分开统计 NULL、0、1 且样本不足时不得下确定性结论（`docs/evolution-roadmap.md:94-96`），Phase 3.1 将该原则扩展到 v2。

### Phase 3.2：从推送代码退役 `entry_signal`

在 v1 积累足够已结案 outcomes、完成 v1/v2 可比性评估后，移除 Telegram 查询和文案对 `entry_signal` 的依赖；但数据库中的 v1 字段继续保留用于历史比较。当前格式化函数仍以 `entry_signal` 生成 v1 标签并附加 v2 标签（`telegram_push.py:62-80`），所以 Phase 3 只切换门禁，不提前执行展示层退役。
