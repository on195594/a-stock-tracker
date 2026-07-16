#!/usr/bin/env python3
"""Export the M4 frame using three AKShare calls and a direct official SZSE HTTPS snapshot."""

from __future__ import annotations

import io
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import pandas as pd
import requests  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.export_m4_sampling_frame_akshare import (  # noqa: E402
    MAX_RESPONSE_BYTES,
    AkshareRunner,
    CallResult,
    ExportError,
    HttpCapture,
    Runner,
)

AUTHORIZATION_ID = "qualitative-v2-m4-akshare-szse-https-frame-export-2026-07-15-01"
LEGACY_EXPORT_DISABLED = "legacy M4 SZSE exporter is disabled; use the authorized capture-first 'capture' command"
AUTHORIZED_AKSHARE_FUNCTIONS = frozenset({"index_component_sw", "stock_zh_a_spot_em", "stock_sse_summary"})
SZSE_ENDPOINT = "https://www.szse.cn/api/report/ShowReport"
SZSE_REFERER = "https://www.szse.cn/market/overview/index.html"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "milestone-004" / "incoming"
BASE_EXPORTER_PATH = PROJECT_ROOT / "scripts" / "export_m4_sampling_frame_akshare.py"
SessionFactory = Callable[[], Any]


class RestrictedAkshareRunner:
    """Expose only the three AKShare functions authorized for this execution."""

    def __init__(self, delegate: Runner | None = None) -> None:
        self._delegate = delegate or AkshareRunner()
        self.version = self._delegate.version

    def call(self, function_name: str, arguments: Mapping[str, str]) -> CallResult:
        if function_name not in AUTHORIZED_AKSHARE_FUNCTIONS:
            raise ExportError(f"AKShare function is not authorized by the current execution: {function_name}")
        return self._delegate.call(function_name, arguments)


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def _positive_decimal(value: object, *, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ExportError(f"SZSE returned an invalid {label}")
    text = str(value).strip().replace(",", "")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ExportError(f"SZSE returned an invalid {label}") from exc
    if not number.is_finite() or number <= 0:
        raise ExportError(f"SZSE returned a non-positive {label}")
    return number


def _parse_szse_xlsx(raw: bytes) -> pd.DataFrame:
    if not raw.startswith(b"PK\x03\x04"):
        raise ExportError("SZSE HTTPS interface did not return an XLSX document")
    try:
        dataframe = pd.read_excel(io.BytesIO(raw), engine="openpyxl")
    except Exception as exc:
        raise ExportError(f"SZSE HTTPS XLSX parse failure: {type(exc).__name__}") from exc
    if len(dataframe.columns) != 5 or dataframe.empty:
        raise ExportError("SZSE HTTPS XLSX schema or row count drift")
    dataframe.columns = ["证券类别", "数量", "成交金额", "总市值", "流通市值"]
    normalized_rows: list[dict[str, object]] = []
    for raw_row in dataframe.to_dict(orient="records"):
        category = str(raw_row["证券类别"]).strip()
        if not category or category.lower() == "nan":
            raise ExportError("SZSE HTTPS XLSX contains an empty security category")
        row: dict[str, object] = {"证券类别": category}
        for field in ("数量", "成交金额", "总市值", "流通市值"):
            value = raw_row[field]
            if field in {"数量", "总市值"} and "A股" in category:
                row[field] = str(_positive_decimal(value, label=f"{category}/{field}"))
            else:
                row[field] = str(value).strip().replace(",", "")
        normalized_rows.append(row)
    if not any("A股" in str(row["证券类别"]) for row in normalized_rows):
        raise ExportError("SZSE HTTPS XLSX omitted A-share rows")
    return pd.DataFrame(normalized_rows, columns=["证券类别", "数量", "成交金额", "总市值", "流通市值"])


class SzseHttpsSource:
    """Fetch one date-bound XLSX from the official SZSE HTTPS interface."""

    def __init__(self, *, session_factory: SessionFactory = _default_session_factory) -> None:
        self._session_factory = session_factory

    def __call__(self, sampling_date: date) -> CallResult:
        raise ExportError(LEGACY_EXPORT_DISABLED)
        params = {
            "SHOWTYPE": "xlsx",
            "CATALOGID": "1803_sczm",
            "TABKEY": "tab1",
            "txtQueryDate": sampling_date.isoformat(),
        }
        headers = {
            "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/octet-stream",
            "Referer": SZSE_REFERER,
            "User-Agent": "a-stock-tracker-m4-audit-static-export/1",
        }
        session = self._session_factory()
        try:
            response = session.get(
                SZSE_ENDPOINT,
                params=params,
                headers=headers,
                timeout=(10, 30),
                verify=True,
                allow_redirects=False,
                stream=True,
            )
            status = int(getattr(response, "status_code", 0))
            response_url = str(getattr(response, "url", ""))
            parts = urlsplit(response_url)
            if parts.scheme != "https" or parts.hostname != "www.szse.cn":
                response.close()
                raise ExportError("SZSE response escaped its approved HTTPS origin")
            if not 200 <= status < 300:
                response.close()
                raise ExportError(f"SZSE HTTPS interface returned status={status}")
            chunks: list[bytes] = []
            byte_count = 0
            try:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    byte_count += len(chunk)
                    if byte_count > MAX_RESPONSE_BYTES:
                        raise ExportError("SZSE HTTPS response exceeds 32 MiB")
                    chunks.append(bytes(chunk))
            finally:
                response.close()
            raw = b"".join(chunks)
            content_type = (
                str(getattr(response, "headers", {}).get("Content-Type", "application/octet-stream"))
                .split(";", 1)[0]
                .strip()
                .lower()
            )
        except ExportError:
            raise
        except (OSError, requests.RequestException) as exc:
            raise ExportError(f"SZSE HTTPS transport failure: {type(exc).__name__}") from exc
        finally:
            session.close()
        dataframe = _parse_szse_xlsx(raw)
        capture = HttpCapture(
            SZSE_ENDPOINT,
            params,
            response_url,
            status,
            content_type,
            raw,
        )
        return CallResult(
            "szse_https_market_overview",
            {"date": sampling_date.isoformat()},
            dataframe,
            (capture,),
        )


def main() -> int:
    print(f"M4_AKSHARE_SZSE_HTTPS_FRAME_EXPORT_BLOCKED: {LEGACY_EXPORT_DISABLED}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
