"""Redact obvious secrets from a log message before it lands on disk.

The on-disk log file is *also* readable by the agent itself (that's the
whole point — bot self-introspection via ``read_file logs/…``) and by
anyone with workspace access.  ``stderr`` keeps the unredacted stream so
``kubectl logs`` stays useful for operators who already have pod access,
but anything the bot can later replay through its own tool surface is
sanitized first.

Best-effort, not security-grade:

- A determined attacker who can craft log messages can still find
  variants this regex set doesn't recognize.
- The goal is to keep accidental secret material — Bearer tokens, basic
  auth, JSON token fields, common provider key prefixes — from sitting
  unmasked in the agent-visible log file.  Treat anything stronger
  (vault auditing, full SIEM redaction) as out of scope here.

Patterns covered:

- ``-u 'user:password'`` style basic auth in curl commands (the
  cluster-ops skill uses this pattern; today's failed cron logged it).
- ``Authorization: Bearer <token>`` headers and bare ``Bearer <token>``.
- JSON-ish ``"token": "..."``, ``"api_key": "..."``, ``"password": "..."``
  fields (and the same with single quotes / no quotes).
- URL query strings: ``?token=…``, ``?api_key=…``, ``?access_token=…``.
- Common provider key prefixes: ``sk-…`` (OpenAI/Anthropic-style),
  ``ghp_…`` / ``ghs_…`` (GitHub tokens), ``xoxb-…`` / ``xoxp-…`` (Slack).
"""

from __future__ import annotations

import re

_MASK = "***"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # curl basic auth: -u 'user:password' / -u user:password
    (
        re.compile(r"(-u\s+['\"]?)([^:\s'\"]+):([^'\"\s]+)(['\"]?)"),
        r"\1\2:" + _MASK + r"\4",
    ),
    # Bearer tokens in or out of an Authorization header.
    (
        re.compile(r"(Bearer\s+)[A-Za-z0-9._\-+/=]+", re.IGNORECASE),
        r"\1" + _MASK,
    ),
    # JSON-ish secret fields: "token": "...", "api_key": "...", etc.
    # The value class excludes whitespace, quotes, commas, closing brace,
    # ``&``, and ``;`` so a URL query like ``token=abc&id=42`` is not
    # over-eaten past the first ampersand (the dedicated URL pattern below
    # handles that case).
    (
        re.compile(
            r"(['\"]?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
            r"token|secret|password|authorization|auth|credential|"
            r"private[_-]?key)['\"]?\s*[:=]\s*['\"]?)"
            r"([^\s'\",}&;]+)",
            re.IGNORECASE,
        ),
        r"\1" + _MASK,
    ),
    # URL query-string credentials: ?token=…&api_key=…
    (
        re.compile(
            r"([?&](?:token|api_key|access_token|key|secret)=)([^&\s]+)",
            re.IGNORECASE,
        ),
        r"\1" + _MASK,
    ),
    # Common provider key prefixes — captured raw without surrounding
    # quotes so they hit string contents loguru rendered literally.
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "sk-" + _MASK),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "ghp_" + _MASK),
    (re.compile(r"ghs_[A-Za-z0-9]{20,}"), "ghs_" + _MASK),
    # ``gho_`` is GitHub's OAuth user-to-server prefix (what ``gh auth login``
    # issues for a personal account, and what cluster-ops uses in basic-auth
    # ``-u user:gho_…`` calls). Adding it here closes the 2026-06-11 leak
    # captured in bibistellar/nanobot#5 where logs/gateway-2026-06-11.log held
    # a full OAuth token unredacted because this prefix was missing.
    (re.compile(r"gho_[A-Za-z0-9]{20,}"), "gho_" + _MASK),
    (re.compile(r"xox[bpars]-[A-Za-z0-9\-]{10,}"), "xox-" + _MASK),
)


def redact_text(text: str) -> str:
    """Return *text* with the patterns above replaced by ``***``.

    Empty / non-string inputs are returned unchanged.  Order matters:
    the curl-basic-auth rule runs first so it catches ``user:secret``
    fragments before the more permissive ``token=value`` rule can match
    the colon-delimited form differently.
    """
    if not isinstance(text, str) or not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
