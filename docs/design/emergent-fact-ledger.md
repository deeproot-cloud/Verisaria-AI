# 涌现事实账本（Emergent Fact Ledger）

> 让引擎记住 **LLM 已经裁定出的中间事实**，使大型调查/谈判型内容包能闭环——
> 而不引入作者手写的剧情状态机。

## 0. 一句话

终态世界旗标只在 `success` 翻；但 `partial_success` 当场确立的中间事实/条件，由引擎
记下来，后续仲裁能复用。玩家体验保持开放，路径全由 LLM 在当下裁定，**零作者预写分支**。

## 1. 背景与动机

两个大内容包（`tidebreak_quarantine_harbor`、`skyglass_memory_inquest`）的真实
MiniMax 试玩都得到 `INCOMPLETE_OR_FAIL`：不是加载/解析失败，而是**剧情文本已给出条件，
系统状态无法承接**。NPC 演出了合理的让步（"我可以给你看，但别写我名字"），但：

- Channel C 当前是**严格二值**：[session.py `_handle_world_change_request`](../../src/verisaria/runtime/session.py)
  里，只要 outcome 不是 `success`，所有 `world.<var>` 变更一律 drop、**什么都不留**
  （当初修 playtest B4「嘴上说我再想想、世界却开了门」留下的设计）。
- 于是每轮谈判都从零状态开始；"证据已拿到 / 条件已承诺" 没有落点 → NPC 像集体失忆。

详见 `reports/engine_content_structure_recommendations.md` 及两份 test-agent 报告。

## 2. 设计哲学（已与产品方对齐）

- **目标**：稳定跑通、可玩、好玩的大内容包。**不**做自动回归测试框架（那是测试 Agent 的事）。
- **要的是**：引擎把**已经发生过的涌现裁定**沉淀下来，供后续复用。
- **不要的是**：作者预写完整任务节点、强行推进的剧情状态机。
- 因此本方案**只**记录 LLM 当场裁定的事实，**不**引入 `world_tree` / `test_routes` /
  `npc_profiles` 硬门 / 作者写死的 `success_conditions`。参见记忆 `emergent-fact-ledger-direction`。

### 为什么这同时提升自由度且削弱剧本感
- 任务状态机"有剧本感"是因为**条件由作者写死**（拿道具 X → 找 Y），可逆推、可刷。
- 账本的条件**源于当下虚构**：NPC 当场用自己的话说要什么就记什么，每个存档生成的条件都不同，
  没有清单可反推 → 读起来像"真实处境"而非"任务"。
- **命门**：收益完全押在「事实检索 + 仲裁一致性」上。做砸了会退化成"随意"（随机 ≠ 自由，
  随机 = 烦躁），比现在诚实但失忆的墙更糟。所以质量门槛硬：相关事实每次都要浮上来、
  arbiter 要当它已成立。

## 3. 数据模型

新模块 `engine/fact_ledger.py`：

```
EstablishedFact:
  text: str        # LLM 当场写的客观中间事实，如「森工愿交三号泵报告，条件是匿名」
  regarding: str   # 这条让步针对的 world_var id（如 "pump_failure_disclosed"）
  npc_id: str      # 做出让步的权威 NPC
  tick: int        # 何时

FactLedger:
  add(text, regarding, npc_id, tick)      # 同 var 同文本去重，更新 tick
  relevant(regarding) -> list[Fact]       # 按 regarding var 过滤（天然有界）
  get_state() / load_state()              # 进存档
```

v1 刻意不做：status/过期衰减、跨 var 依赖、lexical 相关性（按 `regarding` 过滤已够crisp）。

## 4. 三个触点

**① 写**（改 `_handle_world_change_request` 里 drop 那段）
终态旗标**仍不翻**（B4 不变量原样保住）；当 outcome == `partial_success` 且 arbiter 给了
`established_fact`，`ledger.add(text, regarding=var_id, npc_id=authority, tick)`。
`failure` / `redirect` 不写（没确立任何东西）。

