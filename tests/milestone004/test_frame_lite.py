from __future__ import annotations

import csv
import json
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pytest

from scripts import build_m4_frame_lite as lite


TOKEN = "opaque-test-token-that-must-never-leak"
TRADE_DATE = "20260717"


@dataclass
class Dataset:
    classification: list[list[object]]
    members: dict[str, list[list[object]]]
    stocks: dict[str, list[list[object]]]
    daily: list[list[object]]


def make_dataset(*, per_industry: int = 3, reverse: bool = False) -> Dataset:
    classification = [
        [code, name, None, "L1", code.removesuffix(".SI"), "Y", "SW2021"]
        for code, name in lite.SW2021_INDUSTRIES.items()
    ]
    members: dict[str, list[list[object]]] = {code: [] for code in lite.SW2021_INDUSTRIES}
    stocks: dict[str, list[list[object]]] = {"SSE": [], "SZSE": []}
    daily: list[list[object]] = []
    number = 600000
    market_cap = 1000
    for industry_code, industry_name in lite.SW2021_INDUSTRIES.items():
        for _ in range(per_industry):
            ts_code = f"{number:06d}.SH"
            name = f"公司{number}"
            members[industry_code].append(
                [industry_code, industry_name, None, None, None, None, ts_code, name, "20200101", None, "Y"]
            )
            stocks["SSE"].append([ts_code, name, "主板", "SSE", "CNY", "L", "20200101"])
            daily.append([ts_code, TRADE_DATE, market_cap])
            number += 1
            market_cap += 1
    if reverse:
        classification.reverse()
        for rows in members.values():
            rows.reverse()
        stocks["SSE"].reverse()
        daily.reverse()
    return Dataset(classification, members, stocks, daily)


def response_bytes(fields: tuple[str, ...], rows: list[list[object]], **data_overrides: object) -> bytes:
    data: dict[str, object] = {
        "fields": list(fields),
        "items": rows,
        "count": len(rows),
        "has_more": False,
    }
    data.update(data_overrides)
    return json.dumps({"code": 0, "msg": None, "data": data}, ensure_ascii=False).encode()


