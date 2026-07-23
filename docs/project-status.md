# a-stock-tracker Project Status

创建时间：2026-06-26
最近更新：2026-07-24
状态：active PM control page

## 当前结论

主业务仍处于 **Phase 6 report-only 观察**，Framework B 不得生产写入；Phase 5 L3 v2 已完成 QFQ 生产接入和 Telegram 主推切换。定性评分 v2 继续保持全局 `on` 和逐股/逐维 fallback。2026-07-21 另完成 TuShare 估值/市值、通用财务和分红三域生产强切：35 股经独立 shadow/readiness 单事务物化，运行时为 `a-stock-lib==0.4.1`，历史 predictions 未改写。该数据换源不代表 Framework A 有效性、Framework B 生产化或下游 consumer 迁移已完成。

2026-07-23 修复 prediction 解释链路的数据一致性：新 prediction 原子保存实际采用的定性分值、逐维 `v1/v2` 来源和 `v1/v2/hybrid_v2` 模式；Telegram 展示与 reviewer 只使用该行快照，旧行缺失或快照损坏时 reviewer fail-closed，不再用最新 legacy 定性分值解释历史/混合总分。迁移只增加 nullable 列，不回填或改写历史 prediction。

同日完成 cron/reviewer 可靠性加固：`cron-alert-wrap.sh` 对 label 与 `--alert-exit-2` 采用顺序无关且拒绝重复/未知参数的解析；`cron-setup.sh` 只按活动、本项目、去除行尾注释后的命令语义识别和清理 daily 变体，保留其他项目任务及 managed block 外注释；HOLD 输出在最终分支确定后明确区分“保留既有评分链”和“不从零新增”，不再先宣称移除后又保留；fake rollback 从不同 live/snapshot 内容验证字节级恢复。Gemini reviewer 对直接 timeout 与 `URLError(timeout)` 复用既有 3 次/2s+4s 有界重试，非 timeout、鉴权和解析错误继续立即 fallback。测试全程使用 fake crontab/curl/urlopen，无真实网络。

2026-07-23 行情链路进一步强切为 TuShare-only：35/35 股票通过 `tushare.pro_bar(adj="qfq")` 补至各 139 行；生产 `daily_bars` 删除 2,385 条非 TuShare 行并替换旧 QFQ，`market_data_audit` 删除 25 条 BaoStock 历史审计；切换前后 1,999 条 predictions canonical SHA-256 保持 `158cbc0e…6e3`。默认/backfill provider 失败即关闭，旧 BaoStock QFQ 采集和双源对账入口已删除；live cron 已确认使用 `fetch_qfq_daily_bars_tushare.py`。历史 outcome 仅做只读评估，不就地改写。

2026-07-24 完成历史 outcome versioned shadow 的治理与 additive production import。冻结 run `outcome-shadow-a38b36b1092589bf7b0a6326` 共 1,776 个结果（1,767 `computed_aligned`、9 `missing_stock_entry`）和 2,520 个来源 observation；生产新增 3 张 shadow 表与 6 个不可变触发器。迁移前备份、事务内核验、独立 post-commit verifier 和副本 drop-only rollback 演练全部通过，1,999 条 predictions 保护 hash 保持 `05e8d556…0f1ce`。本阶段只增加审计层；legacy outcome、accuracy-report、Sheets、Telegram 和 cron 未切换。

M4/M5 研究路线继续保留，但已从生产上线关键路径拆出。36 股 sample、real bundle、Claude blind reference、Gemini shadow 和 support audit 用于研究代表性与 agreement，不再阻塞具备 validator、独立表、逐股 fallback 和 `off` 回滚的生产读取。定性评分 v2 已有 2026-07-20 自然 daily/acceptance PASS；当前生产下一动作是观察 2026-07-21 新 17:15→17:30→17:45 时序和首个周六 TuShare weekly cycle。研究下一动作才是按独立范围继续 real bundle/审计。

2026-07-18 曾执行一次非 v1.3.2-compliant 的 `index_classify` 直连探测：HTTP 200、provider code 0、31 rows，未持久化原始响应。该探测不构成 frame、authorization、freeze、attestation 或生产采用证据。

