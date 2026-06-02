# RPG World Engine — 开发进度跟踪

> 本文件在每次会话中更新，防止上下文压缩后遗忘进度。

---

## 已完成的里程碑

### Phase 0 Schema
- ✅ 12 Pydantic 模型 + Enums

### Phase 1 Baseline
- ✅ `llm.py`（Fake Provider + Orchestrator）
- ✅ `world.py`（Event Log + WorldCore）
- ✅ `rules.py`
- ✅ `debug.py`

### Phase 2 交互闭环
- ✅ `intent.py`（Input Normalizer + Intent Parser + Prompt Loader）
- ✅ `interaction.py`（Action Composer + InteractionService）
- ✅ `observation.py`（Event → Perception 分发）
- ✅ `arbiter.py`（LLM Arbiter 模块）
- ✅ `validator.py`（State Validator 六层校验）

### Phase 3 主观世界
- ✅ `memory.py`（MemoryStore + BeliefEngine + MemoryCompressor）
- ✅ `subjectivity.py`（InterpretationGenerator + MemoryConverter + SubjectivityService）

### Phase 4 内容运行时
- ✅ `campaign_loader.py`（加载/校验/fallback）
- ✅ Content Pack JSON Schema（Pydantic 自动生成）

### Phase 5 对话与节奏
- ✅ `scheduler.py`（Tick Scheduler）
- ✅ `campaign.py`（Pacing + Signal 表达式）
- ✅ `conversation.py`（会话生命周期 + 指代消歧）

### Phase 6 Player Agenda
- ✅ `agenda.py`（AgendaService + Reflection Scene）
- ✅ `npc_runtime.py`（NPC 运行时框架）

### Phase 7 产品化（部分）
- ✅ `cli.py`（GameSession + REPL + argparse）
- ✅ `__main__.py`
- ✅ `persistence.py`（JSON 存档 + SHA-256 hash）
- ✅ `replay.py`（ReplayEngine + ReplayVerifier）

### P0 增强（全部完成）
- ✅ **P0-1** Response Generator（规则驱动中文叙事模板）
- ✅ **P0-2** Ollama Provider（urllib 纯标准库，零额外依赖）
- ✅ **P0-3** Action Queue（多 action 排序、冲突检测、优先级执行）

### P1 增强（全部完成）
- ✅ **P1-4** 空间语义完善（Connection + 跨 location noise/visual leak）
- ✅ **P1-5** World Book 分层访问控制（`world_book_filter.py` + AccessScope）
- ✅ **P1-6** 系统建议模式（`/hint` + SuggestionMode 四级控制）
- ✅ **P1-7** Coherence Check 独立模块（`coherence.py`，6 类检查增强）

### 债务清理（全部完成）
- ✅ Action Queue 集成到 GameSession（`run_tick` 统一走 queue）
- ✅ Combat 结果流入 Belief 系统（`SubjectivityService` 初始化 + `_process_events_for_subjectivity`）

### P1-1 LLM Arbiter 接入 CLI（✅ 完成）
- ✅ `LLMArbiter` 实例创建于 `GameSession.__init__`
- ✅ `_handle_arbiter_action()` 方法：调用 arbiter → 创建 Event → 写入 Event Log → 应用 accepted state changes → 走 subjectivity pipeline → 返回 narrative
- ✅ `_apply_state_changes()` 方法：解析 dot-separated field path，对 entity attributes 应用 delta
- ✅ `arbiter.py` 类型修正：`arbitrate()` 参数从 `WorldState` 改为 `WorldCore`
- ✅ `cli.py` 暴露 `llm_provider` / `llm_orchestrator` 供测试注册 fixture
- ✅ 7 个集成测试覆盖：社交/偷窃走 arbiter、Event Log 写入、state change 应用、fallback、subjectivity pipeline

