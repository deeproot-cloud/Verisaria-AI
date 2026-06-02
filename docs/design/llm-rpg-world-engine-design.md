# LLM 驱动 RPG 大世界系统完整设计文档

## 0. 文档信息

| 字段 | 内容 |
|---|---|
| 版本 | 3.0 |
| 状态 | 3.0 设计定稿（Schema & 协议冻结） |
| 基于 | architecture-1.1.1-team-design.md、system-design-team-review.md、架构评审反馈及竞争分析 |
| 目标读者 | 产品设计、系统架构、工程、内容设计、测试、LLM prompt / contract 维护者 |

本文是一份独立的系统级设计文档，整合了 MVP-0 至 1.1.1 的全部设计决策，在架构评审基础上补充了对话状态管理、空间语义、叙事节奏控制、信任量化模型、世界认知盲区处理、多玩家预留、tick 驱动调度等新增设计，并基于行业竞争分析完善了差异化定位、数值系统、战斗系统、存档机制及 Vibe Coding 开发方法论。

系统总目标：

```text
构建一个结构化、可回放、可解释、可扩展的 LLM 驱动 RPG 世界运行时。
```

设计原则：

```text
世界有自己的压力，角色有自己的视角，玩家有自己的方向。
```

---

## 1. 产品愿景

### 1.1 核心定位

本系统不是聊天式 RPG（如 Character.AI / SillyTavern），也不是传统固定任务树 RPG，更不是 LLM 直接生成叙事的交互小说（如 AI Dungeon）。它是一个**通用 RPG 世界运行时**——玩家在可持续运行的世界中自由行动，系统维护世界事实、角色认知、社会后果和叙事反馈。

### 1.2 玩家体验目标

- 用自然语言表达复杂行动，用快捷指令处理高频操作
- 通过对象选择和动作菜单降低歧义
- 在世界中建立自己的目标，不被固定主线牵引
- 观察 NPC 因误会、记忆、传闻、关系而产生不同行为
- 感受世界在自己不行动时依然在运转

### 1.3 系统能力目标

- LLM 不直接篡改世界真相，只做解释、提案、裁决
- NPC 不获得上帝视角，认知受限于自身背景和经历
- 玩家私密意图不自动暴露给 NPC
- 后端通用化，支持不同世界、不同模板、不同叙事风格
- 内容可通过文件、数据库、世界书或 LLM 生成后结构化加载
- 所有关键链路可回放、可追溯、可解释

### 1.4 竞争定位与行业对标

#### 1.4.1 现有项目全景

当前 LLM 驱动的叙事/角色扮演领域可大致分为五类：

| 类别 | 代表项目 | 本质 | 核心模式 | 目标用户 |
|---|---|---|---|---|
| **聊天前端** | SillyTavern / TavernAI / KoboldAI / Character.AI | LLM 的"角色扮演外壳" | 玩家 ↔ AI 角色对话，历史记录总结后注入 prompt | 爱好者、角色扮演玩家 |
| **交互叙事** | AI Dungeon / NovelAI / DreamGen | LLM 驱动的交互小说 | LLM 直接生成叙事 + 隐式状态变化 | 文学创作者、冒险玩家 |
| **社会模拟（学术）** | Generative Agents (Smallville) / AgentSociety / OASIS / Project Sid | 多智能体涌现行为研究 | 观察 NPC 自主生活，偏向沙盘而非游戏 | 社会科学研究者 |
| **评估框架** | Concordia / Sotopia / AvalonBench | 社会推理/合作能力基准测试 | 智能体在设定场景中对弈/协作 | AI 研究者 |
| **AI + 传统游戏** | 各类 Unity/Godot + GPT 学生/独立项目 | 传统 RPG 套 LLM 生成皮 | 静态任务树 + LLM 生成对话/地图 | 独立开发者 |

#### 1.4.2 与关键项目的详细对比

**vs 酒馆AI类（SillyTavern / TavernAI / KoboldAI）**

| 维度 | 酒馆AI类 | 本系统 |
|---|---|---|
| **核心定位** | LLM 聊天前端 | 通用 RPG 世界运行时 |
| **世界状态** | 不存在结构化世界状态，一切靠 prompt 上下文维持 | 严格隔离的结构化状态 + 不可变 Event Log |
| **记忆机制** | 对话历史总结 → 塞进 prompt（文本摘要） | Working / Short-term / Long-term 三层记忆 + Belief 系统 |
| **NPC 自主性** | 被动响应玩家输入（群聊模式也只是轮流回复） | Tick 驱动，NPC 在玩家不输入时自主决策、传播传闻 |
| **状态变更** | LLM 直接在生成文本中"决定"发生了什么 | LLM 只提提案，State Validator 执行校验后落盘 |
| **可回放性** | 无 | 固定 seed + fixture 全链路 replay |
| **玩家目标** | 无 | Player Agenda 动态推断 + Reflection Scene |

**技术本质差异**：酒馆AI是**高级 prompt 管理器**——加载 Character Card（system prompt + 示例对话），把历史摘要连新输入发给 LLM，LLM 返回什么就是什么。没有"世界真相"，NPC 今天记住的事明天可能因上下文被挤掉而遗忘。

**我们的技术本质**：LLM 只是解释者和提案者，**世界有自己的账本（Event Log）**。NPC 记得某事不是因为它还在 prompt 里，而是因为那条 Memory 被结构化存储了。

**vs AI Dungeon / NovelAI（交互叙事）**

| 维度 | 交互叙事类 | 本系统 |
|---|---|---|
| **状态所有权** | 隐式状态——LLM 同时扮演"叙事者"和"世界裁判" | 显式状态——Event Log 是世界真相的唯一来源 |
| **世界设定** | NovelAI Lorebook：按关键词触发注入的设定片段 | World Book 分层真相 + 按角色 scope 访问控制 |
| **叙事节奏** | "玩家输入 → LLM 写下一章"的回合制 | Tick 驱动，世界在玩家沉默时继续运转 |
| **一致性保障** | 依赖 LLM 长上下文和摘要，易出现设定漂移 | State Validator + 不可变 Event + Replay 校验 |

**vs Generative Agents / Smallville（斯坦福小镇）**

Smallville 是学术上和我们最接近的项目，其 Memory Stream（观察→记忆→反思→计划）与我们的 Perception → Memory → Belief 有相似性。

| 维度 | Smallville | 本系统 |
|---|---|---|
| **产品形态** | 观察沙盒（研究者看 NPC 生活） | 可玩游戏（玩家在世界中行动） |
| **LLM 角色** | LLM 直接生成行为和状态变化 | LLM 只提案，Validator 把关 |
| **确定性** | 无法回放同一场景 | 固定 seed + fixture，确定性 replay |
| **玩家输入** | 没有玩家输入处理管线 | 完整的 Intent → Action → Event → Observation 链路 |
| **叙事节奏** | NPC 24 小时不间断行动 | Pacing Policy + Tick 调度，对话时世界放慢 |
| **信念系统** | 记忆是单一文本流，无信念强度 | conviction 四级 + would_revise_if，支持顽固偏见 |

**vs AgentSociety / OASIS / Project Sid（大规模社会模拟）**

这些是社会科学研究工具，不是游戏。

| 维度 | 大规模社会模拟 | 本系统 |
|---|---|---|
| **设计目标** | 社会科学研究（舆论、经济、政策影响） | 玩家中心的 RPG 体验 |
| **规模 vs 深度** | 10,000+ 智能体，每个很薄（简单 prompt + 记忆） | 少量 NPC，每个有完整主观世界 |
| **玩家角色** | 无玩家，或玩家只是观察者 | 玩家是 tick 的 action source，世界围绕玩家运转 |
| **可玩性** | 追求涌现现象的"真实度" | 追求"可玩性"和"叙事反馈" |

#### 1.4.3 核心竞争点

**1. 世界真相不在 prompt 中（A1 / A2 原则）**

几乎所有现有项目（包括 Smallville 和 AI Dungeon）都让 LLM **同时扮演叙事者、裁判和状态管理者**。这导致：
- LLM 幻觉直接污染世界状态
- 不同 NPC 的 prompt 中世界描述不一致
- 无法解释"为什么现在是这样"

我们的 State Validator + Event Log 架构，把 LLM 降级为"建议者"，这是工程上最硬的差异。

**2. 四层主观世界 + 信念顽固度**

Smallville 有记忆和反思，但没有 **Interpretation**（同一事件可以被不同 NPC 误解）和 **conviction**（根深蒂固的偏见不会立刻被证据推翻）。这是 RPG 叙事的核心——误会、偏见、信息不对称。

**3. 可回放性**

这是工程护城河。Fake LLM fixture + 固定 seed + 确定性规则引擎，意味着我们可以：
- 写回归测试（AI Dungeon 做不到）
- 调试"为什么 NPC A 在这个时候做了这件事"
- 内容创作者可以验证"改了 World Book 后，同一玩家行为链会产生什么不同结果"

**4. 混合 LLM 策略 + Budget 控制**

现有项目通常是"要么全本地、要么全 API"。我们的 Ollama + GPT 分工 + Budget 机制，是在**成本、延迟、质量**之间做显式权衡，这在产品化阶段是关键优势。

**5. NPC-NPC 自主交互的可控性**

Smallville 的 NPC 交互是"自然涌现"的，无法预测、无法控制传播速度。我们的 Interaction Scheduler 用 **cooldown + salience 阈值 + seed** 把传闻传播控制在可回放、可调试的范围内。

#### 1.4.4 可借鉴的现有机制

| 项目 | 可借鉴的具体机制 | 在我们的系统中的落地位置 |
|---|---|---|
| **NovelAI Lorebook** | 关键词触发的知识注入方式 | World Book 的标签过滤检索（按 faction/region/education 做交集） |
| **Smallville Memory Stream** | 记忆的 salience 评分、时间衰减、反思摘要 | Memory Service 的 Short-term Memory 压缩算法 |
| **SillyTavern Character Cards** | 角色定义的格式化和社区分享模式 | Content Pack 中的 entity.json 角色模板 + 社区分享机制（长期规划） |
| **Concordia** | Game Master LLM 的协调方式 | 未来多玩家冲突仲裁的预留设计（Action Queue 冲突检测接口） |
| **AgentSociety** | 大规模经济/社会压力模拟的指标设计 | Campaign Driver 的 signal 指标体系（food_price、refugee_count 等） |

#### 1.4.5 与"酒馆AI"的关系

> **我们不是酒馆AI的竞品，我们是酒馆AI的"反面"。**

- **酒馆AI做的是减法**：把复杂的 LLM 能力包装成简单的聊天界面，让用户"和角色对话"。
- **我们做的是加法**：在 LLM 之上建立一层完整的世界运行时，让 LLM 的创造力被约束在一个**可解释、可回放、可调试**的框架内。

如果非要做个类比：
- **酒馆AI** ≈ 一个高级的角色扮演聊天室（Discord + AI）
- **AI Dungeon** ≈ 一个AI当DM的跑团（但DM会忘设定、会作弊）
- **Smallville** ≈ 一个可以观察的蚂蚁农场（研究员看NPC生活）
- **我们** ≈ 一个**有物理法则和司法系统的世界**，AI 是律师（提提案），规则引擎是法官（做裁决），Event Log 是档案库（不可篡改）

这就是我们的竞争定位：**不是让 LLM 写故事，而是让 LLM 在一个有秩序的世界里讲故事。**

---

## 2. 设计原则

### 2.1 架构原则

| 编号 | 原则 | 说明 |
|---|---|---|
| A1 | 世界真相不在 prompt 中 | 世界真相在结构化状态和 Event Log 中，LLM 只能提出候选 |
| A2 | LLM 是建议者而非执行者 | LLM 可以解释、提案、重写，但不能越过 State Validator |
| A3 | Event 是不可变事实 | 写入后不修改，只能追加修正事件 |
| A4 | Observation ≠ Event | Observation 是角色感知，Belief 是角色判断，都不等于世界真相 |
| A5 | NPC 决策基于自身认知 | 只能使用该 NPC 可获得的 Perception / Memory / Belief / 局部上下文 |
| A6 | 玩家意图有边界 | performed_content 进世界，player_intent_note 只作裁决上下文 |
| A7 | 世界书是约束而非全知 | 世界书分层，NPC 只看到自己背景允许的版本 |
| A8 | Campaign 提供压力而非剧本 | 冲突驱动替代固定剧情节点 |
| A9 | 世界以 tick 为单位运转 | 玩家输入是 tick 内的 action source，不是唯一触发器 |
| A10 | 可回放是硬性要求 | 所有关键结果必须能用固定 seed + 固定 fixture 重放 |

### 2.2 产品原则

| 编号 | 原则 | 说明 |
|---|---|---|
| P1 | 自由输入为第一公民 | 对象选择和动作菜单是消歧辅助，不是替代 |
| P2 | 系统建议保持谦逊 | suggestion_mode 控制引导强度，避免变成任务导航 |
| P3 | 玩家目标可拒绝 | Agenda 不是强制日志，可接受、改写、拒绝 |
| P4 | 沉默也是一种行动 | 玩家不输入时世界继续运转，NPC 可能主动找来 |
| P5 | 错误降级优于崩溃 | LLM 不可用时系统降级运行，而非停止 |

---

## 3. 系统演进脉络

### 3.1 MVP-0 / 1.0.0：最小生命链路

已验证：

```text
玩家行为 → 世界事件 → NPC 观察/传闻 → NPC 信念变化 → NPC 行为变化
```

验证场景：偷短剑 → 守卫观察 → 守卫怀疑 → 传闻传播 → 铁匠质问 → 玩家解释可降低怀疑。

范围：3 地点、3 NPC、1 玩家、1 物品、4 action、1 条可回放事件链。

### 3.2 1.1.0：Intent → Event → Observation

升级粗粒度链路为结构化 pipeline：

```text
Raw Input → Intent Parser → Parsed Intent → Coherence → Action Composer → World Core → Rules → Arbiter → Validator → Event → Observation
```

新增：Parsed Intent、Coherence Check、LLM Arbiter、State Validator、Partial Observation。

### 3.3 1.1.1：主观世界

> **决策：本阶段作为单一里程碑推进，不拆分为两个子阶段。**
> 
> 原因：当前为项目完全重写阶段，主观世界（Perception / Memory / Belief / Relationship）与内容初始化（Content Pack / Campaign Loader）并非强依赖关系，但在早期验证期并行推进两条链路会增加集成复杂度。考虑到团队当前需要聚焦核心世界运行时的稳定性，1.1.1 统一为主观世界闭环验证，Content Pack 的通用化作为 1.1.2 独立里程碑。

核心变更：

- Intent commitment 四级承诺
- performed_content / player_intent_note 分离
- Observation 拆分 Perception / Interpretation
- LLM Arbiter 带 evidence_refs
- player_intent_note 信号池记录（performed_content / player_intent_note 分离，为后续 Player Agenda 推断预留数据，不实现聚合/确认/Reflection）

### 3.4 1.1.2：通用内容运行时

核心变更：

- Content Pack / World Book schema
- Campaign Loader
- Campaign Driver 冲突引擎
- 模板迁移与测试矩阵

### 3.5 2.0：完整世界运行时

核心变更：

- tick 驱动调度
- 对话状态管理
- 空间语义
- 叙事节奏控制
- 信任量化模型
- 多玩家架构预留

---

## 4. 顶层架构

### 4.1 架构总览

```text
┌─────────────────────────────────────────────────────────────────┐
│                        Client Layer                             │
│              CLI / TUI / Web UI / Future Multiplayer            │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                    Interaction Layer                             │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────────────────┐ │
│  │  Input       │ │  Conversation│ │  Player Agenda           │ │
│  │  Normalizer  │ │  Manager     │ │  Service                 │ │
│  └──────┬──────┘ └──────┬───────┘ └────────────┬─────────────┘ │
│         │               │                      │               │
│  ┌──────▼───────────────▼──────────────────────▼──────┐        │
│  │              Intent Parser (LLM)                    │        │
│  └──────────────────────┬──────────────────────────────┘        │
│                         │                                       │
│  ┌──────────────────────▼──────────────────────────────┐        │
│  │              Coherence Check                         │        │
│  └──────────────────────┬──────────────────────────────┘        │
│                         │                                       │
│  ┌──────────────────────▼──────────────────────────────┐        │
│  │              Action Composer                         │        │
│  └──────────────────────┬──────────────────────────────┘        │
└─────────────────────────┼───────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│                    World Simulation Layer                        │
│                                                                 │
│  ┌─────────────────── Tick Scheduler ──────────────────────┐   │
│  │                                                          │   │
│  │  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │   │
│  │  │ Pacing      │  │ Campaign     │  │ NPC           │  │   │
│  │  │ Policy      │  │ Driver       │  │ Runtime       │  │   │
│  │  └──────┬──────┘  └──────┬───────┘  └───────┬───────┘  │   │
│  │         │                │                   │          │   │
│  │  ┌──────▼────────────────▼───────────────────▼──────┐  │   │
│  │  │              World Core                           │  │   │
│  │  │  (Action Queue → Conflict Detection → Execution)  │  │   │
│  │  └──────────────────────┬────────────────────────────┘  │   │
│  └─────────────────────────┼────────────────────────────────┘   │
│                            │                                    │
│  ┌─────────────────────────▼────────────────────────────┐      │
│  │              Rules Engine                              │      │
│  │  (Hard Rules / Commitment Check / Spatial Filter)     │      │
│  └──────────────────────────┬─────────────────────────────┘      │
│                             │                                    │
│  ┌──────────────────────────▼─────────────────────────────┐      │
│  │              LLM Arbiter                               │      │
│  │  (Soft Judgment / evidence_refs / proposed outcomes)   │      │
│  └──────────────────────────┬─────────────────────────────┘      │
│                             │                                    │
│  ┌──────────────────────────▼─────────────────────────────┐      │
│  │              State Validator                            │      │
│  │  (Reject illegal LLM proposals / boundary check)      │      │
│  └──────────────────────────┬─────────────────────────────┘      │
│                             │                                    │
│  ┌──────────────────────────▼─────────────────────────────┐      │
│  │              Event Log (Immutable)                      │      │
│  └─────────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│                   Subjectivity Layer                             │
│                                                                 │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │  Observation     │  │  Memory /        │  │  Relationship│  │
│  │  Dispatch        │  │  Belief Service  │  │  Snapshot     │  │
│  │  (Attention      │  │  (Perception →   │  │  (trust /    │  │
│  │   Filter)        │  │   Interpretation │  │   suspicion  │  │
│  │                  │  │   → Memory →     │  │   / affection│  │
│  │                  │  │   Belief)        │  │   quantized) │  │
│  └────────┬─────────┘  └────────┬─────────┘  └──────┬───────┘  │
│           │                     │                    │          │
│           └─────────────────────┼────────────────────┘          │
│                                 │                               │
│  ┌──────────────────────────────▼────────────────────────────┐  │
│  │              Persistence Layer                            │  │
│  │  (Entities / Events / Observations / Memory / Beliefs /   │  │
│  │   Relationships / Agenda / LLM Calls / Content Packs)    │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│                   Content Layer                                  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │  Campaign Loader │  │  Content Pack    │  │  World Book  │  │
│  │  (Cold Start /   │  │  (JSON Schema)   │  │  (Layered    │  │
│  │   Hot Reload)    │  │                  │  │   Truth)     │  │
│  └──────────────────┘  └──────────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│                   Infrastructure Layer                           │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐    │
│  │  LLM         │  │  Debug       │  │  Error Recovery    │    │
│  │  Orchestrator│  │  Service     │  │  & Graceful        │    │
│  │  (Provider   │  │  (Trace /    │  │  Degradation       │    │
│  │   Agnostic)  │  │   Replay /   │  │                    │    │
│  │              │  │   Explain)   │  │                    │    │
│  └──────────────┘  └──────────────┘  └────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 Tick 驱动调度模型

系统以 **tick** 为世界推进的基本单位，而非"玩家轮次"。每个 tick 的执行顺序：

```text
Tick N:
  ① Input Collection
     - 收集所有 pending player input（单玩家阶段最多 1 个）
     - 标记 input 来源和时间戳

  ② Intent Processing
     - Input Normalizer → Intent Parser → Parsed Intent[]
     - Coherence Check → filtered intents
     - Action Composer → action batch

  ③ World Advancement
     - Scheduler 推进世界时间
     - Campaign Driver 检查 signals，可能生成 event seed
     - NPC Runtime 为活跃 NPC 生成 candidate decision
     - （NPC decision 也进入 action queue）

  ④ Action Execution
     - World Core 合并 player actions + NPC actions + campaign events
     - 冲突检测（同一目标的并发 action）
     - 排序（priority + time + dependency）
     - 逐个执行：Rules Engine → LLM Arbiter → State Validator → Event Commit

  ⑤ Observation & Subjectivity
     - Observation Dispatch（注意力过滤 → Perception 生成）
     - Memory / Belief 更新
     - Relationship Snapshot 重算
     - player_intent_note 信号记录（Phase 6 前不做聚合）

  ⑥ Response Generation
     - 生成 client response（叙事文本、状态变化、UI 更新）
     - 检查是否触发 Reflection Scene
     - 检查是否触发 clarification / confirmation
