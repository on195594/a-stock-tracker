"""Official disclosure evidence and tool-free, source-checked machine research."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import requests

from a_stock_tracker import evidence_blocks as eb
from a_stock_tracker import evidence_pipeline as ep
from a_stock_tracker.atomic_io import _atomic_write_bytes
from a_stock_tracker.automation import (
    Budget,
    BudgetStop,
    digest,
    locked_root,
    read_result,
    read_state,
    save,
    save_state,
)
from a_stock_tracker.bounded_process import run_bounded
from a_stock_tracker.research import SHANGHAI, ScreenError, now_iso, parse_date

TEMPLATE = eb.REPORT_VERSION
MARKET_REF = "MKT"
UNFORMED = "事实不足，未形成"
MAX_BYTES = 25_000_000
REPORT_PERIODS = {
    "年度报告": "12-31",
    "半年度报告": "06-30",
    "第一季度报告": "03-31",
    "第三季度报告": "09-30",
}
WHITESPACE_RE = re.compile(r"\s+")
DATE_RE = re.compile(
    r"[0-9]{4}(?:-[0-9]{2}-[0-9]{2}|\.[0-9]{1,2}\.[0-9]{1,2}|年(?:[0-9]{1,2}月(?:[0-9]{1,2}日)?)?)"
    r"|[0-9]{1,2}月[0-9]{1,2}日"
)
FULL_DATE_RE = re.compile(
    r"([0-9]{4})(?:-([0-9]{1,2})-([0-9]{1,2})|\.([0-9]{1,2})\.([0-9]{1,2})|年([0-9]{1,2})月([0-9]{1,2})日)"
)
MONTH_DAY_RE = re.compile(r"([0-9]{1,2})月([0-9]{1,2})日")
FIGURE_UNIT_RE = re.compile(r"\s*(?:亿|万|元|倍|%|个百分点)")
REF_RE = re.compile(r"(?<![A-Za-z0-9])(?:F[0-9]+|MKT)(?![0-9])")
NUMBERS_RE = re.compile(r"[+\-\u2212]?[0-9]+(?:[,.][0-9]+)*%?")


def compact(text: str) -> str:
    return WHITESPACE_RE.sub("", text)


class OfficialSource:
    def __init__(self, budget: Budget):
        self.budget = budget
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/"}
        )
        self.stocks: list[dict[str, Any]] | None = None

    def request(self, method: str, url: str, **kwargs) -> bytes:
        # Fixed hosts and paths are constructed by the host, never by model output.
        if not re.fullmatch(
            r"https://(?:www\.cninfo\.com\.cn/new/(?:data/szse_stock\.json|hisAnnouncement/query)|static\.cninfo\.com\.cn/finalpage/[0-9-]{10}/[0-9]+\.[Pp][Dd][Ff])",
            url,
        ):
            raise ScreenError("Non-official disclosure URL")
        for attempt in range(2):
            self.budget.take()
            try:
                with self.session.request(
                    method, url, timeout=(10, 30), allow_redirects=False, stream=True, **kwargs
                ) as response:
                    if response.status_code != 200:
                        raise ScreenError("Official source HTTP failure")
                    data = bytearray()
                    for chunk in response.iter_content(65536):
                        self.budget.check()
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            raise ScreenError("Disclosure exceeds byte budget")
                    return bytes(data)
            except (requests.RequestException, ScreenError):
                if attempt:
                    raise ScreenError("Official source unavailable after two attempts") from None
                time.sleep(1)
        raise ScreenError("Unreachable source state")

    def announcements(self, code: str, cutoff: str) -> list[dict[str, Any]]:
        if self.stocks is None:
            self.stocks = json.loads(
                self.request("GET", "https://www.cninfo.com.cn/new/data/szse_stock.json")
            )["stockList"]
        found = [r for r in self.stocks if r["code"] == code[:6] and r.get("category") == "A股"]
        if len(found) != 1:
            raise ScreenError("Official company identity not unique")
        start = parse_date(cutoff) - timedelta(days=550)
        result: list[dict[str, Any]] = []
        expected = None
        for page in range(1, 9):
            payload = {
                "stock": f"{code[:6]},{found[0]['orgId']}",
                "tabName": "fulltext",
                "pageSize": "30",
                "pageNum": str(page),
                "column": "",
                "category": "category_ndbg_szsh;category_bndbg_szsh;category_sjdbg_szsh;category_yjdbg_szsh;",
                "plate": "",
                "seDate": f"{start.isoformat()}~{cutoff}",
                "searchkey": "",
                "secid": "",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "false",
            }
            data = json.loads(
                self.request(
                    "POST", "https://www.cninfo.com.cn/new/hisAnnouncement/query", data=payload
                )
            )
            total = data.get("totalAnnouncement")
            rows = data.get("announcements") or []
            if (
                type(total) is not int
                or total < 1
                or total > 240
                or (expected is not None and total != expected)
            ):
                raise ScreenError("Official announcement count missing, changing or truncated")
            expected = total
            if any(r.get("secCode") != code[:6] for r in rows):
                raise ScreenError("Announcement security identity mismatch")
            result.extend(rows)
            if len(result) >= total:
                break
            if not rows:
                raise ScreenError("Official announcement page missing")
        if len(result) != expected or len({r.get("announcementId") for r in result}) != len(result):
            raise ScreenError("Official announcement pagination/duplicate conflict")
        return result


def select_documents(
    announcements: list[dict[str, Any]], row: dict[str, Any], cutoff: str
) -> list[dict[str, Any]]:
    documents = []
    for raw in announcements:
        title = raw.get("announcementTitle", "")
        if any(word in title for word in ("摘要", "英文", "English", "取消", "更正公告")):
            continue
        match = re.search(
            r"([0-9]{4})\s*年\s*(年度报告|半年度报告|第一季度报告|第三季度报告)", title
        )
        if not match:
            continue
        period = match[1] + "-" + REPORT_PERIODS[match[2]]
        announced = (
            datetime.fromtimestamp(raw["announcementTime"] / 1000, SHANGHAI).date().isoformat()
        )
        if not period <= announced <= cutoff or raw.get("secCode") != row["code"][:6]:
            raise ScreenError("Announcement identity/date conflict")
        documents.append(
            {
                "id": str(raw["announcementId"]),
                "code": row["code"],
                "title": title,
                "period": period,
                "announced": announced,
                "url": "https://static.cninfo.com.cn/" + raw["adjunctUrl"],
                "official_name": raw.get("secName", ""),
            }
        )
    required = {row["annual_roes"][-1]["period"], row["research_report"].get("period")}
    if None in required:
        raise ScreenError("No verified latest financial period")
    chosen = []
    for period in sorted(required):
        matches = [d for d in documents if d["period"] == period]
        if not matches:
            raise ScreenError("Official report for required period unavailable")
        latest = max(d["announced"] for d in matches)
        versions = [d for d in matches if d["announced"] == latest]
        revised = [d for d in versions if any(w in d["title"] for w in ("修订", "更新", "更正后"))]
        if len(revised) == 1:
            chosen.append(revised[0])
        elif len(versions) == 1:
            chosen.append(versions[0])
        else:
            raise ScreenError("Ambiguous official report version")
    if documents and max(d["period"] for d in documents) > max(required):
        raise ScreenError("Financial API stale versus official disclosure")
    return chosen


PARSER_VERSION = "pdftotext-layout-v1"


def parsed_pages(path: Path, sha: str, cache_root: Path) -> list[str]:
    key = digest({"pdf": sha, "parser": PARSER_VERSION})
    with locked_root(cache_root, blocking=True):
        cached_path = cache_root / f"parsed-{key}.json"
        if cached_path.is_symlink():
            raise ScreenError("Symlink parsed cache refused")
        cached = read_state(cached_path) if cached_path.exists() else None
    if cached is not None:
        if cached.get("pdf") != sha or cached.get("parser") != PARSER_VERSION:
            raise ScreenError("Parsed cache identity mismatch")
        pages = cached.get("pages")
        if (
            not isinstance(pages, list)
            or not 1 <= len(pages) <= 1000
            or any(not isinstance(page, str) or len(compact(page)) < 15 for page in pages)
        ):
            raise ScreenError("Invalid parsed cache pages")
        return pages
    info = run_bounded(["pdfinfo", str(path)], timeout=40, check=True).stdout
    match = re.search(r"^Pages:\s+([0-9]+)$", info, re.M)
    if not match or not 1 <= int(match[1]) <= 1000:
        raise ScreenError("Invalid PDF page count")
    text = run_bounded(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
        timeout=60,
        check=True,
    ).stdout
    pages = text.split("\f")
    if pages[-1] == "":
        pages.pop()
    if len(pages) != int(match[1]) or any(len(compact(page)) < 15 for page in pages):
        raise ScreenError("Missing, blank or non-extractable PDF pages")
    with locked_root(cache_root, blocking=True):
        if cached_path.is_symlink():
            raise ScreenError("Symlink parsed cache refused")
        save_state(cached_path, {"pdf": sha, "parser": PARSER_VERSION, "pages": pages})
    return pages


def extract_document(
    source: OfficialSource,
    document: dict[str, Any],
    directory: Path,
    *,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    payload = source.request("GET", document["url"])
    if not payload.startswith(b"%PDF-"):
        raise ScreenError("Official response is not PDF")
    sha = hashlib.sha256(payload).hexdigest()
    path = directory / f"source-{sha}.pdf"
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ScreenError("Stored PDF hash conflict")
    else:
        _atomic_write_bytes(path, payload)
    pages = parsed_pages(path, sha, cache_root or directory / "parsed-cache")
    identity = compact("".join(pages[:6]))
    issuer = compact(document["official_name"])
    code_token = rf"(?<![0-9]){re.escape(document['code'][:6])}(?![0-9])"
    issuer_established = issuer in identity and any(
        re.search(code_token, page) for page in pages[:6]
    )
    title = next((t for t, end in REPORT_PERIODS.items() if document["period"][5:] == end), None)
    if not issuer_established and title is not None:
        opening_titles = [
            "\n".join(compact(line) for line in opening.splitlines() if compact(line))
            for opening in pages[:6]
        ]
        for page in pages[6:16]:
            code_label = r"(?m)(?:^|[ \t]{2,})(?:股票代码|证券代码)"
            name_label = r"(?m)(?:^|[ \t]{2,})(?:股票简称|证券简称)"
            # A+H issuers annotate the A-share code, e.g. "000333（A 股）、0300（H 股）".
            code_end = r"(?=[ \t]{2,}|[ \t]*$|[ \t]*[（(]A[ \t]*股[）)])"
            codes = re.findall(code_label + r"[ \t:：]{0,64}([0-9]{6})" + code_end, page)
            names = re.findall(
                name_label + r"[ \t:：]{0,64}(\S(?:[^\r\n]{0,62}?\S)?)(?=[ \t]{2,}|[ \t]*$)",
                page,
            )
            full_label = r"(?m)^公司(?:的)?中文名称"
            full_names = re.findall(full_label + r"[ \t:：]{0,64}(\S[^\r\n]{2,78}\S)[ \t]*$", page)
            if (
                len(re.findall(code_label, page))
                == len(re.findall(name_label, page))
                == len(re.findall(full_label, page))
                == 1
                and codes == [document["code"][:6]]
                and [compact(name) for name in names] == [issuer]
                and len(full_names) == 1
            ):
                full_name = compact(full_names[0])
                if (
                    len(full_name) >= 4
                    and full_name.endswith(("有限公司", "股份公司"))
                    and any(
                        re.search(
                            rf"(?m)^{re.escape(full_name)}\n?{document['period'][:4]}年?{title}"
                            rf"(?:全文)?(?=$|[0-9，。:：;；（(])",
                            opening,
                        )
                        for opening in opening_titles
                    )
                ):
                    issuer_established = True
                    break
    if (
        not issuer_established
        or not issuer
        or title is None
        or not re.search(rf"{document['period'][:4]}年?{title}", identity)
    ):
        raise ScreenError("PDF security/report-period identity not established")
    extracted = {
        **document,
        "sha256": sha,
        "retrieved_at": now_iso(),
        "parser_version": PARSER_VERSION,
        "page_count": len(pages),
        "pages": [{"page": i + 1, "text": p} for i, p in enumerate(pages)],
    }
    save(directory / f"source-{sha}.json", extracted, immutable=False)
    return extracted


def evidence_packet(
    documents: list[dict[str, Any]],
    row: dict[str, Any],
    pipeline: str = ep.VERSION,
    *,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    if pipeline != ep.VERSION:
        raise ScreenError("Unknown research pipeline")
    financial = {
        k: row[k]
        for k in (
            "code",
            "name",
            "industry",
            "pb",
            "valuation_date",
            "annual_roes",
            "roe_mean",
            "research_report",
        )
    }
    total_mv = row.get("total_mv")
    financial["market_snapshot"] = {
        "date": row.get("valuation_date"),
        "close": row.get("close"),
        "pe_ttm": row.get("pe_ttm"),
        "pb": row.get("pb"),
        "total_mv_yi": round(total_mv / 10000, 2) if isinstance(total_mv, (int, float)) else None,
        "dv_ttm": row.get("dv_ttm"),
    }
    frozen_documents = [{k: v for k, v in doc.items() if k != "retrieved_at"} for doc in documents]
    selection = eb.select_blocks(frozen_documents, financial, cache_root=cache_root)
    selected_pages = {
        (f["document"], f["page"]) for block in selection["blocks"] for f in block["fragments"]
    }
    return {
        "template": eb.REPORT_VERSION,
        "citation_format": eb.VERSION,
        "pipeline": ep.VERSION,
        "documents": frozen_documents,
        "structured": financial,
        "scope_gaps": [
            "仅定期报告；临时公告、诉讼与重大新闻未全面覆盖",
            "抽取成功不证明模型理解所有附注；范围限于列出的页码",
            "PB分母符号/资产质量未由行情接口独立证明",
        ],
        **selection,
        "gaps": [g for g, coverage in selection["coverage"].items() if not coverage["selected"]],
        "omitted_pages": [
            {"document": d["id"], "page": page["page"]}
            for d in frozen_documents
            for page in d["pages"]
            if (d["id"], page["page"]) not in selected_pages
        ],
    }


def stable_evidence(packet: dict[str, Any], model: dict[str, str]) -> str:
    def clean(value):
        if isinstance(value, dict):
            return {
                k: clean(v)
                for k, v in value.items()
                if k not in {"acquired_at", "retrieved_at", "checked_at"}
            }
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    return digest(
        {
            "packet": clean(packet),
            "model": model,
            "pipeline": ep.VERSION,
            "validation": ep.CACHE_VERSION,
            "prompts": ep.PROMPTS,
            "context_limits": ep.CONTEXT_LIMITS,
            "report_limit": ep.REPORT_LIMIT,
            "analysis_limit": ep.ANALYSIS_LIMIT,
        }
    )


CLAIM_UNITS = ("万元", "亿元", "元/股", "万美元", "亿美元")


DECLINE_WORDS = ("下降", "减少", "降低", "下滑")


def unquoted_numbers(fact: dict[str, Any]) -> list[str]:
    # A number cannot be invented or converted into a different unit by the model.
    quoted_numbers = set(NUMBERS_RE.findall(fact["quote"]))
    claim = fact["claim"]
    # A "(%)" table header in the selected evidence lets "30.85" be restated as "30.85%";
    # sign and digits stay exact.
    percent_column = any(h in fact["quote"] for h in ("(%)", "（%）"))

    def restated(m: re.Match[str]) -> bool:
        bare = m.group()[:-1]
        if not (percent_column and m.group().endswith("%")):
            return False
        # "下降2.35%" restates a signed "-2.35"; the direction word carries the sign.
        declined = any(w in claim[max(0, m.start() - 4) : m.start()] for w in DECLINE_WORDS)
        return bare in quoted_numbers or (declined and f"-{bare}" in quoted_numbers)

    return [
        m.group()
        for m in NUMBERS_RE.finditer(claim)
        if m.group() not in quoted_numbers and not restated(m)
    ]


def unquoted_units(fact: dict[str, Any]) -> list[str]:
    return [unit for unit in CLAIM_UNITS if unit in fact["claim"] and unit not in fact["quote"]]


def source_numbers(value: Any) -> set[tuple[float, bool]]:
    """Absolute values found in a source, each flagged as a percentage or not."""
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        return {(abs(float(value)), False)}
    if isinstance(value, str):
        found = ((number_value(t), t.endswith("%")) for t in NUMBERS_RE.findall(value))
        return {(v, pct) for v, pct in found if v is not None}
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    return set().union(*(source_numbers(v) for v in items))


def number_value(token: str) -> float | None:
    try:
        return abs(float(token.replace(",", "").replace("−", "-").rstrip("%")))
    except ValueError:
        return None  # dotted section numbers such as 1.2.3


def number_matches(token: str, sources: set[tuple[float, bool]]) -> bool:
    """Equal to a source number, allowing yuan → 万/亿 conversion and rounding to shown digits.

    A percentage may restate a bare table value (a "(%)" column) but never an amount
    scaled into 万/亿; a plain amount never comes from a percentage."""
    digits = token.rstrip("%").replace(",", "")
    places = len(digits.split(".")[1]) if "." in digits else 0
    value, tolerance = number_value(token), 0.5 * 10**-places + 1e-9
    assert value is not None
    if token.endswith("%"):
        return any(abs(s - value) <= tolerance for s, _ in sources)
    return any(
        abs(s / scale - value) <= tolerance
        for s, pct in sources
        if not pct
        for scale in (1, 1e4, 1e8)
    )


def date_parts(m: re.Match[str]) -> tuple[int, int, int]:
    y, mo, d = (int(g) for g in m.groups() if g)
    return y, mo, d


def source_dates(text: str) -> set[tuple[int, ...]]:
    """Full dates (year, month, day) and month-days found in compacted source text."""
    found: set[tuple[int, ...]] = set()
    for m in FULL_DATE_RE.finditer(compact(text)):
        y, mo, d = date_parts(m)
        found |= {(y, mo, d), (mo, d)}
    return found


def analysis_number_errors(report: dict[str, Any], packet: dict[str, Any]) -> dict[str, list[str]]:
    """Numbers and dates must come from selected quotes, or the snapshot when MKT is cited."""
    facts = {f["id"]: f for f in report["facts"]}
    structured_data = packet["structured"].get("market_snapshot", {})
    structured = source_numbers(structured_data)
    errors = {}
    for section, item in report["analysis"].items():
        cited = [facts[fid] for fid in item["facts"] if fid in facts]
        texts = [f["quote"] for f in cited]
        sources = source_numbers(texts) | (structured if MARKET_REF in item["facts"] else set())
        dates = source_dates(" ".join(texts)) | (
            source_dates(str(structured_data)) if MARKET_REF in item["facts"] else set()
        )
        text = REF_RE.sub(" ", item["text"])
        bad = []
        date_evidence = compact(" ".join(texts)) + (
            compact(str(structured_data)) if MARKET_REF in item["facts"] else ""
        )
        for month in re.finditer("(?<![0-9])(?:1[0-2]|[1-9])月(?:末|初)?", DATE_RE.sub(" ", text)):
            if month.group() not in date_evidence:
                bad.append(month.group())
        for m in DATE_RE.finditer(text):
            full = FULL_DATE_RE.fullmatch(compact(m.group()))
            month_day = MONTH_DAY_RE.fullmatch(m.group())
            if full:
                ok = date_parts(full) in dates
            elif month_day:
                ok = tuple((int(g) for g in month_day.groups())) in dates
            else:
                ok = compact(m.group()) in compact(" ".join(texts)) or (
                    MARKET_REF in item["facts"]
                    and compact(m.group()) in compact(str(structured_data))
                )
            if not ok:
                bad.append(m.group())
        for m in NUMBERS_RE.finditer(DATE_RE.sub(" ", text)):
            token = m.group()
            unit = FIGURE_UNIT_RE.match(DATE_RE.sub(" ", text), m.end())
            if (
                not token.endswith("%")
                and (not unit)
                and re.fullmatch("[+\\-−]?(?:[0-9]|1[0-2])", token)
            ):
                continue
            if number_value(token) is not None and (not number_matches(token, sources)):
                bad.append(token)
        if bad:
            errors[section] = bad
    return errors


def validate_report(report: dict[str, Any], packet: dict[str, Any]) -> None:
    if packet.get("citation_format") != eb.VERSION:
        raise ScreenError("Retired report format")
    ep.validate_report(report, packet)


def validate_review(review: dict[str, Any], report: dict[str, Any]) -> bool:
    return report.get("version") == ep.VERSION and ep.validate_review(review, report)


def model_command() -> tuple[list[str], Path]:
    """Use only an explicitly configured host runtime, never the Web interpreter."""
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if os.getenv("AUTOMATION_PI_RUNTIME"):
        runtime = Path(os.environ["AUTOMATION_PI_RUNTIME"])
        agent = Path(os.getenv("AUTOMATION_PI_AGENT_DIR", ""))
        extension = Path(os.getenv("AUTOMATION_PI_EXTENSION", ""))
        node = shutil.which("node")
        if (
            not runtime.is_absolute()
            or not (runtime / "dist/core/model-runtime.js").is_file()
            or not agent.is_absolute()
            or not (agent / "auth.json").is_file()
            or not extension.is_absolute()
            or not extension.is_file()
            or not node
        ):
            raise ScreenError("Explicit installed pi runtime, credentials and provider required")
        return [node, str(scripts / "automation_model_pi.mjs")], runtime
    runtime = Path(os.getenv("AUTOMATION_HERMES_RUNTIME", ""))
    if not runtime.is_absolute() or not (runtime / ".venv/bin/python").is_file():
        raise ScreenError("Explicit installed Hermes runtime required; no fallback")
    return [
        str(runtime / ".venv/bin/python"),
        "-c",
        "import runpy,sys; runpy.run_path(sys.argv[1], run_name='__main__')",
        str(scripts / "automation_model.py"),
    ], runtime


def stage_cache_path(root: Path, request_hash: str) -> Path:
    path = root / f"stage-{digest({'request': request_hash, 'contract': ep.CACHE_VERSION})}.json"
    if path.is_symlink():
        raise ScreenError("Symlink stage cache refused")
    return path


def cache_validated_response(directory: Path, label: str, root: Path) -> None:
    """Only called after the current stage's complete mechanical validation."""
    saved = read_state(directory / f"{label}.response.json")
    request = read_result(directory / f"{label}.request.json")
    if saved["request_hash"] != digest(request):
        raise ScreenError("Validated stage request mismatch")
    with locked_root(root, blocking=True):
        path = stage_cache_path(root, saved["request_hash"])
        if path.exists():
            previous = read_state(path)
            if (
                previous["request_hash"] != saved["request_hash"]
                or previous["contract"] != ep.CACHE_VERSION
            ):
                raise ScreenError("Stage cache identity mismatch")
            return
        save_state(
            path,
            {
                "contract": ep.CACHE_VERSION,
                "request_hash": saved["request_hash"],
                "response": saved["response"],
            },
        )


