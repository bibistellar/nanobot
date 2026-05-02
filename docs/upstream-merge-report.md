# 上游合并报告 (upstream/main → origin/main)

**日期**: 2026-05-02
**上游 commit 数**: 41
**涉及文件数**: 78
**我们改动的冲突文件**: 8

---

## 上游主要变更

### 新功能
- Bedrock Converse provider（AWS 原生）
- LongCat provider（OpenAI 兼容）
- sender_id 注入到 runtime context（群聊里 LLM 知道谁在说话）
- origin_message_id（子 agent 回复去重）
- update-setup wizard skill
- Anthropic 长请求自动 fallback 到 stream
- per-session FileState 隔离（修复 #3571）

### Bug 修复
- streaming `</think>` 标签泄漏
- web_fetch URL 清理（markdown backticks）
- DeepSeek reasoning 模式
- 子 agent max_iterations 可配置
- 飞书/Matrix/钉钉/WhatsApp 多个 channel 修复
- SSRF 防护（钉钉）

### 重构
- `try-except` → `contextlib.suppress`
- file_state 从模块级改为 per-loop per-session（contextvars）

---

## 冲突文件详细分析

### 1. `agent/context.py` — 冲突度：极高

**上游改动**:
- 删除了 `_project_history()` 方法（我们的核心改动之一）
- 删除了 `_subagent_status_text()` 函数
- 删除了 `system_to_user_models` 和 `_should_inject_system_to_user()`
- 删除了 `dashscope_client` 参数和所有 Dashscope 集成
- `build_messages()` 移除了 `model`、`task_summary` 参数，新增 `sender_id`
- `_build_runtime_context()` 用 `sender_id` 替换了 `task_summary`
- "Short-term Memory" 改名为 "Memory"

**我们的改动**:
- 新增 `_project_history()` 投影层（subagent_result → user role 渲染）
- 新增 `_subagent_status_text()` 辅助函数
- `build_messages()` 加了 `task_summary` 参数
- `_build_runtime_context()` 加了 `task_summary`（[Background Tasks] 区域）
- Dashscope 动态检索注入 `[Long-term Memory]` 块

**合并策略**: 
- 接受上游删除 `system_to_user_models`（CLIProxyAPI 的 hack，可以去掉）
- **保留** Dashscope 集成（我们的自定义功能，上游不维护）
- **保留** `_project_history()` 和 `_subagent_status_text()`（我们的子 agent 改造）
- 接受 `sender_id` 新增
- **保留** `task_summary`（后台任务状态注入）

### 2. `agent/loop.py` — 冲突度：极高

**上游改动**:
- 删除了 `build_default_tools` 导入和调用，改回内联注册
- 新增 FileStateStore + contextvars 绑定（per-session file state）
- 删除了 `_handle_subagent_result()` 方法，逻辑合并回 `channel=="system"` 分支
- `_persist_subagent_followup()` 改回 `assistant` role + `injected_event` 格式
- `_drain_pending()` 恢复了 300s 子 agent 阻塞等待
- 新增 `_sync_subagent_runtime_limits()` 方法
- SpawnTool 增加 `set_origin_message_id()`
- `build_messages()` 移除 `task_summary`，新增 `sender_id`

**我们的改动**:
- 创建了 `build_default_tools` helper（上游删了）
- 新增 `_handle_subagent_result()` 独立方法
- `_persist_subagent_followup()` 改为 `user` role + 结构化 metadata + O(1) 索引去重
- 删除了 300s 阻塞
- `_drain_pending()` 中子 agent 结果通过模板渲染
- `build_messages()` 传 `task_summary`

**合并策略**:
- 接受内联工具注册（放弃 `build_default_tools`，与上游保持一致）
- 接受 FileStateStore（重要 bugfix #3571）
- **保留** `_handle_subagent_result()` 独立方法（我们的架构改进）
- **保留** `user` role 和结构化 metadata（一等公民设计）
- **保留** 删除 300s 阻塞（核心改动）
- 接受 `_sync_subagent_runtime_limits()`
- 接受 `origin_message_id` 支持
- **保留** `task_summary` 注入

