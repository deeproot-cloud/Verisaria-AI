# HANDOVER.md — RPG World Engine

> 写给下一位接手的 AI Agent。本文档是 `llm-rpg-world-engine-design.md`（v3.0，3907 行）的**工程执行摘要**，聚焦代码现状、已知债务和继续开发时最可能踩的坑。

---

## 1. 项目快照

| 字段 | 内容 |
|---|---|
| 定位 | 通用 LLM 驱动 RPG 世界运行时（非聊天前端、非交互小说） |
| 语言 | Python 3.14.4 |
| 核心依赖 | `pydantic==2.13.4`, `pytest==9.0.3` |
| 依赖原则 | **零额外依赖**：OllamaProvider 直接用 `urllib`；无 `requests`, `httpx`, `sqlalchemy` 等 |
| 源码 | `src/rpg_demo/`（30 个 `.py` 模块） |
| 测试 | `tests/`（44 个测试文件，**623 passed / 3 skipped**） |
| 设计文档 | `llm-rpg-world-engine-design.md`（架构总纲） |
| 启动 | `./play.sh`（Ollama） / `./play.sh --fake`（确定性 FakeLLM，无外部模型） |
| 测试命令 | `./.venv/bin/python -m pytest tests/` |

---

## 2. 架构原则（A1–A5）

这些原则是红线，改动前务必通读相关模块。

| 原则 | 含义 | 代码体现 |
|---|---|---|
| **A1** 输入层只解析意图，不改状态 | `IntentParser` 输出 `ParsedIntent`，不碰 `WorldState` | `intent.py` |
| **A2** LLM 只提案，不直接改状态 | Arbiter 输出 `ValidatedOutcome`，经 `WorldCore` 落盘 | `arbiter.py`, `world.py` |
| **A3** Event 不可变 | `Event` 是 Pydantic 模型，生成后只读；世界变更走 `accepted_state_changes` | `schemas.py` |
| **A4** `WorldState` 是唯一真相源 | 所有叙事、NPC 认知、UI 渲染都从 `WorldState` + `Event Log` 推导 | `world.py` |
| **A5** NPC 无上帝视角 | NPC 只能看到同 location/zone 的事件；跨 location 事件被 `response_generator.py` 过滤 | `response_generator.py`, `subjectivity.py` |

---

## 3. 核心模块地图

### 3.1 中央协调：`cli.py`（~1450 行）
`GameSession` 是心脏。每 tick 的执行顺序：

```
Player raw input
  → IntentParser.parse() → ParsedIntent | ClarificationRequest
    → InteractionService.process_intent() → Action
      → RulesEngine.resolve() → Resolution
        → if requires_arbiter: LLMArbiter.arbitrate()
        → else: ActionQueue.submit(action)
          → NPCActionGenerator.generate_actions()
            → RulesEngine.resolve() (per NPC)
              → ActionQueue.submit(NPC actions)
          → ActionQueue.resolve_with_combat() → Events + combat_actions
            → if combat_actions: CombatEngine.resolve_tick()
            → ResponseGenerator.generate() → narrative string
              → WorldCore.tick_advance()
                → CampaignDriverManager.tick()
                → Reflection / Agenda / Belief updates
```

**Clarification 循环**：`IntentParser` 可返回 `ClarificationRequest`；玩家输入数字/文本后，`_resolve_clarification()` 合并到原输入并重新 `parse(..., skip_ambiguity_check=True)`。

### 3.2 意图解析：`intent.py`（~630 行）
- `IntentParser.parse(raw_text, actor_id, tick, world, context, skip_ambiguity_check)`
- LLM → schema 校验 → `_resolve_target_ref()`（exact → bare → substring → Levenshtein ≤2）
- Ambiguity cleanup + `_build_clarification()`（智能选项：代词推断、中文硬编码映射如 `卫兵→npc.guard_b`、substring 匹配）
- `CoherenceChecker` 内联：movement/physical/social/combat 各做前置校验

