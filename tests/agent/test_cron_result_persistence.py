"""The cron decision turn writes the user-facing notification into the
deliver session's history. Without this the next user turn has no context
for the cron message (bot ends up rebuilding state from task_log files or
contradicting what it just said in chat).

Only the *notification* lands in history — the decision prompt and any
intermediate tool calls during the decision turn stay out (cron noise).
"""

from __future__ import annotations

import json

from nanobot.agent.loop import _extract_cron_notification_text


def test_text_return_path() -> None:
    """Plain final_content is the notification."""
    out = _extract_cron_notification_text("All clear ✅", all_msgs=[])
    assert out == "All clear ✅"


def test_strips_whitespace() -> None:
    assert _extract_cron_notification_text("  hi  \n", []) == "hi"


def test_message_tool_path_string_args() -> None:
    """LLM ignored the 'no tools' instruction and called message; the latest
    message-tool ``content`` arg is what the user saw."""
    all_msgs = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "1",
                    "function": {
                        "name": "message",
                        "arguments": json.dumps(
                            {"channel": "telegram", "chat_id": "1", "content": "vault back online"}
                        ),
                    },
                }
            ],
        },
        {"role": "tool", "content": "Message sent to telegram:1"},
    ]
    out = _extract_cron_notification_text(final_content="", all_msgs=all_msgs)
    assert out == "vault back online"


def test_message_tool_path_dict_args() -> None:
    """Some providers return tool_call args as already-parsed dicts."""
    all_msgs = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "message",
                        "arguments": {"content": "ok"},
                    },
                }
            ],
        },
    ]
    assert _extract_cron_notification_text("", all_msgs) == "ok"


def test_final_content_wins_over_message_tool() -> None:
    """If the LLM also produced a final response (rare), prefer that over a
    stray tool call — the final text is what the evaluator gates and the
    OutboundMessage actually carries."""
    all_msgs = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "message",
                        "arguments": json.dumps({"content": "stale tool call"}),
                    },
                }
            ],
        },
    ]
    assert _extract_cron_notification_text("real final answer", all_msgs) == "real final answer"


def test_latest_message_call_wins() -> None:
    """Multiple message-tool calls: last one is the user-visible one."""
    all_msgs = [
        {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "message", "arguments": json.dumps({"content": "first"})}},
            ],
        },
        {"role": "tool", "content": "Message sent"},
        {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "message", "arguments": json.dumps({"content": "second"})}},
            ],
        },
    ]
    assert _extract_cron_notification_text("", all_msgs) == "second"


def test_ignores_non_message_tool_calls() -> None:
    """Other tools (exec, cron list, ...) made during the decision turn must
    not be mistaken for the notification."""
    all_msgs = [
        {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "exec", "arguments": json.dumps({"command": "ls"})}},
            ],
        },
        {"role": "tool", "content": "file1\nfile2"},
        {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "cron", "arguments": json.dumps({"action": "list"})}},
            ],
        },
    ]
    assert _extract_cron_notification_text("", all_msgs) is None


def test_empty_inputs() -> None:
    assert _extract_cron_notification_text(None, []) is None
    assert _extract_cron_notification_text("", []) is None
    assert _extract_cron_notification_text("   ", []) is None


def test_malformed_arguments_skipped() -> None:
    """Garbage in tool_call arguments must not crash the helper."""
    all_msgs = [
        {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "message", "arguments": "not json {{"}},
                {"function": {"name": "message", "arguments": json.dumps({"content": "good"})}},
            ],
        },
    ]
    assert _extract_cron_notification_text("", all_msgs) == "good"
