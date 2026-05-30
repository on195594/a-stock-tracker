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


def test_registry_file_exists() -> None:
    assert REGISTRY_PATH.exists()


def test_registry_blocks_have_required_keys() -> None:
    for field, block in _blocks().items():
        keys = {m.group(1) for m in re.finditer(r"^\s*([a-zA-Z0-9_]+):", block, re.M)}
        keys.add("field")  # _blocks() uses field as split delimiter; add it back for REQUIRED_KEYS.
        assert REQUIRED_KEYS <= keys, field


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
