# 内容创作者指南 — 用 JSON 造一个会运转的世界

> 本引擎是一个**通用 RPG 世界运行时**：你写一个内容包（JSON），引擎不改一行代码，就能把它跑成一个有秩序、有信息不对称、会自主运转的世界。本文教你怎么写这个 JSON。
>
> 三个现成范例（从简到繁，建议照着改）：
> - `fixtures/content_packs/valid_frontier_town.json` —— 最小可玩（2 地点 3 NPC）
> - `fixtures/content_packs/lively_market_town.json` —— 热闹集市，触发 NPC-NPC 自主交互 + 压力事件
> - `fixtures/content_packs/frostgate_watchpost.json` —— 派系对立 + 分层真相（信息不对称的完整示范）

---

## 0. 最快上手：跑一个现成世界

```bash
./play.sh fixtures/content_packs/frostgate_watchpost.json          # 真实 Ollama
./play.sh fixtures/content_packs/frostgate_watchpost.json --fake   # 确定性 FakeLLM，无需模型
```

游戏内 `/help` 看全部命令；`/pack validate <path>` 校验你写的包。

---

## 1. 内容包骨架

一个内容包是单个 JSON 对象。**必填**：`content_pack_id`、`schema_version`（必须是 `"2.0"`）、`world_premise`、`starting_location`、`initial_entities`。其余可选。

```json
{
  "content_pack_id": "my_world",
  "schema_version": "2.0",
  "world_premise": { "era": "...", "tone": "grim", "central_tension": "..." },
  "starting_location": "gatehouse",
  "initial_locations": [ ... ],
  "initial_entities": [ ... ],
  "initial_relationships": [ ... ],
  "world_book": [ ... ],
  "campaign_drivers": [ ... ],
  "player_agenda_template": { ... },
  "style_guide": { ... },
  "constraints": { ... },
  "rule_presets": { ... }
}
```

> **校验先行**：写完先 `/pack validate`。`error` 级问题会让世界退回 fallback，`warning`/`info` 不阻断但值得看。

---

## 2. 实体（initial_entities）

每个 NPC / 玩家一个对象。

```json
{
  "entity_id": "npc.captain_brann",
  "entity_type": "npc",
  "location_id": "gatehouse",
  "zone_id": "watch_post",
  "attributes": {"strength": 0.75, "perception": 0.7, "faction": "watch", "region": "frostgate"},
  "traits": ["dutiful", "weary", "suspicious"],
  "hp": 100, "max_hp": 100, "stamina": 100
}
```

| 字段 | 说明与**真实行为** |
|---|---|
| `entity_id` | 唯一 id。NPC 习惯用 `npc.` 前缀（代词解析、过滤逻辑都依赖它）。玩家必须叫 `player_001`。 |
| `entity_type` | `"npc"` 或 `"player"`。每个包要有一个 player。 |
| `location_id` / `zone_id` | 起始位置。**location 不需要单独声明**——引擎从所有实体的 `location_id` 自动收集地点。zone 同理。 |
| `attributes` | 数值属性（0.0~1.0）：`strength/dexterity/perception/charisma/constitution/intelligence`。⚠️ **`faction`/`region`/`education` 也放这里**（虽然是字符串），world_book 的访问控制读的就是这里（见 §5）。 |
| `traits` | 性格标签。**直接喂给 LLM 影响 NPC 台词口吻**——`suspicious`/`talkative` 真的会让对话变。 |
| `hp` / `stamina` | 派生状态，可省（默认 100）。 |
| `home_location` | **可选**。NPC 的"岗位"。不写则默认 = 起始 `location_id`。NPC 倾向待在/回到 home，不会乱逛（见 §7）。 |

> **坑 1：faction 必须放 `attributes`，不能用 trait。** `attributes: {"faction": "watch"}` 才能被 world_book 过滤识别；写成 `traits: ["faction:watch"]` **无效**。

---

## 3. 地点与连接（initial_locations）

地点本身由实体的 `location_id` 自动产生；`initial_locations` 用来补充 **zone 信息** 和 **连接关系**（决定 NPC/玩家能走到哪、声音/视线如何泄漏）。