2026-07-18 13:31 +08:00 的首次轻量 probe 在第 1 次 `index_classify` 停止：HTTP 200、provider code 0、31 rows、`has_more=false`，但 provider `count=0`，违反 count/rows 完整性门禁。实际调用 1/35、无重试，未执行其余 probe/build 请求，未生成 exclusions、frame/sample 或派生 hash；raw/summary 无 token，生产与 shadow 路径未访问。

随后修复非空单页 `count=0` 未知总数哨兵：仅当 `has_more=false` 时接受并记录，其他 mismatch 继续失败。14:59 +08:00 的全新 probe 5/5 通过，聚合行数为 classification 31、首行业成员 126、stock 5,200、daily 5,522；同 run build 在 ordinal 6 的首个补采 `index_member_all` 因 transport failure、未捕获响应而停止。无重试或剩余 29 次补采，仍无 exclusions、frame/sample 或派生 hash；raw/summary 无 token。

15:05 +08:00 的第三个全新 probe 同样 5/5 通过；build 捕获到 ordinal 16 的交通运输行业响应后发现一行 provider 成员代码 `T00018.SH`（`上港集箱(退市)`），不符合六位数字加 `.SH/.SZ/.BJ` 的成员结构规则。按既定门禁整批停止，未改写为单股 exclusion；共捕获 16 个响应，其中 15 个完全验证，无重试或剩余 19 次调用，仍无 frame/sample、派生 hash 或 token 泄漏。

经用户明确授权推翻该单点规则后，退市名称且具有 `.SH/.SZ/.BJ` 后缀的非六位成员代码只进入 `invalid_member_code` exclusion；active 非标准代码和未知后缀仍整批失败。15:13 +08:00 的第四个全新 run 35/35 调用通过：classification 31 行、members 5,864 行、stock 5,200 行、daily 5,522 行；输出 frame 4,694 行、exclusions 1,170 行、sample 36 股，12 cells 均为 3 股。`T00018.SH` 是唯一 `invalid_member_code`；三个派生 hash 复验一致，artifacts 无 token。本结果仅为本地研究 frame/sample，不授权 Reviewer、MILESTONE-005 或生产采用。

2026-07-17 M4 v1.3.1 工程实现：v1.3 `0ad8c6…a12df` 历史字节不变；v2 authorization/artifact schemas、36/2 精确矩阵、父 supervisor deadline、attempt 外 phase journal、raw-first publication、exact-number gates/ledger、date failure nullable evidence、candidate/post-publication 双复验、五文件 generator closure 和 legacy rejection guard 已落地。当前仅有无 token/无 socket 合成执行证据，没有真实 authorization 或 Tushare attempt。

2026-07-16 M4 v1.2 离线实现已完成：协议 hash 以 v1.1 hash 为 `supersedes`，保持 SW2021、seed、36 股分层、coverage/Reviewer/evidence 规则不变；新增 canonical authorization 逐调用复验、HTTPS/POST/禁重定向、raw-first create-only、有限脱敏错误、`complete`/`capability_pass` 分离和全离线复验。固定授权窗口为 2026-07-17 00:00 至 2026-07-18 00:00（Asia/Shanghai），窗口外不得联网。

2026-07-16 19:34 +08:00 的 preflight 返回 `authorization_not_yet_valid`（exit 2），且 capability output root 不存在；真实 probe/Tushare 调用计数仍为 0，authorization/attempt 未消费。来源终态尚未判定，不得提前声明 PASS 或 `NO_QUALIFIED_FRAME_SOURCE`。

2026-07-17 09:29 +08:00 的唯一真实 probe 已完成：三调用各一次、无重试、`complete=true`。`index_classify` 与 `index_member_all` 均因账户无接口权限返回 Tushare `40203`，`daily_basic` 因 `5次/天` 限频返回 `40203`；`capability_pass=false`，离线复验 manifest SHA-256 `70f6d73a4b62f9725b3fe983cbdcf212ded5d84cbdedbe777088793b0c33957a`。M4 已收敛为 `NO_QUALIFIED_FRAME_SOURCE`，保持 research-only；继续必须另开 v1.3 候选协议。

2026-07-15 控制面修复已完成：当天 Tushare probe 的 daily/index/calendar/close cross-check 全部 PASS，readiness 恢复 `READY_CRON`，五项 managed cron 已重新安装；weekly PM loop 已修复中文“失败 0 只”和降级 WARNING 的错误分级，真实 dry-run 从误报 FAIL 恢复为符合当前降级事实的 WARN；mypy 24 errors 已清零，31 个历史文件完成 Ruff format 基线化；MILESTONE-003 当时的完整质量门禁为 `478 passed` 且 lint/format/type/diff 全绿。

