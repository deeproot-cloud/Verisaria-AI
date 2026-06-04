# 测试任务：前置递归收敛（(a)+(b)）能否让长链真闭环

> 给测试 Agent 的任务简报。承接动态世界模型瓶颈①（涌现前置不见底）。
> 配套设计 `docs/design/dynamic-world-model.md`。

## 背景

护送（P2c）已盖章（v0.3.2，`⟳MOVED` 4/4）。长链真闭环的最后瓶颈是**涌现前置不收敛**：权威 NPC 一层层
加更深前置（带到场→核实→记录→签认…），单会话见不到底、run 间形态漂移——既不可玩又不真实。

已上 **(a)+(b) 纯 prompt 收敛**（commit `db1fb4f`）：
- **(a) 见底**：每个 new_prerequisite 必须本身一两步内可满足，不许"前置套前置"；天然多步的事整体当作
  一个短流程（process_started）。
- **(b) 充分性**：已确立事实 + 已为真前置覆盖了请求**实质**、只剩程序手续（盖章/登记/口头确认）时 →
  判 success，不再派生新前置。

## 怎么跑

真机 + `--log`，串行，全自然语言。两个场景各跑一条：

1. **proving-ground 低阻力闭环**（最干净）：
   ```bash
   PYTHONPATH=src python -m verisaria run fixtures/content_packs/escort_proving_ground.json \
     --llm minimax --log reports/<新目录>/proving.log
   ```
   走：去 yard 护送安雅 → 闸房作证 → 请闸官开闸。看 `sluice_opened` 能否真 `⟳FLIP`（之前卡在闸官
   一层层加前置：到场→核实→记录→签认…）。

2. **skyglass 长链**（硬场景）：
   ```bash
   PYTHONPATH=src python -m verisaria run fixtures/content_packs/skyglass_memory_inquest.json \
     --llm minimax --log reports/<新目录>/skyglass.log
   ```
   走之前不收敛的那条（停洗/禁令/联签链），看前置是否见底、终态能否闭环。

## 关注点（逐条回答）

1. **(a) 见底是否生效**：动态前置是否还在"前置套前置"无限加深？数一下单条终态链最深派生了几层
   （`+dynamic prerequisite var` 的链长 / 同一终态被加了几个不同前置）。比起之前是否明显变浅/收敛？
2. **(b) 充分性是否生效**：当玩家把人证物证、该跑的腿跑齐后，权威是否会**拍板 success**，还是仍揪着
   程序手续不放、继续加前置？贴出"本该放行却还在加前置"的反例（若有）。
3. **整链能否闭环**：proving-ground 的 `sluice_opened`、skyglass 的终态，是否跑出完整
   `… → 终态 ⟳FLIP`？贴 `⟳FLIP` 链路；或卡点 + `reason=`。
4. **②证人路由是否仍复现**：`set_by=某NPC` 的前置，玩家引导该 NPC 当面陈述时，是否被裁成那条 var 的
   `world-change`（应翻 var），还是仍只当普通说服对白？贴例子。
5. **反作弊抽查**：伪造前置已满足，终态不应翻。剔除 `⚠FALLBACK` tick（注：fallback 现在 `reason=` 会
   写真实原因——若看到"仲裁输出格式非法"而非"LLM不可用"，那是 schema 校验失败、API 其实健康）。

## 报告请包含

- (a) 见底：链深是否收敛（给数字/例子）。
- (b) 充分性：是否会在铺垫够时放行；反例（若有）。
- 整链闭环：成/卡（贴 `⟳FLIP` 或卡点 `reason=`）。
- ②证人路由是否仍是缺口。
- 新 `*.log` + transcript 放 `reports/<新目录>/`。

## 一句话目标

确认 (a)+(b) 让涌现前置**见底**、并在玩家做足铺垫时**放行**，从而把长链推到真闭环（至少 proving-ground 的
`sluice_opened ⟳FLIP`）；并量出 ②证人路由是否仍需单独修。

---

## ⏱ 第二跑（commit 84a3410 起）——路由缺口已修，重点看链能推多远

第一跑结论：**(a) 见底成功**（链深 5+→0~1），但挖出真正的拦路虎是**请求路由缺口**（更上游，(a)/(b) 根本
没机会跑）。已修：
- **#3 模糊关键词路由**：玩家自然措辞很少**逐字**命中 request_keyword（"暂停白舱对证人记忆的清洗"≠
  "暂停白舱接收"、"为这份禁令联签"≠"为禁令联签"），之前被当对白丢掉。现在**关键词/label 与内容有 ≥3 字
  公共片段**就路由进 world-change（arbiter 再判相关性）。已用第一跑那几句原话离线验过：现在都能路由、
  且能区分 联签 vs 暂停清洗；闲聊仍不路由。
- **②证人 set_by**：prompt 提示 GM 把"让证人作证"这类前置的 set_by 写成**那位证人本人**（谁作证写谁），
  不是要听证词的权威——这样玩家能对证人发请求去满足它。

