import os

from a_stock_tracker.paths import ACCURACY_REPORT_PATH as _ACCURACY_REPORT_PATH
from a_stock_tracker.paths import LOG_DIR as _LOG_DIR
from a_stock_tracker.paths import PROJECT_ROOT, WEIGHTS_PATH as _WEIGHTS_PATH

WATCHLIST: list[dict] = [
    # ── 银行与金融蓝筹 ──────────────────────────────────────
    {"code": "601288", "name": "农业银行"},
    {"code": "601939", "name": "建设银行"},
    {"code": "601988", "name": "中国银行"},
    {"code": "601398", "name": "工商银行"},
    {"code": "600036", "name": "招商银行"},
    {"code": "002736", "name": "国信证券"},
    {"code": "600999", "name": "招商证券"},
    # ── 能源与资源 ─────────────────────────────────────────
    {"code": "601225", "name": "陕西煤业"},
    {"code": "601088", "name": "中国神华"},
    {"code": "600938", "name": "中国海油"},
    {"code": "601857", "name": "中国石油"},
    {"code": "600900", "name": "长江电力"},
    {"code": "600023", "name": "浙能电力"},
    {"code": "601991", "name": "大唐发电"},
    {"code": "601600", "name": "中国铝业"},
    {"code": "601899", "name": "紫金矿业"},
    {"code": "600362", "name": "江西铜业"},
    # ── 工业、科技与制造 ──────────────────────────────────────
    {"code": "601668", "name": "中国建筑"},
    {"code": "002594", "name": "比亚迪"},
    {"code": "601138", "name": "工业富联"},
    {"code": "002475", "name": "立讯精密"},
    {"code": "002119", "name": "康强电子"},
    {"code": "600089", "name": "特变电工"},
    {"code": "603606", "name": "东方电缆"},
    {"code": "601179", "name": "中国西电"},
    {"code": "002050", "name": "三花智控"},
    {"code": "002648", "name": "卫星化学"},
    {"code": "600019", "name": "宝钢股份"},
    {"code": "000786", "name": "北新建材"},
    {"code": "002201", "name": "九鼎新材"},
    # ── 电信、医药及消费 ──────────────────────────────────────
    {"code": "600941", "name": "中国移动"},
    {"code": "601728", "name": "中国电信"},
    {"code": "000963", "name": "华东医药"},
    {"code": "601933", "name": "永辉超市"},
    {"code": "600785", "name": "新华百货"},
]

DB_PATH = str(PROJECT_ROOT / "tracker.db")

# Materialization feature flags are read at call time to avoid import-time
# environment leakage during tests and test runners.
FEATURE_FLAG_VALUATION_DOMAIN = "TUSHARE_PRIMARY_VALUATION"
FEATURE_FLAG_FINANCIAL_DOMAIN = "TUSHARE_PRIMARY_FINANCIAL"
FEATURE_FLAG_DIVIDEND_DOMAIN = "TUSHARE_PRIMARY_DIVIDEND"


def get_materialization_feature_flag(name: str) -> bool:
    """Return a boolean feature-flag state from ``os.environ``.

    Allowed states are ``off`` and ``on`` (case-insensitive). Unknown values raise
    ``ValueError`` to keep failures loud and fail-closed.
    """

    value = os.getenv(name, "off").strip().lower()
    if value not in {"off", "on"}:
        raise ValueError(f"Invalid feature flag value {value!r} for {name}")
    return value == "on"


LOG_DIR = str(_LOG_DIR)
WEIGHTS_PATH = str(_WEIGHTS_PATH)
ACCURACY_REPORT_PATH = str(_ACCURACY_REPORT_PATH)