2026-07-16 MILESTONE-004 capture-first 加固已完成：在既有 v1.1 离线工具链上新增机器授权、raw-first receipt/blob、complete/incomplete attempt 隔离、resume/recover 和纯离线 assemble，并关闭 legacy 授权绕过、补齐逐调用授权时钟与取消传播。当前全仓门禁为 `593 passed`，Ruff lint/format/F401、mypy、协议 v1/v1.1 SHA-256 和 `git diff --check` 全绿；加固实现阶段未读取真实数据库、未检索真实公司、未访问真实数据源、未运行真实 Reviewer。

2026-07-15 至 2026-07-16 的七次旧 frame 导出均在 publication 前 fail closed；13:34 的 5,525 行旧响应不可恢复。17:15 的 capture-first attempt 首次将已收的 Tushare/SSE 原始响应即时落盘，manifest SHA-256 为 `905fda74cae7a3b916e430e2554c1bce69aee7538cf638cea9d3415b62eb535d`；但因 Tushare 响应 raw-only 且 31 项 SWS missing，该 attempt 只能保持 incomplete，frame/sample 尚未冻结。

2026-07-10 的 `NEED_QFQ` 是历史离线 gate。2026-07-12 最初通过 BaoStock QFQ 采集解除；2026-07-22 当前采集入口已切换为 TuShare `scripts/fetch_qfq_daily_bars_tushare.py`：`daily_bars` 中 QFQ 覆盖 35/35 代码，生产 daily 在 2026-07-13/14 写入 70 条 v2 记录，Telegram 主推使用 `l3_v2_signal=1`。

2026-07-01 已完成共享包版本对齐：本项目锁定并验证 `a-stock-lib==0.2.0`，继续只保留 tracker 专属的环境门禁、SQLite 缓存、cron 编排和评分管道。

2026-07-02 weekly PM loop 已完成：daily/outcome 自然运行正常，`probe_tushare_market_data.py` 已刷新当天 report，`READY_CRON` 恢复。随后已定向补齐 watchlist 后 14 支股票的基本面缓存并重跑 `accuracy-report`；数据质量门槛恢复 OK，Phase 6 仍因 B label 已结案样本不足保持 report-only。

2026-07-02 同步 a-stock-research v2.5.0"历史分位极低须做反向解读检验"教训到 Framework B dry-run 报告：新增 `roe_latest`（最新单年ROE，同花顺年度数据，非季度）字段抓取，`a_stock_tracker/reporting/framework_b_report.py` 在 `roe_3y_avg` 显著高于 `roe_latest` 时给候选行追加 `⚠️ROE趋势预警` 文本标注。**纯报告层展示，不改变任何打分数值/权重/排序**（不碰 `weights.json`，不改 `score_stock` 调用参数），符合本文档"禁止事项"里"不修改 weights.json"、"不顺手调评分权重"的边界。Framework B 仍 report-only，不受影响。

P0 根因已定位：2026-06-27 `weekly` 实际卡在第 22 只 `002119` 的外部行情调用中，进程残留到 2026-07-02，导致后 14 支股票缓存停留在 2026-06-20 并超过 168h TTL。`fetcher`/`weekly` 的进程级超时保护已完成（commit `89fd7c5`，验证 `208 passed`），后续通过自然 cron 观察是否复发。

2026-07-02 Phase 6 weekly PM loop 已自动化：新增 `scripts/weekly_pm_loop.py`，每周一 09:30 由 cron 运行，复核 weekly/daily/outcome 日志、`READY_CRON` 和 `accuracy-report`，并通过 Telegram bot 发送摘要。实现前已写 spec 并经 agy 独立审查 PASS；实现 commit `f181010`，验证 `211 passed, 1 skipped`。2026-07-13 首轮自然运行暴露的 failure-marker 误报已于 2026-07-15 修复并补测试。

## Active Phase

