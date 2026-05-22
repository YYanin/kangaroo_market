"""Tests for the Phase 7 notification layer (notify.py)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from kangaroo.notify import (
    PushbulletNotifier,
    TelegramNotifier,
    format_closed_alert,
    format_new_alert,
    format_rung_alert,
    make_notifier,
)

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


class TestNotificationFormatting:
    def test_format_new_alert_contains_ticker_and_pct(self) -> None:
        msg = format_new_alert(ticker="AAPL", pct_change=-6.2, summary="Apple Inc")
        assert "[NEW]" in msg
        assert "AAPL" in msg
        assert "-6.2%" in msg
        assert "Apple Inc" in msg

    def test_format_new_alert_positive_pct(self) -> None:
        # pct_change should always render with sign
        msg = format_new_alert(ticker="XYZ", pct_change=1.5, summary="XYZ Corp")
        assert "+1.5%" in msg

    def test_format_rung_alert_contains_key_fields(self) -> None:
        msg = format_rung_alert(
            ticker="MSFT",
            rung_number=2,
            prev_rung_number=1,
            prev_alert_price=100.0,
            current_price=96.0,
            first_alert_price=100.0,
        )
        assert "[RUNG 2]" in msg
        assert "MSFT" in msg
        assert "$100.00" in msg
        assert "4.0%" in msg  # total drawdown

    def test_format_rung_alert_pct_from_prev(self) -> None:
        # 90 vs 100 → -10% from prev
        msg = format_rung_alert(
            ticker="T",
            rung_number=3,
            prev_rung_number=2,
            prev_alert_price=100.0,
            current_price=90.0,
            first_alert_price=110.0,
        )
        assert "-10.0%" in msg

    def test_format_rung_alert_total_drawdown(self) -> None:
        # first=100, current=80 → total drawdown 20%
        msg = format_rung_alert(
            ticker="T",
            rung_number=2,
            prev_rung_number=1,
            prev_alert_price=90.0,
            current_price=80.0,
            first_alert_price=100.0,
        )
        assert "20.0%" in msg

    def test_format_closed_alert_contains_reason(self) -> None:
        msg = format_closed_alert(ticker="GME", reason="SEC investigation detected")
        assert "[CLOSED]" in msg
        assert "GME" in msg
        assert "SEC investigation detected" in msg
        assert "Review before any further action" in msg


# ---------------------------------------------------------------------------
# PushbulletNotifier
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int = 200, body: str = "{}") -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class TestPushbulletNotifier:
    def _make_session(self, status: int = 200) -> MagicMock:
        session = MagicMock()
        session.post.return_value = _FakeResponse(status=status)
        return session

    def test_enqueue_adds_to_queue(self) -> None:
        notifier = PushbulletNotifier(token="tok", session=MagicMock())
        notifier.enqueue("hello")
        notifier.enqueue("world")
        assert notifier._queue == ["hello", "world"]

    async def test_drain_sends_all_messages(self) -> None:
        session = self._make_session()
        notifier = PushbulletNotifier(token="tok", session=session)
        notifier.enqueue("msg1")
        notifier.enqueue("msg2")
        await notifier.drain()

        assert session.post.call_count == 2
        assert notifier._queue == []

    async def test_drain_sends_correct_payload(self) -> None:
        session = self._make_session()
        notifier = PushbulletNotifier(token="secret", session=session)
        notifier.enqueue("test message")
        await notifier.drain()

        call_kwargs = session.post.call_args
        assert call_kwargs.kwargs["headers"] == {"Access-Token": "secret"}
        payload = call_kwargs.kwargs["json"]
        assert payload["type"] == "note"
        assert payload["body"] == "test message"

    async def test_drain_clears_queue_on_http_error(self) -> None:
        session = self._make_session(status=401)
        notifier = PushbulletNotifier(token="bad", session=session)
        notifier.enqueue("msg")
        await notifier.drain()
        assert notifier._queue == []

    async def test_drain_swallows_exception(self) -> None:
        session = MagicMock()
        session.post.side_effect = OSError("network down")
        notifier = PushbulletNotifier(token="tok", session=session)
        notifier.enqueue("msg")
        # Must not raise
        await notifier.drain()
        assert notifier._queue == []

    async def test_drain_is_idempotent_on_empty_queue(self) -> None:
        session = self._make_session()
        notifier = PushbulletNotifier(token="tok", session=session)
        await notifier.drain()
        assert session.post.call_count == 0


# ---------------------------------------------------------------------------
# TelegramNotifier
# ---------------------------------------------------------------------------


class TestTelegramNotifier:
    def _make_session(self, status: int = 200) -> MagicMock:
        session = MagicMock()
        session.post.return_value = _FakeResponse(status=status)
        return session

    def test_enqueue_adds_to_queue(self) -> None:
        notifier = TelegramNotifier(bot_token="t", chat_id="c", session=MagicMock())
        notifier.enqueue("hi")
        assert notifier._queue == ["hi"]

    async def test_drain_sends_to_correct_url(self) -> None:
        session = self._make_session()
        notifier = TelegramNotifier(bot_token="mytoken", chat_id="12345", session=session)
        notifier.enqueue("msg")
        await notifier.drain()

        url = session.post.call_args.args[0]
        assert "mytoken" in url
        assert "sendMessage" in url

    async def test_drain_sends_correct_payload(self) -> None:
        session = self._make_session()
        notifier = TelegramNotifier(bot_token="t", chat_id="999", session=session)
        notifier.enqueue("hello tg")
        await notifier.drain()

        payload = session.post.call_args.kwargs["json"]
        assert payload["chat_id"] == "999"
        assert payload["text"] == "hello tg"

    async def test_drain_clears_queue_on_http_error(self) -> None:
        session = self._make_session(status=400)
        notifier = TelegramNotifier(bot_token="t", chat_id="c", session=session)
        notifier.enqueue("msg")
        await notifier.drain()
        assert notifier._queue == []

    async def test_drain_swallows_exception(self) -> None:
        session = MagicMock()
        session.post.side_effect = OSError("network down")
        notifier = TelegramNotifier(bot_token="t", chat_id="c", session=session)
        notifier.enqueue("msg")
        await notifier.drain()
        assert notifier._queue == []


# ---------------------------------------------------------------------------
# make_notifier factory
# ---------------------------------------------------------------------------


class TestMakeNotifier:
    def _settings(self, provider: str) -> Any:
        import os
        import tempfile
        from pathlib import Path

        from kangaroo.config import load_settings

        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(f"notification:\n  provider: {provider}\n")
            cfg = Path(f.name)

        with tempfile.NamedTemporaryFile(suffix=".env", mode="w", delete=False) as f:
            f.write(
                "POLYGON_API_KEY=x\n"
                "FINNHUB_API_KEY=x\n"
                "PUSHBULLET_TOKEN=pb_tok\n"
                "TELEGRAM_BOT_TOKEN=tg_tok\n"
                "TELEGRAM_CHAT_ID=tg_chat\n"
            )
            env = Path(f.name)

        settings = load_settings(cfg, env)
        os.unlink(cfg)
        os.unlink(env)
        return settings

    def test_pushbullet_provider(self) -> None:
        settings = self._settings("pushbullet")
        notifier = make_notifier(settings, MagicMock())
        assert isinstance(notifier, PushbulletNotifier)

    def test_telegram_provider(self) -> None:
        settings = self._settings("telegram")
        notifier = make_notifier(settings, MagicMock())
        assert isinstance(notifier, TelegramNotifier)

    def test_unknown_provider_defaults_to_pushbullet(self) -> None:
        settings = self._settings("unknown_provider")
        notifier = make_notifier(settings, MagicMock())
        assert isinstance(notifier, PushbulletNotifier)
