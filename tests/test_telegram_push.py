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


def _insert_qualitative_scores(code: str, moat: int, market_pos: int) -> None:
    db = cache_mod.get_db()
    db.execute(
        """INSERT OR REPLACE INTO qualitative_scores
           (code, moat, market_pos, sentiment, scored_date)
           VALUES (?, ?, ?, 3, '2026-05-30')""",
        (code, moat, market_pos),
    )
    db.commit()
    db.close()


def test_push_daily_signals_sends_only_when_score_and_l3_pass(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda token, chat_id, text: sent.append((token, chat_id, text)))
    _insert_prediction("600036", 66.0, 1)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🟢 主推" in text
    assert "N600036(600036)" in text
    assert "✓ L3买点(v1)" in text


def test_tiered_push_backup_tier_sends_when_l3_zero(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600036", 66.0, 0)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🟡 候补" in text
    assert "N600036(600036)" in text
    assert "等待L3(v1)" in text


def test_push_daily_signals_swallows_send_exception(tmp_db, telegram_env, monkeypatch):
    calls: list[tuple[str, str, str]] = []
    _insert_prediction("600036", 66.0, 1)

    def fail_send(token: str, chat_id: str, text: str) -> None:
        calls.append((token, chat_id, text))
        raise RuntimeError("network down")

    monkeypatch.setattr(telegram_push, "_send", fail_send)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(calls) == 1
    assert "🟢 主推" in calls[0][2]


def test_tiered_push_radar_tier_only(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600036", 38.0, 0)

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0, radar_min=35.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🔵 雷达" in text
    assert "N600036(600036)" in text


def test_tiered_push_always_sends_when_all_tiers_empty(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0, radar_min=35.0)

    assert len(sent) == 1
    assert "今日无推荐信号" in sent[0][2]


def test_tiered_push_all_three_tiers(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600001", 66.0, 1)
    _insert_prediction("600002", 66.0, 0)
    _insert_prediction("600003", 38.0, 0)

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0, radar_min=35.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🟢 主推" in text
    assert "🟡 候补" in text
    assert "🔵 雷达" in text


def test_tiered_push_interpretation_includes_moat(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600036", 66.0, 1)
    _insert_qualitative_scores("600036", 8, 5)

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0, radar_min=35.0)

    assert len(sent) == 1
    assert "护城河8/10(强)" in sent[0][2]


def test_tiered_push_no_duplicate_when_multiple_qualitative_dates(tmp_db, telegram_env, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600036", 66.0, 1)
    # Insert two qualitative_scores rows for same code, different scored_date
    db = cache_mod.get_db()
    db.execute(
        "INSERT INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date) VALUES (?, 7, 4, 3, '2026-06-01')",
        ("600036",),
    )
    db.execute(
        "INSERT INTO qualitative_scores (code, moat, market_pos, sentiment, scored_date) VALUES (?, 8, 5, 4, '2026-07-01')",
        ("600036",),
    )
    db.commit()
    db.close()
    telegram_push.push_daily_signals("2026-05-30", threshold=44.0)
    assert len(sent) == 1
    text = sent[0][2]
    # Stock must appear exactly once (no duplicates from multi-date JOIN)
    assert text.count("N600036(600036)") == 1
    # Must use the most recent scored_date (2026-07-01: moat=8 -> "护城河8/10(强)")
    assert "护城河8/10(强)" in text
