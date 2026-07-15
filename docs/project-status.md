# a-stock-tracker Project Status

创建时间：2026-06-26
状态：active PM control page

## 当前结论

主业务仍处于 **Phase 6 report-only 观察**，Framework B 不得生产写入；Phase 5 L3 v2 已完成 QFQ 生产接入和 Telegram 主推切换。来源约束的定性评分 v2 已完成获批的 MILESTONE-002 fixture-first 合同（types/taxonomy/schema/prompt/validator），AGY 只读审查 PASS；真实 Gemini、生产 DB、pipeline、shadow 与 cutover 均未授权。

2026-07-15 控制面修复已完成：当天 Tushare probe 的 daily/index/calendar/close cross-check 全部 PASS，readiness 恢复 `READY_CRON`，五项 managed cron 已重新安装；weekly PM loop 已修复中文“失败 0 只”和降级 WARNING 的错误分级，真实 dry-run 从误报 FAIL 恢复为符合当前降级事实的 WARN；mypy 24 errors 已清零，31 个历史文件完成 Ruff format 基线化；MILESTONE-002 边界加固后的完整质量门禁为 `450 passed` 且 lint/format/type/diff 全绿。

2026-07-10 的 `NEED_QFQ` 是历史离线 gate。2026-07-12 已通过 BaoStock QFQ 采集解除：当前 `daily_bars` 中 QFQ 覆盖 35/35 代码，生产 daily 在 2026-07-13/14 写入 70 条 v2 记录，Telegram 主推使用 `l3_v2_signal=1`。

2026-07-01 已完成共享包版本对齐：本项目锁定并验证 `a-stock-lib==0.2.0`，继续只保留 tracker 专属的环境门禁、SQLite 缓存、cron 编排和评分管道。

2026-07-02 weekly PM loop 已完成：daily/outcome 自然运行正常，`probe_tushare_market_data.py` 已刷新当天 report，`READY_CRON` 恢复。随后已定向补齐 watchlist 后 14 支股票的基本面缓存并重跑 `accuracy-report`；数据质量门槛恢复 OK，Phase 6 仍因 B label 已结案样本不足保持 report-only。

2026-07-02 同步 a-stock-research v2.5.0"历史分位极低须做反向解读检验"教训到 Framework B dry-run 报告：新增 `roe_latest`（最新单年ROE，同花顺年度数据，非季度）字段抓取，`lib/framework_b_report.py` 在 `roe_3y_avg` 显著高于 `roe_latest` 时给候选行追加 `⚠️ROE趋势预警` 文本标注。**纯报告层展示，不改变任何打分数值/权重/排序**（不碰 `weights.json`，不改 `score_stock` 调用参数），符合本文档"禁止事项"里"不修改 weights.json"、"不顺手调评分权重"的边界。Framework B 仍 report-only，不受影响。

P0 根因已定位：2026-06-27 `weekly` 实际卡在第 22 只 `002119` 的外部行情调用中，进程残留到 2026-07-02，导致后 14 支股票缓存停留在 2026-06-20 并超过 168h TTL。`fetcher`/`weekly` 的进程级超时保护已完成（commit `89fd7c5`，验证 `208 passed`），后续通过自然 cron 观察是否复发。

2026-07-02 Phase 6 weekly PM loop 已自动化：新增 `scripts/weekly_pm_loop.py`，每周一 09:30 由 cron 运行，复核 weekly/daily/outcome 日志、`READY_CRON` 和 `accuracy-report`，并通过 Telegram bot 发送摘要。实现前已写 spec 并经 agy 独立审查 PASS；实现 commit `f181010`，验证 `211 passed, 1 skipped`。2026-07-13 首轮自然运行暴露的 failure-marker 误报已于 2026-07-15 修复并补测试。

## Active Phase