### 3. `agent/runner.py` — 冲突度：高

**上游改动**:
- "after final response" 阶段**恢复了** injection check（`_try_drain_injections`）
- `on_stream_end(resuming=should_continue)` 动态传参
- 新增 "after LLM error" 和 "after empty response" 的 injection
- `suppress()` 替换 try-except

**我们的改动**:
- 删除了 "after final response" 的 injection check
- 删除了 "after LLM error" 和 "after empty response" 的 injection
- `on_stream_end(resuming=False)` 固定为 False

**合并策略**:
- **保留我们的改动** — final response 后不 inject 是正确的设计（streaming 已提交）
- 接受 `suppress()` 代码风格
- 注意上游的 `origin_message_id` 相关改动（如果有的话）

### 4. `agent/subagent.py` — 冲突度：高

**上游改动**:
- 删除了 `build_default_tools` 调用，改回内联工具注册
- 新增 per-subagent FileStates 隔离
- 删除了 `get_task_summary()` 方法
- `_announce_result()` 简化：去掉 `duration_ms`/`token_usage`，改回 `channel="system"` + 预渲染模板
- 新增 `origin_message_id` 参数
- `max_iterations` 可配置（不再硬编码 15）

**我们的改动**:
- 用 `build_default_tools` 共享工具注册
- 新增 `get_task_summary()` 方法
- `_announce_result()` 结构化：`channel=origin["channel"]` + 结构化 metadata
- `duration_ms`/`token_usage` 追踪

**合并策略**:
- 接受内联工具注册 + FileStates 隔离
- **保留** `get_task_summary()`（运行时任务状态）
- **保留** 结构化 `_announce_result()`（一等公民设计）
- 接受 `origin_message_id` 和可配置 `max_iterations`

### 5. `session/manager.py` — 冲突度：中

**上游改动**:
- 删除了 `_SUBAGENT_PAYLOAD_FIELDS` 和 type 字段传递
- 删除了旧格式向后兼容（`injected_event` → `type` 转换）
- `get_history()` 简化，只保留标准字段
- `suppress()` 替换 try-except

**我们的改动**:
- 新增 `_SUBAGENT_PAYLOAD_FIELDS` 和结构化 payload 传递
- 新增向后兼容转换
- `add_message()` 支持 `type` 字段

**合并策略**:
- **保留我们的改动**（子 agent 一等公民需要结构化字段）
- 接受 `suppress()` 代码风格

### 6. `agent/tools/defaults.py` — 冲突度：直接冲突

**上游**: 删除了整个文件
**我们**: 创建了这个文件

**合并策略**: 接受删除，改回内联工具注册（与上游保持一致，减少维护负担）

### 7. `command/builtin.py` — 冲突度：中

**上游改动**:
- 删除了 `cmd_model()` 函数
- 移除 `/model` 命令注册
- `suppress()` 替换 try-except

**我们的改动**:
- 新增 `cmd_model()` 函数（交互式 inline keyboard）
- 新增 `/model` 命令注册

**合并策略**: **保留我们的 `/model` 命令**（上游没有，我们的自定义功能）

### 8. `channels/telegram.py` — 冲突度：中

**上游改动**:
- 简化了命令过滤（去掉了私聊/群聊区分逻辑）
- 删除了 `/model` 从 BotCommand 列表
- 删除了 `_resuming` 流式 buffer 保留逻辑
- 删除了 `_system_buttons` 和 force keyboard 逻辑
- 删除了群聊消息 `[Name @username]:` 前缀
- CallbackQueryHandler 改回条件注册（仅 inline_keyboards 开启时）
- `suppress()` 替换 try-except