```json
{
  "location_id": "gatehouse",
  "connections": [
    {"to": "barracks", "distance": "adjacent", "noise_leak": 0.2, "visual_leak": 0.1, "description": "推开结霜的木门"},
    {"to": "refugee_camp", "distance": "near", "noise_leak": 0.1, "visual_leak": 0.0, "description": "穿过结冰的吊桥"}
  ]
}
```

- `distance`: `adjacent` / `near` / `far`。
- `noise_leak` / `visual_leak`（0~1）：相邻地点的声音/视线泄漏。高 noise_leak 意味着隔壁的喊叫能被听到（影响 Observation）。
- **连接是单向声明的**——想双向可走，两个地点都要写对方。校验会提示缺失的反向连接。

> **坑 2：玩家移动靠 LLM 把"去酒馆"解析成 location id。** 引擎已把合法 location 列表注入 LLM prompt，所以 NPC 不会乱编地点；但你的 location id 最好语义清晰（`tavern` 比 `loc_3` 好），中文别名（广场→town_square）已内置部分映射。

---

## 4. 关系（initial_relationships）

```json
{"npc_id": "npc.captain_brann", "target_id": "npc.refugee_kaze", "type": "suspicious"}
```

- 用于**校验**（引用的实体必须存在）和叙事氛围。
- ⚠️ **真实行为提醒**：`initial_relationships` **目前不会自动种子化 NPC-NPC 的"熟悉度"**。NPC 之间的熟悉度靠**共处时长**累积（同处一地每 tick 增长，约 4 tick 过阈值），到阈值后才会触发自主交互（见 §6）。所以想让两个 NPC 互动，**把他们放在同一个 location/zone**，而不是只声明关系。

---

## 5. 世界书与信息不对称（world_book）—— 引擎的灵魂

这是本引擎和"酒馆 AI"最硬的区别：**世界有客观真相，但每个 NPC 只看到自己背景允许的版本。**

```json
{
  "entry_id": "massacre_at_the_pass",
  "layer": "forbidden_knowledge",
  "content": "二十年前守军屠杀过求和的恶魔使节，被教会从记录中抹去。",
  "access": {
    "visible_to": {"faction": ["refugees"]},
    "hidden_from": {"faction": ["watch", "church"]}
  },
  "confidence_policy": "fact"
}
```

**六个真相层级**（`layer`）：

| 层级 | 含义 | 用法 |
|---|---|---|
| `canonical_fact` | 客观真相 | 给所有人 `visible_to: {"faction": ["all"]}` |
| `public_belief` | 普遍认知 | 大众都信的（可能是错的） |
| `faction_propaganda` | 派系宣传 | 只给某派系看、对敌对派系 hidden |
| `forbidden_knowledge` | 被压制的真相 | 只有受害方/知情者看得到 |
| `local_rumor` | 地方传闻 | 按 region 限定 |
| `personal_truth` | 个人经历 | 某个 NPC 独有 |

**access 匹配逻辑（实测行为）：**
1. **`hidden_from` 优先**——命中即不可见，覆盖一切。
2. 再看 `visible_to`——若指定了，实体必须**至少匹配一个**维度（faction/region/education）。
3. `"all"`（如 `{"faction": ["all"]}`）= 所有人可见。

**信息不对称的标准写法**（来自 frostgate）：同一主题写两条对立条目——
- `church_demon_propaganda`：`visible_to: watch+church`，`hidden_from: refugees`
- `massacre_at_the_pass`：`visible_to: refugees`，`hidden_from: watch`

结果：守军看到教会宣传、看不到屠杀；难民看到屠杀、看不到宣传；canonical fact 双方共享。**这就是设计的核心——误会、偏见、信息不对称，由内容包数据驱动，引擎零改动。**

> **坑 3：同一个 `entry_id` 不能出现在多个 layer**（校验报 error）。不同主题用不同 entry_id。

---

## 6. 让世界"活"起来：NPC-NPC 自主交互

NPC 之间会自发对话/传闲话/合作，**但这是涌现型的**，需要三个条件同时满足：

