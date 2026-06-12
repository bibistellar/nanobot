"""Redact rules for the agent-visible log file.

The log file under ``logs/gateway-YYYY-MM-DD.log`` is read by the agent
itself through ``read_file`` / ``grep`` / ``exec tail``.  Without these
rules, a single ``Tool call: exec({"command": "curl -u user:secret …"})``
log line would put the password in front of the agent next time it
self-introspects.  ``stderr`` (and therefore ``kubectl logs``) keeps the
unredacted stream because operators with shell access already see secrets.
"""

from __future__ import annotations

import pytest

from nanobot.utils.log_redact import redact_text


@pytest.mark.parametrize(
    "raw, must_not_contain, must_contain",
    [
        # curl basic auth — exactly the form cluster-ops uses.
        (
            "Tool call: exec({\"command\": \"curl -u 'bibistellar:gho_supersecret123' "
            "https://api.github.com/repos/bibistellar/_k3s\"})",
            "gho_supersecret123",
            "bibistellar:***",
        ),
        # Bare basic auth without quotes.
        (
            "curl -u bibistellar:s3cret https://example.com",
            "s3cret",
            "bibistellar:***",
        ),
        # Bearer header.
        (
            "Authorization: Bearer abcdef1234567890",
            "abcdef1234567890",
            "***",
        ),
        # JSON-ish token field — common provider response logging.
        (
            '{"api_key": "sk-veryReal1234567890abcdef", "model": "claude"}',
            "sk-veryReal1234567890abcdef",
            "***",
        ),
        # JSON-ish password field.
        (
            '{"password": "hunter2", "user": "alice"}',
            "hunter2",
            "***",
        ),
        # URL query string credential.
        (
            "WebFetch error for https://api.example.com?token=abc123def456ghi789",
            "abc123def456ghi789",
            "token=***",
        ),
        # OpenAI/Anthropic-style key prefix sitting raw in a message.
        (
            "Loaded provider key sk-ant-12345678901234567890",
            "sk-ant-12345678901234567890",
            "sk-***",
        ),
        # GitHub PAT raw.
        (
            "Using github token ghp_1234567890abcdefghijABCDEF12345678",
            "ghp_1234567890abcdefghijABCDEF12345678",
            "ghp_***",
        ),
        # GitHub OAuth user-to-server token raw — what `gh auth login`
        # issues and what cluster-ops cron jobs use in basic-auth headers.
        # See bibistellar/nanobot#5 for the production leak that prompted
        # adding this pattern.
        (
            "curl -u bibistellar:gho_AbCdEfGh1234567890IjKlMnOpQrStUvWxYz",
            "gho_AbCdEfGh1234567890IjKlMnOpQrStUvWxYz",
            "gho_***",
        ),
        # Slack bot token raw.
        (
            "Slack webhook xoxb-1234567890-abcdefghijklm",
            "xoxb-1234567890-abcdefghijklm",
            "xox-***",
        ),
    ],
)
def test_redact_replaces_known_secret_shapes(raw, must_not_contain, must_contain):
    redacted = redact_text(raw)
    assert must_not_contain not in redacted, (
        f"redaction must remove the secret payload\n  raw: {raw!r}\n  out: {redacted!r}"
    )
    assert must_contain in redacted, (
        f"redaction must leave a recognizable marker so the agent can tell "
        f"a secret was here\n  raw: {raw!r}\n  out: {redacted!r}"
    )


def test_redact_preserves_innocent_text():
    """Plain operational messages must not be mangled — overzealous redaction
    would make the log unreadable to the agent."""
    msg = (
        "Cron result: job=c0c8ab6f (cluster-health-check) status=failed "
        "deliver=False target=telegram:1752172576"
    )
    assert redact_text(msg) == msg


def test_redact_handles_empty_and_non_string_input():
    """The filter wraps every log record's message — passing None or "" must
    not raise."""
    assert redact_text("") == ""
    assert redact_text(None) is None  # type: ignore[arg-type]


def test_redact_handles_multiple_secrets_in_one_line():
    """Real log lines often contain several secret-shaped tokens at once
    (e.g. curl with -u AND a query-string token).  All of them must go."""
    raw = (
        "exec curl -u 'admin:pw123' "
        "'https://api.example.com/v1/things?token=longtoken1234567890&id=42' "
        "-H 'Authorization: Bearer abcdef1234567890'"
    )
    redacted = redact_text(raw)
    assert "pw123" not in redacted
    assert "longtoken1234567890" not in redacted
    assert "abcdef1234567890" not in redacted
    # Innocent positional bits stick around.
    assert "admin:***" in redacted
    assert "id=42" in redacted
