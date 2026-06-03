"""Agent-visible log sink + retention pruning.

The gateway writes a JSONL log file under ``<workspace>/logs/`` so the
agent can self-introspect via ``read_file`` / ``grep`` (see the
``self-introspect`` skill). These tests pin the on-disk shape and the
retention behavior so a future loguru change can't silently break
either.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from nanobot.cli.commands import (
    _add_agent_visible_log_sink,
    _prune_stale_gateway_logs,
)


# ---------------------------------------------------------------------------
# Retention pruning
# ---------------------------------------------------------------------------


def _make_dated_log(log_dir: Path, days_ago: int) -> Path:
    """Create an empty ``gateway-YYYY-MM-DD.log`` dated *days_ago* days ago."""
    target_date = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
    path = log_dir / f"gateway-{target_date.strftime('%Y-%m-%d')}.log"
    path.write_text("placeholder\n", encoding="utf-8")
    return path


def test_prune_keeps_files_inside_retention_window(tmp_path: Path) -> None:
    """Files dated within the retention window must survive — pruning is
    not allowed to wipe the active log or anything still useful."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    today = _make_dated_log(log_dir, days_ago=0)
    yesterday = _make_dated_log(log_dir, days_ago=1)
    edge = _make_dated_log(log_dir, days_ago=13)  # last day inside the 14-day window

    _prune_stale_gateway_logs(log_dir, retention_days=14)

    for p in (today, yesterday, edge):
        assert p.exists(), f"in-window file unexpectedly deleted: {p.name}"


def test_prune_removes_files_outside_retention(tmp_path: Path) -> None:
    """Anything older than ``retention_days`` is unlinked. This is what
    prevents the logs/ directory from growing unbounded — loguru's own
    rotation is not active when we use a custom-callable sink."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    stale = _make_dated_log(log_dir, days_ago=15)
    way_old = _make_dated_log(log_dir, days_ago=120)

    _prune_stale_gateway_logs(log_dir, retention_days=14)

    assert not stale.exists()
    assert not way_old.exists()


def test_prune_ignores_unrelated_files(tmp_path: Path) -> None:
    """Pruner must only touch ``gateway-YYYY-MM-DD.log`` — neighbouring
    files in the same directory (e.g. an operator dropped a note, or
    another component shares logs/) stay untouched."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    junk = log_dir / "README.txt"
    junk.write_text("not a log\n", encoding="utf-8")
    misnamed = log_dir / "gateway-not-a-date.log"
    misnamed.write_text("nope\n", encoding="utf-8")

    _prune_stale_gateway_logs(log_dir, retention_days=14)

    assert junk.exists()
    assert misnamed.exists()


def test_prune_no_op_on_missing_directory(tmp_path: Path) -> None:
    """If ``logs/`` doesn't exist yet (first ever boot before the sink
    creates it), pruning must silently no-op rather than raise."""
    missing = tmp_path / "never_created"
    # Should not raise.
    _prune_stale_gateway_logs(missing, retention_days=14)


# ---------------------------------------------------------------------------
# Sink shape
# ---------------------------------------------------------------------------


def _read_today_log(log_dir: Path) -> list[dict]:
    """Read today's log file and return parsed JSONL records."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = log_dir / f"gateway-{today}.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_sink_writes_jsonl_with_expected_fields(tmp_path: Path) -> None:
    """Each line must be one self-contained JSON object with the fields
    the self-introspect skill teaches the agent to grep for: time, level,
    channel, logger, message."""
    sink_id = _add_agent_visible_log_sink(tmp_path)
    try:
        logger.info("test marker line")
        # enqueue=True dispatches writes to a thread; ``complete()`` waits
        # for the queue to drain so the assertion below sees the file.
        logger.complete()
    finally:
        # Only remove the sink we added — ``logger.remove()`` with no
        # arg would nuke stderr too and starve every later test.
        logger.remove(sink_id)

    records = _read_today_log(tmp_path / "logs")
    assert records, "sink must produce at least one line for our marker"
    last = records[-1]
    for key in ("time", "level", "channel", "logger", "message"):
        assert key in last, f"sink record missing required key {key!r}: {last!r}"
    assert last["level"] == "INFO"
    assert "test marker line" in last["message"]


def test_sink_redacts_secrets_before_disk_write(tmp_path: Path) -> None:
    """The whole point of the redact pass is that on-disk content the
    agent later reads via ``read_file`` doesn't contain raw secrets.
    A log message with a curl basic-auth string must hit disk with the
    password masked."""
    sink_id = _add_agent_visible_log_sink(tmp_path)
    try:
        logger.info("Tool call: exec curl -u 'admin:super-secret-pw-9999' https://api.example.com")
        logger.complete()
    finally:
        logger.remove(sink_id)

    records = _read_today_log(tmp_path / "logs")
    assert records, "expected at least one persisted record"
    on_disk = records[-1]["message"]
    assert "super-secret-pw-9999" not in on_disk, (
        f"secret made it to disk verbatim: {on_disk!r}"
    )
    assert "admin:***" in on_disk
