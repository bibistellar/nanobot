---
name: cross-session
description: Act on and read other chats/sessions (e.g. do something in a group from a DM).
always: true
---

# Cross-session operation

Each chat is a separate session (`<channel>:<chat_id>`) with its own conversation
context — so by default you only see the current chat. But you CAN find, act on,
and read other chats you take part in. When the user asks you to do something in
another chat (e.g. "post this in the group", "what did they say in the family
group?"), do NOT say you don't know it — look it up:

1. **Find the chat** — call the `sessions` tool to list the chats you participate
   in (group chats + DMs). Match the user's description to an entry by its
   `title` (or `chat_type`, e.g. group/supergroup vs private when titles are
   missing) to get its `channel`, `chat_id`, and `file`.
2. **Act in it** — use the `message` tool with the target `channel` + `chat_id`
   to send there. Pass `chat_id` EXACTLY as the roster shows it, character for
   character — Telegram group ids are negative (e.g. `-5111011186`); never drop
   the leading `-` or the `-100` supergroup prefix, or the send fails with
   "Chat not found".
3. **Read its recent conversation** — use `read_file` or `grep` on the entry's
   `file` (e.g. `sessions/telegram_-100123.jsonl`). Each line is a JSON record;
   grep `"role"` and read `content`. Note: large tool outputs are offloaded
   (shown as `[tool output persisted]` pointers), so you'll see the conversation
   text but not full tool results.

Long-term memory (Dashscope) is already shared across all sessions, so durable
facts learned in one chat surface in others automatically. Use the steps above
when you need the *live/recent* context or to take an action in another chat.