class FakeTransport:
    def __init__(
        self,
        dataset: Dataset,
        override: Callable[[lite.CallSpec, lite.TransportResponse], lite.TransportResponse] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.dataset = dataset
        self.override = override
        self.clock = clock
        self.calls: list[lite.CallSpec] = []
        self.started: list[float] = []

    def send(self, spec: lite.CallSpec, token: str) -> lite.TransportResponse:
        assert token == TOKEN
        self.calls.append(spec)
        if self.clock is not None:
            self.started.append(self.clock())
        if spec.api_name == "index_classify":
            body = response_bytes(spec.fields, self.dataset.classification)
        elif spec.api_name == "index_member_all":
            body = response_bytes(spec.fields, self.dataset.members[spec.params["l1_code"]])
        elif spec.api_name == "stock_basic":
            body = response_bytes(spec.fields, self.dataset.stocks[spec.params["exchange"]])
        elif spec.api_name == "daily_basic":
            body = response_bytes(spec.fields, self.dataset.daily)
        else:  # pragma: no cover - fixed call matrix
            raise AssertionError(spec.api_name)
        response = lite.TransportResponse(200, body)
        return self.override(spec, response) if self.override else response


class Clock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def run_probe(tmp_path: Path, transport: FakeTransport, **kwargs: object) -> Path:
    return lite.run_probe(
        TRADE_DATE,
        token=TOKEN,
        transport=transport,
        artifact_root=tmp_path / "artifacts",
        min_interval_seconds=0,
        now=datetime(2026, 7, 18, 12, 0, tzinfo=lite.SHANGHAI),
        **kwargs,
    )


def run_build(run_root: Path, transport: FakeTransport, **kwargs: object) -> Path:
    return lite.run_build(
        run_root,
        token=TOKEN,
        transport=transport,
        min_interval_seconds=0,
        now=datetime(2026, 7, 18, 12, 5, tzinfo=lite.SHANGHAI),
        **kwargs,
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_probe_and_build_use_exactly_35_calls_and_publish_12_cells(tmp_path: Path) -> None:
    transport = FakeTransport(make_dataset())
    run_root = run_probe(tmp_path, transport)

    assert len(transport.calls) == 5
    assert [call.api_name for call in transport.calls] == [
        "index_classify",
        "index_member_all",
        "stock_basic",
        "stock_basic",
        "daily_basic",
    ]
    assert stat.S_IMODE(run_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((run_root / "raw").stat().st_mode) == 0o700

    derived = run_build(run_root, transport)
    assert len(transport.calls) == 35
    assert len([call for call in transport.calls if call.api_name == "index_member_all"]) == 31
    assert len(list((run_root / "raw").iterdir())) == 35
    assert not (run_root / "derived.tmp").exists()

    frame = read_csv(derived / "frame.csv")
    sample = read_csv(derived / "sample.csv")
    excluded = read_csv(derived / "excluded.csv")
    assert len(frame) == 93
    assert len(sample) == len({row["ts_code"] for row in sample}) == 36
    assert excluded == []
    cells: dict[tuple[str, str], int] = {}
    for row in sample:
        key = (row["super_stratum"], row["market_cap_stratum"])
        cells[key] = cells.get(key, 0) + 1
    assert len(cells) == 12
    assert set(cells.values()) == {3}

    summary = json.loads((run_root / "run-summary.json").read_text())
    assert summary["probe_pass"] is summary["build_pass"] is True
    assert summary["failed_call"] is None
    assert len(summary["calls"]) == 35
    assert summary["sample_rows"] == 36
    assert set(summary["derived_sha256"]) == {"frame.csv", "sample.csv", "excluded.csv"}
    assert TOKEN not in "".join(path.read_text(errors="ignore") for path in run_root.rglob("*") if path.is_file())


def test_rate_limit_spans_probe_and_build_boundary(tmp_path: Path) -> None:
    clock = Clock()
    transport = FakeTransport(make_dataset(), clock=clock.monotonic)
    run_root = lite.run_probe(
        TRADE_DATE,
        token=TOKEN,
        transport=transport,
        artifact_root=tmp_path,
        min_interval_seconds=1.3,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )
    lite.run_build(
        run_root,
        token=TOKEN,
        transport=transport,
        min_interval_seconds=1.3,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )

    assert len(transport.started) == 35
    assert all(right - left >= 1.3 - 1e-9 for left, right in zip(transport.started, transport.started[1:]))


@pytest.mark.parametrize("mutation", ["date", "failed", "incomplete", "fields"])
def test_build_rejects_non_reusable_probe_without_network(tmp_path: Path, mutation: str) -> None:
    probe_transport = FakeTransport(make_dataset())
    run_root = run_probe(tmp_path, probe_transport)
    summary_path = run_root / "run-summary.json"
    summary = json.loads(summary_path.read_text())
    if mutation == "date":
        summary["trade_date"] = "20260716"
    elif mutation == "failed":
        summary["probe_pass"] = False
        summary["failed_call"] = {"phase": "probe", "reason": "failed"}
    elif mutation == "incomplete":
        summary["calls"].pop()
    else:
        summary["calls"][0]["fields"] = ["different"]
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    build_transport = FakeTransport(make_dataset())

    with pytest.raises(lite.LiteError, match="build rejected probe"):
        run_build(run_root, build_transport)

    assert build_transport.calls == []
    assert not (run_root / "derived").exists()


def test_token_loading_prefers_environment_then_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("TUSHARE_TOKEN='dotenv-token'\n", encoding="utf-8")
    monkeypatch.setenv("TUSHARE_TOKEN", "environment-token")
    assert lite._load_token(tmp_path) == "environment-token"
    monkeypatch.delenv("TUSHARE_TOKEN")
    assert lite._load_token(tmp_path) == "dotenv-token"


def test_transport_exception_and_token_echo_never_persist_token(tmp_path: Path) -> None:
    class SecretFailureTransport:
        def send(self, spec: lite.CallSpec, token: str) -> lite.TransportResponse:
            raise RuntimeError(token)

    with pytest.raises(lite.LiteError, match="transport failure"):
        lite.run_probe(
            TRADE_DATE,
            token=TOKEN,
            transport=SecretFailureTransport(),
            artifact_root=tmp_path / "exception",
            min_interval_seconds=0,
        )
    assert TOKEN not in "".join(
        path.read_text(errors="ignore") for path in (tmp_path / "exception").rglob("*") if path.is_file()
    )

    def echo(_spec: lite.CallSpec, response: lite.TransportResponse) -> lite.TransportResponse:
        return lite.TransportResponse(response.status, response.body + TOKEN.encode())

    with pytest.raises(lite.LiteError, match="credential echo"):
        run_probe(tmp_path / "echo", FakeTransport(make_dataset(), echo))
    echo_run = next((tmp_path / "echo" / "artifacts").iterdir())
    assert list((echo_run / "raw").iterdir()) == []
    assert TOKEN not in (echo_run / "run-summary.json").read_text()


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("provider", "provider code"),
        ("http", "HTTP status"),
        ("oversize", "32 MiB"),
        ("count", "count differs"),
        ("zero_without_has_more", "count differs"),
        ("has_more", "paginated"),
        ("fields", "fields differ"),
        ("width", "row width"),
    ],
)
def test_response_integrity_failures_stop_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, message: str
) -> None:
    def override(spec: lite.CallSpec, response: lite.TransportResponse) -> lite.TransportResponse:
        if case == "provider":
            return lite.TransportResponse(200, json.dumps({"code": 40203, "data": None}).encode())
        if case == "http":
            return lite.TransportResponse(503, b"unavailable")
        if case == "oversize":
            return lite.TransportResponse(200, b"x" * 65)
        value = json.loads(response.body)
        if case == "count":
            value["data"]["count"] += 1
        elif case == "zero_without_has_more":
            value["data"]["count"] = 0
            value["data"].pop("has_more")
        elif case == "has_more":
            value["data"]["has_more"] = True
        elif case == "fields":
            value["data"]["fields"][0] = "wrong"
        elif case == "width":
            value["data"]["items"][0].pop()
        return lite.TransportResponse(200, json.dumps(value).encode())

    if case == "oversize":
        monkeypatch.setattr(lite, "MAX_RESPONSE_BYTES", 64)
    with pytest.raises(lite.LiteError, match=message):
        run_probe(tmp_path, FakeTransport(make_dataset(), override))


