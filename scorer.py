# Framework A 定量评分引擎。从 weights.json 读取 breakpoints，对基本面/估值字段线性插值后加权求和。
import json
from typing import Any


class UnsupportedFrameworkError(Exception):
    pass


class InsufficientDataError(Exception):
    pass


# Framework B 暂停积累（2026-05-12）：已有 73 条历史记录保留，但不再产生新记录。
# 待 Framework A 有足够结案数据后，再重启 B 做对比实验。重启：加回 "B"。
SUPPORTED_FRAMEWORKS = {"A"}

# Framework A 非固定字段（计入 data_quality 分母）
NON_FIXED_FIELDS = {"roe_3y_avg", "net_profit_growth", "debt_ratio", "gross_margin", "pb_percentile_10y"}


def compute_daily_pb_percentile(price: float, data: dict[str, Any]) -> float | None:
    """用当日价格 + 缓存 BPS/PB 历史序列计算实时 PB 历史分位。纯函数，不写 DB。"""
    bps = data.get("bps")
    hist = data.get("pb_hist_monthly")
    if not bps or bps <= 0 or not hist or len(hist) < 12:
        return None
    current_pb = price / bps
    if current_pb <= 0:
        return None
    pct = sum(1 for x in hist if float(x) < current_pb) / len(hist) * 100
    return round(pct, 1)


def _interpolate(value: float, breakpoints: list[list[float]]) -> float:
    """在 breakpoints 上做线性插值。
    低于最小值取最小 score，高于最大值取最大 score，中间线性插值。
    invert 字段的 breakpoints 已按"高值→低分"排列，此函数不做特殊处理。
    """
    if value <= breakpoints[0][0]:
        return breakpoints[0][1]
    if value >= breakpoints[-1][0]:
        return breakpoints[-1][1]
    for i in range(len(breakpoints) - 1):
        x0, y0 = breakpoints[i]
        x1, y1 = breakpoints[i + 1]
        if x0 <= value <= x1:
            return y0 + (value - x0) / (x1 - x0) * (y1 - y0)
    return breakpoints[-1][1]


def _score_field(field_name: str, value: float | None, field_cfg: dict) -> float:
    """对单个字段计算得分。fixed 字段直接返回固定值，其余走插值逻辑。"""
    if "phase1_fixed" in field_cfg:
        return float(value) if value is not None else float(field_cfg["phase1_fixed"])
    if value is None:
        return 0.0
    if field_cfg.get("interpolate") and "breakpoints" in field_cfg:
        return _interpolate(value, field_cfg["breakpoints"])
    return 0.0


def score_stock(
    code: str,
    framework: str,
    data: dict[str, Any],
    weights: dict | None = None,
    *,
    enforce_supported: bool = True,
) -> dict:
    """
    Args:
        code:      股票代码
        framework: "A"（当前生产只支持 A）
        data:      从 cache.db 读取的字段字典，值可以为 None
        weights:   可选，直接传入 weights dict（测试用）；None 时从 weights.json 读取
        enforce_supported: 是否强制 framework 在 SUPPORTED_FRAMEWORKS 内。生产写入保持 True；
                           report-only dry-run 可传 False。

    Returns: {
        "quant_score":      float,
        "total_score":      float,
        "component_scores": dict[str, float],
        "missing_fields":   list[str],
        "data_quality":     float,
    }

    Raises:
        UnsupportedFrameworkError: framework 不在已实现集合
        InsufficientDataError:     data_quality < 0.5
    """
    if enforce_supported and framework not in SUPPORTED_FRAMEWORKS:
        raise UnsupportedFrameworkError(f"Framework '{framework}' 暂不支持，已实现：{SUPPORTED_FRAMEWORKS}")

    if weights is None:
        import os

        weights_path = os.path.join(os.path.dirname(__file__), "weights.json")
        with open(weights_path, encoding="utf-8") as f:
            weights = json.load(f)

    fw_cfg = weights["frameworks"][framework]
    all_sections = {**fw_cfg.get("fundamental", {}), **fw_cfg.get("valuation", {})}

    component_scores: dict[str, float] = {}
    missing_fields: list[str] = []

    # 统计 data_quality（只看非固定字段）
    available_non_fixed = 0
    for field_name in NON_FIXED_FIELDS:
        if data.get(field_name) is not None:
            available_non_fixed += 1

    data_quality = available_non_fixed / len(NON_FIXED_FIELDS)

    if data_quality < 0.5:
        raise InsufficientDataError(
            f"{code} 数据质量不足（{available_non_fixed}/{len(NON_FIXED_FIELDS)} 字段可用，"
            f"data_quality={data_quality:.2f} < 0.5）"
        )

    # 计算各字段得分
    quant_score = 0.0
    fixed_score = 0.0

    for field_name, field_cfg in all_sections.items():
        is_fixed = "phase1_fixed" in field_cfg
        value = data.get(field_name)
        score = _score_field(field_name, value, field_cfg)
        component_scores[field_name] = round(score, 2)

        if is_fixed:
            fixed_score += score
        else:
            if data.get(field_name) is None:
                missing_fields.append(field_name)
            quant_score += score

    total_score = quant_score + fixed_score

    return {
        "quant_score": round(quant_score, 2),
        "total_score": round(total_score, 2),
        "component_scores": component_scores,
        "missing_fields": missing_fields,
        "data_quality": round(data_quality, 3),
    }
