# Market Data Readiness New Probe Semantics Spec

创建时间：2026-06-26

## 背景

`scripts/probe_tushare_market_data.py` 已从单一 `PASS`/`FAIL` 改为分层决策：

```text
Write Gate: PASS|FAIL|MANUAL_REQUIRED
Capability Checks: PASS|DEGRADED|BLOCKED
Production Decision: DAILY_WRITES_ALLOWED|DAILY_WRITES_BLOCKED
Index/Calendar Dependent Jobs: ALLOWED|HOLD
```

当前 `scripts/check_market_data_readiness.py` 仍按旧语义判断：

- report 必须包含 `PASS`
- report 不得包含任何 `| failed |`
- close cross-check 必须为 `PASS`

这会把 `index_daily` / `trade_cal` 的非阻塞 `RATE_LIMITED` 行误判成 daily 恢复阻塞，同时也无法表达 `outcome-update` 仍需 index/calendar 能力放行。

## 目标

1. `check_market_data_readiness.py` 必须解析新的 probe 决策字段。
2. readiness 输出必须区分：
   - daily stock writes 是否可恢复；
   - index/calendar-dependent jobs 是否可恢复。
3. 默认 CLI 行为必须继续保护 `cron-setup.sh`，因为 `cron-setup.sh` 当前同时恢复 `daily` 和 `outcome-update`，而 `outcome-update` 会调用 `_ensure_index_prices()` 拉取沪深300指数数据。
4. 恢复 runbook 必须去掉“report 不含 failed 行”这种旧口径，改成读取新决策字段。

## 非目标

- 不修改 `~/a-stock-tracker/lib/`。
- 不修改 Tushare/BaoStock provider。
- 不恢复 cron。
- 不改变 `cron-setup.sh` 当前“一次性添加 daily + outcome-update”的行为。
- 不发起真实网络请求。

## Readiness 语义

### Daily Readiness

`daily_ready == True` 必须同时满足：

- `TUSHARE_TOKEN` 已配置；
- 存在最新 `docs/reviews/*-tushare-capability-probe.md`；
- `Write Gate: PASS`；
- `Production Decision: DAILY_WRITES_ALLOWED`；
- `Close cross-check: PASS`。

`Capability Checks: DEGRADED` 不应阻塞 `daily_ready`，前提是 `Write Gate` 与 close cross-check 均通过。

### Capability Readiness

`capability_ready == True` 必须同时满足：

- `daily_ready == True`；
- `Capability Checks: PASS`；
- `Index/Calendar Dependent Jobs: ALLOWED`。

`Capability Checks: DEGRADED|BLOCKED` 或 `Index/Calendar Dependent Jobs: HOLD` 必须使 `capability_ready == False`。

### Overall Cron Readiness

默认 `main()` 退出码必须代表 `cron-setup.sh` 是否可以恢复当前成组 cron：

- 只有 `daily_ready == True` 且 `capability_ready == True` 时返回 `0`；
- 否则返回 `1`。

原因：`cron-setup.sh` 当前同一门禁同时添加 `daily` 和 `outcome-update`，`outcome-update` 依赖 `_ensure_index_prices()`，不应在 `Index/Calendar Dependent Jobs: HOLD` 时静默恢复。

### Daily-only 观察模式

CLI 可增加只读参数 `--scope daily`，用于人工检查 daily 是否可单独恢复：

- `--scope cron`：默认；按 overall cron readiness 返回退出码；
- `--scope daily`：只按 `daily_ready` 返回退出码；
- 不修改任何 cron 或数据库。

原因：若仅有 `Write Gate: PASS` 但存在 `Capability Checks: DEGRADED`，默认的 `--scope cron` 会返回退出码 1 阻止成组 cron 恢复。此时通过 `--scope daily` 独立检验 `daily_ready`，可作为 staged daily 手工恢复的决策依据。

## Legacy Report Policy

旧格式 report 不包含 `Write Gate` / `Production Decision` / `Index/Calendar Dependent Jobs` 字段。readiness 不应继续用旧 `PASS` / “无 failed 行”推断恢复状态。

若最新 report 缺少新决策字段：

- `daily_ready == False`
- `capability_ready == False`
- 输出 reason：`latest Tushare capability probe uses legacy readiness format; rerun probe`

## Probe Report Selection & Date Validation

为防范文件名排序中的“字典序风险”（如 `2026-6-2` 与 `2026-06-26` 的排序颠倒，或 `draft-...` / `temp-...` 等前缀干扰），最新 report 的选取逻辑必须满足：

1. **文件名正则匹配**：仅匹配符合 `^\d{4}-\d{2}-\d{2}-tushare-capability-probe\.md$` 格式的文件，排除任何不符合 `YYYY-MM-DD` 规范的前缀（如非 2 位月/日、包含字母等）。
2. **按日期排序**：在过滤出标准文件后，按提取出的 `YYYY-MM-DD` 日期进行严格排序，以其最新的一份作为最新 report。
3. **不存在处理**：若 `docs/reviews/` 下不存在任何匹配的报告，则判定 `daily_ready == False` 且 `capability_ready == False`，并输出明确提示。