### 3.3 交互层：`interaction.py`（~300 行）
- `ActionComposer.compose(intent, tick, seq, world)`：intent → Action；movement 支持 entity target 自动解析为 `location_id`
- `InteractionService`：管理 clarification loop + conversation session attachment
- `_attach_conversation_session()`：player speech 时自动 `start_session()` 或 `process_turn()`

### 3.4 世界状态：`world.py` + `schemas.py`
- `WorldCore`：状态管理 + `commit_action()` + `tick_advance()` + Event Log
- `_update_state_from_action()`：**目前只实现了 movement**（更新 `location_id` / `zone_id` / occupant_ids）。其他 physical 动作的状态变更靠 combat engine 或 arbiter 直接写 `accepted_state_changes`。
- `schemas.py`：Pydantic 定义所有不可变数据契约。`Action` 的 `model_validator` 强制校验各 `action_type` 的必填 `params`。

### 3.5 战斗系统：`combat.py`（~660 行）
- `CombatEngine`：回合制状态机
- 关键：`event.location_id = session.location_id`（非硬编码 `"combat"`）；`canonical_facts` 携带 `"actor_id"`
- Combat event 在 `response_generator.py` 中优先用 `facts["actor_id"]` 替代 `event.actor_id`（避免显示 `combat_system`）

### 3.6 NPC 运行时：`npc_runtime.py`（~450 行）
- `NPCActionGenerator`：每 tick 为每个 NPC 生成一个 Action
- **Conversation 模式**：`in_conversation=True` → **100% speech**；`_pick_speech_content()` 读取 `ConversationSession.shared_context["last_content"]` 做上下文分类（question/greeting/statement）并返回对应模板
- Idle 模式：附近有人 → 20% move, 20% speech, 30% look, 30% wait
- `_make_movement(target_id=None)`：避免 ActionQueue 冲突误判

### 3.7 对话管理：`conversation.py`（~300 行）
- `ConversationManager`：session 生命周期 + topic extraction + pronoun resolution
- `process_turn()` 更新 `turn_count`, `last_activity_tick`, `topic_stack`, `shared_context.last_speaker/last_content`
- `timeout_ticks=10`；session 状态：`active` / `interrupted` / `resumed` / `concluded` / `abandoned`

### 3.8 响应生成：`response_generator.py`（~370 行）
- 按 player location 过滤非 player 事件（A5 位置隔离）
- Combat event 特殊处理：用 `facts["actor_id"]` 显示行动者
- 模板系统：支持 `speech` / `movement` / `combat_*` / `combat_fail` / `combat_dodge_stance` 等多语言模板

### 3.9 其他支撑模块
| 模块 | 职责 |
|---|---|
| `rules.py` | `RulesEngine`：物理/移动规则；`wait` 在 physical 白名单中；`_resolve_movement` 支持 `to_zone=None` |
| `action_queue.py` | 冲突检测 + 优先级排序（type_pri, actor_pri, action_id）+ `_is_invalidated`（含 location 存在性检查） |
| `coherence.py` | 验证 intent 与世界状态一致性；movement 目标为 entity 时取其 `location_id`；combat 豁免 `defend`/`dodge`/`flee` 的 target 要求 |
| `memory.py` | `MemoryStore`（三层记忆：working/short_term/long_term）+ `BeliefEngine` |
| `subjectivity.py` | `SubjectivityService`：按 actor 过滤事件，模拟 NPC/Player 的感知边界 |
| `observation.py` | `ObservationDispatcher`：把事件分发到相关角色的记忆中 |
| `arbiter.py` | `LLMArbiter`：LLM 提案 → `ValidatedOutcome` → state changes + narrative |
| `campaign.py` | `CampaignDriverManager`：驱动叙事节奏、场景推进 |
| `agenda.py` | `AgendaService`：玩家动态目标推断 |
| `persistence.py` | 完整状态序列化：world + subjectivity + agenda + combat + conversation + campaign + npc_runtime |
| `replay.py` | 固定 seed + fixture 的全链路回放验证 |
| `llm.py` | `LLMOrchestrator` + `OllamaProvider`（urllib）+ `FakeLLMProvider`（确定性，测试用） |
| `hint_system.py` | 基于当前世界状态给玩家提供上下文提示 |
| `pack_editor.py` | 内容包（JSON）的 CRUD 编辑器 |
| `validator.py` | `ValidatedOutcome` 校验器 |
| `formatter.py` | ANSI escape code 格式化输出 |