### P1-2 AgendaService 接入 GameSession（✅ 完成）
- ✅ `AgendaService` 实例创建于 `GameSession.__init__`（`player_id` 初始化顺序修正）
- ✅ `/agenda` 命令：展示已确认目标、系统推断、公开/隐藏意图、未解疑问、长期抱负
- ✅ 信号采集：`run_tick` 中玩家行动自动馈入 `agenda_service.add_signal()`
- ✅ `confirmed_drives` 接入 `_build_hint_context()`（替换硬编码 `[]`）
- ✅ Reflection Scene 检测：`run_tick` 末尾检查 `should_trigger_reflection()`，触发时追加 narration_hint
- ✅ `agenda.py` 修复：补充 `Literal` 导入，移除 `# type: ignore` 注释
- ✅ 12 个集成测试覆盖：agenda 命令输出、信号采集、confirmed_drives hint、reflection 触发

### P1-3 世界压力事件（✅ 完成）
- ✅ `CampaignDriverManager` 实例创建于 `GameSession.__init__`，从 content pack `campaign_drivers` 加载
- ✅ `_build_campaign_context()` 方法：聚合 entity_count、player_hp、NPC 属性、recent_event_count、combat_active 等世界指标
- ✅ `_check_campaign_drivers()` 方法：每 tick 检查 → 生成 Event（SYSTEM 类型 + `campaign_pressure` tag）→ 写入 Event Log → 走 subjectivity pipeline → 返回叙事
- ✅ Content Pack 更新：`border_scarcity` driver 增加 `possible_events`（market_argument / supply_surge / refugee_unrest）和 `description`
- ✅ 11 个集成测试覆盖：context 构建、触发/不触发、cooldown、多事件权重选择、event 结构

---

## 当前测试状态

**617 tests passed, 3 skipped**（全绿）

---

## 待办事项（按优先级排序）

### 🔴 P0：NPC 自主行动 — 让世界自己运转

| 编号 | 任务 | 状态 | 说明 |
|---|---|---|---|
| P0-1 | NPC Action 生成器 | ✅ | 每 tick 基于规则/agenda/belief 生成 action |
| P0-2 | NPC Action → ActionQueue | ✅ | `cli.py` 中 `TODO: collect NPC actions here` 落地 |
| P0-3 | Idle 推进 | ✅ | 玩家不输入时，NPC tick 自动推进 |
| P0-4 | 传闻传播 | ✅ | NPC 通过 speech action 传播 belief/memory |

### 🟠 P1：填补核心闭环缺口

| 编号 | 任务 | 状态 | 说明 |
|---|---|---|---|
| P1-1 | LLM Arbiter 接入 CLI | ✅ | steal/sneak/social 返回 `requires_arbiter=True` 时真正裁决，通过 Event Log 记录 |
| P1-2 | AgendaService 接入 GameSession | ✅ | `/agenda` 命令、信号采集、Reflection Scene 触发、hint 驱动 |
| P1-3 | 世界压力事件 | ✅ | Campaign Driver 信号超阈值时主动生成 pressure event → Event Log |

### 🟡 P2：稳定性与持久化

| 编号 | 任务 | 状态 | 说明 |
|---|---|---|---|
| P2-1 | 完整存档 | ✅ | persistence 保存/恢复 Subjectivity、Combat、Conversation、Agenda、CampaignDriver、NPC runtime 状态 |
| P2-2 | 错误恢复 | ✅ | LLM 错误分类 + RetryPolicy 指数退避 + Orchestrator 重试 + fallback |
| P2-3 | Combat 接入 ActionQueue | ✅ | ActionQueue.resolve_with_combat + cli.py 统一路由 |

### 🟢 P3：体验优化（产品化）

| 编号 | 任务 | 状态 | 说明 |
|---|---|---|---|
| P3-1 | Rich TUI + Debug CLI + Phase A | ✅ | ANSI 状态栏、颜色编码叙事、结构化命令输出、Clarification 选项高亮、Debug CLI、/map /who /talk 模式 /空输入提示 |
| P3-3 | 内容编辑工具 | ✅ | /pack validate 交互式校验报告、/pack export 世界状态→JSON、/pack import CSV 批量导入 NPC |
| P3-2 | 多轮 Clarification | ✅ | IntentParser 歧义时返回 ClarificationRequest → 玩家可选编号/补充说明/cancel → 最多 3 轮追问后失败 |
| P3-3 | 内容编辑工具 | ⏳ | Content Pack 导入/导出/校验 CLI 增强 |

---

*最后更新：2026-05-26*
