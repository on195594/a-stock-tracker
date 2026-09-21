# TuShare Primary 生产运行手册

适用范围：`a-stock-tracker` 估值/市值、通用财务指标和分红事实
状态：active
最后更新：2026-09-20

## 生产边界

- Shadow DB：`data/tushare-primary.db`
- 原始响应：`artifacts/tushare-ingestion/<run_id>/<endpoint>.jsonl.gz`
- 生产目标：`tracker.db.stock_fundamentals`
- 声明依赖：`a-stock-lib==0.8.0`（固定 GitHub Release wheel 与 SHA256）、`tushare==1.4.29`；生产安装版本仍以 `docs/project-status.md` 的部署记录为准
- 生产 cycle：`a_stock_tracker.data.tushare_primary_production_cycle`
- Token：只从环境读取 `TUSHARE_TOKEN`，不得写入命令、日志、artifact 或 Git

`TUSHARE_PRIMARY_VALUATION|FINANCIAL|DIVIDEND` 三域环境变量保护通用 materialization CLI。scheduled production
cycle 会根据 `daily|weekly` 显式启用对应域，因此紧急停用必须先恢复/修改 crontab，不能
只把环境开关设为 `off`。开关也不会回写已物化的数据；恢复旧字段值必须恢复切换前 DB。

三域 shadow/readiness 与行情恢复的 `scripts/check_market_data_readiness.py` 是两套不同控制面。前者不替代 daily/index/calendar/close probe，后者也不替代三域 35/35 readiness。

## 定时任务

```cron
00 10 * * 6   # weekly：财务、分红采集与物化
00 16 * * 1-5 # QFQ 个股日线与沪深300全收益指数
15 17 * * 1-5 # daily：当日估值采集与物化
30 17 * * 1-5 # pipeline daily、自动策略报告与 Telegram 观察名单
```

权威安装入口：

```bash
cd /home/lin/a-stock-tracker
bash cron-setup.sh
```

查看实际规则：

```bash
crontab -l
```

生产 cycle 必须使用 module 方式启动：

```bash
.venv/bin/python -m scripts.run_tushare_primary_production_cycle daily
.venv/bin/python -m scripts.run_tushare_primary_production_cycle weekly
```

不要改成直接执行 `scripts/run_tushare_primary_production_cycle.py`；文件路径启动不能保证项目包位于导入路径。

## 手动 daily cycle

默认使用本地日期；补跑指定日期时显式提供：

```bash
cd /home/lin/a-stock-tracker
.venv/bin/python -m scripts.run_tushare_primary_production_cycle \
  daily --as-of-date 2026-07-21
```

成功时输出单行 JSON，至少包含以下字段（实际对象还会包含 ingestion 的 run/artifact 明细）：

```json
{"changed_count": 35, "ingestion": {"source_as_of": "2026-07-21", "status": "completed"}, "mode": "daily", "status": "completed"}
```

退出码必须为 0。即使 DB 已发生预期变化，非零退出码仍按失败处理并检查日志与终验结果。

## 手动 weekly cycle

```bash
cd /home/lin/a-stock-tracker
.venv/bin/python -m scripts.run_tushare_primary_production_cycle \
  weekly --as-of-date 2026-07-21
```

weekly 顺序：

1. financial batch；
2. dividend batch；
3. financial readiness；
4. dividend readiness；
5. 单次 materialization，仅开启 financial/dividend 域。

任一批次或 readiness 非 READY 时不得物化。

## 分步诊断

### 1. Readiness

```bash
.venv/bin/python -m scripts.check_tushare_primary_readiness \
  --db-path data/tushare-primary.db \
  --as-of-date 2026-07-21 \
  --scope all
```

可用 scope：

```text
valuation
financial
dividend
all
```

只有顶层 `status=READY` 才可进入对应域 materialization。

### 2. Materialization preview

```bash
TUSHARE_PRIMARY_VALUATION=on \
TUSHARE_PRIMARY_FINANCIAL=on \
TUSHARE_PRIMARY_DIVIDEND=on \
.venv/bin/python -m scripts.materialize_tushare_primary \
  --shadow-db-path data/tushare-primary.db \
  --target-db-path tracker.db \
  --as-of-date 2026-07-21
```