| Phase | 状态 | 当前动作 | 下一检查点 | Exit criteria |
|---|---|---|---|---|
| Phase 4 验证基础 | 已完成，持续观察 | weekly PM loop 自动检查 accuracy-report；不在此阶段顺手调权重 | 每周一自动摘要 | 若要调权重，另开 spec |
| Phase 5 L3 买点层 | v1 保留审计；v2 Phase 2+3 已完成 | 观察 v2 信号分布与自然 outcome；不改 L1/L2 分数 | readiness 已恢复，继续自然运行 | 足量 v2 30/60d 样本和独立复核后再讨论规则变化 |
| 定性评分 v2 MILESTONE-002 | 已完成 | 保持 fixture-first 合同与生产路径隔离；不启动未授权 shadow | 后续里程碑另行立项、批准 | REQ-001~035 对应本地合同齐全；不自动进入 MILESTONE-003 |
| Phase 6 多框架激活 | report-only 观察 | 自动化 weekly PM loop 只读观察 B label / dry-run / cron 日志 | 2026-08-13 后首次 B label 自然结案复核 | B label 已结案 ≥20、overdue=0、数据质量门槛 OK、独立审查通过、另写生产化 spec |
| Phase 7 选股宇宙扩展 | 未启动 | 等 Phase 6 或明确降级策略 | 暂无 | 单独设计动态池和 API 压测 |

## 当前事实基线

| 项目 | 当前状态 | 证据 |
|---|---|---|
| 行情 provider | Tushare 主源 + 隔离 BaoStock degraded fallback | `a-stock-lib==0.2.0`；2026-07-15 回归测试 `373 passed` |
| readiness | **`READY_CRON`** | 2026-07-15 probe 的 daily/index/calendar/close cross-check 全部 PASS |
| cron | weekly / weekly-PM / QFQ / daily / outcome-update 五项已按门禁重新安装 | `00 16` QFQ、`30 16` daily、`00 17` outcome；managed block 已核对 |
| 真实 probe | 当天已刷新并通过 | `docs/reviews/2026-07-15-tushare-capability-probe.md` |
| 真实 backfill | 已完成 | 2026-07-12 35/35 行情刷新成功（ok=35, degraded=0, failed=0） |
| 真实 daily | 最近一次自然运行成功 | 2026-07-14 写入 35 条，L3 覆盖 35/35，Telegram 与 Sheets 成功；2026-07-15 readiness 已重新放行 |
| 生产数据规模 | A 1681 条、B 73 条；A 30d/60d 已结案 953/253 | 2026-07-15 对 `tracker.db` 只读查询；最新 score_date=2026-07-14 |
| Framework B | report-only | `SUPPORTED_FRAMEWORKS={"A"}`，不写 B 生产 predictions |
| Phase 6 阻塞 | B label 已结案样本不足 | 数据质量门槛 OK；B label `0/20`，最早可评估日期 `2026-08-13`（accuracy-report 生成于 2026-07-15） |
| L3 v2 QFQ / 生产写入 | 已完成，选择性待观察 | `daily_bars.adjusted='qfq'` 覆盖 35 个代码、4585 行（至 2026-07-13）；v2 记录 70 条且 70/70 pass，两天 strong 均为 18/18 通过门禁 |
| L3 v2 Phase 3 push trigger | 已完成，生产推送已切换 | `telegram_push.py` 触发条件 `entry_signal=1` → `l3_v2_signal=1`；commit `e080f15`；298/298 tests passed |
| Framework A 倒置诊断 | 已完成，结论：不调权重 | Q5 avg_alpha_30d=-9.91%；根因=截面校准偏差+11支伪复制；agy投资审查：Priority 1=延伸60d/90d；60d首批到期 2026-07-14 |
| 定性评分 v2 | MILESTONE-002 已完成 | contract/types/taxonomy/schema/prompt/validator 齐全；P1/P2 边界加固完成，AGY 严格复审 PASS；未接生产 |
| 质量门禁 | 全部通过 | 450 passed；Ruff lint/format、mypy、`git diff --check` PASS |

## Spec Ledger