def model_call(
    directory: Path,
    label: str,
    prompt: str,
    model: dict[str, str],
    state: dict[str, Any],
    budget: Budget,
    *,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    output = directory / f"{label}.response.json"
    request = {**model, "prompt": prompt, "timeout": 240}
    if "-v4-" in label:
        request["validation_contract"] = ep.CACHE_VERSION
    if os.getenv("AUTOMATION_PI_RUNTIME"):
        request["runtime"] = "pi"
    request_path = directory / f"{label}.request.json"
    usage_path = directory / f"{label}.usage.json"
    if output.exists():
        saved = read_state(output)
        if saved["request_hash"] != digest(request):
            raise ScreenError("Frozen model request mismatch")
        return saved["response"]
    if usage_path.exists() or request_path.exists():
        raise ScreenError(
            "Previous model attempt has no verified response; refusing another charge"
        )
    if cache_root is not None:
        with locked_root(cache_root, blocking=True):
            cached_path = stage_cache_path(cache_root, digest(request))
            cached = read_state(cached_path) if cached_path.exists() else None
        if cached is not None:
            if cached["contract"] != ep.CACHE_VERSION or cached["request_hash"] != digest(request):
                raise ScreenError("Stage cache identity mismatch")
            save(request_path, request, immutable=True)
            save_state(
                usage_path,
                {
                    "request_hash": digest(request),
                    "model": model,
                    "status": "reused_validated_stage",
                    "usage": None,
                },
            )
            save_state(
                output,
                {
                    "request_hash": digest(request),
                    "response": cached["response"],
                    "usage": None,
                    "model": model,
                    "tool_count": 0,
                    "cache": "validated_stage",
                },
            )
            return cached["response"]
    budget.check(260)
    if state["model_calls"] >= 10:
        raise BudgetStop("MODEL_CALL_BUDGET")
    command, runtime = model_command()
    state["model_calls"] += 1  # Count even a killed or failed subprocess before starting it.
    save_state(directory / "state.json", state)
    save(request_path, request, immutable=True)
    audit = {"request_hash": digest(request), "model": model, "status": "started", "usage": None}
    save_state(usage_path, audit)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            ("TUSHARE", "TELEGRAM", "DISCORD", "SLACK", "HERMES_KANBAN", "RESEARCH_SERVICE_")
        )
        and k not in {"STATE_DIR", "TRACKER_DATA_DIR", "PYTHONPATH"}
    }
    process = run_bounded(
        [*command, str(request_path)],
        cwd=runtime,
        env=env,
        timeout=260,
    )
    envelope = json.loads(process.stdout)
    audit.update(status="returned", usage=envelope.get("usage"), returncode=process.returncode)
    save_state(usage_path, audit)
    if process.returncode:
        raise ScreenError("Tool-free model invocation failed")
    if (
        envelope.get("tool_count") != 0
        or envelope.get("model") != model["model"]
        or envelope.get("provider") != model["provider"]
    ):
        raise ScreenError("Model isolation/identity mismatch")
    text = envelope["text"].strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[-1][:-3].strip()  # any fence language tag
    try:
        report = json.loads(text)
    except ValueError:
        # The call is paid either way; keep its output so the failure can be diagnosed.
        audit["unparsed_text"] = envelope["text"]
        save_state(usage_path, audit)
        raise ScreenError("Model result not JSON") from None
    if not isinstance(report, dict):
        raise ScreenError("Model result not an object")
    save_state(
        output,
        {
            "request_hash": digest(request),
            "response": report,
            "usage": envelope["usage"],
            "model": model,
            "tool_count": 0,
        },
    )
    return report