没有 `--execute` 时只能 preview。检查 35 股字段差异、来源、报告期、公告日和估值覆盖后，才可在明确生产授权下增加 `--execute`。

### 3. SQLite 检查

```bash
.venv/bin/python - <<'PY'
import sqlite3

for path in ("tracker.db", "data/tushare-primary.db"):
    with sqlite3.connect(path) as connection:
        print(path, connection.execute("PRAGMA quick_check").fetchone()[0])
PY
```

预期两者均为 `ok`。

## 日志与审计

```text
logs/tushare-primary-daily.log
logs/weekly.log
artifacts/tushare-ingestion/<run_id>/
data/tushare-primary.db
docs/reviews/2026-07-21-tushare-three-domain-cutover-retrospective.md
```

检查最新 run：

```sql
SELECT run_id, endpoint, status, source_as_of, row_count, error_code
FROM ingestion_runs
ORDER BY run_id DESC
LIMIT 20;
```

生产要求：

- run 为 `completed`；
- `source_as_of` 与目标交易日一致；
- artifact 相对路径存在且 SHA-256 可验证；
- Token、请求头和代理凭据不出现在日志/artifact；
- `tracker.db` 的来源字段与 shadow 记录一致。

## 常见故障

### `ModuleNotFoundError: a_stock_tracker`

确认使用：

```bash
.venv/bin/python -m scripts.run_tushare_primary_production_cycle ...
```

不要直接执行脚本文件路径，也不要在生产模块中注入 `sys.path`。

### cron 安装提示旧 market-data probe stale

该提示针对 daily/index/calendar/close 行情恢复门禁。处理方式：

1. 不伪造或手工修改 probe 结论；
2. 按 `docs/runbooks/market-data-provider-recovery.md` 刷新真实 capability probe；
3. 新三域任务仍以 `check_tushare_primary_readiness.py` 为门禁；
4. 检查 `cron-setup.sh` 是否只是保留既有 daily 任务，而不是越权新启用旧行情能力。

### readiness 非 READY

- 不执行 materialization；
- 查看每股 reasons、run status、source_as_of 和 artifact；
- 权限/参数/schema 错误不盲目重试；
- 网络瞬态错误只允许合同规定的有界重试；
- 不用旧源值填充 TuShare observation。

### cycle 写入后退出非零

不能假定事务失败，也不能假定成功。分别检查：

1. cycle stderr/traceback；
2. 最新 ingestion run；
3. `tracker.db PRAGMA quick_check`；
4. 35 股来源字段；
5. `predictions` 行数与 hash；
6. 最终输出序列化或告警包装层。

## 回滚

回滚前先停止或避开正在运行的 weekly/daily/outcome writer，并保存当前失败证据。不得删除 shadow DB 或 artifact。顺序必须是：先停止后续 production cycle，再恢复 DB/依赖，最后复验；只关闭环境开关不足以停止 scheduled cycle。

### 恢复 crontab

```bash
bash cron-setup.sh --rollback /absolute/path/to/crontab-snapshot.txt
```

安装脚本产生的快照位于：

```text
backups/crontab/crontab-YYYYMMDD-HHMMSS.txt
```

恢复后必须重新执行 `crontab -l` 核对。

### 恢复运行依赖

仅使用事先备份并校验过的本地 wheel，禁止临时从不受控索引下载：

```bash
.venv/bin/python -m pip install --no-index --no-deps \
  /absolute/path/to/a_stock_lib-0.5.2-py3-none-any.whl
.venv/bin/python -m pip check
```

### 恢复 tracker DB

- 使用切换前经 `PRAGMA quick_check` 验证的 SQLite 备份；
- 确认没有 writer 后，以临时文件验证并原子 replace；
- 恢复后重新执行 `PRAGMA quick_check`；
- 核对 `predictions` 行数/hash 与切换前基线；
- 不删除失败后的 shadow DB/artifact。

### 回滚成功标准

- 运行时导入预期旧版依赖；
- `tracker.db` 和 shadow DB 均可打开；
- 原 crontab 完整恢复；
- `predictions` 未改变；
- 旧读路径恢复，三域新 materialization 不再被 cron 调用；
- 失败证据、日志和 artifact 保留可审计。
