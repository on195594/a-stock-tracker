#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_TAIL_BYTES = 10 * 1024
READINESS_TIMEOUT_S = 120
ACCURACY_REPORT_TIMEOUT_S = 300
TELEGRAM_TIMEOUT_S = 10

STATUS_ORDER = {"OK": 0, "WARN": 1, "FAIL": 2}
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
FAILURE_RE = re.compile(
    r"(Traceback|ERROR|FAILED(?!\s*=\s*0(?:\b|$))|"
    r"失败(?!\s*(?:[:=：]\s*)?0(?:\s*(?:只|个|项|条))?(?:\s|[,，;；.)）]|$))|"
    r"崩溃|database is locked)",
    re.IGNORECASE,
)
HARD_FAILURE_RE = re.compile(
    r"(Traceback|ERROR|FAILED(?!\s*=\s*0(?:\b|$))|"
    r"失败\s*(?:[:=：]\s*)?[1-9]\d*(?:\s*(?:只|个|项|条))?|"
    r"崩溃|database is locked)",
    re.IGNORECASE,
)
RESTRICTIVE_CONCLUSION_MARKERS = (
    "仍不要启用生产写入",
    "暂不进入 Phase 6 生产化",
    "暂不进入生产化",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def _max_status(statuses: list[str]) -> str:
    return max(statuses, key=lambda status: STATUS_ORDER[status])


def _load_dotenv(project_root: Path) -> None:
    env_path = project_root / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _tail_text(path: Path, max_bytes: int = LOG_TAIL_BYTES) -> str:
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(-max_bytes, os.SEEK_END)
        return fh.read().decode("utf-8", errors="replace")


def _latest_dated_lines(text: str) -> tuple[list[str], bool]:
    lines = text.splitlines()
    dates = [match.group(0) for line in lines for match in [DATE_RE.search(line)] if match]
    if not dates:
        return lines, False
    latest = max(dates)
    return [line for line in lines if latest in line], True


def _failure_severity(line: str) -> str | None:
    """Classify explicit failures while keeping degraded log warnings non-fatal."""
    if FAILURE_RE.search(line) is None:
        return None
    if HARD_FAILURE_RE.search(line) is not None:
        return "FAIL"
    if re.search(r"\bWARNING\b", line, re.IGNORECASE):
        return "WARN"
    return "FAIL"


def check_log(log_name: str, *, max_age: timedelta, now: datetime, project_root: Path) -> CheckResult:
    path = project_root / "logs" / log_name
    if not path.exists():
        return CheckResult(log_name, "WARN", f"{log_name} missing")

    age = now - datetime.fromtimestamp(path.stat().st_mtime)
    statuses = ["OK"]
    details: list[str] = [f"mtime_age={age.days}d"]
    if age > max_age:
        statuses.append("WARN")
        details.append(f"stale>{max_age.days}d")

    tail = _tail_text(path)
    latest_lines, has_date = _latest_dated_lines(tail)
    failure_hits = [(severity, line.strip()) for line in latest_lines if (severity := _failure_severity(line))]
    hard_failures = [line for severity, line in failure_hits if severity == "FAIL"]
    degraded_warnings = [line for severity, line in failure_hits if severity == "WARN"]
    if hard_failures:
        statuses.append("FAIL" if has_date else "WARN")
        details.append(f"failure_marker={hard_failures[-1][:160]}")
    elif degraded_warnings:
        statuses.append("WARN")
        details.append(f"warning_marker={degraded_warnings[-1][:160]}")
    elif not has_date and tail.strip():
        statuses.append("WARN")
        details.append("latest_date_unrecognized")
    else:
        details.append("no_failure_marker")

    return CheckResult(log_name, _max_status(statuses), "; ".join(details))


def _run_command(args: list[str], *, timeout: int, project_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=project_root,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def check_readiness(project_root: Path) -> CheckResult:
    try:
        result = _run_command(
            [
                sys.executable,
                "scripts/check_market_data_readiness.py",
                "--scope",
                "cron",
                "--allow-stale-days",
                "9",
            ],
            timeout=READINESS_TIMEOUT_S,
            project_root=project_root,
        )
    except subprocess.TimeoutExpired:
        return CheckResult("readiness", "FAIL", "timeout")

    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode == 0 and "READY_CRON" in output:
        return CheckResult("readiness", "OK", "READY_CRON")
    if "HOLD_CRON" in output:
        return CheckResult("readiness", "WARN", _first_matching_line(output, "HOLD_CRON"))
    return CheckResult("readiness", "FAIL", _shorten(output or f"exit={result.returncode}"))


def _first_matching_line(text: str, marker: str) -> str:
    for line in text.splitlines():
        if marker in line:
            return line.strip()
    return marker


def check_accuracy_report(project_root: Path) -> CheckResult:
    try:
        result = _run_command(
            [sys.executable, "pipeline.py", "accuracy-report"],
            timeout=ACCURACY_REPORT_TIMEOUT_S,
            project_root=project_root,
        )
    except subprocess.TimeoutExpired:
        return CheckResult("accuracy-report", "FAIL", "timeout")

    output = result.stdout.strip()
    if not output:
        report_path = project_root / "artifacts" / "reports" / "accuracy-report.txt"
        if report_path.exists():
            output = report_path.read_text(encoding="utf-8")

    if result.returncode != 0:
        combined = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        return CheckResult("accuracy-report", "FAIL", _shorten(combined or f"exit={result.returncode}"))

    summary = extract_phase6_summary(output)
    if summary.status == "FAIL":
        return summary
    return summary


def extract_phase6_summary(text: str) -> CheckResult:
    if "Phase 6 readiness" not in text:
        return CheckResult("accuracy-report", "FAIL", "missing Phase 6 readiness section")

    blockers = _first_prefixed_line(text, "Phase 6 生产化阻塞项：")
    next_action = _first_prefixed_line(text, "Phase 6 下一步：")
    conclusion = _first_prefixed_line(text, "结论：", after_marker="Phase 6 readiness")

    missing = [
        name
        for name, value in (
            ("blockers", blockers),
            ("next_action", next_action),
            ("conclusion", conclusion),
        )
        if value is None
    ]
    if missing:
        return CheckResult("accuracy-report", "FAIL", "missing " + ",".join(missing))

    assert blockers is not None
    assert next_action is not None
    assert conclusion is not None

    status = "OK"
    if "无" not in blockers:
        status = "WARN"
    if not any(marker in conclusion for marker in RESTRICTIVE_CONCLUSION_MARKERS):
        status = "WARN"
        conclusion += " | production_guard=missing_restrictive_marker"

    detail = " | ".join([blockers, next_action, conclusion])
    return CheckResult("accuracy-report", status, detail)


def _first_prefixed_line(text: str, prefix: str, *, after_marker: str | None = None) -> str | None:
    if after_marker is not None:
        marker_index = text.find(after_marker)
        if marker_index >= 0:
            text = text[marker_index:]
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.strip()
    return None


def _shorten(text: str, limit: int = 300) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def build_summary(results: list[CheckResult], *, now: datetime, project_root: Path) -> tuple[str, str]:
    overall = _max_status([result.status for result in results])
    lines = [
        "Phase 6 weekly PM loop",
        f"run_at: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"status: {overall}",
        "",
    ]
    for result in results:
        lines.append(f"- {result.name}: {result.status} - {result.detail}")
    lines.extend(
        [
            "",
            f"logs: {project_root / 'logs' / 'weekly-pm-loop.log'}",
            f"summary: {project_root / 'logs' / 'weekly-pm-loop-summary.txt'}",
        ]
    )
    return overall, "\n".join(lines)


def send_telegram(text: str, *, project_root: Path) -> str:
    _load_dotenv(project_root)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return "telegram_skipped"

    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=TELEGRAM_TIMEOUT_S) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"Telegram HTTP {resp.status}")
    return "telegram_sent"


