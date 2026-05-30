import json

from lib.data_quality import evaluate_data_quality


def _complete_data() -> dict:
    return {
        "roe_3y_avg": 16.5,
        "net_profit_growth": 5.5,
        "debt_ratio": 45.0,
        "gross_margin": 56.8,
        "pb_percentile_10y": 18.0,
        "report_period": "2024-09-30",
        "price_at_score": 35.2,
        "bps": 12.0,
        "pb_hist_monthly": [1.0] * 24,
    }


def test_complete_data_quality_is_acceptable() -> None:
    result = evaluate_data_quality("600036", _complete_data())
    assert result.is_acceptable is True
    assert result.missing_required == ()


def test_missing_price_is_visible_but_degradable() -> None:
    data = _complete_data()
    data["price_at_score"] = None
    result = evaluate_data_quality("600036", data)
    assert result.is_acceptable is True
    assert "price_at_score" not in result.missing_required
    assert any(field.name == "price_at_score" and field.status == "missing" for field in result.fields)


def test_missing_pb_percentile_blocks_acceptability() -> None:
    data = _complete_data()
    data["pb_percentile_10y"] = None
    result = evaluate_data_quality("600036", data)
    assert result.is_acceptable is False
    assert result.missing_required == ("pb_percentile_10y",)


def test_fallback_source_is_visible() -> None:
    result = evaluate_data_quality("600036", _complete_data(), {"price_at_score": "tencent_hist"})
    assert result.fallback_fields == ("price_at_score",)
    assert any(field.reason == "tencent_hist" for field in result.fields)


def test_pb_percentile_marks_cached_value_stale_without_daily_inputs() -> None:
    data = _complete_data()
    data["price_at_score"] = None
    result = evaluate_data_quality("600036", data)
    pb_field = next(field for field in result.fields if field.name == "pb_percentile_10y")
    assert pb_field.status == "stale"
    assert "cached_without_daily_inputs:price_at_score" in pb_field.reason


def test_result_is_json_serializable() -> None:
    json.dumps(evaluate_data_quality("600036", _complete_data()).as_dict(), ensure_ascii=False)
