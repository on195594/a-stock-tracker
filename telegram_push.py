"""Telegram 每日信号推送。daily cron 完成后调用，推送评分 >= 阈值的股票。"""
import json
import logging
import os
import urllib.request
import urllib.error

from lib.cache import get_db

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _send(token: str, chat_id: str, text: str) -> None:
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        TELEGRAM_API.format(token=token),
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def push_daily_signals(score_date: str, threshold: float = 55.0) -> None:
    """查询当日评分 >= threshold 的股票，发送 Telegram 消息。"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 未设置，跳过推送")
        return

    db = get_db()
    rows = db.execute(
        """SELECT code, name, total_score, quant_score, entry_signal_version
           FROM predictions
           WHERE score_date=? AND total_score >= ? AND entry_signal = 1
           ORDER BY total_score DESC""",
        (score_date, threshold),
    ).fetchall()
    db.close()

    if not rows:
        logger.info(f"今日无 >= {threshold} 分的评分信号，跳过推送")
        return

    lines = [f"A股每日信号 {score_date}（>={threshold:.0f}分，L3通过）\n"]
    for code, name, total, quant, entry_version in rows:
        label = f"{name}({code})" if name else code
        l3_label = f"L3:{entry_version or 'unknown'} 通过"
        lines.append(f"  {label}  总分:{total:.1f}  量化:{quant:.1f}  {l3_label}")
    text = "\n".join(lines)

    try:
        _send(token, chat_id, text)
        logger.info(f"Telegram 推送成功：{len(rows)} 只股票")
    except urllib.error.HTTPError as e:
        logger.warning(f"Telegram 推送失败（HTTP {e.code}）：{e}")
    except Exception as e:
        logger.warning(f"Telegram 推送失败：{e}")
