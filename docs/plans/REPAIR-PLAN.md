> [!NOTE]
> ✅ **已完成归档（2026-07-07）**：本计划中的所有修复任务已于 2026-05 执行完毕并验证通过。
> 归档原因：工程审查确认所有任务已落地，文档从根目录移入 docs/plans/ 目录。

# a-stock-tracker 修复计划 v2.0（Codex 评审后修订）

**创建时间**: 2026-05-15
**Codex 评审**: 完成（215k tokens，发现 4 个 P0）
**修订摘要**:
- P1（日度变化）核心逻辑重写：改用"当日价格÷BPS→实时PB→历史分位"，真正产生日度变化
- 修复 TTL 冲突、as_completed 崩溃、任务顺序错误
- gross_margin 加数据验证

---

## 根因确认

| 字段 | 问题 |
|------|------|
| `gross_margin` | 全量 NULL，fetcher source='web'，从不从 AKShare 获取 |
| `pb_percentile_10y` | 仅 weekly 更新；且 Baidu API 是月度数据，每日刷新无意义 |

**正确解法**：
- gross_margin → 新浪利润表 `(营业收入-营业成本)/营业收入`
- pb_percentile_10y → **weekly 存储 BPS + 月度历史序列**，daily 用当日收盘价计算实时 PB 分位（无需额外 API 调用）

---

## 架构决策（替代原计划的并发刷新方案）

```
weekly fetch（每周六）:
  [新增] 存储 bps（每股净资产）
  [新增] 存储 pb_hist_monthly（月度PB序列，约731个float）
  [修改] _fetch_pb_hist_and_percentile() 同时返回分位 + 序列

cmd_daily（每工作日）:
  [新增] 对每只股票：current_pb = 今日收盘价 / bps
         daily_pb_percentile = rank(current_pb in pb_hist_monthly)
         注入 data["pb_percentile_10y"] 后再评分
  → 价格每天变 → current_pb 每天变 → 分位每天变 → 评分每天变
```

优点：
- ✅ 无额外 API 调用（daily 不调 Baidu）
- ✅ 无 TTL 冲突（in-memory 计算，不写 stock_fundamentals）
- ✅ 无 ThreadPoolExecutor（不需要并发）
- ✅ 真正日度敏感

---

## 任务分解（已修正顺序）

### T1：修改 lib/fetcher.py — 三项新增

**文件**: `lib/fetcher.py`

#### 修改 1：FIELDS 字典新增两个字段

```python
# 在 FIELDS 末尾添加：
'bps':             ('每股净资产(元)',           'akshare'),
'pb_hist_monthly': ('PB月度历史序列(内部)',      'computed'),
```

#### 修改 2：重写 `_fetch_pb_percentile` → `_fetch_pb_hist_and_percentile`

```python
def _fetch_pb_hist_and_percentile(code: str) -> tuple[float | None, list[float] | None]:
    """调用 Baidu 估值接口，同时返回 (当前分位%, 月度历史序列)。

    分位基于最新月度 PB（非实时价），序列供 cmd_daily 计算实时分位用。
    序列约 731 个 float，序列化后约 6-8KB/股。
    """
    result = timed_call(
        ak.stock_zh_valuation_baidu,
        code,
        timeout=PB_TIMEOUT,
        indicator="市净率",
        period="近十年",
    )
    if isinstance(result, (str, tuple)) or result is None:
        return None, None
    df = result
    assert "value" in df.columns, f"stock_zh_valuation_baidu 列名变更，期望含 'value'，实际：{df.columns.tolist()}"
    values = df["value"].dropna()
    if len(values) < 12:
        return None, None
    series = [round(float(v), 4) for v in values]
    current_pb = series[-1]
    pct = round(float((values < current_pb).sum() / len(values) * 100), 1)
    return pct, series
```

#### 修改 3：在 `cmd_fetch()` Step 5 中使用新函数

将原 Step 5 的 `_fetch_pb_percentile(code)` 调用替换为：

