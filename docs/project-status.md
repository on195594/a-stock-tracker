# a-stock-tracker Project Status

创建时间：2026-06-26
状态：active PM control page

## 当前结论

项目已从行情修复/cron 恢复阶段切回稳定运行观察阶段。当前 active phase 是 **Phase 6 report-only 深化**；Framework B 仍不得生产写入。

## Active Phase

| Phase | 状态 | 当前动作 | 下一检查点 | Exit criteria |
|---|---|---|---|---|
| Phase 4 验证基础 | 已完成，持续观察 | 每周看 accuracy-report，不在此阶段顺手调权重 | 每周一 | 若要调权重，另开 spec |
| Phase 5 L3 买点层 | 已完成，持续观察 | 观察 L3 30d 样本自然结案 | 每周一 | L3 30d 样本 ≥30 后再评估信号有效性 |
| Phase 6 多框架激活 | report-only 深化中 | 只读观察 B label / dry-run / 数据质量 | 2026-07-26 后首次 B label review | B label 已结案 ≥20、overdue=0、独立审查通过、另写生产化 spec |
| Phase 7 选股宇宙扩展 | 未启动 | 等 Phase 6 或明确降级策略 | 暂无 | 单独设计动态池和 API 压测 |

## 当前事实基线

| 项目 | 当前状态 | 证据 |
|---|---|---|
| 行情 provider | Tushare 主源 + 隔离 BaoStock degraded fallback | `a-stock-lib==0.2.0`，`lib/market_data.py` 已使用 `IsolatedBaoStockMarketDataProvider` |
| readiness | `READY_CRON` | `scripts/check_market_data_readiness.py --scope cron` |
| cron | 已恢复 | managed block 管理 weekly / daily / outcome-update |
| 真实 probe | 已通过 | `docs/reviews/2026-06-26-tushare-capability-probe.md` |
| 真实 backfill | 已完成 | 35/35 行情刷新成功，L3 metadata 已重算 |
| 真实 daily | 已完成 | 2026-06-26 daily 完整跑完，因当日已有 A 框记录幂等写入 0 条 |
| Framework B | report-only | `SUPPORTED_FRAMEWORKS={"A"}`，不写 B 生产 predictions |
| Phase 6 阻塞 | B label 已结案样本不足 | `0/20`，最早可评估日期 `2026-07-26` |

## Spec Ledger

| Spec / Plan | 状态 | Owner | 下一动作 | Exit criteria |
|---|---|---|---|---|
| `docs/evolution-roadmap.md` | v1.6 当前基线 | Hermes PM | 随 Phase 状态变化更新 | 和真实系统状态一致 |
| `docs/plans/2026-06-26-phase6-report-only-next-steps.md` | active | Hermes PM | cron 自然运行一轮后执行 P3-B 周度复核 | B label review 前 report-only 流程稳定 |
| `docs/runbooks/market-data-provider-recovery.md` | active | Hermes PM | 若 readiness/cron 语义变更则同步 | HOLD/READY 行为与 `cron-setup.sh` 一致 |
| `docs/reviews/2026-06-26-tushare-capability-probe.md` | latest readiness evidence | 系统探测 | 新 probe 覆盖旧证据 | 最新交易日 probe PASS |
| `accuracy_report.txt` | latest report | pipeline | 每周更新 | Phase 6 仍明确 report-only |

## Open Blockers

| Blocker | Impact | Unblock condition | ETA |
|---|---|---|---|
| B label 已结案样本不足 `0/20` | 阻止 Framework B 生产化 | 已结案 ≥20 且 overdue=0 | 最早 2026-07-26 后 |
| L3 30d 样本不足 `0/30` | 无法判断 L3 信号有效性 | L3 30d 已结案 ≥30 | 等自然结案 |
| cron 恢复后尚需自然跑一轮 | 需要确认非手动运行稳定性 | weekly/daily/outcome 日志均正常 | 2026-06-29 晚后 |
| Framework A strong 层级尚未证明优于基准 | 不宜调权重或宣称模型有效 | 另开权重复核 spec | 待更多样本与独立审查 |

## Weekly PM Loop

| 日期 | 检查 | 结果 | 决策 |
|---|---|---|---|
| 2026-06-26 | 真实 probe/backfill/daily、readiness、cron 恢复 | `READY_CRON`，cron 已恢复，Phase 6 仍 report-only | 进入 cron 自然运行观察，不做生产化写入 |
| 2026-06-29 后 | `logs/weekly.log` / `logs/daily.log` / `logs/outcome.log`、`READY_CRON`、`accuracy-report` | 待执行 | 若异常，先修行情/cron；若正常，只提交报告/状态更新 |
| 2026-07-26 后 | B label 30d 结案、overdue、行业覆盖、B-A delta | 待执行 | 满足门槛后写 `phase6-b-label-review.md`，不直接上线 |

## 每周复核命令

```bash
cd /home/lin/a-stock-tracker
source .venv/bin/activate

tail -n 120 logs/weekly.log
tail -n 120 logs/daily.log
tail -n 120 logs/outcome.log

python3 scripts/check_market_data_readiness.py --scope cron
python3 pipeline.py accuracy-report
```

## 禁止事项

- 不恢复 Framework B 生产写入。
- 不修改 `weights.json`。
- 不把 B provisional thresholds 用作交易或 Telegram 推送门槛。
- 不绕过 B label `0/20` 阻塞项。
- 不在 Phase 6 report-only 任务中顺手改评分权重、schema 或 watchlist。
