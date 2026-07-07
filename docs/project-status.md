# a-stock-tracker Project Status

创建时间：2026-06-26
状态：active PM control page

## 当前结论

项目已从行情修复/cron 恢复阶段切回稳定运行观察阶段。当前 active phase 是 **Phase 6 report-only 观察**；Framework B 仍不得生产写入。

2026-07-01 已完成共享包版本对齐：本项目锁定并验证 `a-stock-lib==0.2.0`，继续只保留 tracker 专属的环境门禁、SQLite 缓存、cron 编排和评分管道。

2026-07-02 weekly PM loop 已完成：daily/outcome 自然运行正常，`probe_tushare_market_data.py` 已刷新当天 report，`READY_CRON` 恢复。随后已定向补齐 watchlist 后 14 支股票的基本面缓存并重跑 `accuracy-report`；数据质量门槛恢复 OK，Phase 6 仍因 B label 已结案样本不足保持 report-only。

2026-07-02 同步 a-stock-research v2.5.0"历史分位极低须做反向解读检验"教训到 Framework B dry-run 报告：新增 `roe_latest`（最新单年ROE，同花顺年度数据，非季度）字段抓取，`lib/framework_b_report.py` 在 `roe_3y_avg` 显著高于 `roe_latest` 时给候选行追加 `⚠️ROE趋势预警` 文本标注。**纯报告层展示，不改变任何打分数值/权重/排序**（不碰 `weights.json`，不改 `score_stock` 调用参数），符合本文档"禁止事项"里"不修改 weights.json"、"不顺手调评分权重"的边界。Framework B 仍 report-only，不受影响。

P0 根因已定位：2026-06-27 `weekly` 实际卡在第 22 只 `002119` 的外部行情调用中，进程残留到 2026-07-02，导致后 14 支股票缓存停留在 2026-06-20 并超过 168h TTL。`fetcher`/`weekly` 的进程级超时保护已完成（commit `89fd7c5`，验证 `208 passed`），后续通过自然 cron 观察是否复发。

2026-07-02 Phase 6 weekly PM loop 已自动化：新增 `scripts/weekly_pm_loop.py`，每周一 09:30 由 cron 运行，复核 weekly/daily/outcome 日志、`READY_CRON` 和 `accuracy-report`，并通过 Telegram bot 发送摘要。实现前已写 spec 并经 agy 独立审查 PASS；实现 commit `f181010`，验证 `211 passed, 1 skipped`。当前 dry-run 能跑通，但会因 2026-06-27 `weekly.log` 最新 weekly 运行中的历史失败标记保持 `FAIL`，等下一轮自然 weekly 成功后应自动恢复。

## Active Phase

| Phase | 状态 | 当前动作 | 下一检查点 | Exit criteria |
|---|---|---|---|---|
| Phase 4 验证基础 | 已完成，持续观察 | weekly PM loop 自动检查 accuracy-report；不在此阶段顺手调权重 | 每周一自动摘要 | 若要调权重，另开 spec |
| Phase 5 L3 买点层 | 已完成，持续观察 | weekly PM loop 自动提示 L3 30d 样本状态 | 每周一自动摘要 | L3 30d 样本 ≥30 后再评估信号有效性 |
| Phase 6 多框架激活 | report-only 观察 | 自动化 weekly PM loop 只读观察 B label / dry-run / cron 日志 | 2026-07-26 后首次 B label 自然结案复核 | B label 已结案 ≥20、overdue=0、数据质量门槛 OK、独立审查通过、另写生产化 spec |
| Phase 7 选股宇宙扩展 | 未启动 | 等 Phase 6 或明确降级策略 | 暂无 | 单独设计动态池和 API 压测 |

## 当前事实基线

