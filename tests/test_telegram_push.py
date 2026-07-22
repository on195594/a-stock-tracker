from __future__ import annotations

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
) -> None:
    db = cache_mod.get_db()
    db.execute(
        """INSERT INTO predictions
           (code, name, framework, score_date, price_at_score, quant_score,
            total_score, weights_hash, report_period, entry_signal,
            entry_signal_version, l3_v2_signal, created_at)
           VALUES (?, ?, 'A', ?, 10.0, ?, ?, ?, ?, ?, 'v1', ?, ?)""",
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
    db.commit()
    db.close()


def test_push_daily_signals_sends_only_when_score_and_l3_pass(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda token, chat_id, text: sent.append((token, chat_id, text)))
    _insert_prediction("600036", 66.0, 1, l3_v2_signal=1)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🟢 主推" in text
    assert "N600036(600036)" in text
    assert "✓ L3买点(v1)" in text


def test_l3_v2_pass_triggers_primary_when_v1_rejects(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600037", 66.0, 0, l3_v2_signal=1)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🟢 主推" in text
    assert "N600037(600037)" in text
    assert "🟡 候补" not in text


def test_l3_v2_reject_routes_v1_pass_to_backup(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600038", 66.0, 1, l3_v2_signal=0)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🟢 主推" not in text
    assert "🟡 候补" in text
    assert "N600038(600038)" in text


def test_l3_v2_null_routes_v1_pass_to_backup(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600039", 66.0, 1, l3_v2_signal=None)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🟢 主推" not in text
    assert "🟡 候补" in text
    assert "N600039(600039)" in text


def test_tiered_push_backup_tier_sends_when_l3_zero(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600036", 66.0, 0, l3_v2_signal=0)

    telegram_push.push_daily_signals("2026-05-30", threshold=65.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "🟡 候补" in text
    assert "N600036(600036)" in text
    assert "等待L3(v1)" in text


def test_push_daily_signals_swallows_send_exception(tmp_db, telegram_env, monkeypatch):
    calls: list[tuple[str, str, str]] = []
    _insert_prediction("600036", 66.0, 1, l3_v2_signal=1)

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
    _insert_prediction("600001", 66.0, 1, l3_v2_signal=1)
    _insert_prediction("600002", 66.0, 0, l3_v2_signal=0)
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
    _insert_prediction("600036", 66.0, 1, l3_v2_signal=1)
    _insert_qualitative_scores("600036", 8, 5)

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0, radar_min=35.0)

    assert len(sent) == 1
    assert "护城河8/10(强)" in sent[0][2]


def test_tiered_push_no_duplicate_when_multiple_qualitative_dates(tmp_db, telegram_env, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    _insert_prediction("600036", 66.0, 1, l3_v2_signal=1)
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
    """Test moat (5-7, <5), market_pos (3, <3), timing <= 5 in _interpret."""
    sent = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))

    _insert_prediction("000001", 66.0, 1, l3_v2_signal=1)  # quant_score = 56.0 (total - 10)
    # moat=6, market_pos=3 -> 护城河6/10, 行业中等(3/5)
    _insert_qualitative_scores("000001", 6, 3)

    _insert_prediction("000002", 66.0, 1, l3_v2_signal=1)  # quant = 56
    # moat=4, market_pos=2 -> 护城河4/10(弱), 行业地位弱(2/5)
    _insert_qualitative_scores("000002", 4, 2)

    # Timing <= 5 -> total=66, quant=62.0 -> timing = 4.0 <= 5 -> "择时弱"
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
    assert "择时弱" in text


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


def test_interpret_timing_good(monkeypatch, tmp_db, telegram_env):
    """Test timing >= 12 in _interpret."""
    sent = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))

    # Timing >= 12 -> total=66, quant=50.0 -> timing = 16.0 >= 12 -> "择时佳"
    db = cache_mod.get_db()
    db.execute(
        """INSERT INTO predictions (code, name, framework, score_date, price_at_score, quant_score, total_score, weights_hash, report_period, entry_signal, entry_signal_version, l3_v2_signal, created_at)
           VALUES ('000004', 'N000004', 'A', '2026-05-30', 10.0, 50.0, 66.0, 'hash', '2024-09-30', 1, 'v1', 1, '2026-05-30T15:00:00')"""
    )
    db.commit()
    db.close()

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0)
    assert len(sent) == 1
    assert "择时佳" in sent[0][2]


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


def test_reviewer_only_receives_top_three_primary_stocks_with_row_metadata(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    reviewed_inputs = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))

    def capture_review(review_input):
        reviewed_inputs.append(review_input)
        return telegram_push.ReviewOutput(explanation=f"review-{review_input.code}")

    monkeypatch.setattr(telegram_push, "gemini_review", capture_review)
    for code, score in (
        ("600001", 51.0),
        ("600002", 75.0),
        ("600003", 63.0),
        ("600004", 82.0),
        ("600005", 70.0),
    ):
        _insert_prediction(
            code,
            score,
            1,
            l3_v2_signal=1,
            weights_hash=f"weights-{code}",
            report_period=f"period-{code}",
        )

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0)

    assert len(sent) == 1
    assert [review.code for review in reviewed_inputs] == ["600004", "600002", "600005"]
    assert len(reviewed_inputs) == 3
    assert reviewed_inputs[0].weights_hash == "weights-600004"
    assert reviewed_inputs[0].report_period == "period-600004"