1. **同处一地**——把想互动的 NPC 放进**同一个 location**（zone 不必同）。
2. **熟悉度达标**——靠共处时长累积，约 **4 tick 同处**即过阈值（无需声明关系）。
3. **有可分享的记忆**——NPC 在世界里观察到事件后自然积累，通常几 tick 就有。

**配方**（来自 lively_market_town）：把 3 个 NPC 放在 `market_square` 同一地点 → 跑几十 tick → 它们之间自发出现 conversation / rumor / cooperation / trade。传闻会**跨 NPC 传播**（A 告诉 B，B 再告诉 C）。

> 默认 frontier_town 的 NPC 分处不同地点，所以**几乎不会**触发自主交互——这是设计（小世界），不是 bug。想看世界活起来，照 market_town 把 NPC 聚在一起。

---

## 7. NPC 行为：守岗 vs 游走

- NPC 有 `home_location`（默认=起始位置），**倾向待在岗位**，很少乱逛；被挪走时会**主动回家**。
- 想要一个 NPC 巡逻/游走，给它一个**没有 home** 的设定（目前需在代码层，内容层默认都有 home）——多数情况你想要的是"守岗"，所以默认行为通常就对。
- 玩家对某 NPC 说话时，**同地点的旁观 NPC 会自动安静**（不抢话），让你的对话有焦点。

---

## 8. 世界压力：campaign_drivers

让世界自己冒出事件（市场争吵、难民被拒、巡逻加强），不靠固定剧本。

```json
{
  "driver_id": "scarcity_xenophobia",
  "type": "social_pressure",
  "description": "寒冷与短缺把对难民的恐惧推向敌意。",
  "signals": [
    {"condition": "entity_count >= 5", "weight": 0.4},
    {"condition": "recent_event_count >= 6", "weight": 0.4},
    {"condition": "tick >= 3", "weight": 0.2}
  ],
  "possible_events": [
    {"event_type": "refugee_denied_entry", "probability": 0.35},
    {"event_type": "watch_patrol_intensified", "probability": 0.3}
  ],
  "severity": 0.5,
  "cooldown_ticks": 5
}
```

**触发规则**：每 tick 求值所有 signal，**命中条件的 weight 之和 ≥ severity** → 从 `possible_events` 按概率抽一个事件。`cooldown_ticks` 内不重复触发。

**signal 支持的指标（实测可用）**：
- `entity_count`（实体总数）、`event_count`（累计事件数）、`tick`（当前 tick）
- `recent_event_count`（最近 5 tick 事件数）、`recent_combat_count`、`combat_active`
- `player_hp`、`player_location`
- NPC 属性的平均值（如 `perception`）也会进 context
- 语法：`metric op value`，op ∈ `> >= < <= == !=`；可用 `and` / `or` 组合。

> **坑 4：severity 设太高 = 永远不触发。** 想让事件常出现，severity 设 0.4~0.5，配几个容易满足的 signal（如 `entity_count >= 5` + `tick >= 3`）。frostgate / market_town 都是这么调的。

---

## 8.5 世界事实与可玩闭环（world_state_vars + 涌现事实账本）

玩家可改变的世界事实（Channel C）写在 `world_state_vars`，每个变量：

```json
{ "var_id": "refugees_admitted", "label": "难民入营", "initial": false,
  "mutable": true, "set_by": ["watch_commander"], "request_keywords": ["开城门", "放进来"] }
```

- **`set_by`**：谁有权翻这个旗标。一个条目可以是 **NPC 的 `attributes.authority` 角色**
  （如 `"watch_commander"`），**也可以直接写 NPC 的 entity_id**（如 `"npc.captain_brann"`）——
  两者都能解析到同一个 NPC，用哪个看你方便。
- **`request_keywords`**：玩家自然语言请求里出现这些词，才会路由到这个变量的 Channel-C 裁定。
- 旗标**只在权威 NPC 被 LLM 仲裁判为 `success` 时翻转**；`partial_success`/拒绝都不翻。

### 让"调查 → 满足条件 → 翻旗"的长链闭环