def test_zero_count_sentinel_is_accepted_and_recorded_for_nonempty_single_pages(tmp_path: Path) -> None:
    def zero_count(_spec: lite.CallSpec, response: lite.TransportResponse) -> lite.TransportResponse:
        value = json.loads(response.body)
        value["data"]["count"] = 0
        return lite.TransportResponse(200, json.dumps(value).encode())

    transport = FakeTransport(make_dataset(), zero_count)
    run_root = run_probe(tmp_path, transport)
    derived = run_build(run_root, transport)
    summary = json.loads((run_root / "run-summary.json").read_text())

    assert derived.is_dir()
    assert summary["probe_pass"] is summary["build_pass"] is True
    assert len(summary["response_anomalies"]) == 34
    assert {item["kind"] for item in summary["response_anomalies"]} == {"unknown_zero_count_sentinel"}
    assert summary["calls"][0]["provider_count"] == 0
    assert summary["calls"][0]["count_mode"] == "unknown_zero_sentinel"


def test_classification_must_match_all_31_codes_and_names(tmp_path: Path) -> None:
    dataset = make_dataset()
    dataset.classification[0][1] = "漂移行业"
    with pytest.raises(lite.LiteError, match="classification differs"):
        run_probe(tmp_path, FakeTransport(dataset))