```

**单玩家简化**：当前阶段每个 tick 最多处理 1 个 player input。NPC action 和 Campaign event 由 Scheduler 按优先级调度。未来多人时，多个 player input 在 ① 阶段并行收集，在 ④ 阶段统一冲突检测。

### 4.3 关键数据流

#### 玩家输入链路

```text
Raw Input / UI Interaction
  → Input Normalizer
  → LLM Intent Parser
  → Parsed Intent[]
  → Coherence Check
  → Action Composer
  → [进入 tick ④]
```

#### NPC 行为链路

```text
Tick ③: Scheduler activates NPC
  → NPC Runtime reads Relationship Snapshot + Memory + Belief
  → LLM NPC Decision
  → Candidate Intent → Action
  → [进入 tick ④]
```

#### 世界事件链路

```text
Tick ③: Campaign Driver checks signals
  → Event Seed generated
  → [进入 tick ④] as campaign action
```

#### 内容初始化链路

```text
World Seed Input
  → Campaign Loader
  → Schema Validation
  → Optional LLM Expansion
  → Consistency Check
  → Initial World State / Event / Memory / Belief / Agenda
```

---

## 5. 核心领域模型

### 5.1 实体关系总览

```text
┌─────────┐     ┌─────────┐     ┌──────────────┐     ┌───────────┐
│  Intent │────▶│  Action │────▶│    Event     │────▶│Observation│
│(proposed)│    │(committed)│   │ (immutable)  │     │(per-actor)│
└─────────┘     └─────────┘     └──────────────┘     └─────┬─────┘
                                                           │
                    ┌──────────────────────────────────────┘
                    ▼
              ┌───────────┐     ┌───────────┐     ┌────────────┐
              │ Perception│────▶│  Memory   │────▶│   Belief   │
              │(raw sense)│     │(retained) │     │(interpreted│
              └───────────┘     └───────────┘     └─────┬──────┘
                                                        │
                    ┌───────────────────────────────────┘
                    ▼
              ┌───────────────┐     ┌──────────────────┐
              │  Relationship │     │  Player Agenda   │
              │  Snapshot     │     │  (drives/goals)  │
              │  (quantized)  │     │                  │
              └───────┬───────┘     └────────┬─────────┘
                      │                      │
                      └──────────┬───────────┘
                                 ▼
                          ┌─────────────┐
                          │   Decision  │
                          │  (NPC/Player│
                          │   response) │
                          └─────────────┘
```

### 5.2 Intent

Intent 表示"玩家或 NPC 试图做什么"，不是世界事实。

```json
{
  "intent_id": "intent_001",
  "source": "natural_language",
  "raw_text": "我轻声且紧张地对艾蕾说，我们先快离开这儿",
  "intent_type": "speech",
  "actor_id": "player_001",
  "target_ref": "艾蕾",
  "target_id": "npc.ele",
  "content": "我们先快离开这儿",
  "modifiers": {
    "volume": "low",
    "emotion": "nervous",
    "urgency": "high"
  },
  "commitment": "committed",
  "confidence": 0.91,
  "ambiguities": [],
  "performed_content": "我们先快离开这儿",
  "player_intent_note": null,
  "conversation_session_id": "conv_001",
  "timestamp": "tick_42"
}
```

**commitment 四级承诺：**

| 级别 | 含义 | 系统行为 |
|---|---|---|
| `considering` | 玩家在想/考虑 | 不生成 Action，不进入世界 |
| `preparing` | 玩家在准备/试探 | 生成预览 Action，不提交，NPC 不可感知 |
| `attempting` | 玩家尝试但不确定成功 | 生成 Action 提交 Arbiter，可能失败 |
| `committed` | 玩家确定执行 | 正常 Action pipeline |

**performed_content 与 player_intent_note 分离：**

```text
输入："我轻声对艾蕾说快走，目的是不让卫兵听见。"

→ performed_content: "快走"          → 进入 speech Event，可被 Observation
→ player_intent_note: "不让卫兵听见" → 进入意图信号池，NPC 不可见
```

### 5.3 Action

Action 是提交给 World Core 的结构化世界行为输入。

```json
{
  "action_id": "act_001",
  "source_intent_id": "intent_001",
  "actor_id": "player_001",
  "action_type": "speech",
  "target_id": "npc.ele",
  "params": {
    "content": "我们先快离开这儿",
    "volume": "low",
    "emotion": "nervous"
  },
  "client_context": {
    "input_method": "natural_language",
    "raw_text": "我轻声且紧张地对艾蕾说，我们先快离开这儿"
  },
  "conversation_session_id": "conv_001",
  "tick": 42
}
```

Action 不包含：结算结果、state changes、relationship_delta。这些由下游模块决定。

**Action 类型与裁决路径：**

`action_type` 是 5 个高层类别，决定 Action 走哪条裁决管道。具体动作语义由 `params["verb"]` 承载。

| action_type | params["verb"] 示例 | 裁决路径 | 说明 |
|---|---|---|---|
| `social` | `persuade`, `deceive`, `bribe`, `threaten` | Rules Engine 硬过滤 → LLM Arbiter 软裁决 → State Validator | 社会交互走 Arbiter，结果受关系、情绪、场景影响 |
| `physical` | `steal`, `climb`, `sneak`, `carry`, `look` | Rules Engine 硬裁决（属性 vs 阈值）→ LLM Arbiter 仅对边缘情况补充 | 物理行为以规则为主，减少 LLM 调用 |
| `combat` | `attack`, `defend`, `dodge`, `cast` | Combat Subsystem（独立伤害公式、回合制）→ 结果写入 Event | 战斗系统初期以规则驱动，不经过 Arbiter |
| `movement` | `move`, `enter`, `leave` | Rules Engine 空间过滤 → 直接生成 Event | 纯规则，无需 LLM |
| `speech` | `talk`, `shout`, `whisper` | Rules Engine 空间/音量过滤 → 直接生成 Event | 语言内容进入 Event，但语义解析由下游 Observer 处理 |

**设计原则：** 尽量把判定工作从 LLM 转移到 Rules Engine，LLM 只负责需要主观解释的社会交互和叙事生成。

### 5.4 Event

Event 是不可变世界事实。

```json
{
  "event_id": "evt_001",
  "event_type": "speech",
  "actor_id": "player_001",
  "target_id": "npc.ele",
  "tick": 42,
  "location_id": "town_square",
  "summary": "player_001 对 npc.ele 低声说话",
  "canonical_facts": {
    "content": "我们先快离开这儿",
    "volume": "low",
    "emotion": "nervous"
  },
  "source_action_id": "act_001",
  "related_events": [],
  "tags": ["speech", "urgent", "private"]
}
```

Event 优先记录可观察事实，不写入主观动机。"贿赂"是 Interpretation，不是 Event。

### 5.5 Observation：Perception → Interpretation

```json
{
  "observation_id": "obs_001",
  "observer_id": "npc.guard_b",
  "source_event_id": "evt_001",
  "tick": 42,
  "perception": {
    "channels": ["sight", "hearing"],
    "saw": "player_001 靠近 npc.ele 并低声说话",
    "heard_keywords": ["离开"],
    "heard_full_content": false,
    "distance": "near",
    "attention_level": "distracted"
  },
  "interpretation": {
    "claim": "player_001 在催促 npc.ele 离开",
    "confidence": 0.45,
    "alternatives": ["闲聊", "警告", "密谋"],
    "emotional_tone": "urgency_detected"
  },
  "detail_level": "partial"
}
```

### 5.6 Memory 与 Belief

Memory 是角色保留下来的感知片段，Belief 是角色当前的判断。

**Memory 分层设计：**

为解决长期运行后 Memory 无限膨胀导致的上下文爆炸问题，引入三层记忆模型：

| 层级 | 范围 | 内容特征 | 注入 NPC 决策上下文的方式 |
|---|---|---|---|
| Working Memory | 最近 5-10 tick | 完整细节，原始 Observation 文本 | 全量注入 |
| Short-term Memory | 10-100 tick | 按主题摘要，保留关键冲突和异常 | 主题摘要注入 |
| Long-term Memory | 100+ tick | 高度压缩的个人史、刻板印象、价值观 | 只注入与当前情境相关的条目 |

```json
{
  "memory_id": "mem_001",
  "owner_id": "npc.guard_b",
  "source_observation_id": "obs_001",
  "tick": 42,
  "content": "看见玩家对同伴低声说了什么，似乎在催促离开",
  "salience": 0.3,
  "decay_rate": 0.05,
  "layer": "working",
  "topic_tags": ["player_behavior", "suspicious_action"],
  "last_recalled_tick": null,
  "compression_of": null
}
```

```json
{
  "memory_id": "mem_050",
  "owner_id": "npc.guard_b",
  "tick": 150,
  "content": "玩家多次在夜间独自前往教堂方向，行为模式可疑",
  "salience": 0.6,
  "decay_rate": 0.02,
  "layer": "short_term",
  "topic_tags": ["player_behavior", "night_activity", "church"],
  "last_recalled_tick": 148,
  "compression_of": ["mem_012", "mem_023", "mem_034"]
}
```

**压缩触发条件：**
- Working Memory 超过 10 条且最旧条目 tick 距当前 > 10 → 触发摘要生成
- Short-term Memory 超过 20 条 → 触发长时压缩
- 压缩由规则驱动（无需 LLM），摘要生成可调用 LLM 或预置模板

**Memory 检索与注入规则：**

当前阶段采用**分层 + 标签 + salience**的纯规则检索方案，不引入向量数据库和 embedding，保证零额外依赖和确定性回放。

```text
① 当前情境标签生成：
   - 来源：location_id + zone_id + conversation_session.topic_stack + 最近 3 个 Event 的摘要关键词
   - 输出：3-5 个情境标签（如 ["guard", "night", "suspicious"]）

② 分层检索：
   - Working Memory：按 tick 范围直接取（最近 5-10 tick）→ 全量注入
   - Short-term Memory：tick 范围 + topic_tags 与情境标签有交集 → 主题摘要注入
   - Long-term Memory：topic_tags 与情境标签交集过滤 → salience 降序取 Top 5

③ 保底策略：
   - 若 Long-term 标签过滤后结果为空 → 降级为"最近 3 条 Long-term Memory"

④ NPC 主动回忆触发（规则驱动）：
   - 条件：NPC Decision 阶段，从 Long-term 中随机选取
   - 筛选：salience > 0.6 且未在最近 20 tick 被提及过
   - 用途：NPC 主动说"我记得你上次……"
```

**为什么第一阶段不用向量数据库？**

| 因素 | 分析 |
|---|---|
| **数据量级** | MVP 阶段单个 NPC 的 Long-term Memory 通常 < 50 条，SQLite + 标签过滤足够 |
| **确定性** | embedding 模型更新会改变相似度分布，破坏 Replay 的确定性 |
| **延迟** | 本地 Ollama embedding 虽快，但仍比标签查询慢 1-2 个数量级 |
| **依赖** | 与"单进程、低外部依赖"的架构原则冲突 |

**第二阶段（Phase 5+）可选的轻量语义层：**

当单个 NPC 记忆量超过 200 条、跨主题语义关联需求增强时，引入**本地 Ollama embedding**（如 `nomic-embed-text`）作为精排层：

```text
① 标签硬匹配（粗筛）：候选集从 200 条 → 20 条
② 若候选集 > 10 条：
   - 调用 Ollama embedding 编码候选 Memory 和当前情境
   - 余弦相似度排序取 Top 5
③ 若候选集 ≤ 10 条：直接按 salience 排序，跳过 embedding
```

设计原则：**90% 的查询不走 embedding，只在真正需要时调用**，控制成本和延迟。embedding 结果可缓存，模型版本固定后不影响回放。

**Belief 与 Memory 的关系：**
- Belief 基于 Memory 生成，但 Memory 删除不影响已形成的 Belief（除非 Belief 被挑战）
- `would_revise_if` 字段指向可能的反证主题，用于快速检索相关 Memory

```json
{
  "belief_id": "bel_001",
  "owner_id": "npc.guard_b",
  "claim": "player_001 可能在计划什么",
  "confidence": 0.35,
  "conviction": "low",
  "source_evidence": ["mem_001"],
  "challenged_by": [],
  "would_revise_if": ["player_001 explains openly", "clear innocent context"],
  "formed_at_tick": 42,
  "last_updated_tick": 42
}
```

**conviction 字段：**

| 级别 | 含义 | 行为 |
|---|---|---|
| `low` | 轻度相信，容易被新证据改变 | 一条反面证据即可降低 confidence |
| `medium` | 较为确信，需要多条反面证据 | 需要 2-3 条矛盾证据动摇 |
| `high` | 深信不疑，极难改变 | 需要直接且强烈的矛盾体验 |
| `dogmatic` | 教条式相信，几乎不可能改变 | 只有极端事件才能动摇 |

conviction 由 belief 来源决定：童年教育 > 派系宣传 > 个人经历 > 传闻 > 间接推断。

### 5.7 Relationship Snapshot

Belief 的结构化投影，供 NPC 决策快速查询。

```json
{
  "snapshot_id": "rel_001",
  "npc_id": "npc.blacksmith",
  "towards": "player_001",
  "tick": 42,
  "dimensions": {
    "trust": 0.45,
    "suspicion": 0.6,
    "fear": 0.1,
    "affection": 0.2,
    "respect": 0.3,
    "familiarity": 0.4
  },
  "last_interaction_summary": "玩家试图讨价还价，态度友好但有些急切",
  "dominant_beliefs": [
    {"belief_id": "bel_sword_suspect", "weight": 0.4, "source": "direct_observation"},
    {"belief_id": "bel_guard_rumor", "weight": 0.3, "source": "rumor"}
  ],
  "updated_at_tick": 42
}
```

Relationship Snapshot 由 Memory/Belief Service 在每次 Belief 变化时自动重算，纯规则驱动，不需要 LLM。

### 5.8 Conversation Session

```json
{
  "session_id": "conv_001",
  "participants": ["player_001", "npc.ele"],
  "started_at_tick": 40,
  "turn_count": 3,
  "topic_stack": ["escape_plan", "guard_suspicion"],
  "shared_context": {
    "ele_mood": "nervous",
    "agreed_plan": "leave quietly",
    "open_question": null
  },
  "status": "active",
  "interruptible": true,
  "pending_interruptions": [],
  "last_activity_tick": 42
}
```

Conversation Session 不阻塞世界推进，但为 Intent Parser 提供指代消歧，为 NPC Decision 提供对话连续性。

状态流转：

```text
active → interrupted → resumed → active
active → concluded
active → abandoned (timeout)
```

### 5.9 Player Agenda

```json
{
  "player_id": "player_001",
  "tick": 42,
  "current_drives": [
    {
      "id": "drive_survive",
      "label": "先解决温饱",
      "strength": 0.8,
      "source": "player_declared",
      "declared_at_tick": 1
    },
    {
      "id": "drive_investigate",
      "label": "调查教会是否在撒谎",
      "strength": 0.6,
      "source": "system_inferred",
      "confirmed": true,
      "confirmed_at_tick": 38,
      "evidence_refs": ["intent_007", "intent_012", "intent_019"]
    }
  ],
  "declared_to_world": ["我们只是去接委托赚钱"],
  "private_intent": ["查清教会是否在撒谎"],
  "system_inferred": [
    {
      "claim": "玩家对恶魔族群有同情倾向",
      "confidence": 0.52,
      "requires_confirmation": true,
      "evidence_refs": ["intent_025", "obs_017"]
    }
  ],
  "open_questions": ["恶魔是否真的如教会所说？", "铁匠是否知道更多？"],
  "rejected_paths": ["加入教会"],
  "long_term_aspirations": [],
  "suggestion_mode": "subtle"
}
```

**意图信号聚合规则：**

- 信号来源：每次 player_intent_note 进入信号池
- 聚合方式：按主题聚类，带时间衰减（最近 5 轮权重 1.0，5-10 轮权重 0.6，10+ 轮权重 0.3）
- 触发阈值：同类信号累积 confidence > 0.7 且 count >= 5（提高阈值，避免频繁打扰）
- **确认机制：不在 tick 间弹窗打断玩家，只在 Reflection Scene 中统一生成 Agenda Update Proposal，交给玩家确认/改写/拒绝**
- 拒绝记录：玩家拒绝的推断进入 rejected_paths，不再重复提议
- 玩家可配置 `inference_mode: off / reflection_only / normal`（默认 reflection_only）

### 5.10 Content Pack / World Book / Campaign Template

**Content Pack 最小结构：**

```json
{
  "content_pack_id": "frontier_town",
  "schema_version": "2.0",
  "world_premise": {
    "era": "medieval_fantasy",
    "tone": "gritty",
    "central_tension": "post_war_tensions"
  },
  "world_book": [
    {
      "entry_id": "demon_existence",
      "layer": "canonical_fact",
      "content": "恶魔族群确实存在，拥有独立的语言、文明和社会结构。",
      "access": {"visible_to": {"faction": ["all"]}, "hidden_from": {}},
      "confidence_policy": "fact"
    },
    {
      "entry_id": "church_demon_propaganda",
      "layer": "faction_propaganda",
      "content": "教会宣称恶魔是人类苦难的根源，必须被驱逐。",
      "access": {"visible_to": {"faction": ["church_of_flame"], "education": ["church_educated"]}, "hidden_from": {"faction": ["demon_refugees"]}},
      "confidence_policy": "belief_not_fact"
    }
  ],
  "starting_location": "town_square",
  "initial_entities": [],
  "initial_relationships": [],
  "initial_conversations": [],
  "player_agenda_template": {
    "current_drives": [
      {"id": "drive_survive", "label": "先解决温饱", "strength": 0.8, "source": "player_declared"}
    ],
    "inference_mode": "reflection_only",
    "suggestion_mode": "subtle"
  },
  "style_guide": {
    "narrative_voice": "第三人称有限视角，聚焦玩家感官体验",
    "dialogue_style": "直接引语，符合角色身份和情绪",
    "tone_references": ["gritty", "grounded", "low_fantasy"],
    "prohibited_tropes": ["fourth_wall_break", "anachronistic_references", "power_fantasy_escalation"]
  },
  "constraints": {
    "max_entities_per_location": 20,
    "max_conversation_participants": 4,
    "player_death": "incapacitated_only",
    "time_scale": "1_tick_≈_1_minute"
  },
  "campaign_drivers": [],
  "rule_presets": {
    "difficulty": "normal",
    "stealth_threshold_base": 0.5,
    "social_threshold_base": 0.5,
    "combat_lethality": "non_lethal_default"
  }
}
```

**World Book 分层真相：**

| 层级 | 含义 | 示例 |
|---|---|---|
| `canonical_fact` | 客观世界真相 | 恶魔族群确实存在，有独立文明 |
| `public_belief` | 普遍认知 | 恶魔是野蛮危险的生物 |
| `faction_propaganda` | 派系宣传 | 教会宣称恶魔是人类苦难的根源 |
| `forbidden_knowledge` | 被压制的真相 | 教会曾屠杀寻求和平的恶魔使节 |
| `local_rumor` | 地方传闻 | 边境有人说见过友善的恶魔商人 |
| `personal_truth` | 个人经历 | 某老兵曾被恶魔救过命 |

每条 World Book 条目附带 access scope：

```json
{
  "entry_id": "demon_war_truth",
  "layer": "faction_propaganda",
  "content": "教会宣称恶魔是人类苦难的根源。",
  "access": {
    "visible_to": {
      "faction": ["church_of_flame"],
      "region": ["human_border_towns"],
      "education": ["church_educated"]
    },
    "hidden_from": {
      "faction": ["demon_refugees"]
    }
  },
  "confidence_policy": "belief_not_fact"
}
```

**Campaign Driver 结构：**

```json
{
  "driver_id": "border_scarcity_xenophobia",
  "type": "social_pressure",
  "description": "资源短缺会推动边境村民排外。",
  "signals": [
    {"condition": "food_price > threshold", "weight": 0.4},
    {"condition": "refugee_count > threshold", "weight": 0.3},
    {"condition": "recent_monster_attack", "weight": 0.3}
  ],
  "possible_events": [
    {"event_type": "market_argument", "probability": 0.3},
    {"event_type": "refugee_denied_entry", "probability": 0.2},
    {"event_type": "guard_patrol_intensified", "probability": 0.4}
  ],
  "severity": 0.45,
  "cooldown_ticks": 10
}
```

**Campaign Driver Signal 条件语法：**

Signal 条件是纯规则表达式，由 Campaign Driver 引擎在 tick ③ 阶段逐条求值。

```text
表达式语法（BNF 子集）：
  expr       := term (('AND' | 'OR') term)*
  term       := comparison | '(' expr ')'
  comparison := metric operator value
  operator   := '>' | '<' | '>=' | '<=' | '==' | '!='
  metric     := world_state 字段路径 | 聚合函数
  value      := number | string | bool

支持的 metric：
  - 世界状态字段：food_price, refugee_count, guard_alertness, player_suspicion_level
  - 聚合函数：COUNT(entities.faction=="church_of_flame"), AVG(relationships.trust)
  - 时间函数：ticks_since_last("event_type:monster_attack"), hour_of_day

示例：
  "food_price > 1.5 AND refugee_count >= 5"
  "guard_alertness > 0.6 OR ticks_since_last('monster_attack') < 20"
  "COUNT(entities.location=='town_square') > 10 AND hour_of_day >= 18"

求值规则：
  - 每个 signal 条件独立求值，结果为 bool
  - 所有 true 条件的 weight 之和 > severity 时，触发 possible_events 抽取
  - possible_events 按 probability 加权随机抽取 1 个（seed 控制，可回放）
  - 同一 driver 在 cooldown_ticks 内不重复触发
```

### 5.11 空间模型

Location 内部引入 Position Zone，提供空间语义而不引入物理引擎。

```json
{
  "location_id": "town_square",
  "zones": [
    {
      "zone_id": "center",
      "description": "广场中央，喷泉旁",
      "visibility": "high",
      "exposure": "high",
      "noise_level": "loud",
      "capacity": 20
    },
    {
      "zone_id": "market_corner",
      "description": "市场角落，摊位后面",
      "visibility": "medium",
      "exposure": "low",
      "noise_level": "moderate",
      "capacity": 5
    },
    {
      "zone_id": "alley_entrance",
      "description": "通往小巷的入口",
      "visibility": "low",
      "exposure": "low",
      "noise_level": "quiet",
      "capacity": 3
    }
  ],
  "connections": [
    {
      "to": "blacksmith",
      "distance": "adjacent",
      "noise_leak": 0.3,
      "visual_leak": 0.1,
      "description": "穿过一条短巷"
    },
    {
      "to": "tavern",
      "distance": "near",
      "noise_leak": 0.1,
      "visual_leak": 0.0,
      "description": "广场对面"
    }
  ],
  "ambient": {
    "light": "daylight",
    "weather": "clear",
    "crowd_density": "moderate"
  }
}
```

Zone 信息用于 Perception 生成的 deterministic 过滤：

- 同一 zone 内 → 完整感知
- 同一 location 不同 zone → 取决于 visibility / noise
- 相邻 location → 取决于 noise_leak / visual_leak
- 远距离 location → 无直接感知（除非特殊事件如爆炸、喊叫）

### 5.12 数值系统

数值系统是 Rules Engine 硬裁决和 Combat Subsystem 的底层基础。首版保持极简，只支持 frontier_town 场景所需的偷窃/说服/潜行/观察。

**基础属性（Attributes）：**

| 属性 | 代码 | 范围 | 影响场景 |
|---|---|---|---|
| 敏捷 | `dexterity` | 0.0 ~ 1.0 | 偷窃成功率、潜行、闪避 |
| 力量 | `strength` | 0.0 ~ 1.0 | 负重、破门、物理攻击伤害 |
| 感知 | `perception` | 0.0 ~ 1.0 | 发现隐藏物/人、Observation 细节度 |
| 魅力 | `charisma` | 0.0 ~ 1.0 | 说服、欺骗、贿赂成功率 |
| 体质 | `constitution` | 0.0 ~ 1.0 | 生命值上限、伤势恢复速度 |
| 智力 | `intelligence` | 0.0 ~ 1.0 | 解谜、魔法效果（如未来引入） |

**衍生状态（Derived Stats）：**

```json
{
  "hp": { "current": 80, "max": 100 },
  "stamina": { "current": 70, "max": 100 },
  "alertness": 0.3,
  "encumbrance": 0.15
}
```

- `hp` 由 constitution 决定：`max_hp = 50 + constitution * 100`
- `stamina` 用于 physical action，消耗后 tick 级恢复
- `alertness` 动态值，受环境/事件影响，影响 Observation 质量
- `encumbrance` 负重率，影响 movement 速度和 stealth

**阈值计算规则：**

```text
score = 玩家属性 + 环境修正(可正可负)
dc    = 基础阈值 + 对抗修正
margin = score - dc

其中：
- 玩家属性：如 dexterity for steal, charisma for persuade（范围 0.0 ~ 1.0）
- 环境修正：noisy_zone +0.2, dark_zone +0.15, crowded_zone +0.1, quiet_zone -0.1
- 基础阈值：0.5（中等难度）
- 对抗修正：目标 perception - 玩家 dexterity（差值的一半），下限 0

判定：
- margin > 0.2  → success
- -0.2 <= margin <= 0.2 → partial_success（需要 Arbiter 补充叙事）
- margin < -0.2 → failure
```

> **设计说明**：属性只参与一次计算（`score` 侧），不重复出现在 `dc` 中。避免旧版设计"属性既算加成又算被减数"导致的双重计算问题。

**首版数值示例（frontier_town）：**

| 角色 | dexterity | strength | perception | charisma | constitution |
|---|---|---|---|---|---|
| player_001（玩家默认） | 0.70 | 0.50 | 0.60 | 0.55 | 0.65 |
| npc.guard_b（守卫） | 0.50 | 0.65 | 0.75 | 0.40 | 0.70 |
| npc.blacksmith（铁匠） | 0.45 | 0.80 | 0.50 | 0.45 | 0.75 |
| npc.ele（艾蕾） | 0.65 | 0.40 | 0.70 | 0.80 | 0.50 |

**Action 与属性的映射：**

| Verb | 所属 Action Type | 主要属性 | 次要属性 | 裁决路径 |
|---|---|---|---|---|
| `steal` | `physical` | dexterity | perception | Rules Engine 硬裁决 |
| `persuade` | `social` | charisma | intelligence | Rules Engine 硬裁决 + Arbiter 边缘情况 |
| `sneak` | `physical` | dexterity | perception | Rules Engine 硬裁决 |
| `intimidate` | `social` | strength | charisma | Rules Engine 硬裁决 |
| `attack` | `combat` | strength | dexterity | Combat Subsystem |
| `defend` | `combat` | dexterity | perception | Combat Subsystem |
| `look` | `physical` | perception | — | Rules Engine 直接生成 Observation |

> `verb` 是具体动作语义标签，放入 `Action.params["verb"]` 中。`action_type` 保持 5 个高层类别不变。

**成长与衰减（长期规划，首版不实现）：**

```text
- 属性成长：通过反复成功使用该属性相关的 action，缓慢提升（每 10 次成功 +0.01）
- 属性临时 buff/debuff：食物、休息、伤势、装备影响
- 属性衰退：长期不使用不衰退（避免惩罚玩家），但临时 buff 会 tick 级衰减
```

---

## 6. 功能设计

### 6.1 输入体系

#### 6.1.1 输入方式

| 方式 | 示例 | 适用场景 |
|---|---|---|
| 自然语言 | "我轻声对艾蕾说快走" | 复杂、有修饰的动作 |
| 快捷指令 | `/whisper 艾蕾 快走` | 高频操作 |
| 对象选择+菜单 | 选择【艾蕾】→【对话】→输入"快走"→情态"轻声" | 消歧辅助 |
| 混合 | 点击艾蕾 + 输入"轻声说我们快走" | 降低输入负担 |

所有输入统一进入 Parsed Intent pipeline。

#### 6.1.2 自然语言预览

对象选择输入后，系统生成可编辑的自然语言预览：

```text
你轻声且紧张地对艾蕾说："我们先快离开这儿。"
```

玩家可编辑后再确认提交。预览本身不消耗 LLM 调用，由模板生成。

### 6.2 Coherence 体系

Coherence 不禁止输入，而是让世界内角色合理响应违和行为。

| 类型 | 示例 | 系统行为 |
|---|---|---|
| world_lore | 对铁匠说"用Python写Hello World" | 铁匠困惑，可能误解为某种咒语 |
| social | 当面拍国王肩膀说"兄弟借点钱" | 侍卫反应，国王不悦 |
| character | 胆小的玩家突然大喊挑战巨龙 | NPC 惊讶，可能认为是虚张声势 |
| physical | 重伤状态下翻墙跑酷 | Arbiter 判定高失败概率 |

Coherence Check 输出：

```json
{
  "coherent": true,
  "violations": [
    {
      "type": "social_coherence",
      "severity": 0.8,
      "description": "对国王使用了平民间的随意称呼",
      "world_reaction_hint": "侍卫可能介入"
    }
  ],
  "suggestion": null
}
```

系统不拦截，但 violation 会传递给 Arbiter 和 Observation 生成。

### 6.3 Observation 分发与注意力过滤

#### 6.3.1 过滤流程

```text
Event 发生
  → 列出所有可能在场的角色
  → Rules Engine 执行 deterministic 注意力过滤：
      1. 直接参与者 → 必然生成完整 Observation
      2. 正在对话/互动中的角色 → 高优先级
      3. 物理距离 + 感知通道 → 硬过滤（zone/visibility/noise）
      4. 角色当前状态 → 忙碌/休息/专注其他事 → 降级或跳过
  → 对通过过滤的角色生成 Perception
  → 对关键观察者生成 Interpretation：
      · 直接参与者（必须）
      · 与事件有利益/情感关联的角色
      · 与事件对象有信任/怀疑历史的角色
  → 其余角色只保留 Perception，Interpretation 延迟按需生成
```

#### 6.3.2 "没注意到"也是信息

被跳过的角色记录 `observation_skip`：

```json
{
  "observer_id": "npc.blacksmith",
  "source_event_id": "evt_001",
  "skipped": true,
  "reason": "busy_forging",
  "tick": 42
}
```

后续如果有角色问铁匠"你看到刚才发生什么了吗？"，系统能回答"他当时在打铁，没注意"，而不是编造。

### 6.4 LLM Arbiter

#### 6.4.1 输出结构

```json
{
  "arbiter_id": "arb_001",
  "source_action_id": "act_001",
  "outcome": "partial_success",
  "reason": "卫兵紧张且贪财，但周围有人，因此只会短暂迟疑。",
  "evidence_refs": [
    {"path": "npc.guard_b.traits.anxious", "value": true, "source": "trait"},
    {"path": "npc.guard_b.traits.greedy", "value": true, "source": "trait"},
    {"path": "world_state.locations.town_square.zones.center.exposure", "value": "high", "source": "world_state"},
    {"path": "relationship.guard_b.towards_player.suspicion", "value": 0.6, "source": "relationship"}
  ],
  "state_changes_proposed": [
    {"field": "npc.guard_b.alertness", "delta": +0.2, "reason": "被搭话引起警觉"}
  ],
  "confidence": 0.78,
  "narration_hint": "卫兵警惕地看了你一眼，手不自觉地摸向腰间的剑柄。"
}
```

#### 6.4.2 校验规则

- `evidence_refs` 必须指向输入上下文中真实存在的字段
- **evidence_refs 自动校验**：State Validator 对每条 evidence_ref 执行字段存在性检查，引用不存在的字段时拒绝整个 Arbiter 输出并记录 `llm_hallucinated_ref`
- `evidence_ref.path` 格式：点分路径，与 `StateChange.field` 同格式规则
- `evidence_ref.source` 用于分类（`trait` | `attribute` | `world_state` | `relationship`），方便 Debug Service 按类别展示
- 不允许引用未授权世界真相
- `state_changes_proposed` 必须经过 State Validator 才能生效
- Debug Service 必须能展示完整的证据引用链

**State Validator 对 Arbiter 输出的校验清单：**

| 检查项 | 规则 | 失败处理 |
|---|---|---|
| 字段存在性 | evidence_refs 中的每个引用必须在输入上下文中存在 | 拒绝输出，记录违规 |
| 数值边界 | state_changes_proposed 的 delta 必须在字段允许范围内 | 截断到边界值，记录警告 |
| 状态一致性 | 修改不能导致矛盾状态（如同一物品在两个位置） | 拒绝输出 |
| 权限检查 | LLM 不能修改它未被告知的世界真相 | 拒绝输出 |
| 因果检查 | 不能基于未观察事件生成 Memory/Belief 变更 | 拒绝输出 |
| Schema 合规 | 输出必须符合 Pydantic ArbiterOutput 模型 | 拒绝输出 |

### 6.5 叙事节奏控制

#### 6.5.1 Pacing Policy

Scheduler 的策略插件，控制世界推进节奏：

```json
{
  "policy_id": "default_pacing",
  "rules": [
    {
      "condition": "player_in_conversation",
      "tick_speed": "slow",
      "description": "玩家在对话中 → 世界放慢，不打断"
    },
    {
      "condition": "player_in_safe_area AND no_pending_events",
      "tick_speed": "fast",
      "description": "玩家在安全区且无待处理事件 → 快速推进"
    },
    {
      "condition": "recent_major_event AND no_reflection_completed",
      "tick_speed": "pause",
      "description": "刚经历重大事件但未触发反思 → 暂停推进"
    },
    {
      "condition": "campaign_pressure > critical_threshold",
      "tick_speed": "force",
      "description": "世界压力超临界 → 强制推进，不管玩家状态"
    }
  ]
}
```

#### 6.5.2 "世界来找你"

如果玩家长时间（可配置 tick 数）没有推进任何 Agenda，系统触发主动事件：

- 信使带来消息
- 同伴表达不满或关切
- 外部威胁升级到无法忽视
- NPC 主动找上门（"你在这里坐了很久了，出什么事了吗？"）

这由 Campaign Driver 的 idle 检测规则驱动，不需要额外模块。

### 6.6 Reflection Scene

Reflection Scene 是更新 Player Agenda 的叙事接口。

**触发条件：**

- 休息（营火、旅店）
- 重大事件后（NPC 死亡、世界观颠覆）
- 到达新区域
- 长时间目标漂移
- 系统推断 Agenda 已变化但缺少确认

**形式：**

梦境、营火夜谈、写日记、祈祷、同伴争吵、独自守夜、重伤幻觉、收到家书。

**输出：**

Agenda Update Proposal，交给玩家确认/改写/拒绝。

```json
{
  "reflection_id": "ref_001",
  "trigger": "major_event:ally_injured",
  "form": "campfire_conversation",
  "companion": "npc.ele",
  "narration": "火光映着艾蕾苍白的脸。她低声说：'你还记得我们为什么出发吗？'",
  "agenda_proposals": [
    {
      "action": "add",
      "drive": {
        "id": "drive_protect",
        "label": "保护身边的人",
        "strength": 0.7,
        "source": "reflection"
      }
    },
    {
      "action": "reconsider",
      "drive_id": "drive_investigate",
      "reason": "调查的代价是否太高？"
    }
  ]
}
```

### 6.7 错误恢复与优雅降级

#### 6.7.1 分层降级策略

```text
Level 0: LLM 正常
  → 完整 pipeline

Level 1: LLM 返回但 schema 不合法
  → retry 1 次
  → 仍失败 → 降级到 Level 2

Level 2: LLM 超时或不可用（或当前 tick LLM Budget 已耗尽）
  → Intent Parser: 降级为关键词匹配 + 模板解析
    ("对X说Y" → speech, "攻击X" → attack, "看" → look)
  → Arbiter: 跳过，Rules Engine deterministic 结果直接作为 outcome
  → Observation: 只生成 Perception（规则驱动），跳过 Interpretation
  → NPC Decision: 降级为基于 Relationship Snapshot 的简单规则选择
  → 告知玩家："当前响应较简略，NPC 反应可能不够细腻"

Level 3: 关键写入部分失败
  → Event 已写入但 Observation 失败 → 记录 pending_observation, needs_retry
  → Observation 已生成但 Memory 失败 → 标记 memory_gap
  → 下一个 tick 之前补全，补全失败则记录永久 gap
```

#### 6.7.2 LLM Budget 机制

每个 tick 设置调用上限，防止成本失控：

```json
{
  "tick_budget": {
    "max_calls": 8,
    "priority_order": [
      "arbiter_social_outcome",
      "parse_player_intent",
      "npc_decision",
      "observation_interpretation",
      "npc_dialogue",
      "reflection_prompt",
      "agenda_inference",
      "coherence_check"
    ]
  }
}
```

- 按优先级顺序执行，超预算时低优先级任务降级为规则驱动
- 远程 Provider（GPT）调用单独计数，优先保障其可用额度
- Budget 耗尽时记录 `budget_exhausted` 事件，供 Debug 追溯

#### 6.7.3 Content Pack 加载失败

```text
加载失败 → 尝试 minimal_fallback（1 地点 + 1 NPC + 空世界书）
  → 告知玩家"内容加载不完整，已使用最小配置"
  → 不崩溃
```

#### 6.7.4 Replay 中的容错覆盖

Fake LLM fixture 必须包含异常场景：

| fixture | 验证目标 |
|---|---|
| `llm_timeout` | 降级到 Level 2 |
| `llm_invalid_json` | retry + 降级 |
| `llm_partial_success` | 部分字段缺失时默认值填充 |
| `llm_forbidden_output` | State Validator 拒绝非法 proposed changes |
| `content_pack_malformed` | minimal fallback 启动 |

### 6.8 存档与读档机制

作为通用 RPG 世界运行时，save/load 是基础功能。设计目标是**确定性存档**——同一存档文件读档后，继续运行的结果与不间断运行一致。

**存档粒度：**

- **Quick Save（快速存档）**：按 tick 级快照，保存当前世界全部状态
- **Checkpoint（检查点）**：重大事件后自动触发（如 Reflection Scene、进入新 location、战斗结束）
- **Manual Save（手动存档）**：玩家主动触发

**存档内容：**

```json
{
  "save_id": "save_001",
  "save_type": "manual",
  "created_at": "2026-05-26T18:35:06+08:00",
  "tick": 42,
  "rng_state": "seed:42,consumed:0",
  "llm_fixture_version": "frontier_town_v1.0.0",
  "content_pack_id": "frontier_town",
  "content_pack_version": "1.0.0",
  "snapshot_hash": "sha256:a3f7c2...",
  "event_log_hash": "sha256:e9b1d4...",
  "world_state": {
    "entities": {},
    "events_cursor": 42,
    "current_location": "town_square",
    "current_zone": "center"
  },
  "subjectivity_state": {
    "memories": {},
    "beliefs": {},
    "relationships": {}
  },
  "player_state": {
    "agenda": {},
    "active_conversations": [],
    "input_history_cursor": 15
  },
  "scheduler_state": {
    "pacing_policy_id": "default_pacing",
    "campaign_driver_states": {},
    "npc_cooldowns": {}
  },
  "llm_budget_state": {
    "current_tick_calls": 3,
    "remaining_budget": 5
  }
}
```

**关键设计：**

- `rng_state`：完整的随机数生成器状态（含 seed + 已消耗计数），读档后精确恢复，保证后续事件序列与不间断运行一致
- `snapshot_hash`：对整个存档内容（除 hash 字段自身外）的 SHA-256 摘要，读档时校验完整性
- `event_log_hash`：对应 Event Log 文件的 hash，确保存档与 Event Log 版本匹配
- `events_cursor`：Event Log 是 append-only 的，存档只需记录读到哪一条
- `llm_fixture_version`：读档后若继续运行 Replay Test，必须匹配 fixture 版本
- `input_history_cursor`：玩家输入历史也是 append-only，记录处理到第几条

> **注意**：Event Log 可单独存储（大世界的性能考虑），但存档文件必须包含 `event_log_hash` 以校验一致性。小世界（MVP）建议 Event Log 内嵌在存档中，保证单文件可移植。

**读档流程：**

```text
① 加载 Content Pack（校验版本匹配）
② 重放 Event Log 从 0 到 events_cursor（重建世界状态）
③ 覆盖 world_state / subjectivity_state / player_state（应用快照差异）
④ 恢复 rng_state（精确重建随机数生成器状态）
⑤ 恢复 scheduler_state（cooldown、Pacing Policy）
⑥ 验证：重放后的状态与存档中的 snapshot_hash 一致
⑦ 验证：Event Log 的 hash 与存档中的 event_log_hash 一致
⑧ 从 tick+1 继续运行
```

**存档与 Replay 的关系：**

> Replay 的确定性要求与 fixture 规范详见 **12.6 Replay Tests**。

- Replay 是从 tick 0 开始重放完整历史
- 读档是从某个 tick 的快照恢复后继续运行
- 读档后的运行结果必须与"不间断运行到该 tick 再继续"一致（这是验证标准）

**存储方式：**

- 单文件 JSON（`saves/{save_id}.json`），便于版本控制和 diff
- 大世界的 Event Log 可单独存储，存档文件只保留 cursor
- 自动存档最多保留 10 个，手动存档无上限

### 6.9 系统建议谦逊度

```text
suggestion_mode = none | subtle | normal | guided
```

| 模式 | 行为 | 示例 |
|---|---|---|
| `none` | 系统不提供任何方向建议 | — |
| `subtle` | 偶尔通过 NPC 对话暗示 | "酒馆老板好像在找人帮忙" |
| `normal` | 在 Reflection Scene 中提出方向 | "你最近似乎更关注战争真相" |
| `guided` | 明确但不强制的建议 | "你可以考虑问问酒馆老板" |

任何模式下都不出现"下一步：去酒馆接任务"这种导航式提示。

### 6.10 战斗系统（最小可行版）

战斗系统是 Combat Subsystem 的最小可行实现，首版只支持 1v1 或 1vN 的简单遭遇战，不引入复杂回合制子系统。

**设计原则：**
- 战斗 action 不经过 LLM Arbiter，纯规则驱动（保证确定性）
- 战斗以 tick 为单位推进，每 tick 每个战斗者执行一个 combat action
- 战斗状态是 World State 的一部分，可被 Observation

**Combat Action 类型：**

| Action | 消耗 stamina | 效果 |
|---|---|---|
| `attack` | 15 | 造成伤害 = (attacker.strength * 30) - (defender.dexterity * 10)，最小 5 |
| `defend` | 10 | 本 tick 受到伤害减半，下 tick 先攻权重 +1 |
| `dodge` | 20 | 本 tick 有 dexterity 概率完全闪避，失败则受全额伤害 |
| `flee` | 10 | 敏捷判定 > 0.6 则脱离战斗，进入相邻 location |
| `use_item` | 5 | 使用药水/道具，效果由物品定义 |

**战斗初始化：**

```text
玩家输入 "attack guard" → Intent Parser 解析为 combat action
→ Action Composer 生成 attack action
→ World Core 检测到 combat action，初始化 Combat Session
→ Combat Session 写入 World State，参与者在当前 tick 锁定为 "in_combat"
```

**Combat Session 结构：**

```json
{
  "combat_id": "cbt_001",
  "started_at_tick": 45,
  "location_id": "town_square",
  "participants": [
    {"entity_id": "player_001", "side": "player", "hp": 80, "max_hp": 100, "stamina": 70, "initiative": 0},
    {"entity_id": "npc.guard_b", "side": "hostile", "hp": 60, "max_hp": 80, "stamina": 55, "initiative": 0}
  ],
  "turn_queue": ["player_001", "npc.guard_b"],
  "status_effects": [],
  "status": "active"
}
```

**每 tick 的战斗推进（Tick ④ Action Execution 阶段）：**

```text
① 收集本 tick 内所有 combat action（玩家 + NPC）
② 按 initiative 排序（defend 后 +1，dexterity 高 +0.5）
③ 逐个执行：
   - attack → 计算伤害 → 扣减目标 hp → 生成 combat_hit Event
   - defend → 标记 defend 状态 → 生成 combat_defend Event
   - dodge → 判定 dexterity roll → 生成 combat_dodge / combat_dodge_fail Event
   - flee → 判定 dexterity > 0.6 → 成功则生成 combat_flee Event，结束 Combat Session
④ 检查死亡：hp <= 0 → 生成 combat_death Event，标记 entity 状态为 "incapacitated"
⑤  stamina 恢复：每个存活参与者在战斗 tick 结束时 +5 stamina
⑥  生成 Combat Observation 给在场旁观者
```

**与 Tick Scheduler 的交互：**

- 战斗中的 tick 不加速（忽略 safe_area 的 fast 规则）
- 战斗中的 Conversation Session 强制中断（`status=interrupted`，不可恢复直到战斗结束）
- Pacing Policy 在 `combat_active` 条件下默认设为 `slow`，给玩家思考时间
- 外部 NPC 可以加入战斗（看到 combat_death Event 后，与战斗者同 faction 的 NPC 可能生成 attack action）

**战斗结束条件：**

- 一方全部 incapacitated（昏迷/死亡）
- 玩家 flee 成功
- 外部事件中断（如守卫队长喊停、地震、剧情事件）——由 Campaign Driver 生成 `combat_interrupt` Event

**战斗后的状态影响：**

> 数值系统与属性阈值计算详见 **5.12 数值系统**。

- `incapacitated` 的 NPC 无法执行 action，但仍可被 Observation
- 玩家 hp <= 0 时触发特殊 Reflection Scene（濒死体验/被救），不直接 Game Over
- 战斗结果写入 Event Log，影响后续 NPC 的 Belief（"player_001 打败了守卫" → 恐惧/尊敬 +）

**首版范围：**

- frontier_town 模板中不包含战斗场景，战斗系统只在代码层面预留接口
- border_war 模板中首次引入小规模冲突（玩家 vs 边境强盗）
- 不实现：魔法系统、群体攻击、连击、装备耐久、地形加成

---

## 7. 模块设计

### 7.1 模块清单

| 模块 | 文件 | 状态 | 职责概述 |
|---|---|---|---|
| Input Normalizer | `intent.py` | 新增 | 统一各类输入为标准格式 |
| Intent Parser | `intent.py` | 新增 | LLM 解析玩家意图为 Parsed Intent（Ollama 为主） |
| Conversation Manager | `conversation.py` | 新增 | 管理对话会话、指代消歧、中断/恢复 |
| Coherence Check | `coherence.py` | 新增 | 检测语境违和并生成 world reaction hint |
| Action Composer | `interaction.py` | 扩展 | 将 Intent 转为 Action batch，支持 Action 类型分类 |
| Interaction Service | `interaction.py` | 扩展 | 管理交互流程、clarification、suggestion |
| World Core | `world.py` | 扩展 | Action queue 合并、冲突检测、执行、Event 写入 |
| Rules Engine | `rules.py` | 扩展 | 硬规则校验、commitment 校验、空间过滤 |
| LLM Arbiter | `arbiter.py` | 新增 | 软裁决、evidence_refs、proposed outcomes（GPT） |
| State Validator | `validator.py` | 新增 | 六层校验（字段存在性、边界、一致性、权限、因果、Schema） |
| Tick Scheduler | `scheduler.py` | 新增 | tick 驱动调度、Pacing Policy |
| Campaign Driver | `campaign.py` | 新增 | signal 检测、event seed 生成、idle 检测（纯规则化） |
| Observation Dispatch | `observation.py` | 新增 | 注意力过滤、Perception 生成 |
| Subjectivity Service | `subjectivity.py` | 新增 | Perception → Interpretation → Memory → Belief |
| Relationship Snapshot | `relationship.py` | 新增 | 关系量化快照自动重算 |
| Memory Service | `memory.py` | 扩展 | Memory CRUD、分层压缩（Working/Short/Long-term）、Belief 更新 |
| Player Agenda Service | `agenda.py` | 新增 | Agenda 管理、推断（Reflection 统一确认）、Reflection 触发 |
| NPC Runtime | `npc_runtime.py` | 扩展 | 基于 subjectivity 决策、NPC 私密目标、NPC Interaction Scheduler |
| Campaign Loader | `campaign_loader.py` | 新增 | Content Pack 加载、校验 CLI、初始化 |
| LLM Orchestrator | `llm.py` | 扩展 | Provider 管理（Ollama + GPT 混合）、Budget 控制、Prompt 版本管理 |
| Persistence | `persistence.py` | 扩展 | 新实体持久化 |
| Debug Service | `debug.py` | 扩展 | 全链路 trace、replay、explain、budget 追踪 |

### 7.2 核心模块详述

#### 7.2.1 Intent Parser (`intent.py`)

**职责：**

- 接收 Input Normalizer 输出
- 调用 LLM 解析为 Parsed Intent[]
- 处理 commitment 解析
- 分离 performed_content / player_intent_note
- 利用 Conversation Session 做指代消歧

**非职责：**

- 不写 Event Log
- 不生成 Memory / Belief
- 不决定 NPC 是否知道玩家意图

**LLM 任务：** `parse_player_intent`

输出 schema 强制校验，失败时降级到关键词匹配。

#### 7.2.2 Conversation Manager (`conversation.py`)

**职责：**

- 创建、维护、结束 Conversation Session
- 管理 topic_stack 和 shared_context
- 处理中断和恢复
- 为 Intent Parser 提供指代消歧上下文
- 检测对话超时自动 abandon

**状态机：**

```text
null → active (玩家发起对话)
active → interrupted (突发事件 / 玩家走开)
interrupted → resumed (玩家回来 / NPC 主动接续)
active → concluded (自然结束)
active → abandoned (超时无输入)
```

**与 Interaction Service 的关系：**

Conversation Manager 管理对话状态，Interaction Service 管理交互流程。当 Interaction Service 检测到当前输入是对话类型时，委托 Conversation Manager 管理会话。

#### 7.2.3 Tick Scheduler (`scheduler.py`)

**职责：**

- 以 tick 为单位推进世界
- 应用 Pacing Policy 控制节奏
- 激活需要行动的 NPC
- 调度 Campaign Driver 检查
- 管理 tick 级别的事件队列

**tick 频率：**

- 默认：每个 player input 后推进 1 tick
- 自动推进：玩家空闲时按配置间隔自动推进
- 快速推进：安全区域 + 无事件时加速
- 暂停：重大事件后等 Reflection

#### 7.2.4 Campaign Driver (`campaign.py`)

**职责：**

- 维护所有活跃的 Campaign Driver
- 每个 tick 检查 signal 条件
- 满足条件时生成 event seed
- 管理 driver cooldown
- 检测玩家 idle 并触发"世界来找你"

**event seed 生命周期：**

```text
signal 满足 → 生成 seed → 进入 action queue → World Core 执行 → Event → Observation
```

#### 7.2.5 Observation Dispatch (`observation.py`)

**职责：**

- 接收 Event
- 执行注意力过滤（deterministic）
- 为通过过滤的角色生成 Perception
- 为关键观察者调用 LLM 生成 Interpretation
- 记录 skip 信息

**过滤规则优先级：**

1. 直接参与者 → 完整 Observation
2. 同 zone 且注意力在 → 完整 Perception + Interpretation
3. 同 location 不同 zone → 受限 Perception（取决于 visibility/noise）
4. 相邻 location → 极受限 Perception（仅大声/高亮事件）
5. 远距离 → 跳过，记录 skip

#### 7.2.6 Subjectivity Service (`subjectivity.py`)

**职责：**

- 将 Perception 转为 Interpretation
- 将 Interpretation / Memory 更新为 Belief
- 管理 conviction 和 would_revise_if
- 处理传闻、误会、证词冲突
- 保证 NPC 不获得未授权世界真相
- 自动重算 Relationship Snapshot

**Belief 更新规则：**

- 新证据 confidence > 现有 belief confidence → 更新 belief
- 新证据与现有 belief 矛盾 → 检查 conviction：
  - low → 直接降低 confidence
  - medium → 需要多条矛盾证据
  - high → 标记 challenged_by，不立即改变
  - dogmatic → 忽略，除非证据极端强烈

#### 7.2.7 Player Agenda Service (`agenda.py`)

**职责：**

- 保存和更新 Player Agenda
- 管理公开/私密/推断目标
- 从 player_intent_note 信号池聚合推断
- 生成 Agenda Update Proposal
- 支持 Reflection Scene 触发和确认
- 为 Interaction Service / Narrator 提供上下文

#### 7.2.8 State Validator（`schemas.py` / `validator.py`）

**职责：**

- 接收 LLM Arbiter 的 proposed state changes
- 执行六层校验（见 6.4.2 校验清单）
- 拒绝非法提案，记录拒绝原因
- 对数值越界做安全截断
- 输出经过校验的 `ValidatedOutcome`

**非职责：**

- 不调用 LLM
- 不生成叙事文本
- 不决定世界走向，只决定哪些变更合法

#### 7.2.9 NPC Interaction Scheduler（`npc_runtime.py` 或独立模块）

**职责：**

- 每个 tick，在玩家行动之后、NPC 自主决策之前，检查 NPC-NPC 交互机会
- 为同 location 且 `familiarity > threshold` 的 NPC 对生成候选交互
- 交互类型：对话、传闻传播、交易、冲突、合作行动
- 生成交互 seed 进入 Action Queue，与 player action 统一冲突检测

**触发条件（规则驱动，无需 LLM）：**

```text
1. 两个 NPC 同 location
2. Relationship Snapshot.familiarity > 0.3
3. 至少一方有值得分享的 Memory（salience > 0.5）
4. 随机概率通过（由 seed 控制，保证可回放）
5. 同一对 NPC 有 cooldown（避免每 tick 都交互）
```

**意义：** 这是"世界自己运转"的关键机制。即使玩家静止不动，NPC 之间也会传播信息、形成联盟或冲突。

#### 7.2.10 Content Pack 校验 CLI（`campaign_loader.py`）

**最小化内容创作工具（Phase 4 交付）：**

```bash
python -m rpg_demo validate content/frontier_town/
```

校验项：
- JSON Schema 合规（基于 Pydantic 模型自动生成）
- 引用完整性（relationship 中的 npc_id 必须存在于 entities）
- World Book 层级冲突检测（同一 entry 不能同时是 canonical_fact 和 faction_propaganda）
- Location connection 双向一致性
- Campaign Driver signal 字段引用合法性
- 初始 Agenda 与 world_premise 一致性

#### 7.2.11 Debug Service (`debug.py`)

**调试命令：**

```text
debug intent <request_id>       # 追踪 Intent 解析过程
debug arbiter <call_id>         # 查看 Arbiter evidence_refs
debug observation <event_id>    # 查看所有角色的 Observation
debug agenda <player_id>        # 查看 Player Agenda 全貌
debug campaign <campaign_id>    # 查看 Campaign Driver 状态
debug coherence <request_id>    # 查看 Coherence 违和检测
debug conversation <session_id> # 查看对话会话状态
debug relationship <npc_id> <target_id>  # 查看关系快照
debug belief <npc_id>           # 查看 NPC 所有 Belief
debug pacing <tick>             # 查看 Pacing Policy 决策
debug replay <scenario_id>      # 执行 replay
debug trace <tick>              # 完整 tick 执行 trace
debug budget <tick>             # 查看 LLM Budget 消耗
```

#### 7.2.12 World Core (`world.py`)

**职责：**

- 接收 Action Composer 输出的 action batch + NPC Runtime 输出的 NPC actions + Campaign Driver 输出的 event seed
- 执行 Action Queue 合并：按 tick 收集所有待执行 action
- 冲突检测：同一 tick 内多个 action 作用于同一目标时的优先级仲裁
- 排序：按 priority + time + dependency 确定执行顺序
- 逐个执行：Rules Engine → LLM Arbiter（如需要）→ State Validator → Event Commit
- Event Log 原子写入：保证 Event 不可变和顺序一致性

**Action Queue 结构：**

```python
class ActionQueue(BaseModel):
    tick: int
    actions: list[Action]
    
    def detect_conflicts(self) -> list[Conflict]:
        # 冲突类型：
        # 1. 目标独占冲突：两个 action 同时修改同一 entity 的同一字段
        # 2. 位置冲突：movement action 与 lock/close action 并发
        # 3. 对话冲突：玩家与 NPC A 对话时，NPC B 尝试插入对话
        return [c for c in conflicts if c.is_exclusive]
    
    def resolve_conflicts(self, conflicts: list[Conflict]) -> list[Action]:
        # 优先级：combat > physical > social > movement > speech
        # 同优先级：player action > NPC action > campaign event
        # 平局：按 action_id 字典序（seed 控制，可回放）
        return sorted(actions, key=lambda a: (
            -a.priority,
            0 if a.actor_id.startswith("player") else 1 if a.actor_id.startswith("npc") else 2,
            a.action_id
        ))
```

**执行流水线（tick ④）：**

```text
Action Queue
  → detect_conflicts() → Conflict[]
  → resolve_conflicts() → 排序后的 Action[]
  → 对每个 Action:
      Rules Engine 硬过滤（空间/属性/commitment）
        → 不通过 → 生成 failure Event
        → 通过且需要软裁决 → 调用 LLM Arbiter
        → 通过且纯规则 → 直接生成 Event
      State Validator 校验 proposed state changes
        → 拒绝 → 记录拒绝原因，回退到 Rules Engine 默认结果
        → 通过 → Event Commit（原子写入 Event Log）
  → 所有 Event Commit 后 → 触发 Observation Dispatch
```

**非职责：**

- 不解析玩家输入（Intent Parser 的职责）
- 不生成 NPC 行为（NPC Runtime 的职责）
- 不生成叙事文本（Narrator 的职责，未来扩展）
- 不直接调用 LLM（通过 Arbiter 间接调用）

**与 Persistence 的关系：**

Event Log 写入后，Persistence Layer 负责落盘。World Core 只维护内存中的当前 tick 状态，不直接操作文件/数据库。

#### 7.2.13 Rules Engine (`rules.py`)

**职责：**

- 硬规则校验：空间约束、属性阈值、commitment 校验、物理可行性
- 为 physical/combat/movement action 提供确定性裁决
- 为 LLM Arbiter 提供边界条件（如"dexterity 0.3 的角色不可能 steal success"）
- 生成硬裁决的 Event（无需 Arbiter 介入）

**规则分类：**

| 规则类型 | 校验内容 | 示例 |
|---|---|---|
| **空间规则** | zone 连通性、容量、visibility | 目标 zone 已满 → movement 失败 |
| **属性规则** | 属性 vs 阈值 | dexterity < threshold → steal 失败 |
| **commitment 规则** | commitment 级别是否允许执行 | considering → 不生成 Action |
| **状态规则** | entity 当前状态是否允许 action | incapacitated → 任何 action 失败 |
| **物理规则** | 负重、距离、环境 | encumbrance > 0.8 → movement 减速 |
| **战斗规则** | combat action 的优先级和结算 | attack vs defend 的伤害计算 |

**裁决输出：**

```python
class RulesOutcome(BaseModel):
    action_id: str
    verdict: Literal["pass", "fail", "needs_arbiter", "edge_case"]
    reason: str
    proposed_event: Event | None          # verdict=pass 时直接生成 Event
    arbitration_context: dict | None       # verdict=needs_arbiter 时传给 Arbiter
    
    # edge_case：属性接近阈值（如 dexterity 0.48 vs threshold 0.5），
    # 由 Arbiter 做叙事化裁决
```

**与 LLM Arbiter 的分工：**

```text
Rules Engine          LLM Arbiter
    |                      |
    |- pass ───────────────|→ 无需调用，直接 Event
    |- fail ───────────────|→ 无需调用，生成 failure Event
    |- needs_arbiter ─────→ 提供上下文，Arbiter 做叙事裁决
    |- edge_case ─────────→ 提供边界数据，Arbiter 决定成败
```

**非职责：**

- 不做社会推理（说服是否成功、NPC 是否被感动）
- 不生成叙事文本
- 不修改世界状态，只输出 verdict 和 proposed_event

> 数值系统与阈值计算详见 **5.12 数值系统**。战斗规则详见 **6.10 战斗系统**。

#### 7.2.14 LLM Arbiter (`arbiter.py`)

**职责：**

- 接收 Rules Engine 的 `needs_arbiter` 或 `edge_case` action
- 构建裁决上下文（玩家属性、NPC 属性、关系快照、环境状态、历史 Event）
- 调用 GPT 生成软裁决（ ArbiterOutput ）
- 保证 evidence_refs 指向真实存在的上下文字段
- 输出 narration_hint 供后续叙事生成

**裁决上下文构建：**

```python
class ArbiterContext(BaseModel):
    action: Action
    actor_attributes: dict              # 发起者的全部属性
    target_attributes: dict | None      # 目标的全部属性
    relationship_snapshot: RelationshipSnapshot | None
    location_state: Location            # 当前 location 和 zone 状态
    recent_events: list[Event]          # 最近 5 个相关 Event
    world_book_entries: list[str]       # 按角色 scope 过滤后的 World Book 条目
    coherence_violations: list[CoherenceViolation] | None
```

**裁决流程：**

```text
① 接收 action + RulesOutcome.arbitration_context
② 构建 ArbiterContext（从 World State 查询，不查询未授权数据）
③ 加载对应 action_type 的 prompt template（`content/prompts/arbiter_{action_type}/`）
④ 调用 GPT，timeout=10s，retry=1
⑤ Schema 校验（ArbiterOutput Pydantic 模型）
⑥ 失败 → 降级到 Rules Engine 默认结果
⑦ State Validator 校验 evidence_refs 和 state_changes_proposed
⑧ 通过 → 生成 Event
```

**非职责：**

- 不直接修改世界状态（必须通过 State Validator）
- 不生成完整叙事（只输出 narration_hint）
- 不做硬规则判断（Rules Engine 已过滤）

> Arbiter Output 的 schema 定义与校验规则详见 **8.2.6 LLM Arbiter Output 协议**。LLM Provider 分配策略详见 **9.4 LLM Provider 与调用策略**。

**与 State Validator 的关系：**

Arbiter 的 output 是"提案"，State Validator 是"审核"。Arbiter 不能跳过 State Validator，State Validator 也不能修改 Arbiter 的 reasoning，只能接受/拒绝/截断 state_changes。

#### 7.2.15 Memory Service (`memory.py`)

**职责：**

- Memory 的 CRUD（按 owner_id 分区）
- 分层压缩：Working → Short-term → Long-term 的自动迁移
- Belief 更新：基于新 Memory 和现有 Belief 的冲突检测
- 为 NPC Runtime 提供检索接口（按 layer + topic_tags + salience）
- 管理 `last_recalled_tick`，用于 salience 衰减计算

**压缩算法：**

```python
class MemoryCompressor:
    def compress_working_to_short(self, memories: list[Memory]) -> Memory:
        # 触发条件：Working Memory > 10 条且最旧条目 tick 距当前 > 10
        # 算法：
        # 1. 按 topic_tags 聚类
        # 2. 每类取 salience 最高的 3 条
        # 3. 调用 LLM（或模板）生成摘要
        summary = llm_summarize(memories, max_length=200)
        # 4. 生成 Short-term Memory，compression_of 指向被压缩的条目
        return Memory(
            content=summary,
            layer="short_term",
            compression_of=[m.memory_id for m in memories],
            salience=max(m.salience for m in memories)
        )
    
    def compress_short_to_long(self, memories: list[Memory]) -> Memory:
        # 触发条件：Short-term Memory > 20 条
        # 算法：同上，但摘要更压缩，保留价值观/刻板印象层面的信息
        summary = llm_summarize(memories, max_length=80, focus="values_and_stereotypes")
        return Memory(
            content=summary,
            layer="long_term",
            compression_of=[m.memory_id for m in memories],
            salience=max(m.salience for m in memories) * 0.8
        )
```

**Belief 更新算法：**

```python
def update_belief(owner_id: str, new_memory: Memory) -> BeliefChange:
    # 1. 检索现有 belief：topic_tags 交集匹配
    # 2. 若新 Memory 支持现有 belief → confidence + (salience * 0.1)
    # 3. 若新 Memory 矛盾现有 belief：
    #    - conviction=low → confidence - (salience * 0.2)
    #    - conviction=medium → 记录 challenged_by，confidence - (salience * 0.1)
    #    - conviction=high → 标记 challenged_by，不立即改变
    #    - conviction=dogmatic → 忽略（除非 salience > 0.9）
    # 4. 若 confidence <= 0 → belief 废除
    # 5. 若不存在相关 belief → 生成新 belief（confidence = salience * 0.5）
    # 6. 触发 Relationship Snapshot 自动重算
    existing = find_belief_by_topic(owner_id, new_memory.topic_tags)
    if existing:
        if is_supporting(existing, new_memory):
            existing.confidence = min(1.0, existing.confidence + new_memory.salience * 0.1)
        else:
            apply_challenge(existing, new_memory)
    else:
        create_new_belief(owner_id, new_memory)
    recalculate_relationship_snapshot(owner_id)
    return BeliefChange(owner_id=owner_id, updated_beliefs=[...])
```

**检索接口：**

```python
def retrieve_memories(
    owner_id: str,
    context_tags: list[str],
    layer: Literal["working", "short_term", "long_term", "all"] = "all",
    limit: int = 10
) -> list[Memory]:
    # 分层检索：
    # - working: 直接按 tick 范围取
    # - short_term: topic_tags 交集 + salience 排序
    # - long_term: topic_tags 交集 + salience 排序，若为空则取最近 3 条
    # 返回的 Memory 更新 last_recalled_tick
    results = filter_by_tags(query_memories(owner_id, layer), context_tags)
    if not results and layer in ("long_term", "all"):
        results = get_recent_memories(owner_id, "long_term", limit=3)
    for mem in results[:limit]:
        mem.last_recalled_tick = get_current_tick()
    return results[:limit]
```

**非职责：**

- 不生成 Observation（Observation Dispatch 的职责）
- 不做 NPC 决策（NPC Runtime 的职责）
- 不直接修改 Event Log（World Core 的职责）

> Memory 分层模型与检索规则详见 **5.6 Memory 与 Belief**。

#### 7.2.16 Coherence Check (`coherence.py`)

**职责：**

- 检测玩家输入与世界设定、社会规范、角色设定、物理规则的违和
- 对每种违和量化 severity（0.0 ~ 1.0）
- 生成 `world_reaction_hint`，供下游 Arbiter 和 Observation 使用
- **不拦截输入**，只标记 violation

**四种违和类型判定规则：**

| 类型 | 触发条件 | severity 计算 |
|---|---|---|
| `world_lore` | 输入内容包含不在 World Book 中的时代错位概念 | 0.3 ~ 0.6（如"Python"→0.6，"手机"→0.5，"飞机"→0.4） |
| `social` | 输入违反当前场景的社会等级规范 | 基于 actor 与 target 的 social_distance（身份差）× 行为冒犯度 |
| `character` | 输入与 actor 的已知 traits 严重矛盾 | 基于 traits 冲突数量，每条冲突 +0.25，上限 0.9 |
| `physical` | 输入要求超出 actor 当前物理状态的能力 | 基于属性差值（如 dexterity 0.2 尝试高难度 steal → 0.7） |

**social coherence 的 severity 公式：**

```python
def calc_social_severity(actor_id: str, target_id: str, action_type: str) -> float:
    social_distance = get_social_distance(actor_id, target_id)  # 0.0~1.0
    offense_level = get_offense_level(action_type)               # 0.0~1.0
    return min(1.0, social_distance * offense_level * 1.5)

# 示例：player（平民）对 king 拍肩膀说"兄弟"
# social_distance = 0.9, offense_level = 0.6 → severity = 0.81
```

**输出结构：**

```python
class CoherenceResult(BaseModel):
    coherent: bool                      # 只要有 violation 就为 false
    violations: list[CoherenceViolation]
    suggestion: str | None              # 可选的澄清建议

class CoherenceViolation(BaseModel):
    type: Literal["world_lore", "social", "character", "physical"]
    severity: float                     # 0.0 ~ 1.0
    description: str
    world_reaction_hint: str            # 如"侍卫可能介入"
```

**与下游模块的关系：**

- violation 传递给 Arbiter：影响社会 action 的成功率（severity > 0.5 时，Arbiter 倾向于 partial_success/failure）
- violation 传递给 Observation：在场 NPC 可能感知到违和行为，生成特殊 Observation

**非职责：**

- 不拦截或修改玩家输入
- 不直接生成 world reaction Event（只提供 hint，由 Arbiter 决定）

> Coherence 输出结构与示例详见 **6.2 Coherence 体系**。

#### 7.2.17 Action Composer (`interaction.py`)

**职责：**

- 将 Parsed Intent[] 转换为 Action batch
- 处理 commitment 级别：considering → 空列表；preparing → 预览 Action（不提交）；attempting/committed → 正式 Action
- 为 Action 补充缺失的上下文字段（zone_id、conversation_session_id）
- 合并同一 tick 内的多个 Intent（如 "走向铁匠铺并拿起锤子" → move + physical）

**Intent → Action 转换表：**

| Intent 类型 | Action 类型 | params 映射 |
|---|---|---|
| `speech` | `speech` | content → params.content, volume/emotion → params |
| `movement` | `movement` | target_ref → params.to_location, to_zone |
| `physical` | `physical` | target_ref → params.target, modifiers → params.modifiers |
| `social` | `social` | target_ref → params.target, approach → params.approach |
| `combat` | `combat` | target_ref → params.target, combat_action → params.combat_action |
| `look` | `physical` | params.content = "look around"（无 skill_check） |
| `wait` | `physical` | params.content = "wait"（无 skill_check） |

**commitment 处理：**

```python
def compose_actions(intents: list[ParsedIntent], tick: int) -> ActionBatch:
    actions = []
    for intent in intents:
        if intent.commitment == CommitmentLevel.CONSIDERING:
            continue  # 不生成 Action
        elif intent.commitment == CommitmentLevel.PREPARING:
            action = convert_intent_to_action(intent, tick)
            action.tags.append("preview")  # 标记为预览，不入 tick
            actions.append(action)
        else:  # ATTEMPTING / COMMITTED
            action = convert_intent_to_action(intent, tick)
            # 补充上下文
            action.zone_id = get_current_zone(intent.actor_id)
            action.conversation_session_id = get_active_session(intent.actor_id)
            actions.append(action)
    return ActionBatch(actions=actions, tick=tick)
```

**batch 合并规则：**

- 同一 tick 内，movement + physical 可合并为 sequential action（先移动后执行）
- speech + social 不可合并（social 包含 speech，优先取 social）
- 冲突的 Intent（如 "攻击守卫" + "贿赂守卫"）保留两者，由 World Core 的冲突检测处理

**非职责：**

- 不解析自然语言（Intent Parser 的职责）
- 不做规则校验（Rules Engine 的职责）
- 不生成 Event（World Core 的职责）

> Intent 的 commitment 四级与字段定义详见 **5.2 Intent**。Action 的字段约束详见 **5.3 Action**。

#### 7.2.18 Interaction Service (`interaction.py`)

**职责：**

- 管理玩家交互流程：clarification（歧义澄清）、confirmation（高风险确认）、suggestion（系统建议）
- 检测当前输入是否需要 clarification（ambiguities 非空、target_ref 模糊、multiple_intents）
- 生成系统建议（受 suggestion_mode 控制）
- 与 Conversation Manager 协作：对话类型输入委托给 Conversation Manager

**Clarification 状态机：**

```text
null → needs_clarification (Intent Parser 返回 ambiguities 非空)
needs_clarification → clarification_sent (系统生成澄清问题发给玩家)
clarification_sent → resolved (玩家回复澄清，重新解析)
clarification_sent → abandoned (玩家超时未回复，降级处理)
resolved → null

示例：
  玩家输入 "给他金币"
  → ambiguities=["'他'指代不明：可能目标有 guard_b, tavern_owner"]
  → 系统回复："你想把金币给谁？"
  → 玩家回复 "给守卫"
  → 重新解析，target_id="npc.guard_b"
```

**Confirmation 触发条件：**

| 条件 | 示例 | 系统行为 |
|---|---|---|
| commitment=preparing | "我准备拔剑" | 生成预览 Action，询问"确认执行？" |
| severity > 0.7 | 对国王失礼 | "这可能导致严重后果，确认？" |
| 不可逆 action | 丢弃关键物品 | "该物品无法找回，确认？" |
| combat 发起 | "攻击守卫" | "这将引发战斗，确认？" |

**Suggestion 生成流程：**

```python
def generate_suggestions(player_id: str, tick: int) -> list[Suggestion]:
    # 1. 读取 Player Agenda 的 current_drives
    # 2. 读取当前 location 的可交互对象
    # 3. 匹配：哪些可交互对象与 current_drives 相关
    # 4. 按 suggestion_mode 过滤：
    #    - none: 返回空列表
    #    - subtle: 只返回与 NPC 对话中自然带出的暗示
    #    - normal: 在 Reflection Scene 中提出
    #    - guided: 明确建议但非强制
    # 5. 返回 Suggestion[]，每条包含文案和可选的 action_preview
    agenda = get_player_agenda(player_id)
    interactables = get_interactables_at(player_id)
    matches = [obj for obj in interactables if is_relevant_to_drives(obj, agenda.current_drives)]
    suggestions = []
    for match in matches[:3]:
        suggestions.append(Suggestion(
            text=f"{match.name} 可能知道关于 {match.relevant_drive} 的事",
            action_preview=None if mode == "subtle" else ActionPreview(target=match.entity_id, action_type="speech")
        ))
    return suggestions
```

**与 Conversation Manager 的关系：**

- Interaction Service 判断输入类型：若 `intent_type=speech` 且存在 active conversation → 委托 Conversation Manager 处理
- Conversation Manager 返回更新后的 session → Interaction Service 继续后续流程
- 对话中断（如战斗触发）由 Interaction Service 发起中断请求，Conversation Manager 执行状态变更

**非职责：**

- 不解析 Intent（Intent Parser 的职责）
- 不管理对话状态（Conversation Manager 的职责）
- 不生成 Action（Action Composer 的职责）

> 输入方式与 clarification 场景详见 **6.1 输入体系**。suggestion_mode 的四种模式详见 **6.9 系统建议谦逊度**。

#### 7.2.19 Input Normalizer (`intent.py`)

> **注**：Input Normalizer 与 Intent Parser 共用 `intent.py`，但职责分离。Normalizer 处理原始输入的清洗和标准化，Parser 负责语义解析。

**职责：**

- 接收各种来源的原始输入（自然语言文本、快捷指令 `/whisper 艾蕾 快走`、UI 点击事件）
- 统一输出为 `RawInput` 结构，消除来源差异
- 快捷指令解析：将 `/command arg1 arg2` 映射为预置模板
- UI 事件转换：将点击/选择/菜单操作转换为自然语言描述

**输入来源处理：**

| 来源 | 示例 | Normalizer 输出 |
|---|---|---|
| 自然语言 | "我轻声对艾蕾说快走" | `{source: "natural_language", raw_text: "我轻声对艾蕾说快走", context: {}}` |
| 快捷指令 | `/whisper 艾蕾 快走` | `{source: "quick_command", command: "whisper", args: ["艾蕾", "快走"]}` |
| UI 选择+输入 | 点击【艾蕾】→ 选择【耳语】→ 输入"快走" | `{source: "ui_interaction", target: "npc.ele", action: "whisper", content: "快走"}` |
| 混合 | 点击艾蕾 + 输入"轻声说我们快走" | `{source: "mixed", target: "npc.ele", raw_text: "轻声说我们快走", input_method: "click+text"}` |

**快捷指令表（首版）：**

```python
QUICK_COMMANDS = {
    "/look": {"intent_type": "look", "params": {}},
    "/move": {"intent_type": "movement", "params_key": "destination"},
    "/whisper": {"intent_type": "speech", "params_keys": ["target", "content"], "modifiers": {"volume": "low"}},
    "/say": {"intent_type": "speech", "params_keys": ["target", "content"], "modifiers": {"volume": "normal"}},
    "/shout": {"intent_type": "speech", "params_keys": ["target", "content"], "modifiers": {"volume": "high"}},
    "/attack": {"intent_type": "combat", "params_keys": ["target"], "params": {"combat_action": "attack"}},
    "/wait": {"intent_type": "wait", "params": {}},
}
```

**非职责：**

- 不做语义解析（Intent Parser 的职责）
- 不查询世界状态（不知道"艾蕾"对应 `npc.ele`，只保留字符串）
- 不做指代消歧（Conversation Manager 的职责）

#### 7.2.20 Relationship Snapshot (`relationship.py`)

> **注**：Relationship Snapshot 与 Subjectivity Service 紧密协作，但独立维护在 `relationship.py` 中，提供只读快照供 NPC Runtime 快速查询。

**职责：**

- 监听 Belief 变化事件，自动重算指定 NPC 对指定目标的 Relationship Snapshot
- 提供缓存机制：同一 tick 内对同一 (npc_id, target_id) 的查询直接返回缓存
- 量化维度计算：trust / suspicion / fear / affection / respect / familiarity

**重算触发条件：**

```python
def should_recalculate(npc_id: str, target_id: str, belief_change: BeliefChange) -> bool:
    # 触发条件 1：belief 的 claim 中包含 target_id
    if target_id in belief_change.claim:
        return True
    # 触发条件 2：belief 的 topic_tags 与 target 相关
    if is_topic_related_to_target(belief_change.topic_tags, target_id):
        return True
    # 触发条件 3：belief 的 confidence 变化超过 0.2
    if abs(belief_change.delta_confidence) > 0.2:
        return True
    return False
```

**维度计算权重：**

| 维度 | 依赖的 Belief 类型 | 权重 |
|---|---|---|
| `trust` | 正面合作经历、承诺兑现 | 直接映射正面 belief confidence |
| `suspicion` | 负面观察、矛盾行为 | 直接映射负面 belief confidence |
| `fear` | 威胁性 belief、力量差距感知 | strength 差值 × 威胁 belief confidence |
| `affection` | 情感互动、共同经历 | 情感类 belief 的加权平均 |
| `respect` | 能力认可、地位差距 | 社会距离倒数 × 正面评价 belief |
| `familiarity` | 互动频率、认识时长 | 按 tick 累计的互动次数衰减函数 |

**缓存策略：**

- 内存缓存：每个 tick 开始时清空，tick 内多次查询同一对关系直接返回
- 持久化：Relationship Snapshot 作为历史数据写入 Persistence，支持回溯查询

**非职责：**

- 不生成 Belief（Subjectivity Service 的职责）
- 不做 NPC 决策（NPC Runtime 的职责）
- 不解释关系变化原因（Debug Service 的职责）

#### 7.2.21 Persistence (`persistence.py`)

**职责：**

- 提供统一的数据持久化接口，隔离上层模块与具体存储实现
- Event Log 的 append-only 写入（文件或 SQLite）
- Entity State 的快寸/读取（支持 point-in-time 查询）
- Observation / Memory / Belief 的 per-actor 分区存储
- 存档/读档的序列化/反序列化

**存储接口：**

```python
class PersistenceLayer:
    # Event Log
    def append_event(self, event: Event) -> None: ...
    def get_events(self, start_tick: int, end_tick: int) -> list[Event]: ...
    
    # Entity State
    def save_entity_state(self, tick: int, states: dict[str, Entity]) -> None: ...
    def load_entity_state(self, tick: int) -> dict[str, Entity]: ...
    
    # Per-actor Subjectivity
    def save_memories(self, owner_id: str, memories: list[Memory]) -> None: ...
    def load_memories(self, owner_id: str, layer: str | None = None) -> list[Memory]: ...
    def save_beliefs(self, owner_id: str, beliefs: list[Belief]) -> None: ...
    def load_beliefs(self, owner_id: str) -> list[Belief]: ...
    
    # Save / Load
    def write_save(self, save_data: SaveData) -> str: ...  # 返回 save_id
    def read_save(self, save_id: str) -> SaveData: ...
    def list_saves(self) -> list[SaveMetadata]: ...
```

**存储策略：**

| 数据类型 | 存储方式 | 格式 |
|---|---|---|
| Event Log | append-only 文件 | JSON Lines（每行一个 Event） |
| Entity State | 每 tick 快照 | SQLite（支持按 tick 索引查询） |
| Memory / Belief | per-actor 分区 | 每个 actor 一个 JSON 文件 |
| Relationship Snapshot | 历史序列 | SQLite（npc_id × target_id × tick 索引） |
| Player Agenda | 版本化文档 | JSON 文件，文件名含 tick 号 |
| Save File | 单文件 JSON | `saves/{save_id}.json` |

**数据库 Schema（SQLite）：**

```sql
-- Event Log：append-only，主键为 (tick, seq)
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,      -- "evt_{tick}_{seq}"
    tick INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    target_id TEXT,
    location_id TEXT NOT NULL,
    zone_id TEXT,
    summary TEXT NOT NULL,
    canonical_facts TEXT,           -- JSON serialized
    source_action_id TEXT,
    related_events TEXT,            -- JSON serialized list
    tags TEXT,                      -- JSON serialized list
    created_at TEXT                 -- ISO timestamp
);
CREATE INDEX idx_events_tick ON events(tick);
CREATE INDEX idx_events_actor ON events(actor_id);
CREATE INDEX idx_events_location ON events(location_id);

