# Market Data Provider Recovery Runbook

创建时间：2026-06-09

## 状态定义

- `SOURCE_DISABLED`：行情 provider 未启用，不是停牌或临时无行情。
- `AUTH_MISSING`：缺少 `TUSHARE_TOKEN` 或 token 无法认证。
- `degraded`：主源 Tushare 失败后，fallback 源返回了可用数据。

## 恢复 daily / outcome cron 前置条件

必须全部满足：

1. `.env` 配置 `TUSHARE_TOKEN`。
2. `python3 scripts/probe_tushare_market_data.py` 输出 `PASS`。
3. 最近的 `docs/reviews/*-tushare-capability-probe.md` 不含 failed 行。
4. 最近的 probe report 包含 `Close cross-check: PASS`；若为 `MANUAL_REQUIRED`，需要先补齐本地参考行情或人工对账后再更新 probe 证据。
5. `python3 scripts/check_market_data_readiness.py` 返回 `READY`。

## 恢复步骤

```bash
source .venv/bin/activate
python3 scripts/probe_tushare_market_data.py
python3 scripts/check_market_data_readiness.py
python3 pipeline.py market-data-backfill --start 2025-01-01 --end 2026-06-09
python3 pipeline.py accuracy-report
sqlite3 tracker.db "SELECT error_code, COUNT(*) FROM market_data_audit WHERE error_code='SOURCE_DISABLED' GROUP BY error_code;"
bash cron-setup.sh
```

## BaoStock 边界

- BaoStock fallback 只在 Tushare 主源失败后以 `degraded` 形式使用。
- `MARKET_DATA_ALLOW_BAOSTOCK_ONLY=1` 只允许 `market-data-backfill` 使用 BaoStock-only。
- BaoStock-only 不允许 `daily` 写入新 `predictions`。
- 若要把 BaoStock-only 用于生产写入，必须另行授权，并至少连续 5 个交易日与 Tushare 或人工行情页面对账。

## 停止条件

任一条件出现时停止 daily/outcome 生产写入：

- `scripts/check_market_data_readiness.py` 返回 `NOT_READY`。
- `price_at_score` 覆盖率连续两个交易日低于 95%。
- L3 覆盖率低于 90%。
- 任一 L3 窗口出现 mixed source / mixed adjustment / unknown volume unit。
- BaoStock fallback 与 Tushare 同日 close 偏差超过 0.5%，且样本超过 3 只。