def report_status_text(status: Any) -> str:
    return {
        "incomplete": "未完成：原文、引用或模型核查有缺口，补齐后再研究",
        "scoped_complete": "范围内已完成，仍须核查反证与缺口",
        "unchanged": "证据未变化，复用已复核报告",
        "passed": "复核通过",
        "failed": "复核未通过，不能作为确定结论",
        "synthetic": "合成报告",
        "not_real": "合成示例，未进行真实复核",
        "gap": "资料缺口，补齐后再研究",
        "excluded": "未通过资格筛选",
        "partial": "部分完成，核对尚缺资料",
    }.get(status, "未记录" if not status else "状态未识别，需核查原始依据")


# Exact host-owned messages only: never publish exception text or provider output.
FAILURE_REASONS = {
    "Required section has no verified facts": (
        "section_unsupported",
        "结论、业务、财务或主要风险缺少通过校验的原文事实或数字出处；未发布草稿。",
    ),
    "Analysis number not in cited facts": (
        "numeric_mismatch",
        "正文数字未能在所引原文中找到出处；未发布草稿。",
    ),
    "Insufficient verified facts": (
        "insufficient_facts",
        "通过校验的原文事实不足 3 条；未发布草稿。",
    ),
    "PDF security/report-period identity not established": (
        "document_identity",
        "原文证券或报告期身份未能确认；核查所选官方报告。",
    ),
    "Missing, blank or non-extractable PDF pages": (
        "pdf_text",
        "原文存在空白或无法提取的页面；核查 PDF 文本层。",
    ),
    "Invalid PDF page count": ("pdf_pages", "无法确认原文页数；核查 PDF 文件与提取工具。"),
    "Official response is not PDF": ("source_format", "官方来源未返回 PDF；核查来源可用性。"),
    "Official source unavailable after two attempts": (
        "source_unavailable",
        "官方原文来源不可用；核查网络与来源服务。",
    ),
    "Official report for required period unavailable": (
        "report_missing",
        "未找到所需报告期的官方报告；核查公告日期与报告范围。",
    ),
    "Financial API stale versus official disclosure": (
        "period_conflict",
        "财务接口与官方公告的最新报告期不一致；核查资料日期。",
    ),
    "Ambiguous official report version": (
        "report_version",
        "官方报告存在无法判定的版本冲突；核查更正公告。",
    ),
    "Unbound/nonverbatim citation": ("citation_mismatch", "生成引用未能逐字匹配原文；未发布草稿。"),
    "Numeric claim not literal in source quote": (
        "numeric_mismatch",
        "生成数字未能匹配引用原文；未发布草稿。",
    ),
    "Numeric unit mismatch": ("numeric_mismatch", "生成数字单位与原文不一致；未发布草稿。"),
    "Model report structure invalid after bounded repair": (
        "structure_invalid",
        "报告结构未通过校验，且一次修复失败或预算不足；未发布草稿。",
    ),
    "Model result not JSON": (
        "model_output_invalid",
        "模型输出不是可解析的 JSON，原始输出已留存在批次中；失败调用仍可能计费。",
    ),
    "Tool-free model invocation failed": (
        "model_failed",
        "模型调用未成功；核查运行时与用量记录，失败仍可能计费。",
    ),
    "Model isolation/identity mismatch": (
        "model_identity",
        "模型身份或无工具隔离检查未通过；核查固定运行配置。",
    ),
    "Previous model attempt has no verified response; refusing another charge": (
        "model_response_unknown",
        "此前模型调用没有已核验响应；为避免重复收费未再次调用，须核查用量。",
    ),
}
FAILURE_MESSAGES = dict(FAILURE_REASONS.values()) | {
    "numeric_mismatch": "生成数字或单位未能匹配引用原文；未发布草稿。",
    "validation_failed": "本阶段资料或结构核验未通过；请按阶段核查已保存依据，未发布确定结论。",
    "timeout": "本阶段处理超时；核查运行时与用量记录，模型失败仍可能计费。",
    "process_failed": "本阶段处理程序失败；核查提取工具或模型运行时。",
    "source_unavailable": "原文来源请求失败；核查网络与来源服务。",
    "context_budget_exceeded": "必要证据或复核上下文超过本次预算，已停止后续模型调用。",
    "evidence_insufficient": "业务、财务或风险证据不足，未生成正文。",
    "repair_exhausted": "该类修复机会已用完，后续核验仍未通过，未发布草稿。",
    "semantic_failed": "独立语义复核未通过，未发布草稿。",
    "schema_invalid": "模型输出不符合约定结构，未发布草稿。",
    "identity_mismatch": "模型返回的公司与本次请求不一致，未发布草稿。",
    "review_binding": "复核结果未绑定本次报告，未发布草稿。",
    "review_incomplete": "复核未逐条覆盖全部事实与句子，未发布草稿。",
    "patch_scope": "局部修复返回了获准范围以外的对象或格式，未发布草稿。",
    "dependency_not_repaired": "局部修复未正确处理受影响的句子，未发布草稿。",
    "repair_incomplete": "局部修复后仍有核验错误，未发布草稿。",
}

