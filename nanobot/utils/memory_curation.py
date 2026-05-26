"""Nightly long-term memory curation.

A lightweight, batched LLM pass that reviews ALL stored long-term memory nodes
and selects stale / redundant / superseded ones for pruning. Mirrors the
tool-call pattern used by ``evaluator.py``. Errs toward KEEPING — only nodes the
model explicitly flags (and that exist in the given batch) are returned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider

_CURATION_SYSTEM = """You are curating an AI assistant's long-term memory during \
its nightly "sleep" consolidation.

You are given a batch of stored memory nodes (each line: `id=<id> | <content>`). \
Call `prune_memory` with the ids of nodes that should be DELETED.

PRUNE (delete) nodes that are:
- Transient/operational trivia: one-off events, point-in-time metrics, \
"X happened on date Y", "checked and everything is normal", status snapshots.
- Stale/superseded: outdated counts, schedules, or states that no longer hold; \
facts replaced by a newer node.
- Redundant: duplicates / near-duplicates of another node in the batch (keep the \
most complete one, prune the rest).

KEEP (do NOT delete) durable nodes:
- User identity, preferences, habits, relationships.
- Standing rules, confirmed approaches, configuration decisions meant to persist.
- Project/domain facts that remain true over time.

When unsure, KEEP it — err toward retention. Only return ids present in this batch."""

_PRUNE_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "prune_memory",
            "description": "Select stale/redundant long-term memory nodes to delete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "delete_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "memory_node_id values to delete. Empty if all should be kept.",
                    },
                },
                "required": ["delete_ids"],
            },
        },
    }
]


async def evaluate_prunable(
    nodes: list[dict[str, Any]],
    provider: LLMProvider,
    model: str,
    *,
    batch_size: int = 40,
) -> list[str]:
    """Return the ids of nodes to prune. Batched; failures skip the batch (keep)."""
    to_delete: list[str] = []
    for start in range(0, len(nodes), batch_size):
        batch = nodes[start : start + batch_size]
        valid_ids = {n.get("memory_node_id") for n in batch}
        listing = "\n".join(
            f"id={n.get('memory_node_id')} | {(n.get('content') or '')[:300]}"
            for n in batch
        )
        try:
            resp = await provider.chat_with_retry(
                messages=[
                    {"role": "system", "content": _CURATION_SYSTEM},
                    {"role": "user", "content": f"Memory nodes:\n{listing}"},
                ],
                tools=_PRUNE_TOOL,
                model=model,
                max_tokens=2048,
                temperature=0.0,
            )
            if not resp.should_execute_tools or not resp.tool_calls:
                continue
            ids = resp.tool_calls[0].arguments.get("delete_ids") or []
            # Guard: only accept ids that were actually in this batch.
            to_delete.extend([i for i in ids if i in valid_ids])
        except Exception:
            logger.exception("memory_curation: batch eval failed; keeping this batch")
    return to_delete
