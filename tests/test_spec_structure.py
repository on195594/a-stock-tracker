"""Spec structure regression tests for the agent engineering governance spec.

These tests keep Phase A acceptance criteria machine-checkable instead of
self-referential prose checks.
"""
from __future__ import annotations

from pathlib import Path

SPEC_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "specs"
    / "2026-05-29-agent-engineering-governance-spec.md"
)


def _read_spec() -> str:
    return SPEC_PATH.read_text(encoding="utf-8")


def test_spec_required_governance_anchors_exist() -> None:
    spec = _read_spec()

    required_anchors = [
        "### GP1. Deterministic core first",
        "### R1. 数据源清单与所有权",
        "### AC1. Spec 完整性",
        "## 9. Stop 条件",
        "Exit Gate：Spec 结构测试通过",
        "Exit Gate：smoke/pytest 输出",
        "Exit Gate：测试和 smoke 都证明报告数字",
    ]

    missing = [anchor for anchor in required_anchors if anchor not in spec]
    assert missing == []


def test_spec_blocks_markdown_registry_escape_hatch() -> None:
    spec = _read_spec()

    assert "docs/data-source-registry.yaml" in spec
    assert "禁止仅用 Markdown 替代" in spec
    assert "registry covers all scored fields: OK" in spec


def test_spec_includes_accuracy_report_sql_anchor() -> None:
    spec = _read_spec()

    assert "SELECT COUNT(*) FROM predictions" in spec
    assert "WHERE outcome_30d IS NOT NULL AND framework = 'A';" in spec
    assert "assert_report_matches_db" in spec


def test_spec_requires_phase_a_clean_baseline_before_phase_b() -> None:
    spec = _read_spec()

    assert "clean baseline commit" in spec
    assert "chore: Phase A baseline" in spec
    assert "才开始 Phase B" in spec


def test_spec_fixes_pb_percentile_target_module() -> None:
    spec = _read_spec()

    assert "`_compute_daily_pb_percentile`，迁移目标固定为 `scorer.py`" in spec