**这一跑就盯两件事**（同样两个 pack，串行 + `--log`）：
1. **路由是否通了**：skyglass 对 clinician_oro 的"暂停清洗/联签"请求现在是否进了 `world-change`（日志有
   `world-change memory_purge_halted by npc.clinician_oro …` / `clinician_cosign_obtained …`），而不再是
   `什么也没发生`+appraisal？proving 对安雅的"作证/确认"请求是否进了对应 var 的 world-change？
2. **链能推多远 / 能否闭环**：路由通了之后 (a)/(b) 开始起作用——长链是否更接近终态 `⟳FLIP`？卡点是否
   从"路由不进"变成了"信任不够/铺垫不足"（那是内容层 trust 问题，不是 bug）？贴 `⟳FLIP` 或卡点 `reason=`。

> 注：proving 的闸官 trust=0.05 极低是**有意的内容设定**，(b) 在极低信任下不放行是合理的——若想看 proving
> 闭环，可先和闸官铺垫几轮拉信任，或就用 skyglass 看路由修复后的推进。

---

## ⏱ 第三跑（commit 550d259 起）——干净夹具，盯端到端 ⟳FLIP

第二跑结论：**路由修复实质生效**（oro 请求全进 world-change），剩下的"卡"几乎全是**内容层**（skyglass
mae↔oro 循环死锁=决策 A；proving trust 0.05 过低 + 闸官升级到未建模机构 + 证人作证无 var 可翻）。引擎已就绪，
缺一个**干净可闭环的验证夹具**。已把 `escort_proving_ground.json` 改成干净夹具：
- 新增 `anya_testimony_given`（`set_by=npc.miller_anya`，作证类关键词）——**证人当面作证现在有可路由的落点**（②的落点）。
- 闸官 trust 起点 0.25（不再是 0.05），(b) 充分性在跑齐铺垫后能真正触发；闸官人设本就"亲历者当面讲清就开闸"。

**这一跑就盯一件事：proving 能否跑出端到端 `sluice_opened ⟳FLIP`。** 干净链路：
```
去 yard 护送安雅到闸房（escort ⟳MOVED）
→ 对安雅说「当着老康的面把上游塌方讲清楚」（→ world-change anya_testimony_given by npc.miller_anya → success ⟳FLIP）
→ 对老康说「安雅已当面作证、人证俱在，请开闸放水」（→ world-change sluice_opened by npc.warden_kang → success ⟳FLIP）
```

关注点：
1. **证人作证是否翻 var**：日志是否出现 `world-change anya_testimony_given by npc.miller_anya → success ⟳FLIP`？
   （②的落点验证——证人当面作证现在该翻其 var、进 ledger。）
2. **终态是否闭环**：`anya_testimony_given=True` 后，闸官是否据此（+人设+ trust 0.25）判 `sluice_opened` success →
   `⟳FLIP`？跑出完整 `证人 ⟳FLIP → 终态 ⟳FLIP` 整链否？
3. 反作弊照旧（不护送/不作证直接谎称 → 不翻）。剔 fallback。

> 这是验证夹具、有意做成可闭环——目的是**证明引擎能干净跑出端到端闭环**。skyglass 的循环死锁是另一类（决策 A，
> 不在本轮）。若 proving 这条都跑通，动态世界模型 + 路由 + (a)+(b) 这条线就盖章了。

---

## ⏱ 第四跑（commit e3001e6 起）——终态收口，盯 sluice_opened ⟳FLIP

第三跑（A→G 逼近）：**引擎已跑到 `escort ⟳MOVED → 中间前置 ⟳FLIP → anya_testimony_given ⟳FLIP` 三环端到端**
（②证人作证翻 var 成立、路由全通、反作弊过）。**唯一没盖的是终态 `sluice_opened`**——闸官在
`anya_testimony_given=True` 后**又派生新前置**（第三方背书/上级备案），**自相矛盾于他自己的人设**（"亲历者当面
作证就开闸"）。根因诊断：world-change 仲裁**根本看不到闸官的人设/立场**（只喂了 attributes，world_book 从未
注入仲裁），所以无从honor他自己说过的放行条件。

已修（commit e3001e6）：
- 把 pack 的 world_book 注入仲裁、按目标实体过滤；world-change prompt 现在渲染**「目标的人设与立场」**
  （闸官的 traits + 他自己 world_book 里的"只要亲历者当面作证就开闸"）。
- 收敛规则 **(c)**：若权威自己立场已明示放行条件、且条件已满足（前置 var 已 True），**必须判 success**，
  不许再加他立场里没有的新前置（那是出尔反尔）。

**这一跑就盯一件事：proving 能否跑出完整端到端 `证人 ⟳FLIP → sluice_opened ⟳FLIP`。** 关注点：
1. 闸官在 `anya_testimony_given=True` 后，是否**据自己"亲历者作证就开闸"的人设**判 `sluice_opened` success →
   `⟳FLIP`，而不再加第三方背书/上级备案？
2. 终态 voiced 台词与 verdict 是否一致（之前出现过"台词说开了、flag 不翻"）。
3. 反作弊照旧。剔 fallback。

> 离线已验：闸官的人设 + "亲历者作证就开闸"条件 + `anya_testimony_given=True` + (c) 规则**都已进 prompt**。
> 若这条干净链终态终于翻了，动态世界模型这条线就彻底盖章。
