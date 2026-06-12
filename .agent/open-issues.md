# Open Issues (bibistellar fork operations)

Running ledger of known bugs / gaps for the production deployment.
Update entries inline as they progress; move resolved items to a
"## Resolved" section at the bottom rather than deleting (for blame
trail when they come back).

Symbols: 🔴 = blocking real usage right now; 🟡 = silent bug, real damage but
not user-facing yet; 🟢 = quality / hygiene improvement.

---

## 🔴 P0-1: Telegram polling deadlocks with the main agent

**Symptom**: After certain LLM-error paths in cron decision turns the
entire process goes silent. Pod stays `Running`, `/health` still returns
`{"status":"ok"}`, but no new log lines, no Telegram polling, no
response to user messages.

**Two confirmed occurrences**:
- 2026-06-10 03:08 UTC → 8h dead until manual rollout
- 2026-06-12 02:03 UTC → caught by user complaint within minutes

Both times the last log line was a cron decision turn whose LLM call
returned empty content twice and was suppressed by the evaluator:

```
WARNING  Empty response on turn 0 for telegram:1752172576 (1/2); retrying
WARNING  Empty response on turn 1 ... after 2 retries; attempting finalization
INFO     Cron result suppressed by evaluator (routine): job=87c08446 (nanobot-upstream-sync)
# <-- silence after this line
```

**External evidence the polling really died**:
`getWebhookInfo` showed `pending_update_count = 4` queued at Telegram
while bot was silent → user messages reached Telegram, never got
`getUpdates`-ed.

**Suspected root cause**: somewhere in `_handle_cron_result` ↔
`evaluate_response` ↔ `runner.run` (empty-response retry path) an
`await` is never resolved / a task isn't released, blocking the
shared asyncio loop. Telegram's polling task lives in the same loop.

**Mitigations available right now**:
- Manual `kubectl rollout restart deploy/nanobot-gateway`
  (new pod consumes the backlog on first `getUpdates`).

**Recommended fix path**:
1. Quick win — add a polling-heartbeat check to `/health` (e.g.
   "last `getUpdates` returned within N seconds"), wire a Kubernetes
   `livenessProbe` to it. The pod auto-restarts the moment polling
   freezes, regardless of how the upstream bug actually triggers.
   ~30 lines + a deployment-spec tweak.