| Phase | 状态 | 当前动作 | 下一检查点 | Exit criteria |
|---|---|---|---|---|
| Phase 4 验证基础 | 已完成，持续观察 | weekly PM loop 自动检查 accuracy-report；不在此阶段顺手调权重 | 每周一自动摘要 | 若要调权重，另开 spec |
| Phase 5 L3 买点层 | v1 保留审计；v2 Phase 2+3 已完成 | 观察 v2 信号分布与自然 outcome；不改 L1/L2 分数 | readiness 已恢复，继续自然运行 | 足量 v2 30/60d 样本和独立复核后再讨论规则变化 |
| TuShare 三域生产主源 | 已完成并验证 | 观察 17:15 daily 与周六 10:00 weekly 自然日志；保持 registry/runbook 同步 | 下一次自然 daily/weekly cycle | 35/35 readiness、原子物化、来源审计、回滚与真实 cron smoke 均通过 |
| 历史 outcome versioned shadow | 已完成生产 additive import | 保持 shadow append-only；报告继续读取 legacy outcome | 若要切报告，另开 consumer/cutover spec | 1 run、1,776 results、2,520 observations、3 表 6 触发器；backup/rehearsal/post-commit proof 可复验 |
| 定性评分 v2 MILESTONE-002 | 已完成 | 保持合同稳定和本地 validator fail-closed | MILESTONE-003 已独立完成 | REQ-001~035 对应本地合同齐全 |
| 定性评分 v2 MILESTONE-003 | 已完成 | 使用独立 CLI/JSONL artifact；不接 pipeline 或生产 DB | M4 sample 已完成；转 M5 数据 Sprint | REQ-036~039 文件持久化、错误分类、脱敏、去重和隔离测试及 AGY 最终只读审查通过 |
| 定性评分 v2 MILESTONE-004 | 轻量 frame/sample 完成 | 35/35 调用、4,694 frame、36 sample、12 cells 完整 | 在新授权下构建 corpus/coverage | sample 固定且不替换；八层 coverage 决定能否进入 real bundle |
| 定性评分 v2 生产读路径 | 全局 `on`；2026-07-20 自然 acceptance PASS | 继续由 managed acceptance 复验 6 股 hybrid、29 股 fallback、历史 seal 与 `off` 回滚 | 新 17:30 时序首次自然运行 | 35/35 正常完成；无批量异常、历史改写或错误 v2 采用 |
| 定性评分 v2 MILESTONE-005 | fixture-first 与离线 builder 完成；发布后研究 | real bundle/blind reference/support audit 与生产解耦 | 独立安排研究批次 | 只形成研究证据，不静默改变生产合同、权重或 fallback |
| Phase 6 多框架激活 | report-only 观察 | 自动化 weekly PM loop 只读观察 B label / dry-run / cron 日志 | 2026-08-13 后首次 B label 自然结案复核 | B label 已结案 ≥20、overdue=0、数据质量门槛 OK、独立审查通过、另写生产化 spec |
| Phase 7 选股宇宙扩展 | 未启动 | 等 Phase 6 或明确降级策略 | 暂无 | 单独设计动态池和 API 压测 |

## 当前事实基线

