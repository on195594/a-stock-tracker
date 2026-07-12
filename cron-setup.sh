#!/bin/bash

# a-stock-tracker cron 自动配置脚本

set -e

PROJECT_DIR="$HOME/a-stock-tracker"
MANAGED_START="# >>> a-stock-tracker cron >>>"
MANAGED_END="# <<< a-stock-tracker cron <<<"

# 检查 .venv 是否存在
if [ ! -d "$PROJECT_DIR/.venv" ]; then
    echo "❌ 错误：$PROJECT_DIR/.venv 不存在"
    echo "请先运行：python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# 创建日志目录
mkdir -p "$PROJECT_DIR/logs"

# 获取现有 crontab
CURRENT_CRONTAB=$(crontab -l 2>/dev/null || true)

# cron 规则
WEEKLY_RULE="00 10 * * 6 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python pipeline.py weekly\" weekly >> $PROJECT_DIR/logs/weekly.log 2>&1"
PM_LOOP_RULE="30 09 * * 1 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python scripts/weekly_pm_loop.py\" weekly-pm-loop >> $PROJECT_DIR/logs/weekly-pm-loop.log 2>&1"
QFQ_RULE="00 16 * * 1-5 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python scripts/fetch_qfq_daily_bars.py\" qfq-daily-bars >> $PROJECT_DIR/logs/qfq-daily-bars.log 2>&1"
DAILY_RULE="30 16 * * 1-5 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python pipeline.py daily\" daily >> $PROJECT_DIR/logs/daily.log 2>&1"
OUTCOME_RULE="00 17 * * 1-5 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python pipeline.py outcome-update\" outcome-update >> $PROJECT_DIR/logs/outcome.log 2>&1"

MARKET_DATA_READY=0
if "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/check_market_data_readiness.py" >/tmp/a-stock-market-data-readiness.log 2>&1; then
    MARKET_DATA_READY=1
else
    echo "⚠️  行情 provider 尚未通过恢复门禁；将只配置 weekly，并移除 daily/outcome-update"
    cat /tmp/a-stock-market-data-readiness.log
fi

# 删除旧版散落规则和新版 managed block，避免旧时间、注释行或路径变化造成误判。
BASE_CRONTAB=$(printf '%s\n' "$CURRENT_CRONTAB" | awk \
    -v start="$MANAGED_START" \
    -v end="$MANAGED_END" '
    $0 == start { in_block = 1; next }
    $0 == end { in_block = 0; next }
    in_block { next }
    /# a-stock-tracker/ { next }
    /a-stock-tracker\/cron-alert-wrap\.sh/ && /pipeline\.py (weekly|daily|outcome-update)/ { next }
    /a-stock-tracker\/cron-alert-wrap\.sh/ && /weekly_pm_loop\.py/ { next }
    /a-stock-tracker\/cron-alert-wrap\.sh/ && /fetch_qfq_daily_bars\.py/ { next }
    { print }
')

MANAGED_CRONTAB=$(cat <<EOF
$MANAGED_START
# a-stock-tracker weekly 基本面刷新 (每周六 10:00)
$WEEKLY_RULE

# a-stock-tracker Phase 6 weekly PM loop (每周一 09:30)
$PM_LOOP_RULE

# a-stock-tracker QFQ 日线采集 (工作日 16:00，pipeline 前)
$QFQ_RULE
EOF
)
echo "✅ 已配置 weekly 任务"
echo "✅ 已配置 weekly PM loop"
echo "✅ 已配置 qfq-daily-bars 任务"

if [ "$MARKET_DATA_READY" -eq 1 ]; then
    MANAGED_CRONTAB=$(cat <<EOF
$MANAGED_CRONTAB

# a-stock-tracker daily (工作日 16:30)
$DAILY_RULE

# a-stock-tracker outcome-update (工作日 17:00)
$OUTCOME_RULE
EOF
)
    echo "✅ 已配置 daily 任务"
    echo "✅ 已配置 outcome-update 任务"
else
    echo "⏸️  已移除 daily / outcome-update cron；配置 TUSHARE_TOKEN 并通过 probe 后再运行本脚本"
fi

MANAGED_CRONTAB=$(cat <<EOF
$MANAGED_CRONTAB
$MANAGED_END
EOF
)

# 写入 crontab
printf '%s\n\n%s\n' "$BASE_CRONTAB" "$MANAGED_CRONTAB" | sed '/^$/N;/^\n$/D' | crontab -

echo ""
echo "=========================================="
echo "✨ cron 定时任务配置完成"
echo "=========================================="
echo "任务详情："
echo "  • weekly:         每周六 10:00 刷新基本面缓存"
echo "  • weekly-pm-loop: 每周一 09:30 复核 Phase 6 并发送 Telegram 摘要"
echo "  • qfq-daily-bars: 每个工作日 16:00 采集 QFQ 前复权日线"
if [ "$MARKET_DATA_READY" -eq 1 ]; then
    echo "  • daily:          每个工作日 16:30 评分 + Sheets 同步"
    echo "  • outcome-update: 每个工作日 17:00 更新到期结果"
else
    echo "  • daily:          HOLD（行情恢复门禁未通过）"
    echo "  • outcome-update: HOLD（行情恢复门禁未通过）"
fi
echo ""
echo "查看定时任务："
echo "  crontab -l"
echo ""
echo "查看执行日志："
echo "  tail -f $PROJECT_DIR/logs/weekly.log"
echo "  tail -f $PROJECT_DIR/logs/weekly-pm-loop.log"
echo "  tail -f $PROJECT_DIR/logs/daily.log"
echo "  tail -f $PROJECT_DIR/logs/outcome.log"
