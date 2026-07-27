#!/bin/bash

# a-stock-tracker cron 自动配置脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${A_STOCK_PROJECT_DIR:-$SCRIPT_DIR}"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd -P)"
MANAGED_START="# >>> a-stock-tracker cron >>>"
MANAGED_END="# <<< a-stock-tracker cron <<<"

if [ "${1:-}" = "--rollback" ]; then
    if [ -z "${2:-}" ] || [ ! -f "$2" ]; then
        echo "❌ 用法：$0 --rollback <crontab-snapshot>" >&2
        exit 2
    fi
    crontab "$2"
    echo "✅ 已恢复 crontab：$2"
    exit 0
fi

# 检查 .venv 是否存在
if [ ! -d "$PROJECT_DIR/.venv" ]; then
    echo "❌ 错误：$PROJECT_DIR/.venv 不存在"
    echo "请先运行：python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# 创建日志目录
mkdir -p "$PROJECT_DIR/logs"

# 获取现有 crontab，并在覆盖前保留可执行回滚快照
CURRENT_CRONTAB=$(crontab -l 2>/dev/null || true)
PRESERVE_EXISTING_DAILY=0
if printf '%s\n' "$CURRENT_CRONTAB" | awk -v project_dir="$PROJECT_DIR" '
    function is_project_daily(line, command) {
        if (line ~ /^[[:space:]]*#/) return 0
        command = line
        sub(/[[:space:]]+#.*/, "", command)
        if (index(command, project_dir) == 0) return 0
        if (command ~ /pipeline\.py[[:space:]]+daily([[:space:]"#]|$)/) return 1
        return command ~ /-m[[:space:]]+pipeline(\.py)?[[:space:]]+daily([[:space:]"#]|$)/
    }
    is_project_daily($0) { found = 1 }
    END { exit found ? 0 : 1 }
'; then
    PRESERVE_EXISTING_DAILY=1
fi
CRON_BACKUP_DIR="${A_STOCK_CRON_BACKUP_DIR:-$PROJECT_DIR/backups/crontab}"
mkdir -p "$CRON_BACKUP_DIR"
CRON_BACKUP_PATH="$CRON_BACKUP_DIR/crontab-$(date +%Y%m%d-%H%M%S).txt"
printf '%s\n' "$CURRENT_CRONTAB" > "$CRON_BACKUP_PATH"

# cron 规则
WEEKLY_RULE="00 10 * * 6 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python -m scripts.run_tushare_primary_production_cycle weekly\" weekly >> $PROJECT_DIR/logs/weekly.log 2>&1"
COHORT_FREEZE_RULE="20 09 * * 1 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python scripts/run_framework_b_cohort_freeze.py\" framework-b-cohort-freeze >> $PROJECT_DIR/logs/framework-b-cohort-freeze.log 2>&1"
PM_LOOP_RULE="30 09 * * 1 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python scripts/weekly_pm_loop.py\" weekly-pm-loop >> $PROJECT_DIR/logs/weekly-pm-loop.log 2>&1"
QFQ_RULE="00 16 * * 1-5 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python scripts/fetch_qfq_daily_bars_tushare.py\" qfq-daily-bars >> $PROJECT_DIR/logs/qfq-daily-bars.log 2>&1"
PRIMARY_DAILY_RULE="15 17 * * 1-5 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python -m scripts.run_tushare_primary_production_cycle daily\" tushare-primary-daily >> $PROJECT_DIR/logs/tushare-primary-daily.log 2>&1"
DAILY_RULE="30 17 * * 1-5 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python pipeline.py daily\" daily >> $PROJECT_DIR/logs/daily.log 2>&1"
ACCEPTANCE_RULE="45 17 * * 1-5 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python scripts/check_qualitative_v2_production.py --require-today\" qualitative-v2-production-acceptance --alert-exit-2 >> $PROJECT_DIR/logs/qualitative-v2-production-acceptance.log 2>&1"
OUTCOME_RULE="00 18 * * 1-5 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python pipeline.py outcome-update\" outcome-update >> $PROJECT_DIR/logs/outcome.log 2>&1"

MARKET_DATA_READY=0
if "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/check_market_data_readiness.py" >/tmp/a-stock-market-data-readiness.log 2>&1; then
    MARKET_DATA_READY=1
else
    echo "⚠️  行情 provider 尚未通过恢复门禁"
    cat /tmp/a-stock-market-data-readiness.log
fi

# 删除旧版散落规则和新版 managed block，避免旧时间、注释行或路径变化造成误判。
BASE_CRONTAB=$(printf '%s\n' "$CURRENT_CRONTAB" | awk \
    -v start="$MANAGED_START" \
    -v end="$MANAGED_END" \
    -v project_dir="$PROJECT_DIR" '
    function is_project_daily(line, command) {
        command = line
        sub(/[[:space:]]+#.*/, "", command)
        if (index(command, project_dir) == 0) return 0
        if (command ~ /pipeline\.py[[:space:]]+daily([[:space:]"#]|$)/) return 1
        return command ~ /-m[[:space:]]+pipeline(\.py)?[[:space:]]+daily([[:space:]"#]|$)/
    }
    $0 == start { in_block = 1; next }
    $0 == end { in_block = 0; next }
    in_block { next }
    /^[[:space:]]*#/ { print; next }
    is_project_daily($0) { next }
    index($0, project_dir "/cron-alert-wrap.sh") > 0 && /pipeline\.py (weekly|daily|outcome-update)/ { next }
    index($0, project_dir "/cron-alert-wrap.sh") > 0 && /check_qualitative_v2_production\.py/ { next }
    index($0, project_dir "/cron-alert-wrap.sh") > 0 && /run_framework_b_cohort_freeze\.py/ { next }
    index($0, project_dir "/cron-alert-wrap.sh") > 0 && /weekly_pm_loop\.py/ { next }
    index($0, project_dir "/cron-alert-wrap.sh") > 0 && /fetch_qfq_daily_bars/ { next }
    index($0, project_dir "/cron-alert-wrap.sh") > 0 && /tushare_primary/ { next }
    { print }
')

MANAGED_CRONTAB=$(cat <<EOF
$MANAGED_START
# a-stock-tracker weekly 基本面刷新 (每周六 10:00)
$WEEKLY_RULE

# a-stock-tracker Framework B prospective cohort 自动冻结 (每周一 09:20)
$COHORT_FREEZE_RULE

# a-stock-tracker Phase 6 weekly PM loop (每周一 09:30)
$PM_LOOP_RULE

# a-stock-tracker QFQ 日线采集 (TuShare, 工作日 16:00，pipeline 前)
$QFQ_RULE

# a-stock-tracker TuShare primary daily (工作日 17:15)
$PRIMARY_DAILY_RULE
EOF
)
echo "✅ 已配置 weekly 任务"
echo "✅ 已配置 Framework B cohort 自动冻结任务"
echo "✅ 已配置 weekly PM loop"
echo "✅ 已配置 qfq-daily-bars 任务"
echo "✅ 已配置 tushare-primary-daily 任务"

if [ "$MARKET_DATA_READY" -eq 1 ] || [ "$PRESERVE_EXISTING_DAILY" -eq 1 ]; then
    if [ "$MARKET_DATA_READY" -eq 0 ]; then
        echo "⚠️  门禁未通过，但检测到既有 daily；保留现有评分链，不用于从零恢复"
    fi
    MANAGED_CRONTAB=$(cat <<EOF
$MANAGED_CRONTAB

# a-stock-tracker daily (工作日 17:30)
$DAILY_RULE

# a-stock-tracker qualitative-v2 production acceptance (工作日 17:45，daily 后、outcome-update 前)
$ACCEPTANCE_RULE

# a-stock-tracker outcome-update (工作日 18:00)
$OUTCOME_RULE
EOF
)
    echo "✅ 已配置 daily 任务"

    echo "✅ 已配置 qualitative-v2 production acceptance 任务"
    echo "✅ 已配置 outcome-update 任务"
else
    echo "⏸️  未发现既有 daily，且行情恢复门禁未通过；不新增评分写任务"
fi

MANAGED_CRONTAB=$(cat <<EOF
$MANAGED_CRONTAB
$MANAGED_END
EOF
)

# 写入 crontab；失败时立即恢复刚才的快照
if ! printf '%s\n\n%s\n' "$BASE_CRONTAB" "$MANAGED_CRONTAB" | sed '/^$/N;/^\n$/D' | crontab -; then
    crontab "$CRON_BACKUP_PATH"
    echo "❌ 安装失败，已恢复 crontab：$CRON_BACKUP_PATH" >&2
    exit 1
fi

echo ""
echo "=========================================="
echo "✨ cron 定时任务配置完成"
echo "=========================================="
echo "rollback snapshot: $CRON_BACKUP_PATH"
echo "任务详情："
echo "  • weekly:         每周六 10:00 刷新基本面缓存"
echo "  • b-cohort-freeze: 每周一 09:20 自动冻结 Framework B report-only cohort"
echo "  • weekly-pm-loop: 每周一 09:30 复核 Phase 6 并发送 Telegram 摘要"
echo "  • qfq-daily-bars: 每个工作日 16:00 采集 QFQ 前复权日线"
if [ "$MARKET_DATA_READY" -eq 1 ] || [ "$PRESERVE_EXISTING_DAILY" -eq 1 ]; then
    echo "  • primary-daily:  每个工作日 17:15 采集并物化 TuShare 估值"
    echo "  • daily:          每个工作日 17:30 评分 + Sheets 同步"
    echo "  • v2-acceptance:  每个工作日 17:45 只读验收；ROLLBACK 触发 Telegram 告警"
    echo "  • outcome-update: 每个工作日 18:00 更新到期结果"
else
    echo "  • daily:          HOLD（行情恢复门禁未通过）"
    echo "  • v2-acceptance:  HOLD（daily 未配置）"
    echo "  • outcome-update: HOLD（行情恢复门禁未通过）"
fi
echo ""
echo "查看定时任务："
echo "  crontab -l"
echo ""
echo "查看执行日志："
echo "  tail -f $PROJECT_DIR/logs/weekly.log"
echo "  tail -f $PROJECT_DIR/logs/framework-b-cohort-freeze.log"
echo "  tail -f $PROJECT_DIR/logs/weekly-pm-loop.log"
echo "  tail -f $PROJECT_DIR/logs/daily.log"
echo "  tail -f $PROJECT_DIR/logs/qualitative-v2-production-acceptance.log"
echo "  tail -f $PROJECT_DIR/logs/outcome.log"
