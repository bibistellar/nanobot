"""Context builder for assembling agent prompts."""

import base64
import mimetypes
import platform
from contextlib import suppress
from importlib.resources import files as pkg_files
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.dashscope_memory import DashscopeMemoryClient
from nanobot.agent.skills import SkillsLoader
from nanobot.utils.helpers import build_assistant_message, current_time_str, detect_image_mime, truncate_text
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


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
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
    ) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills."""
        parts = [self._get_identity(channel=channel)]

        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        # Short-term memory (local MEMORY.md)
        memory = self.memory.get_memory_context()
        if memory and not self._is_template_content(self.memory.read_memory(), "memory/MEMORY.md"):
            parts.append(f"# Short-term Memory\n\n{memory}")

        # Long-term memory hint (actual retrieval happens in build_messages
        # using the user's current message as the search query).
        if self.dashscope:
            parts.append(
                "# Long-term Memory\n\n"
                "You have a long-term memory store. Relevant memories are "
                "automatically retrieved and shown in [Long-term Memory] blocks "
                "before the user's message. You can also proactively search or "
                "add memories using the memory-manage skill when needed."
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

        return "\n\n---\n\n".join(parts)

    def _get_identity(self, channel: str | None = None) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
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
        channel: str | None, chat_id: str | None, timezone: str | None = None,
        session_summary: str | None = None, sender_id: str | None = None,
        task_summary: str | None = None,
    ) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        lines = [f"Current Time: {current_time_str(timezone)}"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        if sender_id:
            lines += [f"Sender ID: {sender_id}"]
        if task_summary:
            lines += ["", "[Background Tasks]", task_summary]
        if session_summary:
            lines += ["", "[Resumed Session]", session_summary]
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

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def _is_template_content(content: str, template_path: str) -> bool:
        """Check if *content* is identical to the bundled template (user hasn't customized it)."""
        with suppress(Exception):
            tpl = pkg_files("nanobot") / "templates" / template_path
            if tpl.is_file():
                return content.strip() == tpl.read_text(encoding="utf-8").strip()
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
        session_summary: str | None = None,
        model: str | None = None,
        task_summary: str | None = None,
        sender_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        # Retrieve long-term memories relevant to the current message
        ltm_block = ""
        if self.dashscope and current_message:
            try:
                ltm = self.dashscope.search_memory(query=current_message)
                if ltm:
                    ltm_block = f"\n[Long-term Memory]\n{ltm}\n[/Long-term Memory]\n"
            except Exception:
                pass

        runtime_ctx = self._build_runtime_context(
            channel, chat_id, self.timezone,
            session_summary=session_summary,
            sender_id=sender_id,
            task_summary=task_summary,
        )
        user_content = self._build_user_content(current_message, media)
        system_prompt = self.build_system_prompt(skill_names, channel=channel)

        # For models that go through proxies that strip system prompt (e.g. CLIProxyAPI OAuth),
        # inject the system prompt into the user message instead.
        inject_system = model and self._should_inject_system_to_user(model)

        if inject_system:
            system_prefix = f"[System Context]\n{system_prompt}\n[/System Context]\n\n"
        else:
            system_prefix = ""

        # Merge runtime context, long-term memory, and user content into a
        # single user message to avoid consecutive same-role messages.
        if isinstance(user_content, str):
            merged = f"{system_prefix}{runtime_ctx}{ltm_block}\n\n{user_content}"
        else:
            merged = [{"type": "text", "text": f"{system_prefix}{runtime_ctx}{ltm_block}"}] + user_content

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

    def add_tool_result(
        self, messages: list[dict[str, Any]],
        tool_call_id: str, tool_name: str, result: Any,
    ) -> list[dict[str, Any]]:
        """Add a tool result to the message list."""
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result})
        return messages

    def add_assistant_message(
        self, messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list."""
        messages.append(build_assistant_message(
            content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks,
        ))
        return messages