@pytest.mark.parametrize("case", ["l1", "future", "out_date", "is_new", "empty", "limit"])
def test_member_contract_failures_stop_build(tmp_path: Path, case: str) -> None:
    dataset = make_dataset()
    target = tuple(lite.SW2021_INDUSTRIES)[1]
    row = dataset.members[target][0]
    if case == "l1":
        row[1] = "漂移行业"
    elif case == "future":
        row[8] = "20260718"
    elif case == "out_date":
        row[9] = "20260717"
    elif case == "is_new":
        row[10] = "N"
    elif case == "empty":
        dataset.members[target] = []
    else:
        dataset.members[target] = [row[:] for _ in range(2000)]
    transport = FakeTransport(dataset)
    run_root = run_probe(tmp_path, transport)

    with pytest.raises(lite.LiteError, match="build failed"):
        run_build(run_root, transport)

    assert not (run_root / "derived").exists()


def test_cross_industry_duplicate_is_batch_failure(tmp_path: Path) -> None:
    dataset = make_dataset()
    codes = tuple(lite.SW2021_INDUSTRIES)
    duplicate = dataset.members[codes[0]][0][:]
    duplicate[0] = codes[1]
    duplicate[1] = lite.SW2021_INDUSTRIES[codes[1]]
    dataset.members[codes[1]].append(duplicate)
    transport = FakeTransport(dataset)
    run_root = run_probe(tmp_path, transport)
    with pytest.raises(lite.LiteError, match="cross-industry duplicate"):
        run_build(run_root, transport)


def _add_member(dataset: Dataset, industry_code: str, ts_code: str, name: str) -> None:
    dataset.members[industry_code].append(
        [
            industry_code,
            lite.SW2021_INDUSTRIES[industry_code],
            None,
            None,
            None,
            None,
            ts_code,
            name,
            "20200101",
            None,
            "Y",
        ]
    )


def _add_stock(
    dataset: Dataset,
    ts_code: str,
    name: str,
    *,
    market: str = "主板",
    exchange: str = "SSE",
    currency: str = "CNY",
    list_date: str = "20200101",
    include_daily: bool = True,
) -> None:
    dataset.stocks[exchange].append([ts_code, name, market, exchange, currency, "L", list_date])
    if include_daily:
        dataset.daily.append([ts_code, TRADE_DATE, 999999])


def test_universe_filters_boards_names_currency_bj_and_listing_age(tmp_path: Path) -> None:
    dataset = make_dataset()
    industry = next(iter(lite.SW2021_INDUSTRIES))
    cases: list[tuple[str, str, dict[str, Any]]] = [
        ("600900.SH", "外币股", {"currency": "USD"}),
        ("689901.SH", "未知板块", {"market": "主板"}),
        ("600902.SH", "退市旧股", {}),
        ("600903.SH", "旧股退", {}),
        ("600904.SH", "ＳＴ风险", {}),
        ("600905.SH", "＊ＳＴ风险", {}),
        ("600906.SH", "新股", {"list_date": "20230718"}),
        ("600907.SH", "缺市值", {"include_daily": False}),
    ]
    for ts_code, name, kwargs in cases:
        _add_member(dataset, industry, ts_code, name)
        _add_stock(dataset, ts_code, name, **kwargs)
    _add_member(dataset, industry, "600908.SH", "缺股票")
    _add_member(dataset, industry, "920001.BJ", "北交所")
    for ts_code, name, market, exchange in [
        ("688001.SH", " 科创公司 ", "科创板", "SSE"),
        ("000001.SZ", "深主板", "主板", "SZSE"),
        ("300001.SZ", "创业板", "创业板", "SZSE"),
        ("600909.SH", "　边界公司　", "主板", "SSE"),
    ]:
        _add_member(dataset, industry, ts_code, name)
        _add_stock(
            dataset,
            ts_code,
            name,
            market=market,
            exchange=exchange,
            list_date="20230717" if ts_code == "600909.SH" else "20200101",
        )
    transport = FakeTransport(dataset)
    run_root = run_probe(tmp_path, transport)
    derived = run_build(run_root, transport)

    frame = {row["ts_code"]: row for row in read_csv(derived / "frame.csv")}
    assert {"688001.SH", "000001.SZ", "300001.SZ", "600909.SH"} <= frame.keys()
    assert frame["600909.SH"]["name"] == "边界公司"
    excluded = read_csv(derived / "excluded.csv")
    counts: dict[str, int] = {}
    for row in excluded:
        counts[row["reason"]] = counts.get(row["reason"], 0) + 1
    assert counts == {
        "bj_exchange": 1,
        "excluded_name": 4,
        "listed_less_than_3_years": 1,
        "missing_daily": 1,
        "missing_stock": 1,
        "non_cny": 1,
        "unsupported_board": 1,
    }


