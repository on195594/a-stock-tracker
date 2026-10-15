"""Shared research API contracts for receivers and clients."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Mode = Literal["screen", "financial", "swing"]
ErrorCode = Literal[
    "invalid_request",
    "unauthorized",
    "not_found",
    "busy",
    "too_large",
    "acceptance_unknown",
    "service_error",
    "quote_changed",
    "quote_expired",
    "channel_limit",
]


class ModelRef(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    provider: str
    model: str


class ModelBudget(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    calls: int
    local_timeout_seconds: int | None = None
    output_token_hard_limit: int | None = None
    cost_hard_limit: float | None = None
    timeout_guarantees_billing_stop: bool | None = None


class Quote(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    title: str
    requests: int
    seconds: int
    reports: int
    prefilter: dict[str, Any] | None = None
    model: ModelRef | None = None
    model_budget: ModelBudget | None = None


class ModeInfo(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    title: str | None = None
    requests: int | None = None
    seconds: int | None = None
    reports: int | None = None
    prefilter: dict[str, Any] | None = None
    available: bool | None = None


class ServiceInfo(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    demo: bool | None = None
    modes: dict[str, ModeInfo] | None = None
    model: ModelRef | None = None
    channel: str | None = None
    channel_remaining: int | None = None
    busy: bool | None = None
    rule: str | None = None


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    mode: Mode
    codes: list[str]
    confirmed: Literal[True]
    # Preserve the confirmed offer verbatim for the receiver's equality check.
    quote: dict[Any, Any]
    from_: str | None = Field(default=None, alias="from")
    resume_from: str | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_incoming(cls, value: Any) -> Any:
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
        if (
            not isinstance(mode, str)
            or mode not in {"screen", "financial", "swing"}
            or value["confirmed"] is not True
        ):
            raise ValueError("必须明确确认研究类型与预算")
        if not isinstance(codes, list) or any(
            not isinstance(c, str) or not re.fullmatch(r"(?:60\d{4}\.SH|00\d{4}\.SZ)", c)
            for c in codes
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


class StoredRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Historical request fields are data, not new execution authorization.
    id: Any = None
    mode: Any = None
    codes: Any = None
    confirmed: Any = None
    quote: Any = None
    from_: Any = Field(default=None, alias="from")
    resume_from: Any = None


class RunState(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    id: str
    status: str
    channel: str | None = None
    request: StoredRequest | None = None
    result: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    demo: bool | None = None
    created_at: str | None = None
    finished_at: str | None = None
    limits: dict[str, Any] | None = None
    message: str | None = None
    usage: dict[str, Any] | None = None
    continued_from: str | None = None
    continued_by: str | None = None
    reused_from: str | None = None
    can_continue: bool | None = None
    continuation_quote: Quote | None = None
    continuations: int | None = None
    reused: bool | None = None


class RunList(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    runs: list[RunState]


class Fact(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    id: str
    claim: str
    title: str
    page: int
    period: str
    announced: str
    quote: str
    url: str


class Section(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    text: str
    facts: list[str]


class Quality(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    generated: int | None = None
    repair: str | None = None
    dropped: list[dict[str, Any]] | None = None
    unformed: dict[str, str] | None = None
    passed: int | None = None


class ReportView(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    code: str
    status: str
    evidence_review: str | None = None
    rule: str | None = None
    quality: Quality | None = None
    documents: list[dict[str, str | None]] = Field(default_factory=list)
    clues: dict[str, Any] = Field(default_factory=dict)
    analysis: dict[str, Section] = Field(default_factory=dict)
    facts: list[Fact] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    omitted_pages: int | None = None
    diagnostic: dict[str, Any] | None = None
    error: str | None = None


class ReportSummary(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    run_id: str
    code: str
    finished_at: str | None = None
    status: str
    rule: str | None = None
    documents: list[dict[str, str | None]] = Field(default_factory=list)
    omitted_pages: int | None = None
    gap_count: int
    quality: Quality | None = None


class ReportList(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    code: str
    reports: list[ReportSummary]


class PriceReference(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    code: str
    status: str
    as_of: str | None = None
    expires: str | None = None
    expired: bool | None = None
    levels: dict[str, Any] | None = None
    reasons: list[str] | None = None


class RankingPage(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    run_id: str
    total: int
    offset: int
    items: list[dict[str, Any]]
    next_cursor: str | None
    is_last: bool
    prefilter: dict[str, Any] | None
    coverage: dict[str, Any]
    industries: list[str]


class ErrorBody(BaseModel):
    model_config = ConfigDict(strict=True)

    detail: str
    code: ErrorCode