FAILURE_STAGES = {
    "evidence_load",
    "announcements",
    "document_selection",
    "document_extraction",
    "evidence_selection",
    "report_generation",
    "citation_validation",
    "independent_review",
    "review_validation",
    "cached_review",
    "select",
    "compose",
    "review",
    "repair",
    "recheck",
    "fact_selection",
    "composition",
    "report_validation",
}


def failure_diagnostic(stage: str, exc: Exception) -> dict[str, str]:
    category = FAILURE_REASONS.get(str(exc), ("validation_failed", ""))[0]
    if isinstance(exc, ep.PipelineError):
        stage = exc.stage if exc.stage in FAILURE_STAGES else "report_validation"
        category = exc.category if exc.category in FAILURE_MESSAGES else "validation_failed"
    elif isinstance(exc, subprocess.TimeoutExpired):
        category = "timeout"
    elif isinstance(exc, subprocess.SubprocessError):
        category = "process_failed"
    elif isinstance(exc, requests.RequestException):
        category = "source_unavailable"
    return {"stage": stage, "category": category, "message": FAILURE_MESSAGES[category]}


def public_diagnostic(value: Any) -> dict[str, str] | None:
    # Reconstruct the message from trusted codes even for existing artifacts.
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("stage"), str)
        or value["stage"] not in FAILURE_STAGES
        or not isinstance(value.get("category"), str)
        or value["category"] not in FAILURE_MESSAGES
    ):
        return None
    return {
        "stage": value["stage"],
        "category": value["category"],
        "message": FAILURE_MESSAGES[value["category"]],
    }


