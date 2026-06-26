#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from argparse import ArgumentParser
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROBE_GLOB = "*-tushare-capability-probe.md"
PROBE_NAME_RE = re.compile(r"^(?P<run_date>\d{4}-\d{2}-\d{2})-tushare-capability-probe\.md$")


@dataclass(frozen=True)
class ProbeDecision:
    write_gate: str | None
    capability_checks: str | None
    production_decision: str | None
    dependent_jobs: str | None
    close_cross_check: str | None
    parse_errors: list[str]


@dataclass(frozen=True)
class ReadinessStatus:
    daily_ready: bool
    capability_ready: bool
    report_path: Path | None
    reasons: list[str]

    @property
    def cron_ready(self) -> bool:
        return self.daily_ready and self.capability_ready


def _today() -> date:
    return date.today()


def _load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _probe_report_date(report_path: Path) -> date | None:
    match = PROBE_NAME_RE.match(report_path.name)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group("run_date"))
    except ValueError:
        return None


def latest_probe_report() -> Path | None:
    reports: list[tuple[date, Path]] = []
    for report in (PROJECT_ROOT / "docs" / "reviews").glob(PROBE_GLOB):
        report_date = _probe_report_date(report)
        if report_date is None:
            continue
        reports.append((report_date, report))
    if not reports:
        return None
    return max(reports, key=lambda item: item[0])[1]


def _extract_unique_field(text: str, field: str) -> tuple[str | None, str | None]:
    pattern = re.compile(rf"^{re.escape(field)}:\s*(?P<value>[A-Z_]+)\s*$", re.MULTILINE)
    matches = pattern.findall(text)
    if not matches:
        return None, None
    if len(matches) > 1:
        return None, f"{field} appears multiple times in latest Tushare capability probe"
    return matches[0], None


def parse_probe_decision(report_path: Path) -> ProbeDecision:
    text = report_path.read_text(encoding="utf-8")
    parse_errors: list[str] = []
    fields: dict[str, str | None] = {}
    for field in (
        "Write Gate",
        "Capability Checks",
        "Production Decision",
        "Index/Calendar Dependent Jobs",
        "Close cross-check",
    ):
        value, parse_error = _extract_unique_field(text, field)
        fields[field] = value
        if parse_error is not None:
            parse_errors.append(parse_error)
    return ProbeDecision(
        write_gate=fields["Write Gate"],
        capability_checks=fields["Capability Checks"],
        production_decision=fields["Production Decision"],
        dependent_jobs=fields["Index/Calendar Dependent Jobs"],
        close_cross_check=fields["Close cross-check"],
        parse_errors=parse_errors,
    )


def readiness_status(*, require_fresh_report: bool = False) -> ReadinessStatus:
    _load_dotenv()
    reasons: list[str] = []
    if not os.environ.get("TUSHARE_TOKEN"):
        reasons.append("TUSHARE_TOKEN is not configured")

    report = latest_probe_report()
    daily_ready = False
    capability_ready = False
    if report is None:
        reasons.append("no Tushare capability probe report found")
    else:
        report_date = _probe_report_date(report)
        if require_fresh_report and report_date != _today():
            reasons.append(f"latest Tushare capability probe is stale: {_format_path(report)}")
        decision = parse_probe_decision(report)
        required_fields = {
            "Write Gate": decision.write_gate,
            "Capability Checks": decision.capability_checks,
            "Production Decision": decision.production_decision,
            "Index/Calendar Dependent Jobs": decision.dependent_jobs,
            "Close cross-check": decision.close_cross_check,
        }
        missing = [name for name, value in required_fields.items() if value is None]
        if decision.parse_errors:
            reasons.extend(decision.parse_errors)
        if missing:
            reasons.append("latest Tushare capability probe uses legacy readiness format; rerun probe")
        elif not decision.parse_errors:
            if decision.write_gate != "PASS":
                reasons.append(f"Write Gate is {decision.write_gate}")
            if decision.production_decision != "DAILY_WRITES_ALLOWED":
                reasons.append(f"Production Decision is {decision.production_decision}")
            if decision.close_cross_check != "PASS":
                reasons.append(f"Close cross-check is {decision.close_cross_check}")
            daily_ready = (
                bool(os.environ.get("TUSHARE_TOKEN"))
                and not (require_fresh_report and report_date != _today())
                and decision.write_gate == "PASS"
                and decision.production_decision == "DAILY_WRITES_ALLOWED"
                and decision.close_cross_check == "PASS"
            )
            if decision.capability_checks != "PASS":
                reasons.append(f"Capability Checks is {decision.capability_checks}")
            if decision.dependent_jobs != "ALLOWED":
                reasons.append(f"Index/Calendar Dependent Jobs is {decision.dependent_jobs}")
            capability_ready = daily_ready and decision.capability_checks == "PASS" and decision.dependent_jobs == "ALLOWED"

    return ReadinessStatus(daily_ready, capability_ready, report, reasons)


def _format_path(path: Path | None) -> str:
    if path is None:
        return "none"
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _print_details(status: ReadinessStatus) -> None:
    print(f"- daily writes: {'READY' if status.daily_ready else 'HOLD'}")
    print(f"- index/calendar-dependent jobs: {'READY' if status.capability_ready else 'HOLD'}")
    print(f"- latest report: {_format_path(status.report_path)}")
    for reason in status.reasons:
        print(f"- {reason}")


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Check market data readiness from the latest Tushare probe report.")
    parser.add_argument(
        "--scope",
        choices=("cron", "daily"),
        default="cron",
        help="cron requires daily and index/calendar readiness; daily checks staged daily writes only.",
    )
    args = parser.parse_args(argv)

    status = readiness_status(require_fresh_report=args.scope == "cron")
    if args.scope == "daily":
        if status.daily_ready:
            print("READY_DAILY: daily market data writes can be considered for staged recovery")
            _print_details(status)
            return 0
        print("HOLD_DAILY: daily market data writes are not cleared")
        _print_details(status)
        return 1

    if status.cron_ready:
        print("READY_CRON: daily and index/calendar-dependent market data jobs can be considered for cron recovery")
        _print_details(status)
        return 0
    print("HOLD_CRON: market data cron recovery is not cleared")
    _print_details(status)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
