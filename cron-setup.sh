#!/bin/bash

# a-stock-tracker 通用数据采集 cron 配置脚本

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
CRON_BACKUP_DIR="${A_STOCK_CRON_BACKUP_DIR:-$PROJECT_DIR/backups/crontab}"
mkdir -p "$CRON_BACKUP_DIR"
CRON_BACKUP_PATH="$CRON_BACKUP_DIR/crontab-$(date +%Y%m%d-%H%M%S).txt"
printf '%s\n' "$CURRENT_CRONTAB" > "$CRON_BACKUP_PATH"

# cron 规则
WEEKLY_RULE="00 10 * * 6 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python -m scripts.run_tushare_primary_production_cycle weekly\" weekly >> $PROJECT_DIR/logs/weekly.log 2>&1"
QFQ_RULE="00 16 * * 1-5 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python -m scripts.fetch_qfq_daily_bars_tushare\" qfq-daily-bars >> $PROJECT_DIR/logs/qfq-daily-bars.log 2>&1"
PRIMARY_DAILY_RULE="15 17 * * 1-5 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python -m scripts.run_tushare_primary_production_cycle daily\" tushare-primary-daily >> $PROJECT_DIR/logs/tushare-primary-daily.log 2>&1"

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

# a-stock-tracker QFQ 日线与交易日历证据刷新 (TuShare, 工作日 16:00)
$QFQ_RULE

# a-stock-tracker TuShare primary daily (工作日 17:15)
$PRIMARY_DAILY_RULE
EOF
)
echo "✅ 已配置 weekly 任务"
echo "✅ 已配置 qfq-daily-bars + trading-calendar 任务"
echo "✅ 已配置 tushare-primary-daily 任务"
echo "⏹️  Framework A 已结案；不配置评分、策略报告或 Telegram 观察名单任务"

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
echo "  • qfq-daily-bars: 每个工作日 16:00 刷新交易日历并采集 QFQ 前复权日线"
echo "  • primary-daily:  每个工作日 17:15 采集并物化 TuShare 估值"
echo "  • framework-a:    CLOSED_UNPROVEN（无定时评分、报告或通知）"
echo ""
echo "查看定时任务："
echo "  crontab -l"
echo ""
echo "查看执行日志："
echo "  tail -f $PROJECT_DIR/logs/weekly.log"
echo "  tail -f $PROJECT_DIR/logs/qfq-daily-bars.log"
echo "  tail -f $PROJECT_DIR/logs/tushare-primary-daily.log"
