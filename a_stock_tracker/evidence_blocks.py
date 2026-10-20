"""Deterministic, source-bound evidence bundles; no data acquisition or model calls."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from a_stock_tracker.automation import digest, encoded, locked_root, read_state, save_state
from a_stock_tracker.research import ScreenError

VERSION = "evidence-bundles-v1"
REPORT_VERSION = "cited-machine-research-v4"
GROUPS = {
    "business": ("主要业务", "主营业务", "经营模式", "主营产品", "主要产品"),
    "financial": ("主要会计数据", "营业收入", "净利润", "每股收益", "净资产收益率"),
    "cashflow": ("现金流量", "经营活动", "现金流"),
    "nonrecurring": ("非经常性损益", "非经常性", "公允价值变动"),
    "balance": ("资产负债", "应收账款", "其他应收款", "短期借款", "偿债"),
    "risk": ("风险", "审计", "减值", "诉讼", "担保", "关联交易", "不确定性"),
}
REQUIRED_GROUPS = ("business", "financial", "risk")
ROLES = {"main", "header", "unit", "period", "footnote", "relation"}
NUMBER = re.compile(r"[-+]?\d[\d,.]*(?:%|％)?")
UNIT = re.compile(r"单位\s*[:：]|币种\s*[:：]")
TABLE_TITLE = re.compile(r"[^\n]{2,60}(?:表|状况|项目和金额|会计数据|财务指标)[）)\s]*$")
PAGE_FOOTER = re.compile(r"\s*\d+\s*/\s*\d+\s*")


class Fragment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document: str
    document_hash: str
    page: int = Field(ge=1)
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    role: str
    text: str


class EvidenceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    version: str
    fragments: list[Fragment] = Field(min_length=1, max_length=6)
    groups: list[str]
    matches: dict[str, list[str]]
    relation: dict[str, Any]


def compact(text: str) -> str:
    return "".join(text.split())


def context_size(value: Any) -> int:
    """UTF-8 byte estimate, deliberately conservative; not provider token accounting."""
    return len(value.encode("utf-8")) if isinstance(value, str) else len(encoded(value))


def paragraphs(lines: list[str]) -> list[tuple[int, int]]:
    result = []
    start = None
    for i, line in enumerate([*lines, ""], 1):
        if line.strip() and not PAGE_FOOTER.fullmatch(line):
            if start is None:
                start = i
        elif start is not None:
            result.append((start, i - 1))
            start = None
    return result


def fragment(doc: dict[str, Any], page: dict[str, Any], start: int, end: int, role: str) -> dict:
    lines = page["text"].splitlines(keepends=True)
    if role not in ROLES or not 1 <= start <= end <= len(lines):
        raise ScreenError("Invalid evidence fragment")
    return Fragment(
        document=doc["id"],
        document_hash=doc["sha256"],
        page=page["page"],
        start=start,
        end=end,
        role=role,
        text="".join(lines[start - 1 : end]),
    ).model_dump()


def bundle(fragments: list[dict], relation: dict[str, Any]) -> dict | None:
    if not fragments or len(fragments) > 6:
        return None
    if fragments[0]["role"] != "main" or any(f["role"] == "main" for f in fragments[1:]):
        return None
    if any(
        not (15 if i == 0 else 1) <= len(compact(f["text"])) <= 1600
        for i, f in enumerate(fragments)
    ):
        return None
    text = compact("\n".join(f["text"] for f in fragments))
    matches = {g: [word for word in words if word in text] for g, words in GROUPS.items()}
    matches = {g: words for g, words in matches.items() if words}
    if not matches:
        return None
    content = {
        "version": VERSION,
        "fragments": fragments,
        "groups": sorted(matches),
        "matches": matches,
        "relation": relation,
    }
    return EvidenceBlock.model_validate({"id": digest(content), **content}).model_dump()


def numeric_row(line: str) -> bool:
    # A label plus one separated numeric column is already a table, never prose.
    columns = re.split(r"\s{2,}", line.strip())
    return len(columns) >= 2 and sum(bool(NUMBER.fullmatch(c)) for c in columns) >= 1


def line_chunks(lines: list[str], start: int, end: int, maximum: int = 1000):
    """Never split a source line. Adjacent context is kept in the same immutable fragment."""
    first, size = start, 0
    for i in range(start, end + 1):
        length = len(compact(lines[i - 1]))
        if size and size + length > maximum:
            yield first, i - 1
            first, size = i, 0
        size += length
    if first <= end:
        yield first, end


def document_blocks(doc: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Conservative plain-text relations. Ambiguous tables are omissions, not prose."""
    blocks, rejected = [], []
    previous_tables: list[dict] = []
    for page in doc["pages"]:
        lines = page["text"].splitlines(keepends=True)
        spans = paragraphs(lines)
        current_tables = []
        title = ""
        title_span = None
        for start, end in spans:
            text = "".join(lines[start - 1 : end])
            if len(compact(text)) < 120 and TABLE_TITLE.search(
                text.strip().replace("（续）", "").replace("(续)", "").replace("续表", "")
            ):
                title = compact(text).replace("（续）", "").replace("(续)", "").replace("续表", "")
                title_span = (start, end)
            rows = [i for i in range(start, end + 1) if numeric_row(lines[i - 1])]
            if not rows:
                if UNIT.search(text):
                    continue
                # Header/footer metadata alone must not become a factual source.
                if len(compact(text)) < 15 or ("报告" in text and len(lines[start - 1 : end]) == 1):
                    continue
                for a, b in line_chunks(lines, start, end):
                    value = bundle([fragment(doc, page, a, b, "main")], {"kind": "paragraph"})
                    if value:
                        blocks.append(value)
                continue
            first = rows[0]
            header_start = start
            # A blank before a unit/header is common; only the immediately preceding
            # nonnumeric paragraph may supply it, never an arbitrary earlier page header.
            pos = spans.index((start, end))
            if first == start and pos:
                a, b = spans[pos - 1]
                before = "".join(lines[a - 1 : b])
                if UNIT.search(before) and not any(numeric_row(x) for x in lines[a - 1 : b]):
                    header_start = a
            header = "".join(lines[header_start - 1 : first - 1])
            # Multiple unit declarations indicate competing tables: refuse this span.
            if (
                len(re.findall(r"单位\s*[:：]", header)) > 1
                or len(re.findall(r"币种\s*[:：]", header)) > 1
                or any(
                    UNIT.search(line) or TABLE_TITLE.search(line.strip())
                    for line in lines[first - 1 : end]
                )
            ):
                rejected.append({"page": page["page"], "start": start, "reason": "ambiguous_table"})
                continue
            proof = {"kind": "same_page_table", "table_title": title}
            context = []
            if header.strip():
                context.append(fragment(doc, page, header_start, first - 1, "header"))
            has_unit = bool(UNIT.search(header))
            if not has_unit:
                # Cross-page relation requires an explicit continuation marker and
                # matching repeated headers, not just similar numbers or proximity.
                marker_text = (
                    "".join(lines[title_span[0] - 1 : title_span[1]]) if title_span else ""
                )
                continued = any(marker in marker_text for marker in ("续表", "（续）", "(续)"))
                normalized = (
                    compact(header).replace("续表", "").replace("（续）", "").replace("(续)", "")
                )
                matching = [
                    t
                    for t in previous_tables
                    if title
                    and t["title"] == title
                    and t["header"] == normalized
                    and t["page"] + 1 == page["page"]
                ]
                if not continued or len(matching) != 1 or title_span is None:
                    rejected.append(
                        {"page": page["page"], "start": start, "reason": "table_context_missing"}
                    )
                    continue
                context.extend(matching[0]["context"])
                context.append(fragment(doc, page, *title_span, "relation"))
                proof = {
                    "kind": "explicit_continuation",
                    "table_title": title,
                    "previous_page": matching[0]["page"],
                    "repeated_header": normalized,
                }
            # Notes following the table belong only when explicitly labelled as notes.
            if pos + 1 < len(spans):
                a, b = spans[pos + 1]
                if re.match(r"\s*(?:注\s*[:：]|备注\s*[:：]|说明\s*[:：])", lines[a - 1]):
                    context.append(fragment(doc, page, a, b, "footnote"))
            current_tables.append(
                {
                    "title": title,
                    "page": page["page"],
                    "header": compact(re.sub(r"单位[^\n]*|币种[^\n]*", "", header)),
                    "context": context,
                }
            )
            for a, b in line_chunks(lines, first, end):
                value = bundle([fragment(doc, page, a, b, "main"), *context], proof)
                if value:
                    blocks.append(value)
                else:
                    rejected.append({"page": page["page"], "start": a, "reason": "fragment_limits"})
        previous_tables = current_tables
    # Duplicate source packages are not extra evidence.
    return list({b["id"]: b for b in blocks}.values()), rejected


