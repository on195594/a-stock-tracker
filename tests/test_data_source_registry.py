from __future__ import annotations

import re
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "docs" / "data-source-registry.yaml"
WEIGHTS_PATH = Path(__file__).resolve().parents[1] / "weights.json"

REQUIRED_KEYS = {
    "field", "owner", "requirement", "source_primary", "source_fallback",
    "cache", "refresh", "affects_scoring", "affects_outcome", "failure_behavior",
}


def _registry_text() -> str:
    return REGISTRY_PATH.read_text(encoding="utf-8")


def _fields() -> set[str]:
    return set(re.findall(r"^\s*-\s+field:\s*([a-zA-Z0-9_]+)\s*$", _registry_text(), re.M))


def _blocks() -> dict[str, str]:
    text = _registry_text()
    parts = re.split(r"(?m)^\s*-\s+field:\s*", text)
    blocks = {}
    for part in parts[1:]:
        first, _, rest = part.partition("\n")
        blocks[first.strip()] = first + "\n" + rest
    return blocks


def _block_keys(field: str) -> set[str]:
    block = _blocks()[field]
    keys = {m.group(1) for m in re.finditer(r"^\s*([a-zA-Z0-9_]+):", block, re.M)}
    keys.add("field")
    return keys


def test_registry_file_exists() -> None:
    assert REGISTRY_PATH.exists()


def test_registry_blocks_have_required_keys() -> None:
    for field in _blocks():
        assert REQUIRED_KEYS <= _block_keys(field), field


def test_registry_covers_framework_a_scored_fields() -> None:
    import json
    weights = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
    framework_a = weights["frameworks"]["A"]
    expected = set(framework_a["fundamental"]) | set(framework_a["valuation"])
    assert expected <= _fields()


def test_registry_covers_report_contract_fields() -> None:
    expected = {
        "framework", "weights_hash", "report_period", "price_at_score",
        "outcome_30d", "outcome_60d", "outcome_90d",
        "benchmark_30d", "benchmark_60d", "benchmark_90d",
    }
    assert expected <= _fields()


def test_registry_includes_fallback_and_failure_language() -> None:
    text = _registry_text()
    for needle in ["source_fallback:", "failure_behavior:", "fallback", "missing"]:
        assert needle in text


def test_registry_covers_market_data_boundary_fields() -> None:
    assert {
        "daily_bars",
        "daily_bars_volume_unit",
        "entry_signal",
        "entry_signal_status",
        "entry_signal_reason",
    } <= _fields()


def test_market_data_fields_include_user_visible_failure_contract() -> None:
    required_keys = {
        "owner",
        "source_primary",
        "source_fallback",
        "cache",
        "refresh",
        "failure_behavior",
        "user_visible_impact",
    }

    for field in ["daily_bars", "entry_signal", "entry_signal_status", "entry_signal_reason"]:
        assert required_keys <= _block_keys(field), field