大型调查/谈判剧情常常是"先拿到证据/承诺，再据此推进"。**别把中间步骤只写在对话里**——
把它们也声明成 `world_state_vars`：

- 例如终态 `archive_injunction_filed` 之前，先声明中间前置 `clinician_cosign_obtained`
  （`set_by` 指向有权签字的医师）。
- 玩家先去找医师 → 裁定 success → `clinician_cosign_obtained` 真的翻 True（**这就是 fulfillment
  的底真**：仲裁器本就看得见每个变量的当前值）。
- 玩家回到档案官请求提交禁令 → 仲裁器看到前置已为 True + 账本里先前的让步 → 判 success。

引擎会**自动记住** `partial_success` 时 NPC 当场提出的软条件（涌现事实账本），后续裁定复用——
你**不需要**写 `success_conditions` 或任何分支图，依赖关系由 LLM 从虚构 + 账本 + 可见旗标值自己推。
设计详见 [docs/design/emergent-fact-ledger.md](../design/emergent-fact-ledger.md)。

> **坑**：中间前置若不声明成变量，玩家"我已经拿到联签"只是口头声称、没有旗标背书，仲裁器会
> （正确地）不认账 → 长链卡死。声明成变量即可解锁。

---

## 9. 其余可选字段

- **`player_agenda_template`**：玩家初始目标（drives）。`inference_mode`（`off`/`reflection_only`/`normal`）控制系统多主动地推断玩家意图，默认 `reflection_only`（只在反思场景提议，不打扰）。
- **`style_guide`**：`narrative_voice` / `tone_references` / `prohibited_tropes`。**真实影响 LLM 叙事口吻**——这是基调换皮的关键。⚠️ **坑 5**：`world_premise.tone` 最好出现在 `tone_references` 里，否则校验给 info 提示。
- **`constraints`**：`max_entities_per_location`、`player_death`（`incapacitated_only` 等）。
- **`rule_presets`**：`difficulty`、`stealth_threshold_base`、`combat_lethality`。

---

## 10. 从零造一个世界 — 步骤清单

1. **定基调**：写 `world_premise`（era / tone / central_tension）+ `style_guide`（让 tone 出现在 tone_references）。
2. **画地图**：定几个 location id，在 `initial_locations` 写双向 `connections`。
3. **放角色**：`initial_entities`，每个 NPC 给 `location_id` + `traits`（影响口吻）+（如要信息不对称）`attributes.faction`。
4. **想让世界活**：把会互动的 NPC 放进**同一个 location**（§6）。
5. **写真相**：`world_book` 用分层 + access scope 制造信息不对称（§5）——这是引擎最值钱的能力。
6. **加压力**：写 1~2 个 `campaign_drivers`，severity 0.4~0.5，signal 用易满足的指标（§8）。
7. **校验**：`/pack validate <path>`，清掉所有 error。
8. **快验**：`./play.sh <path> --fake` 确定性跑一局，确认能加载、能移动、NPC 有反应。
9. **真验**：`./play.sh <path>`（真实 Ollama）跑几十 tick，玩家多发呆，看世界是否"活"——NPC 自发交互、传闻传播、压力事件、基调对味。

---

## 附：常见报错速查

| 现象 | 原因 | 解法 |
|---|---|---|
| 包退回 fallback（只剩 1 地点 1 NPC） | 有 `error` 级校验问题 | `/pack validate` 看 error |
| `Unsupported schema version` | `schema_version` 不是 `"2.0"` | 改成 `"2.0"` |
| NPC 看不到该看的世界书 | faction 写在 trait 里 / access scope 写反 | faction 放 `attributes`；核对 visible_to/hidden_from |
| NPC 之间从不互动 | 分处不同地点 / 没攒够熟悉度 | 放进同一 location，多跑几十 tick |
| 压力事件从不触发 | severity 太高 / signal 指标拼错 | 降 severity，用 §8 列出的指标名 |
| 玩家"去某地"被拒 | location id 太晦涩、无中文别名 | 用语义清晰的 id；必要时在 intent 层加别名 |
