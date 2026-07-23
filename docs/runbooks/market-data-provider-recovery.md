# Market Data Provider Recovery Runbook

创建时间：2026-06-09
最新状态：2026-07-21 起，tracker 运行时为 `a-stock-lib==0.4.1`，继续使用 Tushare/BaoStock 行情 provider；probe 的 BaoStock 参考源使用隔离版 provider 防止 SDK socket hang；`a_stock_tracker/data/market_data.py` 保留 tracker 环境门禁和 cache service。2026-07-15 capability report 当前已 stale，必须真实刷新后才能再次声明 `READY_CRON`。

## 状态定义

- `SOURCE_DISABLED`：行情 provider 未启用，不是停牌或临时无行情。
- `AUTH_MISSING`：缺少 `TUSHARE_TOKEN` 或 token 无法认证。
- `degraded`：主源 Tushare 失败后，fallback 源返回了可用数据。

## 恢复 daily / outcome 成组 cron 前置条件

必须全部满足：

1. `.env` 配置 `TUSHARE_TOKEN`。
2. `python3 scripts/probe_tushare_market_data.py` 生成当天的分层决策 report。
3. 最近的 `docs/reviews/*-tushare-capability-probe.md` 包含：
   - `Write Gate: PASS`
   - `Production Decision: DAILY_WRITES_ALLOWED`
   - `Capability Checks: PASS`
   - `Index/Calendar Dependent Jobs: ALLOWED`
   - `Close cross-check: PASS`
4. `python3 scripts/check_market_data_readiness.py --scope cron` 返回 `READY_CRON`；cron scope 会拒绝过期 report。

注意：`index_daily` / `trade_cal` 的非阻塞 failed 行不再直接代表 daily 写入不可恢复；必须看 report 的 `Write Gate` 和 `Production Decision`。但 `cron-setup.sh` 会同时恢复 `daily` 与 `outcome-update`，而 `outcome-update` 依赖沪深300指数价，因此成组 cron 恢复仍必须要求 `Index/Calendar Dependent Jobs: ALLOWED`。

若 `Close cross-check` 为 `MANUAL_REQUIRED`，需要先补齐本地参考行情或人工对账后再更新 probe 证据。

## 恢复步骤

```bash
source .venv/bin/activate
python3 scripts/probe_tushare_market_data.py
python3 scripts/check_market_data_readiness.py --scope cron
python3 pipeline.py market-data-backfill --start 2025-01-01 --end 2026-06-09
python3 pipeline.py accuracy-report
sqlite3 tracker.db "SELECT error_code, COUNT(*) FROM market_data_audit WHERE error_code='SOURCE_DISABLED' GROUP BY error_code;"
bash cron-setup.sh
```

## Staged daily 恢复

当最近 report 显示：

- `Write Gate: PASS`
- `Production Decision: DAILY_WRITES_ALLOWED`
- `Close cross-check: PASS`
- `Capability Checks: DEGRADED`
- `Index/Calendar Dependent Jobs: HOLD`

则可以把下面命令的结果作为 staged daily 恢复讨论证据：

```bash
python3 scripts/check_market_data_readiness.py --scope daily
```

若返回 `READY_DAILY`，仅说明股票日线写入门禁通过，不代表 `outcome-update` 或 index/calendar-dependent jobs 可以恢复。

在此状态下：

- 严禁执行 `bash cron-setup.sh` 恢复成组 cron。
- 严禁添加 `outcome-update` cron。
- 只有 PM 明确授权 staged daily 恢复后，才可手工添加单条 `daily` cron：
- staged daily 仍须遵循当前生产时序：先让 17:15 的 TuShare valuation cycle 完成，再于 17:30 启动 daily。

```bash
(crontab -l 2>/dev/null | grep -v "pipeline.py daily" || true; echo "30 17 * * 1-5 /home/lin/a-stock-tracker/cron-alert-wrap.sh \"cd /home/lin/a-stock-tracker && .venv/bin/python pipeline.py daily\" daily >> /home/lin/a-stock-tracker/logs/daily.log 2>&1") | crontab -
```

## BaoStock 边界

- BaoStock fallback 只在 Tushare 主源失败后以 `degraded` 形式使用。
- `MARKET_DATA_ALLOW_BAOSTOCK_ONLY=1` 只允许 `market-data-backfill` 使用 BaoStock-only。
- BaoStock-only 不允许 `daily` 写入新 `predictions`。
- 若要把 BaoStock-only 用于生产写入，必须另行授权，并至少连续 5 个交易日与 Tushare 或人工行情页面对账。

## 停止与恢复边界

以下条件分别用于阻止从零恢复或要求停止生产写入；`HOLD_CRON` 的 stale/unknown 保留边界按首条单独处理：

- `scripts/check_market_data_readiness.py --scope cron` 返回 `HOLD_CRON`：禁止从零恢复成组 `daily` / `acceptance` / `outcome-update`。`cron-setup.sh` 若识别到本项目已有 daily，会保留现有评分链并明确输出“保留现有评分链，不用于从零恢复”；若没有已有 daily，则只保留非评分任务并输出“不新增评分写任务”。HOLD 本身不证明 provider 已确认失效；若诊断已确认不安全，应按事故处置显式停用，不要依赖安装器的 stale/unknown 保留路径。
- `scripts/check_market_data_readiness.py --scope daily` 返回 `HOLD_DAILY`：停止 daily 写入。
- `price_at_score` 覆盖率连续两个交易日低于 95%。
- L3 覆盖率低于 90%。
- 任一 L3 窗口出现 mixed source / mixed adjustment / unknown volume unit。
- BaoStock fallback 与 Tushare 同日 close 偏差超过 0.5%，且样本超过 3 只。
