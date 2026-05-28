"""Delivery failure feedback from ChannelManager to the originating session.

The ``message`` tool tags every outbound with ``_origin_session_key`` so the
session that initiated the send can be notified when the channel can't deliver
(e.g. Telegram Forbidden when the recipient never started the bot, or a wrong
chat_id). Without this feedback the agent never learns its proactive send
failed — the tool returns "sent" optimistically — and goes on to lie about
success in task logs etc.

These tests verify the manager publishes a ``delivery_failure`` system event
back to that session on terminal send failure, and stays silent when there is
nothing to route to.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.channels.manager import ChannelManager
from nanobot.config.schema import Config


class _AlwaysFailingChannel(BaseChannel):
    name = "mock"
    display_name = "Mock"

    def __init__(self, config, bus, exc: Exception):
        super().__init__(config, bus)
        self._exc = exc
        self.send = AsyncMock(side_effect=exc)

    async def start(self):  # pragma: no cover - not exercised
        pass

    async def stop(self):  # pragma: no cover - not exercised
        pass


def _make_manager(exc: Exception) -> tuple[ChannelManager, _AlwaysFailingChannel]:
    cfg = Config()
    cfg.channels.send_max_retries = 2  # keep the test quick
    bus = MessageBus()
    mgr = ChannelManager(cfg, bus)
    ch = _AlwaysFailingChannel({}, bus, exc)
    mgr.channels["mock"] = ch
    return mgr, ch


@pytest.mark.asyncio
async def test_terminal_failure_publishes_event_to_origin_session() -> None:
    mgr, ch = _make_manager(PermissionError("Forbidden: bot can't initiate"))
    msg = OutboundMessage(
        channel="mock",
        chat_id="6510547879",
        content="hi",
        metadata={"_origin_session_key": "telegram:1752172576"},
    )

    await mgr._send_with_retry(ch, msg)

    inbound = await asyncio.wait_for(mgr.bus.consume_inbound(), timeout=1.0)
    assert inbound.channel == "system"
    assert inbound.session_key_override == "telegram:1752172576"
    assert inbound.metadata["type"] == "delivery_failure"
    assert inbound.metadata["target_channel"] == "mock"
    assert inbound.metadata["target_chat_id"] == "6510547879"
    assert "PermissionError" in inbound.metadata["error"]
    # The agent reads `content`; it must clearly contradict the prior optimistic
    # "sent" confirmation so a self-correction is unambiguous.
    assert "did NOT actually" in inbound.content
    assert "mock:6510547879" in inbound.content


@pytest.mark.asyncio
async def test_failure_without_origin_session_publishes_nothing() -> None:
    """Untagged outbounds (e.g. raw channel deliveries) have nowhere to route
    the failure back to — staying silent is the right behavior, the same as
    today's logging."""
    mgr, ch = _make_manager(RuntimeError("network down"))
    msg = OutboundMessage(channel="mock", chat_id="42", content="hi", metadata={})

    await mgr._send_with_retry(ch, msg)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(mgr.bus.consume_inbound(), timeout=0.1)


@pytest.mark.asyncio
async def test_successful_send_does_not_publish_failure() -> None:
    """No false positives when the send eventually succeeds (or never raises)."""
    cfg = Config()
    cfg.channels.send_max_retries = 1
    bus = MessageBus()
    mgr = ChannelManager(cfg, bus)

    class _OkChannel(BaseChannel):
        name = "ok"
        display_name = "Ok"

        async def start(self):
            pass

        async def stop(self):
            pass

        send = AsyncMock()

    ch = _OkChannel({}, bus)
    mgr.channels["ok"] = ch
    msg = OutboundMessage(
        channel="ok",
        chat_id="1",
        content="hi",
        metadata={"_origin_session_key": "telegram:1"},
    )

    await mgr._send_with_retry(ch, msg)

    consume = asyncio.create_task(mgr.bus.consume_inbound())
    await asyncio.sleep(0.05)
    assert not consume.done()
    consume.cancel()
    with suppress(asyncio.CancelledError):
        await consume