-- Entity State：每 tick 快照，支持 point-in-time 查询
CREATE TABLE entity_states (
    tick INTEGER NOT NULL,
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,      -- npc | item | location
    state TEXT NOT NULL,            -- JSON serialized Entity
    PRIMARY KEY (tick, entity_id)
);
CREATE INDEX idx_entity_states_entity ON entity_states(entity_id);

-- Memory：per-actor 分区，但仍存入 SQLite 便于查询
CREATE TABLE memories (
    memory_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    source_observation_id TEXT,
    tick INTEGER NOT NULL,
    content TEXT NOT NULL,
    salience REAL NOT NULL,
    decay_rate REAL NOT NULL,
    layer TEXT NOT NULL,            -- working | short_term | long_term
    topic_tags TEXT,                -- JSON serialized list
    last_recalled_tick INTEGER,
    compression_of TEXT             -- JSON serialized list of memory_id
);
CREATE INDEX idx_memories_owner ON memories(owner_id);
CREATE INDEX idx_memories_layer ON memories(owner_id, layer);
CREATE INDEX idx_memories_tags ON memories(owner_id, topic_tags); -- 实际使用 LIKE 查询

-- Belief：per-actor
CREATE TABLE beliefs (
    belief_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    claim TEXT NOT NULL,
    confidence REAL NOT NULL,
    conviction TEXT NOT NULL,       -- low | medium | high | dogmatic
    source_evidence TEXT,           -- JSON serialized list of memory_id
    challenged_by TEXT,             -- JSON serialized list of memory_id
    would_revise_if TEXT,           -- JSON serialized list
    formed_at_tick INTEGER NOT NULL,
    last_updated_tick INTEGER NOT NULL
);
CREATE INDEX idx_beliefs_owner ON beliefs(owner_id);

