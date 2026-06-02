"""Context builder for assembling agent prompts."""

import base64
import mimetypes
import platform
from pathlib import Path
from typing import Any, Mapping, Sequence

from nanobot.agent.dashscope_memory import DashscopeMemoryClient
from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.agent.tools import mcp as mcp_tools
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.apps.cli import utils as cli_app_utils
from nanobot.bus.events import InboundMessage
from nanobot.session.goal_state import goal_state_runtime_lines
from nanobot.utils.helpers import (
    current_time_str,
    detect_image_mime,
    load_bundled_template,
    truncate_text,
)
from nanobot.utils.prompt_templates import render_template

_SUBAGENT_STATUS_TEXT = {
    "completed": "completed successfully",
    "failed": "failed",
    "timeout": "timed out",
    "interrupted": "was interrupted",
    "error": "failed",
    "ok": "completed successfully",
}


def _subagent_status_text(status: str | None) -> str:
    if not status:
        return "completed"
    return _SUBAGENT_STATUS_TEXT.get(status, status)


def session_extra(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return persisted kwargs for turn-attached capabilities."""
    return cli_app_utils.session_extra(metadata) | mcp_tools.session_extra(metadata)


def runtime_lines(state: Any, msg: Any, workspace: Path, *, skip: bool = False) -> list[str]:
    """Return model-visible runtime annotations for turn-attached capabilities."""
    return [
        *cli_app_utils.runtime_lines(msg, workspace, skip=skip),
        *mcp_tools.runtime_lines(
            msg,
            configured_server_names=set(state._mcp_servers),
            connected_server_names=set(state._mcp_stacks),
            skip=skip,
        ),
    ]


async def connect_mcp(state: Any, tools: ToolRegistry) -> None:
    await mcp_tools.connect_missing_servers(state, tools)


async def handle_runtime_control(state: Any, msg: InboundMessage, tools: ToolRegistry) -> bool:
    return await mcp_tools.handle_runtime_control(state, msg, tools)


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"
    _MAX_RECENT_HISTORY = 50
    _MAX_HISTORY_CHARS = 32_000  # hard cap on recent history section size
    _RUNTIME_CONTEXT_END = "[/Runtime Context]"

    def __init__(self, workspace: Path, timezone: str | None = None, disabled_skills: list[str] | None = None,
                 dashscope_client: DashscopeMemoryClient | None = None,
                 system_to_user_models: list[str] | None = None):
        self.workspace = workspace
        self.timezone = timezone
        self.memory = MemoryStore(workspace)
        self.dashscope = dashscope_client
        self.system_to_user_models = system_to_user_models or []
        self.skills = SkillsLoader(workspace, disabled_skills=set(disabled_skills) if disabled_skills else None)

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
        session_summary: str | None = None,
        workspace: Path | None = None,
    ) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills."""
        root = workspace or self.workspace
        parts = [self._get_identity(channel=channel, workspace=root)]

        bootstrap = self._load_bootstrap_files(root)
        if bootstrap:
            parts.append(bootstrap)

        parts.append(render_template("agent/tool_contract.md"))

        # Long-term memory is managed entirely by Dashscope. Relevant
        # memories are retrieved per-message in build_messages() and injected
        # as [Long-term Memory] blocks. MEMORY.md is no longer used.
        if self.dashscope:
            parts.append(
                "# Memory\n\n"
                "You have a long-term memory store. Relevant memories are "
                "automatically retrieved and shown in [Long-term Memory] blocks "
                "before the user's message. You can also proactively search or "
                "add memories using the memory-manage skill when needed.\n\n"
                "For routine task logs (health checks, image upgrades, upstream sync), "
                "append each day's results to a per-day dated file "
                "`memory/task_log/<YYYY-MM-DD>.md` (one file per day, named by that "
                "day's date); never merge multiple days into one file."
            )

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary(exclude=set(always_skills))
        if skills_summary:
            parts.append(render_template("agent/skills_section.md", skills_summary=skills_summary))

        entries = self.memory.read_unprocessed_history(since_cursor=self.memory.get_last_dream_cursor())
        if entries:
            capped = entries[-self._MAX_RECENT_HISTORY:]
            history_text = "\n".join(
                f"- [{e['timestamp']}] {e['content']}" for e in capped
            )
            history_text = truncate_text(history_text, self._MAX_HISTORY_CHARS)
            parts.append("# Recent History\n\n" + history_text)

        if session_summary:
            parts.append(f"[Archived Context Summary]\n\n{session_summary}")

        return "\n\n---\n\n".join(parts)

    def _get_identity(self, channel: str | None = None, workspace: Path | None = None) -> str:
        """Get the core identity section."""
        root = workspace or self.workspace
        workspace_path = str(root.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return render_template(
            "agent/identity.md",
            workspace_path=workspace_path,
            runtime=runtime,
            platform_policy=render_template("agent/platform_policy.md", system=system),
            channel=channel or "",
        )

    @staticmethod
    def _build_runtime_context(
        channel: str | None,
        chat_id: str | None,
        timezone: str | None = None,
        sender_id: str | None = None,
        task_summary: str | None = None,
        supplemental_lines: Sequence[str] | None = None,
    ) -> str:
        """Build untrusted runtime metadata block appended after user content."""
        lines = [f"Current Time: {current_time_str(timezone)}"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        if sender_id:
            lines += [f"Sender ID: {sender_id}"]
        if supplemental_lines:
            lines.extend(supplemental_lines)
        if task_summary:
            lines += ["", "[Background Tasks]", task_summary]
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines) + "\n" + ContextBuilder._RUNTIME_CONTEXT_END

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        if isinstance(left, str) and isinstance(right, str):
            return f"{left}\n\n{right}" if left else right

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [item if isinstance(item, dict) else {"type": "text", "text": str(item)} for item in value]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

    def _load_bootstrap_files(self, workspace: Path | None = None) -> str:
        """Load all bootstrap files from workspace."""
        parts = []
        root = workspace or self.workspace

        for filename in self.BOOTSTRAP_FILES:
            file_path = root / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def _is_template_content(content: str, template_path: str) -> bool:
        """Check if *content* is identical to the bundled template (user hasn't customized it)."""
        tpl = load_bundled_template(template_path)
        if tpl is not None:
            return content.strip() == tpl.strip()
        return False

    def _should_inject_system_to_user(self, model: str) -> bool:
        """Check if the current model requires system prompt in user message."""
        return any(model.startswith(prefix) for prefix in self.system_to_user_models)

    @staticmethod
    def _project_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Project session history into messages safe to send to the LLM.

        Messages tagged with an internal ``type`` are translated to standard
        LLM-API roles. Currently:

        * ``type == "subagent_result"`` → rendered as a ``user``-role message
          using ``agent/subagent_announce.md``. Structured payload fields
          (``task_id``/``status``/``duration_ms``/...) drive the template;
          they are stripped from the projected message.
        * ``type == "subagent_spawned"`` → skipped entirely (the spawn record
          exists for session bookkeeping; the LLM doesn't need to see a
          duplicate of the spawn tool result).
        * Any other ``type`` (or no ``type``) → passed through after
          stripping internal payload fields.
        """
        projected: list[dict[str, Any]] = []
        for message in history:
            msg_type = message.get("type")
            if msg_type == "subagent_spawned":
                continue
            if msg_type == "subagent_result":
                rendered = render_template(
                    "agent/subagent_announce.md",
                    label=message.get("label") or "subagent",
                    status_text=_subagent_status_text(message.get("status")),
                    task=message.get("result_task") or "",
                    result=message.get("content") or message.get("result") or "",
                )
                projected.append({"role": "user", "content": rendered})
                continue
            entry = {
                k: v for k, v in message.items()
                if k not in ContextBuilder._INTERNAL_PROJECTION_FIELDS
            }
            projected.append(entry)
        return projected

    _INTERNAL_PROJECTION_FIELDS = frozenset({
        "type",
        "task_id",
        "parent_task_id",
        "label",
        "status",
        "duration_ms",
        "result",
        "result_task",
        "token_usage",
        "spawned_at",
    })

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        current_role: str = "user",
        sender_id: str | None = None,
        session_summary: str | None = None,
        model: str | None = None,
        task_summary: str | None = None,
        session_metadata: Mapping[str, Any] | None = None,
        current_runtime_lines: Sequence[str] | None = None,
        workspace: Path | None = None,
        runtime_state: Any | None = None,
        inbound_message: Any | None = None,
        skip_runtime_lines: bool = False,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        root = workspace or self.workspace

        # Retrieve long-term memories relevant to the current message
        # (fork feature: shared Dashscope long-term memory across sessions).
        ltm_block = ""
        if self.dashscope and current_message:
            try:
                ltm = self.dashscope.search_memory(query=current_message)
                if ltm:
                    ltm_block = f"\n[Long-term Memory]\n{ltm}\n[/Long-term Memory]\n"
            except Exception:
                pass

        # Supplemental runtime lines: sustained-goal state plus
        # caller-provided lines, surfaced inside the runtime-context block.
        extra = [
            *goal_state_runtime_lines(session_metadata),
        ]
        if runtime_state is not None and inbound_message is not None:
            extra.extend(runtime_lines(runtime_state, inbound_message, root, skip=skip_runtime_lines))
        if current_runtime_lines:
            extra.extend(line for line in current_runtime_lines if line)

        runtime_ctx = self._build_runtime_context(
            channel, chat_id, self.timezone,
            sender_id=sender_id,
            task_summary=task_summary,
            supplemental_lines=extra or None,
        )
        user_content = self._build_user_content(current_message, media)
        system_prompt = self.build_system_prompt(
            skill_names, channel=channel, session_summary=session_summary,
        )

        # For models that go through proxies that strip system prompt (e.g. CLIProxyAPI OAuth),
        # inject the system prompt into the user message instead.
        inject_system = model and self._should_inject_system_to_user(model)

        if inject_system:
            system_prefix = f"[System Context]\n{system_prompt}\n[/System Context]\n\n"
        else:
            system_prefix = ""

        # Merge runtime context, long-term memory, and user content into a
        # single user message to avoid consecutive same-role messages.
        # (Fork keeps the prefix-merge layout — upstream simplified this away
        # but we still need `system_prefix` for CLIProxyAPI-style proxies
        # that strip the system message, and `ltm_block` for Dashscope LTM.)
        if isinstance(user_content, str):
            merged = f"{system_prefix}{runtime_ctx}{ltm_block}\n\n{user_content}"
        else:
            merged = [{"type": "text", "text": f"{system_prefix}{runtime_ctx}{ltm_block}"}] + user_content

        # `_project_history` renders our fork's structured `subagent_result`
        # message type via subagent_announce.md and skips `subagent_spawned`.
        # Upstream dropped this and feeds `history` raw — don't take that,
        # subagent results would surface as wall-of-JSON to the LLM.
        projected_history = self._project_history(history)

        if inject_system:
            messages = [*projected_history]
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                *projected_history,
            ]

        if messages and messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), merged)
            messages[-1] = last
            return messages
        messages.append({"role": current_role, "content": merged})
        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                continue
            raw = p.read_bytes()
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(raw).decode()
            images.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
                "_meta": {"path": str(p)},
            })

        if not images:
            return text
        return images + [{"type": "text", "text": text}]