**我们的改动**:
- 群聊命令需要 @bot 才响应
- `/model` 加入 BotCommand 列表
- `_resuming` buffer 保留
- `_system_buttons` 强制 keyboard
- 群聊消息前缀（之前的 commit）
- CallbackQueryHandler 始终注册

**合并策略**:
- **保留** 群聊 @bot 过滤（合理改进）
- **保留** `/model` 相关改动
- 评估 `_resuming` — 因为我们删除了 after-final-response injection，`_resuming` 可能不再需要
- **保留** 群聊消息前缀（LLM 需要知道谁在说话）
- 接受 `suppress()` 代码风格

---

## 需要特别注意的上游删除

| 被删除的内容 | 我们是否依赖 | 处理方式 |
|---|---|---|
| `dashscope_memory.py` | 是（核心功能） | **保留**，不跟上游删 |
| `tools/defaults.py` | 是（我们创建的） | 接受删除，改回内联 |
| `_project_history()` | 是（投影层） | **保留** |
| `get_task_summary()` | 是（任务状态） | **保留** |
| `_handle_subagent_result()` | 是（独立方法） | **保留** |
| `system_to_user_models` | 是（CLIProxyAPI hack） | 评估是否还需要 |
| `cmd_model()` | 是（我们加的） | **保留** |

---

## 建议合并步骤

### Phase 1: 无冲突的安全合并
cherry-pick 以下类型的 commit：
- 新 provider（Bedrock、LongCat）
- channel 修复（飞书、Matrix、钉钉、WhatsApp）
- 工具修复（web_fetch URL 清理、think 标签）
- `suppress()` 代码风格（非冲突文件）

### Phase 2: 有冲突但方向一致的合并
- FileStateStore per-session 隔离（接受上游）
- `origin_message_id` 支持（接受上游，补充到我们的代码）
- `sender_id` runtime context 注入（接受上游）
- `max_iterations` 可配置（接受上游）

### Phase 3: 核心冲突文件手动合并
按顺序处理：
1. `context.py` — 接受上游框架 + 保留 Dashscope + 保留投影
2. `subagent.py` — 接受工具注册 + FileStates + 保留结构化 announce
3. `loop.py` — 接受 FileStates + 保留 _handle_subagent_result + 保留去阻塞
4. `runner.py` — 保留我们的 injection 删除
5. `session/manager.py` — 保留结构化字段
6. `telegram.py` — 保留群聊过滤 + /model
7. `builtin.py` — 保留 /model
8. 删除 `tools/defaults.py`

### Phase 4: 测试验证
- 本地测试核心功能（子 agent、记忆、/model）
- CI 测试
- 部署验证

---

## 已确认的合并决策

| 项目 | 决策 | 说明 |
|---|---|---|
| `tools/defaults.py` | 接受删除 | 改回内联注册，与上游保持一致减少维护负担 |
| 群聊消息前缀 `[Name @username]:` | **保留** | 上游删了，但我们需要群聊历史每条消息都带发送者，便于 LLM 区分不同用户 |
| `sender_id` runtime context | **接受上游** | 与消息前缀共存：runtime context 标记当前消息发送者，消息前缀保留历史消息的发送者区分 |
| `system_to_user_models` | **保留** | CLIProxyAPI OAuth 模式会剥离 system prompt，需要这个机制把 system prompt 注入 user message |
| `/model` 命令 | **保留** | 上游没有，我们的自定义功能（交互式 inline keyboard 切换模型） |
| Dashscope 记忆集成 | **保留** | 上游删了 `dashscope_memory.py`，这是我们的自定义长期记忆功能 |
| 子 agent 一等公民架构 | **保留** | user role + 结构化 metadata + _project_history + _handle_subagent_result |
| 300s 阻塞删除 | **保留** | spawn 后立即结束 turn |
| after-final-response injection 删除 | **保留** | streaming 已提交 = turn 结束 |