2. Real fix — repro the empty-response retry path with a fake
   provider that returns empty content, watch for the lost await.
   Start from `nanobot.agent.runner:run` line ~438 (the "Empty
   response on turn N" branch).

---

## 🔴 P0-2: SSRF whitelist is empty in the gateway process despite config

**Symptom**: `bot.exec("curl http://cliproxyapi...svc.cluster.local/...")`
is blocked with `"internal/private URL detected"` even though
`config.tools.ssrfWhitelist` contains `10.0.0.0/8` and the actual
service IP (10.43.74.78) is squarely in that range.

**Live evidence in production pod**:
```py
import nanobot.security.network as ssrfmod
print(ssrfmod._allowed_networks)     # → []  (BUG: should be 4 networks)
from nanobot.config.loader import load_config
load_config(Path("/data/.nanobot/config.json"))
print(ssrfmod._allowed_networks)
# → [IPv4Network('10.0.0.0/8'), IPv4Network('100.64.0.0/10'), ...]
```

So `configure_ssrf_whitelist` is *capable* of populating the list,
it's just empty in the running gateway process.

**Suspected root cause**: some `load_config()` call (the lazy
config_loader inside `web.py:205`, or one of the WebUI MCP routes)
is hitting a code path that reaches `_apply_ssrf_whitelist` with an
empty list, clobbering the earlier good state. List is module-level
global, last writer wins.

**Mitigation already applied**: configured `tools.image_generation`
with `provider=openai` pointing at `cliproxyapi`. The image-gen tool's
HTTP client does NOT route through `validate_url_target`, so it
completely bypasses the SSRF guard. Bot can now generate images
without `exec`-ing into the SSRF trap.

**Recommended fix path**:
1. Audit every `load_config()` call site (loader.py, web.py:205,
   mcp.py:772, onboard.py:1322, cli/commands.py at 1736/1775,
   webui/mcp_presets_api.py:425/831/930). Find which one passes an
   empty whitelist into `_apply_ssrf_whitelist`. Most likely a code
   path that builds a fresh `Config()` rather than reading from
   disk.
2. Defensive change: in `configure_ssrf_whitelist`, refuse to *shrink*
   the list — only union new CIDRs in. Sketchy global state but
   matches what callers actually expect.

---

## 🔴 P0-3: init script's `sed` to patch HTTPXRequest timeouts has been silently failing for ~4 months

**Symptom**: deployment YAML's gateway container args contain:

```sh
sed -i 's/connect_timeout=30.0/connect_timeout=120.0/g; s/read_timeout=30.0/read_timeout=120.0/g' \
     /usr/local/lib/python3.12/site-packages/nanobot/channels/telegram.py 2>/dev/null
echo "patched telegram timeout to 120s"   # ← cheerfully lying since day 1
```

Container runs as uid 1000, site-packages owned by root → `sed -i` can't
write the temp file → `sed: couldn't open temporary file …: Permission denied`
swallowed by `2>/dev/null`. The `echo` always prints regardless.

**Real consequence**: `connect_timeout`/`read_timeout` have been the
source defaults (30s) the whole time, NOT the 120s the deployment
claims. Plenty enough for normal traffic; not enough when mihomo
egress flutters at startup (e.g. 2026-06-11 07:35 Telegram channel
startup failed with `httpx.ConnectError` because of this).

**Mitigations applied**:
- 2026-06-11: Added `write_timeout=30.0` to source so it ships in
  the image (commit `affbc542`). 1MB photo uploads now work because
  30s is well above the actual median upload time (~10s).
- The lie about "120s" still applies to connect/read until the next
  fix.

**Recommended fix path**:
- Promote all three timeouts to `TelegramConfig` fields (already has
  `pool_timeout: float = 5.0` and `connection_pool_size`), set
  sensible defaults of 60–120s, delete the entire `sed` hack from
  the deployment spec.
- Bonus: drop `2>/dev/null` on any future init-script `sed` so the
  next silent-fail bug screams instead of whispering.

---

## 🟡 P1-1: Log file leaks `gho_…` (GitHub OAuth) tokens unredacted

`nanobot/utils/log_redact.py` covers `ghp_` and `ghs_` prefixes but not
`gho_` (OAuth user-to-server tokens — what `gh auth login` issues for
a personal account). Real example sitting in production today:

```json
{"message": "Tool call: exec({\"command\": \"... -u 'bibistellar:gho_<REDACTED_FOR_DOC>' ...\"})"}
```

(The real token sat unredacted in `logs/gateway-2026-06-11.log` — pulled
it out by hand for this writeup. GitHub Push Protection caught the
first commit attempt that contained the literal value, which is the
same control that should have triggered inside `log_redact.py` and
didn't. Nice meta-confirmation of the bug.)


Bot can read its own log via `read_file` (the whole point of the
sink), so the leak is also a privilege bump for the bot — it can
see a token that ops-secrets.json gates behind file perms.

**Fix**: one line in `_PATTERNS`:

```py
(re.compile(r"gho_[A-Za-z0-9]{20,}"), "gho_" + _MASK),
```

Add tests in `tests/utils/test_log_redact.py`.

---

## 🟡 P1-2: `/health` doesn't detect dead polling (compound with P0-1)

`/health` only confirms the HTTP server thread is up. It returns `ok`
even when Telegram polling is dead, cron timer is hung, or LLM
calls are completely failing.

**Fix**: enrich `/health` to track:
- last successful `getUpdates` timestamp per channel
- last successful cron timer tick
- last successful LLM call (rolling window)

Report degraded if any indicator is older than some threshold. Wire
a Kubernetes `livenessProbe` to the strict version so dead-polling
self-recovers (see P0-1 mitigation #1).

---

## 🟡 P1-3: Bot can rollout-restart its own LLM upstream

2026-06-10 03:07 UTC: `image-upgrade-check` cron subagent ran
`kubectl rollout restart deployment/cliproxyapi` — that's the
cluster's LLM proxy, the same one the bot was using to talk to
Anthropic. Connection error cascade for the next 8 hours, ending
with P0-1.

User clarified that rolling out updated images IS expected behavior
for `image-upgrade-check`. The bug is that the bot has no concept of
"this is my own upstream — defer / batch / coordinate with operator
before bouncing it".

**Fix path**:
- Cluster-ops skill or `image-upgrade-check` prompt: name the set
  of services the gateway depends on (cliproxyapi, mihomo, possibly
  cloudflared if telegram routes through it). Require the bot to
  ASK the operator before restarting any of them, even when the
  upgrade is otherwise routine.
- Alternative belt-and-braces: tag those deployments with a
  `nanobot.io/coordinated-restart=ask` label, modify the cluster-ops
  skill to honor the label.

---

## 🟢 P2-1: `task_log` timestamps not normalized to Beijing time

Cron prompts write `date -u +%Y-%m-%d` (UTC). Operator (CST+8) has
asked for Beijing time so the daily log file boundary matches local
"today". Minor — bot's pretty consistent about UTC labelling inside
the file — but the filename date is what most queries grep on.

**Fix**: update all four cron prompts to use `TZ=Asia/Shanghai date +%Y-%m-%d`
for the filename, keep UTC inside section headers (or also switch
those to CST — operator's call).

---

## 🟢 P2-2: GHCR image tag is `:latest`, not pinned to commit sha

Pod spec uses `ghcr.io/bibistellar/nanobot:latest`. If anyone (or
anything) pushes a different layer under that tag mid-rollout, the
new pod could end up running unrelated code. Low probability since
only CI pushes `:latest`, but no-cost mitigation.

**Fix**: pin to `ghcr.io/bibistellar/nanobot@sha256:…` (immutable
digest) in the deployment spec, update by templated PR after each
build, or use a GitOps tool (Argo Image Updater) that does the
substitution automatically.

---

## 🟢 P2-3: `image_generation` tool not yet smoke-tested end to end

We configured `tools.image_generation = {enabled: true, provider:
openai, model: gpt-image-2, api_base: cliproxyapi...}` on 2026-06-12,
restarted the pod, confirmed the tool registers (21 tools, including
`generate_image`). Have NOT actually asked the bot to generate an
image yet. Until that happens we don't know if:
- cliproxyapi's `/v1/images/generations` route is functional via
  this API key
- `gpt-image-2` is the right model name (`gpt-image-1`? `dall-e-3`?
  bot self-reported using `gpt-image-2` previously but cliproxyapi
  may route it differently)
- save_dir is writable
- The full path returns image bytes that the message tool can
  attach (composes with the send_photo write_timeout fix)

**Fix**: just ask the bot to draw something, then read the
resulting `generate_image` tool log + the message-tool delivery.
First failure mode found here gets re-filed.

---

## 🟢 P2-4: Cron prompts could use a "this is a check, not an action" hard line

`image-upgrade-check` ran for 34 minutes on 2026-06-11 partly because
it dove from "check upgrades" into "apply upgrades" without a hard
break. While "apply is expected" (per operator), the unboundedness is
what bit us. Today's prompt has good structure but no explicit
"after step N return control to operator if any restart targets the
gateway's own dependencies" clause.

Combine with P1-3 (cluster-coordinated restart label).

---

## 📋 Companion / context

These aren't "bugs" but live alongside the issues above and shape
fix choices:

- **Memory of past quick-fixes is in `~/.claude/projects/.../memory/MEMORY.md`** —
  load it before assuming what's normal (mihomo proxy quirks, deploy
  workflow, telegram bot can't DM first, etc.).
- **Subagent fail-tolerance** (`fail_on_tool_error=False` since
  commit `0f917afe`) means LLM tool-name slips like `Grep` vs `grep`
  no longer kill a cron. Confirmed working today (2026-06-12 02:01:58
  → 02:02:10 self-correction).
- **`task_log` failure tombstones** (`9fd7f897` / `08c50815`) only
  fire when cron service hears back from `_handle_cron_result`. If
  the deadlock in P0-1 also blocks `_handle_cron_result` (likely),
  the tombstone path is unreachable too. Logged here so we remember
  to check during P0-1 repro.

---

## Resolved

(empty — move entries up here with date + commit sha when fixed)
