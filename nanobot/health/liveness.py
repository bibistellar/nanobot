"""Liveness heartbeat registry.

Background: the gateway has gone silent twice in a week (2026-06-10 / 2026-06-12)
because the main agent's LLM-error-recovery path deadlocked the asyncio loop or
the Telegram polling coroutine. ``/health`` used to return ``ok`` regardless,
because all it confirmed was that the HTTP server thread itself was running.
That made the Kubernetes liveness contract useless: the pod could be functionally
dead for 8 hours while the orchestrator happily showed it as healthy.

This module gives ``/health`` something *real* to check. Each long-running
coroutine that the gateway depends on registers itself once at startup, then
calls :func:`beat` on each tick of its outer loop. ``/health`` calls
:func:`evaluate`, which fails the request as soon as any registered probe goes
silent for too long.

The contract is intentionally simple: any single stale probe → ``healthy=False``.
Caller (``_health_server`` in ``nanobot.cli.commands``) maps that to HTTP 503,
which the Kubernetes ``livenessProbe`` interprets as "restart the pod".

Note: this catches *whole-coroutine* death (the outer keepalive task stops
yielding, so :func:`beat` is never called). It does NOT catch the case where a
sub-task inside PTB's polling stack stalls while the outer keepalive loop keeps
running — that would require hooking into PTB internals. The Telegram outer
keepalive is a useful proxy, not a perfect one. Documented here so the next
debug session doesn't waste time looking for "polling is stuck but heartbeat
is fresh" as a contradiction; it's a known limitation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock


@dataclass
class _Probe:
    last_seen_monotonic: float
    grace_period_s: float
    stale_after_s: float


_LOCK = Lock()
_REGISTRY: dict[str, _Probe] = {}
_STARTED_AT_MONOTONIC = time.monotonic()


def register_probe(
    name: str,
    *,
    grace_period_s: float = 30.0,
    stale_after_s: float = 60.0,
) -> None:
    """Register a heartbeat source.

    Idempotent: calling twice with the same name keeps the existing
    ``last_seen_monotonic`` rather than resetting it. ``grace_period_s``
    is measured from process start — within that window, a probe with no
    beat is still considered healthy (gives slow channels time to come up).
    ``stale_after_s`` is the alert threshold after the grace window.
    """
    with _LOCK:
        if name in _REGISTRY:
            return
        _REGISTRY[name] = _Probe(
            last_seen_monotonic=time.monotonic(),
            grace_period_s=grace_period_s,
            stale_after_s=stale_after_s,
        )


def beat(name: str) -> None:
    """Record a fresh heartbeat for *name*.

    Unregistered names are a no-op (we don't want a typo'd third-party
    log line to silently materialize new probes). Concurrent-safe.
    """
    now = time.monotonic()
    with _LOCK:
        probe = _REGISTRY.get(name)
        if probe is None:
            return
        probe.last_seen_monotonic = now


def evaluate() -> tuple[bool, dict[str, float]]:
    """Return ``(healthy, ages)``.

    ``ages`` maps probe name to seconds since its last beat (or since
    process start, for probes that have never beaten yet). Useful as
    the body of the ``/health`` response so operators can see *which*
    probe went silent.

    ``healthy`` is False iff at least one probe is past its
    ``stale_after_s`` AND the process is past its overall grace
    window. During grace, everything is reported healthy regardless,
    so a slow Telegram channel boot doesn't trigger a self-restart loop.
    """
    now = time.monotonic()
    age_since_start = now - _STARTED_AT_MONOTONIC

    with _LOCK:
        snapshot = {name: (probe.last_seen_monotonic, probe.grace_period_s, probe.stale_after_s)
                    for name, probe in _REGISTRY.items()}

    ages: dict[str, float] = {}
    healthy = True
    for name, (last_seen, grace_s, stale_after_s) in snapshot.items():
        age = now - last_seen
        ages[name] = age
        if age_since_start < grace_s:
            # Process still in startup grace — ignore stale check entirely.
            continue
        if age > stale_after_s:
            healthy = False
    return healthy, ages


def reset() -> None:
    """Test-only — drop all registered probes and rebase the startup clock."""
    global _STARTED_AT_MONOTONIC
    with _LOCK:
        _REGISTRY.clear()
    _STARTED_AT_MONOTONIC = time.monotonic()