@pytest.mark.parametrize("case", ["stock_exchange", "daily_date", "daily_value", "daily_duplicate"])
def test_batch_stock_and_daily_structure_errors_fail_probe(tmp_path: Path, case: str) -> None:
    dataset = make_dataset()
    if case == "stock_exchange":
        dataset.stocks["SSE"][0][3] = "SZSE"
    elif case == "daily_date":
        dataset.daily[0][1] = "20260716"
    elif case == "daily_value":
        dataset.daily[0][2] = 0
    else:
        dataset.daily.append(dataset.daily[0][:])
    with pytest.raises(lite.LiteError, match="probe failed"):
        run_probe(tmp_path, FakeTransport(dataset))


def test_input_order_does_not_change_frame_or_sample(tmp_path: Path) -> None:
    first_transport = FakeTransport(make_dataset())
    first_run = run_probe(tmp_path / "first", first_transport)
    first = run_build(first_run, first_transport)
    second_transport = FakeTransport(make_dataset(reverse=True))
    second_run = run_probe(tmp_path / "second", second_transport)
    second = run_build(second_run, second_transport)

    assert (first / "frame.csv").read_bytes() == (second / "frame.csv").read_bytes()
    assert (first / "sample.csv").read_bytes() == (second / "sample.csv").read_bytes()


def test_small_sampling_cell_fails_without_derived_success(tmp_path: Path) -> None:
    transport = FakeTransport(make_dataset(per_industry=1))
    run_root = run_probe(tmp_path, transport)
    with pytest.raises(lite.LiteError, match="fewer than three"):
        run_build(run_root, transport)

    summary = json.loads((run_root / "run-summary.json").read_text())
    assert summary["probe_pass"] is True
    assert summary["build_pass"] is False
    assert summary["failed_call"]["phase"] == "build"
    assert not (run_root / "derived").exists()
    assert not (run_root / "derived.tmp").exists()
    assert len(list((run_root / "raw").iterdir())) == 35


def test_create_only_raw_stops_before_supplemental_request(tmp_path: Path) -> None:
    transport = FakeTransport(make_dataset())
    run_root = run_probe(tmp_path, transport)
    second_code = tuple(lite.SW2021_INDUSTRIES)[1]
    (run_root / "raw" / f"006-index-member-{second_code}.json").write_text("occupied")
    before = len(transport.calls)

    with pytest.raises(lite.LiteError, match="already exists"):
        run_build(run_root, transport)

    assert len(transport.calls) == before
    assert not (run_root / "derived").exists()


def test_failed_atomic_publication_removes_temporary_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeTransport(make_dataset())
    run_root = run_probe(tmp_path, transport)
    real_write = lite._write_create_only

    def fail_sample(path: Path, raw: bytes) -> None:
        if path.name == "sample.csv":
            raise lite.LiteError("synthetic publication failure")
        real_write(path, raw)

    monkeypatch.setattr(lite, "_write_create_only", fail_sample)
    with pytest.raises(lite.LiteError, match="publication failure"):
        run_build(run_root, transport)

    assert not (run_root / "derived").exists()
    assert not (run_root / "derived.tmp").exists()
    assert json.loads((run_root / "run-summary.json").read_text())["build_pass"] is False


def test_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        lite.main(["--help"])
    assert error.value.code == 0
    assert "probe" in capsys.readouterr().out
