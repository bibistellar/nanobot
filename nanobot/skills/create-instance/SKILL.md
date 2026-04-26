---
name: create-instance
description: "Create a new nanobot instance with separate config and workspace. Use when the user wants to set up a new bot for a different channel, persona, or purpose. Triggers on phrases like: 'create a new instance', 'create a new bot', 'set up a new bot', 'add a new channel', '创建新实例', '创建新bot', '帮我创建一个新agent'."
---

# Create Instance

Set up a new nanobot instance with its own config and workspace.

## When to Use

When the user wants to create a new bot instance — typically for a different channel (Telegram, Discord, WeChat, etc.) or with different settings.

## Steps

1. **Collect information from the user** (ask one at a time if not already provided):
   - **Instance name** (required): a short identifier like `telegram-bot`, `discord-bot`
   - **Channel type** (required): e.g. `telegram`, `discord`, `weixin`, `feishu`, `slack`
   - **Model** (optional): LLM model to use. Defaults to the same model as the current instance.

2. **Do NOT collect sensitive information** in the chat (API keys, bot tokens, secrets). API keys are automatically copied from the current instance. Channel-specific tokens (e.g. `telegram.token`) still need to be filled in manually.

3. **Run the creation script** using the exec tool — always pass `--inherit-config` with the current instance's config path so API keys are copied:

```bash
python nanobot/skills/create-instance/scripts/create_instance.py --name <name> --channel <channel> --inherit-config <current-config-path> [--model <model>] [--config-dir <path>]
```

You can find the current config path from the environment or by reading the running config. If unsure, check the `NANOBOT_CONFIG` env var or use `~/.nanobot/config.json` as default.

4. **Report results to the user**:
   - Where the config and workspace were created
   - Which fields they need to fill in (the script will list them)
   - The command to start the instance: `nanobot gateway --config <config-path>`

## Examples

User: "帮我创建一个 Telegram bot"

→ Ask: "给这个实例取个名字？" (if not obvious from context)
→ Ask: "用什么模型？还是用当前的模型？" (optional, can skip if user doesn't care)
→ Run: `python nanobot/skills/create-instance/scripts/create_instance.py --name telegram-bot --channel telegram --inherit-config ~/.nanobot/config.json`
→ Tell user: config created at `~/.nanobot-telegram/config.json`, please fill in `channels.telegram.token`, then start with `nanobot gateway --config ~/.nanobot-telegram/config.json`
