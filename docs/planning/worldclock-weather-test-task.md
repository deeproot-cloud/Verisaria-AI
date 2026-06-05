# 测试任务：活世界——世界时钟 / 天气 / NPC 作息（slices 1/2/3a）

> 给测试 Agent 的任务简报。配套设计 `docs/design/worldclock-and-weather.md`。
> 这是一条**新线**（不是动态世界模型那条），但第 3 项回归直接关系到你正在跑的护送/谈判 pack。

## 背景

新增「活世界」时间/天气子系统，三片已落地（master，单测 1019 全绿）：

- **slice 1 世界时钟**：时间不是 `tick×常量`，而是挂在引擎已有的 `PacingSpeed` 上**变速流动**——对话/战斗一拍
  ≈几分钟，安静/`/skip` 一拍 ≈半小时。`WorldState.clock_minutes` 持久化；状态条显示 🌅晨/☀️昼/🌆暮/🌙夜 +
  第N天 HH:MM。包可声明 `world_premise.opening_time`（"黄昏"/"18:30"）。
- **slice 2 天气**：每个气候（温带/寒带/热带/干旱/海洋）是一条 mild→rough 阶梯，按世界时每小时做钳制 ±1
  随机游走；`stable_seed(pack_id)+hour` 播种 → 存档可重放。包可声明 `climate`/`opening_weather`。
- **slice 3a NPC 作息**：时段驱动 NPC 的 `home_location` 锚——白天离家外出、黄昏/夜里回家。**包级 opt-in、
  默认关**（`world_premise.npc_daily_rhythm`）。**为什么默认关**：loader 会把未声明 home 的 NPC 默认锚到出生地，
  所以常开会扰动你正在跑的回归 pack。默认关时行为与 P1.8 **逐字节一致**。

## 怎么观测（重要——和动态世界模型那条不同）

时钟/天气/作息**不是 Channel-C 内容**，`_clog` 不会记。最省事的观测面是 **snapshot + 事件流**，请在你的
driver 脚本里（参考 `scripts/run_escort_*.py` 的写法）：
- 每 tick 打印 `snapshot.time_of_day`、`snapshot.clock`、`snapshot.weather`；
- 收集 `NpcMoved` 事件（自主进出场会发它，带显示名）；
- 需要看 NPC 真实位置时用 `EngineSession.debug_god_view` 或直接读 `world.state.entities[*].location_id`。

开作息：在 pack 的 `world_premise` 里加（可一并试开场时刻/气候）：
```json
"world_premise": { "...": "...", "npc_daily_rhythm": true, "opening_time": "清晨", "climate": "寒带" }
```

## 怎么跑

真机 + `--log`，串行，全自然语言。三个场景：

1. **作息日循环（主）**：用一个**开了 `npc_daily_rhythm` 的 pack**（frostgate 的 3 地点拓扑就够看），从清晨起，
   `/skip` `/wait` 把时间推过一整天（晨→昼→暮→夜→次日晨）。**到空旷无人处 `/skip` 时间走得快**（≈30min/步），
   有人的地方走得慢。盯：白天 NPC 是否散开离家、黄昏/夜里是否回家归位；玩家驻足时能否看到 NPC 自主
   `NpcMoved`（"队长布兰 → 兵营"这种）。

2. **回归安全（关键）**：`escort_proving_ground.json` **不开 flag** 照常跑那条护送闭环链（去 yard 护送安雅→
   作证→请闸官开闸），确认 `anya_testimony_given ⟳FLIP → sluice_opened ⟳FLIP` 仍闭环（默认关应零回归）。
   然后**把同一 pack 开 flag** 再跑一遍，看 NPC 日间游走是否干扰谈判/护送——注意：被玩家点名对话中的 NPC
   不该乱走（对话优先级 in_conversation），只有旁观/闲置 NPC 才按作息动。

3. **时钟/天气合理性 + 存档（次）**：任意场景跑一段较长会话，看时间变速是否自然、天气是否在气候内缓慢漂移；
   存一次档、读回来，确认 clock + weather 一致、天气可重放（同 pack 同时刻应同一片天）。

## 关注点（逐条回答）

1. **作息观感**：NPC 是否形成可信日节律（白天散、夜里归），还是太频繁/太死板？给个粗略数字（如某 NPC 白天 vs
   夜里在家的比例，或一天里位置变化次数）。
2. **自主进出场**：玩家驻足时，NPC 走进/走出本地点是否作为 `NpcMoved`（**带显示名**，非 raw id）被玩家感知？贴例。
3. **回归（最关键）**：不开 flag，proving 链是否**照常闭环**（与上一次盖章一致）？开 flag 后，护送/谈判是否被 NPC
   游走拖累——贴反例（该在场的关键 NPC 跑掉导致卡链 / 或确认"被点名 NPC 不乱走"成立）。
