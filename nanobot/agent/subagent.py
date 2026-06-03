"""Subagent manager for background task execution."""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.runner import AgentRunner, AgentRunSpec
from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.file_state import FileStates
from nanobot.agent.tools.loader import ToolLoader
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.self import MyTool
from nanobot.security.workspace_access import (
    WorkspaceScope,
    bind_workspace_scope,
    reset_workspace_scope,
    workspace_sandbox_status,
)
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import AgentDefaults, ToolsConfig
from nanobot.providers.base import LLMProvider
from nanobot.utils.prompt_templates import render_template

# `ContextBuilder` is only used at typing time; importing it eagerly would
# create a cycle because context.py already imports SubagentManager-adjacent
# helpers transitively.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanobot.agent.context import ContextBuilder


@dataclass(slots=True)
class SubagentStatus:
    """Real-time status of a running subagent."""

    task_id: str
    label: str
    task_description: str
    started_at: float          # time.monotonic()
    phase: str = "initializing"  # initializing | awaiting_tools | tools_completed | final_response | done | error
    iteration: int = 0
    tool_events: list = field(default_factory=list)   # [{name, status, detail}, ...]
    usage: dict = field(default_factory=dict)          # token usage
    stop_reason: str | None = None
    error: str | None = None


class _SubagentHook(AgentHook):
    """Hook for subagent execution — logs tool calls and updates status."""

    def __init__(self, task_id: str, status: SubagentStatus | None = None) -> None:
        super().__init__()
        self._task_id = task_id
        self._status = status

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.debug(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tool_call.name, args_str,
            )

    async def after_iteration(self, context: AgentHookContext) -> None:
        if self._status is None:
            return
        self._status.iteration = context.iteration
        self._status.tool_events = list(context.tool_events)
        self._status.usage = dict(context.usage)
        if context.error:
            self._status.error = str(context.error)


