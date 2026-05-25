# 上游合并报告 (upstream/main → main)

**日期**: 2026-05-25
**上游 commit 数**: 268（HKUDS/nanobot main，自上次 41-commit 合并以来）
**合并分支**: `merge/upstream-main-26-05-25`
**git-flagged 冲突文件**: 9（另含若干自动合并产生的语义冲突，见下）
**改动规模**: 326 files, +60547 / −8951

> 上一次合并（41 commits, 2026-05-02）的详细策略见 git 历史中本文件的旧版本。本次合并沿用同一套"保留本地特性、跟随上游重构"的原则。

---

## 上游主要变更（拉入）

- **#3991 apps/MCP 统一重构**：工具系统与 CLI apps 整合到 `nanobot/apps/`，新增 `cli_apps.py`、`exec_session.py`、`apply_patch.py`、`long_task.py`。
- **大量 provider 新增**：Step/StepFun、Zhipu、Novita、APIFree、Skywork、OpenAI Codex 图像、OpenAI apiType/extraBody 配置等。
- **model presets 系统**：`config.model_presets` + `resolve_preset()` + 运行时 preset 快照（`preset_snapshot_loader` / `runtime_model_publisher`）。
- **sustained goals**：`session/goal_state.py` + `/goal` 命令 + runtime context 注入。
- **DM pairing**：`/pairing` 命令 + pairing store。
- **Signal channel**、webui 重构（裁剪 legacy 组件、apply-patch 流式进度、侧栏性能）。
- **测试结构重组**：`test_runner.py` 拆分为 `test_runner_core/errors/fallback/governance/hooks/injections/persistence/...`。
- **tool contract 内化**：`templates/agent/tool_contract.md` 注入 system prompt（取代 `TOOLS.md`）。

## 跟随上游接纳的改动

| 项目 | 处理 |
|---|---|
| apps/MCP 工具加载重构（ToolLoader 插件扫描） | 接纳 |
| model presets 配置基础设施 | 接纳（schema + loop 注入；但 `/model` 命令仍用本地版，见下） |
| `supplemental_lines` / `goal_state_runtime_lines`（sustained goals） | 接纳，与本地 `task_summary` 并存于 runtime context |
| `tool_contract.md` 注入 | 接纳（替换 MEMORY.md 段，但 Memory 段仍用 Dashscope 版） |
| `image_gen_provider_configs()` helper | 接纳（取代本地硬编码 openrouter/aihubmix dict） |
| `sender_id` / `origin_message_id` / 可配置 `max_iterations` / per-subagent FileStates | 接纳 |
| Telegram `goal` / `pairing` 命令 | 接纳并入本地命令路由 |

## 保留的本地特性

| 项目 | 说明 |
|---|---|
| Dashscope 长期记忆 | `dashscope_memory.py` + `[Long-term Memory]` 注入 + Memory 段提示 |
| `system_to_user_models` | CLIProxyAPI OAuth 会剥离 system prompt，需注入到 user message |
| 子 agent 一等公民架构 | `_handle_subagent_result()`、`_project_history()`、结构化 `_announce_result()`、`get_task_summary()` → `[Background Tasks]` |
| runner: 去掉 after-final-response injection | streaming 已提交 = turn 结束（见下"行为分歧"） |
| `/model` 交互式选择器 | 查 `/v1/models`、按钮切任意模型（**非**上游的 preset 切换） |
| Telegram 群聊 `@bot` 命令过滤 | commit 779ba6a8：群里仅响应 `/cmd@bot`，私聊接受裸命令 |
| cron origin/deliver 拆分 + system_events | 本地 cron 重构 |

## 需要决策的冲突（本次新增，已与维护者确认）

1. **ask_user 工具** — 上游 commit `9e15925c` 主动删除（我们未定制）。**决策：跟随上游删除**（`tools/ask.py`、loop.py 中全部引用、`AskUserPrompt.tsx`、`test_ask_user.py`）。
2. **notebook 工具** — 上游用 `apply_patch`+`exec_session` 取代。**决策：跟随上游删除**（`tools/notebook.py`、`test_notebook_tool.py`）。
3. **`/model` 命令碰撞** — 本地交互式选择器 vs 上游 preset 切换。**决策：保留本地选择器**，删除上游 `cmd_model`（preset 版）及重复注册。
4. **runner after-final-response injection** — 合并后保留了本地行为；上游新增的 2 个 `checkpoint2` 测试断言相反。**决策：保留本地行为，删除该 2 个测试**（`test_checkpoint2_injects_after_final_response_with_resuming_stream`、`test_checkpoint2_preserves_final_response_in_history_before_followup`）。

## 自动合并产生的隐性冲突（git 未标记，已手动修复）

- **loop.py**：自动合并残留 ask_user 引用（`ask_user_options_from_messages` / `ask_user_outbound`），已按上游方式清理 `_handle_subagent_result` 出站构造。
- **builtin.py**：拼接产生**重复 `cmd_model` 定义** + 重复 `/model` 注册（Python 静默用第二个），已去重保留本地版。
- **tools/defaults.py**：本地遗留死代码，导入了上游已删除的 `GlobTool`；ToolLoader 会扫描导入它导致启动报异常。**已删除**（上游早已移除，本就该删）。

## 删除的文件（15）

`tools/ask.py`、`tools/notebook.py`、`tools/defaults.py`、`utils/webui_titles.py`（上游迁至 `_webui_turns`）、`templates/TOOLS.md`（→`tool_contract.md`）、`tests/agent/test_ask_user.py`、`tests/agent/test_runner.py`（上游拆分重组）、`tests/tools/test_notebook_tool.py`、6 个 webui legacy 组件（上游重构）。

## 验证状态

- ✅ 无残留冲突标记；所有解决文件 `ruff` 通过。
- ✅ 全树 `ruff` 总数 241 ≤ 上游基线 242（本次解决**未引入**新 lint 问题）。
- ✅ 语义检查：无悬空引用（GlobTool / webui_titles / notebook / ask / build_default_tools 全树 0 处）；webui 无指向已删组件的悬空 import；唯一的本地工具模块 defaults.py 已清理。
- ⚠️ **本地无法运行测试套件**（开发机仅 Python 3.9.6，无 pytest / 项目依赖；nanobot 跑在 k8s）。**webui 亦无法本地构建**（无 bun）。最终验证须经 CI 或部署环境。

## 待办 / 注意

- **builtin.py 孤立死代码**：删除上游 preset 版 `cmd_model` 后，`_model_command_status` / `_model_preset_names` / `_active_model_preset_name` / `_format_preset_names` / `_command_error_message` 成为未使用函数（无害，ruff 不报）。可后续清理。
- **model presets 基础设施保留但 `/model` 不接管**：preset 配置/快照仍在（无害，默认空 dict），但用户切模型走本地交互式选择器。
- **E402 等上游既有 lint 债**：`cli/commands.py` 16 处 E402 等为上游既有（上游同款），非本次引入。
