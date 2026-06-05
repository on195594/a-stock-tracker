"""
sheets_sync.py — Google Sheets 同步模块

写入两个 tab：
  predictions_detail  每条预测记录 + outcome + alpha（每日全量覆盖）
  accuracy_report     按分段命中率汇总（schema hash 未变则跳过，变则重写）

规则：Sheets sync 失败只记 WARNING，不影响 pipeline daily 主流程。
"""

import hashlib
import json
import logging
import os

import gspread
from gspread.utils import ValueInputOption
from google.oauth2.service_account import Credentials

from lib.cache import get_db

logger = logging.getLogger(__name__)

SHEET_URL = os.environ.get("SHEETS_URL")
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "credentials", "service_account.json")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Tab 名称
TAB_PREDICTIONS = "predictions_detail"
TAB_ACCURACY    = "accuracy_report"


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
    "90d命中率(绝对)", "90d命中率(vs沪深300)", "90d平均alpha",
]

def _load_thresholds() -> dict:
    try:
        with open(os.path.join(os.path.dirname(__file__), "weights.json"), encoding="utf-8") as _f:
            return json.load(_f).get("thresholds", {})
    except Exception:
        return {}

_thr      = _load_thresholds()
_STR      = _thr.get("buy_strong",   44)
_MOD      = _thr.get("buy_moderate", 35)
_LGT      = _thr.get("buy_light",    26)

# 分段从 weights.json thresholds 动态读取，hash 自动感知阈值变更
_ACC_SEGMENTS = [
    (f"≥{_STR}分",            _STR,  200),
    (f"{_MOD}-{_STR}分",      _MOD,  _STR),
    (f"{_LGT}-{_MOD}分",      _LGT,  _MOD),
    (f"<{_LGT}分",            0,     _LGT),
    ("全部",                   0,     200),
]

_P = TAB_PREDICTIONS  # 公式引用的源 tab，与 TAB_PREDICTIONS 保持同步


def _acc_schema_hash() -> str:
    """_ACC_HEADERS + _ACC_SEGMENTS 的 md5[:6]，用于检测 schema 变更。"""
    raw = str(_ACC_HEADERS) + str(_ACC_SEGMENTS)
    return hashlib.md5(raw.encode()).hexdigest()[:6]


def _acc_row(label: str, lo: int, hi: int) -> list:
    """生成一个分段的11列内容（标签 + 10个 COUNTIFS/AVERAGEIFS 公式字符串）。"""
    E = f"{_P}!E:E"
    H = f"{_P}!H:H"
    J = f"{_P}!J:J"
    K = f"{_P}!K:K"
    M = f"{_P}!M:M"
    N = f"{_P}!N:N"
    P = f"{_P}!P:P"
    sc = f'{E},">="&{lo},{E},"<"&{hi},'  # score range condition

    count   = f'=COUNTIFS({sc}{H},"<>")'
    h30     = f'=IFERROR(COUNTIFS({sc}{H},">"&0)/COUNTIFS({sc}{H},"<>"),"")'
    vs30    = f'=IFERROR(COUNTIFS({sc}{J},">"&0)/COUNTIFS({sc}{J},"<>"),"")'
    avg30   = f'=IFERROR(AVERAGEIFS({J},{sc}{H},"<>"),"")'
    h60     = f'=IFERROR(COUNTIFS({sc}{K},">"&0)/COUNTIFS({sc}{K},"<>"),"")'
    vs60    = f'=IFERROR(COUNTIFS({sc}{M},">"&0)/COUNTIFS({sc}{M},"<>"),"")'
    avg60   = f'=IFERROR(AVERAGEIFS({M},{sc}{K},"<>"),"")'
    h90     = f'=IFERROR(COUNTIFS({sc}{N},">"&0)/COUNTIFS({sc}{N},"<>"),"")'
    vs90    = f'=IFERROR(COUNTIFS({sc}{P},">"&0)/COUNTIFS({sc}{P},"<>"),"")'
    avg90   = f'=IFERROR(AVERAGEIFS({P},{sc}{N},"<>"),"")'
    return [label, count, h30, vs30, avg30, h60, vs60, avg60, h90, vs90, avg90]


def _init_accuracy_formula_tab(sh: gspread.Spreadsheet) -> None:
    """初始化 accuracy_report tab：写入 COUNTIFS/AVERAGEIFS 公式。
    schema hash 未变则跳过；hash 变更（分段/列名修改）则自动重写。
    hash 存储在 header 行末尾的额外列，不影响数据区公式。
    """
    ws = _get_or_create_tab(sh, TAB_ACCURACY)
    existing = ws.get_all_values()
    if existing:
        first_row = existing[0]
        stored_hash = first_row[len(_ACC_HEADERS)] if len(first_row) > len(_ACC_HEADERS) else None
        if stored_hash == _acc_schema_hash():
            logger.info("accuracy_report tab schema 未变，跳过初始化")
            return
        logger.info("accuracy_report schema 变更（%s → %s），重写", stored_hash, _acc_schema_hash())
    current_hash = _acc_schema_hash()
    data = [_ACC_HEADERS + [current_hash]] + [_acc_row(label, lo, hi) for label, lo, hi in _ACC_SEGMENTS]
    ws.update(range_name="A1", values=data, value_input_option=ValueInputOption.user_entered)
    logger.info("accuracy_report 公式写入完成（%d 分段，schema=%s）", len(_ACC_SEGMENTS), current_hash)


# ─── 主入口 ──────────────────────────────────────────────────────────────────

def sync_all() -> None:
    """全量同步：写入 predictions_detail，并初始化 accuracy_report 公式。"""
    if not SHEET_URL:
        logger.warning("SHEETS_URL 未设置，跳过 Sheets sync（请在 .env 中配置）")
        return
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sync_all()
