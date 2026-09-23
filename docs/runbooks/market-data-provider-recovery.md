# Market Data Provider Recovery Runbook

创建时间：2026-06-09
最后更新：2026-09-21

## 当前范围

Framework A 已以 `CLOSED_UNPROVEN` 结案。本文只处理 TuShare 通用数据任务的恢复：

- 周六财务/分红采集与物化；
- 工作日 16:00 SSE 交易日历、QFQ 个股行情和沪深300全收益；
- 工作日 17:15 估值采集与物化。

Framework A daily、自动策略报告和 Telegram 观察名单已永久退出受管 cron。任何恢复操作都不得重新启用它们。

行情运行时强制为 TuShare-only；默认与 backfill provider 均 fail-closed，不实例化第二行情源。生产 `daily_bars` 只接受 TuShare，QFQ 来源固定为 `tushare.pro_bar.qfq`。

## 状态定义

- `SOURCE_DISABLED`：TuShare 行情 provider 未启用，不代表停牌或临时无行情。
- `AUTH_MISSING`：缺少 `TUSHARE_TOKEN` 或 token 无法认证。
- `failed`：TuShare 失败后保持失败，不允许第二行情源补写。
- `HOLD_CRON` / `HOLD_DAILY`：历史 readiness 诊断结果，不再控制或授权 Framework A cron。

## 通用数据恢复前置条件

1. 环境中存在有效的 `TUSHARE_TOKEN`。
2. `python3 -m scripts.probe_tushare_market_data` 能生成当日分层诊断报告。
3. `python3 -m scripts.check_market_data_readiness --scope cron` 的结果仅用于定位 provider、缓存和日期问题；不得据此恢复 Framework A。
4. QFQ、交易日历和 TuShare primary 三域分别满足各自来源、日期、覆盖率及原子物化合同。

## 恢复步骤

```bash
source .venv/bin/activate
python3 -m scripts.probe_tushare_market_data
python3 -m scripts.check_market_data_readiness --scope cron
sqlite3 tracker.db "SELECT error_code, COUNT(*) FROM market_data_audit WHERE error_code='SOURCE_DISABLED' GROUP BY error_code;"
bash cron-setup.sh
crontab -l
```

`cron-setup.sh` 固定安装三项通用数据任务并清理项目本地的 Framework A daily。恢复后必须确认 crontab 中不存在 `pipeline.py daily`，且无关项目和系统任务保持不变。

## TuShare-only 边界

- `get_default_market_data_provider()` 只返回 TuShare 或 disabled provider。
- 旧 `MARKET_DATA_ALLOW_BAOSTOCK_ONLY` 环境变量不再生效。
- QFQ 强来源合同为 `tushare.pro_bar.qfq`；不得写入其他来源或 mixed adjustment。
- probe 只读取本地 `tushare.daily` 缓存作同源一致性检查；参考缺失或数据库损坏时直接返回 `FAIL`，不保留人工对账分支。
- probe 样本必须来自历史固定 watchlist，并在 `daily_bars` 中存在同交易日、`adjusted='none'`、`source='tushare.daily'` 的参考行。

## 停止条件

出现以下任一情况时停止对应通用数据写入并保留失败证据：

- token 缺失、认证失败或 provider 被禁用；
- `daily_bars` 出现非 TuShare 来源、mixed adjustment 或未知 volume unit；
- 交易日历、QFQ、基准或 TuShare primary 数据未满足各自的新鲜度和覆盖合同；
- SQLite quick check 失败、来源字段矛盾或原子物化无法完成。

停止通用数据任务不授权删除数据库、shadow 数据、artifact 或历史 prediction。恢复也不授权重新开启 Framework A、L3 投资判断、策略报告或 Telegram 观察名单。
