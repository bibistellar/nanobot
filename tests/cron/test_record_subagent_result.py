"""Tests for ``CronService.record_subagent_result``.

These exercise the backwrite path that fixes the long-standing "cron always
reports OK" bug: ``_execute_job`` marks ``last_status = "ok"`` the moment
``on_job`` returns, but for ``agent_turn`` jobs ``on_job`` only spawns a
subagent and returns within milliseconds.  The subagent's real outcome
(possibly minutes later) arrives via the cron-result handler, which must
call ``record_subagent_result`` to overwrite the placeholder.

When the subagent fails we also drop a short stanza into today's task_log
so the day's record isn't silently empty (the success path relies on the
subagent's own ``write_file`` call, which obviously doesn't run when it
crashed).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule


def _make_service(tmp_path: Path, *, with_task_log: bool = True) -> CronService:
    store_path = tmp_path / "cron" / "jobs.json"
    task_log_dir = tmp_path / "memory" / "task_log" if with_task_log else None
    service = CronService(store_path, task_log_dir=task_log_dir)
    service.add_job(
        name="cluster-health-check",
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="UTC"),
        message="check the cluster",
    )
    # Force the action-log merge so the job lands in jobs.json (mirrors the
    # pattern used by ``tests/cron/test_cron_persistence.py``).
    service._running = True
    try:
        service._load_store()
    finally:
        service._running = False
    return service


def _seed_spawn_record(service: CronService) -> str:
    """Simulate what ``_execute_job`` writes at spawn time: a placeholder
    run_history entry plus ``last_status='ok'``.  Returns the job id."""
    store = service._load_store()
    assert store
    job = store.jobs[0]
    from nanobot.cron.service import _now_ms
    from nanobot.cron.types import CronRunRecord

    job.state.last_run_at_ms = _now_ms()
    job.state.last_status = "ok"  # the lie that record_subagent_result fixes
    job.state.last_error = None
    job.state.run_history.append(
        CronRunRecord(
            run_at_ms=job.state.last_run_at_ms,
            status="ok",
            duration_ms=14,  # the misleading spawn-dispatch duration
            error=None,
        )
    )
    service._save_store()
    return job.id


def test_completed_status_keeps_state_ok(tmp_path: Path) -> None:
    """A successful subagent leaves ``last_status`` as ``ok`` and clears any
    prior error; the placeholder duration is overwritten with real wall-clock."""
    service = _make_service(tmp_path)
    job_id = _seed_spawn_record(service)

    service.record_subagent_result(job_id, status="completed")

    store = service._load_store()
    job = next(j for j in store.jobs if j.id == job_id)
    assert job.state.last_status == "ok"
    assert job.state.last_error is None
    assert job.state.run_history[-1].status == "ok"
    # Real wall-clock from spawn time, NOT the seeded ms-level placeholder.
    assert job.state.run_history[-1].duration_ms >= 0


def test_failed_status_writes_error_and_tombstone(tmp_path: Path) -> None:
    """A failed subagent flips ``last_status`` to ``error``, records the
    error text, and appends a short stanza to today's task_log."""
    service = _make_service(tmp_path)
    job_id = _seed_spawn_record(service)

    service.record_subagent_result(
        job_id,
        status="failed",
        error="kubectl not found on PATH",
    )

    store = service._load_store()
    job = next(j for j in store.jobs if j.id == job_id)
    assert job.state.last_status == "error"
    assert job.state.last_error == "kubectl not found on PATH"
    assert job.state.run_history[-1].status == "error"
    assert job.state.run_history[-1].error == "kubectl not found on PATH"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tombstone = tmp_path / "memory" / "task_log" / f"{today}.md"
    assert tombstone.exists(), "expected today's task_log file to exist"
    body = tombstone.read_text(encoding="utf-8")
    assert "Cron Failure" in body
    assert "cluster-health-check" in body
    assert "kubectl not found on PATH" in body


def test_failed_status_without_task_log_dir_does_not_crash(tmp_path: Path) -> None:
    """When ``task_log_dir`` is not configured, failure writeback still
    updates state but the tombstone is silently skipped (graceful degrade)."""
    service = _make_service(tmp_path, with_task_log=False)
    job_id = _seed_spawn_record(service)

    service.record_subagent_result(job_id, status="failed", error="boom")

    store = service._load_store()
    job = next(j for j in store.jobs if j.id == job_id)
    assert job.state.last_status == "error"
    assert job.state.last_error == "boom"
    # No task_log directory should have been created.
    assert not (tmp_path / "memory" / "task_log").exists()


def test_unknown_job_id_is_no_op(tmp_path: Path) -> None:
    """Subagent results for jobs that have been deleted in the meantime
    must not raise — we just drop them on the floor."""
    service = _make_service(tmp_path)
    # Does not raise.
    service.record_subagent_result("nonexistent-job-id", status="failed", error="x")


def test_tombstone_bounded_against_runaway_error(tmp_path: Path) -> None:
    """A multi-kilobyte stacktrace from a runaway subagent must not bloat
    the daily task_log file — the tombstone is meant to be a marker, not
    the full post-mortem."""
    service = _make_service(tmp_path)
    job_id = _seed_spawn_record(service)

    huge_error = "X" * 5000
    service.record_subagent_result(job_id, status="failed", error=huge_error)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tombstone = tmp_path / "memory" / "task_log" / f"{today}.md"
    body = tombstone.read_text(encoding="utf-8")
    # 1000-char cap + ellipsis marker, plus markdown frame ≪ 5000.
    assert len(body) < 1500, f"tombstone too large: {len(body)} bytes"
    assert "…" in body
