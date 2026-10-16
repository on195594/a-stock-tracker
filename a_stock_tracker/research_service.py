"""Explicit, bounded research requests. Run separately from the credential-free Web."""

from __future__ import annotations

import base64
import binascii
import fcntl
import heapq
import hmac
import json
import math
import os
import re
import resource
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from a_stock_tracker.automation import (
    PB_MV_SCREEN,
    coverage,
    digest,
    locked_root,
    read_result,
    read_state,
    save,
    save_state,
)
from a_stock_tracker.bounded_process import execution_lease_environment
from a_stock_tracker.contracts import (
    ErrorBody,
    ErrorCode,
    Quote,
    RankingPage,
    ReportList,
    RunList,
    RunRequest,
    RunState,
    ServiceInfo,
)
from a_stock_tracker.disclosures import TEMPLATE, model_command, structured_report
from a_stock_tracker.opportunities import load_board
from a_stock_tracker.research import SHANGHAI, ScreenError, parse_date

REPO_ROOT = Path(__file__).resolve().parents[1]
LIMITS: dict[str, dict[str, Any]] = {
    "screen": {
        "title": "财报初筛找候选",
        "requests": 1500,
        "seconds": 3600,
        "reports": 0,
        "prefilter": PB_MV_SCREEN,
    },
    "financial": {"title": "单家公司财报研究", "requests": 100, "seconds": 1800, "reports": 1},
    "swing": {"title": "价格纪律参考", "requests": 20, "seconds": 900, "reports": 0},
}
OWNER = {"owner": "explicit-research-requests-v1"}
MAX_ROOT_BYTES = 2_000_000_000
MAX_RUN_BYTES = 256_000_000
MCP_DAILY_LIMIT = 3


def disk_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())


class QuoteChanged(ValueError):
    pass


class ChannelLimit(ValueError):
    pass


def quote(
    info: dict[str, Any] | ServiceInfo, mode: str, remaining: int | None = None
) -> dict[str, Any]:
    if isinstance(info, ServiceInfo):
        info = info.model_dump(by_alias=True, exclude_unset=True)
    value = {**info["modes"][mode], "model": info["model"] if mode == "financial" else None}
    value.pop("available", None)
    if mode == "screen" and remaining is not None:
        value["requests"] = min(remaining + 20, 1500)
        # Scanning reserves 65 seconds before attempting a company.
        value["seconds"] = max(66, math.ceil(value["requests"] * 3600 / 1500))
    if mode == "screen":
        value["prefilter"] = info["modes"][mode].get("prefilter")
    elif mode == "financial":
        value["model_budget"] = {
            "calls": 10,
            "local_timeout_seconds": 240,
            "output_token_hard_limit": None,
            "cost_hard_limit": None,
            "timeout_guarantees_billing_stop": False,
        }
    return Quote.model_validate(value).model_dump(by_alias=True, exclude_unset=True)


def result_summary(result: dict[str, Any]) -> dict[str, Any]:
    if "board" in result:
        board = result["board"]
        return {
            "board": {
                "rule": board.get("rule"),
                "rows": [
                    {
                        k: row[k]
                        for k in (
                            "code",
                            "status",
                            "as_of",
                            "expires",
                            "expired",
                            "levels",
                            "reasons",
                        )
                        if k in row
                    }
                    for row in board["rows"]
                ],
            }
        }
    return {
        "reports": [
            {
                k: report[k]
                for k in (
                    "code",
                    "status",
                    "evidence_review",
                    "documents",
                    "omitted_pages",
                    "error",
                )
            }
            | {
                "quality": report.get("quality"),
                "analysis": {"counterevidence": report["analysis"]["counterevidence"]}
                if "counterevidence" in report["analysis"]
                else {},
            }
            for report in result.get("reports", [])
        ]
    }


def validate_request(value: Any) -> dict[str, Any]:
    try:
        return RunRequest.model_validate(value).model_dump(by_alias=True, exclude_unset=True)
    except ValidationError as exc:
        error = exc.errors()[0].get("ctx", {}).get("error")
        raise ValueError(str(error) if error is not None else "请求格式不正确") from None


