from __future__ import annotations

import asyncio

import pytest

from nanobot.agent.tools.context import RequestContext
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.cron.service import CronService


@pytest.mark.asyncio
async def test_message_tool_keeps_task_local_context() -> None:
    seen: list[tuple[str, str, str]] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def send_callback(msg):
        seen.append((msg.channel, msg.chat_id, msg.content))
        return None

    tool = MessageTool(send_callback=send_callback)

    async def task_one() -> str:
        tool.set_context(RequestContext(channel="feishu", chat_id="chat-a"))
        entered.set()
        await release.wait()
        return await tool.execute(content="one")

    async def task_two() -> str:
        await entered.wait()
        tool.set_context(RequestContext(channel="email", chat_id="chat-b"))
        release.set()
        return await tool.execute(content="two")

    result_one, result_two = await asyncio.gather(task_one(), task_two())

    assert result_one == "Message sent to feishu:chat-a"
    assert result_two == "Message sent to email:chat-b"
    assert ("feishu", "chat-a", "one") in seen
    assert ("email", "chat-b", "two") in seen


@pytest.mark.asyncio
async def test_spawn_tool_keeps_task_local_context() -> None:
    seen: list[tuple[str, str, str]] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    class _Manager:
        max_concurrent_subagents = 1

        def get_running_count(self) -> int:
            return 0

        async def spawn(
            self,
            *,
            task: str,
            label: str | None,
            origin_channel: str,
            origin_chat_id: str,
            session_key: str,
            origin_message_id: str | None = None,
            temperature: float | None = None,
        ) -> str:
            seen.append((origin_channel, origin_chat_id, session_key))
            return f"{origin_channel}:{origin_chat_id}:{task}"

    tool = SpawnTool(_Manager())

    async def task_one() -> str:
        tool.set_context(RequestContext(channel="whatsapp", chat_id="chat-a"))
        entered.set()
        await release.wait()
        return await tool.execute(task="one")

    async def task_two() -> str:
        await entered.wait()
        tool.set_context(RequestContext(channel="telegram", chat_id="chat-b"))
        release.set()
        return await tool.execute(task="two")

    result_one, result_two = await asyncio.gather(task_one(), task_two())

    assert result_one == "whatsapp:chat-a:one"
    assert result_two == "telegram:chat-b:two"
    assert ("whatsapp", "chat-a", "whatsapp:chat-a") in seen
    assert ("telegram", "chat-b", "telegram:chat-b") in seen


@pytest.mark.asyncio
async def test_cron_tool_keeps_task_local_context(tmp_path) -> None:
    tool = CronTool(CronService(tmp_path / "jobs.json"))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def task_one() -> str:
        tool.set_context(RequestContext(channel="feishu", chat_id="chat-a"))
        entered.set()
        await release.wait()
        return await tool.execute(action="add", message="first", every_seconds=60)

    async def task_two() -> str:
        await entered.wait()
        tool.set_context(RequestContext(channel="email", chat_id="chat-b"))
        release.set()
        return await tool.execute(action="add", message="second", every_seconds=60)

    result_one, result_two = await asyncio.gather(task_one(), task_two())

    assert result_one.startswith("Created job")
    assert result_two.startswith("Created job")

    jobs = tool._cron.list_jobs()
    assert {job.payload.origin_channel for job in jobs} == {"feishu", "email"}
    assert {job.payload.origin_chat_id for job in jobs} == {"chat-a", "chat-b"}


# --- Basic single-task regression tests ---


@pytest.mark.asyncio
async def test_message_tool_basic_set_context_and_execute() -> None:
    """Single task: set_context then execute should route correctly."""
    seen: list[tuple[str, str, str]] = []

    async def send_callback(msg):
        seen.append((msg.channel, msg.chat_id, msg.content))

    tool = MessageTool(send_callback=send_callback)
    tool.set_context(RequestContext(channel="telegram", chat_id="chat-123", message_id="msg-456"))

    result = await tool.execute(content="hello")
    assert result == "Message sent to telegram:chat-123"
    assert seen == [("telegram", "chat-123", "hello")]


@pytest.mark.asyncio
async def test_message_tool_default_values_without_set_context() -> None:
    """Without set_context, constructor defaults should be used."""
    seen: list[tuple[str, str, str]] = []

    async def send_callback(msg):
        seen.append((msg.channel, msg.chat_id, msg.content))

    tool = MessageTool(
        send_callback=send_callback,
        default_channel="discord",
        default_chat_id="general",
    )

    result = await tool.execute(content="hi")
    assert result == "Message sent to discord:general"
    assert seen == [("discord", "general", "hi")]