-- Relationship Snapshot：历史序列，支持回溯
CREATE TABLE relationship_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    npc_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    tick INTEGER NOT NULL,
    dimensions TEXT NOT NULL,       -- JSON serialized {trust, suspicion, fear, ...}
    last_interaction_summary TEXT,
    dominant_beliefs TEXT,          -- JSON serialized list
    updated_at_tick INTEGER NOT NULL
);
CREATE INDEX idx_rel_snapshots_pair ON relationship_snapshots(npc_id, target_id);
CREATE INDEX idx_rel_snapshots_tick ON relationship_snapshots(npc_id, target_id, tick);

-- LLM Call Log：用于 replay 和成本追踪
CREATE TABLE llm_calls (
    call_id TEXT PRIMARY KEY,
    tick INTEGER NOT NULL,
    task_type TEXT NOT NULL,
    provider TEXT NOT NULL,         -- ollama | gpt | fake
    prompt_hash TEXT NOT NULL,      -- sha256 of prompt
    raw_response TEXT,
    parsed_response TEXT,           -- JSON serialized
    latency_ms INTEGER,
    tokens_in INTEGER,
    tokens_out INTEGER,
    fallback_reason TEXT,           -- null if normal
    created_at TEXT
);
CREATE INDEX idx_llm_calls_tick ON llm_calls(tick);
CREATE INDEX idx_llm_calls_task ON llm_calls(task_type);
```

**文件系统布局：**

```text
data/
  events.logl              -- JSON Lines，append-only Event Log
  world.db                 -- SQLite，entity_states / memories / beliefs / relationships / llm_calls
  saves/
    save_001.json
    save_002.json
  content_packs/
    frontier_town/           -- 只读，从 content/templates/ 复制或软链接