---

## 4. 关键数据流（单 tick 详解）

### 4.1 常规动作（speech / movement / look）
```
Player: "去酒馆"
  → IntentParser.parse()
    → ParsedIntent(action_type=MOVEMENT, target_id="tavern")
      → ActionComposer.compose()
        → Action(params={to_location: "tavern"})
          → RulesEngine.resolve()
            → can_execute=True
              → ActionQueue.submit(player_action)
                → _collect_npc_actions()
                  → NPCActionGenerator.generate_actions(world, tick, active_convs, memory_store, **conversation_manager**)
                    → per NPC: RulesEngine.resolve() → submit
                → ActionQueue.resolve_with_combat(world)
                  → sorted_actions → conflicts → execute → Events
                    → ResponseGenerator.generate(events, world, player_id)
                      → narrative string
                        → WorldCore.tick_advance()
```

### 4.2 战斗动作
```
Player: "攻击卫兵"
  → IntentParser.parse() → COMBAT intent
    → RulesEngine.resolve() → can_execute=True
      → ActionQueue.submit() → resolve_with_combat() → combat_actions 非空
        → cli._handle_combat_action(action)
          → CombatEngine.resolve_tick() → combat events
            → narrative + tick_advance()
```

### 4.3 Clarification 路径
```
Player: "打他"
  → IntentParser.parse() → ambiguities=["他"]
    → _build_clarification() → ClarificationRequest(question="'他' 是指 ... ?", options=[...])
      → REPL 显示选项，等待玩家输入
        → player: "1"
          → _resolve_clarification() → ("打他（指npc.guard_b）", skip_ambiguity=True)
            → 重新 parse(..., skip_ambiguity_check=True)
              → CoherenceChecker 兜底验证
                → 正常执行
```

---

## 5. 已知问题 & 技术债务

| 优先级 | 问题 | 影响 | 建议修复方向 |
|---|---|---|---|
| **高** | `RulesEngine` 与 `WorldCore` 双轨 event 生成 | `rules.resolve()` 的 `summary`/`canonical_facts` 在常规路径中被 `world.commit_action()` 覆盖；两份逻辑可能不一致 | 统一入口：所有状态变更都走 `WorldCore._update_state_from_action()`，RulesEngine 只输出 `can_execute` + `requires_arbiter` |
| **高** | `_update_state_from_action()` 仅支持 movement | steal/attack/loot 等 physical 动作无内置状态变更；combat 靠 combat engine，其他靠 arbiter 直接写 `accepted_state_changes` | 扩展 `_update_state_from_action()` 的物理动作分支，或定义标准 physical 动作 schema |
| **中** | LLM 解析偶尔不稳定 | 相同输入连续 parse 可能偶尔失败（LLM 特性） | 增加 retry + fallback 模板；或关键路径引入规则 fallback |
| **中** | NPC 对话模板较简单 | `_pick_speech_content()` 用硬编码模板回应；无 LLM 生成 | Phase-2 可接入轻量 LLM 生成对话，但保持 NPC 无上帝视角约束 |
| **低** | ActionQueue 排序中 physical < movement < speech | 导致 NPC wait 事件显示在 player movement 之前 | 可调整为按 `submit` 顺序为主，type_pri 为辅 |

---

## 6. 测试策略

- **运行**：`./.venv/bin/python -m pytest tests/ -q`
- **状态**：623 passed, 3 skipped（replay 2 个 + campaign driver 1 个）
- **模式**：
  - 单元测试覆盖各子系统（`test_*` 对应各模块）
  - CLI 集成测试（`test_cli.py`, `test_combat_action_queue.py` 等）
  - 全链路持久化测试（`test_full_persistence.py`）
  - 确定性回放测试（`test_replay.py`）
