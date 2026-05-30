# Phase 5 L3 买点层实施计划

**日期：** 2026-05-30
**状态：** ready-for-execution
**Spec：** `docs/specs/2026-05-30-phase5-l3-entry-signal-spec.md`
**执行原则：** strict TDD，小步提交，所有 AKShare/Telegram 调用必须 mock。

---

## 0. 请求改写与执行边界

用户请求：将“L3 买点层优先、Framework B 后置”的结论写入项目文档，并确认允许新增 `predictions` 字段、改 Telegram 推送、增加日线历史窗口读取、把 L3 report 纳入 `accuracy-report`。

执行边界：

- 本计划允许后续实现上述四类项目内副作用。
- 本计划不允许自动交易、真实下单、改写历史 DB、隐式启用 cron、真实外部 API 测试。
- 本计划不复活 Framework B，只为其保留后置入口。

---

## 1. Phase Ledger

| Phase | 状态 | 目标 |
|---|---|---|
| P0 | done | 文档化 L3 优先结论与授权边界 |
| P1 | not_started | DB schema 与迁移测试 |
| P2 | not_started | L3 v1 纯计算 seam |
| P3 | not_started | daily 写入 L3 字段 |
| P4 | not_started | Telegram 推送接入 L3 过滤 |
| P5 | not_started | accuracy-report 增加 L3 section |
| P6 | not_started | 文档、回归、独立审查与提交 |

---

## 2. P1 — DB schema 与迁移测试

### 目标

让 `predictions` 支持：

- `entry_signal INTEGER NULL`
- `entry_signal_version TEXT NULL`

### 最小落地改动

- 测试：`tests/test_pipeline.py` 或新建 `tests/test_l3_entry_signal.py`
- 代码：`lib/cache.py`

### TDD 步骤

1. RED：新增测试，创建新 DB 后检查 `PRAGMA table_info(predictions)` 包含两个字段。
2. RED：构造旧 schema DB，调用 `get_db()` 后检查自动补列。
3. GREEN：在建表 DDL 中加入字段，并补充旧库迁移逻辑；对 `predictions` 只允许 `ALTER TABLE ADD COLUMN`，禁止 `DROP TABLE` / 重建表。
4. REFACTOR：抽出 schema ensure helper（如已有迁移模式则沿用）。

### 验证命令

```bash
pytest tests/test_pipeline.py -q
pytest tests/ -q
```

### Rollback

```bash
git restore lib/cache.py tests/test_pipeline.py tests/test_l3_entry_signal.py
```

### Stop 条件

- 迁移需要改写历史行。
- 迁移破坏 `alpha_*d` generated column。

---

## 3. P2 — L3 v1 纯计算 seam

### 目标

实现纯函数，输入日线数据，输出 L3 判断结果。

### 建议接口

可新建 `lib/entry_signal.py`：

```python
from dataclasses import dataclass

ENTRY_SIGNAL_VERSION = "v1"

@dataclass(frozen=True)
class EntrySignalResult:
    signal: int | None
    version: str | None
    reason: str

# reason 使用固定常量，便于报告聚合：
# PASS / BELOW_MA60 / BELOW_MA120 / LOW_VOLUME / INSUFFICIENT_DATA / MISSING_COLUMNS


def compute_entry_signal(daily_bars) -> EntrySignalResult:
    ...
```

### 规则

- 输入 schema 固定为归一化后的 `date/close/volume`，不在纯函数内处理 AKShare 中文列名。
- `close > MA60`
- `close > MA120`
- `volume_5d_avg > volume_20d_avg`
- 三项采用 AND 语义：三项必须全部满足才 `signal=1, version="v1"`。
- 任一条件不满足：`signal=0, version="v1"`。
- 不足 120 个交易日或缺列：`signal=None, version="v1"`，不得写 `version=None`。

### TDD 步骤

1. RED：通过、跌破 MA60、只过 MA60 未过 MA120、量能不足、窗口不足、缺列六类测试。
2. GREEN：实现最小 pandas/stdlib 计算逻辑。
3. REFACTOR：让返回 reason 稳定，便于报告解释。

### 验证命令

```bash
pytest tests/test_l3_entry_signal.py -q
pytest tests/ -q
```

### Rollback

```bash
git restore lib/entry_signal.py tests/test_l3_entry_signal.py
```

---

## 4. P3 — daily 写入 L3 字段

### 目标

`cmd_daily()` 在写入 predictions 时同时写入 L3 字段。

### 最小落地改动

- `pipeline.py`
- `tests/test_pipeline.py`

### 设计

