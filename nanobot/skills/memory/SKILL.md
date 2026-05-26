---
name: memory
description: Two-layer memory — local short-term history + Dashscope long-term, organized by Dream.
always: true
---

# Memory

## Structure

- **Short-term memory** — `memory/history.jsonl`: an append-only log of recent
  conversation turns. Recent unprocessed entries are surfaced as "Recent
  History" in context. Not loaded wholesale — search it with the `grep` tool.
- **Long-term memory** — Dashscope: durable facts. Relevant memories are
  retrieved automatically per message and shown in `[Long-term Memory]` blocks
  before the user's message. Use the memory-manage flow to search/add when needed.
- `SOUL.md` (personality/tone) and `USER.md` (user profile) — **human-maintained
  identity files.** They are read into context but are NOT auto-managed; edit
  them directly when the user asks you to change persona or profile.

## How Dream organizes memory

Dream runs once a day (and on demand via `/dream`) as a single "organize" pass:

1. **Consolidate** recent short-term history into Dashscope long-term memory.
2. **Archive** short-term entries that are consolidated and older than the
   retention window out of the active log into `memory/history.archive.jsonl`
   (archived, never deleted).
3. **Curate** long-term: deduplicate and prune stale/superseded nodes (pruned
   contents are backed up to `memory/dashscope_pruned.jsonl`).

## Search past events

`memory/history.jsonl` is JSONL — each line has `cursor`, `timestamp`, `content`.

- Broad search: `grep(pattern="keyword", path="memory", glob="*.jsonl", output_mode="count", case_insensitive=true)`
- Exact lines: add `output_mode="content"` plus `context_before`/`context_after`.
- Literal timestamps/JSON: `fixed_strings=true`. Page with `head_limit`/`offset`.
- Older archived turns live in `memory/history.archive.jsonl`.

## Notes

- `/dream` manually triggers the organize pass and reports a summary
  (consolidated / pruned counts).
- Long-term memory is curated automatically; you don't manually edit it.
