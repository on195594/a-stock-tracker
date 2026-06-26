# Phase 6 Report-only 深化下一步计划

创建时间：2026-06-26
状态：approved-for-report-only

## 当前事实基线

- 行情恢复门禁已通过：`scripts/check_market_data_readiness.py --scope cron` 返回 `READY_CRON`。
- cron 已恢复为 managed block：weekly / daily / outcome-update 均使用 canonical 时间。
- 真实 backfill 已完成：`market-data-backfill` 行情刷新 `ok=35 degraded=0 failed=0 total=35`，L3 metadata 重算 245 条 prediction。
- 真实 daily 已完成：L3 行情刷新 `ok=35 degraded=0 failed=0 total=35`；当日已有 A 框记录，daily 幂等结果为写入 0 条；Sheets sync 成功。
- `accuracy_report.txt` 当前结论：Phase 6 可继续 report-only 深化；仍不要启用生产写入。
- 当前阻塞：B label 自然结案样本不足，`0/20`，最早可评估日期 `2026-07-26`。

## 目标

在不写入 Framework B 生产 `predictions` 的前提下，把 Phase 6 的 report-only 观察变成稳定、可审计、可复查的月度/周度流程，为 2026-07-26 之后的 B label 复核做准备。

## 非目标

- 不恢复 Framework B 生产写入。
- 不修改 `weights.json` 的生产权重。
- 不把 B provisional thresholds 用作交易或 Telegram 推送门槛。
- 不绕过 `accuracy_report` 中的 `B label 已结案样本不足（0/20）` 阻塞项。

## P3-A：固化 B label report-only 观察口径

执行条件：立即可做。

任务：

1. 保持 `accuracy-report` 的 B label tracking 只读 A 框 outcome 代理，不写 `predictions`。
2. 在报告中持续输出：
   - B label 样本数；
   - 已结案 30d 数；
   - 未来可结案数；
   - 到期但 outcome 为空风险；
   - 最早可评估日期；
   - 分 label 行业覆盖。
3. 若 `已结案 < 20`，报告必须继续输出“禁止解释命中率/胜率”。

验收：

```bash
.venv/bin/python pipeline.py accuracy-report
```

报告中必须保留：

```text
B label outcome 自然结案（非金融质量候选）：WAIT
B label 阈值/命中率解释：禁止
Phase 6 生产化阻塞项：B label 已结案样本不足
```

## P3-B：建立每周 report-only 复核节奏

执行条件：cron 已恢复，weekly / daily / outcome-update 正常运行至少一轮后。

任务：

1. 每周一检查上一周 cron 日志：
   - `logs/daily.log`
   - `logs/outcome.log`
   - `logs/weekly.log`
2. 每周运行一次：

```bash
.venv/bin/python pipeline.py accuracy-report
```

3. 只提交报告变化，不提交数据库或日志。
4. 若出现以下任一情况，停止 Phase 6 深化并先治理数据链路：
   - `price_at_score` 覆盖率连续两个交易日低于 95%；
   - L3 覆盖率低于 90%；
   - B label 到期但 outcome 为空风险 > 0；
   - `check_market_data_readiness.py --scope cron` 返回 `HOLD_CRON`。

验收：

- `accuracy_report.txt` 更新后仍明确 report-only；
- git diff 只包含报告或文档变化；
- 不出现 Framework B 新生产记录。

## P3-C：2026-07-26 后的 B label 复核门槛

执行条件：`accuracy-report` 显示 B label 已结案 30d 样本 ≥ 20，且 overdue 风险为 0。

任务：

1. 复核分 label 的 30d 超额命中率和 alpha，但仍不得直接生产化。
2. 检查行业集中度，避免单一行业驱动结论。
3. 对比 A 框同代码最新记录，确认 B-A delta 与 outcome 方向是否一致。
4. 写一份只读评审：`docs/reviews/YYYY-MM-DD-phase6-b-label-review.md`。

进入生产化设计前必须满足：

- B label 已结案 30d 样本 ≥ 20；
- 到期缺失 outcome = 0；
- 至少两个 label bucket 有样本；
- 结论经独立审查通过；
- 另写 spec，不直接改 `weights.json` 或 `scorer.py`。

## 建议下一步

1. 现在只提交本计划。
2. 下一轮工作先补一个小型只读检查脚本或测试，验证“Framework B report-only 不写 predictions”的边界。
3. 等 cron 自然运行一轮后，再做 P3-B 周度复核。