def test_reviewer_handles_fewer_than_three_primary_stocks(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    reviewed_inputs = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))

    def capture_review(review_input):
        reviewed_inputs.append(review_input)
        return telegram_push.ReviewOutput(explanation="single review")

    monkeypatch.setattr(telegram_push, "gemini_review", capture_review)
    _insert_prediction("600010", 66.0, 1, l3_v2_signal=1)

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0)

    assert len(sent) == 1
    assert [review.code for review in reviewed_inputs] == ["600010"]


def test_reviewer_not_called_without_primary_stocks(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    review_calls: list[Any] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    monkeypatch.setattr(telegram_push, "gemini_review", lambda value: review_calls.append(value))
    _insert_prediction("600020", 66.0, 1, l3_v2_signal=0)
    _insert_prediction("600021", 38.0, 0)

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0, radar_min=35.0)

    assert len(sent) == 1
    assert review_calls == []
    assert "🟡 候补" in sent[0][2]
    assert "🔵 雷达" in sent[0][2]


def test_reviewer_output_only_appears_for_reviewed_primary_stocks(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))

    def review(review_input):
        return telegram_push.ReviewOutput(
            explanation=f"explanation-{review_input.code}",
            objections=(f"objection-{review_input.code}",),
            human_questions=(f"question-{review_input.code}",),
        )

    monkeypatch.setattr(telegram_push, "gemini_review", review)
    for code, score in (("600030", 80.0), ("600031", 70.0), ("600032", 60.0), ("600033", 50.0)):
        _insert_prediction(code, score, 1, l3_v2_signal=1)
    _insert_prediction("600034", 49.0, 1, l3_v2_signal=0)
    _insert_prediction("600035", 38.0, 0)

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0, radar_min=35.0)

    text = sent[0][2]
    for code in ("600030", "600031", "600032"):
        assert f"🤖 审查: explanation-{code}" in text
        assert f"⚠️ 异议: objection-{code}" in text
        assert f"❓ 待核实: question-{code}" in text
    for code in ("600033", "600034", "600035"):
        assert f"explanation-{code}" not in text
        assert f"objection-{code}" not in text
        assert f"question-{code}" not in text
    assert telegram_push.MESSAGE_TRUNCATION_MARKER not in text


def test_reviewer_output_visibly_distinguishes_fallback(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))

    def review(review_input):
        return telegram_push.ReviewOutput(
            explanation=f"review-{review_input.code}",
            is_fallback=review_input.code == "600051",
        )

    monkeypatch.setattr(telegram_push, "gemini_review", review)
    _insert_prediction("600050", 80.0, 1, l3_v2_signal=1)
    _insert_prediction("600051", 70.0, 1, l3_v2_signal=1)

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0)

    text = sent[0][2]
    assert "🤖 审查: review-600050" in text
    assert "📋 说明(降级，未调用真实LLM): review-600051" in text
    assert "🤖 审查: review-600051" not in text


def test_reviewer_fields_are_bounded_without_truncating_whole_message(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    monkeypatch.setattr(
        telegram_push,
        "gemini_review",
        lambda _review_input: telegram_push.ReviewOutput(
            explanation="解" * 1000,
            objections=("异" * 1000,),
            human_questions=("问" * 1000,),
        ),
    )
    _insert_prediction("600052", 80.0, 1, l3_v2_signal=1)

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0)

    text = sent[0][2]
    assert telegram_push.MESSAGE_TRUNCATION_MARKER not in text
    assert "解" * (telegram_push.REVIEW_EXPLANATION_LIMIT - 1) + "…" in text
    assert "异" * (telegram_push.REVIEW_OBJECTIONS_LIMIT - 1) + "…" in text
    assert "问" * (telegram_push.REVIEW_QUESTIONS_LIMIT - 1) + "…" in text
    assert "解" * telegram_push.REVIEW_EXPLANATION_LIMIT not in text


def test_pathological_message_is_truncated_to_telegram_limit(tmp_db, telegram_env, monkeypatch, caplog):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))
    monkeypatch.setattr(
        telegram_push,
        "gemini_review",
        lambda _review_input: telegram_push.ReviewOutput(
            explanation="超长说明" * 1000,
            objections=("超长异议" * 1000,),
            human_questions=("超长问题" * 1000,),
        ),
    )

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


def test_reviewer_exception_omits_one_block_without_interrupting_push(tmp_db, telegram_env, monkeypatch):
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telegram_push, "_send", lambda *args: sent.append(args))

    def review(review_input):
        if review_input.code == "600041":
            raise RuntimeError("boom")
        return telegram_push.ReviewOutput(explanation=f"review-{review_input.code}")

    monkeypatch.setattr(telegram_push, "gemini_review", review)
    _insert_prediction("600040", 80.0, 1, l3_v2_signal=1)
    _insert_prediction("600041", 70.0, 1, l3_v2_signal=1)
    _insert_prediction("600042", 60.0, 1, l3_v2_signal=1)
    _insert_prediction("600043", 50.0, 1, l3_v2_signal=0)
    _insert_prediction("600044", 38.0, 0)

    telegram_push.push_daily_signals("2026-05-30", threshold=44.0, radar_min=35.0)

    assert len(sent) == 1
    text = sent[0][2]
    assert "N600041(600041)" in text
    assert "review-600041" not in text
    assert "review-600040" in text
    assert "review-600042" in text
    assert "🟡 候补" in text
    assert "N600043(600043)" in text
    assert "🔵 雷达" in text
    assert "N600044(600044)" in text