```python
# ── Step 5：PB 历史10年分位 + 月度序列（Baidu，允许30s）──
print("  [5/7] PB 历史分位 + 月度序列...", flush=True)
pct, series = _fetch_pb_hist_and_percentile(code)
if pct is not None:
    results["pb_percentile_10y"] = pct
    results["pb_hist_monthly"] = series
    print(f"  ✅ PB历史10年分位={pct}%，序列 {len(series)} 个数据点")
else:
    null_reasons["pb_percentile_10y"] = "PB历史数据不足或接口失败"
    null_reasons["pb_hist_monthly"] = "同上"
    logger.warning("  ⚠️ PB历史分位获取失败")
```

#### 修改 4：在 Step 2（财务数据）中存储 BPS

在已有的 `results['debt_ratio'] = ...` 之后添加：

```python
bps = parse_float(fin_df["每股净资产"].iloc[-1])
if bps is not None:
    results["bps"] = bps
else:
    null_reasons["bps"] = "每股净资产数据缺失"
```

（注：`bps` 变量已被计算，仅需写入 `results`）

#### 修改 5：新增 gross_margin 获取（Step 4.5）

在 FIELDS 后添加辅助集合与函数：

```python
_FINANCIAL_INDUSTRY_SKIP = frozenset({"银行", "保险", "证券", "信托", "期货", "多元金融", "非银金融", "券商"})


def _compute_gross_margin(code: str, industry: str) -> float | None:
    """从新浪利润表计算近3年年报平均毛利率（金融行业返回 None）。

    金融行业的营业收入≠传统产品收入，毛利率无意义。
    建筑/钢铁/资源等非金融行业均正常计算（低毛利是行业特征，不跳过）。
    """
    if any(kw in industry for kw in _FINANCIAL_INDUSTRY_SKIP):
        return None
    prefix = "sh" if code.startswith("6") else "sz"
    result = timed_call(
        ak.stock_financial_report_sina,
        stock=f"{prefix}{code}",
        symbol="利润表",
        timeout=API_TIMEOUT,
    )
    if isinstance(result, (str, tuple)) or result is None:
        return None
    df = result
    required_cols = {"报告日", "营业收入", "营业成本"}
    if not required_cols.issubset(df.columns):
        return None
    # 只取年报（报告日以 1231 结尾），最近3年
    annual = df[df["报告日"].astype(str).str.endswith("1231")].head(3)
    rev = pd.to_numeric(annual["营业收入"], errors="coerce")
    cos = pd.to_numeric(annual["营业成本"], errors="coerce")
    # 过滤：营业收入必须为正
    valid_mask = rev > 0
    rev_v, cos_v = rev[valid_mask], cos[valid_mask]
    if len(rev_v) < 2:  # 至少需要2年数据
        return None
    gm_series = (rev_v - cos_v) / rev_v * 100
    result_val = round(float(gm_series.mean()), 2)
    # 合理性校验（-20% 到 95% 之间）
    if not (-20 <= result_val <= 95):
        logger.warning(f"  ⚠️ {code} 毛利率 {result_val}% 超出合理范围，置 None")
        return None
    return result_val
```

在 cmd_fetch() Step 4（股息率）之后，Step 5（PB）之前插入 Step 4.5：

```python
# ── Step 4.5：毛利率（新浪利润表，近3年年报均值）──
print("  [4.5/7] 计算毛利率（新浪利润表）...", flush=True)
import pandas as pd  # 在函数顶部已有，确认 import

gm = _compute_gross_margin(code, industry)
if gm is not None:
    results["gross_margin"] = gm
    print(f"  ✅ 毛利率={gm}%")
else:
    null_reasons["gross_margin"] = "金融行业跳过或接口失败或数据不足"
    logger.warning("  ⚠️ 毛利率 获取失败")
```

同时将 FIELDS 中 `gross_margin` source 改为 `'computed'`：

```python
'gross_margin': ('毛利率(%)', 'computed'),
```

更新步骤标签：Step 1-6 改为 Step 1-7（`[1/7]` 至 `[7/7]`，其中 4.5 单独显示）。

---

### T2：修改 pipeline.py — 日度 PB 分位计算注入

**文件**: `pipeline.py`

#### 修改 1：新增辅助函数 `_compute_daily_pb_percentile`

