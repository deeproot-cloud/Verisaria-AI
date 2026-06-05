# 动态世界模型 + 现场动作（GM 主动完善世界）

> 让 LLM（作为 GM）在剧情需要时**当场把涌现的条件提升为有底真的世界变量**，并随存档持久化；
> 同时给玩家"请 NPC 到场/随行/见证"的可执行动作。配套：
> [emergent-fact-ledger.md](emergent-fact-ledger.md)。

## 0. 一句话

账本记住 LLM 的**软让步**；本设计让 GM 把**软条件**升级成**硬变量**（有底真、可满足、可持久化），
从而让"调查链涌现出新条件 → 玩家有结构化路径去满足 → 闭环"成立——终态旗标仍只在 `success` 翻。

## 1. 背景

第二轮回归（`reports/skyglass_ledger_regression_test/`）：反作弊 B 通过，多 NPC 长链 A 卡死。
根因不是账本遗忘，而是 **LLM 涌现出比世界模型更细、没有结构化落点的条件**（奥罗要白舱清单、
药剂批号、亲眼查看……）。当前没有对应 world var、也没有可执行动作能满足，条件互相咬死。

## 2. 决策（2026-06 用户拍板）

- **(a) 约束 arbiter 到"够得着"的条件 +（b）内容补关键节点**——认可。
- **更进一步：GM 应能主动完善世界**——动态创建/更新已加载的世界模型（先聚焦 world vars），
  并随存档一起保存。
- **现场动作（summon/follow/visit/witness）排进来**。

## 3. 机制一：动态前置变量（GM 缺什么就声明什么）

**缝 = arbiter。** 当 arbiter（GM）要求一个当前世界模型里不存在的前置条件时，它在输出里**同时
声明这个前置变量**，引擎把它注册成一等世界状态：

- `ArbiterOutput` 增加可选 `new_prerequisite`：`{ var_id, label, set_by, request_keywords }`
  （var_id 用稳定 snake_case；set_by 指向能满足它的 NPC——通常就是提条件的那位）。
- 引擎把它注册进**现在可变的** `_world_var_specs` + `world_vars`（初始 `False`），并写进存档。
- 之后玩家有了结构化路径：去找那个 NPC 满足它 → 又一次 Channel-C 裁定 → `success` → 翻 `True`；
  终态变量的裁定本就看得见所有变量当前值，于是能据此推进。

**路由**：动态变量带 `request_keywords`（arbiter 给）；缺失时回退到"目标 NPC 上下文识别"。

**约束 arbiter（决策 a）**：prompt 引导它——"只提玩家在当前世界（已有变量/动作/在场 NPC）里
**够得着**的条件；若必须引入新前置，就用 `new_prerequisite` 把它**声明成可满足的变量**，不要留下
无路可走的空要求。"

## 4. 机制二：现场动作（满足"到场/亲眼"类前置）

LLM 很自然会要"带我去塔基""我得亲眼看白舱"。给玩家可执行动作：

- **summon / request-accompany**：请在场 NPC 随行，或请 NPC 到某地。
- **witness / show-on-site**：在某地点向 NPC 展示/共同查看，满足"亲眼"前置。

实现走现有意图/行动管线（新动作类型或 verb），成功后可翻一个"到场/已见证"类变量（可由机制一
动态创建，也可内容预声明）。

## 5. 不变量 / 红线（沿用账本，新增动态相关）

1. **终态旗标只在 `success` 翻**；GM 新建的变量起始 `False`，**创建≠满足**——反作弊不受损。
2. **有界**：动态变量去重（稳定 var_id；**同义则复用**）+ 每局上限，防止 GM 刷爆世界状态。
   - **F1 收敛护栏（`v0.5.x`，真包审计后落地）**：注册新前置时若与已有变量（作者/动态）**语义近重复**
     就**复用已有变量**（id-stem 包含，或新关键词已命中已有 label；并把新措辞折进已有 keywords），杜绝
     `pump_failure_disclosed_publicly` 与作者 `pump_failure_disclosed` 互不相认。
   - **充分性闭环（F1 三跑后）**：真机暴露的真病根是 arbiter 在 reason 里**软性**不断发明新前置（id 不相关、
     躲过去重；硬数量 cap 试过、反噬成「无路可走」死结 + 误伤独立前置，已**回退**）。改用「**prompt 为主 +
     引擎兜底**」：① 每个终态把**已满足的前置**喂进裁定 prompt，硬规则 (d)「已满足的前置之上不得再加码、必须
     success」；② 引擎兜底——`serves` 记录每个前置所属终态，当某终态**所有已声明前置全为真**时，
     `_terminal_requirements_met` 触发，即便 arbiter 仍 partial 也**强制 success**（反作弊不破：前置是真满足、
     非 bluff；红线由「只在 arbiter success 翻」放宽为「success 或 声明前置被真满足」，用户拍板）。
