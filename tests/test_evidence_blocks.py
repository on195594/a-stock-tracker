"""Only synthetic documents: source integrity, table relations and recall boundaries."""

import copy

import pytest

from a_stock_tracker import evidence_blocks as b
from a_stock_tracker.research import ScreenError


def document(*pages, identity="doc", period="2026-06-30"):
    return {
        "id": identity,
        "sha256": "a" * 64,
        "period": period,
        "pages": [{"page": i, "text": text} for i, text in enumerate(pages, 1)],
    }


TEXT = """主要业务

公司主要业务为生产合成设备，营业收入保持增长，产能利用仍需观察。

主要会计数据

单位：元 币种：人民币
项目             2026年        2025年
营业收入         120.00        100.00
净利润           20.00         18.00

风险提示

主要风险为应收账款回收不确定性；审计指出需关注客户集中度。
"""


def test_blocks_bind_exact_fragments_and_group_evidence():
    doc = document(TEXT)
    blocks, rejected = b.document_blocks(doc)
    assert not rejected
    assert set().union(*(set(x["groups"]) for x in blocks)) >= {"business", "financial", "risk"}
    tables = [x for x in blocks if x["relation"]["kind"] == "same_page_table"]
    assert tables and len(tables[0]["fragments"]) == 2
    assert "2026年" in tables[0]["fragments"][1]["text"]
    for block in blocks:
        b.validate_block(block, [doc])
        for f in block["fragments"]:
            assert f["text"] == "".join(TEXT.splitlines(keepends=True)[f["start"] - 1 : f["end"]])


def test_tampering_ranges_roles_and_context_changes_identity():
    doc = document(TEXT)
    block = b.document_blocks(doc)[0][0]
    for key, value in [
        ("text", "伪造内容"),
        ("start", 0),
        ("document_hash", "b" * 64),
        ("role", "header"),
    ]:
        changed = copy.deepcopy(block)
        changed["fragments"][0][key] = value
        with pytest.raises((ScreenError, ValueError)):
            b.validate_block(changed, [doc])
    fragments = copy.deepcopy(block["fragments"])
    one = b.bundle(fragments, {"kind": "paragraph"})
    two = b.bundle(fragments, {"kind": "paragraph", "proof": "different"})
    assert one["id"] != two["id"]


def test_missing_units_are_not_silently_treated_as_valid_table():
    doc = document(
        "主要会计数据\n\n项目       本期        上期\n营业收入    120.00      100.00\n净利润      20.00       18.00\n"
    )
    blocks, rejected = b.document_blocks(doc)
    assert not blocks
    assert rejected[0]["reason"] == "table_context_missing"


def test_large_table_keeps_header_and_footnote_in_separate_fragments():
    rows = "".join(f"营业收入项目{i}       {i}.00       {i + 1}.00\n" for i in range(120))
    doc = document(
        "主要会计数据\n\n单位：元\n项目           本期         上期\n"
        + rows
        + "\n注：比较数据已调整，金额仅为合成例子。\n"
    )
    blocks, rejected = b.document_blocks(doc)
    tables = [x for x in blocks if x["relation"]["kind"] == "same_page_table"]
    assert len(tables) > 1 and not rejected
    assert all(
        {f["role"] for f in x["fragments"]} == {"main", "header", "footnote"} for x in tables
    )
    assert all(len(b.compact(f["text"])) <= 1600 for x in tables for f in x["fragments"])


def test_recall_accounts_for_omission_and_changes_cache_with_inputs():
    doc = document(TEXT)
    selected = b.select_blocks([doc], {"code": "600001.SH"})
    assert all(selected["coverage"][g]["selected"] for g in b.REQUIRED_GROUPS)
    tiny = b.select_blocks([doc], {"code": "600001.SH"}, limit=1)
    assert tiny["blocks"] == [] and tiny["expanded"]
    assert tiny["coverage"]["risk"]["omitted"]
    assert tiny["cache_key"] != selected["cache_key"]
    other = b.select_blocks([doc], {"code": "600001.SH", "risk_indicator": "changed"})
    assert other["cache_key"] != selected["cache_key"]


def test_review_context_deduplicates_shared_pages():
    doc = document(TEXT, TEXT, TEXT)
    blocks, _ = b.document_blocks(doc)
    pages = b.review_pages(blocks, [doc])
    assert [p["page"] for p in pages] == [1, 2, 3]


def test_single_numeric_column_without_unit_is_not_prose():
    doc = document("主要会计数据\n\n项目       本期\n营业收入    120.00\n净利润      20.00\n")
    blocks, rejected = b.document_blocks(doc)
    assert not blocks
    assert rejected[0]["reason"] == "table_context_missing"