def validate_block(value: dict, documents: list[dict]) -> None:
    block = EvidenceBlock.model_validate(value).model_dump()
    identity = {k: v for k, v in block.items() if k != "id"}
    if block["version"] != VERSION or digest(identity) != block["id"]:
        raise ScreenError("Evidence block identity mismatch")
    docs = {d["id"]: d for d in documents}
    for f in block["fragments"]:
        doc = docs.get(f["document"])
        page = next((p for p in doc["pages"] if p["page"] == f["page"]), None) if doc else None
        if not doc or page is None or fragment(doc, page, f["start"], f["end"], f["role"]) != f:
            raise ScreenError("Evidence fragment not in frozen document")
    if bundle(block["fragments"], block["relation"]) != block:
        raise ScreenError("Invalid evidence block")
    source_ids = {f["document"] for f in block["fragments"]}
    if len(source_ids) != 1 or block not in document_blocks(docs[next(iter(source_ids))])[0]:
        raise ScreenError("Unverified evidence relation")


def select_blocks(
    documents: list[dict], structured: dict, limit: int = 25000, *, cache_root: Path | None = None
) -> dict:
    """Group-balanced recall with one deterministic expansion; account for omissions."""
    cache_inputs = {
        "version": VERSION,
        "documents": [
            {
                "id": d["id"],
                "sha256": d["sha256"],
                "period": d["period"],
                "parser": d.get("parser_version"),
                "pages_hash": digest(d["pages"]),
            }
            for d in documents
        ],
        "structured": structured,
        "limit": limit,
        "quotas": [2, 6],
        "ranking": "specific-table-first-v1",
    }
    if cache_root is not None:
        with locked_root(cache_root, blocking=True):
            path = cache_root / f"evidence-{digest(cache_inputs)}.json"
            if path.is_symlink():
                raise ScreenError("Symlink evidence cache refused")
            cached = read_state(path) if path.exists() else None
        if cached is not None:
            if cached["cache_key"] != digest({**cache_inputs, "expanded": cached["expanded"]}):
                raise ScreenError("Evidence cache identity mismatch")
            for block in cached["blocks"]:
                validate_block(block, documents)
            return cached
        result = select_blocks(documents, structured, limit)
        with locked_root(cache_root, blocking=True):
            if path.is_symlink():
                raise ScreenError("Symlink evidence cache refused")
            save_state(path, result)
        return result
    all_blocks: list[dict] = []
    rejected: list[dict] = []
    for doc in documents:
        blocks, omissions = document_blocks(doc)
        all_blocks.extend(blocks)
        rejected.extend({"document": doc["id"], **o} for o in omissions)
    by_group = {g: [b for b in all_blocks if g in b["groups"]] for g in GROUPS}
    # Prefer current measured evidence over broad accounting-policy keyword matches.
    periods = {d["id"]: d["period"] for d in documents}
    for group, candidates in by_group.items():
        candidates.sort(
            key=lambda b: (
                periods[b["fragments"][0]["document"]],
                b["relation"]["kind"] != "paragraph",
                sum(word in compact(b["fragments"][0]["text"]) for word in GROUPS[group]),
                -b["fragments"][0]["page"],
                -b["fragments"][0]["start"],
                b["id"],
            ),
            reverse=True,
        )
    chosen: dict[str, dict] = {}
    expanded = False
    for quota in (2, 6):
        expanded = quota == 6
        for index in range(quota):
            for group in GROUPS:
                candidates = by_group[group]
                if index < len(candidates):
                    b = candidates[index]
                    proposal = {**chosen, b["id"]: b}
                    if context_size(list(proposal.values())) <= limit:
                        chosen = proposal
        if all(any(g in b["groups"] for b in chosen.values()) for g in REQUIRED_GROUPS):
            break
    coverage = {
        g: {
            "available": len(items),
            "selected": [b["id"] for b in chosen.values() if g in b["groups"]],
            "omitted": [b["id"] for b in items if b["id"] not in chosen],
        }
        for g, items in by_group.items()
    }
    cache_inputs["expanded"] = expanded
    return {
        "blocks": list(chosen.values()),
        "coverage": coverage,
        "rejected": rejected,
        "expanded": expanded,
        "cache_key": digest(cache_inputs),
        "context_estimate": {"method": "utf8_bytes", "value": context_size(list(chosen.values()))},
    }


def review_pages(blocks: list[dict], documents: list[dict]) -> list[dict]:
    wanted: dict[str, set[int]] = defaultdict(set)
    for block in blocks:
        for f in block["fragments"]:
            wanted[f["document"]].add(f["page"])
            doc = next(d for d in documents if d["id"] == f["document"])
            page = next(p for p in doc["pages"] if p["page"] == f["page"])
            lines = page["text"].splitlines()
            meaningful = [
                i
                for i, line in enumerate(lines, 1)
                if line.strip() and not PAGE_FOOTER.fullmatch(line)
            ]
            if meaningful and f["start"] <= meaningful[0]:
                wanted[f["document"]].add(f["page"] - 1)
            if meaningful and f["end"] >= meaningful[-1]:
                wanted[f["document"]].add(f["page"] + 1)
    return [
        {"document": d["id"], "page": p["page"], "text": p["text"]}
        for d in documents
        for p in d["pages"]
        if p["page"] in wanted[d["id"]]
    ]