- **添加测试**：修改任何子系统后，优先在对应 `test_*.py` 中加测试；CLI 行为变更需更新 `test_cli.py`。

---

## 7. 开发备忘

### 7.1 内容包格式
- 内容包是 JSON 文件，位于 `fixtures/content_packs/`
- 当前默认：`fixtures/content_packs/valid_frontier_town.json`
- 加载器：`campaign_loader.py`
- **完整创作指南见 `CONTENT_GUIDE.md`**（字段含义、信息不对称/world_book 分层、NPC 自主交互/home 锚定、campaign driver 触发、踩坑速查）
- 三个范例包：`valid_frontier_town`（最小）、`lively_market_town`（涌现交互）、`frostgate_watchpost`（派系+分层真相）

### 7.2 Save 格式
- Save 目录：`saves/`
- 包含完整运行时状态，可跨 session 加载：`./play.sh load <save_id>`

### 7.3 FakeLLMProvider 行为
- `--fake` 模式下 LLM 调用走 `FakeLLMProvider`
- 基于输入文本的 substring 匹配返回确定性 JSON
- 关键 substring 映射定义在 `llm.py` 中
- **用途**：无 Ollama 时快速验证玩法链路；regression 测试

### 7.4 常用 Debug 命令（游戏内）
```
/history     最近事件
/inspect <id> 查看实体状态
/belief <id>  查看信念
/memory <id>  查看记忆
/agenda       玩家议程
/combat       战斗状态
/map          地图
```

---

## 8. 最近重大变更（若你拿到的是此版本）

- **NPC 对话上下文感知**：`npc_runtime.py` + `cli.py`；conversation 中 NPC 100% speech，根据玩家上一条消息分类（question/greeting/statement）选择回应模板。
- **Clarification 无限循环修复**：`parse(..., skip_ambiguity_check=True)` + 智能选项生成。
- **Target 解析增强**：Levenshtein ≤2 typo 容错 + 中文硬编码映射 + substring 匹配。
- **ResponseGenerator 位置过滤**：非 player 事件仅在 `event.location_id == player_loc` 时可见。
- **Movement 支持 Entity target**：`ActionComposer._build_params` 自动解析；`to_zone` 可选。
- **Combat 链路修复**：event `location_id` 同步；`_handle_combat_action` 使用 `action.actor_id` 非硬编码 `player_id`。
- **18+ 零散 bug fixes**：`action_queue` invalidated 检查、`rules.py` wait 白名单、world movement 后 zone 同步等。

---

## 9. 接手后建议的阅读顺序

1. **5 分钟**：本文件 + `src/rpg_demo/schemas.py`（理解数据契约）
2. **15 分钟**：`src/rpg_demo/cli.py` 的 `_execute_tick()` 方法（理解主循环）
3. **10 分钟**：`llm-rpg-world-engine-design.md` 的第 1–3 章（产品愿景 + 架构总览）
4. **按需深入**：修改哪个模块，先读对应 `test_*.py` 再读源码

---

## 10. 红线清单（Do Not Break）

- [ ] **零额外依赖**：如需新库，必须装在 venv 中，且 OllamaProvider 不能用 `requests`/`httpx`
- [ ] **所有变更必须有测试**：`pytest` 全绿是合并门槛
- [ ] **Event 不可变**：不要给 `Event` 实例加 setter 或直接改字段
- [ ] **LLM 不改状态**：LLM 输出只能进 `arbiter_output` / `ValidatedOutcome`，不能直接赋值 `WorldState`
- [ ] **NPC 无上帝视角**：跨 location 事件必须被过滤；NPC memory 只能写 observable 事件
- [ ] **Schema 变更需同步**：改了 `schemas.py` 后，检查 `persistence.py` 序列化 + `replay.py` 兼容性