3. **Coherence**：新变量不得与现有世界状态矛盾；id/label 合法。
4. **持久化**：动态变量（及其 specs）随存档保存/载入；账本已持久化。
5. **A5**：动态变量是世界事实，按既有可见性规则；不泄露 NPC 私有。

## 6. 范围纪律（v1 不做）

- v1 的"动态世界模型"**只做 world vars 维度**（前置变量）。**不做**动态创建实体/地点/lore/世界书
  ——那是更大的面，远期再议。
- 不做作者 `success_conditions` / 分支图（一以贯之）。

## 7. 分阶段（每阶段可跑可测）

1. ✅ **P1 — 动态前置变量**：arbiter `new_prerequisite` 字段 + prompt 约束；引擎动态注册 + 持久化
   + 路由；不变量测试。**五轮真机验证：机制站住**（uptake/路由/前缀/花名册/反作弊全过），但长链
   会卡在"前置需要线下流程/到场/见证"——P2 信号。
2. **P2 — 让事实穿过世界（不止穿过对话）**，按真机暴露的形态分两块：
   - ✅ **P2b 流程成熟**：当事人启动一个需时间/线下完成的流程（提交理事会、递申请）时，arbiter 判
     partial_success 并在 `process_started{var_id, matures_in_ticks}` 里把对应动态变量标记为"已启动、
     N tick 后自动成熟"；引擎每 tick 检查、到点把变量翻 True（发 WorldVarChanged）。
     有界（≤`_MAX_PROCESS_TICKS`）、持久化（pending_until 随动态 spec 存档）、反作弊不破（只有真实
     裁定能启动流程，不是玩家声称）。
   - **P2c 现场/召集**（自由试玩确认信号，`reports/freeplay_validation_first_run/`）：把现有 NPC 带到场，
     满足"亲眼/到场/见证"前置。最干净落点 = 护送证人 worker_lira → archive_stack，当着 archivist_mae
     面前口述证词（满足 `lira_witness_statement_recorded`）。
     - **机制 = 护送请求，与世界变更同构**：玩家"对 X 说：跟我去 Y / 一起去 Y / 跟我来"——引擎检测
       到护送请求（对在场 NPC 说 + 护送关键词 + 可解析目的地 Y，或"来/过来"=召到玩家当前位置），交
       arbiter 裁定 X 是否愿意随行（同世界变更，按关系/人设/说服力）；success → X（与玩家）移动到 Y，
       发 NpcMoved/PlayerMoved + 角色化台词；非 success → 角色化拒绝。到场后"当面见证"仍走既有
       Channel-C（可由 P1 动态创建"已见证"变量）。
     - **避开第三方歧义**：让玩家**直接对被护送的 NPC 说话**（"对莉拉说：跟我去档案署作证"），
       addressee=被护送者、目的地=地点，无第三方人名，绕开下面友 friction 2。
   - **两处解析摩擦（P2c 前置，自由试玩暴露）**，escort 话术天然会踩：
     1. "去找<NPC>" / "去<地点>找<NPC>" 移动解析时灵时不灵（NPC id 泄成 location → unknown location，
        或菜单回退）；纯"去<地点名>"始终可靠。
     2. 对白里第三方人名/地名被误当动作对象（"对梅说我可以把**莉拉**带过来"→"'莉拉'指代不明"）——
        已命名的真实 NPC 不该判为 ambiguity。

> 注：第五跑的"理事会"是个**未建模机构**。本设计用 P2b 把它抽象成"启动→成熟"的流程，避免为每个
> 机构造 NPC；真正需要"把某个现有 NPC 带到场"才用 P2c。动态创建实体仍不在范围内。

## 8. 验证（命门）

重跑 skyglass A 长链：奥罗涌现的实证条件能否被 GM 转成可满足的动态前置（或经现场动作满足），
最终终态 `⟳FLIP`；且过程仍读起来像谈判、反作弊仍成立（伪造不翻旗、GM 不刷爆变量）。

**结果（六轮真机迭代，2026-06-04）：`PASS_P1_P2B_PROCESS_CLOSURE`（`v0.3.0`）。**
完整闭环跑通：GM 声明动态前置（真实 setter）→ 启动线下流程 → pending 成熟 `⟳FLIP` → 中间变量上推到
终态 `memory_purge_halted ⟳FLIP`；反作弊单独 run 通过（伪造停洗令不翻终态）。六轮各填一个真机潜坑：
prompt uptake → 路由 → `npc.` 前缀 → 花名册防编人 → P2b 流程成熟 → 流程启动可见对白。
报告：`reports/skyglass_dynamic_prereq_*_run/`。