```

**Replay 支持：**

- Event Log 的 append-only 特性天然支持 Replay
- 读档时重放 Event Log 到指定 tick，然后覆盖 snapshot
- Persistence Layer 提供 `get_events_cursor()` 返回当前已读取的 Event 位置

**非职责：**

- 不定义数据 schema（schemas.py / Pydantic 的职责）
- 不做数据校验（State Validator 的职责）
- 不管理缓存策略（各 Service 自行管理内存缓存）

#### 7.2.22 LLM Orchestrator (`llm.py`)

**职责：**

- 统一管理所有 LLM 调用入口，提供 `call(task_type, payload)` 接口
- Provider 路由：根据 task_type 配置选择 Ollama / GPT / Fake
- Budget 控制：每个 tick 的 `max_llm_calls` 上限管理
- Prompt 版本管理：加载 `content/prompts/{task_type}/{version}.txt`
- 成本记录、超时、重试、降级

**调用接口：**

```python
class LLMOrchestrator:
    def call(self, task_type: str, payload: dict, timeout: float = 10.0) -> LLMResult:
        # 1. 检查 Budget：当前 tick 剩余额度
        if not self.budget_manager.can_call(task_type):
            return LLMResult(fallback=True, reason="budget_exhausted")
        
        # 2. 选择 Provider
        provider = self.provider_registry.get(task_type)
        
        # 3. 加载 Prompt
        prompt_version = self.prompt_registry.get_active_version(task_type)
        prompt_template = self.prompt_registry.load(task_type, prompt_version)
        prompt = prompt_template.format(**payload)
        
        # 4. 调用 LLM（含 retry=1）
        raw_response = provider.call(prompt, timeout=timeout)
        
        # 5. Schema 解析
        parsed = self.schema_validator.parse(task_type, raw_response)
        
        # 6. 记录成本与日志
        self.cost_tracker.record(task_type, provider, raw_response.tokens)
        
        return LLMResult(raw=raw_response, parsed=parsed, provider=provider.name)
