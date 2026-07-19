# Phase 6 weekly PM loop 自动化 spec

日期：2026-07-02
状态：revised-for-agy-review
Owner：Hermes PM
目标仓库：`a-stock-tracker`

## 背景

Phase 6 当前仍处于 report-only 观察期。现有手工 weekly PM loop 每周检查：

- `logs/weekly.log`
- `logs/daily.log`
- `logs/outcome.log`
- `scripts/check_market_data_readiness.py --scope cron`
- `pipeline.py accuracy-report`

2026-07-02 的手工复核已经发现并修复过 weekly 卡死导致的基本面缓存过期问题。为了避免后续周度复核依赖 PM 记忆，本 spec 将该 loop 固化为本机 cron 自动化，并通过 Telegram bot 推送摘要。

## 目标

1. 每周自动运行 Phase 6 PM loop。
2. 自动生成本地审计日志和最新摘要。
3. 通过 Telegram bot 推送成功/失败摘要。
4. 遇到数据链路风险时发出明确告警，但不自动修改评分、权重、watchlist 或 Framework B 生产配置。

## 非目标

- 不恢复 Framework B 生产写入。
- 不修改 `SUPPORTED_FRAMEWORKS`、`weights.json`、watchlist、schema 或历史 predictions。
- 不自动提交运行产物或任何文档变化。
- 不在测试中访问真实 Telegram、Tushare、BaoStock、Gemini 或 Google Sheets。
- 不替代 2026-07-26 后的 B label 人工复核；自动化只提醒是否达到复核条件。

## 调度设计

新增 `scripts/weekly_pm_loop.py`，由 cron 每周一 09:30 运行：

```cron
30 9 * * 1 /home/lin/a-stock-tracker/cron-alert-wrap.sh "cd /home/lin/a-stock-tracker && .venv/bin/python scripts/weekly_pm_loop.py" weekly-pm-loop >> /home/lin/a-stock-tracker/logs/weekly-pm-loop.log 2>&1
```

选择周一 09:30 的理由：

- 周六 10:00 的 `weekly` 已完成，有足够时间暴露卡死或失败。
- 工作日 16:30/17:00 的 `daily`/`outcome-update` 不会与该检查重叠。
- PM 能在周一盘前/盘中看到告警，不影响周末基本面刷新。

`cron-setup.sh` 应把该规则纳入 managed block，并保留 readiness 行为：

- `weekly-pm-loop` 始终配置。
- `daily` / `outcome-update` 仍只在 market data readiness 通过时配置。
- readiness 未通过时，PM loop 仍运行并推送 HOLD 摘要。
- managed block 清理逻辑必须能清理历史散落的 `weekly_pm_loop.py` 规则，避免多次重跑 `cron-setup.sh` 后重复调度。

## 凭证与通知

复用项目根目录 `.env`：

