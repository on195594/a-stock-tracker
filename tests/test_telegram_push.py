from __future__ import annotations

import json
import os
import sys
from typing import Any

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from a_stock_tracker.data import cache as cache_mod  # noqa: E402
import a_stock_tracker.reporting.telegram_push as telegram_push  # noqa: E402


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
    l3_v2_signal: int | None = None,
    weights_hash: str = "hash",
    report_period: str = "2024-09-30",
    with_snapshot: bool = True,
) -> None:
    snapshot_json = json.dumps({"moat": 7, "market_pos": 3, "sentiment": 3}) if with_snapshot else None
    sources_json = json.dumps({"moat": "v1", "market_pos": "v1", "sentiment": "v1"}) if with_snapshot else None
    mode = "v1" if with_snapshot else None
    db = cache_mod.get_db()
    db.execute(
        """INSERT INTO predictions
           (code, name, framework, score_date, price_at_score, quant_score,
            total_score, weights_hash, report_period, entry_signal,
            entry_signal_version, l3_v2_signal, qualitative_snapshot_json,
            qualitative_sources_json, qualitative_mode, created_at)
           VALUES (?, ?, 'A', ?, 10.0, ?, ?, ?, ?, ?, 'v1', ?, ?, ?, ?, ?)""",
        (
            code,
            f"N{code}",
            score_date,
            total_score - 10,
            total_score,
            weights_hash,
            report_period,
            entry_signal,
            l3_v2_signal,
            snapshot_json,
            sources_json,
            mode,
            score_date + "T15:00:00",
        ),
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
    db.execute(
        """UPDATE predictions
           SET qualitative_snapshot_json=?, qualitative_sources_json=?, qualitative_mode='v1'
           WHERE code=?""",
        (
            json.dumps({"moat": moat, "market_pos": market_pos, "sentiment": 3}),
            json.dumps({"moat": "v1", "market_pos": "v1", "sentiment": "v1"}),
            code,
        ),
    )
    db.commit()
    db.close()


def test_prediction_snapshot_validation_rejects_invalid_values_and_modes():
    valid_sources = json.dumps({"moat": "v1", "market_pos": "v1", "sentiment": "v1"})
    assert (
        telegram_push._prediction_qualitative_snapshot(
            "600036",
            json.dumps({"moat": True, "market_pos": 3, "sentiment": 3}),
            valid_sources,
            "v1",
        )
        is None
    )
    assert (
        telegram_push._prediction_qualitative_snapshot(
            "600036",
            json.dumps({"moat": 7, "market_pos": 3, "sentiment": 3}),
            valid_sources,
            "hybrid_v2",
        )
        is None
    )


def test_push_daily_signals_sends_only_when_score_and_l3_pass(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda token, chat_id, text: sent.append((token, chat_id, text)))
    _insert_prediction("600036", 66.0, 1, l3_v2_signal=1)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "📊 A股未验证观察名单" in text
    assert "🟢 高分观察" in text
    assert "N600036(600036)" in text
    assert "L3风险门禁:通过(v2)" in text
    assert "L3买点" not in text


def test_l3_v2_pass_triggers_primary_when_v1_rejects(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600037", 66.0, 0, l3_v2_signal=1)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🟢 高分观察" in text
    assert "N600037(600037)" in text
    assert "🟡 高分风险观察" not in text


def test_l3_v2_reject_routes_v1_pass_to_backup(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600038", 66.0, 1, l3_v2_signal=0)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🟢 高分观察" not in text
    assert "🟡 高分风险观察" in text
    assert "N600038(600038)" in text


def test_l3_v2_null_routes_v1_pass_to_backup(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600039", 66.0, 1, l3_v2_signal=None)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🟢 高分观察" not in text
    assert "🟡 高分风险观察" in text
    assert "N600039(600039)" in text
    assert "L3风险门禁:不可用(v2)" in text
    assert "L3风险门禁:拒绝(v2)" not in text


def test_tiered_push_backup_tier_sends_when_l3_zero(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600036", 66.0, 0, l3_v2_signal=0)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🟡 高分风险观察" in text
    assert "N600036(600036)" in text
    assert "L3风险门禁:拒绝(v2)" in text
    assert "(v1)" not in text


def test_push_daily_signals_swallows_send_exception(tmp_db, telegram_env, monkeypatch):
    calls: list[tuple[str, str, str]] = []
    _insert_prediction("600036", 66.0, 1, l3_v2_signal=1)

    def fail_send(token: str, chat_id: str, text: str) -> None:
        calls.append((token, chat_id, text))
        raise RuntimeError("network down")

    monkeypatch.setattr(telegram_push, "_send", fail_send)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(calls) == 1
    assert "🟢 高分观察" in calls[0][2]


def test_tiered_push_radar_tier_only(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600036", 38.0, 0)

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0, radar_min=35.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🔵 一般观察" in text
    assert "N600036(600036)" in text


def test_tiered_push_always_sends_when_all_tiers_empty(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0, radar_min=35.0)

    assert len(sent) == 1
    assert "今日无观察信号" in sent[0][2]


def test_tiered_push_all_three_tiers(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600001", 66.0, 1, l3_v2_signal=1)
    _insert_prediction("600002", 66.0, 0, l3_v2_signal=0)
    _insert_prediction("600003", 38.0, 0)

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0, radar_min=35.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🟢 高分观察" in text
    assert "🟡 高分风险观察" in text
    assert "🔵 一般观察" in text


def test_tiered_push_interpretation_includes_moat(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600036", 66.0, 1, l3_v2_signal=1)
    _insert_qualitative_scores("600036", 8, 5)

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0, radar_min=35.0)

    assert len(sent) == 1
    assert "护城河8/10(强)" in sent[0][2]


def test_tiered_push_no_duplicate_when_multiple_qualitative_dates(tmp_db, telegram_env, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600036", 66.0, 1, l3_v2_signal=1, with_snapshot=False)
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


def test_interpret_edge_cases(monkeypatch, tmp_db, telegram_env):
    """Test moat (5-7, <5) and market_pos (3, <3) interpretation."""
    sent = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))

    _insert_prediction("000001", 66.0, 1, l3_v2_signal=1)  # quant_score = 56.0 (total - 10)
    # moat=6, market_pos=3 -> 护城河6/10, 行业中等(3/5)
    _insert_qualitative_scores("000001", 6, 3)

    _insert_prediction("000002", 66.0, 1, l3_v2_signal=1)  # quant = 56
    # moat=4, market_pos=2 -> 护城河4/10(弱), 行业地位弱(2/5)
    _insert_qualitative_scores("000002", 4, 2)

    db = cache_mod.get_db()
    db.execute(
        """INSERT INTO predictions (code, name, framework, score_date, price_at_score, quant_score, total_score, weights_hash, report_period, entry_signal, entry_signal_version, l3_v2_signal, created_at)
           VALUES ('000003', 'N000003', 'A', '2026-05-30', 10.0, 62.0, 66.0, 'hash', '2024-09-30', 1, 'v1', 1, '2026-05-30T15:00:00')"""
    )
    db.commit()
    db.close()

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0)
    assert len(sent) == 1
    text = sent[0][2]

    assert "护城河6/10" in text
    assert "行业中等(3/5)" in text
    assert "护城河4/10(弱)" in text
    assert "行业地位弱(2/5)" in text
    assert "择时弱" not in text
    assert "择时佳" not in text


def test_push_daily_signals_missing_env_vars(monkeypatch, tmp_db):
    """When TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing, it skips pushing."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    sent = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))

    telegram_push.push_daily_signals("2026-05-30")
    assert len(sent) == 0


def test_push_daily_signals_http_error(monkeypatch, tmp_db, telegram_env):
    """Test HTTPError is caught."""
    import urllib.error

    def fail_send(*args):
        raise urllib.error.HTTPError("url", 403, "Forbidden", {}, None)

    monkeypatch.setattr(telegram_push, "_send", fail_send)
    _insert_prediction("600036", 66.0, 1, l3_v2_signal=1)

    # Should not raise exception
    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)


def test_qualitative_score_gap_is_not_described_as_timing(monkeypatch, tmp_db, telegram_env):
    """Qualitative score contribution must not be presented as entry timing."""
    sent = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))

    db = cache_mod.get_db()
    db.execute(
        """INSERT INTO predictions (code, name, framework, score_date, price_at_score, quant_score, total_score, weights_hash, report_period, entry_signal, entry_signal_version, l3_v2_signal, created_at)
           VALUES ('000004', 'N000004', 'A', '2026-05-30', 10.0, 50.0, 66.0, 'hash', '2024-09-30', 1, 'v1', 1, '2026-05-30T15:00:00')"""
    )
    db.commit()
    db.close()

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0)
    assert len(sent) == 1
    assert "择时佳" not in sent[0][2]
    assert "择时弱" not in sent[0][2]
    assert "L3风险门禁:通过(v2)" in sent[0][2]


def test_send_real_execution(monkeypatch):
    """Test _send executes urlopen correctly."""
    from unittest.mock import patch

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = lambda s, *a: False

        telegram_push._send("test_token", "chat_123", "Hello World")

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://api.telegram.org/bottest_token/sendMessage"
        assert req.get_method() == "POST"


def test_display_uses_prediction_hybrid_snapshot_not_latest_legacy(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600036", 66.0, 1, l3_v2_signal=1)
    _insert_qualitative_scores("600036", 7, 3)
    db = cache_mod.get_db()
    db.execute(
        """UPDATE predictions
           SET qualitative_snapshot_json=?, qualitative_sources_json=?, qualitative_mode='hybrid_v2'
           WHERE code='600036'""",
        (
            json.dumps({"moat": 9, "market_pos": 5, "sentiment": 3}),
            json.dumps({"moat": "v2", "market_pos": "v2", "sentiment": "v1"}),
        ),
    )
    db.commit()
    db.close()

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0)

    assert "护城河9/10(强)" in sent[0][2]


def test_display_falls_back_to_legacy_cache_when_prediction_snapshot_is_missing(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600010", 66.0, 1, l3_v2_signal=1, with_snapshot=False)
    _insert_qualitative_scores("600010", 7, 3)
    db = cache_mod.get_db()
    db.execute(
        """UPDATE predictions
           SET qualitative_snapshot_json=NULL, qualitative_sources_json=NULL, qualitative_mode=NULL
           WHERE code='600010'"""
    )
    db.commit()
    db.close()

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0)

    assert "护城河7/10" in sent[0][2]


def test_pathological_message_is_truncated_to_telegram_limit(tmp_db, telegram_env, monkeypatch, caplog):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))

    for index in range(3):
        _insert_prediction(f"61{index:04d}", 80.0 - index, 1, l3_v2_signal=1)
    for index in range(45):
        _insert_prediction(f"62{index:04d}", 70.0 - index / 10, 0, l3_v2_signal=0)
    for index in range(45):
        _insert_prediction(f"63{index:04d}", 40.0 - index / 100, 0)

    with caplog.at_level("WARNING"):
        telegram_push.push_daily_signals("2026-05-30", threshold=44.0, radar_min=35.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert len(text) <= telegram_push.TELEGRAM_TEXT_LIMIT
    assert text.endswith(telegram_push.MESSAGE_TRUNCATION_MARKER)
    assert "Telegram 消息过长" in caplog.text
