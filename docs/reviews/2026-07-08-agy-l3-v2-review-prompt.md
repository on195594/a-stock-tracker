# AGY Read-only Review Prompt — L3 v2 买点规则评估

你是 a-stock-tracker 项目的独立工程/量化规则 reviewer。请只读评估，不要修改任何文件，不要创建文件，不要运行会写入 DB、日志、报告、缓存或外部服务的命令。

## 项目边界

- 仓库：`/home/lin/a-stock-tracker`
- 项目定位：A 股 watchlist 评分验证项目，不自动交易、不下单、不维护真实账户。
- 当前 L3 v1 用于 Telegram 推送过滤：`total_score >= 44 AND entry_signal = 1`。
- 用户问题：现有 L3 v1 买点规则是否合理；Hermes 初步建议 v2 增加强弱分层、量能阈值、趋势斜率、距离均线/压力位、大盘过滤。请你评估该 v2 方向并给出修复计划。

## 当前 L3 v1 规则源码快照

`lib/entry_signal.py` 核心逻辑：

```python
ENTRY_SIGNAL_VERSION = "v1"

def compute_entry_signal(daily_bars: Any) -> EntrySignalResult:
    required_columns = {"date", "close", "volume"}
    columns = set(getattr(daily_bars, "columns", []))
    if not required_columns.issubset(columns):
        missing = required_columns - columns
        reason = REASON_MISSING_VOLUME if "volume" in missing else REASON_MISSING_COLUMNS
        return EntrySignalResult(None, ENTRY_SIGNAL_VERSION, reason, "insufficient")

    bars = daily_bars.sort_values("date") if "date" in columns else daily_bars
    if len(bars) < 120:
        return EntrySignalResult(None, ENTRY_SIGNAL_VERSION, REASON_INSUFFICIENT_DATA, "insufficient")

    close = bars["close"].astype(float)
    volume = bars["volume"].astype(float)
    latest_close = float(close.iloc[-1])
    ma60 = float(close.tail(60).mean())
    ma120 = float(close.tail(120).mean())
    volume_5d_avg = float(volume.tail(5).mean())
    volume_20d_avg = float(volume.tail(20).mean())

    if latest_close <= ma60:
        return EntrySignalResult(0, ENTRY_SIGNAL_VERSION, REASON_BELOW_MA60, "reject")
    if latest_close <= ma120:
        return EntrySignalResult(0, ENTRY_SIGNAL_VERSION, REASON_BELOW_MA120, "reject")
    if volume_5d_avg <= volume_20d_avg:
        return EntrySignalResult(0, ENTRY_SIGNAL_VERSION, REASON_LOW_VOLUME, "reject")
    return EntrySignalResult(1, ENTRY_SIGNAL_VERSION, REASON_PASS, "pass")
```

Spec 原始要求：

```text
entry_signal=1 当且仅当：
1. 最新收盘价 close > MA60
2. 最新收盘价 close > MA120
3. volume_5d_avg > volume_20d_avg
```

## Hermes 已观察到的问题证据

### 1. 2026-07-08 长江电力触发情况

```json
{
  "code": "600900",
  "name": "长江电力",
  "score_date": "2026-07-08",
  "total_score": 55.9,
  "quant_score": 38.9,
  "entry_signal_status": "pass",
  "entry_signal_reason": "PASS"
}
```

K 线组件：

```json
{
  "latest_date": "2026-07-08",
  "latest_close": 27.83,
  "ma60": 27.0562,
  "ma120": 26.8982,
  "vol5": 1254006.812,
  "vol20": 1242142.2705,
  "vol5_gt_vol20": true,
  "price_gt_ma60": true,
  "price_gt_ma120": true,
  "20d_high": 28.29,
  "20d_low": 26.15
}
```

Interpretation：通过条件成立，但量能只比 20 日均量高约 1%，且价格接近 20 日高点，不像高赔率低吸买点。

### 2. 已结案 30d 后验观察（样本含连续日期重复，不能简单当独立样本）

```text
L3 pass 全部已结案：n=51, avg_ret=-9.71%, avg_bench=-0.37%, avg_alpha=-9.34%, hit_rate=3.9%
总分>=44 且 L3 pass：n=27, avg_score=49.20, avg_ret=-13.57%, avg_bench=-0.42%, avg_alpha=-13.14%, hit_rate=0.0%
```

最近高分 pass 已结案按日：

```text
2026-06-08 n=4 avg_alpha=-11.80 hit_rate=0.0 codes=002475,601088,601138,601225
2026-06-05 n=4 avg_alpha=-17.92 hit_rate=0.0 codes=002475,601088,601138,601225
2026-06-04 n=5 avg_alpha=-13.58 hit_rate=0.0 codes=002475,600900,601088,601138,601225
2026-06-03 n=5 avg_alpha=-11.89 hit_rate=0.0 codes=002475,600900,601088,601138,601225
2026-06-02 n=5 avg_alpha=-11.22 hit_rate=0.0 codes=002475,600900,601088,601138,601225
2026-06-01 n=4 avg_alpha=-13.12 hit_rate=0.0 codes=600900,601088,601138,601225
```

## Hermes 初步 v2 方向（待你审查）

保留 L3 v1 的趋势过滤，但改成分层输出：

- `pass_strong`
- `pass_weak`
- `reject`
- `insufficient/unavailable`

候选增强条件：

1. 趋势过滤：`close > MA60` 且 `close > MA120` 保留。
2. 趋势斜率：增加 `MA60 >= MA60_5days_ago`，避免买入下行均线假突破。
3. 量能强度：把 `vol5 > vol20` 改为强信号要求 `vol5 >= vol20 * 1.10`；低于这个但仍大于 vol20 可降为 weak。
4. 距离均线：若 `close / MA60 - 1 > 5%`，标记追高风险或降级。
5. 压力位：若接近 20/60 日高点，降级或标记 weak；若突破平台且量能足够，可 strong。
6. 大盘过滤：沪深300或中证/行业指数低于 MA20/MA60 时降级，不直接强推。
7. 高股息/除权：除权附近避免固定价位误判，单独 reason。

## 需要你输出

请用中文输出，结构固定：

1. `Verdict`: `PASS`, `PASS_WITH_NOTES`, 或 `REQUEST_CHANGES`。
2. `Blocking findings`: 如果 v2 方向有会导致误判/不可实现/污染回测的问题，请列出证据和最小修复。
3. `Important notes`: 非阻塞但应纳入计划的建议。
4. `Safety boundary assessment`: 是否涉及凭证、cron、Telegram、DB 重写、外部副作用、自动交易；哪些必须禁止。
5. `修复计划`: 给出按阶段执行的项目计划，必须包含：
   - 先写 spec 的位置和内容；
   - 数据模型是否需要新增字段，还是复用现有字段；
   - v2 规则最小可落地版本；
   - 回测/验证口径，如何处理连续日期重复样本；
   - 测试范围；
   - Telegram 推送文案如何避免把 weak 当强买点；
   - 回滚路径。
6. `Recommended next step`: 只给一个下一步。

请重点评估“v2 规则是否应该立即改代码”，还是先做 spec + 离线回测报告。不要泛泛讲投资理论。