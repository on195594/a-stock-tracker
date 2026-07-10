# L3 v2 Offline Backtest Retrospective

日期：2026-07-10
状态：completed / NEED_QFQ

## 1. 本次目标

实现 L3 v2 只读离线回测，不改生产 L3、不写 `tracker.db`、不触发 Telegram/cron/Gemini/Sheets，先验证：

- qfq 派生是否可行。
- v1/v2 信号分布是否可比。
- raw daily 与同股 20 交易日冷却去重口径。
- 交易成本对 30d alpha 的影响。
- 是否允许进入后续 TDD。

## 2. 完成产物

- 脚本：`scripts/offline_l3_v2_backtest.py`
- 报告：`docs/reviews/2026-07-08-l3-v2-backtest-report.md`
- artifacts：`docs/reviews/l3-v2-backtest-artifacts/`
  - `signals_daily.csv`
  - `events_raw_daily.csv`
  - `events_dedup_20d.csv`
  - `metrics_summary.json`
- 复审证据：`docs/reviews/agy-l3-v2-backtest-review/`
  - `prompt.txt`
  - `before-hashes.sha256`
  - `after-hashes.sha256`
  - `stdout.md`
  - `stderr.log`

## 3. 当前结论

Decision gate：`NEED_QFQ`

关键数据：

- Framework A prediction rows：1541
- buy_strong rows：766
- stock count：38
- local none panels：35
- qfq panels：0
- qfq reason counts：`{"TUSHARE_RATE_LIMIT": 38}`
- buy_strong qfq issue rows：766

解释：

- 项目内 `.env` 的 `TUSHARE_TOKEN` 已被脚本读取，当前不是 token 缺失。
- Tushare `adj_factor` 接口返回 `频率超限(1次/分钟)`，导致本轮不能形成足够 qfq 覆盖。
- v2 `pass_strong` 必须依赖 qfq；qfq 覆盖不足时，none-adjusted 结果只能作为 weak/reject 诊断，不能作为 GO_TDD 证据。

## 4. 关键实现边界

- SQLite 使用 `file:...?...mode=ro` 打开，并设置/校验 `PRAGMA query_only=ON`。
- 脚本不导入生产 `pipeline.py`、`telegram_push.py`、Gemini 或 Sheets 模块。
- 默认不写报告；只有显式 `--output` / `--artifacts-dir` 且非 `--dry-run` 才写文件。
- `--allow-tushare-fetch` 只在内存中派生 qfq，不写 `daily_bars`。
- `--no-external-fetch` 和 `--allow-tushare-fetch` 互斥。

## 5. 复审发现与修复

AGY 第一次复审返回 `REQUEST_CHANGES`，指出两个阻塞问题：

1. 20 日冷却使用了 sparse prediction dates，而不是市场交易日。
2. qfq 只调整 OHLC，没有反向调整 volume。

已修复：

- `run_backtest()` 从 `index_prices` 读取沪深 300 日期作为市场交易日序列，`build_dedup_events()` 按该序列计算同股 20 交易日冷却。
- qfq 派生中 `volume = raw_volume / (adj_factor_t / adj_factor_latest)`，并对 zero factor / missing factor 做 alignment failure。
- 重新生成报告和 artifacts。
- 重新运行 AGY 复审，最终 verdict 为 `APPROVE`。
- before/after hashes 无漂移。

## 6. 验证命令

已执行：

```bash
python3 -m py_compile scripts/offline_l3_v2_backtest.py
.venv/bin/ruff check scripts/offline_l3_v2_backtest.py
git diff --check -- scripts/offline_l3_v2_backtest.py docs/reviews/2026-07-08-l3-v2-backtest-report.md
python3 scripts/offline_l3_v2_backtest.py --db tracker.db --start 2026-07-01 --end 2026-07-08 --dry-run --no-external-fetch
python3 scripts/offline_l3_v2_backtest.py --db tracker.db --start 2025-01-01 --end 2026-07-08 --output docs/reviews/2026-07-08-l3-v2-backtest-report.md --artifacts-dir docs/reviews/l3-v2-backtest-artifacts --allow-tushare-fetch --preload-trading-days 120
agy --print-timeout 10m --print "$(cat docs/reviews/agy-l3-v2-backtest-review/prompt.txt)"
```

## 7. 经验教训

- “token 存在”不等于“qfq 覆盖可用”。`daily` 和 `adj_factor` 的权限/限频要分开验证。
- 回测样本域可以来自 `predictions`，但交易日冷却必须使用市场交易日 calendar。
- qfq 价格复权后，若规则使用 volume，也必须同步定义 volume 复权口径。
- 决策 gate 要比指标表更保守：只要 buy_strong 子集 qfq coverage 不足，就不得 GO_TDD。
- 独立复审应在报告生成后进行；复审指出阻塞项后必须重新生成 artifacts 和 hash。

## 8. 后续建议

下一步不应直接 TDD 生产 v2，而应先写 qfq 获取方案：

- 明确 Tushare `adj_factor` 的限频和授权等级。
- 设计离线 qfq cache 或分批 collector，支持断点续跑。
- 保持 DB 只读，除非另开 spec 授权新的隔离缓存文件或表。
- 重跑 L3 v2 offline report，要求 buy_strong qfq issue 降为 0 后再讨论 TDD。
