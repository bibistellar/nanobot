"""Tests for SubagentManager."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.subagent import SubagentManager
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider


@pytest.mark.asyncio
async def test_subagent_uses_tool_loader():
    """Verify subagent registers tools via ToolLoader, not hard-coded imports."""
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    sm = SubagentManager(
        provider=provider,
        workspace=Path("/tmp"),
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
    )
    tools = sm._build_tools()
    assert tools.has("read_file")
    assert tools.has("write_file")
    # Subagents must be able to deliver — without `message` a cron task whose
    # job is "send a greeting to chat X" runs, finds no tool, returns empty,
    # and the cron evaluator interprets "empty = success" while nothing was
    # actually sent. (Real bug observed: daily-linnea-greeting 2026-05-28.)
    assert tools.has("message")
    # Subagents must NOT spawn more subagents (recursion).
    assert not tools.has("spawn")


@pytest.mark.asyncio
async def test_subagent_message_tool_can_actually_send(tmp_path):
    """Subagent's `message` tool must publish to the bus — without a real
    bus injected, MessageTool falls back to send_callback=None and every send
    returns 'Error: Message sending not configured', silently failing the task."""
    from nanobot.bus.events import OutboundMessage
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    bus = MessageBus()
    sm = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        model="test",
        max_tool_result_chars=16_000,
    )

    msg_tool = sm._build_tools().get("message")
    result = await msg_tool.execute(content="hi", channel="telegram", chat_id="42")

    assert "Message sent" in result
    delivered: OutboundMessage = await bus.consume_outbound()
    assert delivered.channel == "telegram"
    assert delivered.chat_id == "42"
    assert delivered.content == "hi"


@pytest.mark.asyncio
async def test_subagent_build_tools_isolates_file_read_state(tmp_path):
    """Each spawned subagent needs a fresh file-state cache."""
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    sm = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
    )

    first_read = sm._build_tools().get("read_file")
    second_read = sm._build_tools().get("read_file")

    assert first_read is not second_read
    assert (await first_read.execute(path="note.txt")).startswith("1| hello")
    second_result = await second_read.execute(path="note.txt")
    assert second_result.startswith("1| hello")
    assert "File unchanged" not in second_result