```text
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

发送策略：

- token/chat_id 缺失：脚本退出码为 0，本地日志记录 `telegram_skipped`，避免无凭证环境持续制造 cron 失败。
- Telegram HTTP/网络失败：脚本退出码为 1，让 `cron-alert-wrap.sh` 尝试二次告警；本地日志保留失败原因。
- PM loop 运行正常但业务检查为 WARN/FAIL，且详细摘要已成功发送：脚本退出码为 2，避免 `cron-alert-wrap.sh` 再发送重复告警。
- `cron-alert-wrap.sh` 必须跳过 exit code 2 的二次 Telegram 告警，但保留非 0 退出码用于 cron 日志审计。

Telegram 文案必须包含：

- 标题：`Phase 6 weekly PM loop`
- 运行日期时间
- 总体状态：`OK` / `WARN` / `FAIL`
- readiness 结果：`READY_CRON` / `HOLD_CRON` / `UNKNOWN`
- cron 日志状态：weekly / daily / outcome
- `accuracy-report` 中的 Phase 6 结论、阻塞项、下一步
- 本地日志路径

## 检查内容

### 1. cron 日志新鲜度

读取以下文件的末尾内容和修改时间：

- `logs/weekly.log`
- `logs/daily.log`
- `logs/outcome.log`

判定：

- `weekly.log` 最近修改时间超过 9 天：`WARN`
- `daily.log` 最近修改时间超过 4 天：`WARN`
- `outcome.log` 最近修改时间超过 4 天：`WARN`
- 任一日志不存在：`WARN`
- 日志尾部出现明显失败关键词：`FAIL`
- 只读取每个日志文件末尾固定窗口，第一版为 10KB。
- 失败关键词只在最新日志日期对应的行内匹配；若尾部无法识别日期，则退化为检查 10KB 窗口但状态最高为 `WARN`，避免旧错误长期制造 `FAIL`。
- 失败关键词匹配不区分大小写。

失败关键词第一版限定为：

```text
Traceback
ERROR
FAILED
失败
崩溃
database is locked
```

### 2. readiness

运行：

```bash
.venv/bin/python scripts/check_market_data_readiness.py --scope cron --allow-stale-days 9
```

判定：

- 退出码 0 且输出包含 `READY_CRON`：`OK`
- 输出包含 `HOLD_CRON`：`WARN`
- 非 0 且无法识别：`FAIL`

`--allow-stale-days 9` 是 PM loop 专用宽限：周一 09:30 运行时不要求 Tushare capability probe 当天刚刷新，避免每周固定误报。`cron-setup.sh` 自身的 readiness 门禁仍保持严格当天新鲜度，不使用该宽限。

### 3. accuracy-report

运行：

```bash
.venv/bin/python pipeline.py accuracy-report
```

该命令更新被忽略的 `artifacts/reports/accuracy-report.txt`，自动化脚本不执行 git 操作。

脚本从 stdout 或 `artifacts/reports/accuracy-report.txt` 提取：

- `Phase 6 readiness`
- `Phase 6 生产化阻塞项`
- `Phase 6 下一步`
- `结论：...`

判定：

- 缺少上述 Phase 6 section：`FAIL`
- 结论仍为 report-only / 暂不生产化：`OK` 或 `WARN`，取决于阻塞项。
- 结论必须包含限制性字样之一：`仍不要启用生产写入`、`暂不进入 Phase 6 生产化`、`暂不进入生产化`。
- 若结论偏离预期模板且缺少限制性字样，视为“可能暗示可以生产化”：`WARN`，提示 PM 另写生产化 spec，不自动启用。

## 输出文件

新增：

- `scripts/weekly_pm_loop.py`
- `tests/test_weekly_pm_loop.py`

运行时生成或更新：

- `logs/weekly-pm-loop.log`
- `logs/weekly-pm-loop-summary.txt`

不纳入 git：

- `logs/`
- `artifacts/`
- `.env`

## 实现约束

- 使用 Python 标准库实现，不新增依赖。
- Telegram 发送使用 `urllib.request`，与现有 `telegram_push.py` 风格一致。
- 外部命令通过 `subprocess.run(..., timeout=...)` 执行。
- `tests/test_weekly_pm_loop.py` 必须严格 monkeypatch `subprocess.run`；任何未 mock 的 subprocess 调用都应抛异常，避免测试读取真实 `tracker.db` 或写入真实 `artifacts/`。
- 每个检查必须有独立 timeout：
  - readiness：120 秒
  - accuracy-report：300 秒
- 单项 timeout 记为 `FAIL`，不得卡死整轮 PM loop。
- 测试通过 monkeypatch/mock 隔离 subprocess、时间、文件和 Telegram 发送。
- 不读取或打印完整 token；日志里不得泄露 Telegram token。

## 验收标准

1. `pytest tests/test_weekly_pm_loop.py -q` 通过。
2. `pytest tests/test_pipeline.py tests/test_telegram_push.py tests/test_weekly_pm_loop.py -q` 通过。
3. `scripts/weekly_pm_loop.py --dry-run --no-telegram` 可在无 `.env` 环境运行并生成摘要。
4. `cron-setup.sh` 生成的 managed block 包含 `weekly-pm-loop`。
5. `cron-setup.sh` 重跑不会保留旧的散落 `weekly_pm_loop.py` 规则。
6. `cron-alert-wrap.sh` 对 exit code 2 不发送重复 Telegram 告警。
7. agy 独立审查通过后才能实现。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| Telegram 发送失败导致 PM 没看到摘要 | 本地 `logs/weekly-pm-loop.log` 保留完整结果；发送失败返回非 0 触发 `cron-alert-wrap.sh` 二次告警 |
| `accuracy-report` 写入 tracked 文件造成未预期 git diff | spec 明确自动化不提交；PM 每周可人工决定是否提交报告变化 |
| 日志关键词误报 | 第一版宁可 WARN/FAIL 偏保守；关键词集中在脚本常见错误和历史事故 |
| readiness HOLD 被误当成失败 | HOLD 是 `WARN`，提醒先治理行情链路，不视为脚本崩溃 |
| 自动化越权启用 Phase 6 生产化 | 脚本只读检查和通知；即使报告显示可评估，也只提示另写 spec |