## CLI 输出契约

默认 `--scope cron`：

```text
HOLD_CRON: market data cron recovery is not cleared
- daily writes: READY|HOLD
- index/calendar-dependent jobs: READY|HOLD
- latest report: docs/reviews/YYYY-MM-DD-tushare-capability-probe.md
- reason...
```

当全部通过：

```text
READY_CRON: daily and index/calendar-dependent market data jobs can be considered for cron recovery
- daily writes: READY
- index/calendar-dependent jobs: READY
- latest report: docs/reviews/YYYY-MM-DD-tushare-capability-probe.md
```

`--scope daily` 可输出：

```text
READY_DAILY: daily market data writes can be considered for staged recovery
```

或：

```text
HOLD_DAILY: daily market data writes are not cleared
```

## Runbook 更新

`docs/runbooks/market-data-provider-recovery.md` 必须改为：

- daily / outcome 成组 cron 恢复需要：
  - `Write Gate: PASS`
  - `Production Decision: DAILY_WRITES_ALLOWED`
  - `Capability Checks: PASS`
  - `Index/Calendar Dependent Jobs: ALLOWED`
  - `Close cross-check: PASS`
  - `python3 scripts/check_market_data_readiness.py --scope cron` 返回 `READY_CRON`
- 若只有 `Write Gate: PASS` 但 `Capability Checks: DEGRADED`：
  - 严禁执行 `cron-setup.sh` 恢复成组 cron
  - 可把 `python3 scripts/check_market_data_readiness.py --scope daily` 的 `READY_DAILY` 作为 staged daily 恢复讨论证据
  - 若 PM 明确授权 staged daily 恢复，只能手工添加单条 `daily` cron，且不得添加 `outcome-update`
  - `outcome-update` 与 index/calendar-dependent jobs 保持 HOLD
- 停止条件应从 `NOT_READY` 改为：
  - `HOLD_CRON`：停止或不恢复成组 cron；
  - `HOLD_DAILY`：停止 daily 写入。

Staged daily 恢复命令必须在 runbook 中明确给出，格式应与 `cron-setup.sh` 的 `DAILY_RULE` 一致，并显式排除 `outcome-update`：

```bash
# 仅在 PM 明确授权 staged daily 恢复后使用；严禁在此状态下运行 cron-setup.sh
(crontab -l 2>/dev/null; echo "30 16 * * 1-5 /home/lin/a-stock-tracker/cron-alert-wrap.sh \"cd /home/lin/a-stock-tracker && .venv/bin/python pipeline.py daily\" daily >> /home/lin/a-stock-tracker/logs/daily.log 2>&1") | crontab -
```

## 测试计划

Focused tests:

```bash
.venv/bin/python -m pytest tests/test_market_data_readiness.py -q
```

`tests/test_market_data_readiness.py` 必须覆盖：

- 当前 2026-06-26 report 语义：`daily_ready=True`、`capability_ready=False`、默认 cron scope 返回阻塞、daily scope 返回通过；
- 全字段通过：`daily_ready=True`、`capability_ready=True`；
- legacy report default-deny：旧版 `PASS` + `Close cross-check: PASS` 但缺少新决策字段时，`daily_ready=False`、`capability_ready=False`；
- `Write Gate` 为 `FAIL` 或 `MANUAL_REQUIRED` 时，`daily_ready=False`；
- `Production Decision` 为 `DAILY_WRITES_BLOCKED` 时，`daily_ready=False`；
- `Write Gate: PASS` 但 `Close cross-check: FAIL|MANUAL_REQUIRED` 时 daily 阻塞；
- `Write Gate: PASS` 且 `Production Decision: DAILY_WRITES_ALLOWED`，但 `Capability Checks: DEGRADED|BLOCKED` 或 `Index/Calendar Dependent Jobs: HOLD` 时 capability 阻塞；
- 缺少 `TUSHARE_TOKEN` 时两个 scope 都阻塞；
- 目录中无报告存在时，两个 scope 都阻塞；
- 严格前缀与排序过滤：验证非标准前缀（如 `2026-6-2-tushare-...`、`draft-tushare-...`）被排除，且在有标准报告和非标准报告混合时，仅正确解析并按日期排序选取最新标准报告。

Full tests:

```bash
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/python -m pytest tests/ -q
```

Smoke checks:

```bash
.venv/bin/python scripts/check_market_data_readiness.py
.venv/bin/python scripts/check_market_data_readiness.py --scope daily
```

在当前 2026-06-26 真实 report 下，预期：

- default `--scope cron`：退出码 `1`，输出 `daily writes: READY` 与 `index/calendar-dependent jobs: HOLD`；
- `--scope daily`：退出码 `0`，输出 `READY_DAILY`。