| Spec / Plan | 状态 | Owner | 下一动作 | Exit criteria |
|---|---|---|---|---|
| `docs/evolution-roadmap.md` | v1.12 当前基线 | Hermes PM | 随 Phase 状态变化更新 | 和真实系统状态一致 |
| `docs/plans/2026-06-26-phase6-report-only-next-steps.md` | active | Hermes PM | 继续 P3-B 周度复核 | B label review 前 report-only 流程稳定 |
| `docs/specs/2026-07-02-weekly-pm-loop-automation-spec.md` | implemented；2026-07-15 parser 误报已修复 | Hermes PM + agy review | 观察下一次自然摘要 | 每周一自动 Telegram 摘要可信，不重复告警，不越权启用生产化 |
| `/home/lin/a-stock-lib/docs/plans/2026-07-01-three-project-next-work-plan.md` | active cross-project plan | Hermes PM | 按 P0/P1/P2 顺序推进共享包、tracker、research 联动事项 | 三项目版本/文档/任务边界一致 |
| `docs/runbooks/market-data-provider-recovery.md` | active | Hermes PM | 若 readiness/cron 语义变更则同步 | HOLD/READY 行为与 `cron-setup.sh` 一致 |
| `docs/reviews/2026-07-15-tushare-capability-probe.md` | latest probe evidence，全部 PASS | 系统探测 | 按 freshness 门禁定期刷新 | `check_market_data_readiness.py --scope cron` 当前 READY |
| `accuracy_report.txt` | latest tracked report（生成于 2026-07-15） | pipeline | 按周更新 | Phase 6 仍明确 report-only |
| `docs/specs/2026-07-08-l3-v2-entry-signal-spec.md` | draft 历史父 spec；其 Phase 2/3 子 spec 已实施 | Hermes PM + agy review | 观察 v2 生产数据，不再执行旧 NEED_QFQ 下一步 | 规则变更需另开审查，不回写历史分数 |
| `docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md` | approved；MILESTONE-002 已完成 | Hermes PM + codex + AGY review | 保持生产隔离；MILESTONE-003~006 分别待批 | fixture-first 全部通过；不得自动进入 shadow/cutover |
| `docs/plans/2026-07-08-l3-v2-offline-backtest-plan.md` | implemented，历史 | Hermes PM + agy review | 保留追溯；生产已采用后续 BaoStock QFQ 方案 | 脚本只读 `tracker.db`，独立 review gate 通过 |
| `docs/reviews/2026-07-08-l3-v2-backtest-report.md` | 历史 L3 v2 offline evidence | offline script | 不再把其中 NEED_QFQ 当当前 gate | 后续生产 QFQ 事实以 DB、Phase 2/3 spec 和运行日志为准 |
| `docs/reviews/2026-07-10-l3-v2-backtest-retro.md` | task retrospective | Hermes PM | 规则或采集链路变更前阅读 | 防止重复踩 token/限频/去重/qfq volume 问题 |
| `docs/specs/phase3-l3-v2-push-trigger-switch.md` | implemented | Hermes PM + agy review | 已上线，下一步观察 l3_v2_signal 推送分布 | Phase 3 全量测试通过（298/298），可选 Phase 3.1 accuracy-report 信号分布 section |
| `docs/reviews/2026-07-12-framework-a-inversion-diagnosis.md` | final | Hermes PM + agy review | 当前已有 253 条 A 60d 结案，执行只读延伸评估；90d 继续等待 | 五分位 60d/90d alpha 对比完成，不顺手调权重 |

## Open Blockers