def request_info() -> dict[str, Any]:
    market_token = os.getenv("TUSHARE_TOKEN", "")
    market = bool(market_token and not market_token.startswith("REPLACE_"))
    try:
        model_command()
        runtime_ready = True
    except ScreenError:
        runtime_ready = False
    model = bool(
        runtime_ready
        and os.getenv("RESEARCH_PROVIDER")
        and os.getenv("RESEARCH_MODEL")
        and not os.environ["RESEARCH_PROVIDER"].startswith("REPLACE_")
        and not os.environ["RESEARCH_MODEL"].startswith("REPLACE_")
    )
    return {
        "demo": False,
        "modes": {
            k: {**v, "available": market and (k != "financial" or model)} for k, v in LIMITS.items()
        },
        "model": {
            "provider": os.getenv("RESEARCH_PROVIDER", ""),
            "model": os.getenv("RESEARCH_MODEL", ""),
        },
    }


class ResearchStore:
    def __init__(self, root: Path):
        self.root = root
        with locked_root(root, blocking=True):
            marker = root / "request-service.json"
            if marker.exists():
                if read_result(marker) != OWNER:
                    raise ScreenError("Research service owner mismatch")
            else:
                if any(p.name not in {"automation.json", ".lock"} for p in root.iterdir()):
                    raise ScreenError("Research service needs its own empty private root")
                save(marker, OWNER, immutable=True)

    def directory(self, identifier: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("请求编号不正确")
        directory = self.root / identifier
        if directory.is_symlink() or (directory / "request.json").is_symlink():
            raise ScreenError("Symlink request refused")
        return directory

    def lease(self):
        path = self.root / ".execution"
        if path.is_symlink():
            raise ScreenError("Symlink execution lock refused")
        handle = path.open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            handle.close()
            raise
        return handle

    def status(
        self,
        identifier: str,
        *,
        include_result: bool = True,
        states: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with locked_root(self.root, blocking=True):
            path = self.directory(identifier) / "request.json"
            if not path.is_file():
                raise FileNotFoundError(identifier)
            state = RunState.model_validate(read_state(path)).model_dump(
                by_alias=True, exclude_unset=True
            )
            if state["status"] == "running":
                try:
                    handle = self.lease()
                except BlockingIOError:
                    pass
                else:
                    handle.close()
                    state.update(
                        status="interrupted",
                        message="研究进程已中断，未确认发布成功；不会自动重新收费。",
                    )
                    save_state(path, state)
        state.pop("result", None)
        # Published batches are immutable; verify them without holding the request-state lock.
        if include_result and state["status"] in {"completed", "partial"}:
            source = state.get("reused_from", identifier)
            state["result"] = self.result(self.directory(source), state["request"]["mode"])
            if state.get("reused_from"):
                prior_usage = state["result"].get("usage") or {}
                state["result"]["usage"] = {
                    **prior_usage,
                    "requests": 0,
                    "request_limit": state["request"]["quote"]["requests"],
                    "seconds_used": None,
                    "stop_reason": "reused",
                    "newly_covered": 0,
                }
            if state["request"]["mode"] == "screen":
                summary = state["result"].get("coverage", {})
                try:
                    age = (
                        datetime.now(SHANGHAI).date() - parse_date(summary.get("cutoff", ""))
                    ).days
                    state["can_continue"] = (
                        bool(summary.get("counts", {}).get("pending"))
                        and 0 <= age <= 7
                        and summary.get("prefilter") == PB_MV_SCREEN
                    )
                except (ValueError, ScreenError):
                    state["can_continue"] = False
        if state.get("request", {}).get("mode") == "screen":
            source = state.get("reused_from", identifier)
            latest = self.latest(source, states=states)
            state["continued_by"] = latest if latest != source else None
            if state["continued_by"]:
                state["can_continue"] = False
            if state.get("can_continue"):
                state["continuation_quote"] = quote(
                    request_info(), "screen", state["result"]["coverage"]["counts"]["pending"]
                )
        return state

    def metadata(self):
        for path in self.root.glob("*/request.json"):
            if (
                not re.fullmatch(r"[a-f0-9]{32}", path.parent.name)
                or path.parent.is_symlink()
                or path.is_symlink()
            ):
                continue
            try:
                yield path, read_state(path)
            except (OSError, ValueError, ScreenError):
                continue

    def chain(
        self, identifier: str, states: dict[str, dict[str, Any]] | None = None
    ) -> tuple[str, int]:
        seen: set[str] = set()
        count = 0
        while True:
            if identifier in seen:
                raise ScreenError("Cyclic research chain")
            seen.add(identifier)
            path = self.directory(identifier) / "request.json"
            state = (
                states[identifier]
                if states is not None and identifier in states
                else read_state(path)
            )
            parent = state.get("reused_from") or state.get("continued_from")
            if not parent:
                return identifier, count
            count += bool(state.get("continued_from") and not state.get("reused_from"))
            identifier = parent

    def latest(self, identifier: str, states: dict[str, dict[str, Any]] | None = None) -> str:
        # shortcut: scans every request.json on uncached status reads; add an index if records reach thousands.
        path = self.directory(identifier) / "request.json"
        state = (
            states[identifier] if states is not None and identifier in states else read_state(path)
        )
        identifier = state.get("reused_from", identifier)
        successors = {
            s["continued_from"]: s["id"]
            for s in (states.values() if states is not None else (s for _, s in self.metadata()))
            if s.get("continued_from") and not s.get("reused_from")
        }
        seen = set()
        while identifier in successors:
            if identifier in seen:
                raise ScreenError("Cyclic research chain")
            seen.add(identifier)
            identifier = successors[identifier]
        return identifier

    def recent(self) -> list[dict[str, Any]]:
        # Keep only compact chain metadata; load at most ten full states/results.
        chains: dict[str, dict[str, Any]] = {}
        metadata = list(self.metadata())
        states = {path.parent.name: state for path, state in metadata}
        for path, state in metadata:
            try:
                root, count = self.chain(path.parent.name, states=states)
                stamp = path.stat().st_mtime
                item = chains.setdefault(
                    root, {"id": root, "time": 0.0, "continuations": 0, "reused": False}
                )
                item["time"] = max(item["time"], stamp)
                item["reused"] |= bool(state.get("reused_from"))
                if not state.get("reused_from") and count >= item["continuations"]:
                    item.update(id=path.parent.name, continuations=count)
            except (OSError, ValueError, ScreenError):
                continue
        runs = []
        for item in heapq.nlargest(10, chains.values(), key=lambda item: item["time"]):
            try:
                state = self.status(item["id"], include_result=False, states=states)
                if (
                    state["status"] in {"completed", "partial"}
                    and state["request"]["mode"] != "screen"
                ):
                    state["summary"] = result_summary(
                        self.result(self.directory(item["id"]), state["request"]["mode"])
                    )
                state.update(continuations=item["continuations"], reused=item["reused"])
                runs.append(state)
            except (OSError, ValueError, ScreenError):
                continue
        return runs

    def channel_used(self) -> int:
        today = datetime.now(SHANGHAI).date()
        used = 0
        for _, state in self.metadata():
            if state.get("channel", "web") != "mcp":
                continue
            try:
                created = datetime.fromisoformat(state["created_at"]).astimezone(SHANGHAI).date()
            except (KeyError, TypeError, ValueError):
                continue
            used += created == today
        return used

    def submit(self, payload: dict[str, Any], channel: str = "web") -> dict[str, Any]:
        payload = validate_request(payload)
        directory = self.directory(payload["id"])
        with locked_root(self.root, blocking=True):
            path = directory / "request.json"
            if path.exists():
                state = read_state(path)
                if state["request"] != payload:
                    raise ValueError("相同请求编号不能更改范围")
                return state
            info = request_info()
            remaining = None
            if payload.get("resume_from"):
                checkpoint = self.screen_checkpoint(payload["resume_from"])[0]
                remaining = self.result(checkpoint, "screen")["coverage"]["counts"]["pending"]
            if payload.get("from"):
                source = read_state(self.directory(payload["from"]) / "request.json")
                if source["request"]["mode"] != "screen":
                    raise ValueError("来源必须是本研究目录内的初筛请求")
            if payload["quote"] != quote(info, payload["mode"], remaining):
                raise QuoteChanged("服务预算或模型已变化，请重新确认")
            if channel == "mcp" and self.channel_used() >= MCP_DAILY_LIMIT:
                raise ChannelLimit(
                    "MCP 渠道今日提交次数已用完（每日 3 次），明日再试或在网页提交。"
                )
            if not info["modes"][payload["mode"]]["available"]:
                raise ValueError("该研究类型尚未配置行情或模型服务，请联系管理员")
            handle = self.lease()
            try:
                if disk_bytes(self.root) + MAX_RUN_BYTES > MAX_ROOT_BYTES:
                    raise ValueError("研究存储预算不足，请管理员核查；不会自动删除历史数据")
                continuation = None
                if payload.get("resume_from"):
                    continuation = self.screen_checkpoint(payload["resume_from"])[0].name
                    for previous in self.root.glob("*/request.json"):
                        if previous.is_symlink() or previous.parent.is_symlink():
                            continue
                        if read_state(previous).get("continued_from") == continuation:
                            raise ValueError("该检查点已发起后续请求，请打开最近研究核对进度")
                if payload["mode"] == "screen" and not continuation:
                    today = datetime.now(SHANGHAI).date()
                    for previous in self.root.glob("*/request.json"):
                        if previous.is_symlink() or previous.parent.is_symlink():
                            continue
                        old = read_state(previous)
                        if (
                            old["request"]["mode"] == "screen"
                            and old["request"]["quote"] == payload["quote"]
                            and old["status"] in {"completed", "partial"}
                            and not old.get("reused_from")
                            and datetime.fromisoformat(old["created_at"])
                            .astimezone(SHANGHAI)
                            .date()
                            == today
                        ):
                            latest = self.latest(old["id"])
                            old = read_state(self.directory(latest) / "request.json")
                            if old["status"] not in {"completed", "partial"}:
                                continue
                            self.result(self.directory(latest), "screen")
                            directory.mkdir(mode=0o700)
                            state = {
                                **old,
                                "id": payload["id"],
                                "request": payload,
                                "channel": channel,
                                "created_at": datetime.now(timezone.utc).isoformat(),
                                "finished_at": datetime.now(timezone.utc).isoformat(),
                                "reused_from": old["id"],
                                "message": "复用今日已校验初筛；没有新增请求或扩大覆盖。",
                            }
                            save_state(path, state)
                            handle.close()
                            return state
                directory.mkdir(mode=0o700)
                state = {
                    "id": payload["id"],
                    "request": payload,
                    "channel": channel,
                    "status": "running",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "limits": payload["quote"],
                    "message": "预算已确认，后台执行中；离开网页不会取消已受理研究。",
                }
                if continuation:
                    state["continued_from"] = continuation
                save_state(path, state)
                Thread(target=self.execute, args=(directory, state, handle), daemon=True).start()
            except BaseException:
                handle.close()
                raise
            return state

    def screen_checkpoint(self, identifier: str):
        directory = self.directory(identifier)
        if (directory / "request.json").is_symlink():
            raise ScreenError("Symlink checkpoint metadata refused")
        metadata = read_state(directory / "request.json")
        if metadata.get("reused_from"):
            directory = self.directory(metadata["reused_from"])
            if (directory / "request.json").is_symlink():
                raise ScreenError("Symlink checkpoint metadata refused")
            metadata = read_state(directory / "request.json")
        if metadata["request"]["mode"] != "screen" or metadata["status"] != "partial":
            raise ValueError("只能继续尚未完成的财报初筛")
        root = directory / "batch"
        if root.is_symlink():
            raise ScreenError("Symlink checkpoint refused")
        runs = list(root.glob("run-*"))
        if len(runs) != 1 or not re.fullmatch(r"run-[0-9T-]+-[a-f0-9]{12}", runs[0].name):
            raise ScreenError("Invalid checkpoint run")
        run = runs[0]
        if run.is_symlink() or any(p.is_symlink() for p in run.iterdir()):
            raise ScreenError("Symlink checkpoint refused")
        state = read_state(run / "state.json")
        frozen = read_result(run / "universe.json", state["freeze"])
        if frozen.get("prefilter") != PB_MV_SCREEN:
            raise ValueError("初筛规则已变化，请确认新规则后发起新批次")
        age = (datetime.now(SHANGHAI).date() - parse_date(frozen["cutoff"])).days
        if not 0 <= age <= 7:
            raise ValueError("原初筛已超过七天，请明确发起新的初筛")
        if state["research"] or state["model_calls"] or frozen["sample"]:
            raise ScreenError("Only scan-only market checkpoints can continue")
        if any(not re.fullmatch(r"[0-9]{6}\.(SH|SZ)", code) for code in state["scan"]):
            raise ScreenError("Invalid checkpoint security code")
        result = self.result(directory, "screen")
        if not result["coverage"]["counts"].get("pending"):
            raise ValueError("没有尚未覆盖的股票；资料缺口不会自动重试")
        return directory, run, state, frozen

    def copy_checkpoint(self, directory: Path, identifier: str) -> str:
        _, source, state, frozen = self.screen_checkpoint(identifier)
        with locked_root(directory / "batch") as root:
            target = root / source.name
            target.mkdir(mode=0o700)
            save(target / "universe.json", frozen, immutable=True)
            for code, sha in state["scan"].items():
                name = f"scan-{code}.json"
                envelope = read_result(source / name, sha)
                if (
                    envelope["state"]["freeze"] != state["freeze"]
                    or envelope["state"]["result"]["code"] != code
                ):
                    raise ScreenError("Checkpoint scan identity mismatch")
                save(target / name, envelope, immutable=True)
            save_state(target / "state.json", state)
        return source.name

    def command(self, directory: Path, payload: dict[str, Any]) -> list[str]:
        mode, codes = payload["mode"], payload["codes"]
        limits = payload["quote"]
        args = [
            sys.executable,
            "-m",
            "a_stock_tracker.opportunities" if mode == "swing" else "a_stock_tracker.automation",
            "--root",
            str(directory / "batch"),
            "--request-budget",
            str(limits["requests"]),
            "--seconds",
            str(limits["seconds"]),
        ]
        if mode == "swing":
            args += ["--codes", ",".join(codes), "--latest"]
        else:
            args += ["--max-research", str(limits["reports"])]
            if mode == "screen":
                args += [
                    "--pb-mv-screen",
                    "--screen-contract",
                    digest(payload["quote"]["prefilter"]),
                ]
            if codes:
                args += ["--sample", ",".join(codes)]
        if payload.get("resume_from"):
            run_name = self.copy_checkpoint(directory, payload["resume_from"])
            frozen_state = read_state(directory / "batch" / run_name / "state.json")
            args += [
                "--resume",
                run_name,
                "--provider",
                frozen_state["model"]["provider"],
                "--model",
                frozen_state["model"]["model"],
            ]
        if mode == "financial":
            args += [
                "--provider",
                os.environ["RESEARCH_PROVIDER"],
                "--model",
                os.environ["RESEARCH_MODEL"],
            ]
        return args

    def result(self, directory: Path, mode: str) -> dict[str, Any]:
        root = directory / "batch"
        if mode == "swing":
            board = load_board(root)
            if board is None:
                raise ScreenError("No verified board")
            return {"board": board}
        result, _ = self.batch_result(directory, mode)
        result["truncated"] = False
        # Leave room for request metadata under the client's 4 MB response ceiling.
        if len(json.dumps(result, ensure_ascii=False).encode()) > 3_800_000:
            result["truncated"] = True
            result["truncation_note"] = (
                "排名超过响应大小上限，仅展示部分行业/排名；不代表完整结果。"
            )
            groups = result["groups"]
            result["groups"] = []
            used = len(json.dumps(result, ensure_ascii=False).encode())
            for group in groups:
                kept = {**group, "ranking": []}
                used += len(json.dumps(kept, ensure_ascii=False).encode()) + 2
                for row in group["ranking"]:
                    size = len(json.dumps(row, ensure_ascii=False).encode()) + 2
                    if used + size > 3_800_000:
                        break
                    kept["ranking"].append(row)
                    used += size
                if kept["ranking"]:
                    result["groups"].append(kept)
                if len(kept["ranking"]) != len(group["ranking"]):
                    break
        return result

    def batch_result(self, directory: Path, mode: str) -> tuple[dict[str, Any], str]:
        """Verify and project the full batch before response-size truncation."""
        root = directory / "batch"
        runs = list(root.glob("run-*"))
        if len(runs) != 1:
            raise ScreenError("Ambiguous financial batch")
        run = runs[0]
        state = read_state(run / "state.json")
        frozen = read_result(run / "universe.json", state["freeze"])
        rows = [
            read_result(run / f"scan-{r['code']}.json", state["scan"][r["code"]])["state"]["result"]
            if r["code"] in state["scan"]
            else r
            for r in frozen["rows"]
        ]
        summary = coverage(rows, frozen)
        by_code = {r["code"]: r for r in rows}
        selected = (
            [r["code"] for r in rows if r["status"] == "qualified"] if mode != "screen" else []
        )
        reports = []
        for code, sha in state["research"].items():
            report = read_result(run / f"report-{code}.json", sha)["state"]["result"]
            reports.append(structured_report(report))
        result: dict[str, Any] = {
            "coverage": {
                **{k: summary[k] for k in ("mode", "data_date", "status", "total", "counts")},
                "source_times": frozen.get("source_times", {}),
                "cutoff": frozen.get("cutoff"),
                "prefilter": summary["prefilter"],
                "prefilter_counts": summary["prefilter_counts"],
            },
            "candidates": [
                {
                    k: by_code[c].get(k)
                    for k in (
                        "code",
                        "name",
                        "industry",
                        "pb",
                        "total_mv",
                        "roe_mean",
                        "checked_at",
                    )
                }
                for c in selected
            ],
            "reports": reports,
        }
        result["groups"] = [
            {
                "industry": group["industry"],
                "periods": group["periods"],
                "ranking": [
                    {
                        **rank,
                        **{
                            k: by_code[rank["code"]].get(k)
                            for k in ("name", "pb", "roe_mean", "total_mv")
                        },
                    }
                    for rank in group["ranking"]
                ],
            }
            for group in summary["groups"]
        ]
        result["usage"] = state.get("usage")
        return result, state["freeze"]

    def find_reports(self, code: str) -> dict[str, Any]:
        if not re.fullmatch(r"(?:60\d{4}\.SH|00\d{4}\.SZ)", code):
            raise HTTPException(400, "仅支持沪深主板代码，如 600519.SH 或 000001.SZ")
        candidates = []
        for path, state in self.metadata():
            request = state.get("request", {})
            if (
                request.get("mode") != "financial"
                or state.get("status") not in {"completed", "partial"}
                or code not in request.get("codes", [])
            ):
                continue
            try:
                finished_at = state.get("finished_at") or state.get("created_at")
                timestamp = datetime.fromisoformat(finished_at).timestamp() if finished_at else 0
                candidates.append((timestamp, path, finished_at))
            except (OSError, ValueError, ScreenError):
                continue
        candidates.sort(key=lambda item: item[0], reverse=True)
        reports = []
        for _, path, finished_at in candidates:
            try:
                result = self.result(self.directory(path.parent.name), "financial")
                for report in result.get("reports", []):
                    if report["code"] == code and report.get("evidence_review") == "passed":
                        reports.append(
                            {
                                "run_id": path.parent.name,
                                "code": code,
                                "name": report.get("name"),
                                "finished_at": finished_at,
                                "status": report["status"],
                                "rule": report.get("rule"),
                                "documents": report.get("documents", []),
                                "omitted_pages": report.get("omitted_pages"),
                                "gap_count": len(report.get("gaps", [])),
                                "quality": report.get("quality"),
                            }
                        )
                        if len(reports) == 20:
                            return {"code": code, "reports": reports}
            except (OSError, ValueError, ScreenError):
                continue
        return {"code": code, "reports": reports}

    def ranking(
        self,
        identifier: str,
        industry: str | None = None,
        query: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        state = self.status(identifier, include_result=False)
        if state.get("request", {}).get("mode") != "screen" or state["status"] not in {
            "completed",
            "partial",
        }:
            raise HTTPException(400, "该研究没有可分页的初筛排名")
        if query is not None and len(query) > 20:
            raise HTTPException(400, "搜索条件最多 20 个字符")
        result, freeze = self.batch_result(
            self.directory(state.get("reused_from", identifier)), "screen"
        )
        rows = [
            {"industry": group["industry"], **rank}
            for group in result["groups"]
            if not industry or industry == group["industry"]
            for rank in group["ranking"]
            if not query or query in rank["code"] or query in (rank.get("name") or "")
        ]
        binding = {"freeze": freeze, "industry": industry, "query": query}
        offset = 0
        if cursor is not None:
            try:
                decoded = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                offset = decoded["offset"]
                if (
                    decoded != {**binding, "offset": offset}
                    or type(offset) is not int
                    or not 0 <= offset <= len(rows)
                ):
                    raise ValueError("Invalid cursor")
            except (ValueError, TypeError, KeyError, binascii.Error):
                raise HTTPException(400, "分页游标与当前批次或过滤条件不符") from None
        next_offset = offset + 50
        is_last = next_offset >= len(rows)
        return {
            "run_id": identifier,
            "total": len(rows),
            "offset": offset,
            "items": rows[offset:next_offset],
            "next_cursor": None
            if is_last
            else base64.urlsafe_b64encode(
                json.dumps({**binding, "offset": next_offset}, separators=(",", ":")).encode()
            ).decode(),
            "is_last": is_last,
            "prefilter": result["coverage"]["prefilter"],
            "coverage": result["coverage"],
            "industries": [group["industry"] for group in result["groups"]],
        }

    def execute(self, directory: Path, state: dict[str, Any], handle) -> None:
        process = None
        try:
            # Pass one identified lease to the batch and its bounded model/PDF children.
            # They retain it even if both the receiver and batch leader die.
            with (directory / "execution.log").open("x") as log:
                process = subprocess.Popen(
                    self.command(directory, state["request"]),
                    cwd=str(REPO_ROOT),
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                    pass_fds=(handle.fileno(),),
                    env=execution_lease_environment(handle.fileno()),
                )
                try:
                    deadline = time.monotonic() + state["limits"]["seconds"] + 30
                    while True:
                        if disk_bytes(directory) > MAX_RUN_BYTES:
                            raise ScreenError("Research disk budget exceeded")
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ScreenError("Research time budget exceeded")
                        try:
                            code = process.wait(timeout=min(2, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            continue
                finally:
                    # A dead leader can leave model descendants running; retain the lease until cleanup.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
            if code not in (0, 2) or disk_bytes(directory) > MAX_RUN_BYTES:
                raise ScreenError("Batch failed")
            result = self.result(directory, state["request"]["mode"])
            complete = code == 0 or (
                state["request"]["mode"] == "screen" and result["coverage"]["status"] == "complete"
            )
            state.pop("result", None)
            state["usage"] = result.get("usage")
            state.update(
                exit_code=code,
                status="completed" if complete else "partial",
                message="本次研究已结束。请核对覆盖、缺口与证据，不是买入信号。",
            )
        except Exception:
            state.pop("result", None)
            state.update(
                status="failed",
                message="本次研究未通过或预算已耗尽；没有成功发布可用结果。不会自动重试。",
            )
        finally:
            if state.get("usage") is None and state["request"]["mode"] != "swing":
                try:
                    root = directory / "batch"
                    runs = list(root.glob("run-*"))
                    audit = runs[0] / "state.json" if len(runs) == 1 else root / "invocation.json"
                    if (
                        not root.is_symlink()
                        and not audit.parent.is_symlink()
                        and not audit.is_symlink()
                        and audit.exists()
                    ):
                        value = read_state(audit).get("usage") if runs else read_result(audit)
                        state["usage"] = (
                            {
                                k: value.get(k)
                                for k in (
                                    "requests",
                                    "request_limit",
                                    "seconds_used",
                                    "stop_reason",
                                    "newly_covered",
                                    "covered",
                                    "remaining",
                                )
                            }
                            if value
                            else None
                        )
                except (OSError, ValueError, ScreenError):
                    state["usage"] = None
            state["finished_at"] = datetime.now(timezone.utc).isoformat()
            try:
                with locked_root(self.root, blocking=True):
                    save_state(directory / "request.json", state)
            finally:
                handle.close()


def create_service(root: Path, tokens: dict[str, str]) -> FastAPI:
    tokens = dict(tokens)
    if "web" not in tokens or any(
        len(token) < 32 or token.startswith("REPLACE_") for token in tokens.values()
    ):
        raise ValueError("Research service requires a private token of at least 32 characters")
    if len(set(tokens.values())) != len(tokens):
        raise ValueError("Research service channel tokens must be distinct")
    store = ResearchStore(root)

    def channel_info(channel: str) -> dict[str, Any]:
        info = request_info()
        # Probe under the root lock so the probe never races a submit for the lease.
        with locked_root(store.root, blocking=True):
            try:
                store.lease().close()
                busy = False
            except BlockingIOError:
                busy = True
        return {
            **info,
            "channel": channel,
            "channel_remaining": max(0, MCP_DAILY_LIMIT - store.channel_used())
            if channel == "mcp"
            else None,
            "busy": busy,
            "rule": TEMPLATE,
        }

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        codes: dict[int, ErrorCode] = {
            400: "invalid_request",
            403: "unauthorized",
            404: "not_found",
            409: "busy",
            413: "too_large",
            429: "channel_limit",
            503: "acceptance_unknown",
        }
        return JSONResponse(
            ErrorBody(
                detail=exc.detail, code=codes.get(exc.status_code, "service_error")
            ).model_dump(by_alias=True, exclude_unset=True),
            status_code=exc.status_code,
        )

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def dispatch(request: Request, path: str):
        channel = None
        for key, token in tokens.items():
            if hmac.compare_digest(
                request.headers.get("authorization", "").encode(), ("Bearer " + token).encode()
            ):
                channel = key
        if channel is None:
            raise HTTPException(403, "研究服务授权失败")
        try:
            if request.method == "GET":
                if path == "info":
                    return ServiceInfo.model_validate(
                        await run_in_threadpool(channel_info, channel)
                    ).model_dump(by_alias=True, exclude_unset=True)
                if path == "runs":
                    return RunList.model_validate(
                        {"runs": await run_in_threadpool(store.recent)}
                    ).model_dump(by_alias=True, exclude_unset=True)
                if path == "reports":
                    return ReportList.model_validate(
                        await run_in_threadpool(
                            store.find_reports, request.query_params.get("code", "")
                        )
                    ).model_dump(by_alias=True, exclude_unset=True)
                if path.startswith("runs/") and path.endswith("/ranking"):
                    return RankingPage.model_validate(
                        await run_in_threadpool(
                            store.ranking,
                            path.removeprefix("runs/").removesuffix("/ranking"),
                            request.query_params.get("industry"),
                            request.query_params.get("query"),
                            request.query_params.get("cursor"),
                        )
                    ).model_dump(by_alias=True, exclude_unset=True)
                if path.startswith("runs/"):
                    return RunState.model_validate(
                        await run_in_threadpool(store.status, path.removeprefix("runs/"))
                    ).model_dump(by_alias=True, exclude_unset=True)
            elif path == "runs":
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 4096:
                        raise HTTPException(413, "请求过大")
                state = await run_in_threadpool(store.submit, json.loads(body), channel)
                try:
                    return RunState.model_validate(state).model_dump(
                        by_alias=True, exclude_unset=True
                    )
                except ValidationError:
                    # The run may already be accepted and billed; never report it as rejected.
                    raise HTTPException(503, "研究状态未确认，请稍后核对原请求编号。") from None
            raise HTTPException(404, "没有该研究入口")
        except QuoteChanged:
            return JSONResponse(
                ErrorBody(
                    detail="服务预算或模型已变化，请重新确认", code="quote_changed"
                ).model_dump(by_alias=True, exclude_unset=True),
                status_code=409,
            )
        except ChannelLimit as exc:
            raise HTTPException(429, str(exc)) from None
        except FileNotFoundError:
            raise HTTPException(404, "没有该研究记录") from None
        except (ValueError, ScreenError):
            raise HTTPException(
                400, "请求、配置或结果完整性未通过核查；不能当作成功结果。"
            ) from None
        except BlockingIOError:
            raise HTTPException(409, "已有研究执行中，请查看进度，不重复启动。") from None
        except OSError:
            raise HTTPException(503, "研究状态未确认，请稍后核对原请求编号。") from None

    return app


def main() -> None:
    import uvicorn

    if "RESEARCH_SERVICE_ROOT" not in os.environ or "RESEARCH_SERVICE_TOKEN" not in os.environ:
        sys.exit(
            "RESEARCH_SERVICE_ROOT and RESEARCH_SERVICE_TOKEN are required; load .research.env first"
        )
    os.umask(0o077)
    root = Path(os.environ["RESEARCH_SERVICE_ROOT"])
    tokens = {"web": os.environ["RESEARCH_SERVICE_TOKEN"]}
    if "RESEARCH_SERVICE_MCP_TOKEN" in os.environ:
        tokens["mcp"] = os.environ["RESEARCH_SERVICE_MCP_TOKEN"]
    app = create_service(root, tokens)
    socket = os.getenv("RESEARCH_SERVICE_SOCKET")
    if socket:
        path = Path(socket)
        if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("Research socket must have an explicit non-symlink path")
        bridge, artifacts = path.parent.resolve(), root.resolve()
        if bridge == artifacts or bridge in artifacts.parents or artifacts in bridge.parents:
            raise ValueError("Research bridge and artifacts must be separate directories")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (
            path.parent.stat().st_mode & 0o077
            or (path.exists() and not path.is_socket())
            or any(p != path for p in path.parent.iterdir())
        ):
            raise ValueError("Research socket directory must be private and socket-only")
        uvicorn.run(app, uds=str(path), access_log=False)
    else:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=int(os.getenv("RESEARCH_SERVICE_PORT", "8560")),
            access_log=False,
        )


if __name__ == "__main__":
    # Includes execution logs and inherited worker writes; never truncate historical data.
    resource.setrlimit(resource.RLIMIT_FSIZE, (32_000_000, 32_000_000))
    main()
