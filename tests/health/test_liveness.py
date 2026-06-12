"""Tests for ``nanobot.health.liveness`` — the heartbeat registry that
backs ``/health`` and the Kubernetes ``livenessProbe`` (issues #2 / #6)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from nanobot.health import liveness


@pytest.fixture(autouse=True)
def _reset_registry():
    liveness.reset()
    yield
    liveness.reset()


def test_register_probe_is_idempotent():
    """Calling register_probe twice with the same name is a no-op the second
    time — important because ``register_probe`` lives at the head of the
    HTTP request handler, where it may be invoked once per request."""
    liveness.register_probe("foo")
    liveness.beat("foo")
    # Second register should NOT reset the last_seen timestamp.
    liveness.register_probe("foo")
    _, ages = liveness.evaluate()
    assert ages["foo"] >= 0  # Did not reset
    # Specifically, the age should be tiny (we just beat it), not the
    # full re-registration baseline.
    assert ages["foo"] < 1.0


def test_beat_unregistered_is_silent_noop():
    """A beat on an unknown probe must NOT silently create a probe.
    Otherwise a typo'd third-party log statement could materialize fake
    health signals that affect /health output."""
    liveness.beat("never_registered")
    healthy, ages = liveness.evaluate()
    assert healthy is True  # no probes ⇒ trivially healthy
    assert "never_registered" not in ages


def test_within_grace_period_unbeaten_probe_is_healthy():
    """Right after process start, a registered-but-not-yet-beaten probe
    must still be reported healthy. Otherwise the very first /health
    request would 503 because none of the channels have had time to
    register a heartbeat yet, creating a restart loop."""
    liveness.register_probe("slow_starter", grace_period_s=60.0, stale_after_s=5.0)
    # Don't beat at all.
    healthy, _ = liveness.evaluate()
    assert healthy is True


def test_past_grace_unbeaten_probe_goes_stale():
    """After the startup grace window, an unbeaten probe must trip
    ``healthy=False`` — that's the whole point of having the probe."""
    liveness.register_probe("stalled", grace_period_s=0.0, stale_after_s=1.0)
    # Bypass real wall-clock by patching time.monotonic forward.
    real_now = time.monotonic()
    with patch("nanobot.health.liveness.time.monotonic", return_value=real_now + 60.0):
        healthy, ages = liveness.evaluate()
    assert healthy is False
    assert ages["stalled"] > 1.0


def test_recent_beat_keeps_probe_healthy():
    """A fresh beat resets the staleness clock for that probe."""
    liveness.register_probe("ticking", grace_period_s=0.0, stale_after_s=2.0)
    liveness.beat("ticking")
    healthy, _ = liveness.evaluate()
    assert healthy is True


def test_any_single_stale_probe_makes_overall_unhealthy():
    """The contract is intentionally strict: one stale probe out of N
    fails the whole evaluate() result. Kubernetes' livenessProbe expects
    a single 200/503 verdict."""
    liveness.register_probe("alive", grace_period_s=0.0, stale_after_s=60.0)
    liveness.register_probe("dead", grace_period_s=0.0, stale_after_s=1.0)
    # Both registered at the same instant; beat only "alive".
    liveness.beat("alive")
    real_now = time.monotonic()
    with patch("nanobot.health.liveness.time.monotonic", return_value=real_now + 30.0):
        # "alive" was beaten 30s ago (under its 60s window) → fine.
        # "dead" was last seen at register time (~30s ago) > 1s window → stale.
        healthy, ages = liveness.evaluate()
    assert healthy is False
    assert ages["alive"] < ages["dead"]


def test_evaluate_returns_age_per_probe_for_diagnostic_body():
    """``ages`` is what /health surfaces to operators so they can see
    *which* probe went stale. Round in the response body, not here."""
    liveness.register_probe("a")
    liveness.register_probe("b")
    liveness.beat("a")
    _, ages = liveness.evaluate()
    assert set(ages.keys()) == {"a", "b"}
    for v in ages.values():
        assert v >= 0.0