| Blocker | Impact | Unblock condition | ETA |
|---|---|---|---|
| B label 已结案样本不足 `0/20` | 阻止 Framework B 生产化 | 已结案 ≥20 且 overdue=0 | 最早 2026-08-13 后 |
| qualitative v2 后续里程碑未授权 | MILESTONE-002 只能保持本地 fixture-first，不进入真实 shadow/cutover | 另行批准 evidence source、shadow persistence 与生产边界 | 待用户决策 |
| Framework A strong 层级尚未证明优于基准 | 不宜调权重或宣称模型有效 | 另开权重复核 spec | 待更多样本与独立审查 |
| L3 v2 当前未体现过滤选择性 | 2026-07-13/14 共 70/70 pass，strong 18/18 每日全部通过 | 先增加 v2 状态/门禁分布报告并积累 30/60d outcome；不据两天样本改规则 | P2 |
| Framework A 60d/90d 延伸评估未执行 | “持有期错配”假说尚未复核 | 当前已有 A 60d 结案 253 条；先做只读五分位对比，90d 继续等待 | P2 |

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
| 2026-07-06 | 最新 `accuracy-report` 同步 | post-fix A 30d 结案 490/100 OK；数据质量 OK；B label 0/20，最早可评估 2026-08-02；L3 30d 已结案 28/30 | Phase 6 继续 report-only；L3 仍等满 30 条后只读复核 |
| 2026-07-09 | 当前工作区测试验证 | `.venv/bin/python -m pytest -q`：`250 passed`；`git diff --check` 已清理为通过 | 可继续整理未提交文档/测试改动，不恢复 B 生产写入 |
| 2026-07-10 | L3 v2 offline backtest | 只读脚本、报告、artifacts、AGY 复审完成；脚本自动读取 `.env` token；修复 20 交易日冷却口径和 qfq volume 复权；`py_compile`、`ruff`、`git diff --check` 通过；AGY APPROVE | 当前决策 NEED_QFQ；阻塞为 Tushare `adj_factor` 限频，不是 token 缺失；下一步单独设计 qfq 分批/缓存方案 |
| 2026-07-12 | Phase 5 L3 v2 Phase 2+3 对齐；Framework A 倒置诊断；Tushare probe 刷新；cron 恢复 | Phase 3 推送触发已切换（commit `e080f15`，298/298 passed）；Framework A 倒置 4 假设诊断完成（Q5 avg_alpha=-9.91%，11只伪复制，不调权重）；agy 投资视角审查完成（Priority 1=60d/90d延伸）；probe 刷新 `READY_CRON`；market-data-backfill ok=35；cron-setup.sh 恢复 daily+outcome-update | 等 2026-07-14 首批 60d 入库后跑延伸评估；Phase 3.1（accuracy-report 增加 l3_v2 信号分布）可选 |
| 2026-07-15 | 项目状态对齐与 P0/P1 修复 | 当天 probe 全 PASS，恢复 READY_CRON 并重装五项 cron；weekly-PM 零失败/降级 warning 分级已修复，真实 dry-run WARN；mypy 24 errors 清零、31 files format 基线化；完整测试与所有质量 gate 通过 | 下一步 task 2.3；L3 v2/Framework A 做只读观察；Framework B 继续 report-only |
| 2026-07-15 | 定性评分 v2 MILESTONE-002 fixture-first 合同 | types/taxonomy/schema/prompt/validator 齐全；99 项定向测试、404 项全量测试与全部质量门禁通过；AGY 只读审查 PASS、无 Critical/Important | 宣布 MILESTONE-002 完成；保持生产隔离，后续 shadow/cutover 需单独批准 |
| 2026-07-15 | 定性评分 v2 P1/P2 边界加固 | 中央 contract、typed-context 复验、有限 JSON/canonical date、64 项及字节/字符上限全部落地；原四个 fail-open 探针均转为拒绝；145 项定向、450 项全量测试和 AGY 严格复审 PASS | 关闭生产就绪审查全部 P1/P2；继续保持 fixture-first 与生产隔离 |
| 2026-08-13 后 | B label 30d 结案、overdue、行业覆盖、B-A delta | 待执行 | 满足门槛后写 `phase6-b-label-review.md`，不直接上线 |

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

python3 scripts/offline_l3_v2_backtest.py \
  --db tracker.db \
  --start 2025-01-01 \
  --end 2026-07-08 \
  --output docs/reviews/2026-07-08-l3-v2-backtest-report.md \
  --artifacts-dir docs/reviews/l3-v2-backtest-artifacts \
  --allow-tushare-fetch \
  --preload-trading-days 120
```

## 禁止事项

- 不恢复 Framework B 生产写入。
- 不修改 `weights.json`。
- 不把 B provisional thresholds 用作交易或 Telegram 推送门槛。
- 不绕过 B label `0/20` 阻塞项。
- 不在 Phase 6 report-only 任务中顺手改评分权重、schema 或 watchlist。
- 不在 qfq 覆盖不足或 `QFQ_ALIGNMENT_FAILED` 影响 buy_strong 子集时输出 `GO_TDD`。
- 不用快速循环请求 Tushare `adj_factor` 绕过限频；先设计显式限速、缓存或分批方案。
