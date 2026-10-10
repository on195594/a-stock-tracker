"""Explicit, bounded research requests. Run separately from the credential-free Web."""

from __future__ import annotations

import fcntl
import hmac
import json
import os
import re
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from a_stock_tracker.auth import create_owner_actor
from a_stock_tracker.automation import (
    candidates,
    coverage,
    locked_root,
    read_result,
    read_state,
    save,
    save_state,
)
from a_stock_tracker.disclosures import render as render_report
from a_stock_tracker.opportunities import load_board
from a_stock_tracker.research import ScreenError

REPO_ROOT = Path(__file__).resolve().parents[1]
LIMITS = {
    "screen": {"title": "财报初筛找候选", "requests": 400, "seconds": 1800, "reports": 0},
    "swing": {"title": "指定股票量价研究", "requests": 20, "seconds": 900, "reports": 0},
    "financial": {"title": "单家公司财报研究", "requests": 100, "seconds": 1800, "reports": 1},
}
OWNER = {"owner": "explicit-research-requests-v1"}


def quote(info: dict[str, Any], mode: str) -> dict[str, Any]:
    return {**LIMITS[mode], "model": info["model"] if mode != "screen" else None}


def validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"id", "mode", "codes", "confirmed", "quote"}:
        raise ValueError("请求格式不正确")
    if not isinstance(value["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["id"]):
        raise ValueError("请求编号不正确")
    mode, codes = value["mode"], value["codes"]
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
    runtime = Path(os.getenv("AUTOMATION_HERMES_RUNTIME", ""))
    model = bool(
        runtime.is_absolute()
        and (runtime / ".venv/bin/python").is_file()
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
        if directory.is_symlink():
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
            if include_result and state["status"] in {"completed", "partial"}:
                state["result"] = self.result(self.directory(identifier), state["request"]["mode"])
            return state

    def recent(self) -> list[dict[str, Any]]:
        runs = []
        paths = [
            p
            for p in self.root.glob("*/request.json")
            if re.fullmatch(r"[a-f0-9]{32}", p.parent.name)
            and not p.parent.is_symlink()
            and not p.is_symlink()
        ]
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p in paths[:10]:
            try:
                state = self.status(p.parent.name, include_result=False)
                runs.append({k: v for k, v in state.items() if k != "result"})
            except (FileNotFoundError, ValueError, ScreenError):
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
            if payload["quote"] != quote(info, payload["mode"]):
                raise ValueError("服务预算或模型已变化，请重新确认")
            if not info["modes"][payload["mode"]]["available"]:
                raise ValueError("该研究类型尚未配置行情或模型服务，请联系管理员")
            handle = self.lease()
            try:
                directory.mkdir(mode=0o700)
                state = {
                    "id": payload["id"],
                    "request": payload,
                    "status": "running",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "limits": LIMITS[payload["mode"]],
                    "message": "预算已确认，后台执行中；离开网页不会取消已受理研究。",
                }
                save_state(path, state)
                Thread(target=self.execute, args=(directory, state, handle), daemon=True).start()
            except BaseException:
                handle.close()
                raise
            return state

    def command(self, directory: Path, payload: dict[str, Any]) -> list[str]:
        mode, codes = payload["mode"], payload["codes"]
        limits = LIMITS[mode]
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
            if codes:
                args += ["--sample", ",".join(codes)]
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
        selected = candidates(summary, frozen)
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
        return {
            "coverage": {
                **{k: summary[k] for k in ("mode", "data_date", "status", "total", "counts")},
                "source_times": frozen.get("source_times", {}),
                "cutoff": frozen.get("cutoff"),
            },
            "candidates": [
                {
                    k: by_code[c].get(k)
                    for k in ("code", "name", "industry", "pb", "roe_mean", "checked_at")
                }
                for c in selected
            ],
            "reports": reports,
        }

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
                    code = process.wait(timeout=state["limits"]["seconds"] + 30)
                finally:
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
            if code not in (0, 2):
                raise ScreenError("Batch failed")
            result = self.result(directory, state["request"]["mode"])
            complete = code == 0 or (
                state["request"]["mode"] == "screen" and result["coverage"]["status"] == "complete"
            )
            state.update(
                result=result,
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

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def dispatch(request: Request, path: str):
        if not hmac.compare_digest(
            request.headers.get("authorization", "").encode(), ("Bearer " + token).encode()
        ):
            raise HTTPException(403, "研究服务授权失败")
        try:
            if request.method == "GET":
                if path == "info":
                    return request_info()
                if path == "runs":
                    return {"runs": store.recent()}
                if path.startswith("runs/"):
                    return store.status(path.removeprefix("runs/"))
            elif path == "runs":
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 4096:
                        raise HTTPException(413, "请求过大")
                return store.submit(json.loads(body))
            raise HTTPException(404, "没有该研究入口")
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
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.parent.stat().st_mode & 0o077 or (path.exists() and not path.is_socket()):
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
    main()