- 数据读取：使用 `ak.stock_zh_a_hist(symbol=code, period="daily", start_date=..., end_date=..., adjust="")` 增加 120+ 交易日日线窗口读取。
- 归一化：pipeline 边界层把 AKShare `日期/收盘/成交量` 归一化为 P2 纯函数需要的 `date/close/volume`。
- 计算：调用 P2 的纯函数。
- 写入：INSERT 包含 `entry_signal`、`entry_signal_version`。
- 失败：L3 不可计算时写 `entry_signal=NULL, entry_signal_version="v1"`，不得阻断基础评分。

### TDD 步骤

1. RED：mock 日线历史足够且通过 L3，daily 后 DB 行 `entry_signal=1`。
2. RED：mock 日线不足，daily 后 DB 行 `entry_signal IS NULL AND entry_signal_version='v1'`。
3. RED：确认旧历史行不会被 UPDATE。
4. GREEN：接入读取、计算、INSERT。

### 验证命令

```bash
pytest tests/test_pipeline.py -q
pytest tests/ -q
```

### Stop 条件

- daily 因 L3 数据失败而跳过本可评分股票。
- 测试发起真实 AKShare 请求。

---

## 5. P4 — Telegram 推送接入 L3 过滤

### 目标

推送条件升级为：

```text
total_score >= buy_strong AND entry_signal = 1
```

### 最小落地改动

- `telegram_push.py`
- `tests/test_telegram_push.py` 或现有 pipeline 推送测试

### TDD 步骤

1. RED：`score >= buy_strong` 且 `entry_signal=1` → 调用发送。
2. RED：`score >= buy_strong` 但 `entry_signal=0` → 不发送。
3. RED：`score >= buy_strong` 但 `entry_signal=NULL` → 不发送。
4. RED：发送异常不抛出到 daily。
5. GREEN：更新过滤条件与文本。

### 验证命令

```bash
pytest tests/test_telegram_push.py -q
pytest tests/test_pipeline.py -q
pytest tests/ -q
```

### Stop 条件

- 无法用 mock 证明不发真实 Telegram。
- 推送逻辑把 NULL 当作通过。

---

## 6. P5 — accuracy-report 增加 L3 section

### 目标

`accuracy-report` 纳入 L3 report。

### 最小落地改动

- `pipeline.py`
- `tests/test_pipeline.py`

### 报告要求

标题固定包含：

```text
L3 买点层
```

最小统计：

- v1 记录数。
- `entry_signal=1/0/NULL` 数量，并区分 `NULL/NULL` 与 `NULL/v1`。
- strong 候选中 L3 通过/拒绝数量。
- L3 30d 已结案样本不足提示。
- L3 30d 命中率：分母固定为 `framework='A' AND entry_signal=1 AND entry_signal_version='v1' AND outcome_30d IS NOT NULL`；命中固定为 `alpha_30d > 0`。

### TDD 步骤

1. RED：空表也显示 L3 section。
2. RED：`NULL/NULL`、`NULL/v1` 与 `0/v1` 分开统计。
3. RED：strong 候选中通过/拒绝数量正确。
4. RED：L3 30d 命中率使用 `alpha_30d > 0`，且只统计 Framework A 的 `entry_signal=1/v1` 已结案样本。
5. RED：L3 30d 样本 `<30` 时显示样本不足。
6. GREEN：新增 SQL helper 与报告渲染。

### 验证命令

```bash
pytest tests/test_pipeline.py -q
pytest tests/ -q
python3 pipeline.py accuracy-report
```

运行真实 `accuracy-report` 后检查：

```bash
git status --short
```

若仅更新 tracked `accuracy_report.txt`，按本次任务目标决定是否提交；不要把非目标内容混入。

---

## 7. P6 — 文档、回归与审查

### 目标

收尾验证，确保 spec、README、roadmap、实现一致。

### 最小落地改动

- 更新 `README.md` 当前状态与 L3 说明。
- 更新 `docs/evolution-roadmap.md` Phase 5 状态。
- 可选：保存独立只读审查到 `docs/reviews/`。

### 验证命令

```bash
pytest tests/ -q
pytest tests/test_spec_structure.py -q
git diff --check
git status --short
```

### 提交建议

分阶段提交：

1. `docs: 记录 L3 买点层决策与计划`
2. `feat: 新增 L3 entry signal schema`
3. `feat: 实现 L3 entry signal 计算`
4. `feat: daily 写入 L3 entry signal`
5. `feat: Telegram 推送接入 L3 过滤`
6. `feat: accuracy-report 增加 L3 报告`

---

## 8. 执行顺序建议

不要一次性大改。推荐先执行 P1 + P2，形成可验证核心，再接 P3/P4/P5。

每个 Phase 完成后都必须：

```bash
pytest tests/ -q
git diff --check
git status --short
```

若出现非目标 tracked 文件变更，先解释来源，再决定提交或恢复。