4. **时钟变速**：贴几拍的 `clock` 增量——对话/战斗拍是否几分钟、空地 `/skip` 拍是否半小时量级？读着自然吗？
5. **天气**：贴一段较长会话的天气序列，是否缓慢、在气候阶梯内、无突变（如温带不该突然下雪）？存档重放是否同一片天？
6. **沉浸缺口（重要判断）**：时段/天气目前**只上了状态条，没喂进 LLM 的叙述/对白 prompt**——所以 NPC 不会说
   "天黑了""外面下着雪"，叙述也不提时辰天候。在真机里这缺口明显吗、影响沉浸吗？**值不值得做 slice 3b**
   （把时段/天气注入叙述 + NPC 对白上下文）？给个判断。

## 报告请包含

- 作息观感（数字/例子）+ 是否干扰谈判链（反例或"被点名不乱走"确认）。
- 回归结论：不开 flag 时 proving 是否照常闭环。
- 时钟变速、天气漂移、存档一致性各贴一例。
- **沉浸缺口判断：要不要 slice 3b**（这一条最想听你的真机感受）。
- driver 脚本 + 新 `*.log` + transcript 放 `reports/<新目录>/`。

## 一句话目标

确认活世界（变速时钟 + 气候天气 + NPC 作息）在真机里**可信、好玩、且默认关时对动态世界模型零回归**；并基于真机
手感判断「把时段/天气喂进 LLM 叙述/对白」这一步（slice 3b）值不值得做。

---

## ⏱ 第二跑（commit f1a5c98 起）—— slice 3b 已落地，真机看 NPC 是否真接住天候/时辰

第一跑结论：活世界三片真机可信、回归零、但**沉浸缺口明显**（状态条有雪/夜，NPC 与叙述失语）。已按你的建议补
**slice 3b**（两处，数据现成、player-perceivable、过 A5）：
- **NPC 对白 prompt**：`_environment_section` 末尾加「此刻是<时段>，<天气短语>。」（如「此刻是夜里，下着雪。」）。
- **过渡环境叙述**：一拍跨过时段边界或天气变化 → 发一句 ambient `Narration`（「天色渐渐暗了下来。」/「天气变了，
  下着雪。」），每拍至多一句时段 + 一句天气。

单测只能证 prompt **带上**了雪/夜；**它是否被 LLM 自然织进对白，只有真机能看**——这就是这一跑的重点。

### 怎么跑

挑一个**天候/时辰即张力**的场景（frostgate 最贴：风雪封山）。为了让雪/夜立刻在场、不靠漂移等：在 `world_premise`
直接声明 `climate: "寒带"` + `opening_weather: "雪"` + `opening_time: "夜里"`（再叠不叠 `npc_daily_rhythm` 都行，
本跑重点不是作息）。然后**真机对哨兵/队长连说几轮**（问问题、闲聊、求情都行），并 `/skip` 跨一次暮→夜 / 触发一次
天气变化。driver 里照旧打 `snapshot.time_of_day/clock/weather`，并把每条 `Narration` 事件原文收下来。

### 关注点（逐条回答）

1. **NPC 是否真接住天候/时辰**：下雪的夜里，哨兵/队长的回话里是否**自然提到这场雪 / 这个深夜**（如「这鬼天气，雪
   都堵到城门了」「这么晚了你还来」），还是仍是与当下无关的模板腔？贴 2–3 条真机原话对照。**这是 slice 3b 成不成的
   核心判据。**
2. **过渡叙述是否上得来**：`/skip` 跨暮→夜、或天气从晴→雪那一拍，事件流里是否出现「天色…暗…」「天气变了，下着雪。」
   这类 ambient `Narration`？是否**每拍至多一句时段 + 一句天气、不刷屏**？贴例。
3. **沉浸缺口是否真的合上了**：相较第一跑「活了一半」，现在时辰/天气是否**和已经很有生气的 NpcMoved 进出场对齐**了？
   主观判断：「活世界」是否更完整？
4. **零回归 / 零 fallback**：往对白 prompt 加这句**有没有引发 PARSE/VALIDATION 退化或拖慢**？`FALLBACK` 是否仍 = 0、
   tick 超时是否仍 = 0？（注：fallback 的 `reason=` 写真实原因——「仲裁/对白输出格式非法」是 schema 校验失败、API 其实健康。）
5. **A5 抽查**：env 行只喂了**玩家也能感知的**时段/天气，NPC 不应因此「知道」它本不该知道的东西；顺手确认无越界。