def structured_report(result: dict[str, Any]) -> dict[str, Any]:
    """Project a verified report envelope; never expose rejected drafts or raw pages."""
    packet = result.get("evidence", {})
    report = result.get("report", {}) if result.get("evidence_review") == "passed" else {}
    docs = {d["id"]: d for d in packet.get("documents", [])}
    if report:
        validate_report(report, packet)
        if not validate_review(result.get("review", {}), report):
            raise ScreenError("Published report review binding invalid")
    analysis = ep.projected_analysis(report) if report else {}
    facts = ep.projected_facts(report, packet) if report else []
    gaps = [ep.GAP_TEXT[g] for g in report.get("gaps", [])]
    name = (
        result.get("name")
        or packet.get("structured", {}).get("name")
        or next(
            (d.get("official_name") for d in packet.get("documents", []) if d.get("official_name")),
            None,
        )
    )
    return {
        "code": result["code"],
        "name": name,
        "status": result["status"],
        "evidence_review": result.get("evidence_review"),
        "rule": packet.get("template"),
        "quality": result.get("quality"),
        "documents": [{k: d[k] for k in ("period", "announced")} for d in docs.values()],
        "clues": {
            k: packet.get("structured", {}).get(k)
            for k in ("industry", "valuation_date", "pb", "roe_mean")
        },
        "snapshot": packet.get("structured", {}).get("market_snapshot"),
        "analysis": analysis,
        "facts": facts,
        # Fixed system disclaimers stay apart from the report's own, company-specific gaps.
        "gaps": gaps,
        "scope": packet.get("scope_gaps", []),
        "omitted_pages": len(packet["omitted_pages"])
        if isinstance(packet.get("omitted_pages"), list)
        else None,
        "diagnostic": public_diagnostic(result.get("diagnostic")),
        "error": result.get("error"),
    }