def run_loop(*, project_root: Path, now: datetime) -> tuple[str, str]:
    results = [
        check_log("weekly.log", max_age=timedelta(days=9), now=now, project_root=project_root),
        check_log("daily.log", max_age=timedelta(days=4), now=now, project_root=project_root),
        check_log("outcome.log", max_age=timedelta(days=4), now=now, project_root=project_root),
        check_readiness(project_root),
        check_accuracy_report(project_root),
    ]
    return build_summary(results, now=now, project_root=project_root)


def write_summary(summary: str, *, project_root: Path) -> None:
    logs_dir = project_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "weekly-pm-loop-summary.txt").write_text(summary + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 6 weekly PM loop and send a Telegram summary.")
    parser.add_argument("--dry-run", action="store_true", help="Print the summary to stdout.")
    parser.add_argument("--no-telegram", action="store_true", help="Do not send Telegram notification.")
    args = parser.parse_args(argv)

    now = datetime.now()
    overall, summary = run_loop(project_root=PROJECT_ROOT, now=now)
    write_summary(summary, project_root=PROJECT_ROOT)

    if args.dry_run:
        print(summary)

    telegram_status = "telegram_disabled"
    if not args.no_telegram:
        try:
            telegram_status = send_telegram(summary, project_root=PROJECT_ROOT)
        except Exception as exc:
            print(f"telegram_failed: {exc}", file=sys.stderr)
            return 1

    print(telegram_status)
    if overall == "OK":
        return 0
    if telegram_status in {"telegram_sent"}:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
