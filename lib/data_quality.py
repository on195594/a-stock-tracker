from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FieldRequirement(StrEnum):
    REQUIRED = "required"
    DEGRADABLE = "degradable"
    DERIVED = "derived"
    FIXED = "fixed"


class FieldStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    FALLBACK = "fallback"
    STALE = "stale"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class DataQualityField:
    name: str
    requirement: FieldRequirement
    status: FieldStatus
    source: str
    reason: str = ""


@dataclass(frozen=True)
class DataQualityResult:
    code: str
    fields: tuple[DataQualityField, ...]
    missing_required: tuple[str, ...] = field(default_factory=tuple)
    fallback_fields: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_acceptable(self) -> bool:
        return not self.missing_required

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "is_acceptable": self.is_acceptable,
            "missing_required": list(self.missing_required),
            "fallback_fields": list(self.fallback_fields),
            "fields": [item.__dict__ for item in self.fields],
        }


_REQUIRED_FIELDS = ("roe_3y_avg", "net_profit_growth", "debt_ratio", "gross_margin")
_SUPPORT_FIELDS = ("report_period", "price_at_score")
_DERIVED_SUPPORT = ("bps", "pb_hist_monthly")
_PB_DAILY_DEPENDENCIES = ("price_at_score", "bps", "pb_hist_monthly")


def evaluate_data_quality(
    code: str,
    data: dict[str, Any],
    fallback_sources: dict[str, str] | None = None,
    industry: str = "",
) -> DataQualityResult:
    fallback_sources = fallback_sources or {}
    fields: list[DataQualityField] = []

    financial_industry = any(
        keyword in industry
        for keyword in ("银行", "保险", "证券", "券商", "金融", "信托", "期货")
    )

    def add(name: str, requirement: FieldRequirement) -> None:
        if name == "gross_margin" and financial_industry and data.get(name) is None:
            fields.append(
                DataQualityField(
                    name,
                    requirement,
                    FieldStatus.NOT_APPLICABLE,
                    name,
                    "financial_industry",
                )
            )
            return
        if name in fallback_sources:
            status = FieldStatus.FALLBACK
            reason = fallback_sources[name]
        elif data.get(name) is None:
            status = FieldStatus.MISSING
            reason = "missing"
        else:
            status = FieldStatus.OK
            reason = ""
        fields.append(DataQualityField(name, requirement, status, source=name, reason=reason))

    def add_pb_percentile() -> None:
        name = "pb_percentile_10y"
        if name in fallback_sources:
            fields.append(
                DataQualityField(
                    name,
                    FieldRequirement.REQUIRED,
                    FieldStatus.FALLBACK,
                    name,
                    fallback_sources[name],
                )
            )
            return
        if data.get(name) is None:
            fields.append(DataQualityField(name, FieldRequirement.REQUIRED, FieldStatus.MISSING, name, "missing"))
            return
        missing_inputs = [dep for dep in _PB_DAILY_DEPENDENCIES if data.get(dep) is None]
        if len(data.get("pb_hist_monthly") or []) < 12:
            missing_inputs.append("pb_hist_monthly")
        if missing_inputs:
            fields.append(
                DataQualityField(
                    name,
                    FieldRequirement.REQUIRED,
                    FieldStatus.STALE,
                    name,
                    "cached_without_daily_inputs:" + ",".join(missing_inputs),
                )
            )
            return
        fields.append(DataQualityField(name, FieldRequirement.REQUIRED, FieldStatus.OK, name, "daily_computable"))

    for name in _REQUIRED_FIELDS:
        add(name, FieldRequirement.REQUIRED)
    add_pb_percentile()
    for name in _SUPPORT_FIELDS:
        add(name, FieldRequirement.DEGRADABLE)
    for name in _DERIVED_SUPPORT:
        add(name, FieldRequirement.DERIVED)

    missing_required = tuple(
        item.name for item in fields
        if item.requirement == FieldRequirement.REQUIRED
        and item.status == FieldStatus.MISSING
    )
    fallback_fields = tuple(item.name for item in fields if item.status == FieldStatus.FALLBACK)
    return DataQualityResult(code=code, fields=tuple(fields), missing_required=missing_required, fallback_fields=fallback_fields)