**已知后续**：自然移动/多跳寻路（试玩仍靠 `/inject`）；P2c 现场/召集（"亲眼/到场"前置，本轮闭环未触达）。

**循环前置 = 接受为涌现难度（决策 A，2026-06 用户拍板）**：护送验证里证人莉拉"清洗暂停前不肯作证、
而暂停又需她的证词"绕成死结——这是 GM 涌现出的难度，**引擎不强行破环**。理由：护送 + P1/P2b 已能让
**非死锁**路线闭环（run 6 工会路已证），死锁路线就是走不通，玩家改走能通的路。不引入作者 quest 图、
不教 arbiter 自动破环。

**P2c 护送（`v0.3.1`）**：escort 改用**专用意愿裁定**（轻量 prompt，不走世界变量/前置那套——之前复用
world-change prompt 会把"陪我走一趟"裁成"先满足某前置"，结构性压低命中）。三轮真机：检测/目的地解析/
反作弊全健康，loader 修复确认（声明但没人站的空房间不再被丢、10 地点全可达）；`⟳MOVED` 未出仅因
skyglass 全员"离岗即受损"（内容层），缺一个低阻力可护送对象——机制本身已验证。

**v0.3.1 加固**：里程碑前做了一轮 5 维度对抗式代码评审（反作弊/逻辑/持久化/解析/arbiter 契约），确认并
修复 7 处——最关键是**通用 arbiter 路径缺世界旗标 success 门**（反作弊漏洞：失败/部分裁定也能翻旗）。
另：按显示名解析对话目标、动态 var 的 pending 进度在"被固化进 pack"后存档丢失、地点加载非确定性顺序、
两条 arbiter 路径异常安全、文档。报告见对话记录。

**前置递归收敛（决策 (a)+(b)，2026-06 用户拍板，commit db1fb4f）**：长链卡死的最后一个瓶颈 = 涌现前置
**不见底**（权威一层层加更深前置：带到场→核实→记录→签认…，单会话到不了底、run 间形态漂移——既不可玩
又不真实，等于 GM 移动球门）。评估三条路（见对话）后选 **(a)+(b) 纯 prompt**（真实感+可玩性最佳组合，
(c) 引擎硬上限因"强制放行最出戏"只留作可选兜底）：
- **(a) 见底**：每个 new_prerequisite 必须本身一两步内可满足，不许"前置套前置"；天然多步的事整体当作
  **一个短流程**（process_started）。
- **(b) 充分性**：已确立事实 + 已为真前置覆盖了请求**实质**、只剩程序手续时 → 判 success，不再派生新前置。
反作弊不变（success 仍需真实裁定，吹牛照样失败）。命门=真机重跑看长链能否收敛闭环。

- **(c) 不出尔反尔（v0.4.0）**：把 pack 的 world_book 注入仲裁、按目标过滤，world-change prompt 渲染
  **「目标的人设与立场」**（权威 traits + 他自己 world_book 里的放行条件）；规则 (c)=权威自己立场已明示
  放行条件、且条件已满足（前置 var 已 True）时**必须 success**，不许再加他立场里没有的新前置。这修掉了
  终态被"派生第三方背书/上级备案"无限拖住的最后一公里。
反作弊不变（success 仍需真实裁定，吹牛照样失败）。

**②证人路由**：已修——前置 `set_by` 写成证人本人（prompt nudge）+ 模糊关键词路由 + pack 预声明证人 var
（如 `anya_testimony_given`），证人当面作证现在真翻其 var、进 ledger。
**③**（schema 降级 + 误导日志）已修（v0.3.2）。

## 9. 盖章：长链端到端闭环（`v0.4.0`，2026-06-04 真机实锤）

`escort_proving_ground` 干净夹具真机跑通完整链路（`reports/prereq_convergence_test_fourth_run/`）：
```
escort npc.miller_anya → gatehouse : success ⟳MOVED
→ anya_testimony_given by npc.miller_anya → success ⟳FLIP   （②证人作证翻 var）
→ sluice_opened by npc.warden_kang → success ⟳FLIP          （终态：闸官 honor 自己"亲历者作证就开闸"的立场，(c) 生效）
```
终态链**派生 0 个动态前置**（不再套娃/漂移）；voiced 台词与 verdict 一致；反作弊 PASS（伪造作证不翻，
(c) 只在 var 真为 True 时放行）；0 FALLBACK、0 tick 超时。**动态世界模型 + 路由 + (a)(b)(c) 这条线彻底
盖章。** skyglass 的 mae↔oro 循环死锁是另一类（决策 A，接受为涌现难度，不在此线）。
