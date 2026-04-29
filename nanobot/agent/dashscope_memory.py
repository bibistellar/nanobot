"""Dashscope (Aliyun) Memory API client for long-term memory storage."""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_API_BASE = "https://dashscope.aliyuncs.com/api/v2/apps/memory"


class DashscopeMemoryClient:
    """Client for Aliyun Dashscope Memory API (long-term memory)."""

    def __init__(self, api_key: str, user_id: str = "nanobot_default"):
        self.api_key = api_key
        self.user_id = user_id

    def _request(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Make a request to the Dashscope Memory API."""
        url = f"{_API_BASE}/{endpoint}"
        payload["user_id"] = self.user_id
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.error("Dashscope Memory API error (%s): %s", endpoint, e)
            return {}

    def add_memory(self, messages: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Add memory from conversation messages.

        Args:
            messages: List of {"role": "user"|"assistant", "content": "..."}

        Returns:
            List of memory nodes created/updated.
        """
        result = self._request("add", {"messages": messages})
        nodes = result.get("memory_nodes", [])
        if nodes:
            logger.info(
                "Dashscope: added %d memory node(s) for user %s",
                len(nodes),
                self.user_id,
            )
        return nodes

    def search_memory(self, query: str, max_results: int = 10) -> str:
        """Search memory by semantic query.

        Args:
            query: Natural language query.
            max_results: Max number of results.

        Returns:
            Formatted string of relevant memories for injection into prompt.
        """
        result = self._request(
            "search",
            {"messages": [{"role": "user", "content": query}]},
        )
        nodes = result.get("memory_nodes", [])
        if not nodes:
            return ""

        # Format memories for prompt injection
        lines = []
        for node in nodes[:max_results]:
            content = node.get("content", "")
            if content:
                lines.append(f"- {content}")

        if lines:
            logger.info(
                "Dashscope: recalled %d memories for user %s",
                len(lines),
                self.user_id,
            )
        return "\n".join(lines)

    def list_memory(self) -> list[dict[str, Any]]:
        """List all memories for the current user."""
        result = self._request("list", {})
        return result.get("memory_nodes", [])

    def delete_memory(self, memory_node_id: str) -> bool:
        """Delete a specific memory node."""
        result = self._request("delete", {"memory_node_id": memory_node_id})
        return bool(result)