@pytest.mark.asyncio
async def test_spawn_tool_basic_set_context_and_execute() -> None:
    """Single task: set_context then execute should pass correct origin."""
    seen: list[tuple[str, str, str]] = []

    class _Manager:
        max_concurrent_subagents = 1

        def get_running_count(self) -> int:
            return 0

        async def spawn(
            self,
            *,
            task,
            label,
            origin_channel,
            origin_chat_id,
            session_key,
            origin_message_id=None,
            temperature=None,
        ):
            seen.append((origin_channel, origin_chat_id, session_key))
            return f"ok: {task}"

    tool = SpawnTool(_Manager())
    tool.set_context(RequestContext(channel="feishu", chat_id="chat-abc"))

    result = await tool.execute(task="do something")
    assert result == "ok: do something"
    assert seen == [("feishu", "chat-abc", "feishu:chat-abc")]


@pytest.mark.asyncio
async def test_spawn_tool_default_values_without_set_context() -> None:
    """Without set_context, default cli:direct should be used."""
    seen: list[tuple[str, str, str]] = []

    class _Manager:
        max_concurrent_subagents = 1

        def get_running_count(self) -> int:
            return 0

        async def spawn(
            self,
            *,
            task,
            label,
            origin_channel,
            origin_chat_id,
            session_key,
            origin_message_id=None,
            temperature=None,
        ):
            seen.append((origin_channel, origin_chat_id, session_key))
            return "ok"

    tool = SpawnTool(_Manager())

    await tool.execute(task="test")
    assert seen == [("cli", "direct", "cli:direct")]


@pytest.mark.asyncio
async def test_cron_tool_basic_set_context_and_execute(tmp_path) -> None:
    """Single task: set_context then add job should use correct target."""
    tool = CronTool(CronService(tmp_path / "jobs.json"))
    tool.set_context(RequestContext(channel="wechat", chat_id="user-789"))

    result = await tool.execute(action="add", message="standup", every_seconds=300)
    assert result.startswith("Created job")

    jobs = tool._cron.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].payload.origin_channel == "wechat"
    assert jobs[0].payload.origin_chat_id == "user-789"


@pytest.mark.asyncio
async def test_cron_tool_no_context_returns_error(tmp_path) -> None:
    """Without set_context, add should fail with a clear error."""
    tool = CronTool(CronService(tmp_path / "jobs.json"))

    result = await tool.execute(action="add", message="test", every_seconds=60)
    assert result == "Error: no session context (channel/chat_id)"


@pytest.mark.asyncio
async def test_cron_tool_deliver_to_routes_to_explicit_target(tmp_path) -> None:
    """deliver_to='channel:chat_id' should set deliver_* without touching origin_*."""
    tool = CronTool(CronService(tmp_path / "jobs.json"))
    tool.set_context(RequestContext(channel="slack", chat_id="C_general"))

    result = await tool.execute(
        action="add",
        message="check the build status",
        every_seconds=600,
        deliver_to="telegram:user_12345",
    )
    assert result.startswith("Created job")

    jobs = tool._cron.list_jobs()
    assert len(jobs) == 1
    # Origin stays at the place the user created the task.
    assert jobs[0].payload.origin_channel == "slack"
    assert jobs[0].payload.origin_chat_id == "C_general"
    # Delivery is routed to the explicitly requested target.
    assert jobs[0].payload.deliver_channel == "telegram"
    assert jobs[0].payload.deliver_chat_id == "user_12345"


@pytest.mark.asyncio
async def test_cron_tool_deliver_to_here_uses_current_session(tmp_path) -> None:
    """deliver_to='here' (or omitted) should leave delivery == origin."""
    tool = CronTool(CronService(tmp_path / "jobs.json"))
    tool.set_context(RequestContext(channel="slack", chat_id="C_general"))

    await tool.execute(
        action="add",
        message="x",
        every_seconds=60,
        deliver_to="here",
    )
    jobs = tool._cron.list_jobs()
    assert jobs[0].payload.deliver_channel == "slack"
    assert jobs[0].payload.deliver_chat_id == "C_general"


def test_parse_deliver_to() -> None:
    """deliver_to spec parsing handles 'here', empty, malformed, and well-formed."""
    assert CronTool._parse_deliver_to(None) == (None, None)
    assert CronTool._parse_deliver_to("") == (None, None)
    assert CronTool._parse_deliver_to("here") == (None, None)
    assert CronTool._parse_deliver_to("HERE") == (None, None)
    assert CronTool._parse_deliver_to("telegram:12345") == ("telegram", "12345")
    # Slack thread spec — chat_id keeps the remaining colons.
    assert CronTool._parse_deliver_to("slack:C123:1700.42") == ("slack", "C123:1700.42")
    # Malformed specs gracefully fall back to None,None (caller uses defaults).
    assert CronTool._parse_deliver_to("nochannel") == (None, None)
    assert CronTool._parse_deliver_to(":empty") == (None, None)
    assert CronTool._parse_deliver_to("empty:") == (None, None)
