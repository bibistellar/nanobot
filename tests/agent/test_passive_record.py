"""Passive recording of un-addressed group chatter into session history.

AgentLoop._record_passive_message appends a message to a chat's history for
context (so the bot stays aware of, and can later read, the conversation)
without running a turn or producing a reply.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus


def _make_loop(tmp_path: Path) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")


@pytest.mark.asyncio
async def test_records_message_and_captures_chat_metadata(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    msg = InboundMessage(
        channel="telegram",
        sender_id="123",
        chat_id="-5111011186",
        content="[Alice @alice]: hello everyone",
        metadata={
            "_record_only": True,
            "chat_title": "Family Group",
            "chat_type": "group",
        },
    )

    await loop._record_passive_message(msg)

    session = loop.sessions.get_or_create("telegram:-5111011186")
    users = [m for m in session.messages if m.get("role") == "user"]
    assert users, "expected the passive message to be appended as a user turn"
    assert users[-1]["content"] == "[Alice @alice]: hello everyone"
    # Title/type captured so the cross-session roster can name the group.
    assert session.metadata.get("title") == "Family Group"
    assert session.metadata.get("chat_type") == "group"


@pytest.mark.asyncio
async def test_skips_blank_content(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    msg = InboundMessage(
        channel="telegram",
        sender_id="1",
        chat_id="-1",
        content="   ",
        metadata={"_record_only": True},
    )

    await loop._record_passive_message(msg)

    session = loop.sessions.get_or_create("telegram:-1")
    assert [m for m in session.messages if m.get("role") == "user"] == []


@pytest.mark.asyncio
async def test_passive_records_share_session_with_addressed_turns(tmp_path: Path) -> None:
    """Passive chatter lands in the same session file as addressed messages,
    so a later turn sees it as prior context."""
    loop = _make_loop(tmp_path)
    key = "telegram:-5111011186"
    for text in ("[Bob @bob]: morning", "[Carol @carol]: hi all"):
        await loop._record_passive_message(
            InboundMessage(
                channel="telegram",
                sender_id="9",
                chat_id="-5111011186",
                content=text,
                metadata={"_record_only": True},
            )
        )

    session = loop.sessions.get_or_create(key)
    contents = [m["content"] for m in session.messages if m.get("role") == "user"]
    assert contents == ["[Bob @bob]: morning", "[Carol @carol]: hi all"]