| 项目 | 当前状态 | 证据 |
|---|---|---|
| 运行依赖 | `a-stock-lib==0.4.1` | 实际 import 0.4.1，`pip check` 无破损依赖 |
| 行情 provider | TuShare-only，失败即关闭 | 默认/backfill 均不实例化第二行情源；旧 BaoStock-only 环境变量无效 |
| readiness | market-data probe stale；TuShare 三域 35/35 READY | 两套 readiness 独立，旧报告不得冒充当前 `READY_CRON` |
| cron | weekly / weekly-PM / QFQ / primary-daily / daily / acceptance / outcome 已安装 | `00 16` QFQ、`15 17` primary、`30 17` daily、`45 17` acceptance、`00 18` outcome |
| TuShare 三域生产 | 已完成 | shadow 215 completed/0 failed；35 行物化；来源/评分错误 0；两库 quick_check=ok |
| 真实 backfill | 已完成 | 2026-07-23 TuShare QFQ 35/35，各 139 行，日期缺口 0 |
| 真实 daily | 2026-07-20 自然运行成功 | 写入 35 条；17:45 acceptance `PASS`，6 hybrid/29 fallback，`rollback_verified=true` |
| 生产数据规模 | predictions 1,999 条（A 1,926 / B 73）；shadow results 1,776 条 | 2026-07-24 `quick_check=ok`；shadow import 前后 predictions 保护 hash `05e8d556…0f1ce` 不变 |
| Framework B | report-only | `SUPPORTED_FRAMEWORKS={"A"}`，不写 B 生产 predictions |
| Phase 6 阻塞 | B label 已结案样本不足 | 数据质量门槛 OK；B label `0/20`，最早可评估日期 `2026-08-13`（accuracy-report 生成于 2026-07-15） |
| L3 v2 QFQ / 生产写入 | 已完成，选择性待观察 | `daily_bars.adjusted='qfq'` 仅 `tushare.pro_bar.qfq`，35 个代码、4,865 行（至 2026-07-23）；非 TuShare 或 mixed source fail-closed |
| L3 v2 Phase 3 push trigger | 已完成，生产推送已切换 | `telegram_push.py` 触发条件 `entry_signal=1` → `l3_v2_signal=1`；commit `e080f15`；298/298 tests passed |
| Framework A 倒置诊断 | 已完成，结论：不调权重 | Q5 avg_alpha_30d=-9.91%；根因=截面校准偏差+11支伪复制；agy投资审查：Priority 1=延伸60d/90d；60d首批到期 2026-07-14 |
| 定性评分 v2 | 全局 `on`；6/35 hybrid、29/35 v1 fallback | v2 表 6 行：000963/002050/600036/600900/601088/603606；全部为 moat/market_pos scored、sentiment NULL |
| 定性评分 v2 研究审计 | M4 sample 与 M5 fixture-first/builder 完成；real bundle 未完成 | sample SHA `b278a7…d7635d`；不再作为当前生产读取阻塞项 |
| 质量门禁 | 全部通过 | 2026-07-24 Phase 2 最终全仓 1,112 passed；Ruff、format 165 files、mypy 155 source files、pip check、shell syntax、`git diff --check` PASS |

## Spec Ledger