```

**Budget 管理：**

```python
class LLMBudgetManager:
    def __init__(self, max_calls: int = 8):
        self.max_calls = max_calls
        self.current_tick_calls = 0
        self.call_log = []  # 记录每次调用的 task_type 和 provider
    
    def can_call(self, task_type: str) -> bool:
        priority = PRIORITY_ORDER.index(task_type)
        # 高优先级任务始终允许（arbiter_social_outcome 优先）
        if priority <= 2:
            return self.current_tick_calls < self.max_calls
        # 低优先级任务：检查剩余额度
        remaining = self.max_calls - self.current_tick_calls
        return remaining > 0
    
    def record_call(self, task_type: str):
        self.current_tick_calls += 1
        self.call_log.append({"task_type": task_type, "tick": get_current_tick()})
    
    def reset_tick(self):
        self.current_tick_calls = 0
```

**Provider 切换策略：**

| 场景 | 行为 |
|---|---|
| Ollama 超时 | retry 1 次 → 仍失败 → 若 task 支持降级，返回 `fallback=True` |
| GPT API 错误 | retry 1 次 → 仍失败 → 若 Ollama 支持该 task，降级到 Ollama |
| Fake LLM 模式 | 直接返回 fixture，不调用任何真实 LLM |
| Budget 耗尽 | 低优先级 task 返回 `fallback=True`，高优先级 task 仍允许 |

**Prompt 版本管理：**

```python
class PromptRegistry:
    def load(self, task_type: str, version: str) -> str:
        path = f"content/prompts/{task_type}/{version}.txt"
        return read_file(path)
    
    def get_active_version(self, task_type: str) -> str:
        manifest = read_json("content/prompts/manifest.json")
        return manifest[task_type]["active_version"]
    
    def list_versions(self, task_type: str) -> list[str]:
        # 返回该 task_type 下的所有可用版本
        import os
        prompt_dir = f"content/prompts/{task_type}"
        if not os.path.exists(prompt_dir):
            return []
        versions = [f.replace(".txt", "") for f in os.listdir(prompt_dir) if f.endswith(".txt")]
        return sorted(versions)
