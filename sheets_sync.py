"""
sheets_sync.py — Google Sheets 双向同步模块

写入三个 tab：
  predictions_detail  每条预测记录 + outcome + alpha
  accuracy_report     按分段命中率汇总
  holdings            持仓跟踪（从 Sheet 读取成本/股数，写入当前价/盈亏）

读取：
  watchlist tab 暂不实现（当前 config.py 是真相来源）
  holdings tab：从 Sheet 读取持仓数据

规则：Sheets sync 失败只记 WARNING，不影响 pipeline daily 主流程。
"""

import json
import logging
import os
import sqlite3
from datetime import date

import gspread
from google.oauth2.service_account import Credentials

import config
from lib.cache import get_db

logger = logging.getLogger(__name__)

SHEET_URL = os.environ.get(
    "SHEETS_URL",
    "https://docs.google.com/spreadsheets/d/1-zTm43GnD-ASC6pQres82zuF5YSVAA20fKSXwjbJSHg/edit",
)
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "credentials", "service_account.json")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Tab 名称
TAB_PREDICTIONS = "predictions_detail"
TAB_ACCURACY    = "accuracy_report"
TAB_HOLDINGS    = "holdings"


# ─── 连接 ────────────────────────────────────────────────────────────────────

def _get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_or_create_tab(sh: gspread.Spreadsheet, title: str) -> gspread.Worksheet:
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=2000, cols=20)


# ─── Tab 1：predictions_detail ───────────────────────────────────────────────

_PRED_HEADERS = [
    "code", "name", "framework", "score_date", "total_score", "quant_score",
    "price_at_score", "outcome_30d", "benchmark_30d", "alpha_30d",
    "outcome_60d", "benchmark_60d", "alpha_60d",
    "outcome_90d", "benchmark_90d", "alpha_90d",
    "estimate_flag", "report_period", "weights_hash",
]


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def push_predictions(sh: gspread.Spreadsheet) -> int:
    """把 predictions 表全量写入 predictions_detail tab，返回写入行数。"""
    db = get_db()
    rows = db.execute(
        """SELECT code, name, framework, score_date, total_score, quant_score,
                  price_at_score,
                  outcome_30d, benchmark_30d, alpha_30d,
                  outcome_60d, benchmark_60d, alpha_60d,
                  outcome_90d, benchmark_90d, alpha_90d,
                  estimate_flag, report_period, weights_hash
           FROM predictions
           ORDER BY score_date DESC, total_score DESC"""
    ).fetchall()
    db.close()

    ws = _get_or_create_tab(sh, TAB_PREDICTIONS)
    data = [_PRED_HEADERS] + [[_fmt(v) for v in row] for row in rows]
    ws.update(range_name="A1", values=data)
    logger.info("predictions_detail 写入 %d 行", len(rows))
    return len(rows)


# ─── Tab 2：accuracy_report ──────────────────────────────────────────────────

_ACC_HEADERS = [
    "分段", "记录数",
    "30d命中率(绝对)", "30d命中率(vs沪深300)", "30d平均alpha",
    "60d命中率(绝对)", "60d命中率(vs沪深300)", "60d平均alpha",
]


def push_accuracy(sh: gspread.Spreadsheet) -> None:
    """按 total_score 分段写入命中率汇总。"""
    db = get_db()
    segments = [
        ("≥65分", 65, 200),
        ("55-65分", 55, 65),
        ("45-55分", 45, 55),
        ("<45分", 0, 45),
        ("全部", 0, 200),
    ]

    data = [_ACC_HEADERS]
    for label, lo, hi in segments:
        row_data = db.execute(
            """SELECT
                 COUNT(*),
                 ROUND(AVG(CASE WHEN outcome_30d > 0 THEN 1.0 ELSE 0.0 END) * 100, 1),
                 ROUND(AVG(CASE WHEN alpha_30d > 0 THEN 1.0 ELSE 0.0 END) * 100, 1),
                 ROUND(AVG(alpha_30d), 2),
                 ROUND(AVG(CASE WHEN outcome_60d > 0 THEN 1.0 ELSE 0.0 END) * 100, 1),
                 ROUND(AVG(CASE WHEN alpha_60d > 0 THEN 1.0 ELSE 0.0 END) * 100, 1),
                 ROUND(AVG(alpha_60d), 2)
               FROM predictions
               WHERE total_score >= ? AND total_score < ?
                 AND outcome_30d IS NOT NULL""",
            (lo, hi),
        ).fetchone()
        n = row_data[0] or 0
        data.append([label, str(n)] + [_fmt(v) for v in row_data[1:]])

    db.close()
    ws = _get_or_create_tab(sh, TAB_ACCURACY)
    ws.update(range_name="A1", values=data)
    logger.info("accuracy_report 写入 %d 分段", len(segments))


# ─── Tab 3：holdings ─────────────────────────────────────────────────────────

_HOLDINGS_HEADERS = [
    "code", "name", "cost_price", "shares", "cost_total",
    "latest_score", "latest_score_date", "latest_total_score",
    "memo",
]


def push_holdings_template(sh: gspread.Spreadsheet) -> None:
    """首次初始化 holdings tab，填入 watchlist 作为模板，已有内容不覆盖。"""
    ws = _get_or_create_tab(sh, TAB_HOLDINGS)
    existing = ws.get_all_values()
    if existing and existing[0] == _HOLDINGS_HEADERS:
        logger.info("holdings tab 已存在，跳过模板写入")
        return

    db = get_db()
    latest_scores = {}
    for row in db.execute(
        """SELECT code, MAX(score_date), total_score
           FROM predictions GROUP BY code"""
    ).fetchall():
        latest_scores[row[0]] = (row[1], row[2])
    db.close()

    data = [_HOLDINGS_HEADERS]
    for item in config.WATCHLIST:
        code = item["code"]
        score_info = latest_scores.get(code, ("", ""))
        data.append([
            code, item["name"],
            "",   # cost_price — 用户填写
            "",   # shares — 用户填写
            "",   # cost_total — 公式 =C2*D2
            "",   # latest_score
            score_info[0],        # latest_score_date
            _fmt(score_info[1]),  # latest_total_score
            "",   # memo
        ])

    ws.update(range_name="A1", values=data)
    logger.info("holdings 模板写入 %d 只股票", len(config.WATCHLIST))


# ─── 主入口 ──────────────────────────────────────────────────────────────────

def sync_all() -> None:
    """全量同步：push predictions + accuracy + holdings 模板。失败只记 WARNING。"""
    try:
        gc = _get_client()
        sh = gc.open_by_url(SHEET_URL)
    except Exception as e:
        logger.warning("Sheets 连接失败，跳过同步：%s", e)
        return

    try:
        n = push_predictions(sh)
        logger.info("Sheets sync: predictions_detail %d 行", n)
    except Exception as e:
        logger.warning("predictions_detail 写入失败：%s", e)

    try:
        push_accuracy(sh)
    except Exception as e:
        logger.warning("accuracy_report 写入失败：%s", e)

    try:
        push_holdings_template(sh)
    except Exception as e:
        logger.warning("holdings 写入失败：%s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sync_all()