### 报告请包含

- NPC 接住天候/时辰：2–3 条真机原话（核心结论：自然接住 / 仍失语）。
- 过渡叙述：是否上得来、是否不刷屏（贴例）。
- 沉浸缺口是否合上（主观判断）。
- 零回归 / `FALLBACK=0` / 超时计数。
- 新 `*.log` + transcript + driver 放 `reports/<新目录>/`。

### 一句话目标

确认 slice 3b 让 **NPC 在真机里真的接住此刻的雪/夜**、过渡叙述不刷屏地补上时辰天候，从而把第一跑指出的沉浸缺口
**合上**——且对动态世界模型与 dialogue 链零回归、`FALLBACK=0`。

---

## ⏱ 第三跑（commit 7c8a177 起）—— `stationed` 守岗能否让「开 rhythm + 不卡链」成立

第二跑暴露的唯一保留：开 `npc_daily_rhythm` 后，**旁观的关键 NPC（闸官老康）日间 ×2.5 离岗游走、卡住护送链**
（引擎自发派生 `anya_and_kang_face_to_face`、终态未闭合）。这是「常开扰动护送 pack」的预期表现，但要让有权威
的包**能安全启用 rhythm**，需要一个让关键 NPC 守岗的开关。

已上 **slice 3c-a `stationed`**（commit `7c8a177`）：包给关键 NPC 标 `"stationed": true` → 该 NPC **永不自主
游走**（仍说话/张望/等待），即便世界在跑 rhythm 也始终在岗可达。`EntityState.stationed`（默认 False → 零行为
变化）；`_generate_for_npc` 两个移动分支前置 `not stationed`，RNG 照抽 → 复现性不变；离线实测守岗 NPC 日间离岗率
0%（普通 NPC 21%）。

**这一跑就盯一件事：把第二跑那条失败的 flag-on 跑，在闸官守岗后重跑——开着 rhythm，护送链还能不能闭合。**

### 怎么跑

`escort_proving_ground.json`，在 `world_premise` **开 `npc_daily_rhythm: true`**（与第二跑 flag-on 同），并给
**闸官老康**那条 `initial_entities` 记录加 `"stationed": true`（仅此一处声明式开关，注入内存副本，fixture 逐字节
不改）。可叠 `opening_time` 让开局落在日间（晨/昼，老康原本最容易离岗的时段）以构成最强压力测试。然后真机走那条
干净链：
```
去 yard 护送安雅到闸房（escort ⟳MOVED）
→ 对安雅说「当着老康的面把上游塌方讲清楚」（→ anya_testimony_given ⟳FLIP）
→ 对老康说「人证俱在，请开闸放水」（→ sluice_opened ⟳FLIP）
```
driver 里照旧打 `snapshot.time_of_day/clock/weather`，并收 `NpcMoved` 事件（盯老康是否还自发离岗）。

### 关注点（逐条回答）

1. **守岗是否成立**：开 rhythm 的日间，老康是否**全程待在岗位、零自发 `NpcMoved`**（对照第二跑 `★NPC_MOVED 闸官老康:
   闸房->院子`）？贴老康的位置序列 / 有无 NpcMoved。
2. **链是否闭合**：老康守岗后，护送链是否跑出完整 `anya_testimony_given ⟳FLIP → sluice_opened ⟳FLIP`——即
   **「开着 rhythm 也不卡链」成立**？贴 `⟳FLIP` 链路或卡点 `reason=`。
3. **其余 NPC 仍按作息动**：非 `stationed` 的 NPC（哨兵/难民等）开 rhythm 后是否照常日间散开/夜里归家、`NpcMoved`
   带显示名照常感知？（确认 `stationed` 只钉住被标的那个，没误伤整体活世界。）
4. **零回归 / FALLBACK=0 / 超时=0**：照旧。剔 fallback（`reason=` 写真实原因）。

### 报告请包含

- 守岗：老康位置序列 + 有无自发 NpcMoved（核心：守岗成立 / 仍离岗）。
- 链闭合：开 rhythm 下是否跑出 `… → sluice_opened ⟳FLIP`（贴链路或卡点）。
- 其余 NPC 作息是否照常（没被 `stationed` 误伤）。
- 零回归 / `FALLBACK=0` / 超时计数；新 `*.log` + transcript + driver 放 `reports/<新目录>/`。

### 一句话目标

确认 `stationed` 让**「开 `npc_daily_rhythm` + 关键 NPC 守岗 → 护送链照常闭合」**在真机成立——即活世界的日夜节律
现在可以**安全用在有权威的内容包**上，不再以卡链为代价；且不误伤其余 NPC 的作息、零回归、`FALLBACK=0`。
