#!/bin/bash

# a-stock-tracker cron 自动配置脚本

set -e

PROJECT_DIR="$HOME/a-stock-tracker"

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
DAILY_RULE="30 16 * * 1-5 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python pipeline.py daily\" daily >> $PROJECT_DIR/logs/daily.log 2>&1"
OUTCOME_RULE="00 17 * * 1-5 $PROJECT_DIR/cron-alert-wrap.sh \"cd $PROJECT_DIR && .venv/bin/python pipeline.py outcome-update\" outcome-update >> $PROJECT_DIR/logs/outcome.log 2>&1"

MARKET_DATA_READY=0
if "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/check_market_data_readiness.py" >/tmp/a-stock-market-data-readiness.log 2>&1; then
    MARKET_DATA_READY=1
else
    echo "⚠️  行情 provider 尚未通过恢复门禁；将只配置 weekly，不新增 daily/outcome-update"
    cat /tmp/a-stock-market-data-readiness.log
fi

# 检查并追加规则
if echo "$CURRENT_CRONTAB" | grep -q "pipeline.py weekly"; then
    echo "ℹ️  weekly 任务已存在，跳过"
else
    CURRENT_CRONTAB=$(echo "$CURRENT_CRONTAB"; echo ""; echo "# a-stock-tracker weekly 基本面刷新 (每周六 10:00)"; echo "$WEEKLY_RULE")
    echo "✅ 已添加 weekly 任务"
fi

if [ "$MARKET_DATA_READY" -eq 1 ]; then
    if echo "$CURRENT_CRONTAB" | grep -q "pipeline.py daily"; then
        echo "ℹ️  daily 任务已存在，跳过"
    else
        CURRENT_CRONTAB=$(echo "$CURRENT_CRONTAB"; echo ""; echo "# a-stock-tracker daily (工作日 16:30)"; echo "$DAILY_RULE")
        echo "✅ 已添加 daily 任务"
    fi

    if echo "$CURRENT_CRONTAB" | grep -q "pipeline.py outcome-update"; then
        echo "ℹ️  outcome-update 任务已存在，跳过"
    else
        CURRENT_CRONTAB=$(echo "$CURRENT_CRONTAB"; echo ""; echo "# a-stock-tracker outcome-update (工作日 17:00)"; echo "$OUTCOME_RULE")
        echo "✅ 已添加 outcome-update 任务"
    fi
else
    echo "⏸️  跳过 daily / outcome-update cron；配置 TUSHARE_TOKEN 并通过 probe 后再运行本脚本"
fi

# 写入 crontab
echo "$CURRENT_CRONTAB" | crontab -

echo ""
echo "=========================================="
echo "✨ cron 定时任务配置完成"
echo "=========================================="
echo "任务详情："
echo "  • weekly:         每周六 10:00 刷新基本面缓存"
echo "  • daily:          每个工作日 16:30 评分 + Sheets 同步"
echo "  • outcome-update: 每个工作日 17:00 更新到期结果"
echo ""
echo "查看定时任务："
echo "  crontab -l"
echo ""
echo "查看执行日志："
echo "  tail -f $PROJECT_DIR/logs/weekly.log"
echo "  tail -f $PROJECT_DIR/logs/daily.log"
echo "  tail -f $PROJECT_DIR/logs/outcome.log"
