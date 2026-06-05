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