def report_summary_status(report: dict[str, Any]) -> str:
    if report.get("evidence_review") == "failed":
        return "未能生成结论：独立复核未通过"
    if report.get("evidence_review") == "passed":
        return "研究完成"
    if report.get("error"):
        return "未能生成结论"
    return report_status_text(report["status"])


def scope_text(report: dict[str, Any]) -> str:
    """Coverage detail for the evidence fold, not for the headline status."""
    if report.get("omitted_pages") is None:
        return "未纳入分析的原文页数未知"
    if not report["omitted_pages"]:
        return "所选定期报告全文均已送入分析"
    return f"所选定期报告中有 {report['omitted_pages']} 页未送入分析，结论只覆盖已读页面"


def quality_text(quality: Any) -> str | None:
    if not isinstance(quality, dict):
        return None
    repair = quality.get("repair")
    repair = repair if isinstance(repair, str) else "none"
    dropped = quality.get("dropped")
    dropped = dropped if isinstance(dropped, (list, tuple)) else []
    unformed = quality.get("unformed")
    unformed = unformed if isinstance(unformed, dict) else {}
    count = quality.get("repairs", 1)
    parts = {"repaired": f"修复 {count} 次", "failed": f"修复 {count} 次未成功"}
    text = [
        f"机械校验通过 {quality.get('mechanical_passed', 0)} 条 / 语义复核通过 {quality.get('semantic_passed', 0)} 条"
    ]
    if repair in parts:
        text.append(parts[repair])
    if dropped:
        text.append(f"剔除 {len(dropped)} 条未通过校验的事实")
    carried = quality.get("carried_sections")
    if isinstance(carried, list) and carried:
        names = {
            "summary": "摘要",
            "business": "业务",
            "financial": "财务",
            "counterevidence": "主要风险",
            "valuation": "估值",
            "change_conditions": "改变判断的条件",
        }
        waived = "、".join(names.get(str(c), "其他段落") for c in carried)
        text.append(f"修复后复核对未改动的{waived}提出新意见，沿用首轮复核结论")
    sections = [("valuation", "估值方法"), ("change_conditions", "改变判断的条件")]
    titles = [title for key, title in sections if unformed.get(key)]
    if titles:
        text.append("未形成：" + "、".join(titles))
    return "质量：" + " / ".join(text)


