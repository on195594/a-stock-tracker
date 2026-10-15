"""Server-rendered research pages: plain forms and links, one same-origin script."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from a_stock_tracker.auth import ACTIVE_SESSIONS, Actor, AuthError, purge_expired_sessions
from a_stock_tracker.research_client import (
    DemoResearchClient,
    ResearchQuoteChanged,
    ResearchRejected,
    ResearchUnavailable,
    configured_client,
)
from a_stock_tracker.research_service import quote, validate_request
from a_stock_tracker.web import presenters as p
from a_stock_tracker.web import session as s
from a_stock_tracker.web.security import Origin, SecurityMiddleware
from a_stock_tracker.web.settings import Settings, settings_from_env

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
QUOTE_CHANGED = "报价或模型已变化，已刷新报价并保留输入；请重新核对并勾选确认。"
LOGIN_ERRORS = {
    "state": "登录请求已过期或不是本浏览器发起，请重新登录。",
    "denied": "该 GitHub 账号未获授权。",
    "failed": "GitHub 登录未完成，请重新尝试。",
}


def plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True, exclude_unset=True)
    if isinstance(value, list):
        return [plain(item) for item in value]
    return value


def asset_version() -> str:
    digest = hashlib.sha256()
    for path in sorted(STATIC.iterdir()):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def create_app(
    settings: Settings,
    client: Any = None,
    *,
    exchange: Callable[[str, Settings], Any] = s.github_user_id,
) -> SecurityMiddleware:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    version = asset_version()
    templates.env.globals.update(
        static=lambda name: f"/static/{name}?v={version}", p=p, demo=settings.demo
    )
    origin = Origin(settings)

    def actor_of(request: Request) -> Actor | None:
        return s.current_actor(request, settings)

    def to_login() -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    def render(
        request: Request, actor: Actor | None, name: str, context: dict[str, Any], status: int = 200
    ) -> Response:
        # In-flight revocation: re-check the session after any awaited receiver read.
        if actor is not None and actor_of(request) is None:
            return to_login()
        return templates.TemplateResponse(
            request,
            name,
            {**context, "actor": actor, "csrf": s.csrf_token(actor, settings) if actor else ""},
            status_code=status,
        )

    def failure(
        request: Request, actor: Actor | None, title: str, detail: str, status: int, **links: Any
    ) -> Response:
        return render(
            request, actor, "message.html", {"title": title, "detail": detail, **links}, status
        )

    async def post_guard(request: Request) -> tuple[Actor | None, dict[str, Any] | None]:
        """POST provenance and CSRF; returns (actor, form) or (None, None) when refused."""
        headers = {k.lower(): v for k, v in request.headers.items()}
        if not origin.same_origin(headers):
            return None, None
        form = dict(await request.form())
        actor = actor_of(request)
        if actor is None or not s.csrf_ok(actor, settings, form.get("csrf")):
            return None, None
        return actor, form

    def refused(request: Request) -> Response:
        return failure(
            request,
            None,
            "请求未通过来源核查",
            "该操作需要从本站页面提交，或登录已失效。未执行任何研究。",
            403,
            back="/",
        )

    # ---- authentication -------------------------------------------------------------

    @app.get("/login")
    async def login(request: Request) -> Response:
        if actor_of(request) is not None:
            return RedirectResponse("/", status_code=303)
        error = LOGIN_ERRORS.get(request.query_params.get("error", ""))
        return render(request, None, "login.html", {"error": error})

    @app.get("/oauth/start")
    async def oauth_start() -> Response:
        if settings.demo:
            raise StarletteHTTPException(404)
        return s.authorize_redirect(lambda url: RedirectResponse(url, status_code=303), settings)

    @app.get("/oauth_callback")
    async def oauth_callback(request: Request) -> Response:
        if settings.demo:
            raise StarletteHTTPException(404)
        valid_state = s.consume_state(request)
        if not valid_state:
            response: Response = RedirectResponse("/login?error=state", status_code=303)
        else:
            try:
                actor = await run_in_threadpool(
                    s.owner_from_code, request.query_params.get("code", ""), settings, exchange
                )
            except AuthError as exc:
                reason = "denied" if "unauthorized" in str(exc) else "failed"
                response = RedirectResponse(f"/login?error={reason}", status_code=303)
            else:
                response = RedirectResponse("/", status_code=303)
                s.start_session(response, settings, actor.user_id)
        s.clear_cookie(response, settings, s.STATE_COOKIE)
        return response

    @app.post("/login/demo")
    async def demo_login(request: Request) -> Response:
        headers = {k.lower(): v for k, v in request.headers.items()}
        if not settings.demo:
            raise StarletteHTTPException(404)
        if not origin.same_origin(headers):
            return refused(request)
        response = RedirectResponse("/", status_code=303)
        s.start_session(response, settings, s.DEMO_USER)
        return response

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        actor, form = await post_guard(request)
        if actor is None and actor_of(request) is not None:
            return refused(request)
        response = RedirectResponse("/login", status_code=303)
        s.end_session(request, response, settings)
        return response

    @app.get("/settings")
    async def account(request: Request) -> Response:
        actor = actor_of(request)
        if actor is None:
            return to_login()
        expires = None
        if actor.sid:
            purge_expired_sessions()
            entry = ACTIVE_SESSIONS.get(actor.sid)
            if entry:
                expires = p.local_time(datetime.fromtimestamp(entry[1], timezone.utc).isoformat())
        return render(request, actor, "settings.html", {"expires": expires})

    # ---- research pages -------------------------------------------------------------

    async def read(fn: Callable[..., Any], *args: Any) -> Any:
        return plain(await run_in_threadpool(fn, *args))

    @app.get("/")
    async def home(request: Request) -> Response:
        actor = actor_of(request)
        if actor is None:
            return to_login()
        context: dict[str, Any] = {"connected": client is not None}
        if client is not None:
            try:
                # Independent reads run concurrently, as the Flet home page did.
                info, recent = await asyncio.gather(read(client.info), read(client.recent))
            except ResearchUnavailable:
                context["unreachable"] = True
            else:
                context["configured"] = any(m.get("available") for m in info["modes"].values())
                context["groups"] = p.recent_groups(recent)
        return render(request, actor, "home.html", context)

    async def form_page(
        request: Request,
        actor: Actor,
        *,
        mode: str,
        code_text: str = "",
        source: str = "",
        resume_from: str = "",
        explicit_resume: bool = False,
        feedback: str = "",
        status: int = 200,
    ) -> Response:
        if client is None:
            return render(request, actor, "unconnected.html", {})
        try:
            info = await read(client.info)
            if mode not in info["modes"]:
                mode = "financial"
            resume_source = None
            if mode == "screen" and not resume_from:
                recent = await read(client.recent)
                latest = next(
                    (r for r in recent if (r.get("request") or {}).get("mode") == "screen"), None
                )
                if latest and latest["status"] == "partial":
                    resume_from = latest["id"]
            if mode == "screen" and resume_from:
                if not p.RUN_ID.fullmatch(resume_from):
                    resume_source = None
                else:
                    resume_source = await read(client.status, resume_from)
                pending = (
                    ((resume_source or {}).get("result") or {})
                    .get("coverage", {})
                    .get("counts", {})
                    .get("pending", 0)
                )
                if (
                    not resume_source
                    or (resume_source.get("request") or {}).get("mode") != "screen"
                    or resume_source["status"] != "partial"
                    or not pending
                    or not resume_source.get("can_continue")
                ):
                    if explicit_resume:
                        return failure(
                            request,
                            actor,
                            "该初筛不能继续",
                            "已无待覆盖股票、冻结资料已超过七天或筛选规则已变化。请返回入口核对并确认新的初筛。",
                            409,
                            back="/analysis",
                        )
                    resume_from, resume_source = "", None
        except ResearchUnavailable:
            return failure(
                request,
                actor,
                "研究服务暂时无法连接",
                "后台研究服务未运行或无法连接。请联系管理员核对宿主服务状态；已受理的后台研究继续受预算约束。",
                503,
                # Rebuild a GET form URL from the inputs, even when reached from a POST.
                retry="/analysis?"
                + urlencode(
                    {
                        key: value
                        for key, value in {
                            "mode": mode,
                            "code": code_text,
                            "from": source,
                            "resume_from": resume_from,
                        }.items()
                        if value
                    }
                ),
                back="/",
            )
        if resume_source:
            offer = resume_source["continuation_quote"]
            coverage = resume_source["result"]["coverage"]
            continuation = (
                f"继续原初筛\n冻结依据日期 {coverage['data_date']}，"
                f"尚未覆盖 {coverage['counts']['pending']} 家；只扫描未覆盖部分，原批次不变。\n"
                "只覆盖剩余部分，不自动重试缺口；以上上限是本次新增预算。"
            )
        else:
            offer, continuation = quote(info, mode), None
        available = info["modes"][mode]["available"]
        return render(
            request,
            actor,
            "analysis.html",
            {
                "mode": mode,
                "modes": info["modes"],
                "title": p.MODE_TITLES[mode],
                "code_text": code_text,
                "source": source if mode != "screen" and p.RUN_ID.fullmatch(source) else "",
                "resume_from": resume_from if resume_source else "",
                "request_id": uuid.uuid4().hex,
                "quote_json": json.dumps(offer, ensure_ascii=False, separators=(",", ":")),
                "lines": p.quote_lines(info, mode, offer),
                "continuation": continuation,
                "confirm_label": p.confirmation_label(info, mode),
                "submit_label": "确认新增预算并继续初筛" if resume_source else "确认启动研究",
                "available": available,
                "feedback": feedback
                or ("" if available else "本类型尚未配置，需管理员完成一次连接；不能假装已研究。"),
            },
            status,
        )

    @app.get("/analysis")
    async def analysis(request: Request) -> Response:
        actor = actor_of(request)
        if actor is None:
            return to_login()
        q = request.query_params
        return await form_page(
            request,
            actor,
            mode=q.get("mode", "financial"),
            code_text=q.get("code", "")[:80],
            source=q.get("from", ""),
            resume_from=q.get("resume_from", ""),
            explicit_resume=bool(q.get("resume_from")),
        )

    async def submit(
        request: Request, actor: Actor, payload: dict[str, Any], code_text: str
    ) -> Response:
        try:
            run = await read(client.submit, payload)
        except ResearchQuoteChanged:
            return await form_page(
                request,
                actor,
                mode=payload["mode"],
                code_text=code_text,
                source=payload.get("from", ""),
                resume_from=payload.get("resume_from", ""),
                feedback=QUOTE_CHANGED,
                status=409,
            )
        except ResearchRejected as exc:
            return await form_page(
                request,
                actor,
                mode=payload["mode"],
                code_text=code_text,
                source=payload.get("from", ""),
                resume_from=payload.get("resume_from", ""),
                feedback=str(exc),
                status=400,
            )
        except ResearchUnavailable as exc:
            # Acceptance unknown: only the same id may be resubmitted, never a new request.
            return render(
                request,
                actor,
                "reconcile.html",
                {
                    "payload": payload,
                    "payload_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    "costs": p.frozen_cost_lines(payload),
                    "code_text": code_text,
                    "feedback": str(exc),
                },
                503,
            )
        return RedirectResponse("/analysis/" + run["id"], status_code=303)

    @app.post("/analysis")
    async def start(request: Request) -> Response:
        actor, form = await post_guard(request)
        if actor is None or form is None:
            return refused(request)
        if client is None:
            return render(request, actor, "unconnected.html", {})
        mode = str(form.get("mode", ""))
        code_text = str(form.get("codes", ""))[:80]
        source, resume_from = str(form.get("from", "")), str(form.get("resume_from", ""))

        def again(message: str) -> Any:
            return form_page(
                request,
                actor,
                mode=mode,
                code_text=code_text,
                source=source,
                resume_from=resume_from,
                feedback=message,
                status=400,
            )

        if mode not in p.MODE_TITLES:
            return await again("请选择研究类型后再确认。")
        codes = [] if mode == "screen" else p.normalize_codes(code_text)
        if mode != "screen" and (not codes or len(codes) > (1 if mode == "financial" else 5)):
            return await again(
                "请只输入一家公司的股票代码，再重新确认。"
                if mode == "financial"
                else "请输入 1–5 家公司的股票代码，再重新确认。"
            )
        if form.get("confirmed") != "on":
            return await again("请先勾选确认范围、预算与费用边界，再启动研究。")
        try:
            offer = json.loads(str(form.get("quote", "")))
        except ValueError:
            return await again(QUOTE_CHANGED)
        payload: dict[str, Any] = {
            "id": str(form.get("id", "")),
            "mode": mode,
            "codes": codes,
            "confirmed": True,
            "quote": offer,
        }
        if mode == "screen" and resume_from:
            payload["resume_from"] = resume_from
        if mode != "screen" and source:
            payload["from"] = source
        try:
            payload = validate_request(payload)
        except ValueError as exc:
            return await again(str(exc))
        return await submit(request, actor, payload, code_text)

    @app.post("/analysis/reconcile")
    async def reconcile(request: Request) -> Response:
        actor, form = await post_guard(request)
        if actor is None or form is None:
            return refused(request)
        if client is None:
            return render(request, actor, "unconnected.html", {})
        try:
            payload = validate_request(json.loads(str(form.get("payload", ""))))
        except ValueError:
            return failure(
                request,
                actor,
                "原请求无法核对",
                "原请求内容不完整，未提交。",
                400,
                back="/analysis",
            )
        return await submit(request, actor, payload, str(form.get("codes", ""))[:80])

    def bad_id(request: Request, actor: Actor) -> Response:
        return failure(
            request,
            actor,
            "请求编号不正确",
            "请核对请求编号，或返回重新查看最近研究。",
            404,
            back="/analysis",
        )

    async def load_run(request: Request, actor: Actor, identifier: str) -> Any:
        if client is None:
            return render(request, actor, "unconnected.html", {})
        try:
            return await read(client.status, identifier)
        except ResearchUnavailable as exc:
            return failure(
                request,
                actor,
                "研究状态暂不可确认",
                f"{exc} 任务可能仍在后台执行。重新查看原请求，不另起一轮收费。",
                503,
                retry="/analysis/" + identifier,
                back="/analysis",
            )

    @app.get("/analysis/{identifier}/state.json")
    async def state(request: Request, identifier: str) -> Response:
        actor = actor_of(request)
        if actor is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not p.RUN_ID.fullmatch(identifier) or client is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        try:
            run = await read(client.status, identifier)
        except ResearchUnavailable:
            return JSONResponse({"error": "unavailable"}, status_code=503)
        if actor_of(request) is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"status": run["status"]})

    @app.get("/analysis/{identifier}/ranking")
    async def ranking(request: Request, identifier: str) -> Response:
        actor = actor_of(request)
        if actor is None:
            return to_login()
        if not p.RUN_ID.fullmatch(identifier):
            return bad_id(request, actor)
        if client is None:
            return render(request, actor, "unconnected.html", {})
        q = request.query_params
        industry, term = q.get("industry") or None, (q.get("query") or "").strip()[:20] or None
        try:
            page = await read(client.ranking, identifier, industry, term, q.get("cursor") or None)
        except ResearchRejected as exc:
            return failure(
                request, actor, "无法读取排名", str(exc), 400, back="/analysis/" + identifier
            )
        except ResearchUnavailable:
            return failure(
                request,
                actor,
                "排名暂不可读取",
                "研究服务暂时无法连接；未启动新请求。",
                503,
                retry=str(request.url.path)
                + ("?" + request.url.query if request.url.query else ""),
                back="/analysis/" + identifier,
            )
        return render(
            request,
            actor,
            "ranking.html",
            {
                "run_id": identifier,
                "page": page,
                "rows": [p.ranking_row(r) for r in page["items"]],
                "industry": industry or "",
                "query": term or "",
            },
        )

    @app.get("/analysis/{identifier}/{code}")
    async def price_detail(request: Request, identifier: str, code: str) -> Response:
        actor = actor_of(request)
        if actor is None:
            return to_login()
        if not p.RUN_ID.fullmatch(identifier) or not p.CODE.fullmatch(code):
            return bad_id(request, actor)
        run = await load_run(request, actor, identifier)
        if isinstance(run, Response):
            return run
        board = (run.get("result") or {}).get("board")
        if board is None:
            return failure(
                request,
                actor,
                "没有价格参考",
                "该研究不含价格参考批次。",
                404,
                back="/analysis/" + identifier,
            )
        return render(
            request,
            actor,
            "price.html",
            {"run": run, "board": board, "rows": p.board_rows(board, code), "code": code},
        )

    @app.get("/analysis/{identifier}")
    async def run_page(request: Request, identifier: str) -> Response:
        actor = actor_of(request)
        if actor is None:
            return to_login()
        if not p.RUN_ID.fullmatch(identifier):
            return bad_id(request, actor)
        run = await load_run(request, actor, identifier)
        if isinstance(run, Response):
            return run
        status = run["status"]
        mode = (run.get("request") or {}).get("mode")
        result = run.get("result") if status in {"completed", "partial"} else None
        context: dict[str, Any] = {
            "run": run,
            "mode": mode,
            "label": p.STATUS_LABELS.get(status, "状态待核查"),
            "result": result,
            "basis": f"请求编号：{run['id']}\n发起时间（上海）：{p.local_time(run.get('created_at'))}\n本次类型：{(run.get('limits') or {}).get('title', '未记录')}",
            "usage": p.usage_text(
                (result or {}).get("usage") or run.get("usage"),
                "financial" if mode == "financial" else mode,
            ),
        }
        if result and mode == "screen":
            context["screen"] = p.screen_view(run)
        elif result and mode == "financial":
            context["reports"] = [p.report_view(r) for r in result.get("reports", [])]
        elif result and "board" in result:
            context["board"] = result["board"]
            context["rows"] = p.board_rows(result["board"])
        return render(request, actor, "run.html", context)

    @app.exception_handler(StarletteHTTPException)
    async def not_found(request: Request, exc: StarletteHTTPException) -> Response:
        return HTMLResponse(
            templates.get_template("message.html").render(
                request=request,
                title="页面不存在" if exc.status_code == 404 else "请求未完成",
                detail="请返回研究首页。",
                back="/",
                actor=None,
                csrf="",
            ),
            status_code=exc.status_code,
        )

    return SecurityMiddleware(app, settings)


def create_app_from_env() -> SecurityMiddleware:
    settings = settings_from_env()
    client = DemoResearchClient() if settings.demo else configured_client()
    return create_app(settings, client)