| 项目 | 当前状态 | 证据 |
|---|---|---|
| 行情 provider | Tushare 主源 + 隔离 BaoStock degraded fallback | `a-stock-lib==0.2.0`，`lib/market_data.py` 已使用 `IsolatedBaoStockMarketDataProvider`；2026-07-04 tracker 测试 `225 passed, 1 skipped` |
| readiness | `READY_CRON` | 2026-07-02 `scripts/check_market_data_readiness.py --scope cron` |
| cron | 已恢复，latest daily/outcome 自然运行正常；weekly timeout hardening 与 weekly PM loop 自动化已完成 | daily: 2026-07-01 16:30 写入 21/跳过 14；outcome: 2026-07-01 17:00 更新 35；weekly 2026-06-27 残留进程已于 2026-07-02 清理；commit `89fd7c5` 加固单股 fetch 子进程超时；commit `f181010` 安装每周一 09:30 `weekly-pm-loop` |
| 真实 probe | 已刷新并通过 | `docs/reviews/2026-07-02-tushare-capability-probe.md` |
| 真实 backfill | 已完成 | 35/35 行情刷新成功，L3 metadata 已重算 |
| 真实 daily | 已完成 | 2026-06-26 daily 完整跑完，因当日已有 A 框记录幂等写入 0 条 |
| Framework B | report-only | `SUPPORTED_FRAMEWORKS={"A"}`，不写 B 生产 predictions |
| Phase 6 阻塞 | B label 已结案样本不足 | 数据质量门槛 OK；B label `0/20`，最早可评估日期 `2026-07-26` |

## Spec Ledger

| Spec / Plan | 状态 | Owner | 下一动作 | Exit criteria |
|---|---|---|---|---|
| `docs/evolution-roadmap.md` | v1.7 当前基线 | Hermes PM | 随 Phase 状态变化更新 | 和真实系统状态一致 |
| `docs/plans/2026-06-26-phase6-report-only-next-steps.md` | active | Hermes PM | 继续 P3-B 周度复核，并单独 harden weekly/fetcher timeout | B label review 前 report-only 流程稳定 |
| `docs/specs/2026-07-02-weekly-pm-loop-automation-spec.md` | implemented | Hermes PM + agy review | 等首轮自然 cron 摘要；异常先修行情/cron | 每周一自动 Telegram 摘要可用，不重复告警，不越权启用生产化 |
| `/home/lin/a-stock-lib/docs/plans/2026-07-01-three-project-next-work-plan.md` | active cross-project plan | Hermes PM | 按 P0/P1/P2 顺序推进共享包、tracker、research 联动事项 | 三项目版本/文档/任务边界一致 |
| `docs/runbooks/market-data-provider-recovery.md` | active | Hermes PM | 若 readiness/cron 语义变更则同步 | HOLD/READY 行为与 `cron-setup.sh` 一致 |
| `docs/reviews/2026-07-02-tushare-capability-probe.md` | latest readiness evidence | 系统探测 | 新 probe 覆盖旧证据 | 最新交易日 probe PASS |
| `accuracy_report.txt` | latest report | pipeline | 每周更新 | Phase 6 仍明确 report-only |

## Open Blockers

| Blocker | Impact | Unblock condition | ETA |
|---|---|---|---|
| B label 已结案样本不足 `0/20` | 阻止 Framework B 生产化 | 已结案 ≥20 且 overdue=0 | 最早 2026-07-26 后 |
| L3 30d 样本不足 `9/30` | 无法判断 L3 信号有效性 | L3 30d 已结案 ≥30 | 等自然结案 |
| 首轮自动 weekly PM loop 待自然验证 | 需要确认新 cron 能按时发送 Telegram 摘要 | `weekly-pm-loop` 周一 09:30 自然运行并产生日志/摘要 | 下一周一 |
| Framework A strong 层级尚未证明优于基准 | 不宜调权重或宣称模型有效 | 另开权重复核 spec | 待更多样本与独立审查 |

## Weekly PM Loop

