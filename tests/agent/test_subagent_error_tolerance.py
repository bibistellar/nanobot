"""Subagent ↔ main agent error-tolerance alignment.

Background: ``SubagentManager`` historically passed ``fail_on_tool_error=True``
to the runner, which meant a single stray PascalCase tool name (``Grep``
instead of ``grep`` — a Claude 4.x training preference) instantly killed
the whole subagent with no chance to self-correct.  Today's
``image-upgrade-check`` cron crashed exactly this way.

The main agent runs with ``fail_on_tool_error=False`` — tool errors come
back as a tool result, the LLM sees ``Tool 'Grep' not found. Available:
…, grep, …`` and picks the right name on the next iteration.  This is
the behavior subagents should also have.

These tests pin:

1. The run spec passed to the runner has ``fail_on_tool_error=False``
   (the contract — once a future refactor flips it back, this trips).
2. ``_SubagentHook.before_execute_tools`` logs at INFO so the agent-
   visible file sink under ``logs/gateway-YYYY-MM-DD.log`` captures
   subagent tool activity (previously DEBUG, invisible to self-
   introspection).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from loguru import logger

from nanobot.agent.hook import AgentHookContext
from nanobot.agent.subagent import SubagentManager, _SubagentHook
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider


# ---------------------------------------------------------------------------
# fail_on_tool_error spec
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_run_spec_tolerates_tool_errors(tmp_path: Path) -> None:
    """The AgentRunSpec the subagent hands to the runner must carry
    ``fail_on_tool_error=False`` so a wrong tool name (or any single tool
    error) doesn't terminate the whole subagent — the LLM sees the error
    as a tool result and self-corrects, same as the main agent."""
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    sm = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
    )

    captured_spec = {}

    async def _capture(spec):
        captured_spec["spec"] = spec
        # Return a minimal result-shaped object so _run_subagent's
        # post-run code path doesn't blow up — we only care about the spec.
        result = MagicMock()
        result.stop_reason = "stop"
        result.error = None
        result.final_content = "done"
        result.usage = {}
        result.tool_events = []
        return result

    with patch.object(sm.runner, "run", side_effect=_capture):
        await sm.spawn(task="trivial task", label="test")
        # spawn() schedules a background task — give it a tick.
        await asyncio.sleep(0.05)
        # Drain any pending tasks the spawn started.
        pending = [t for t in sm._running_tasks.values() if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    assert "spec" in captured_spec, "runner.run was never invoked — the spawn path is broken"
    assert captured_spec["spec"].fail_on_tool_error is False, (
        "fail_on_tool_error must be False so subagents recover from "
        "transient tool errors (e.g. Claude using 'Grep' instead of 'grep') "
        "the same way the main agent does"
    )


# ---------------------------------------------------------------------------
# Hook log level
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_hook_logs_tool_calls_at_info_level(tmp_path: Path) -> None:
    """``_SubagentHook.before_execute_tools`` must log at INFO so the
    agent-visible JSONL log file picks it up.  Previously this was DEBUG,
    which meant our log sink (level=INFO) silently dropped every
    subagent tool call — making it impossible for the bot to self-
    introspect why a cron subagent failed."""
    captured: list[str] = []

    # Route loguru records into a python list — loguru's stock pytest
    # capture doesn't compose cleanly, so we attach a tiny sink.
    sink_id = logger.add(
        lambda msg: captured.append(msg.record["message"] + "|" + msg.record["level"].name),
        level="INFO",
        format="{message}",
    )
    try:
        hook = _SubagentHook("test-task-id")
        # Fake the minimum AgentHookContext surface that
        # before_execute_tools touches.
        tool_call = MagicMock()
        tool_call.name = "read_file"
        tool_call.arguments = {"path": "memory/MEMORY.md"}
        ctx = AgentHookContext(
            iteration=1,
            messages=[],
            tool_calls=[tool_call],
        )
        await hook.before_execute_tools(ctx)
    finally:
        logger.remove(sink_id)

    info_lines = [line for line in captured if line.endswith("|INFO")]
    assert info_lines, (
        f"expected at least one INFO log from before_execute_tools; "
        f"captured: {captured!r}"
    )
    body = info_lines[-1].split("|INFO")[0]
    assert "test-task-id" in body, (
        "log line must include the subagent task id for grep-by-task workflows"
    )
    assert "read_file" in body, "log line must include the tool name"
    assert "memory/MEMORY.md" in body, "log line must include the arguments"


def test_runner_does_not_emit_tool_call_record_unless_invoked(caplog: pytest.LogCaptureFixture) -> None:
    """Smoke: the hook itself produces no logs at import-or-instantiation
    time — only when ``before_execute_tools`` runs.  Guards against a
    future refactor that accidentally adds a logger call in ``__init__``
    (which would spam at every spawn even when no tools were used)."""
    with caplog.at_level(logging.INFO):
        _SubagentHook("idle-task-id")
    assert not [r for r in caplog.records if "idle-task-id" in r.message]