class SubagentManager:
    """Manages background subagent execution."""

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        max_tool_result_chars: int,
        model: str | None = None,
        tools_config: ToolsConfig | None = None,
        restrict_to_workspace: bool = False,
        disabled_skills: list[str] | None = None,
        max_iterations: int | None = None,
        max_concurrent_subagents: int | None = None,
        llm_wall_timeout_for_session: Callable[[str | None], float | None] | None = None,
        context_builder: "ContextBuilder | None" = None,
        runtime_state: Any | None = None,
    ):
        defaults = AgentDefaults()
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.tools_config = tools_config or ToolsConfig()
        self.max_tool_result_chars = max_tool_result_chars
        self.restrict_to_workspace = restrict_to_workspace
        self.disabled_skills = set(disabled_skills or [])
        self.max_iterations = (
            max_iterations
            if max_iterations is not None
            else defaults.max_tool_iterations
        )
        self.max_concurrent_subagents = (
            max_concurrent_subagents
            if max_concurrent_subagents is not None
            else defaults.max_concurrent_subagents
        )
        self.runner = AgentRunner(provider)
        self._llm_wall_timeout_for_session = llm_wall_timeout_for_session
        # ``context_builder`` and ``runtime_state`` keep subagents in lockstep
        # with the main agent's identity layer: the same ContextBuilder is
        # reused to produce the static prompt parts (bootstrap files, always-
        # skill text, memory rules, tool contract) and to retrieve LTM blocks
        # for the task text, and ``runtime_state`` is the main AgentLoop
        # instance that the read-only ``my`` tool needs to inspect.  Both
        # default to ``None`` so bare unit tests can still construct a
        # SubagentManager without dragging in the full agent stack — the code
        # falls back to the legacy ``subagent_system.md`` lite template in
        # that case.
        self.context_builder = context_builder
        self.runtime_state = runtime_state
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_statuses: dict[str, SubagentStatus] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}

    def _subagent_tools_config(self) -> ToolsConfig:
        """Build a ToolsConfig scoped for subagent use.

        Propagates the main agent's ``my`` config so a deployment that has
        explicitly disabled ``my`` is honored on the subagent side too.
        The actual MyTool instance the subagent registers is forced into
        read-only mode in ``_build_tools`` regardless of ``my.allow_set``.
        """
        return ToolsConfig(
            exec=self.tools_config.exec,
            web=self.tools_config.web,
            my=self.tools_config.my,
            restrict_to_workspace=self.restrict_to_workspace,
        )

    def _build_tools(
        self,
        workspace: Path | None = None,
        tools_config: ToolsConfig | None = None,
    ) -> ToolRegistry:
        """Build an isolated subagent tool registry via ToolLoader."""
        root = self.workspace if workspace is None else workspace
        registry = ToolRegistry()
        cfg = tools_config if tools_config is not None else self._subagent_tools_config()
        ctx = ToolContext(
            config=cfg,
            workspace=str(root.resolve()),
            # Bus is required for the `message` tool: without it MessageTool
            # falls back to send_callback=None and every send returns
            # "Error: Message sending not configured" — silently breaking any
            # subagent task whose job is to deliver something.
            bus=self.bus,
            file_state_store=FileStates(),
            workspace_sandbox=workspace_sandbox_status(
                restrict_to_workspace=cfg.restrict_to_workspace,
                workspace=root,
            ),
        )
        ToolLoader().load(ctx, registry, scope="subagent")
        # ``my`` is registered manually because it carries a non-discoverable
        # back-reference to the main AgentLoop (its runtime state).  We give
        # the subagent a READ-ONLY variant — it can ``check`` the same
        # configuration the main agent sees, but cannot mutate model /
        # iteration limits / scratchpad (that would let one parallel branch
        # silently rewrite the main agent's settings).
        if (
            self.runtime_state is not None
            and cfg.my.enable
            and not registry.has("my")
        ):
            registry.register(
                MyTool(runtime_state=self.runtime_state, modify_allowed=False)
            )
        return registry

    def set_provider(self, provider: LLMProvider, model: str) -> None:
        self.provider = provider
        self.model = model
        self.runner.provider = provider

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        origin_message_id: str | None = None,
        temperature: float | None = None,
        disabled_tools: set[str] | None = None,
        result_metadata: dict[str, Any] | None = None,
        workspace_scope: WorkspaceScope | None = None,
    ) -> str:
        """Spawn a subagent to execute a task in the background.

        ``disabled_tools`` removes the named tools from the subagent's
        registry after default loading — used e.g. by cron to prevent the
        scheduled job from creating new cron jobs (recursion).

        ``result_metadata`` is merged into the announcement InboundMessage's
        metadata so callers can carry job/job-like context through to the
        main agent that handles the result.
        """
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id, "session_key": session_key}

        status = SubagentStatus(
            task_id=task_id,
            label=display_label,
            task_description=task,
            started_at=time.monotonic(),
        )
        self._task_statuses[task_id] = status

        bg_task = asyncio.create_task(
            self._run_subagent(
                task_id,
                task,
                display_label,
                origin,
                status,
                origin_message_id,
                temperature=temperature,
                disabled_tools=disabled_tools,
                result_metadata=result_metadata,
                workspace_scope=workspace_scope,
            )
        )
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            self._task_statuses.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)

        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        status: SubagentStatus,
        origin_message_id: str | None = None,
        temperature: float | None = None,
        disabled_tools: set[str] | None = None,
        result_metadata: dict[str, Any] | None = None,
        workspace_scope: WorkspaceScope | None = None,
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)

        async def _on_checkpoint(payload: dict) -> None:
            status.phase = payload.get("phase", status.phase)
            status.iteration = payload.get("iteration", status.iteration)

        try:
            root = workspace_scope.project_path if workspace_scope is not None else self.workspace
            cfg = None
            if workspace_scope is not None:
                cfg = self._subagent_tools_config()
                cfg.restrict_to_workspace = workspace_scope.restrict_to_workspace
            tools = self._build_tools(workspace=root, tools_config=cfg)
            # Fork addition: cron uses this to drop the `cron` tool from the
            # cron-spawned subagent's registry so a scheduled job can't
            # schedule a new job (recursion guard). Apply after _build_tools
            # so the workspace_scope branch above still loads the full set
            # before unregister.
            for tool_name in disabled_tools or ():
                tools.unregister(tool_name)
            system_prompt = self._build_subagent_prompt(workspace=root)
            # Mirror the main agent: prepend any Dashscope LTM hits that
            # match the task text.  Without this the subagent would lose
            # all "institutional knowledge" (deploy workflow, known proxy
            # quirks, prior incident notes) that the main agent gets for
            # free on every turn.  ``fetch_ltm_block`` already swallows
            # errors and returns "" on no-result / missing config.
            ltm_block = (
                self.context_builder.fetch_ltm_block(task)
                if self.context_builder is not None
                else ""
            )
            user_content = f"{ltm_block}{task}" if ltm_block else task
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

            sess_key = origin.get("session_key")
            llm_timeout = (
                self._llm_wall_timeout_for_session(sess_key)
                if self._llm_wall_timeout_for_session
                else None
            )
            token = bind_workspace_scope(workspace_scope) if workspace_scope is not None else None
            try:
                result = await self.runner.run(AgentRunSpec(
                    initial_messages=messages,
                    tools=tools,
                    model=self.model,
                    temperature=temperature,
                    max_iterations=self.max_iterations,
                    max_tool_result_chars=self.max_tool_result_chars,
                    hook=_SubagentHook(task_id, status),
                    max_iterations_message="Task completed but no final response was generated.",
                    error_message=None,
                    fail_on_tool_error=True,
                    checkpoint_callback=_on_checkpoint,
                    session_key=sess_key,
                    workspace=root,
                    llm_timeout_s=llm_timeout,
                ))
            finally:
                if token is not None:
                    reset_workspace_scope(token)
            status.phase = "done"
            status.stop_reason = result.stop_reason

            duration_ms = int((time.monotonic() - status.started_at) * 1000)
            token_usage = dict(result.usage) if result.usage else None

            if result.stop_reason == "tool_error":
                status.tool_events = list(result.tool_events)
                await self._announce_result(
                    task_id=task_id, label=label, task=task,
                    result=self._format_partial_progress(result),
                    origin=origin, status="failed",
                    duration_ms=duration_ms, token_usage=token_usage,
                    origin_message_id=origin_message_id,
                    extra_metadata=result_metadata,
                )
            elif result.stop_reason == "error":
                await self._announce_result(
                    task_id=task_id, label=label, task=task,
                    result=result.error or "Error: subagent execution failed.",
                    origin=origin, status="failed",
                    duration_ms=duration_ms, token_usage=token_usage,
                    origin_message_id=origin_message_id,
                    extra_metadata=result_metadata,
                )
            else:
                final_result = result.final_content or "Task completed but no final response was generated."
                logger.info("Subagent [{}] completed successfully", task_id)
                await self._announce_result(
                    task_id=task_id, label=label, task=task,
                    result=final_result, origin=origin, status="completed",
                    duration_ms=duration_ms, token_usage=token_usage,
                    origin_message_id=origin_message_id,
                    extra_metadata=result_metadata,
                )

        except Exception as e:
            status.phase = "error"
            status.error = str(e)
            duration_ms = int((time.monotonic() - status.started_at) * 1000)
            logger.exception("Subagent [{}] failed", task_id)
            await self._announce_result(
                task_id=task_id, label=label, task=task,
                result=f"Error: {e}", origin=origin, status="failed",
                duration_ms=duration_ms, token_usage=None,
                origin_message_id=origin_message_id,
                extra_metadata=result_metadata,
            )

    async def _announce_result(
        self,
        *,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
        duration_ms: int,
        token_usage: dict[str, int] | None,
        origin_message_id: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Publish the subagent result back to the main agent's bus.

        The result is published as an InboundMessage carrying structured
        metadata under ``metadata["type"] == "subagent_result"``. The main
        agent's dispatch path branches on that type to route the result
        through the proper subagent-result handling (persistence + projection
        at LLM-context-build time) instead of treating it as a regular user
        message.

        ``content`` carries the raw subagent output text; the formatted
        announcement that the LLM eventually sees is rendered later by
        ``ContextBuilder._project_history`` from the structured fields, so
        we don't store pre-rendered text on disk.
        """
        override = origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}"
        metadata: dict[str, Any] = {
            "type": "subagent_result",
            "task_id": task_id,
            "label": label,
            "status": status,
            "duration_ms": duration_ms,
            "result_task": task,
            "token_usage": token_usage,
            "origin_message_id": origin_message_id,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        msg = InboundMessage(
            channel=origin["channel"],
            sender_id="subagent",
            chat_id=origin["chat_id"],
            content=result,
            session_key_override=override,
            metadata=metadata,
        )

        await self.bus.publish_inbound(msg)
        logger.debug(
            "Subagent [{}] announced result to {}:{} (status={}, duration={}ms)",
            task_id, origin['channel'], origin['chat_id'], status, duration_ms,
        )

    @staticmethod
    def _format_partial_progress(result) -> str:
        completed = [e for e in result.tool_events if e["status"] == "ok"]
        failure = next((e for e in reversed(result.tool_events) if e["status"] == "error"), None)
        lines: list[str] = []
        if completed:
            lines.append("Completed steps:")
            for event in completed[-3:]:
                lines.append(f"- {event['name']}: {event['detail']}")
        if failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {failure['name']}: {failure['detail']}")
        if result.error and not failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {result.error}")
        return "\n".join(lines) or (result.error or "Error: subagent execution failed.")

    def _build_subagent_prompt(self, workspace: Path | None = None) -> str:
        """Build a subagent system prompt aligned with the main agent's
        identity layer.

        When a ``ContextBuilder`` is wired in (production path: AgentLoop
        passes ``self.context``), the prompt is the main agent's static
        identity stack — identity / bootstrap files (AGENTS / SOUL /
        USER) / tool contract / memory rules / always-skill **full text** /
        skills index — prefixed with a short subagent preamble explaining
        the scope/recursion/read-only-my caveats.  This keeps subagents
        consistent with the main agent's knowledge ("the cluster-ops
        repo is bibistellar/_k3s" used to live only in the always-skill
        text the subagent never saw; now it does).

        When no ContextBuilder is available (bare unit-test construction),
        falls back to the legacy ``subagent_system.md`` lite template so
        existing tests don't need to bring up the full agent stack.
        """
        from nanobot.agent.context import ContextBuilder
        from nanobot.agent.skills import SkillsLoader

        root = workspace or self.workspace
        time_ctx = ContextBuilder._build_runtime_context(None, None)

        if self.context_builder is not None:
            preamble = render_template(
                "agent/subagent_preamble.md",
                time_ctx=time_ctx,
                workspace=str(root),
            )
            static_parts = self.context_builder.build_static_prompt_parts(
                workspace=root,
            )
            return "\n\n---\n\n".join([preamble, *static_parts])

        # Legacy fallback path — no ContextBuilder available.
        skills_summary = SkillsLoader(
            root,
            disabled_skills=self.disabled_skills,
        ).build_skills_summary()
        return render_template(
            "agent/subagent_system.md",
            time_ctx=time_ctx,
            workspace=str(root),
            skills_summary=skills_summary or "",
        )

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        tasks = [self._running_tasks[tid] for tid in self._session_tasks.get(session_key, [])
                 if tid in self._running_tasks and not self._running_tasks[tid].done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    def get_task_summary(self, session_key: str) -> str:
        """Return a human-readable summary of tasks for a session.

        Used by runtime context injection so the LLM always knows what
        background tasks are running.
        """
        task_ids = self._session_tasks.get(session_key, set())
        if not task_ids:
            return ""
        lines = []
        for tid in sorted(task_ids):
            status = self._task_statuses.get(tid)
            if not status:
                continue
            elapsed = int(time.monotonic() - status.started_at)
            if status.phase in ("done", "error"):
                state = "completed" if status.phase == "done" else f"failed: {status.error or 'unknown'}"
            else:
                state = f"running ({elapsed}s, phase: {status.phase}, iteration: {status.iteration})"
            lines.append(f"- [{status.label}] (id: {tid}) {state}")
        return "\n".join(lines)

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)

    def get_running_count_by_session(self, session_key: str) -> int:
        """Return the number of currently running subagents for a session."""
        tids = self._session_tasks.get(session_key, set())
        return sum(
            1 for tid in tids
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        )
