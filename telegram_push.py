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


def _interpret(
    quant_score: float,
    total_score: float,
    moat: int | None,
    market_pos: int | None,
) -> str:
    timing = round(total_score - quant_score, 1)
    parts: list[str] = []

    if moat is not None:
        if moat >= 8:
            parts.append(f"护城河{moat}/10(强)")
        elif moat >= 5:
            parts.append(f"护城河{moat}/10")
        else:
            parts.append(f"护城河{moat}/10(弱)")

    if market_pos is not None:
        if market_pos >= 4:
            parts.append(f"行业龙头({market_pos}/5)")
        elif market_pos == 3:
            parts.append(f"行业中等({market_pos}/5)")
        else:
            parts.append(f"行业地位弱({market_pos}/5)")

    if timing >= 12:
        parts.append("择时佳")
    elif timing <= 5:
        parts.append("择时弱")

    return "  ".join(parts) if parts else ""


def _format_stock_line(
    code: str,
    name: str | None,
    total_score: float,
    quant_score: float,
    entry_signal: int | None,
    entry_signal_version: str | None,
    moat: int | None,
    market_pos: int | None,
    l3_v2_signal: int | None = None,
) -> str:
    label = f"{name}({code})" if name else code
    interp = _interpret(quant_score, total_score, moat, market_pos)
    l3_tag = "✓ L3买点" if entry_signal == 1 else "等待L3"
    if entry_signal_version:
        l3_tag += f"({entry_signal_version})"
    if l3_v2_signal == 1:
        l3_tag += " (v2✓)"
    elif l3_v2_signal == 0:
        l3_tag += " (v2✗)"

    line = f"  {label}  总分:{total_score:.1f}  量化:{quant_score:.1f}  {l3_tag}"
    if interp:
        line += f"\n    {interp}"
    return line


def push_daily_signals(score_date: str, threshold: float = 44.0, radar_min: float = 35.0) -> None:
    """查询当日分层推荐股票，发送 Telegram 消息。"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 未设置，跳过推送")
        return

    db = get_db()
    primary = db.execute(
        """SELECT p.code, p.name, p.total_score, p.quant_score, p.entry_signal,
                  p.entry_signal_version, q.moat, q.market_pos, p.l3_v2_signal
           FROM predictions p
           LEFT JOIN qualitative_scores q
             ON p.code = q.code
            AND q.scored_date = (
              SELECT MAX(sq.scored_date) FROM qualitative_scores sq WHERE sq.code = p.code
            )
           WHERE p.score_date=? AND p.total_score >= ? AND p.l3_v2_signal = 1
           ORDER BY p.total_score DESC""",
        (score_date, threshold),
    ).fetchall()
    backup = db.execute(
        """SELECT p.code, p.name, p.total_score, p.quant_score, p.entry_signal,
                  p.entry_signal_version, q.moat, q.market_pos, p.l3_v2_signal
           FROM predictions p
           LEFT JOIN qualitative_scores q
             ON p.code = q.code
            AND q.scored_date = (
              SELECT MAX(sq.scored_date) FROM qualitative_scores sq WHERE sq.code = p.code
            )
           WHERE p.score_date=? AND p.total_score >= ?
             AND (p.l3_v2_signal IS NULL OR p.l3_v2_signal != 1)
           ORDER BY p.total_score DESC""",
        (score_date, threshold),
    ).fetchall()
    radar = db.execute(
        """SELECT p.code, p.name, p.total_score, p.quant_score, p.entry_signal,
                  p.entry_signal_version, q.moat, q.market_pos, p.l3_v2_signal
           FROM predictions p
           LEFT JOIN qualitative_scores q
             ON p.code = q.code
            AND q.scored_date = (
              SELECT MAX(sq.scored_date) FROM qualitative_scores sq WHERE sq.code = p.code
            )
           WHERE p.score_date=? AND p.total_score >= ? AND p.total_score < ?
           ORDER BY p.total_score DESC""",
        (score_date, radar_min, threshold),
    ).fetchall()
    db.close()

    sections = [f"📊 A股推荐 {score_date}\n"]

    if primary:
        lines = [f"🟢 主推（买点触发，总分>={threshold:.0f}）"]
        for code, name, total, quant, entry_signal, entry_version, moat, market_pos, v2_signal in primary:
            lines.append(_format_stock_line(code, name, total, quant, entry_signal, entry_version, moat, market_pos, v2_signal))
        sections.append("\n".join(lines))

    if backup:
        lines = [f"🟡 候补（高分等待买点，总分>={threshold:.0f}）"]
        for code, name, total, quant, _entry_signal, entry_version, moat, market_pos, v2_signal in backup:
            lines.append(_format_stock_line(code, name, total, quant, 0, entry_version, moat, market_pos, v2_signal))
        sections.append("\n".join(lines))

    if radar:
        lines = [f"🔵 雷达（{radar_min:.0f}~{threshold:.0f}分，关注）"]
        for code, name, total, quant, entry_signal, entry_version, moat, market_pos, v2_signal in radar:
            lines.append(_format_stock_line(code, name, total, quant, entry_signal, entry_version, moat, market_pos, v2_signal))
        sections.append("\n".join(lines))

    total_counted = len(primary) + len(backup) + len(radar)
    if total_counted == 0:
        sections.append("今日无推荐信号")

    sections.append(f"\n共评估{total_counted}只股票（{score_date}盘后）")
    text = "\n\n".join(section for section in sections if section)

    try:
        _send(token, chat_id, text)
        logger.info(f"Telegram 推送成功：{total_counted} 只股票")
    except urllib.error.HTTPError as e:
        logger.warning(f"Telegram 推送失败（HTTP {e.code}）：{e}")
    except Exception as e:
        logger.warning(f"Telegram 推送失败：{e}")