**② 读**（改 `_world_vars_for_arbiter`）
给每个 var 附 `established_facts = ledger.relevant(regarding=var_id)`；arbiter prompt 渲成：
```
已确立的中间事实（先前交涉留下，可作裁定依据）：
  · 森工愿交三号泵报告，条件是匿名
```
并加约束：**"若这些事实显示先前条件【现在已满足】，可据此判 success；但中间事实本身不自动
成功，且当事人已背弃信任时你可推翻先前让步。"**

**③ 持久化**：账本进 save/load。

**schema**：`ArbiterOutput` 加可选 `established_fact: str | None`；prompt 要求
"partial_success 时填一句客观中间事实，否则留空"。仅此一处 schema 变更。

## 5. 不变量 / 红线

1. 旗标只在 `success` 翻；账本**永不**翻旗标。
2. 账本按 `regarding` 过滤，有界，不爆 prompt。
3. **隐形管线**——不发协议事件、不进任何面板，只喂 arbiter。绝不做成玩家可见的"条件 2/3"
   （一旦可视化成进度条就退回任务感）。
4. 事实是**判断输入，不是绑定合同**：arbiter 可推翻（反悔）。
5. A5：事实全来自玩家自己先前的裁定请求，喂 arbiter 安全。

## 6. 并行 P0：播种 RelationshipStore（独立真 bug）

已核实：`initial_relationships` 只用于 `/who`·`/talk` 的 attitude 文字标签
（[session.py:2508/2548](../../src/verisaria/runtime/session.py)）与 loader 引用校验
（[campaign_loader.py:127](../../src/verisaria/engine/campaign_loader.py)），**从不进
RelationshipStore**。而 arbiter 读的是运行时关系快照 → pack 写的开局关系对裁定零影响。

修：load 时 `_seed_initial_relationships()`——有显式 `dimensions` 直接用，否则按
`type→dimensions` 小表映射（procedural_neutrality→familiarity 0.1，formal_suspicion→
suspicion 0.2，…）。约 25 行，独立提交。

## 7. 测试（TDD，验管线不验 LLM 判断）

- FactLedger 单测：add / relevant(按 var) / state roundtrip / 去重。
- 两轮累积**管线**：turn1 喂 fake arbiter 出 partial_success+established_fact → 断言账本有
  该事实；再断言 `_world_vars_for_arbiter()` 对该 var 已带 established_facts。**不**断言 LLM 是否
  翻旗（非确定）。
- 不变量：partial_success **不翻**终态旗标但**写**了事实。
- 关系播种：load 后 `relationship_store.get(npc, player)` 是种入维度；arbiter context 的
  authority_relationship 反映它。

## 8. 明确不做（范围纪律）

- (b) 普通对话里的调查发现自动检测——先验 (a) 命门，再议（需模糊检测 pass，额外 LLM 成本）。
- world_tree / test_routes / npc_profiles 硬门 / 作者写 success_conditions。
- 玩家可见的账本/任务 UI；事实过期衰减。

## 9. 提交节奏

1. **A** — 关系播种 + 测试（独立真 bug）。
2. **B** — FactLedger 模块 + 持久化 + 单测。
3. **C** — arbiter `established_fact` 字段 + prompt 渲染/约束 + session 写读接线 + 累积管线测试。

整体克制：一个 ~60 行新模块、一个 schema 字段、session 三处小改、arbiter prompt ~10 行、
关系播种 ~25 行。不碰 TUI、不碰涌现哲学。

## 10. 验证（命门检查）

(a) 跑通后，用 `tidebreak` / `skyglass` 真机各跑一轮：先前 FAIL 的旗标能否经"多轮累积 →
条件满足 → success"翻转，且过程读起来像谈判、不像填表。若仲裁出现"忘了先前事实/每次重新
审判"的随意感，说明检索或 prompt 约束不到位——那是本方案真正的风险点，优先调它。
