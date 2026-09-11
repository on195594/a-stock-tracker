"""Telegram 每日观察名单推送。daily cron 完成后调用，展示评分 >= 阈值的股票。"""

import json
import logging
import os
import urllib.request
import urllib.error
from dataclasses import dataclass

from a_stock_tracker.data.cache import get_db
from a_stock_tracker.qualitative.production import DIMENSION_NAMES, SCORE_RANGES

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_TEXT_LIMIT = 4096
MESSAGE_TRUNCATION_MARKER = "\n\n⚠️ 消息过长已截断，请查看日志获取完整内容"


@dataclass(frozen=True)
class PredictionQualitativeSnapshot:
    scores: dict[str, int]
    sources: dict[str, str]
    mode: str


def _parse_json_object(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _sources_match_mode(sources: dict[str, str], mode: str) -> bool:
    unique_sources = set(sources.values())
    return (
        (mode == "v1" and unique_sources == {"v1"})
        or (mode == "v2" and unique_sources == {"v2"})
        or (mode == "hybrid_v2" and unique_sources == {"v1", "v2"})
    )


def _prediction_qualitative_snapshot(
    code: str,
    snapshot_json: object,
    sources_json: object,
    mode: object,
) -> PredictionQualitativeSnapshot | None:
    scores_raw = _parse_json_object(snapshot_json)
    sources_raw = _parse_json_object(sources_json)
    if scores_raw is None or sources_raw is None or not isinstance(mode, str):
        logger.warning("%s prediction 缺少合法定性快照，不以当前缓存替代历史判断", code)
        return None
    if set(scores_raw) != set(DIMENSION_NAMES) or set(sources_raw) != set(DIMENSION_NAMES):
        logger.warning("%s prediction 定性快照字段漂移，不展示定性解读", code)
        return None
    scores: dict[str, int] = {}
    sources: dict[str, str] = {}
    for dimension in DIMENSION_NAMES:
        score = scores_raw[dimension]
        source = sources_raw[dimension]
        minimum, maximum = SCORE_RANGES[dimension]
        if isinstance(score, bool) or not isinstance(score, int) or not minimum <= score <= maximum:
            logger.warning("%s prediction 定性快照分值无效，不展示定性解读", code)
            return None
        if not isinstance(source, str) or source not in {"v1", "v2"}:
            logger.warning("%s prediction 定性快照来源无效，不展示定性解读", code)
            return None
        scores[dimension] = score
        sources[dimension] = source
    if not _sources_match_mode(sources, mode):
        logger.warning("%s prediction 定性快照模式不一致，不展示定性解读", code)
        return None
    return PredictionQualitativeSnapshot(scores=scores, sources=sources, mode=mode)


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
    moat: int | None,
    market_pos: int | None,
) -> str:
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

    return "  ".join(parts) if parts else ""


def _format_stock_line(
    code: str,
    name: str | None,
    total_score: float,
    quant_score: float,
    moat: int | None,
    market_pos: int | None,
    l3_v2_signal: int | None = None,
) -> str:
    label = f"{name}({code})" if name else code
    interp = _interpret(moat, market_pos)
    if l3_v2_signal == 1:
        l3_tag = "L3极端风险提示:正常(v2)"
    elif l3_v2_signal == 0:
        l3_tag = "L3极端风险提示:触发(v2)"
    else:
        l3_tag = "L3极端风险提示:不可用(v2)"

    line = f"  {label}  总分:{total_score:.1f}  量化:{quant_score:.1f}  {l3_tag}"
    if interp:
        line += f"\n    {interp}"
    return line


def _frozen_dates(raw: object) -> str:
    snapshot = _parse_json_object(raw)
    dates = snapshot.get("qualitative_as_of") if snapshot else None
    if not isinstance(dates, dict) or set(dates) != set(DIMENSION_NAMES):
        return "日期未记录"
    values = sorted({value for value in dates.values() if isinstance(value, str)})
    if any(not isinstance(value, str) for value in dates.values()):
        values.append("部分未知/固定回退")
    return "/".join(values)


def push_daily_signals(score_date: str, threshold: float = 44.0, radar_min: float = 35.0) -> None:
    """Show research observations, using only the prediction's immutable snapshots."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 未设置，跳过推送")
        return

    db = get_db()
    try:
        rows = db.execute(
            """SELECT code, name, total_score, quant_score, l3_v2_signal,
                      qualitative_snapshot_json, qualitative_sources_json, qualitative_mode,
                      scoring_snapshot_json
               FROM predictions WHERE framework='A' AND score_date=? AND total_score IS NOT NULL
               ORDER BY total_score DESC, code""",
            (score_date,),
        ).fetchall()
    finally:
        db.close()
    primary = [row for row in rows if row[2] >= threshold]
    radar = [row for row in rows if radar_min <= row[2] < threshold]
    # Safety context stays before the stock list so truncation cannot remove it.
    sections = [
        f"📊 A股未验证观察名单 {score_date}\n",
        "仅供研究，不构成买入建议。报告验证Q5约前20%，不是本名单；分数阈值未通过收益验证。\n"
        "跨行业分数有适用性偏差；无仓位/退出规则。L3正常不代表安全或买点。\n"
        "以下定性分均为冻结缓存（非当日核实），情绪分不代表当前情绪。",
    ]
    for title, candidates in (
        (f"🟢 高分观察（总分>={threshold:.0f}）", primary),
        (f"🔵 一般观察（{radar_min:.0f}~{threshold:.0f}分）", radar),
    ):
        if not candidates:
            continue
        lines = [title]
        for code, name, total, quant, signal, scores_json, sources_json, mode, scoring_json in candidates:
            snapshot = _prediction_qualitative_snapshot(code, scores_json, sources_json, mode)
            lines.append(
                _format_stock_line(
                    code,
                    name,
                    total,
                    quant,
                    snapshot.scores["moat"] if snapshot else None,
                    snapshot.scores["market_pos"] if snapshot else None,
                    signal,
                )
            )
            lines.append(
                f"    定性冻结日期：{_frozen_dates(scoring_json)}"
                if snapshot
                else "    历史定性快照缺失，不展示当前缓存"
            )
        sections.append("\n".join(lines))

    total_counted = len(primary) + len(radar)
    if total_counted == 0:
        sections.append("今日无观察信号")
    sections.append(f"\n共评估{len(rows)}只，列入观察{total_counted}只（{score_date}盘后）")
    text = "\n\n".join(sections)
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
