"""Cron types."""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class CronSchedule:
    """Schedule definition for a cron job."""
    kind: Literal["at", "every", "cron"]
    # For "at": timestamp in ms
    at_ms: int | None = None
    # For "every": interval in ms
    every_ms: int | None = None
    # For "cron": cron expression (e.g. "0 9 * * *")
    expr: str | None = None
    # Timezone for cron expressions
    tz: str | None = None


@dataclass
class CronPayload:
    """What to do when the job runs.

    The payload distinguishes *origin* (where the job was created) from
    *deliver target* (where the result should be sent).  A user can ask
    in a group chat to schedule a task whose results should be delivered
    to their DM — origin captures the former, deliver_* the latter.
    """
    kind: Literal["system_event", "agent_turn"] = "agent_turn"
    message: str = ""
    # Whether to forward the result to a user-visible channel at all.
    deliver: bool = True

    # Where to deliver the result.  When unset, falls back to origin_*.
    deliver_channel: str | None = None
    deliver_chat_id: str | None = None
    deliver_meta: dict = field(default_factory=dict)  # platform routing (e.g. Slack thread_ts)

    # Where the job was originally created.  Used as deliver fallback and as
    # session context for the main agent's deliver decision.
    origin_channel: str | None = None
    origin_chat_id: str | None = None
    origin_session_key: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronPayload":
        """Construct a CronPayload, mapping legacy fields to the new schema.

        Legacy fields ``channel``/``to``/``channel_meta``/``session_key`` are
        mapped into ``origin_*`` (they always described the creation context)
        and copied into ``deliver_*`` as the legacy delivery target.
        """
        data = dict(data)
        legacy_channel = data.pop("channel", None)
        legacy_to = data.pop("to", None)
        legacy_meta = data.pop("channel_meta", None)
        legacy_session = data.pop("session_key", None)

        # Map legacy → origin_* (origin describes creation; we historically
        # used these as both origin and delivery target).
        data.setdefault("origin_channel", legacy_channel)
        data.setdefault("origin_chat_id", legacy_to)
        data.setdefault("origin_session_key", legacy_session)

        # Map legacy → deliver_* so existing jobs keep delivering to the
        # same place after the upgrade.
        data.setdefault("deliver_channel", legacy_channel)
        data.setdefault("deliver_chat_id", legacy_to)
        if legacy_meta is not None and not data.get("deliver_meta"):
            data["deliver_meta"] = legacy_meta

        return cls(**data)

    @property
    def effective_deliver_channel(self) -> str | None:
        """Channel to deliver to, falling back to origin_channel."""
        return self.deliver_channel or self.origin_channel

    @property
    def effective_deliver_chat_id(self) -> str | None:
        """Chat id to deliver to, falling back to origin_chat_id."""
        return self.deliver_chat_id or self.origin_chat_id


@dataclass
class CronRunRecord:
    """A single execution record for a cron job."""
    run_at_ms: int
    status: Literal["ok", "error", "skipped"]
    duration_ms: int = 0
    error: str | None = None


@dataclass
class CronJobState:
    """Runtime state of a job."""
    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: Literal["ok", "error", "skipped"] | None = None
    last_error: str | None = None
    run_history: list[CronRunRecord] = field(default_factory=list)


@dataclass
class CronJob:
    """A scheduled job."""
    id: str
    name: str
    enabled: bool = True
    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="every"))
    payload: CronPayload = field(default_factory=CronPayload)
    state: CronJobState = field(default_factory=CronJobState)
    created_at_ms: int = 0
    updated_at_ms: int = 0
    delete_after_run: bool = False

    @classmethod
    def from_dict(cls, kwargs: dict):
        state_kwargs = dict(kwargs.get("state", {}))
        state_kwargs["run_history"] = [
            record if isinstance(record, CronRunRecord) else CronRunRecord(**record)
            for record in state_kwargs.get("run_history", [])
        ]
        kwargs["schedule"] = CronSchedule(**kwargs.get("schedule", {"kind": "every"}))
        kwargs["payload"] = CronPayload.from_dict(kwargs.get("payload", {}))
        kwargs["state"] = CronJobState(**state_kwargs)
        return cls(**kwargs)


@dataclass
class CronStore:
    """Persistent store for cron jobs."""
    version: int = 1
    jobs: list[CronJob] = field(default_factory=list)