```python
def _compute_daily_pb_percentile(price: float, data: dict) -> float | None:
    """用当日收盘价 + 缓存的每股净资产/历史序列，计算实时 PB 历史分位。

    只做内存计算，不写数据库，无 TTL 问题。
    bps <= 0 或历史序列不足 12 个点时返回 None（保留旧值）。
    """
    bps = data.get("bps")
    hist = data.get("pb_hist_monthly")
    if not bps or bps <= 0 or not hist or len(hist) < 12:
        return None
    current_pb = price / bps
    pct = sum(1 for x in hist if float(x) < current_pb) / len(hist) * 100
    return round(pct, 1)
```

#### 修改 2：在 `cmd_daily()` 评分循环中注入日度分位

在已有代码 `data = dict(fundamentals.get("data", fundamentals))` 之后，
`qual = get_qualitative_score(code, name)` 之前，添加：

```python
# 注入日度实时 PB 分位（股价变化→分位变化→评分每日变化）
if price_at_score:
    daily_pct = _compute_daily_pb_percentile(price_at_score, data)
    if daily_pct is not None:
        data["pb_percentile_10y"] = daily_pct
        logger.debug(f"  {code} 实时PB分位={daily_pct}%（价={price_at_score}, bps={data.get('bps')}）")
    else:
        logger.debug(f"  {code} 无法计算实时PB分位（bps/hist缺失），使用缓存值")
```

无需修改 `_refresh_pb_percentiles` 函数（该函数从计划中移除）。

---

### T3：更新测试

**文件**: `tests/test_pipeline.py` 和 `tests/test_scorer.py`

新增测试用例（最少覆盖以下场景）：

```python
# test_fetcher.py（新建或添加到 test_pipeline.py）


def test_compute_gross_margin_financial_skip():
    """银行行业应返回 None"""
    result = _compute_gross_margin("600036", "银行")
    assert result is None


def test_compute_gross_margin_missing_columns(monkeypatch):
    """利润表缺列时返回 None"""
    import pandas as pd

    monkeypatch.setattr(
        "akshare.stock_financial_report_sina", lambda **kw: pd.DataFrame({"报告日": ["20251231"], "营业收入": [100]})
    )
    result = _compute_gross_margin("603606", "制造")
    assert result is None


def test_compute_gross_margin_insufficient_data(monkeypatch):
    """少于2年数据时返回 None"""
    import pandas as pd

    df = pd.DataFrame({"报告日": ["20251231"], "营业收入": [100.0], "营业成本": [80.0]})
    monkeypatch.setattr("akshare.stock_financial_report_sina", lambda **kw: df)
    result = _compute_gross_margin("603606", "制造")
    assert result is None


def test_compute_gross_margin_zero_revenue(monkeypatch):
    """零营业收入行应被跳过"""
    import pandas as pd

    df = pd.DataFrame(
        {
            "报告日": ["20251231", "20241231", "20231231"],
            "营业收入": [0.0, 100.0, 120.0],
            "营业成本": [0.0, 80.0, 90.0],
        }
    )
    monkeypatch.setattr("akshare.stock_financial_report_sina", lambda **kw: df)
    result = _compute_gross_margin("603606", "制造")
    assert result is not None  # 应用2条有效数据计算


def test_compute_daily_pb_percentile_normal():
    """正常情况：价格变化导致分位变化"""
    data = {"bps": 10.0, "pb_hist_monthly": [1.0] * 50 + [2.0] * 50}
    # price=25 → pb=2.5 → 高于全部100个点 → 100%
    assert _compute_daily_pb_percentile(25.0, data) == 100.0
    # price=5 → pb=0.5 → 低于全部 → 0%
    assert _compute_daily_pb_percentile(5.0, data) == 0.0


def test_compute_daily_pb_percentile_missing_bps():
    """缺 bps 时返回 None"""
    data = {"pb_hist_monthly": [1.0] * 50}
    assert _compute_daily_pb_percentile(25.0, data) is None


def test_compute_daily_pb_percentile_insufficient_hist():
    """序列不足12个点返回 None"""
    data = {"bps": 10.0, "pb_hist_monthly": [1.0] * 5}
    assert _compute_daily_pb_percentile(25.0, data) is None


def test_daily_pb_percentile_injected_before_scoring(monkeypatch, tmp_path):
    """cmd_daily 中 pb_percentile_10y 被实时值覆盖后传给 score_stock"""
    # ... mock get_fundamentals 返回 bps=10, pb_hist_monthly=[1.0]*100, pb_percentile_10y=50
    # mock snapshot_data = {code: 15.0}  → pb=1.5 → 约50%分位
    # 验证 score_stock 接收到的 data["pb_percentile_10y"] 是计算值而非缓存值
    pass  # 实现时完善
```

