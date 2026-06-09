# Market Data Provider Implementation Audit

日期：2026-06-09

范围：`docs/plans/2026-06-09-market-data-provider-replacement-plan.md`

## 总体结论

本地工程改造已覆盖 Phase 0 guardrail、Phase 1/2 provider scaffold、Phase 3 backfill scaffold 和 cron recovery gate。`TUSHARE_TOKEN` 已配置后，Tushare 股票日线能力、close 交叉校验、历史日线 backfill 和 L3 覆盖率已通过。生产恢复仍未完成，原因是 `index_daily` / `trade_cal` 在 probe 中触发 Tushare 1 次/小时限频，最新 probe 仍未 PASS。

当前状态：blocked by Tushare hourly rate limit on required probe endpoints.

## 需求审计

| 计划要求 | 当前证据 | 状态 |
|---|---|---|
| 删除 AKShare/东方财富行情生产路径 | `lib/market_data.py` 默认 provider 不再导入/构造 AKShare，`market_data.ak` 仅为测试 shim | 完成 |
| disabled provider 下 `daily` 不写 predictions | `tests/test_pipeline.py::test_daily_with_disabled_default_provider_writes_audit_but_no_predictions` | 完成 |
| `SOURCE_DISABLED` 可审计 | `MarketDataCacheService.refresh_daily_bars()` 写 `market_data_audit`，测试覆盖 | 完成 |
| Tushare capability probe | `scripts/probe_tushare_market_data.py` 生成 `docs/reviews/2026-06-09-tushare-capability-probe.md`；股票日线 PASS，`index_daily`/`trade_cal` 最新为 RATE_LIMITED | 部分通过，等待限频窗口 |
| Tushare provider | `lib/tushare_provider.py` 覆盖 daily/index_daily/trade_cal、代码转换、结构化错误 | 本地完成 |
| Tushare token 缺失 -> `AUTH_MISSING` | `tests/test_market_data.py::test_tushare_provider_token_missing_returns_auth_missing` | 完成 |
| canonical symbol 规则 | `pipeline._ensure_index_prices()` 传 `000300`，provider 层转换；测试覆盖 | 完成 |
| `source=tushare.daily` / `source=tushare.index_daily` | provider 常量与测试覆盖 | 完成 |
| BaoStock fallback | `lib/baostock_provider.py` + `CompositeMarketDataProvider` | 本地完成 |
| BaoStock-only 不允许 daily 写生产评分 | `tests/test_pipeline.py::test_daily_with_baostock_only_env_still_does_not_write_predictions` | 完成 |
| `daily_bars.volume_unit` 持久化 | `lib/cache.py` schema 和 `upsert_daily_bars()` 增加 `volume_unit` | 完成 |
| L3 拒绝 mixed source/adjusted/volume_unit | `_compute_stock_entry_signal()` 与测试覆盖 | 完成 |
| market-data-backfill 命令 | `pipeline.py market-data-backfill --start --end` | 完成 |
| backfill 不重算历史 `total_score` | `tests/test_pipeline.py::test_market_data_backfill_recomputes_existing_l3_metadata_without_rescoring` | 完成 |
| cron 恢复门禁 | `scripts/check_market_data_readiness.py` + `cron-setup.sh` gate | 完成 |
| close 价差 ≤ 0.5% 作为恢复门禁 | 最新 probe report 显示 `Close cross-check: PASS`，样本差异均 ≤ 0.5% | 完成 |
| L3 覆盖率 ≥ 95% | `pipeline.py accuracy-report` 显示 `L3 覆盖率：210/210 = 100.0%` | 完成 |
| `price_at_score` 覆盖率 ≥ 95% | 依赖真实 Tushare token/probe | 未验证 |
| cron 恢复 | readiness 当前 `NOT_READY`，cron-setup 会跳过 daily/outcome | 阻塞 |

## 当前阻塞证据

`.venv/bin/python scripts/check_market_data_readiness.py` 当前输出：

```text
NOT_READY: market data provider is not cleared for cron recovery
- latest Tushare capability probe is not PASS: /home/lin/a-stock-tracker/docs/reviews/2026-06-09-tushare-capability-probe.md
```

最新 probe report 显示：

- `daily 600036` / `000001` / `002594`：`ok`
- `index_daily 000300`：`RATE_LIMITED`，Tushare 返回 1 次/小时限频
- `trade_cal SSE`：`RATE_LIMITED`，Tushare 返回 1 次/小时限频
- `Close cross-check: PASS`
- `Result: FAIL`

## 继续执行条件

等待 Tushare 1 小时限频窗口结束后，按顺序运行：

```bash
.venv/bin/python scripts/probe_tushare_market_data.py
.venv/bin/python scripts/check_market_data_readiness.py
.venv/bin/python pipeline.py accuracy-report
```

只有 readiness 返回 `READY` 后，才可恢复 `daily` / `outcome-update` cron。