def render(result: dict[str, Any]) -> str:
    projected = structured_report(result)
    lines = [
        f"# {result['code']} 自动研究",
        "",
        f"状态：{report_summary_status(projected)}；规则：{result.get('evidence', {}).get('template', TEMPLATE)}；非交易建议、非个人研究状态。",
        f"报告版本：{result.get('evidence_key', 'none')}",
        "",
    ]
    quality = quality_text(result.get("quality"))
    if quality is not None:
        lines.append(quality)
    if "report" not in result:
        return "\n".join(lines + ["缺口：" + result.get("error", "原文/模型验证未完成")])
    packet, report = result["evidence"], projected
    row = packet["structured"]
    if summary := report["analysis"].get("summary"):
        lines += ["## 一句话结论", summary["text"], ""]
    if market := row.get("market_snapshot"):
        lines += [
            f"行情快照（{market.get('date')}）：收盘 {market.get('close')} 元；PE(TTM) {market.get('pe_ttm')}；PB {market.get('pb')}；总市值 {market.get('total_mv_yi')} 亿元；股息率 {market.get('dv_ttm')}%",
            "",
        ]
    lines += [
        f"入围线索：行业 {row['industry']}；估值日 {row['valuation_date']} PB={row['pb']}；三年ROE均值={row['roe_mean']}%。仅行业内研究顺序，不是全市场投资总榜。",
        "",
        "## 原文事实（仅放行已复核的引用）",
    ]
    for fact in report["facts"]:
        lines += [
            f"- [{fact['id']}] {fact['claim']} — [{fact['title']}]({fact['url']}) PDF第{fact['page']}页，报告期{fact['period']}，公告{fact['announced']}",
        ]
        for fragment in fact["fragments"]:
            lines += [
                f"  - {fragment['role']} · PDF第{fragment['page']}页 · 原文行{fragment['start']}–{fragment['end']}",
                f"    > {fragment['text']}",
            ]
    for field, title in (
        ("business", "业务判断"),
        ("financial", "财务判断"),
        ("valuation", "估值方法与假设"),
        ("counterevidence", "最强反证"),
        ("change_conditions", "改变判断的证据条件"),
    ):
        item = report["analysis"][field]
        lines += [
            "",
            f"## {title}（推论/假设）",
            item["text"] + (" [" + ", ".join(item["facts"]) + "]" if item["facts"] else ""),
        ]
    lines += [
        "",
        "## 缺口与范围",
        *["- " + g for g in packet["scope_gaps"] + report["gaps"]],
        f"- 未送入模型的全文页数：{len(packet['omitted_pages'])}；详见证据JSON。",
        "- 数字为原文逐字值/单位校验；语义由独立模型步骤复核，不是审计保证，也不是a-stock-qa/decision-v1通过。",
    ]
    return "\n".join(lines) + "\n"


