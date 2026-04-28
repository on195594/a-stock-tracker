"""
sheets_sync.py — Google Sheets 同步模块

写入三个 tab：
  predictions_detail  每条预测记录 + outcome + alpha（每日全量覆盖）
  accuracy_report     按分段命中率汇总（首次初始化写入公式，之后不覆盖）
  holdings            持仓跟踪模板（首次初始化写入 watchlist，之后不覆盖）

规则：Sheets sync 失败只记 WARNING，不影响 pipeline daily 主流程。
"""

import logging
import os

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

# schema contract: 列顺序与 accuracy_report 公式中的列字母绑定，加列需同步更新公式
_PRED_HEADERS = [
    "code", "name", "framework", "score_date", "total_score", "quant_score",  # A-F
    "price_at_score",                                                           # G
    "outcome_30d", "benchmark_30d", "alpha_30d",                               # H-J
    "outcome_60d", "benchmark_60d", "alpha_60d",                               # K-M
    "outcome_90d", "benchmark_90d", "alpha_90d",                               # N-P
    "estimate_flag", "report_period", "weights_hash",                          # Q-S
]


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _cell(v):
    """保留数值类型（float/int），None 转空字符串。RAW 模式写入后 Sheets 存为数字。"""
    return "" if v is None else v


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
    data = [_PRED_HEADERS] + [[_cell(v) for v in row] for row in rows]
    ws.update(range_name="A1", values=data)
    logger.info("predictions_detail 写入 %d 行", len(rows))
    return len(rows)


# ─── Tab 2：accuracy_report ──────────────────────────────────────────────────
#
# 列映射（_PRED_HEADERS 顺序，加列需同步更新下方公式）：
#   E=total_score | H=outcome_30d | J=alpha_30d | K=outcome_60d | M=alpha_60d

_ACC_HEADERS = [
    "分段", "记录数",
    "30d命中率(绝对)", "30d命中率(vs沪深300)", "30d平均alpha",
    "60d命中率(绝对)", "60d命中率(vs沪深300)", "60d平均alpha",
]

_ACC_SEGMENTS = [
    ("≥65分",  65, 200),
    ("55-65分", 55,  65),
    ("45-55分", 45,  55),
    ("<45分",    0,  45),
    ("全部",     0, 200),
]

_P = TAB_PREDICTIONS  # 公式引用的源 tab，与 TAB_PREDICTIONS 保持同步


def _acc_row(label: str, lo: int, hi: int) -> list:
    """生成一个分段的8列内容（标签 + 7个 COUNTIFS/AVERAGEIFS 公式字符串）。"""
    E = f"{_P}!E:E"
    H = f"{_P}!H:H"
    J = f"{_P}!J:J"
    K = f"{_P}!K:K"
    M = f"{_P}!M:M"
    sc = f'{E},">="&{lo},{E},"<"&{hi},'  # score range condition

    count = f'=COUNTIFS({sc}{H},"<>")'
    h30   = f'=IFERROR(COUNTIFS({sc}{H},">"&0)/COUNTIFS({sc}{H},"<>"),"")'
    vs30  = f'=IFERROR(COUNTIFS({sc}{J},">"&0)/COUNTIFS({sc}{J},"<>"),"")'
    avg30 = f'=IFERROR(AVERAGEIFS({J},{sc}{H},"<>"),"")'
    h60   = f'=IFERROR(COUNTIFS({sc}{K},">"&0)/COUNTIFS({sc}{K},"<>"),"")'
    vs60  = f'=IFERROR(COUNTIFS({sc}{M},">"&0)/COUNTIFS({sc}{M},"<>"),"")'
    avg60 = f'=IFERROR(AVERAGEIFS({M},{sc}{K},"<>"),"")'
    return [label, count, h30, vs30, avg30, h60, vs60, avg60]


def _init_accuracy_formula_tab(sh: gspread.Spreadsheet) -> None:
    """首次初始化 accuracy_report tab：写入 COUNTIFS/AVERAGEIFS 公式。非空则跳过。"""
    ws = _get_or_create_tab(sh, TAB_ACCURACY)
    if ws.get_all_values():
        logger.info("accuracy_report tab 已存在，跳过初始化")
        return
    data = [_ACC_HEADERS] + [_acc_row(label, lo, hi) for label, lo, hi in _ACC_SEGMENTS]
    ws.update(range_name="A1", values=data, value_input_option="USER_ENTERED")
    logger.info("accuracy_report 公式写入完成（%d 分段）", len(_ACC_SEGMENTS))


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
    for i, item in enumerate(config.WATCHLIST):
        row_num = i + 2  # row 1 = headers，数据从 row 2 起
        code = item["code"]
        score_info = latest_scores.get(code, ("", ""))
        data.append([
            code, item["name"],
            "",                          # cost_price — 用户填写
            "",                          # shares — 用户填写
            f"=C{row_num}*D{row_num}",   # cost_total
            "",                          # latest_score
            score_info[0],               # latest_score_date
            _fmt(score_info[1]),         # latest_total_score
            "",                          # memo
        ])

    ws.update(range_name="A1", values=data, value_input_option="USER_ENTERED")
    logger.info("holdings 模板写入 %d 只股票", len(config.WATCHLIST))


# ─── 主入口 ──────────────────────────────────────────────────────────────────

def sync_all() -> None:
    """全量同步：写入 predictions_detail；首次初始化 accuracy_report 公式和 holdings 模板。"""
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
        _init_accuracy_formula_tab(sh)
    except Exception as e:
        logger.warning("accuracy_report 初始化失败：%s", e)

    try:
        push_holdings_template(sh)
    except Exception as e:
        logger.warning("holdings 写入失败：%s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sync_all()
