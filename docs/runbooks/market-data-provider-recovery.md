# Market Data Provider Recovery Runbook

创建时间：2026-06-09
最新状态：2026-07-23 起，tracker 行情运行时强制为 TuShare-only；默认与 backfill provider 均 fail-closed，不再实例化第二行情源。生产 `daily_bars` 仅保留 TuShare，QFQ 由 `tushare.pro_bar(adj="qfq")` 提供。2026-07-27 capability probe 五项通过，`check_market_data_readiness.py --scope cron` 与 `--scope daily` 均返回 READY。

## 状态定义

- `SOURCE_DISABLED`：行情 provider 未启用，不是停牌或临时无行情。
- `AUTH_MISSING`：缺少 `TUSHARE_TOKEN` 或 token 无法认证。
- `failed`：TuShare 失败后保持失败，不允许第二行情源补写。

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
python3 pipeline.py accuracy-report
sqlite3 tracker.db "SELECT error_code, COUNT(*) FROM market_data_audit WHERE error_code='SOURCE_DISABLED' GROUP BY error_code;"
bash cron-setup.sh
```

不保留 staged daily 或手工编辑 crontab 的恢复路径。readiness 未满足成组恢复条件时保持停止；条件满足后只通过 `cron-setup.sh` 恢复受管任务。

## TuShare-only 边界

- `get_default_market_data_provider()` 只返回 TuShare 或 disabled provider。
- 旧 `MARKET_DATA_ALLOW_BAOSTOCK_ONLY` 环境变量不再生效。
- QFQ 强来源合同为 `tushare.pro_bar.qfq`；出现其他来源时 L3 v2 返回 `QFQ_SOURCE_MISMATCH`。
- probe 只读取本地 `tushare.daily` 缓存作同源一致性检查；参考缺失或数据库损坏时返回 `MANUAL_REQUIRED`，不联网降级。
- probe 的个股样本必须来自当前 watchlist，且在 `daily_bars` 中存在同交易日、`adjusted='none'`、`source='tushare.daily'` 的参考行；不得继续使用未跟踪样本（例如已退出本项目样本集的 `000001`），否则会把样本配置漂移误报为 provider 未就绪。

## 停止与恢复边界

以下条件分别用于阻止从零恢复或要求停止生产写入；`HOLD_CRON` 的 stale/unknown 保留边界按首条单独处理：

- `scripts/check_market_data_readiness.py --scope cron` 返回 `HOLD_CRON`：禁止从零恢复成组 `daily` / `acceptance` / `outcome-update`。`cron-setup.sh` 若识别到本项目已有 daily，会保留现有评分链并明确输出“保留现有评分链，不用于从零恢复”；若没有已有 daily，则只保留非评分任务并输出“不新增评分写任务”。HOLD 本身不证明 provider 已确认失效；若诊断已确认不安全，应按事故处置显式停用，不要依赖安装器的 stale/unknown 保留路径。
- `scripts/check_market_data_readiness.py --scope daily` 返回 `HOLD_DAILY`：停止 daily 写入。
- `price_at_score` 覆盖率连续两个交易日低于 95%。
- L3 覆盖率低于 90%。
- 任一 L3 窗口出现 mixed source / mixed adjustment / unknown volume unit。
- `daily_bars` 出现任一非 TuShare source，或 `market_data_audit` 出现已禁用来源记录。
