#!/bin/bash
# 包裹 a-stock-tracker 的 cron 命令：原样执行，非0退出码时发 Telegram 告警，再透传原退出码。
# 目的：堵住"cron 调用层失败（venv损坏/python缺失/cd失败）"这层盲区——
# 这层失败发生在 pipeline.py 自身的异常处理之前，_alert_crash() 不会触发，
# 否则又是 2026-04 那次 claude command not found 静默两个月的同类风险。
#
# 用法：cron-alert-wrap.sh "<完整shell命令>" [<告警文本里用的标签>] [--alert-exit-2]
# 默认保留 exit 2 不重复告警的历史行为；需要把 exit 2 作为业务告警信号的任务必须显式传入
# --alert-exit-2（例如 qualitative-v2 production acceptance 的 ROLLBACK）。

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

CMD="$1"
LABEL="${2:-$CMD}"
ALERT_EXIT_2=0
if [ "${3:-}" = "--alert-exit-2" ]; then
    ALERT_EXIT_2=1
elif [ -n "${3:-}" ]; then
    echo "unsupported option: $3" >&2
    exit 64
fi

bash -c "$CMD"
EXIT_CODE=$?

if [ "$EXIT_CODE" -ne 0 ] \
    && { [ "$EXIT_CODE" -ne 2 ] || [ "$ALERT_EXIT_2" -eq 1 ]; } \
    && [ -f "$ENV_FILE" ]; then
    TOKEN=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2-)
    CHAT_ID=$(grep -m1 '^TELEGRAM_CHAT_ID=' "$ENV_FILE" | cut -d= -f2-)
    if [ -n "$TOKEN" ] && [ -n "$CHAT_ID" ]; then
        STATUS="失败"
        if [ "$EXIT_CODE" -eq 2 ]; then
            STATUS="ROLLBACK"
        fi
        curl -s -m 10 -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
            --data-urlencode chat_id="${CHAT_ID}" \
            --data-urlencode text="🚨 a-stock-tracker cron ${STATUS}（exit=${EXIT_CODE}）: ${LABEL}
详见 ${PROJECT_DIR}/logs/ 对应日志" >/dev/null
    fi
fi

exit "$EXIT_CODE"
