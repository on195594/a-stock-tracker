"""Telegram 每日信号推送。daily cron 完成后调用，推送评分 >= 阈值的股票。"""

import json
import logging
import os
import urllib.request
import urllib.error

from a_stock_tracker.data.cache import get_db
from a_stock_tracker.integrations.agent_reviewer import ReviewInput, ReviewOutput, gemini_review

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_TEXT_LIMIT = 4096
MESSAGE_TRUNCATION_MARKER = "\n\n⚠️ 消息过长已截断，请查看日志获取完整内容"

# Keep one stock's complete reviewer block below roughly 300 characters,
# including labels, indentation, and line breaks.
REVIEW_EXPLANATION_LIMIT = 110
REVIEW_OBJECTIONS_LIMIT = 60
REVIEW_QUESTIONS_LIMIT = 60


def _truncate_with_ellipsis(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


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
                  p.entry_signal_version, q.moat, q.market_pos, p.l3_v2_signal,
                  p.weights_hash, p.report_period
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
        for rank, (
            code,
            name,
            total,
            quant,
            entry_signal,
            entry_version,
            moat,
            market_pos,
            v2_signal,
            weights_hash,
            report_period,
        ) in enumerate(primary):
            stock_line = _format_stock_line(
                code, name, total, quant, entry_signal, entry_version, moat, market_pos, v2_signal
            )
            lines.append(stock_line)
            if rank >= 3:
                continue

            try:
                missing_fields = tuple(
                    field_name for field_name, value in (("moat", moat), ("market_pos", market_pos)) if value is None
                )
                review_input = ReviewInput(
                    code=code,
                    name=name,
                    score_result={
                        "total_score": total,
                        "quant_score": quant,
                        "moat": moat,
                        "market_pos": market_pos,
                        "l3_v2_signal": v2_signal,
                    },
                    data_quality_result={
                        "moat_available": moat is not None,
                        "market_pos_available": market_pos is not None,
                    },
                    missing_fields=missing_fields,
                    risk_flags=(),
                    policy_version="framework-a-primary-push-v1",
                    weights_hash=weights_hash,
                    report_period=report_period,
                )
                review: ReviewOutput = gemini_review(review_input)
                review_label = "📋 说明(降级，未调用真实LLM):" if review.is_fallback else "🤖 审查:"
                explanation = _truncate_with_ellipsis(review.explanation, REVIEW_EXPLANATION_LIMIT)
                review_lines = [f"    {review_label} {explanation}"]
                if review.objections:
                    objections = _truncate_with_ellipsis("; ".join(review.objections), REVIEW_OBJECTIONS_LIMIT)
                    review_lines.append(f"    ⚠️ 异议: {objections}")
                if review.human_questions:
                    questions = _truncate_with_ellipsis("; ".join(review.human_questions), REVIEW_QUESTIONS_LIMIT)
                    review_lines.append(f"    ❓ 待核实: {questions}")
                lines.append("\n".join(review_lines))
            except Exception as e:
                logger.warning("%s reviewer block omitted: %s", code, e)
        sections.append("\n".join(lines))

    if backup:
        lines = [f"🟡 候补（高分等待买点，总分>={threshold:.0f}）"]
        for code, name, total, quant, _entry_signal, entry_version, moat, market_pos, v2_signal in backup:
            lines.append(_format_stock_line(code, name, total, quant, 0, entry_version, moat, market_pos, v2_signal))
        sections.append("\n".join(lines))

    if radar:
        lines = [f"🔵 雷达（{radar_min:.0f}~{threshold:.0f}分，关注）"]
        for code, name, total, quant, entry_signal, entry_version, moat, market_pos, v2_signal in radar:
            lines.append(
                _format_stock_line(code, name, total, quant, entry_signal, entry_version, moat, market_pos, v2_signal)
            )
        sections.append("\n".join(lines))

    total_counted = len(primary) + len(backup) + len(radar)
    if total_counted == 0:
        sections.append("今日无推荐信号")

    sections.append(f"\n共评估{total_counted}只股票（{score_date}盘后）")
    text = "\n\n".join(section for section in sections if section)
    original_length = len(text)
    if original_length > TELEGRAM_TEXT_LIMIT:
        text = text[: TELEGRAM_TEXT_LIMIT - len(MESSAGE_TRUNCATION_MARKER)] + MESSAGE_TRUNCATION_MARKER
        logger.warning("Telegram 消息过长，已从 %d 字符截断至 %d 字符", original_length, len(text))

    try:
        _send(token, chat_id, text)
        logger.info(f"Telegram 推送成功：{total_counted} 只股票")
    except urllib.error.HTTPError as e:
        logger.warning(f"Telegram 推送失败（HTTP {e.code}）：{e}")
    except Exception as e:
        logger.warning(f"Telegram 推送失败：{e}")