def test_explicit_cross_page_relation_keeps_marker_and_repeated_header():
    doc = document(
        "主要会计数据\n\n单位：元\n项目      本期      上期\n营业收入    120.00    100.00\n",
        "主要会计数据（续）\n\n项目      本期      上期\n归属净利润      20.00     18.00\n",
    )
    blocks, rejected = b.document_blocks(doc)
    continued = [block for block in blocks if block["relation"]["kind"] == "explicit_continuation"]
    assert not rejected and len(continued) == 1
    block = continued[0]
    assert {f["page"] for f in block["fragments"]} == {1, 2}
    assert any(f["role"] == "relation" and "（续）" in f["text"] for f in block["fragments"])
    b.validate_block(block, [doc])
    for altered in ["主要会计数据", "另一张主要会计数据（续）"]:
        wrong = copy.deepcopy(doc)
        wrong["pages"][1]["text"] = wrong["pages"][1]["text"].replace("主要会计数据（续）", altered)
        result, rejected = b.document_blocks(wrong)
        assert not any(x["relation"]["kind"] == "explicit_continuation" for x in result)
        assert rejected


def test_recomputed_hash_does_not_authorize_fabricated_relation():
    doc = document(TEXT)
    block = copy.deepcopy(b.document_blocks(doc)[0][0])
    block["relation"] = {"kind": "explicit_continuation", "previous_page": 99}
    block["id"] = b.digest({k: v for k, v in block.items() if k != "id"})
    with pytest.raises(ScreenError, match="Unverified evidence relation"):
        b.validate_block(block, [doc])


def test_competing_table_unit_inside_span_is_rejected():
    doc = document(
        "主要会计数据\n\n单位：元\n项目     本期    上期\n营业收入    120.00    100.00\n单位：万元\n另一项目    本期    上期\n营业收入    220.00    200.00\n"
    )
    blocks, rejected = b.document_blocks(doc)
    assert not blocks and rejected[0]["reason"] == "ambiguous_table"


def test_internal_fragments_do_not_pull_unrelated_neighbor_pages():
    doc = document("无关前页", TEXT + "\n页内后续段落。\n", "无关后页")
    block = next(x for x in b.document_blocks(doc)[0] if x["relation"]["kind"] == "same_page_table")
    assert [p["page"] for p in b.review_pages([block], [doc])] == [2]


def test_evidence_cache_binds_all_inputs_and_revalidates_sources(tmp_path):
    doc = document(TEXT)
    root = tmp_path / "cache"
    first = b.select_blocks([doc], {"code": "600001.SH"}, cache_root=root)
    assert b.select_blocks([doc], {"code": "600001.SH"}, cache_root=root) == first
    changed = b.select_blocks([doc], {"code": "600001.SH", "period": "different"}, cache_root=root)
    assert changed["cache_key"] != first["cache_key"]
    assert len(list(root.glob("evidence-*.json"))) == 2
    for path in root.glob("evidence-*.json"):
        path.write_text("{}")
    with pytest.raises(ScreenError, match="checksum"):
        b.select_blocks([doc], {"code": "600001.SH"}, cache_root=root)


def test_recall_keeps_specific_balance_table_ahead_of_broad_policies():
    policy = "本公司按会计政策评估应收账款、其他应收款、现金流量、公允价值变动及减值风险。"
    pages = [f"政策编号{i}。{policy}" for i in range(4)]
    pages.append(
        "资产负债状况\n\n单位：元\n项目             本期        上期\n"
        "应收账款         120.00      100.00\n其他应收款       20.00       10.00\n"
    )
    doc = document(*pages)
    selected = b.select_blocks([doc], {"code": "600001.SH"})
    assert any(
        x["relation"]["kind"] == "same_page_table" and "应收账款" in x["fragments"][0]["text"]
        for x in selected["blocks"]
    )


def test_recall_puts_mdna_risk_section_ahead_of_policy_risk_text():
    # Production 000858/603606/600519: the MD&A risk section lost to policy text and tables.
    policy = "本公司按会计政策评估应收账款减值风险与信用风险，并关注诉讼与担保事项。"
    pages = [TEXT, *(f"政策编号{i}。{policy}" for i in range(5))]
    pages.insert(
        0, "十、重大风险提示\n本公司已在本报告中阐述了公司可能面对的风险，敬请投资者关注。\n"
    )
    pages.append(
        "五、其他披露事项\n(一) 可能面对的风险\n  一是有效需求恢复不及预期；二是行业持续深度调整。\n"
    )
    selected = b.select_blocks([document(*pages)], {"code": "600001.SH"})
    texts = [x["fragments"][0]["text"] for x in selected["blocks"] if "risk" in x["groups"]]
    assert any("有效需求恢复不及预期" in t for t in texts)
    assert not any("敬请投资者关注" in t for t in texts)


def test_validating_many_blocks_derives_each_document_once(monkeypatch):
    # Production: recent() re-verified every block by rebuilding all blocks of its document,
    # so listing 20 reports took 17.6 s and the web client timed out.
    doc = document(TEXT)
    blocks = b.document_blocks(doc)[0]
    assert len(blocks) > 1
    calls = []
    original = b.document_blocks
    monkeypatch.setattr(b, "document_blocks", lambda d: calls.append(d["id"]) or original(d))
    derived: dict = {}
    for block in blocks:
        b.validate_block(block, [doc], derived)
    assert calls == ["doc"]
    forged = copy.deepcopy(blocks[0])
    forged["relation"] = {"kind": "paragraph", "forged": True}
    with pytest.raises(ScreenError):
        b.validate_block(forged, [doc], derived)
