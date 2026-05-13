"""Registry for cron jobs with ``kind="system_event"``.

System events are internal jobs that bypass the agent loop entirely.
Each handler is registered by ``job.name`` and invoked directly by the
cron firing path when the job's payload kind is ``"system_event"``.

Usage::

    from nanobot.cron.system_events import register_system_event

    async def _run_dream(agent, job):
        await agent.dream.run()

    register_system_event("dream", _run_dream)
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

SystemEventHandler = Callable[[Any, Any], Awaitable[None]]

_HANDLERS: dict[str, SystemEventHandler] = {}


def register_system_event(name: str, handler: SystemEventHandler) -> None:
    """Register a system_event handler keyed by ``job.name``.

    Re-registering an existing name silently replaces the previous handler
    so callers can re-import the module without stale handlers.
    """
    _HANDLERS[name] = handler


def get_system_event(name: str) -> SystemEventHandler | None:
    """Return the handler for ``name`` or ``None`` if unregistered."""
    return _HANDLERS.get(name)


def clear_system_events() -> None:
    """Test-only helper: reset the registry."""
    _HANDLERS.clear()
