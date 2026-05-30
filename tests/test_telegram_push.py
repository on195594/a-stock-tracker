from __future__ import annotations

import os
import sys
from typing import Any

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib import cache as cache_mod  # noqa: E402
import telegram_push  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
    cache_mod.get_db().close()
    return db_path


@pytest.fixture
def telegram_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")


def _insert_prediction(
    code: str,
    total_score: float,
    entry_signal: int | None,
    score_date: str = "2026-05-30",
) -> None:
    db = cache_mod.get_db()
    db.execute(
        """INSERT INTO predictions
           (code, name, framework, score_date, price_at_score, quant_score,
            total_score, weights_hash, report_period, entry_signal,
            entry_signal_version, created_at)
           VALUES (?, ?, 'A', ?, 10.0, ?, ?, 'hash', '2024-09-30', ?, 'v1', ?)""",
        (code, f"N{code}", score_date, total_score - 10, total_score, entry_signal, score_date + "T15:00:00"),
    )
    db.commit()
    db.close()


def test_push_daily_signals_sends_only_when_score_and_l3_pass(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda token, chat_id, text: sent.append((token, chat_id, text)))
    _insert_prediction("600036", 66.0, 1)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(sent) == 1
    assert "N600036(600036)" in sent[0][2]
    assert "L3:v1 通过" in sent[0][2]


@pytest.mark.parametrize("entry_signal", [0, None])
def test_push_daily_signals_skips_score_passed_rows_when_l3_does_not_pass(
    tmp_db, telegram_env, monkeypatch, entry_signal: int | None
):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600036", 66.0, entry_signal)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert sent == []


def test_push_daily_signals_swallows_send_exception(tmp_db, telegram_env, monkeypatch):
    _insert_prediction("600036", 66.0, 1)

    def fail_send(token: str, chat_id: str, text: str) -> None:
        raise RuntimeError("network down")

    monkeypatch.setattr(telegram_push, "_send", fail_send)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)
