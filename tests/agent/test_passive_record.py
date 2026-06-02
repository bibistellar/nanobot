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


@pytest.mark.asyncio
async def test_passive_record_with_image_describes_and_stores_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A photo-only group message gets a vision describe folded into history."""
    loop = _make_loop(tmp_path)

    async def fake_describe(path, provider, model):
        # Asserts the helper is called with the loop's provider/model so the
        # passive vision call cannot accidentally use a different runtime.
        assert provider is loop.provider
        assert model == "test-model"
        return f"a screenshot of {Path(path).name}"

    monkeypatch.setattr(
        "nanobot.agent.loop.describe_image_for_history", fake_describe
    )

    photo = tmp_path / "tg-photo.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0fake jpeg")

    msg = InboundMessage(
        channel="telegram",
        sender_id="42",
        chat_id="-5111011186",
        content="[Alice @alice]: ",  # photo-only message → trailing prefix
        media=[str(photo)],
        metadata={"_record_only": True, "chat_title": "Family", "chat_type": "group"},
    )

    await loop._record_passive_message(msg)

    session = loop.sessions.get_or_create("telegram:-5111011186")
    users = [m for m in session.messages if m.get("role") == "user"]
    assert users, "expected the passive photo message to land in history"
    persisted = users[-1]
    assert persisted["content"] == (
        "[Alice @alice]:\n[image: a screenshot of tg-photo.jpg]"
    )
    # Original media path is preserved so the bot can re-open it via read_file.
    assert persisted.get("media") == [str(photo)]


@pytest.mark.asyncio
async def test_passive_record_image_describe_failure_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the vision call returns None (provider error, unsupported model, etc.)
    we still record the message with a generic placeholder so it isn't lost."""
    loop = _make_loop(tmp_path)

    async def failing_describe(path, provider, model):
        return None

    monkeypatch.setattr(
        "nanobot.agent.loop.describe_image_for_history", failing_describe
    )

    photo = tmp_path / "broken.png"
    photo.write_bytes(b"not really a png")

    msg = InboundMessage(
        channel="telegram",
        sender_id="42",
        chat_id="-5",
        content="[Bob @bob]: check this",
        media=[str(photo)],
        metadata={"_record_only": True},
    )

    await loop._record_passive_message(msg)

    users = [
        m for m in loop.sessions.get_or_create("telegram:-5").messages
        if m.get("role") == "user"
    ]
    assert users[-1]["content"] == "[Bob @bob]: check this\n[image: image]"
    assert users[-1].get("media") == [str(photo)]


@pytest.mark.asyncio
async def test_passive_record_media_only_still_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty content + media should still be recorded (without the empty
    early-return short-circuiting it away)."""
    loop = _make_loop(tmp_path)

    async def fake_describe(path, provider, model):
        return "a sunset over a lake"

    monkeypatch.setattr(
        "nanobot.agent.loop.describe_image_for_history", fake_describe
    )

    photo = tmp_path / "sunset.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0jpeg")

    msg = InboundMessage(
        channel="telegram",
        sender_id="7",
        chat_id="-9",
        content="",
        media=[str(photo)],
        metadata={"_record_only": True},
    )

    await loop._record_passive_message(msg)

    users = [
        m for m in loop.sessions.get_or_create("telegram:-9").messages
        if m.get("role") == "user"
    ]
    assert users[-1]["content"] == "[image: a sunset over a lake]"