```

**非职责：**

- 不定义 prompt 文案（content/prompts/ 目录下的文件职责）
- 不做业务逻辑裁决（Arbiter 的职责）
- 不管理世界状态（World Core 的职责）

---

## 8. 协议设计

### 8.1 协议清单

**新增协议：**

```text
docs/protocols/parsed-intent.md
docs/protocols/player-agenda.md
docs/protocols/content-pack.md
docs/protocols/world-book.md
docs/protocols/campaign-template.md
docs/protocols/conversation-session.md
docs/protocols/relationship-snapshot.md
docs/protocols/spatial-model.md
docs/protocols/pacing-policy.md
```

**扩展协议：**

```text
docs/protocols/action.md        (新增 conversation_session_id, zone_id)
docs/protocols/event.md         (新增 spatial_context)
docs/protocols/observation-memory-belief.md  (Perception/Interpretation 分层)
```

### 8.2 核心协议定义

以下给出各协议的核心字段、约束和状态机。完整协议文档见 `docs/protocols/*.md`，本文档给出 AI 生成代码所需的全部接口信息。

#### 8.2.1 Action 协议

```python
class Action(BaseModel):
    action_id: str                    # 格式: "act_{tick}_{seq}"
    source_intent_id: str | None      # 关联的 Intent
    actor_id: str                     # player_xxx 或 npc.xxx
    action_type: ActionType           # Enum: speech | movement | physical | social | combat
    target_id: str | None             # 目标对象 ID
    params: dict[str, Any]            # 动态 payload（协议层允许 Any；实现层优先用 TypedDict）
    zone_id: str | None               # 动作发生的 zone
    conversation_session_id: str | None
    tick: int
    
    @model_validator(mode="after")
    def validate_params_by_type(self):
        # speech: params 必须含 content
        # movement: params 必须含 to_location, to_zone
        # physical: params 必须含 verb (steal | climb | sneak | look ...)
        # social: params 必须含 verb (persuade | deceive | threaten | bribe)
        # combat: params 必须含 verb (attack | defend | dodge | flee)
        required = {
            ActionType.SPEECH: ["content"],
            ActionType.MOVEMENT: ["to_location", "to_zone"],
            ActionType.PHYSICAL: ["verb"],
            ActionType.SOCIAL: ["verb"],
            ActionType.COMBAT: ["verb"],
        }
        req = required.get(self.action_type, [])
        missing = [f for f in req if f not in self.params]
        if missing:
            raise ValueError(f"Action type {self.action_type.value} requires params: {missing}")
        return self
```

**约束规则：**
- `action_id` 全局唯一，格式 `act_{tick}_{seq}`，seq 按 tick 内顺序递增

> Action 与 Event 的领域模型定义详见 **5.3 Action** 与 **5.4 Event**。
- `actor_id` 为 `player_xxx` 或 `npc.xxx` 前缀
- `action_type=combat` 时，不经过 Arbiter，直接进 Combat Subsystem
- `action_type=movement` 时，Rules Engine 检查 zone 连通性和容量

#### 8.2.2 Event 协议

```python
class Event(BaseModel):
    event_id: str                     # 格式: "evt_{tick}_{seq}"
    event_type: EventType             # Enum 同 ActionType + system
    actor_id: str
    target_id: str | None
    tick: int
    location_id: str
    zone_id: str | None
    summary: str                      # 中性描述，不含主观判断
    canonical_facts: dict[str, Any]   # 动态 payload（协议层允许 Any；实现层优先用 TypedDict）
    source_action_id: str | None
    related_events: list[str]         # 关联 event_id 列表
    tags: list[str]                   # 用于索引和过滤
    
    @field_validator("summary")
    @classmethod
    def no_subjective_motive(cls, v):
        # 禁止 summary 中出现动机词（如"因为嫉妒"、"为了复仇"）
        forbidden = ["因为", "为了", "意图", "计划"]
        for word in forbidden:
            if word in v:
                raise ValueError(f"Event summary must be neutral, found: {word}")
        return v
```

**约束规则：**
- Event 一旦写入不可修改，只能追加修正 Event

> Event 的不可变性原则详见 **2.1 架构原则 A3**。
- `canonical_facts` 只记录可观察事实，不记录 Interpretation
- `summary` 必须中性，动机进下游 Observation 的 interpretation
- `event_type=system` 用于 Campaign Driver 生成的事件和状态变更

#### 8.2.3 Parsed Intent 协议

```python
class ParsedIntent(BaseModel):
    intent_id: str
    source: Literal["natural_language", "quick_command", "ui_interaction"]
    raw_text: str
    intent_type: IntentType            # speech | movement | physical | social | combat | look | wait
    actor_id: str
    target_ref: str | None             # 原始文本中的目标名称（可能模糊）
    target_id: str | None              # 解析后的确切目标 ID
    content: str | None
    modifiers: dict[str, Any]          # 动态 payload（协议层允许 Any；实现层优先用 TypedDict）
    commitment: CommitmentLevel        # considering | preparing | attempting | committed
    confidence: float                  # 0.0 ~ 1.0
    ambiguities: list[str]             # 解析时的模糊点，需 clarification
    performed_content: str | None      # 进入世界的可见内容
    player_intent_note: str | None     # 私密意图，不进入世界
    conversation_session_id: str | None
    timestamp: int                     # tick 号

class CommitmentLevel(str, Enum):
    CONSIDERING = "considering"    # 在想，不生成 Action
    PREPARING = "preparing"        # 在准备，生成预览但不提交
    ATTEMPTING = "attempting"      # 尝试，可能失败
    COMMITTED = "committed"        # 确定执行，正常 pipeline
```

**约束规则：**
- `commitment=considering` → 不生成 Action，不入 tick
- `commitment=preparing` → 生成预览 Action，NPC 不可感知
- `player_intent_note` 必须严格隔离，不得出现在任何 Event/Observation 中
- `ambiguities` 非空时，系统应触发 clarification 而非强行解析

#### 8.2.4 Observation / Memory / Belief 协议

```python
class Observation(BaseModel):
    observation_id: str
    observer_id: str
    source_event_id: str
    tick: int
    perception: Perception             # 结构化感官输入
    interpretation: Interpretation | None   # 可为空（非关键观察者）
    detail_level: Literal["full", "partial", "minimal"]

class Perception(BaseModel):
    channels: list[Literal["sight", "hearing", "smell", "touch"]]
    saw: str | None
    heard_keywords: list[str]
    heard_full_content: bool
    distance: Literal["near", "far", "adjacent"]
    attention_level: Literal["focused", "distracted", "unaware"]

class Interpretation(BaseModel):
    claim: str                         # 主观判断
    confidence: float                  # 0.0 ~ 1.0
    alternatives: list[str]            # 其他可能解释
    emotional_tone: str | None

class Memory(BaseModel):
    memory_id: str
    owner_id: str
    source_observation_id: str | None
    tick: int
    content: str
    salience: float                    # 0.0 ~ 1.0
    decay_rate: float                  # 每 tick 衰减量
    layer: Literal["working", "short_term", "long_term"]
    topic_tags: list[str]
    last_recalled_tick: int | None
    compression_of: list[str] | None   # 被压缩的 memory_id 列表

class Belief(BaseModel):
    belief_id: str
    owner_id: str
    claim: str
    confidence: float                  # 0.0 ~ 1.0
    conviction: Literal["low", "medium", "high", "dogmatic"]
    source_evidence: list[str]         # memory_id 列表
    challenged_by: list[str]           # 挑战该 belief 的 memory_id
    would_revise_if: list[str]         # 反证条件描述
    formed_at_tick: int
    last_updated_tick: int
```

**约束规则：**
- Observation 的 `interpretation` 可为空，但直接参与者必须有
- Memory 的 `layer` 转换由规则驱动，非 LLM 决定
- Belief 更新时，`conviction` 决定需要多少条矛盾证据才能动摇
- `would_revise_if` 用于快速检索可能改变 belief 的 Memory

#### 8.2.5 Content Pack 协议

```python
class ContentPack(BaseModel):
    content_pack_id: str
    schema_version: Literal["2.0"]
    world_premise: WorldPremise
    world_book: list[WorldBookEntry]
    starting_location: str
    initial_entities: list[Entity]
    initial_relationships: list[Relationship]
    initial_conversations: list[ConversationTemplate]
    player_agenda_template: AgendaTemplate
    style_guide: StyleGuide
    constraints: Constraints
    campaign_drivers: list[CampaignDriver]
    rule_presets: RulePresets

class WorldBookEntry(BaseModel):
    entry_id: str
    layer: Literal["canonical_fact", "public_belief", "faction_propaganda", 
                   "forbidden_knowledge", "local_rumor", "personal_truth"]
    content: str
    access: AccessScope                 # 可见/隐藏的角色属性条件
    confidence_policy: Literal["fact", "belief_not_fact"]
```

**约束规则：**
- `schema_version` 不匹配时 Campaign Loader 拒绝加载
- `initial_entities` 中的 `npc_id` 必须在 `initial_relationships` 中有定义或孤立声明
- `world_book` 中同一条 `entry_id` 不能同时出现在互斥的 `layer` 中

#### 8.2.6 LLM Arbiter Output 协议

```python
class EvidenceRef(BaseModel):
    path: str                           # 点分路径，如 "npc.guard_b.traits.anxious"
    value: str | float | bool | int     # 引用的值
    source: Literal["trait", "attribute", "world_state", "relationship"]

class ArbiterOutput(BaseModel):
    arbiter_id: str
    source_action_id: str
    outcome: Literal["success", "partial_success", "failure", "redirect"]
    reason: str                         # 叙事化裁决理由
    evidence_refs: list[EvidenceRef]    # 结构化引用，必须存在于输入上下文
    state_changes_proposed: list[StateChange]
    redirect_to_action_type: ActionType | None = None  # outcome=redirect 时必须提供
    confidence: float
    narration_hint: str | None          # 给 Narrator 的叙事提示
    
    @model_validator(mode="after")
    def validate_redirect_has_target(self):
        if self.outcome == "redirect" and self.redirect_to_action_type is None:
            raise ValueError("outcome='redirect' requires redirect_to_action_type")
        return self

class StateChange(BaseModel):
    field: str                          # 点分路径，如 "npc.guard_b.alertness"
    delta: float | int | bool | str     # 变更值
    reason: str
    
    @field_validator("field")
    @classmethod
    def validate_field_exists_in_context(cls, v, info):
        # State Validator 在运行时检查 field 是否存在于输入上下文
        # 此处不做静态校验，因为上下文是运行时动态构建的
        # 格式校验：field 必须是点分路径，如 "npc.guard_b.alertness"
        if not re.match(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$", v):
            raise ValueError(f"Invalid field path format: {v}")
        return v
```

**约束规则：**
- `evidence_refs` 必须指向输入上下文中真实存在的字段，否则 State Validator 拒绝
- `state_changes_proposed` 不允许修改未授权世界真相（如直接改 Event Log）
- `outcome=redirect` 时，必须提供 `redirect_to_action_type` 建议（模型层已校验）

### 8.3 Schema 版本管理

```text
schema_version: "2.0"

兼容策略：
- minor version 变更：向后兼容，新增可选字段
- major version 变更：可能破坏兼容，提供 migration 工具
- Content Pack 必须声明 schema_version
- Campaign Loader 根据版本选择解析逻辑
- Fake LLM fixture 必须声明其依赖的 schema_version
- 协议文档的 markdown 文件名包含版本号（如 action-v2.0.md）
```

---

## 9. 技术选型

### 9.1 语言与运行时

| 选型 | 决策 | 理由 |
|---|---|---|
| 语言 | Python 3.11+ | 当前已验证，类型提示成熟 |
| 架构 | 单进程模块化 | 当前阶段利于 replay、debug、schema 演进 |
| 接口 | CLI / TUI 并行 | 开发和测试用 CLI，体验测试用 TUI |
| 测试 LLM | Fake LLM + Ollama | CI 用 Fake，smoke test 用 Ollama |
| Schema | Pydantic v2 | 直接引入，避免手写 validator 后期迁移成本 |

### 9.2 Schema 方案

```text
Phase 1：Pydantic v2 定义全部核心模型（Intent / Action / Event / Observation / Memory / Belief / Content Pack）
Phase 2：基于 Pydantic 自动生成 JSON Schema，供 Content Pack 校验使用
Phase 3：如性能成为瓶颈，评估 Rust/PyO3 关键路径
```

**决策理由：**
- Pydantic v2 性能足够支撑当前阶段，且自带嵌套验证、JSON Schema 导出、序列化
- 手写 validator 在 10+ 个 schema 后维护成本极高，迁移工作量不可接受
- Fake LLM fixture 可直接实例化为 Pydantic model，测试代码更简洁

### 9.3 存储

| 数据 | 存储方式 | 要求 |
|---|---|---|
| Event | append-only log | 不可变 |
| Entity State | mutable snapshot | 支持 point-in-time 查询 |
| Observation / Memory / Belief | per-actor partition | 支持按 NPC 查询 |
| Relationship Snapshot | per-pair cache | Belief 变化时自动重算 |
| Memory Embedding Cache | per-actor file（可选） | Phase 5+ 启用，存储预计算的 embedding 向量，模型版本锁定 |
| Player Agenda | versioned document | 变化有 source |
| LLM Call | append-only log | 包含 raw output、parsed output、retry、fallback、budget 消耗 |
| LLM Budget | per-tick snapshot | 记录每次 tick 的预算分配和耗尽原因 |
| Prompt Template | versioned file | 内容 hash、A/B 测试标记 |
| Content Pack | file / DB | 加载记录可 replay |

### 9.4 LLM Provider 与调用策略

**混合部署策略：**

| 调用路径 | 推荐 Provider | 理由 |
|---|---|---|
| 高频、低延迟、可降级 | Ollama（本地） | Intent Parser、NPC Decision、Observation Interpretation 调用量大，本地运行成本低 |
| 低频、高稳定性、需复杂推理 | GPT（远程） | Arbiter、State Validator 输入、Campaign Driver 信号解析需要稳定输出质量 |
| CI / 回归测试 | Fake LLM | 确定性回放，零成本 |

**要求：**

- provider 可替换（Fake / Ollama / 远程），通过统一接口 `LLMOrchestrator.call(task_type, payload)` 调用
- 所有输出必须 schema validation（Pydantic）
- 远程 provider 需要：成本记录、超时、重试、redaction
- 每个任务类型有独立的 prompt template、schema 和 provider 偏好配置
- **LLM Budget 机制**：每个 tick 设置 `max_llm_calls` 上限，超预算时低优先级任务降级为规则驱动

**任务类型与默认 Provider：**

```text
parse_player_intent      → Ollama（高频，可降级为关键词匹配）
coherence_check          → Ollama
arbiter_social_outcome   → GPT（需复杂社会推理）
observation_interpretation → Ollama
npc_decision             → Ollama（高频）
npc_dialogue             → Ollama
agenda_inference         → Ollama（仅在 Reflection 触发，低频）
content_seed_expand      → GPT（内容生成质量要求高）
reflection_prompt        → GPT（叙事质量要求高）
```

**Prompt 版本管理：**

每个 prompt template 纳入版本控制：
- 版本号、内容 hash、作者、变更原因
- Fake LLM fixture 声明其依赖的 prompt 版本
- 支持 A/B 测试（同一任务类型可切换 prompt 版本）
- 存储位置：`content/prompts/{task_type}/{version}.txt` + `manifest.json`

---

## 10. 代码结构

### 10.1 目标结构

```text
src/rpg_demo/
  # === 核心世界 ===
  world.py              # World Core
  rules.py              # Rules Engine
  scheduler.py          # Tick Scheduler + Pacing Policy
  event.py              # Event 类型定义和工具

  # === 输入与交互 ===
  intent.py             # Input Normalizer + Intent Parser
  interaction.py        # Interaction Service + Action Composer
  coherence.py          # Coherence Check
  conversation.py       # Conversation Manager

  # === 主观世界 ===
  observation.py        # Observation Dispatch + Attention Filter
  subjectivity.py       # Perception → Interpretation → Belief
  memory.py             # Memory Service + Belief CRUD + 分层压缩
  relationship.py       # Relationship Snapshot

  # === 角色与内容 ===
  npc_runtime.py        # NPC Runtime + NPC Interaction Scheduler
  agenda.py             # Player Agenda Service
  campaign.py           # Campaign Driver
  campaign_loader.py    # Campaign Loader + Content Pack 校验 CLI
  content_pack.py       # Content Pack 类型和校验

  # === LLM ===
  llm.py                # LLM Orchestrator + Provider 管理 + Budget 控制
  arbiter.py            # LLM Arbiter

  # === 基础设施 ===
  persistence.py        # Persistence Layer
  schemas.py            # Pydantic Schema 定义
  validator.py          # State Validator（六层校验）
  debug.py              # Debug Service
  error_recovery.py     # 降级策略和容错
  tui.py                # TUI 界面

content/
  prompts/              # Prompt 版本管理
    manifest.json
    parse_player_intent/
      v1.0.0.txt
      v1.1.0.txt
    arbiter_social_outcome/
      v1.0.0.txt
    ...
  templates/
    frontier_town/
      content_pack.json
      world_book.json
      locations.json
      entities.json
      relationships.json
      campaign_drivers.json
      initial_agenda.json
      conversations.json
      replay_scenarios.json
    border_war/
      ...
    court_intrigue/
      ...
    small_party_journey/
      ...

docs/
  protocols/
    parsed-intent.md
    player-agenda.md
    content-pack.md
    world-book.md
    campaign-template.md
    conversation-session.md
    relationship-snapshot.md
    spatial-model.md
    pacing-policy.md
    action.md
    event.md
    observation-memory-belief.md
    memory-compression.md
    prompt-versioning.md
    llm-budget.md
  architecture/
    system-overview.md
    tick-scheduler.md
    subjectivity-model.md
    error-recovery.md
    state-validator.md
```

### 10.2 落地原则

1. 先协议，后模块
2. 先 fake fixture，后真实 LLM
3. 先 replay 场景，后扩展内容规模
4. 新模块优先薄封装，不追求首版完美
5. 每个模块独立可测试，不依赖运行时状态

### 10.3 Vibe Coding 开发方法论

本项目采用 **协议驱动 + AI 协作** 的开发模式。文档本身就是代码生成的规格书，AI 助手在明确的接口契约和测试矩阵约束下完成模块实现。

**为什么适合 Vibe Coding：**

- 协议驱动、模块化、强接口约束 → AI 擅长"按规格填空"
- Fake fixture + Replay test → AI 改代码后可自验证
- 大量 JSON schema / Pydantic model → AI 生成准确率高
- 状态机清晰、数据流单向 → 减少 AI 对隐式依赖的误判

**AI 协作的风险与防控：**

| 风险 | 防控策略 |
|---|---|
| 模块间隐式依赖遗漏 | 每个模块的输入/输出用 Pydantic 模型锁死，禁止隐式全局状态 |
| Tick 调度顺序搞混 | State Machine 和调度顺序由人类设计，AI 只实现函数体 |
| Prompt 工程走样 | Prompt 版本管理 + A/B 测试接口，文案由人类终审 |
| 协议漂移 | 任何协议变更必须通过 Schema Test + Contract Test |

**开发流程（每个模块）：**

```text
① 协议冻结：输出 Pydantic 模型 + 状态机 + 错误码枚举
② Fake Fixture：为每个 LLM 调用点编写确定性 fixture
③ 骨架代码：AI 生成模块接口 + 类型提示 + 空实现
④ 填充实现：AI 按协议和 fixture 填充函数体
⑤ 单元测试：pytest 覆盖正常路径 + 异常路径 + 边界条件
⑥ 集成测试：模块接入链路，Replay Test 通过
⑦ 真实 LLM：替换 Fake fixture，跑 smoke test
```

**代码规范（AI 必须遵守）：**

```text
- 类型提示 mandatory；公共接口避免裸 `Any`
- 动态 payload（`params`、`canonical_facts`、`modifiers` 等）优先用 `TypedDict` / `discriminated union` / `dict[str, str | float | int | bool | None]`，次选在模型层显式声明字段
- 函数长度 ≤ 60 行，超过必须拆分子函数
- 禁止隐式全局状态，所有依赖显式注入
- 每个公共函数必须有对应 Fake fixture test
- LLM 调用必须走 LLMOrchestrator.call()，禁止直接调 API
- Prompt 文本必须引用 content/prompts/ 中的版本化文件，禁止硬编码在代码中
```

**人类必须把关的三道关口：**

1. **State Validator 规则**：任何新增/修改校验规则需人类 review
2. **Tick 调度顺序**：Scheduler 的激活顺序和优先级由人类维护
3. **Prompt 文案**：叙事质量和角色一致性由人类终审

**每个 Phase 的 AI 可交付物：**

| Phase | AI 可交付物 | 人类验收标准 |
|---|---|---|
| Phase 0 | 协议初版 Pydantic 模型、docs/protocols/ 骨架 | Schema Test 全通过 |
| Phase 1 | Baseline 稳定化、CI 流水线、Debug 命令实现 | Replay Test 全通过 |
| Phase 2 | Intent Parser、Action Composer、State Validator、LLM Budget | Contract Test + INT-01~05 |
| Phase 3 | Memory 压缩、Belief 更新、NPC Interaction Scheduler、player_intent_note 信号池 | INT-06~12 + Replay 含异常路径 |
| Phase 4 | Campaign Loader、Content Pack 校验 CLI、Prompt 版本管理 | INT-07 + border_war 模板草案 |
| Phase 5 | Conversation Manager、Tick Scheduler、Pacing Policy | INT-08~10 |
| Phase 6 | Reflection Scene、Agenda Update、Suggestion Mode | INT-11 + small_party_journey |
| Phase 7 | TUI 展示、对象选择预览、多轮 clarification | 产品验收 PROD-01~12 |

---

## 11. 默认模板与测试矩阵

### 11.1 模板定义

| 模板 | 主题 | 核心能力验证 | 关键场景 |
|---|---|---|---|
| `frontier_town` | 偷窃、传闻、信任 | MVP 回归、Observation、Memory/Belief | 偷短剑 → 守卫观察 → 传闻传播 → 铁匠质问 |
| `border_war` | 宏观压力、宣传、目标变化 | World Book 分层、Campaign Driver、Player Agenda | 资源短缺 → 排外情绪 → 教会宣传 vs 真相 → 玩家目标转变 |
| `court_intrigue` | 礼仪违和、秘密、误解 | Coherence、Subjectivity、Conversation | 对贵族失礼 → 被误解为挑衅 → 秘密被部分观察 → 信任危机 |
| `small_party_journey` | 同伴旁观、关系变化、反思 | Partial Observation、Relationship、Reflection | 旅途中同伴旁观玩家选择 → 关系变化 → 营火反思 → Agenda 更新 |

### 11.2 每个模板包含

```text
content_pack.json          # 内容包主文件
world_book.json            # 世界书（含分层真相）
locations.json             # 地点和空间模型
entities.json              # NPC 和物品
relationships.json         # 初始关系
campaign_drivers.json      # 冲突驱动
initial_agenda.json        # 初始 Player Agenda
conversations.json         # 预设对话模板
replay_scenarios.json      # replay 测试场景
```

**frontier_town 关键文件示例：**

`entities.json`（节选）：

```json
{
  "entities": [
    {
      "entity_id": "npc.blacksmith",
      "entity_type": "npc",
      "name": "老格雷",
      "description": "镇上的铁匠，沉默寡言但手艺精湛。",
      "location": "blacksmith",
      "zone": "forge_area",
      "attributes": {"dexterity": 0.45, "strength": 0.80, "perception": 0.50, "charisma": 0.45, "constitution": 0.75},
      "traits": ["honest", "hardworking", "suspicious_of_strangers"],
      "faction": "townsfolk",
      "inventory": ["short_sword", "hammer", "iron_ingot"]
    },
    {
      "entity_id": "npc.guard_b",
      "entity_type": "npc",
      "name": "守卫伯恩",
      "description": "广场巡逻的卫兵，紧张且容易受惊。",
      "location": "town_square",
      "zone": "center",
      "attributes": {"dexterity": 0.50, "strength": 0.65, "perception": 0.75, "charisma": 0.40, "constitution": 0.70},
      "traits": ["anxious", "greedy", "loyal_to_duty"],
      "faction": "town_guard",
      "inventory": ["spear", "leather_armor"]
    },
    {
      "entity_id": "npc.tavern_owner",
      "entity_type": "npc",
      "name": "玛莎",
      "description": "酒馆老板娘，消息灵通。",
      "location": "tavern",
      "zone": "main_hall",
      "attributes": {"dexterity": 0.55, "strength": 0.40, "perception": 0.80, "charisma": 0.70, "constitution": 0.50},
      "traits": ["gossipy", "observant", "neutral"],
      "faction": "townsfolk",
      "inventory": ["ale_keg", "ledger"]
    },
    {
      "entity_id": "item.short_sword",
      "entity_type": "item",
      "name": "铁匠的短剑",
      "description": "一把做工精良的短剑，摆在铁匠铺的展示架上。",
      "location": "blacksmith",
      "zone": "forge_area",
      "properties": {"value": 15, "weight": 1.2, "stealable": true}
    }
  ]
}
```

`locations.json`（节选）：

```json
{
  "locations": [
    {
      "location_id": "town_square",
      "name": "小镇广场",
      "description": "边境小镇的中心，嘈杂而繁忙。",
      "zones": [
        {"zone_id": "center", "description": "广场中央，喷泉旁", "visibility": "high", "exposure": "high", "noise_level": "loud", "capacity": 20},
        {"zone_id": "market_corner", "description": "市场角落，摊位后面", "visibility": "medium", "exposure": "low", "noise_level": "moderate", "capacity": 5},
        {"zone_id": "alley_entrance", "description": "通往小巷的入口", "visibility": "low", "exposure": "low", "noise_level": "quiet", "capacity": 3}
      ],
      "connections": [
        {"to": "blacksmith", "distance": "adjacent", "noise_leak": 0.3, "visual_leak": 0.1},
        {"to": "tavern", "distance": "near", "noise_leak": 0.1, "visual_leak": 0.0}
      ],
      "ambient": {"light": "daylight", "weather": "clear", "crowd_density": "moderate"}
    },
    {
      "location_id": "blacksmith",
      "name": "铁匠铺",
      "description": "锻炉的热气和锤击声充斥着这里。",
      "zones": [
        {"zone_id": "forge_area", "description": "锻炉旁，工具架和展示台", "visibility": "high", "exposure": "high", "noise_level": "loud", "capacity": 4},
        {"zone_id": "storage", "description": "后面的储物间", "visibility": "low", "exposure": "low", "noise_level": "quiet", "capacity": 2}
      ],
      "connections": [
        {"to": "town_square", "distance": "adjacent", "noise_leak": 0.3, "visual_leak": 0.1}
      ],
      "ambient": {"light": "firelight", "weather": "indoor", "crowd_density": "sparse"}
    }
  ]
}
```

`relationships.json`（节选）：

```json
{
  "relationships": [
    {
      "from": "npc.guard_b",
      "to": "npc.blacksmith",
      "initial_snapshot": {
        "trust": 0.60,
        "suspicion": 0.10,
        "fear": 0.05,
        "affection": 0.30,
        "respect": 0.50,
        "familiarity": 0.70
      },
      "history": "守卫经常来铁匠铺修装备，关系尚可。"
    },
    {
      "from": "npc.guard_b",
      "to": "player_001",
      "initial_snapshot": {
        "trust": 0.30,
        "suspicion": 0.40,
        "fear": 0.10,
        "affection": 0.10,
        "respect": 0.20,
        "familiarity": 0.15
      },
      "history": "对陌生玩家保持警惕。"
    }
  ]
}
```

`campaign_drivers.json`（节选）：

```json
{
  "campaign_drivers": [
    {
      "driver_id": "shortage_suspicion",
      "type": "social_pressure",
      "description": "近期物资短缺导致镇民对陌生人更加警惕。",
      "signals": [
        {"condition": "player_suspicion_level > 0.3", "weight": 0.5},
        {"condition": "ticks_since_last('theft_event') < 30", "weight": 0.5}
      ],
      "possible_events": [
        {"event_type": "guard_questioning", "probability": 0.4},
        {"event_type": "merchant_refusal", "probability": 0.3}
      ],
      "severity": 0.50,
      "cooldown_ticks": 20
    }
  ]
}
```

### 11.3 Replay 场景示例

**frontier_town / scenario_stealth:**

```text
固定 seed: 42
固定 Content Pack: frontier_town
Fake LLM responses: frontier_town_stealth_fixture.json

步骤:
1. tick_1: 玩家输入"看看周围" → look intent
2. tick_2: 玩家输入"走向铁匠铺" → move intent
3. tick_3: 玩家输入"趁铁匠不注意拿走短剑" → steal intent, committed
4. tick_3: Rules Engine 判定 steal success (player dex 0.7 > threshold 0.5)
5. tick_3: Event 写入: player stole short_sword from blacksmith
6. tick_3: Observation Dispatch:
   - guard_b: 完整 Perception (同 location, 视线范围内)
   - tavern_owner: 无 Observation (不同 location)
7. tick_4: guard_b Memory 更新, suspicion +0.4
8. tick_4: guard_b 与 tavern_owner 互动, 传播传闻
9. tick_5: tavern_owner 获得 secondhand observation, suspicion +0.2
10. tick_5: 验证: blacksmith 尚不知情 (不在场)

预期输出: 与 fixture 完全一致
```

---

## 12. 验证与测试体系

### 12.1 测试分层

| 层级 | 目的 | 工具 | 覆盖范围 |
|---|---|---|---|
| Schema Tests | 数据结构合法性 | pytest + Pydantic | 所有协议和数据结构 |
| Unit Tests | 模块逻辑正确性 | pytest | 每个模块的核心函数 |
| Contract Tests | LLM 输入输出约束 | pytest + Fake LLM | 所有 LLM 任务类型 |
| Integration Tests | 模块间协作 | pytest + fixture | 关键链路 |
| Replay Tests | 确定性可重放 | pytest + 固定 seed | 全链路场景 |
| Debug Tests | 可解释性 | pytest | 所有 debug 命令 |
| Degradation Tests | 容错和降级 | pytest + 故障注入 | LLM 失败、内容加载失败 |

### 12.2 Schema Tests

覆盖所有数据结构：

```text
Parsed Intent
Action
Event
Observation (Perception / Interpretation)
Memory
Belief
Relationship Snapshot
Conversation Session
Player Agenda
Content Pack
World Book
Campaign Template
Campaign Driver
Pacing Policy
Spatial Model (Location / Zone / Connection)
LLM Arbiter output
```

### 12.3 LLM Contract Tests

每个 LLM 任务必须有 Fake LLM fixture，验证：

| 任务 | 校验重点 |
|---|---|
| `parse_player_intent` | commitment 正确、performed_content/player_intent_note 分离 |
| `coherence_check` | 违和类型正确、不禁止输入 |
| `arbiter_social_outcome` | evidence_refs 指向真实字段、不输出已提交 state changes |
| `observation_interpretation` | 不泄露未授权世界真相、confidence 合理 |
| `npc_decision` | 基于自身认知、不使用上帝视角 |
| `npc_dialogue` | 符合角色设定、不泄露 OOC 信息 |
| `agenda_inference` | evidence_refs 有据、不重复已拒绝路径 |
| `content_seed_expand` | 输出符合 Content Pack schema |
| `reflection_prompt` | 输出包含 Agenda Update Proposal |

异常场景 fixture：

```text
llm_timeout              → 验证降级到 Level 2
llm_invalid_json         → 验证 retry + 降级
llm_partial_success      → 验证默认值填充
llm_forbidden_output     → 验证 State Validator 拒绝
llm_hallucinated_ref     → 验证 evidence_refs 校验失败
llm_budget_exhausted     → 验证低优先级任务降级为规则驱动
```

**Prompt 版本测试：**

- Fake LLM fixture 必须声明依赖的 prompt 版本
- 切换 prompt 版本后，同一输入产生不同输出时，需更新 fixture
- Prompt manifest 格式校验（`content/prompts/manifest.json`）

### 12.4 Unit Tests

重点模块和场景：

**Intent Parser:**
- commitment 四级解析正确性
- performed_content / player_intent_note 分离
- 指代消歧（利用 Conversation Session）
- 快捷指令直接映射，不调用 LLM

**Coherence Check:**
- 四种违和类型正确分类
- 不阻断输入，只记录 violation

**Observation Dispatch:**
- 同 zone 完整感知
- 不同 zone 受限感知
- 相邻 location 噪音泄漏
- 忙碌角色跳过并记录 skip

**Subjectivity:**
- Perception → Interpretation 正确转换
- conviction 四级对 belief 更新的影响
- 传闻置信度衰减
- 证词冲突处理

**Memory Service:**
- Working Memory 超过阈值触发压缩
- Short-term Memory 主题摘要正确性
- 压缩后 Belief 不丢失
- 分层检索按情境注入正确 Memory 层级

**Relationship Snapshot:**
- Belief 变化触发自动重算
- 多维度量化正确性

**Player Agenda:**
- 信号聚合阈值和衰减
- 推断仅在 Reflection Scene 触发确认
- 拒绝路径不再重复
- inference_mode 配置生效

**Tick Scheduler:**
- Pacing Policy 条件匹配
- 对话中世界放慢
- 压力临界强制推进

**State Validator:**
- evidence_refs 字段存在性校验
- 数值边界截断
- 状态一致性检查（物品位置矛盾）
- 权限检查（LLM 不能改未知真相）

**NPC Interaction Scheduler:**
- cooldown 机制
- salience 阈值过滤
- 同 location + familiarity 触发
- seed 控制下可回放

**Error Recovery:**
- Level 2 降级后 Intent 解析仍可用
- pending_observation 补全机制
- Content Pack 加载失败 fallback
- LLM Budget 耗尽后规则降级

### 12.5 Integration Tests

| 编号 | 场景 | 验证重点 |
|---|---|---|
| INT-01 | 偷短剑 → 守卫观察 → 传闻传播 → 铁匠质问 | 全链路 MVP 回归 |
| INT-02 | 点击艾蕾 + 输入"轻声说快走" → 系统补目标 | 对象选择 + 自然语言混合输入 |
| INT-03 | "我准备拔剑" → 系统不提交攻击 | commitment = preparing |
| INT-04 | 低声给卫兵金币 → 同伴只获 partial observation | 注意力过滤 + 空间模型 |
| INT-05 | 对铁匠说编程概念 → 铁匠世界内误解 | Coherence + world reaction |
| INT-06 | 连续关注教会行为 → 系统推断 Agenda 变化 → 请求确认 | 意图信号聚合 |
| INT-07 | 从 Content Pack 冷启动新世界 → replay 通过 | Campaign Loader + 全链路 |
| INT-08 | 对话中途被突发事件打断 → 恢复后接续 | Conversation Manager 状态机 |
| INT-09 | 玩家长时间空闲 → "世界来找你"事件触发 | Campaign Driver idle 检测 |
| INT-10 | LLM 超时 → 系统降级运行 → 玩家可继续 | Error Recovery Level 2 |
| INT-11 | Reflection Scene 触发 → Agenda Update Proposal → 玩家确认 | 反思 + Agenda 更新（不弹窗打断） |
| INT-12 | NPC 的 high conviction belief 被矛盾证据挑战 → 不立即改变 | conviction 机制 |
| INT-13 | 守卫与酒馆老板同处一室 → 守卫传播玩家可疑行为 → 酒馆老板形成 secondhand belief | NPC Interaction Scheduler |
| INT-14 | LLM Budget 耗尽（高频 tick） → Arbiter 仍可用，Observation Interpretation 降级为规则 | Budget 机制 + 优先级 |
| INT-15 | 玩家经历 50+ tick → Memory 压缩触发 → NPC 仍能基于长时记忆识别玩家模式 | Memory 分层压缩 |

### 12.6 Replay Tests

要求：

- 固定初始 Content Pack
- 固定 random seed
- 固定 Fake LLM output（每个任务类型有 fixture）
- Intent → Event → Observation → Belief → Relationship → Agenda 全链路可重放
- 包含正常路径和异常路径（LLM 降级、部分失败、Budget 耗尽）
- 包含 Memory 压缩后的长时运行场景
- 包含 NPC-NPC 交互链的可回放性

### 12.7 Debug Explainability Tests

必须能解释：

```text
raw input → Parsed Intent 的解析过程
Parsed Intent → Action 的转换
Coherence 为什么触发、什么类型
LLM Arbiter 使用了哪些 evidence_refs
Event 为什么只写中性事实
旁观者为什么只知道部分信息
Player Agenda 为什么被建议更新
NPC 为什么产生某个 belief 或 action
Relationship Snapshot 为什么变化
Pacing Policy 为什么选择当前节奏
Conversation 为什么中断/恢复
降级策略为什么触发、降到哪一级
LLM Budget 为什么耗尽、哪些任务被降级
NPC 为什么与另一个 NPC 交互、传播了什么信息
Memory 为什么被压缩、压缩后保留了什么主题
```

---

## 13. 验收标准

### 13.1 产品验收

| 编号 | 标准 | 验证方式 |
|---|---|---|
| PROD-01 | 玩家可自由输入，也可通过对象选择辅助 | TUI 演示 |
| PROD-02 | "准备""考虑"不被当成已执行行为 | Integration Test INT-03 |
| PROD-03 | NPC 不知道玩家未公开的私密目标 | Integration Test + Debug trace |
| PROD-04 | NPC 对事件的理解可不完整、带偏见、带误会 | Integration Test INT-04 |
| PROD-05 | 玩家可接受、改写或拒绝系统建议目标 | Integration Test INT-06 |
| PROD-06 | 默认模板可快速启动试玩 | Campaign Loader 测试 |
| PROD-07 | 对话可被中断并恢复 | Integration Test INT-08 |
| PROD-08 | 玩家长时间空闲时世界仍运转 | Integration Test INT-09 |
| PROD-09 | LLM 不可用时系统可降级运行 | Integration Test INT-10 |
| PROD-10 | 系统建议不变成任务导航 | 人工审查 suggestion_mode 输出 |
| PROD-11 | NPC 之间会自主传播信息 | Integration Test INT-13 |
| PROD-12 | 长时间运行后 NPC 记忆不丢失关键信息 | Integration Test INT-15 |

### 13.2 工程验收

| 编号 | 标准 | 验证方式 |
|---|---|---|
| ENG-01 | 所有协议有 schema validation | Schema Tests 全通过 |
| ENG-02 | 所有 LLM 任务有 Fake fixture 和 contract test | Contract Tests 全通过 |
| ENG-03 | Content Pack 初始化可 replay | Replay Tests 全通过 |
| ENG-04 | Debug 能追踪全链路 | Debug Explainability Tests |
| ENG-05 | State Validator 拒绝非法 LLM proposals | Contract Test llm_forbidden_output |
| ENG-06 | 单进程架构可运行，无强制外部依赖 | CI 全绿 |
| ENG-07 | LLM 降级路径可测试 | Degradation Tests 全通过 |
| ENG-08 | 异常路径有 replay 覆盖 | Replay Tests 含异常 fixture |
| ENG-09 | Prompt 版本可切换且与 fixture 绑定 | Contract Tests 含 prompt 版本声明 |
| ENG-10 | LLM Budget 机制生效 | Degradation Tests 含 budget_exhausted 场景 |
| ENG-11 | State Validator 六层校验覆盖 | Unit Tests + Contract Tests 全通过 |

### 13.3 内容验收

| 编号 | 标准 | 验证方式 |
|---|---|---|
| CONT-01 | 至少 2 个默认模板可加载 | Campaign Loader 测试 |
| CONT-02 | 世界书支持至少 3 种 truth layer | World Book schema 测试 |
| CONT-03 | Campaign Driver 能生成动态 event seed | Integration Test INT-09 |
| CONT-04 | Reflection Scene 能产生 Agenda Update Proposal | Integration Test INT-11 |
| CONT-05 | 空间模型支持 zone 级感知过滤 | Unit Tests Observation Dispatch |
| CONT-06 | NPC-NPC 交互可配置 cooldown 和 salience 阈值 | Unit Tests NPC Interaction Scheduler |
| CONT-07 | Content Pack 校验 CLI 可检测引用完整性 | Campaign Loader 测试 |

---

## 14. 主要难点与解决方案

| 编号 | 难点 | 风险 | 解决方案 |
|---|---|---|---|
| D1 | LLM 过度解析玩家输入 | 把准备动作当已执行 | commitment 四级 + 高风险 preview/confirmation |
| D2 | 玩家意图泄露给 NPC | 破坏主观世界 | performed_content / player_intent_note 严格分离 |
| D3 | Event 过早写入动机 | 世界真相被主观判断污染 | 中性 event type，动机进 interpretation/belief |
| D4 | 世界书 prompt 过大 | 成本高且泄露知识 | 分层检索，按角色 scope 注入 |
| D5 | Campaign Driver 过抽象 | 无法产生可玩事件 | 每个 driver 绑定 signals + possible_events + cooldown |
| D6 | Player Agenda 太像任务日志 | 玩家自由感下降 | 目标可拒绝/改写，建议文案保持谦逊 |
| D7 | 默认模板维护成本高 | 模板失效影响测试 | 模板即测试矩阵，每个绑定 replay |
| D8 | schema 膨胀 | 开发被协议拖住 | 先最小 seed，分阶段扩展 |
| D9 | Observation 扇出导致 LLM 调用过多 | 性能和成本问题 | 注意力过滤前置 + 按需延迟 Interpretation |
| D10 | 对话状态与世界推进的冲突 | 对话被打断或世界静止 | Conversation Manager + Pacing Policy 协同 |
| D11 | 空间模型粒度选择 | 太粗没用，太细复杂 | Zone 级别（非物理引擎），deterministic 过滤 |
| D12 | 信念更新过于频繁或过于迟钝 | NPC 行为不稳定或固执 | conviction 四级 + would_revise_if 规则 |
| D13 | 信号聚合误判玩家意图 | 烦人的确认弹窗 | 高阈值触发 + 衰减 + 玩家可关闭推断 |
| D14 | LLM 降级后体验断崖 | 玩家感知到质量下降 | Perception 仍完整（规则驱动），只缺 Interpretation 深度 |
| D15 | 多玩家 Action 冲突 | 状态不一致 | Action queue 冲突检测（预留，当前单玩家不触发） |
| D16 | Replay 含 LLM 降级路径 | 确定性难保证 | 降级路径也是 deterministic（关键词匹配 + 规则引擎） |
| D17 | Ollama 性能波动导致 tick 延迟 | 本地模型推理速度不稳定 | Budget 机制 + 异步预加载 + 关键路径降级到 GPT |
| D18 | Content Pack 创作门槛 | 设计师手写 JSON 效率低 | 提供校验 CLI + 示例模板 + 错误提示友好化 |
| D19 | NPC-NPC 交互导致信息传播过快 | 世界失稳，所有 NPC 瞬间知道所有事 | Interaction Scheduler 的 cooldown + salience 阈值 + 传闻衰减 |

---

## 15. 路线图

> 本文档覆盖全部设计，但**编码实现按阶段递进**。下表明确每个阶段的交付物与边界，避免"终稿焦虑"。

| 阶段 | 交付版本 | 核心交付物 | 边界（不做什么） |
|---|---|---|---|
| Phase 0 | — | Schema 冻结 + Fake Fixture + 20+ Schema Tests | 不接入真实 LLM |
| Phase 1 | 1.0.0 Baseline | CI 全绿 + replay 稳定 | 不实现主观世界 |
| Phase 2 | 1.1.0 | Intent Parser → Action → Event → Observation 全链路 | 不实现 Belief/Reflection |
| Phase 3 | 1.1.1 | 主观世界（Perception/Interpretation/Belief/Memory 分层） | 不实现 Player Agenda 推断 |
| Phase 4 | 1.1.2 | Content Pack + Campaign Loader + 空间模型 | 不实现对话管理器 |
| Phase 5 | — | Conversation Manager + Tick Scheduler + Pacing Policy | 不实现 Agenda 自动推断 |
| Phase 6 | 2.0 | Player Agenda + Reflection Scene + 行为推断 | 不实现多人 |
| Phase 7 | — | TUI + 产品化 + 错误恢复 | 不实现客户端图形界面 |

---

### Phase 0：协议冻结（第 1-2 周）

- 完成系统级团队评审
- 冻结最小 schema：Parsed Intent、Action、Event、Observation、Content Pack
- 明确里程碑拆分
- 输出：`docs/protocols/` 初版

### Phase 1：1.0.0 Baseline 稳定化（第 3-4 周）

- 保持 MVP-0 replay 通过
- 保持 LLM contract tests 通过
- 保持 Debug explainability tests 通过
- 清理现有模块文档与实现差异
- 输出：稳定 baseline，CI 全绿

### Phase 2：1.1.0 交互闭环（第 5-8 周）

- Parsed Intent schema + Intent Parser（Ollama 为主，关键词降级）
- Input Normalizer + 快捷指令 + `/act`
- Action Composer + Action 类型分类（social / physical / combat / movement / speech）
- Coherence Check（Ollama）
- LLM Arbiter + evidence_refs（GPT，高稳定性要求）
- State Validator（六层校验）
- Partial Observation（注意力过滤初版，规则驱动）
- LLM Budget 机制初版
- 输出：自然语言输入 → 结构化 Event → 部分 Observation 全链路

### Phase 3：1.1.1 主观世界（第 9-12 周）

> **不拆分为两个子阶段**，统一推进主观世界闭环。

- commitment 四级
- performed_content / player_intent_note 分离
- Observation 拆分 Perception / Interpretation
- Belief conviction + would_revise_if
- Memory 分层压缩（Working / Short-term / Long-term）
- Relationship Snapshot 初版
- player_intent_note 信号池基础设施（仅记录，不触发聚合/确认/Reflection）
- NPC Interaction Scheduler 初版（NPC-NPC 传闻传播）
- Debug trace 全链路
- 输出：用 frontier_town 跑通"偷剑 → 旁观者部分感知 → NPC 基于误解行动 → 意图不泄露 → NPC 间传闻传播"

### Phase 4：1.1.2 通用内容运行时（第 13-16 周）

- Content Pack schema + Campaign Loader（Pydantic 自动生成 JSON Schema）
- Content Pack 校验 CLI
- World Book 分层真相 + 标签过滤检索
- Campaign Driver + signal 检测（纯规则化，不引入 LLM）
- 空间模型 Zone 初版
- Prompt 版本管理（`content/prompts/` 目录 + manifest）
- frontier_town 迁移为 Content Pack
- border_war 模板草案
- 初始化 replay
- 输出：从 JSON Content Pack 冷启动新世界，replay 通过

### Phase 5：对话与节奏（第 17-20 周）

- Conversation Manager
- Tick Scheduler + Pacing Policy
- "世界来找你" idle 检测
- 对话中断/恢复
- court_intrigue 模板
- 输出：多轮对话连贯 + 世界持续运转

### Phase 6：Player Agenda 与 Reflection（第 21-24 周）

- Player Agenda schema + Agenda Service
- 开局目标选择
- 行为推断目标变化 + 确认机制
- Reflection Scene 触发和 Agenda Update
- suggestion_mode
- small_party_journey 模板
- 输出：Player Agenda 全生命周期

### Phase 7：产品化增强（第 25-28 周）

- TUI 展示 intent / agenda / relationship
- 对象选择 + 自然语言预览
- 多轮 clarification
- 错误恢复全面覆盖
- 内容编辑和导入导出工具
- 输出：可试玩的产品原型

---

## 16. 长期规划

### 16.1 世界层

- 多区域、多派系、多经济压力
- 长期社会模拟（派系关系随时间演化）
- 世界书增量更新（事件改变世界认知）
- 派系宣传与历史叙事冲突的动态解析
- 世界事件链（一个 Campaign Driver 的 output 成为另一个的 signal）

### 16.2 角色层

- 更复杂的情绪模型（情绪衰减、情绪传染）
- 更丰富的价值观系统（价值冲突驱动内在矛盾）
- 证词冲突与信任传播网络
- NPC 私密目标与 NPC Agenda
- 同伴长期成长、离队、背叛、和解
- NPC 之间的 Relationship Snapshot（不只是 NPC → Player）

### 16.3 创作层

- Campaign Template 可视化编辑器
- 世界书导入导出工具
- LLM 辅助生成 Content Pack
- Replay 可视化回放器
- Prompt / schema 版本管理
- 社区分享 Content Pack

### 16.4 客户端层

- Web UI（可点击对象面板、行动预览、风险提示）
- Debug / Replay 可视化
- 移动端适配
- 语音输入

### 16.5 多人层

- 多玩家 Action 冲突检测和合并
- 玩家之间 Observation（A 能看到 B 在做什么）
- 共享 Agenda 和私密 Agenda
- 玩家间 Conversation Session
- 世界状态同步和一致性保证

---

## 17. 团队评审建议问题（已决策）

| 编号 | 问题 | 决策 | 理由 |
|---|---|---|---|
| 1 | 1.1.1 是否拆成两个里程碑？ | **不拆分** | 当前为项目完全重写阶段，聚焦主观世界闭环；Content Pack 通用化作为 1.1.2 独立推进 |
| 2 | Content Pack 先用 JSON 还是 DB？ | **JSON 目录** | 便于版本控制、diff 审查，后期性能不足再迁移 |
| 3 | Player Agenda 属于哪个模块？ | **独立模块**（`agenda.py`） | 横跨输入、主观世界、叙事多层，独立更清晰 |
| 4 | Campaign Driver 第一版规则化程度？ | **纯规则化**（signal 条件 + probability table） | 第一版不引入 LLM 生成事件，保证可预测性和可回放性 |
| 5 | Perception / Interpretation 是否单独持久化？ | **都持久化** | Interpretation 生成成本高，且是 Belief 更新的输入，需要可追溯 |
| 6 | World Book 如何控制 prompt 大小与知识泄露？ | **标签过滤 + 摘要注入** | 按角色属性（faction/region/education）做集合交集过滤，不全文注入 |
| 7 | 默认模板至少几个？ | **4 个**（frontier_town, border_war, court_intrigue, small_party_journey） | 刚好覆盖核心能力矩阵：偷窃传闻、宏观压力、礼仪秘密、同伴反思 |
| 8 | TUI 第一版展示多少信息？ | **极简** | 只显示当前 tick、location、最近 1-2 条 Event；Agenda/Intent 放 debug 模式 |
| 9 | 复合 action 是 transaction 还是部分成功？ | **部分成功** | 每个子 action 独立走 Arbiter，方便回放和解释 |
| 10 | evidence_refs 如何做自动校验？ | **State Validator 字段存在性检查** | 引用不存在的字段时拒绝整个输出并记录 `llm_hallucinated_ref` |
| 11 | Conversation Manager 与 Interaction Service 边界？ | **Conversation 管状态，Interaction 管流程** | Conversation 负责"对话是否在继续"，Interaction 负责"玩家这次输入要做什么" |
| 12 | Zone 粒度是否足够？ | **当前足够** | Zone 是叙事空间单位，非物理引擎；RPG 不需要坐标级精度 |
| 13 | tick 频率如何平衡？ | **1 tick / player input**，空闲时自动推进 | 安全区 + 无事件时加速；对话中放慢；重大事件后暂停等 Reflection |
| 14 | 多玩家预留到什么程度？ | **只在 Action Queue 预留冲突检测接口** | 其他模块暂不做多人兼容，避免过度设计 |
| 15 | LLM Provider 如何分配？ | **Ollama 为主，GPT 用于核心调度** | 高频调用走本地降低成本，高稳定性要求走远程保证质量 |
| 16 | Schema 方案如何选？ | **直接用 Pydantic v2** | 避免手写 validator 后期迁移成本；自动生成 JSON Schema 供 Content Pack 使用 |

---

## 18. 结论

这套系统的长期竞争力不在于单次回复写得像小说，而在于它能持续维护：

- 稳定的世界事实
- 可追溯的事件因果
- 角色局部认知和误解
- 世界持续运转的压力
- 玩家动态目标
- 可加载、可测试、可复用的内容包
- 连贯的对话和空间体验

推荐团队落地顺序：

```text
先稳定 baseline；
再完成交互闭环；
再推进主观世界；
再做通用内容运行时；
再做对话、节奏和 Agenda；
最后做产品化增强和多人。
```

每个阶段都有独立的验证闭环，不依赖后续阶段。如果某个阶段延期，已完成阶段的价值不受影响。

最终系统应成为一个**通用 RPG 世界运行时**，而不是一个固定剧情 demo。