| 日期 | 检查 | 结果 | 决策 |
|---|---|---|---|
| 2026-06-26 | 真实 probe/backfill/daily、readiness、cron 恢复 | `READY_CRON`，cron 已恢复，Phase 6 仍 report-only | 进入 cron 自然运行观察，不做生产化写入 |
| 2026-07-01 | 共享包版本对齐、tracker 依赖锁定、全量测试 | `a-stock-lib==0.2.0`，tracker 测试 `205 passed` | 继续 Phase 6 report-only；下一步只做日志/样本复核，不恢复 B 生产写入 |
| 2026-07-02 | `logs/weekly.log` / `logs/daily.log` / `logs/outcome.log`、`READY_CRON`、`accuracy-report` | daily/outcome 最新自然运行正常；当天 probe PASS 后 `READY_CRON`；accuracy-report 显示 cache 缺失/过期 14/35、L3 30d 9/30、B label 0/20 | 不进入生产化；下一步先补齐 watchlist 基本面缓存并重跑 accuracy-report |
| 2026-07-02 | P0 cache 修复：清理 2026-06-27 残留 weekly，定向刷新后 14 支过期缓存，重跑 `accuracy-report` | 基本面缓存可用 35/35，required 字段可接受 35/35，PB 日度可计算 35/35；B label 候选增至 11，已结案仍为 0/20 | 数据质量阻塞解除；下一步修 weekly/fetcher timeout hardening，继续等待 B label 自然结案 |
| 2026-07-02 | weekly/fetcher timeout hardening | 单股 fetch 独立子进程 + fetcher 内部进程级 timeout；`208 passed` | 等下一轮自然 weekly 验证，不恢复 B 生产写入 |
| 2026-07-02 | Phase 6 weekly PM loop 自动化 | spec 经 agy 审查 PASS；新增 `scripts/weekly_pm_loop.py`、Telegram 摘要、exit code 2 去重、readiness 9 天宽限、日志最新日期过滤；`211 passed, 1 skipped`；crontab 已安装每周一 09:30 | 等首轮自然自动摘要；当前仍不恢复 B 生产写入 |
| 2026-07-04 | agy 工程审查修复（Batch A+B）：DB per-stock SAVEPOINT、spot_em 重试计数器、Gemini 退避重试+过期缓存降级、subprocess stderr 转发、SQL identifier allowlist、outcome window allowlist、agent_reviewer 接入真实 Gemini、测试补全 | 11 commits；`225 passed, 1 skipped`（+14 vs 上轮）；agy 复核全通过；collab-retro 已记录，lessons-learned 新增 B-5/D-5/F-6 |
| 2026-07-26 后 | B label 30d 结案、overdue、行业覆盖、B-A delta | 待执行 | 满足门槛后写 `phase6-b-label-review.md`，不直接上线 |

## 每周自动复核

当前 cron managed block 已包含：

```cron
30 09 * * 1 /home/lin/a-stock-tracker/cron-alert-wrap.sh "cd /home/lin/a-stock-tracker && .venv/bin/python scripts/weekly_pm_loop.py" weekly-pm-loop >> /home/lin/a-stock-tracker/logs/weekly-pm-loop.log 2>&1
```

脚本会生成：

- `logs/weekly-pm-loop.log`
- `logs/weekly-pm-loop-summary.txt`

无 Telegram 凭证时脚本不会失败；Telegram 发送失败返回 1 触发外层告警；业务 WARN/FAIL 且摘要已发送时返回 2，`cron-alert-wrap.sh` 不重复发送第二条告警。

## 手工复核命令

```bash
cd /home/lin/a-stock-tracker
source .venv/bin/activate

tail -n 120 logs/weekly.log
tail -n 120 logs/daily.log
tail -n 120 logs/outcome.log

python3 scripts/check_market_data_readiness.py --scope cron
python3 pipeline.py accuracy-report
python3 scripts/weekly_pm_loop.py --dry-run --no-telegram
```

## 禁止事项

- 不恢复 Framework B 生产写入。
- 不修改 `weights.json`。
- 不把 B provisional thresholds 用作交易或 Telegram 推送门槛。
- 不绕过 B label `0/20` 阻塞项。
- 不在 Phase 6 report-only 任务中顺手改评分权重、schema 或 watchlist。
