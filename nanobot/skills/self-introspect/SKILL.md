---
name: self-introspect
description: Read your own runtime logs to diagnose past behavior — cron failures, subagent crashes, tool errors, "why did I do X". The gateway writes its own logs to `logs/gateway-YYYY-MM-DD.log` (JSONL) in the workspace; this skill describes the layout and the queries that pay off.
---

# Self-Introspection

When the user (or you) needs to answer **"why did I just do X?"** —
*Why did that cron fail? What did the subagent actually see? Did the
notification go through?* — read your own runtime log. The gateway
writes a JSON-lines log file to `logs/gateway-YYYY-MM-DD.log` (UTC date)
in the workspace. Each line is a self-contained JSON record.

Do this **before** speculating. If the log has the answer, quoting it is
honest; guessing without checking is the failure mode this skill exists
to prevent.

## File layout

- `logs/gateway-YYYY-MM-DD.log` — today's log (UTC date).
- `logs/gateway-<earlier dates>.log` — kept for **14 days**, then auto-removed.
- One JSON object per line. Schema:
  ```json
  {
    "time": "2026-06-03T01:05:33.123456+00:00",
    "level": "INFO",
    "channel": "telegram",
    "logger": "nanobot.module:function:42",
    "message": "Cron result: job=… status=failed …",
    "exception": "Traceback (most recent call last):\n  …"
  }
  ```
- Secrets (Bearer tokens, basic-auth `-u user:pw`, JSON `token`/`api_key`/
  `password` fields, common provider key prefixes) are masked to `***`
  before write. Seeing `***` in a line is the redaction, not corruption.

## Query toolbox

Default to `exec` with `grep` / `tail` rather than dumping the whole file
into context with `read_file`. The whole file can be megabytes.

### Today's recent activity

```
exec(command="tail -200 logs/gateway-$(date -u +%Y-%m-%d).log")
```

### All errors today

```
exec(command="grep '\"level\":\"ERROR\"' logs/gateway-$(date -u +%Y-%m-%d).log | tail -50")
```

### All warnings today

```
exec(command="grep '\"level\":\"WARNING\"' logs/gateway-$(date -u +%Y-%m-%d).log | tail -50")
```

### A specific cron job — by name or job id

```
exec(command="grep 'cluster-health-check\\|c0c8ab6f' logs/gateway-*.log | tail -50")
```

The cron-result line looks like:
```
Cron result: job=<id> (<name>) status=<failed|completed> deliver=<bool> target=<channel>:<chat_id>
```

### A specific subagent — by task id

The subagent's task id is the 8-char hex in the spawn log line:
```
Spawned subagent [4c555fe8]: cron:cluster-health-check
```
Trace its full lifecycle:
```
exec(command="grep '4c555fe8' logs/gateway-*.log")
```

### Tracebacks with surrounding context

```
exec(command="grep -B 1 -A 30 '\"exception\"' logs/gateway-$(date -u +%Y-%m-%d).log | tail -100")
```

### A specific tool call

```
exec(command="grep 'Tool call' logs/gateway-$(date -u +%Y-%m-%d).log | grep -i web_fetch | tail -20")
```

### Yesterday's failure for comparison

```
exec(command="grep 'status=failed' logs/gateway-$(date -u -d 'yesterday' +%Y-%m-%d).log | tail -20")
```
*(On systems without GNU date, fall back to literal date arithmetic.)*

## Reading individual lines as JSON

When you've narrowed to one or two interesting lines, parse them as JSON
to pick out fields cleanly. `jq` is not assumed to be present — Python
inline works fine and is portable:

```
exec(command="grep '<marker>' logs/gateway-*.log | python3 -c \"import sys, json; [print(json.dumps(json.loads(line), indent=2, ensure_ascii=False)) for line in sys.stdin]\"")
```

## Anti-patterns

- **Don't `read_file logs/gateway-…log` blindly.** Files can be tens of
  MB. Use `grep` / `tail` to slice down first, then `read_file` with
  `offset` if you need a specific line range.
- **Don't quote the redaction marker as the cause.** If you see `***`
  it's the log redaction — not corrupted data, not a missing config, not
  an upstream bug. Just means a secret was there.
- **Don't ignore the timestamp.** Logs from earlier pod incarnations sit
  in the same file (cross-restart history is the whole point of writing
  to PVC). Match the UTC time on the line you cite.

## When to stop reading and act

If you've found the relevant log line(s), summarize what they say in
your own words to the user — quote the JSON line verbatim only when the
exact message text matters (e.g. a specific error string the user
should grep their own systems for). Otherwise paraphrase concisely.

If the log shows the problem is *your own behavior* (you made a wrong
tool call, used a stale skill, invented a fact), say so plainly and
propose a concrete fix. The point of self-introspection is to close
that loop, not to recite trivia.
