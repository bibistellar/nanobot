"""Tool to list the chat sessions the bot participates in (for cross-session ops)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import StringSchema, tool_parameters_schema


@tool_parameters(
    tool_parameters_schema(
        channel=StringSchema(
            "Optional channel filter (e.g. 'telegram'). Omit to list every channel."
        ),
        required=[],
    )
)
class SessionsTool(Tool):
    """List the chats/sessions the bot takes part in, for cross-session actions."""

    # Internal/ephemeral session keys to hide from the roster.
    _SKIP_PREFIXES = ("cron", "unified:")

    def __init__(self, sessions: Any):
        self._sessions = sessions

    @property
    def name(self) -> str:
        return "sessions"

    @property
    def description(self) -> str:
        return (
            "List the chats/sessions you take part in — group chats and DMs across channels — "
            "so you can act on a chat other than the current one. Each entry has channel, "
            "chat_id, title, chat_type (e.g. private/group/supergroup), last_active, file, and a "
            "short preview. To act in another chat: send to it with the `message` tool (target "
            "channel + chat_id) — pass chat_id EXACTLY as shown, including any leading '-' "
            "(Telegram group ids are negative). To read its recent conversation, use `read_file` "
            "or `grep` on the given `file` path. Optional `channel` filter (e.g. 'telegram')."
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "sessions", None) is not None

    @classmethod
    def create(cls, ctx: Any) -> "Tool":
        return cls(sessions=ctx.sessions)

    async def execute(self, channel: str | None = None, **_: Any) -> str:
        rows: list[dict[str, Any]] = []
        for s in self._sessions.list_sessions():
            key = s.get("key", "")
            if not key or ":" not in key or key.startswith(self._SKIP_PREFIXES):
                continue
            ch, _, cid = key.partition(":")
            if channel and ch != channel:
                continue
            path = s.get("path") or ""
            rows.append({
                "channel": ch,
                "chat_id": cid,
                "title": s.get("title") or "",
                "chat_type": s.get("chat_type") or "",
                "last_active": s.get("updated_at") or "",
                "file": f"sessions/{Path(path).name}" if path else "",
                "preview": (s.get("preview") or "")[:80],
            })
        if not rows:
            return "No chat sessions found."
        rows.sort(key=lambda r: r.get("last_active") or "", reverse=True)
        return json.dumps(rows, ensure_ascii=False, indent=2)
