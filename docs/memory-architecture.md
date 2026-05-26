# 记忆体系架构（目标 + 现状 + 待修）

> 目标：短期记忆 = 本地会话 history；Dream 负责更新/维护 Dashscope 上的长期记忆；
> 长期记忆每条用户消息触发检索并注入上下文。本文档对齐设计并列出待修项。

## 1. 目标架构

```
┌─ 短期记忆（本地）──────────────────────────────────────┐
│ 会话 history（history.jsonl + 会话消息）                │
│ 上限: ① token 上限  ② 保留天数                          │
│ 触发(到天数 或 超 token): 提取信息 → 长期记忆, 然后【归档, 不删除】│
└──────────────────┬─────────────────────────────────────┘
                   │ Dream（更新 + 维护）
                   ▼
┌─ 长期记忆（Dashscope）─────────────────────────────────┐
│ ① 增量: Dream 把到期/超限的短期信息固结写入             │
│ ② 维护: Dream 周期性评估全量(去重/裁剪过时)            │
│ 检索: 每条用户消息 → search → 注入 [Long-term Memory]    │
│   用户:ABC 【长期记忆:ABC】  用户:DEF 【长期记忆:DEF】   │
└────────────────────────────────────────────────────────┘
```

长期检索机制（context.py 每条消息 `dashscope.search_memory` → `[Long-term Memory]` 块）**符合预期，保持不动。**

## 2. 现状映射

| 目标 | 现状代码 | 状态 |
|---|---|---|
| 短期=本地会话 history | `history.jsonl` + 会话消息；`# Recent History` 窗口(cursor>dream_cursor)注入 prompt | ✅ 存在 |
| 短期 token 上限 | `maybe_consolidate_by_tokens`（`context_window_tokens × consolidation_ratio=0.5`）；会话超预算→`archive()` 摘要→`append_history` | ✅ 存在 |
| 短期 保留天数 | 只有 `session_ttl_minutes`(空闲分钟数压缩) + `compact_history` 按**条数**裁剪 | ❌ **无"天数"维度** |
| 到期/超限→提取到长期 | Dream 读未处理窗口→Dashscope（**cursor bug 刚修**） | ✅ 已修 |
| 提取后【归档不删除】 | `compact_history` 超 `max_history_entries` 直接 `_write_entries(kept)` **丢弃最旧** | ❌ **是删除, 非归档** |
| 长期=Dashscope 增量 | Dream `add_memory` | ✅ 已修 |
| 长期=Dream 维护(去重/裁剪) | 无任何 curation；Dashscope 只增不减 → 520 节点膨胀 | ❌ **缺失** |
| 长期 每条消息检索注入 | context.py `search_memory`→`[Long-term Memory]` | ✅ 符合预期 |

### 半迁移遗留（与目标无关但需收尾）
- `SOUL.md`/`USER.md`：仍读进 prompt，但 Dream 改 Dashscope-only 后**不再维护** → 冻结；`skills/memory/SKILL.md` 仍声称"Dream 管理，勿编辑"（**失真**）。
- `memory/MEMORY.md`：context 不读、Dream 不写 → **孤儿**。
- `/dream-log`、`/dream-restore`：基于本地 git 版本，Dashscope-only 下 git 已冻结 → **失去意义**。

## 3. 待修清单

### A. 短期记忆边界
- **A1 保留天数（新增）**：给 history 增加按天保留——超过 N 天的条目必须先固结进长期再归档。新增配置 `shortTermRetentionDays`。
- **A2 token 上限（已存在）**：复用会话 token 预算即可；明确它作为短期 token 上限的语义。
- **A3 归档而非删除（改）**：`compact_history` 改为把要移出的条目**追加到归档文件**（如 `memory/history.archive.jsonl`）而非丢弃；且只归档**已固结（cursor 之下）**的条目，避免丢未处理数据。

### B. Dream（短期→长期 的桥 + 长期维护）
- **B1 先提取后归档**：保证条目被 Dashscope 固结成功后才允许归档/移出（cursor 推进已修；归档逻辑须只动 ≤cursor 的条目）。
- **B2 curation 评估阶段（新增）**：低频（建议每日/每 N 轮）`list_memory` 全量 → LLM 评估去重/裁剪过时 → `delete_memory`（+ 可选合并后 re-add）；删前留底。**首次运行即清理现有 520 条。**

### C. 长期检索
- 保持现状（用户确认符合预期）。可选微调 `max_results`/检索质量，非必须。

### D. 遗留收尾（架构自洽）
- **D1 SOUL/USER 定位** ✅（已定）：长期记忆归 Dashscope 后，SOUL/USER 改为**人工维护的身份/人设文件**——稳定、读进 prompt、**不归 Dream/curation 管理**。skill 文档须改为"人工维护，可手动编辑"。
- **D2 MEMORY.md 退役**：删文件 + 去除残留读取路径（`get_memory_context` 等）。
- **D3 `/dream-log`、`/dream-restore` 退役或重定向**（本地 git 版本在 Dashscope 体系下无意义）。
- **D4 同步 `skills/memory/SKILL.md`**：改写为新架构，纠正"SOUL/USER/MEMORY 由 Dream 管理"的失真描述。

## 4. 建议实施顺序

1. **A3 + B1**（归档不删除 + 先提取后归档）——保证数据安全，优先。
2. **B2 curation**——根治长期膨胀，首次运行清 520。
3. **A1 保留天数**——补齐短期边界。
4. **D 系列**——退役 MEMORY.md / dream_log-restore，更新 skill 文档，明确 SOUL/USER 定位。

> 注：A/B 集中在 `nanobot/agent/memory.py`（MemoryStore + Dream/Consolidator）+ `config/schema.py`；D 涉及 `command/builtin.py`、`agent/context.py`、`skills/memory/SKILL.md`、模板。
</content>