def research_batch(
    directory: Path,
    frozen: dict[str, Any],
    summary: dict[str, Any],
    state: dict[str, Any],
    previous: list,
    budget: Budget,
    maximum: int,
    *,
    cache_root: Path | None = None,
) -> None:
    if maximum and state.get("pipeline") != ep.VERSION:
        raise ScreenError("Retired research pipeline cannot be resumed")
    source = OfficialSource(budget)
    rows = {r["code"]: r for r in summary["rows"]}
    known = {}
    for prior_dir, prior_state in previous:
        if prior_dir == directory:
            continue
        for code, ref in prior_state.get("research", {}).items():
            read_result(prior_dir / f"report-{code}.json", ref)
            prior = read_state(prior_dir / f"report-{code}.json")["result"]
            if prior.get("evidence_key") and prior.get("evidence_review") == "passed":
                known[code] = prior
    for code, sha in state["research"].items():
        read_result(directory / f"report-{code}.json", sha)
    while state["cursor"] < len(state["queue"]) and len(state["research"]) < maximum:
        code = state["queue"][state["cursor"]]
        row = rows[code]
        output = directory / f"report-{code}.json"
        packet = None
        quality: dict[str, Any] | None = None
        selected_documents: list[dict[str, Any]] = []
        stage = "evidence_load"
        try:
            budget.check(60)
            if output.exists():
                result = read_state(output)["result"]
                if result["code"] != code or result["freeze"] != state["freeze"]:
                    raise ScreenError("Recovered report identity mismatch")
            else:
                packet_path = directory / f"evidence-{code}.json"
                if packet_path.exists():
                    saved_packet = read_state(packet_path)
                    if saved_packet["freeze"] != state["freeze"]:
                        raise ScreenError("Evidence/frozen run mismatch")
                    packet = saved_packet["packet"]
                else:
                    stage = "announcements"
                    announcements_path = directory / f"announcements-{code}.json"
                    if announcements_path.exists():
                        saved_announcements = read_state(announcements_path)
                        if (
                            saved_announcements["freeze"] != state["freeze"]
                            or saved_announcements["code"] != code
                            or saved_announcements["cutoff"] != frozen["cutoff"]
                        ):
                            raise ScreenError("Announcements/frozen run mismatch")
                        anns = saved_announcements["rows"]
                    else:
                        anns = source.announcements(code, frozen["cutoff"])
                        save_state(
                            announcements_path,
                            {
                                "freeze": state["freeze"],
                                "code": code,
                                "checked_at": now_iso(),
                                "cutoff": frozen["cutoff"],
                                "rows": anns,
                            },
                        )
                    stage = "document_selection"
                    selected_documents = select_documents(anns, row, frozen["cutoff"])
                    stage = "document_extraction"
                    documents = [
                        extract_document(
                            source,
                            doc,
                            directory,
                            **(
                                {"cache_root": cache_root}
                                if cache_root is not None and state.get("pipeline") == ep.VERSION
                                else {}
                            ),
                        )
                        for doc in selected_documents
                    ]
                    stage = "evidence_selection"
                    packet = evidence_packet(
                        documents,
                        row,
                        state.get("pipeline", ep.VERSION),
                        **({"cache_root": cache_root} if cache_root is not None else {}),
                    )
                    save_state(packet_path, {"packet": packet, "freeze": state["freeze"]})
                if (
                    packet.get("pipeline") != ep.VERSION
                    or packet.get("citation_format") != eb.VERSION
                ):
                    raise ScreenError("Retired research evidence cannot be resumed")
                key = stable_evidence(packet, state["model"])
                old = known.get(code)
                if old and old["evidence_key"] == key:
                    stage = "cached_review"
                    validate_report(old["report"], packet)
                    if not validate_review(old["review"], old["report"]):
                        raise ScreenError("Cached citation review invalid")
                    result = {
                        **old,
                        "code": code,
                        "freeze": state["freeze"],
                        "status": "unchanged",
                        "change": "unchanged",
                        "evidence_key": key,
                        "evidence": packet,
                        "previous_report": old["evidence_key"],
                    }
                else:
                    quality = {}
                    call = partial(model_call, cache_root=cache_root) if cache_root else model_call
                    on_validated = (
                        (lambda label: cache_validated_response(directory, label, cache_root))
                        if cache_root is not None
                        else None
                    )
                    report, review = ep.run(
                        packet, directory, state, budget, call, quality, on_validated
                    )
                    result = {
                        "code": code,
                        "name": row.get("name", ""),
                        "freeze": state["freeze"],
                        "status": "scoped_complete",
                        "evidence_review": "passed",
                        "evidence_key": key,
                        "evidence": packet,
                        "report": report,
                        "review": review,
                        "quality": quality,
                        "change": "new" if not old else "evidence_changed",
                        "model": state["model"],
                    }
                save_state(output, {"result": result})
        except BudgetStop:
            break  # Do not commit candidate or move cursor; generation is reusable after restart.
        except (
            ScreenError,
            ValueError,
            KeyError,
            TypeError,
            subprocess.SubprocessError,
            requests.RequestException,
        ) as exc:
            if quality is not None and quality.get("pipeline") == ep.VERSION:
                stage = quality.get("stage", stage)
            diagnostic = failure_diagnostic(stage, exc)
            result = {
                "code": code,
                "name": row.get("name", ""),
                "freeze": state["freeze"],
                "status": "incomplete",
                "diagnostic": diagnostic,
                "error": diagnostic["message"],
                "evidence": packet
                if packet is not None
                else {
                    "structured": {
                        k: row[k]
                        for k in ("industry", "valuation_date", "pb", "roe_mean")
                        if k in row
                    },
                    "documents": [
                        {k: doc[k] for k in ("id", "period", "announced")}
                        for doc in selected_documents
                    ],
                    "scope_gaps": ["原文证据整理尚未完成；所列日期仅为已选公告元数据。"],
                },
            }
            if quality is not None:
                result["quality"] = quality
            save_state(output, {"result": result})
        # Publish and verify first; commit terminal state AND cursor in one atomic write.
        recovered = read_state(output)["result"]
        if recovered != result:
            raise ScreenError("Published report verification failed")
        _atomic_write_bytes(directory / f"report-{code}.md", render(result).encode())
        state["research"][code] = hashlib.sha256(output.read_bytes()).hexdigest()
        state["cursor"] += 1
        state["last_industry"] = row["industry"]
        save_state(directory / "state.json", state)
    published = [
        read_state(directory / f"report-{code}.json")["result"] for code in state["research"]
    ]
    lines = [
        f"# 自动研究批次 {directory.name}",
        f"市场覆盖：{summary['status']}；模式：{frozen['mode']}；估值日：{frozen['data_date']}",
        f"范围 {summary['total']} 家，状态 {json.dumps(summary['counts'], ensure_ascii=False)}",
        "",
    ]
    if screening := frozen.get("prefilter"):
        lines += [
            f"前置门槛：0＜PB≤{screening['pb_max']}；总市值≥{screening['total_mv_min_yi']}亿元，不设上限。",
            "仅筛后同业排名，不是完整行业前三；门槛未经收益验证，可能漏选。",
        ]
    lines += [
        f"- {r['code']}: {r['status']}；变化 {r.get('change', '未建立')}；[报告](report-{r['code']}.md)"
        for r in published
    ]
    lines += [
        f"待研究 {len(state['queue']) - state['cursor']} 家；模型调用 {state['model_calls']}/10。",
        "未安装定时器，未自动投递，未更改个人研究/账户。",
        "抽样不是全市场筛选；scoped_complete仅表示所列事实和推论的有界复核完成，全文未覆盖页及其他缺口仍保留。",
    ]
    _atomic_write_bytes(directory / "summary.md", ("\n".join(lines) + "\n").encode())
