"""Explicit, bounded research requests. Run separately from the credential-free Web."""

from __future__ import annotations

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
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from a_stock_tracker.auth import create_owner_actor
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
from a_stock_tracker.disclosures import model_command
from a_stock_tracker.disclosures import render as render_report
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
    "swing": {"title": "指定股票量价研究", "requests": 20, "seconds": 900, "reports": 0},
    "financial": {"title": "单家公司财报研究", "requests": 100, "seconds": 1800, "reports": 1},
}
OWNER = {"owner": "explicit-research-requests-v1"}
MAX_ROOT_BYTES = 2_000_000_000
MAX_RUN_BYTES = 256_000_000


def disk_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())


class QuoteChanged(ValueError):
    pass


def quote(info: dict[str, Any], mode: str, remaining: int | None = None) -> dict[str, Any]:
    value = {**info["modes"][mode], "model": info["model"] if mode != "screen" else None}
    value.pop("available", None)
    if mode == "screen" and remaining is not None:
        value["requests"] = min(remaining + 20, 1500)
        # Scanning reserves 65 seconds before attempting a company.
        value["seconds"] = max(66, math.ceil(value["requests"] * 3600 / 1500))
    if mode == "screen":
        value["prefilter"] = info["modes"][mode].get("prefilter")
    else:
        value["model_budget"] = {
            "calls": 10,
            "local_timeout_seconds": 240,
            "output_token_hard_limit": None,
            "cost_hard_limit": None,
            "timeout_guarantees_billing_stop": False,
        }
    return value


def validate_request(value: Any) -> dict[str, Any]:
    required = {"id", "mode", "codes", "confirmed", "quote"}
    if (
        not isinstance(value, dict)
        or not required <= set(value)
        or set(value) - required - {"resume_from", "from"}
    ):
        raise ValueError("请求格式不正确")
    if "from" in value and (
        not isinstance(value["from"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", value["from"])
        or value["mode"] == "screen"
    ):
        raise ValueError("来源初筛编号不正确")
    if not isinstance(value["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["id"]):
        raise ValueError("请求编号不正确")
    mode, codes = value["mode"], value["codes"]
    if "resume_from" in value and (
        mode != "screen"
        or not isinstance(value["resume_from"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", value["resume_from"])
    ):
        raise ValueError("继续初筛必须指定有效的原请求编号")
    if not isinstance(mode, str) or mode not in LIMITS or value["confirmed"] is not True:
        raise ValueError("必须明确确认研究类型与预算")
    if not isinstance(codes, list) or any(
        not isinstance(c, str) or not re.fullmatch(r"(?:60\d{4}\.SH|00\d{4}\.SZ)", c) for c in codes
    ):
        raise ValueError("仅支持沪深主板代码，如 600519.SH 或 000001.SZ")
    if len(set(codes)) != len(codes) or not (
        (mode == "screen" and not codes)
        or (mode == "swing" and 1 <= len(codes) <= 5)
        or (mode == "financial" and len(codes) == 1)
    ):
        raise ValueError("量价研究限一至五家；财报研究限一家；初筛不填代码")
    if not isinstance(value["quote"], dict):
        raise ValueError("必须核对本次预算与模型")
    return value


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
            k: {**v, "available": market and (k == "screen" or model)} for k, v in LIMITS.items()
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

    def status(self, identifier: str, *, include_result: bool = True) -> dict[str, Any]:
        with locked_root(self.root, blocking=True):
            path = self.directory(identifier) / "request.json"
            if not path.is_file():
                raise FileNotFoundError(identifier)
            state = read_state(path)
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
            latest = self.latest(source)
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

    def chain(self, identifier: str) -> tuple[str, int]:
        seen: set[str] = set()
        count = 0
        while True:
            if identifier in seen:
                raise ScreenError("Cyclic research chain")
            seen.add(identifier)
            state = read_state(self.directory(identifier) / "request.json")
            parent = state.get("reused_from") or state.get("continued_from")
            if not parent:
                return identifier, count
            count += bool(state.get("continued_from") and not state.get("reused_from"))
            identifier = parent

    def latest(self, identifier: str) -> str:
        # shortcut: scans every request.json per status read; add an index if records reach thousands.
        state = read_state(self.directory(identifier) / "request.json")
        identifier = state.get("reused_from", identifier)
        successors = {
            s["continued_from"]: s["id"]
            for _, s in self.metadata()
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
        for path, state in self.metadata():
            try:
                root, count = self.chain(path.parent.name)
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
                state = self.status(item["id"], include_result=False)
                state.update(continuations=item["continuations"], reused=item["reused"])
                runs.append(state)
            except (OSError, ValueError, ScreenError):
                continue
        return runs

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
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
        if mode != "screen":
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
            board = load_board(create_owner_actor("0", "0"), root)
            if board is None:
                raise ScreenError("No verified board")
            return {"board": board}
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
            reports.append(
                {
                    "code": code,
                    "status": report["status"],
                    "evidence_review": report.get("evidence_review"),
                    "text": render_report(report),
                }
            )
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

    def execute(self, directory: Path, state: dict[str, Any], handle) -> None:
        process = None
        try:
            # Child inherits the lease: a service crash cannot permit a concurrent second spend.
            with (directory / "execution.log").open("x") as log:
                process = subprocess.Popen(
                    self.command(directory, state["request"]),
                    cwd=str(REPO_ROOT),
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                    pass_fds=(handle.fileno(),),
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


def create_service(root: Path, token: str) -> FastAPI:
    if len(token) < 32 or token.startswith("REPLACE_"):
        raise ValueError("Research service requires a private token of at least 32 characters")
    store = ResearchStore(root)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        codes = {
            400: "invalid_request",
            403: "unauthorized",
            404: "not_found",
            409: "busy",
            413: "too_large",
            503: "acceptance_unknown",
        }
        return JSONResponse(
            {"detail": exc.detail, "code": codes.get(exc.status_code, "service_error")},
            status_code=exc.status_code,
        )

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def dispatch(request: Request, path: str):
        if not hmac.compare_digest(
            request.headers.get("authorization", "").encode(), ("Bearer " + token).encode()
        ):
            raise HTTPException(403, "研究服务授权失败")
        try:
            if request.method == "GET":
                if path == "info":
                    return await run_in_threadpool(request_info)
                if path == "runs":
                    return {"runs": await run_in_threadpool(store.recent)}
                if path.startswith("runs/"):
                    return await run_in_threadpool(store.status, path.removeprefix("runs/"))
            elif path == "runs":
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 4096:
                        raise HTTPException(413, "请求过大")
                return await run_in_threadpool(store.submit, json.loads(body))
            raise HTTPException(404, "没有该研究入口")
        except QuoteChanged:
            return JSONResponse(
                {"detail": "服务预算或模型已变化，请重新确认", "code": "quote_changed"},
                status_code=409,
            )
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
    app = create_service(root, os.environ["RESEARCH_SERVICE_TOKEN"])
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
