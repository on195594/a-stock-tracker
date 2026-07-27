# Framework B prospective cohort 自动冻结规范

创建时间：2026-07-27
状态：implemented-and-production-verified

## 1. 目标

把当前依赖人工执行的 `framework-b-cohort-freeze` 固化为每周自动任务，使 prospective frozen cohort 能持续积累不同 `cohort_week`，并继续由现有 `outcome-update` 自然生成 30d outcome。

## 2. 非目标

- 不启用 Framework B 生产评分或交易决策。
- 不修改 `SUPPORTED_FRAMEWORKS`、`weights.json`、既有 predictions 或历史 B legacy 数据。
- 不把 rolling `[UNFROZEN-PREVIEW]` 纳入门禁。
- 不提前生成、回填或伪造 `outcome_30d`。
- 不自动放宽 `closed>=20`、`closed_weeks>=3`、`overdue=0`、`missing_source=0` 门槛。

## 3. 调度

- 每周一 `09:20 Asia/Shanghai` 运行，位于周六 TuShare weekly 刷新之后、周一 `09:30` weekly PM loop 之前。
- `label_date` 使用任务运行当天；source 仍由现有 `_latest_a_source(..., score_date<=label_date)` 固定绑定，不切换为后续 latest-A。
- 日志写入 `logs/framework-b-cohort-freeze.log`，由 `cron-alert-wrap.sh` 包裹；非零退出触发既有 Telegram cron 告警。

## 4. 自动化合同

新增 `scripts/run_framework_b_cohort_freeze.py`，按固定顺序执行：

1. 运行 `scripts/check_market_data_readiness.py --scope cron --allow-stale-days 9`；只有返回 0 且包含 `READY_CRON` 才继续。
2. 运行 `pipeline.py framework-b-cohort-freeze --dry-run --label-date YYYY-MM-DD`。
3. 严格解析 dry-run JSON，要求：
   - `dry_run=true`；
   - `candidates>0`；
   - `inserted=0`；
   - `skipped_existing=0`。
4. 运行同一 `label_date` 的正式 freeze。
5. 严格解析正式 JSON，要求：
   - `dry_run=false`；
   - `candidates` 与 dry-run 相同；
   - `inserted + skipped_existing == candidates`；
   - `cohort_week` 与 dry-run 相同。
6. 输出单行 JSON 摘要并返回 0。任何命令失败、输出非 JSON、候选为 0 或计数不一致均 fail-closed 返回非零。

同周重跑必须是成功的幂等重放：允许 `inserted=0, skipped_existing=candidates`，不得重复累计同一 source/同一周样本。

## 5. 安全边界

- dry-run 必须先于正式写入，且 dry-run 使用既有只读 DB 路径。
- 正式写入继续复用 `insert_freeze_payloads()` 的 `BEGIN IMMEDIATE`、`ON CONFLICT DO NOTHING`、commit/rollback 合同。
- 自动化只写 `framework_b_label_cohorts`；不得修改 predictions。
- readiness 非 READY、候选为空、部分写入计数不一致时立即停止并告警。
- 自动脚本自身不直接调用 Telegram API；外层通知由既有 `cron-alert-wrap.sh` 保证。被调用的 `pipeline.py` 子命令保留现有 `_alert_crash()` 行为，因此极少数未捕获 CLI 异常可能同时产生 pipeline crash 告警与 wrapper 告警；这是显式接受的重复告警，不得为去重而绕过任一故障信号。

## 6. 测试与验收

### TDD

- readiness HOLD：不得调用 dry-run/execute。
- dry-run 候选为 0：不得 execute，返回非零。
- dry-run 输出损坏：fail-closed。
- 正常首次冻结：execute 后 `inserted + skipped == candidates`。
- 同周重跑：`inserted=0, skipped=candidates` 仍成功。
- execute 候选数/周次与 dry-run 不一致：fail-closed。
- cron installer：规则位于 `09:20 Monday`，在 `09:30 weekly PM loop` 之前；重复安装不重复；旧散落规则会被清理。

### 生产验收

1. 备份当前 crontab 和 `tracker.db`。
2. 运行一次自动脚本真实 smoke；本周 W31 应新增候选，或若已存在则完整 skip。
3. 再运行一次验证幂等：第二次必须 `inserted=0` 且全部 skipped。
4. 回读 `framework_b_label_cohorts`：当前周行数与 candidates 一致，predictions 行数及内容 hash 不变。
5. 安装 cron 后回读规则仅一条，且现有 weekly/daily/outcome/PM 规则不丢失。
6. 运行 `accuracy-report`，确认样本数和 cohort 周数增加，但 Phase 6 生产门禁仍保持。
7. 全量 pytest、Ruff、format、mypy、`git diff --check` 通过。
8. 最终独立只读复审为 `APPROVE_LANDING`。

## 7. 回滚与停止条件

- cron 回滚：使用 `cron-setup.sh` 自动生成的 crontab snapshot，或执行 `cron-setup.sh --rollback <snapshot>`。
- 数据回滚：真实 smoke 前创建 `tracker.db` SQLite 在线备份；仅在 smoke 违反计数/不变性时恢复，正常新增的 W31 cohort 不回滚。
- 任一以下情况停止：readiness 非 READY、候选 0、JSON 合同不符、部分写入、predictions hash 变化、数据库 `quick_check` 非 `ok`、cron 规则重复或丢失。

## 8. 预期时间线

若 W31 于 2026-07-27 冻结、W32 于 2026-08-03 冻结，则三个 cohort 周的 30d 到期日依次约为 2026-08-19、2026-08-26、2026-09-02；达到门槛后仅允许进入人工 review，不自动生产化。