**验收标准**: `pytest tests/ -v` 全部通过，新增用例 ≥ 7 个。

---

### T4：weekly 全量刷新（在 T3 通过后执行）

```bash
cd /home/lin/a-stock-tracker
source .venv/bin/activate
python pipeline.py weekly
```

**验收**:
```sql
-- 非金融股 gross_margin 填充率 > 60%
SELECT COUNT(*) FROM stock_fundamentals
WHERE json_extract(data,'$.gross_margin') IS NOT NULL;
-- 期望 > 20

-- BPS 和历史序列已存储
SELECT code, json_extract(data,'$.bps'),
       json_array_length(json_extract(data,'$.pb_hist_monthly'))
FROM stock_fundamentals LIMIT 5;
-- 期望 bps 非 NULL，序列长度约 731
```

---

### T5：验证日度评分变化

触发方式：直接在命令行运行两次 daily（不同日期数据），或修改 snapshot_data mock。

**真实验证方法**（当日运行）：

```bash
# 1. 查今日评分
sqlite3 tracker.db "SELECT code, total_score, quant_score FROM predictions WHERE score_date=date('now') ORDER BY total_score DESC LIMIT 10;"

# 2. 查 pb_percentile_10y 是否有实时值（若 bps 存在，值应与 weekly 值不同）
sqlite3 tracker.db "SELECT code, json_extract(data,'$.bps'), json_extract(data,'$.pb_percentile_10y') FROM stock_fundamentals LIMIT 5;"

# 3. 对比：明日运行后，avg(total_score) 应与今日不同（若股价变动）
```

---

### T6：更新 CLAUDE.md

删除旧警告：
```
gross_margin 对所有 38 只股票均为 NULL
```

替换为：
```
gross_margin 从 2026-05-15 起使用新浪利润表计算（金融行业仍为 NULL）
pb_percentile_10y 从 2026-05-15 起改为日度实时计算：daily 用当日收盘价÷BPS÷历史序列
BPS 和 pb_hist_monthly 新增至 FIELDS，由 weekly 填充
```

---

## 执行顺序

```
T1（修改 fetcher.py）
  ↓
T2（修改 pipeline.py）
  ↓
T3（写/跑测试，必须全部 PASS 才继续）
  ↓
T4（weekly 刷新生产数据）
  ↓
T5（验证日度评分变化）
  ↓
T6（更新 CLAUDE.md）
```

---

## 回滚方案（已修订）

```bash
# 执行前创建回滚点（不影响用户其他工作）
cd /home/lin/a-stock-tracker
git add lib/fetcher.py pipeline.py tests/
git stash push -m "repair-plan-v2-rollback" lib/fetcher.py pipeline.py tests/

# 若需回滚
git stash pop
# 注意：T4 weekly 写入数据库后无法通过 git 回滚
# 若需回滚数据：sqlite3 tracker.db "DELETE FROM stock_fundamentals WHERE updated_at > '2026-05-15';"
```

---

## 验收总表

| 检查项 | 验证命令 | 通过标准 |
|--------|---------|---------|
| gross_margin 填充 | `SELECT COUNT(*) FROM stock_fundamentals WHERE json_extract(data,'$.gross_margin') IS NOT NULL` | > 20 |
| BPS 填充 | `SELECT COUNT(*) FROM stock_fundamentals WHERE json_extract(data,'$.bps') IS NOT NULL` | > 30 |
| pb_hist_monthly 填充 | `SELECT AVG(json_array_length(json_extract(data,'$.pb_hist_monthly'))) FROM stock_fundamentals` | > 700 |
| 测试全部通过 | `pytest tests/ -v` | 0 failures |
| 日度评分有差异 | 对比两天评分 | avg(total_score) 不同 |

## 约束（不变）

- **不修改 weights.json** — 不触发 weights_hash 变更
- **不修改 lib/cache.py** — TTL 逻辑不动
- **不修改 db_executor.py** — 生产安全红线
- **不使用 ThreadPoolExecutor 在 daily** — 无需并发，纯内存计算