| Spec / Plan | 状态 | Owner | 下一动作 | Exit criteria |
|---|---|---|---|---|
| `docs/evolution-roadmap.md` | v1.30 当前基线 | Hermes PM | 随 Phase 状态变化更新 | 和真实系统状态一致 |
| `docs/specs/2026-07-21-tushare-three-domain-forced-cutover.md` | implemented；三域生产合同 | Hermes PM + codex | 观察自然 cycle；字段/时序变化同步 registry/runbook | 35/35、原子物化、回滚和真实 smoke 均可复验 |
| `docs/specs/2026-07-23-tushare-only-market-data-cutover.md` | implemented；活动行情强切合同 | Hermes PM | 观察自然 daily/outcome；历史 outcome 治理另开 spec | TuShare-only、35/35 QFQ、predictions hash 不变、回滚证据与独立审查 |
| `docs/reviews/2026-07-23-tushare-only-outcome-impact-review.md` | final；只读影响评估 | Hermes PM | 不改写历史；若治理则建立 versioned shadow | BaoStock 成功写入下界和 TuShare shadow 差异均披露 |
| `docs/specs/2026-07-23-outcome-shadow-phase1.md` | implemented；历史 outcome 冻结/重建合同 | Hermes PM + codex | 保持 run/schema/hash 不可变 | v4 candidate/replay/reconciliation/manifest 与独立复审可复验 |
| `docs/specs/2026-07-24-outcome-shadow-phase2.md` | implemented；生产 additive import 合同 | Hermes PM + codex | consumer/report 切换需另开 spec | backup、四态幂等、单事务导入、post-commit verifier 和副本回滚均通过 |
| `docs/reviews/2026-07-24-outcome-shadow-phase2-stage-a-review.md` | final；`APPROVE_LANDING` | independent Codex | 无 P0/P1/P2；保留生产 proof | 工具 commit `1ad2016` 与 rehearsal `...002120` 绑定 |
| `docs/reviews/2026-07-23-tushare-only-final-independent-review.md` | final；Claude Code 替代 AGY 独立只读复审，APPROVE | Claude Code | 无 blocker；P2 已处置或按 spec 保留 | P0/P1 none，仓库 hash 无漂移，最终门禁复验通过 |
| `docs/specs/2026-07-23-prediction-qualitative-snapshot-reviewer-fix.md` | implemented；prediction 解释快照合同 | Hermes PM | 观察下一次自然 daily；旧行继续 fail-closed | 新行快照与 total_score 同事务；历史行不回填；reviewer 不再拼接最新 legacy 值 |
| `docs/specs/2026-07-23-cron-reviewer-reliability-fix.md` | implemented；cron 语义去重与 reviewer timeout 合同 | Hermes PM | 安装后观察下一次自然 cron；不真实触发 Gemini | 安装幂等且不改无关 crontab；timeout 最多 3 次；非 timeout 立即 fallback |
| `docs/runbooks/tushare-primary-production.md` | active | 运维 | daily/weekly 故障与回滚按本手册执行 | 命令与 cron/代码保持一致 |
| `docs/reviews/2026-07-19-qualitative-v2-production-retrospective.md` | final；当前生产复盘 | codex | 下一交易日自然 cron 确认 | 把上线安全、数据覆盖和研究有效性分开管理 |
| `qualitative_v2_production_acceptance_baseline.json` | active；生产验收基线 | codex | v2 adoption 变化时随同审查更新 | checker 只读复验，不自动学习或覆盖基线 |
| `docs/plans/2026-07-18-m5-two-sprint-execution.md` | historical research plan；D1 授权已执行/后续路线已演化 | user + codex | 仅在继续 36 股研究审计时引用 | 不再作为生产上线或扩大预计算覆盖的串行前置 |
| `docs/plans/2026-06-26-phase6-report-only-next-steps.md` | active | Hermes PM | 继续 P3-B 周度复核 | B label review 前 report-only 流程稳定 |
| `docs/specs/2026-07-02-weekly-pm-loop-automation-spec.md` | implemented；2026-07-15 parser 误报已修复 | Hermes PM + agy review | 观察下一次自然摘要 | 每周一自动 Telegram 摘要可信，不重复告警，不越权启用生产化 |
| `/home/lin/a-stock-lib/docs/plans/2026-07-01-three-project-next-work-plan.md` | active cross-project plan | Hermes PM | 按 P0/P1/P2 顺序推进共享包、tracker、research 联动事项 | 三项目版本/文档/任务边界一致 |
| `docs/runbooks/market-data-provider-recovery.md` | active | Hermes PM | 若 readiness/cron 语义变更则同步 | HOLD/READY 行为与 `cron-setup.sh` 一致 |
| `docs/reviews/2026-07-15-tushare-capability-probe.md` | latest historical probe evidence；当前 stale | 系统探测 | 刷新真实 probe 后再评估 | 不引用过期 PASS；以实时 `check_market_data_readiness.py --scope cron` 为准 |
| `artifacts/reports/accuracy-report.txt` | ignored runtime report | pipeline | 按周更新 | Phase 6 仍明确 report-only，运行后不污染 Git 状态 |
| `docs/specs/2026-07-08-l3-v2-entry-signal-spec.md` | draft 历史父 spec；其 Phase 2/3 子 spec 已实施 | Hermes PM + agy review | 观察 v2 生产数据，不再执行旧 NEED_QFQ 下一步 | 规则变更需另开审查，不回写历史分数 |
| `docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md` | approved；生产 hybrid/global read 已实施 | Hermes PM + codex + AGY review | 保持合同稳定；覆盖扩展走预计算批次 | 不得把 fallback 冒充 v2，不得把结构有效性冒充预测有效性 |
| `docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration-v1.1.md` | frozen historical；acquisition route 技术关闭 | codex + AGY review | 保持协议/hash/artifacts 不变；不得 retry/resume/assemble incomplete | seed、URL、Reviewer、Wilson 与 scope disposition 契约均由测试覆盖 |
| `docs/plans/2026-07-16-milestone-004-frame-source-capability-preregistration-v1.2.md` | frozen；执行完成；capability FAIL | codex + Claude review PASS | 无；attempt 已消费 | `NO_QUALIFIED_FRAME_SOURCE`；继续需另开 v1.3 |
| `docs/plans/2026-07-17-milestone-004-segmented-rest-preregistration-v1.3.1.md` | frozen implementation contract；无真实授权 | codex | 保持 hash/provenance；仅在新外部授权后可执行 | capability/date/capture 分别授权；本阶段不联网、不组装 |
| `reviews/milestone-004-audit-v1.2/capability-probe-2026-07-17.md` | final；complete=true/capability_pass=false | codex | 保留 hash 与 non-adoptable package；不得重试 | manifest 离线复验通过；终态已收敛 |
| `reviews/milestone-004-audit-v1.1/execution-authorization.md` | historical；相关 live attempts 已消费 | user + codex | 无；以 v1.1 closeout 和 v1.2 gate 为当前控制面 | 不访问生产 DB，不复用旧授权，不运行 Reviewer，不接生产 pipeline |
| `docs/runbooks/qualitative-v2-shadow.md` | active | codex | 仅对获批 context 使用显式 `--execute` | JSONL 隔离、同 hash 幂等、无生产副作用 |
| `docs/runbooks/qualitative-v2-m5-fixture-first.md` | implemented；real build offline、model execution synthetic only | codex | D1~D3 完成后构建并 preview real bundle | 外部模型 adapter 仍须 exact execution approval |
| `docs/plans/2026-07-08-l3-v2-offline-backtest-plan.md` | implemented，历史 | Hermes PM + agy review | 保留追溯；生产已采用后续 BaoStock QFQ 方案 | 脚本只读 `tracker.db`，独立 review gate 通过 |
| `docs/reviews/2026-07-08-l3-v2-backtest-report.md` | 历史 L3 v2 offline evidence | offline script | 不再把其中 NEED_QFQ 当当前 gate | 后续生产 QFQ 事实以 DB、Phase 2/3 spec 和运行日志为准 |
| `docs/reviews/2026-07-10-l3-v2-backtest-retro.md` | task retrospective | Hermes PM | 规则或采集链路变更前阅读 | 防止重复踩 token/限频/去重/qfq volume 问题 |
| `docs/specs/phase3-l3-v2-push-trigger-switch.md` | implemented | Hermes PM + agy review | 已上线，下一步观察 l3_v2_signal 推送分布 | Phase 3 全量测试通过（298/298），可选 Phase 3.1 accuracy-report 信号分布 section |
| `docs/reviews/2026-07-12-framework-a-inversion-diagnosis.md` | final | Hermes PM + agy review | 当前已有 253 条 A 60d 结案，执行只读延伸评估；90d 继续等待 | 五分位 60d/90d alpha 对比完成，不顺手调权重 |

## Open Blockers

| Blocker | Impact | Unblock condition | ETA |
|---|---|---|---|
| B label 已结案样本不足 `0/20` | 阻止 Framework B 生产化 | 已结案 ≥20 且 overdue=0 | 最早 2026-08-13 后 |
| qualitative v2 仅 6/35 有合法 partial 行 | 不阻断安全读取，但限制 v2 实际覆盖 | 分批复用官方 PDF/离线提取/validator 路线补齐其余 29 股 | P1，按批次推进 |
| 新 TuShare 时序尚缺完整自然运行证据 | 手动 daily cycle 已 PASS，但 17:15→17:30→17:45 尚未由 cron 自然走完 | 查看三份日志和 acceptance 决策；异常按各自 runbook 回滚 | 下一交易日 |
| market-data cron readiness 报告已过期 | 当前 managed cron 继续保留；不能引用旧 PASS 新启用行情能力 | 刷新 capability probe 并按真实结果恢复 `READY_CRON` | 下次行情控制面变更前 |
| 历史 outcome consumer 尚未切换 | versioned shadow 已生产物化，但 accuracy-report/Sheets 仍读取 legacy outcome；450/1,767 差异不能全部归因于 BaoStock | 另开 consumer/cutover spec，明确并行报告、口径标签、回滚和展示边界 | P1；不得就地覆盖 legacy outcome |
| M5 real bundle/blind-reference/support audit 未完成 | 限制研究代表性与 agreement 结论 | 作为发布后独立研究执行 | P2，不阻断当前生产读取 |
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
| 2026-07-15 | 定性评分 v2 MILESTONE-003 文件 shadow seam | 独立 Gemini client/CLI、0600 JSONL、并发锁与同 hash 去重、bounded retry/response/error、可选 legacy comparison 已完成；122 项定向、478 项全量测试和 AGY 最终只读审查 PASS；两次空 packet Gemini 调用均为 `VALID_INSUFFICIENT_DATA`，重复 CLI 运行 `api_called=false` | MILESTONE-003 完成；生产 DB/pipeline/cron/Telegram 保持不变；下一步必须先做 MILESTONE-004 evidence feasibility audit |
| 2026-07-15 | 定性评分 v2 MILESTONE-004 v1.1 离线工具链 | 预注册/hash、确定性 frame/sample、URL/corpus、technical ledger、并发安全 create-only chain、默认禁用且隔离的双 reviewer、裁决和原子 coverage report 已完成；76 项 M4 定向、554 项全量测试与全部质量门禁通过 | 工具链完成但真实 audit 未执行；不读取真实 DB、不检索公司、不运行 Reviewer；下一步需用户单独批准候选 source/dataset 审计授权 |
| 2026-07-15 | M4 静态输入执行授权 | 用户批准仅用带 provenance 的只读静态 dataset 完成 frame/sample/corpus；授权留档 SHA-256 为 `37aee66f8a3dc1a9f4aea61fe53f75cd4556ad2d9316b51b6d4c8b340c16f903` | dataset 尚未提供，不创建空 manifest；Reviewer、生产 DB/live provider/pipeline 继续禁止 |
| 2026-07-18 | M4 sample + M5 fixture-first | 4,694 frame、36 sample 已冻结；M5 orchestration、artifact seals、聚合 report、离线 bundle、D1 preflight 和只读 snapshot builder 已实现，51 项定向/866 项全量通过 | 批准 SHA `93c471…c0cf1` 后才 seal/执行 D1；不提前执行 Reviewer/Claude/Gemini |
| 2026-07-19 | 定性评分 v2 生产闭环与全局读模式 | 东方电缆及指定五股形成 6 行合法 partial v2；模式切到 `on`；只读验证 35/35 返回有效，6 股 hybrid、29 股 v1 fallback；predictions 保持 1,859 行 | 下一交易日自然 cron 确认；随后分批扩大覆盖，M5 研究审计独立推进 |
| 2026-07-19 | 自动化生产验收与回滚检查 | tracked baseline 封存历史评分字段；真实只读预检 PASS；`off` 证明 35 股均走 v1 且不访问 v2 DB | 下一交易日以 `--require-score-date` 验收 daily 行数和 adoption 日志 |
| 2026-07-19 | 自动化验收 managed cron 与告警 | 工作日 16:45 在 daily/outcome-update 之间执行 `--require-today`；真实 crontab 已去重安装 1 条；验收 exit 2 显式发送 `ROLLBACK` Telegram 告警，weekly PM 的 exit 2 去重语义不变；全仓 937 passed | 观察 2026-07-20 首次自然 cron 日志与告警链路；重跑 `cron-setup.sh` 前刷新已过期的 readiness |
| 2026-07-19 | P2 pytest/shadow 幂等修复 | pytest 项目根路径由 `pyproject.toml` 固定；shadow record/request key 均增加 model；跨模型回归用例通过；文档入口全仓 938 passed | 关闭两个 P2；不同模型可共享 artifact 文件但不会复用彼此结果 |
| 2026-07-21 | TuShare 估值/财务/分红三域生产强切 | 0.4.1；35/35 readiness；35 行原子物化；两库 quick_check=ok；predictions 1,894 行/hash 不变；真实 cycle run 215 成功；两次中间回滚均无错误 | 切换完成；观察新 cron 自然时序，刷新 stale market-data probe；下游 consumer 仍独立迁移 |
| 2026-07-23 | TuShare-only 行情强切与历史标签只读评估 | 35/35 QFQ 各 139 行；非 TuShare 行/审计清理；predictions 1,999 行/hash 不变；live cron 使用 TuShare 脚本；历史 outcome 450/1,767 与当前 TuShare shadow 不一致 | 活动链路切换完成；历史标签不改写，另开 provenance/versioned shadow 治理 |
| 2026-07-24 | 历史 outcome Phase 1+2 versioned shadow | v4 candidate/replay 与副本 import/replay/revert 通过；工具 commit `1ad2016`；生产 additive import 后 3 表 6 触发器、1,776 results、2,520 observations，post-commit verifier PASS | 审计 shadow 已落地；legacy outcome 与所有 consumer 保持不变，consumer 切换另开 spec |
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
- 不把 `QUALITATIVE_V2_MODE=on` 描述成 35 股已有 v2 数据，也不把 v1 fallback 计为 v2 覆盖。
- 不让 M5 研究 agreement 门禁静默阻塞已有独立表、逐股 fallback 和 `off` 回滚的生产读路径。
