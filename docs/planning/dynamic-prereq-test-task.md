# 测试任务：动态前置变量（P1）能否让 GM 自己完善世界、推进长链

> 给测试 Agent 的任务简报。配套设计见 [docs/design/dynamic-world-model.md](../design/dynamic-world-model.md)。
> 承接第二轮回归（`reports/skyglass_ledger_regression_test/`）：账本闭环的前半段已通过，
> 长链 A 卡在"LLM 涌现出比世界模型更细、无结构落点的条件"。

---

## ⏱ 第二跑（2026-06-03 之后，commit 86dc261 起）——重点只看 uptake

第一跑结果 `FAIL_DYNAMIC_PREREQ_NOT_USED`（`reports/skyglass_dynamic_prereq_test/`）：机制接线正确，
但真实 arbiter **从不主动**填 `new_prerequisite`（日志 0 次），新条件全留在散文/账本里 → 链卡死。

已据此把 arbiter prompt 改硬（必须声明、established_fact 软备忘 vs new_prerequisite 可追踪路径的
分工、ascii 蛇形 id、加了完整示例）、给授权回复加了"只陈述确实为真、不虚构进展"的底真约束、并加了
"提了但被丢弃"的诊断日志。**这一跑就验两件事，别的可略**：

1. **uptake 是否 > 0**：用**同一个删了手工中间变量的测试 pack**
   （`reports/skyglass_dynamic_prereq_test/skyglass_dynamic_prereq_pack.json`）原样重跑，盯日志：
   - `+dynamic prerequisite var '<id>' (set_by=...)` 还是不是 0？出现几次？造的 var 合不合理（id 是
     ascii 蛇形吗、set_by 对得上能满足它的 NPC 吗）？
   - 若出现 `new_prerequisite proposed but NOT registered (dup/cap/bad-id): ...`，把它贴出来——说明
     模型填了、但引擎丢了（去重/上限/坏 id），是另一类问题。
   - uptake >0 后，去满足那个动态 var，看能否 `⟳FLIP` 并推进终态裁定。
2. **台词不再与底真矛盾**：复跑"玩家谎称某前置已完成"的场景，确认 NPC 可见台词**不再**声称未发生
   的进展/未亲见的凭证（上轮"我看到了梅档案官的章"那种）。

报告给出：uptake 次数 + 至少一条 `+dynamic prerequisite var` 链路（或说明为何仍 0）+ 台词矛盾是否
消除。日志放 `reports/<新目录>/`。

> 若 uptake 仍为 0：不用再调，直接回报，开发侧会上"policy 重试"后手（partial_success 点名了未结构化
> 的新条件却没填 new_prerequisite 时判不合规并重试）。

---

## ⏱ 第三跑（commit 7dc5f29 起）——盯闭环

第二跑结果 `PARTIAL_PASS_UPTAKE_FAIL_CLOSURE`（`reports/skyglass_dynamic_prereq_second_run/`）：
uptake 0→1（GM 声明了 `union_witness_statement_filed`），台词矛盾已修；但**声明出来的动态前置
触发不到**——玩家和有效权威 NPC（Tamsin，`union_authority`）同地、发了 4 次实质请求，却因关键词门
（GM 现造变量关键词无从调优、很可能为空）退化成普通对话，没路由、没 `⟳FLIP`。

已修：**动态变量放宽路由**——对动态变量的权威 NPC 发实质（非提问）请求，即使关键词不命中也路由、
交 arbiter 判；日志补打 `keywords=...`；prompt 要求 set_by 只写**确实存在**的 NPC + 多给自然关键词。

**这一跑就盯闭环，复用同一个去脚手架 pack**
（`reports/skyglass_dynamic_prereq_test/skyglass_dynamic_prereq_pack.json`）：

1. **动态前置能否被满足并翻旗**：GM 声明动态前置后，去找其 `set_by` 指向的**真实存在**的权威 NPC
   （如 Tamsin），用**自然语言**发实质请求（不必猜关键词）。盯日志：
   - 是否出现 `world-change <动态var> by <NPC> → ...`（说明这次**路由成功**了）？
   - 能否 `success → ⟳FLIP`？翻了之后回到上游 NPC（如奥罗），`memory_purge_halted` 等终态能否因
     前置已为真而 `success → ⟳FLIP`，跑出**完整闭环**？
2. **顺带确认 GM 给的关键词**：看新日志 `+dynamic prerequisite var '<id>' (set_by=..., keywords=...)`，
   关键词到底空不空、合不合理；set_by 是否还在编不存在的 NPC。
3. 反作弊照旧抽查一次（伪造前置不翻旗），确认放宽路由没把反作弊放过。

报告给出：是否跑出**至少一条完整 `动态前置 ⟳FLIP → 终态 ⟳FLIP` 闭环**（贴日志链路），或卡在哪。
剔除 `⚠FALLBACK` tick。日志放 `reports/<新目录>/`。

> 闭环成立 → P1 站住，接着上 P2（现场动作 summon/witness）。仍不成立 → 贴卡点日志，开发侧继续调。

---

## ⏱ 第四跑（commit f3f4f5f 起）——盯闭环（prefix 已修）

第三跑结果 `PARTIAL_PASS_DYNAMIC_ROUTING_FAIL_CLOSURE`（`reports/skyglass_dynamic_prereq_third_run/`）：
放宽路由生效（自然语言成功进入 `world-change oro_halt_order_endorsed`），反作弊、去重、观测都正常；
但链断在一环——GM 把链上的第二个动态变量声明成 `oro_halt_request_filed (set_by=['clinician_oro'])`，
**少了 `npc.` 前缀**，导致玩家找 `npc.clinician_oro` 怎么说都路由不过去。

已修：`set_by` 匹配现在**容忍 `npc.` 前缀有无**（`clinician_oro ≡ npc.clinician_oro`），role/id 都认。

**这一跑就盯闭环，复用同一个去脚手架 pack**
（`reports/skyglass_dynamic_prereq_test/skyglass_dynamic_prereq_pack.json`）：

1. **整链能否闭环**：GM 声明动态前置链后，**逐环满足**——先找下游动态变量的权威 NPC（如 Oro 满足
   `oro_halt_request_filed`），用自然语言发实质请求，看是否 `world-change <var> by <NPC> → success → ⟳FLIP`；
   再回上游（Mae 的 `oro_halt_order_endorsed`）→ `⟳FLIP`；最后终态（`memory_purge_halted`）能否因前置
   均为真而 `success → ⟳FLIP`。**目标：至少一条完整的 `动态前置 ⟳FLIP →（可多环）→ 终态 ⟳FLIP`。**
2. **若仍卡**：贴卡在哪一环的 `world-change` 行 + `reason=`；特别注意 set_by 是否还有前缀/不存在 NPC 问题
   （现在缺前缀也该能匹配了），以及关键词/路由是否仍有漏。
3. 反作弊抽查一次（伪造前置不翻旗）。剔除 `⚠FALLBACK` tick。

报告给出：闭环是否成立（贴完整 `⟳FLIP` 链路）或卡点。日志放 `reports/<新目录>/`。

> 闭环成立 → **P1 站住**，接 P2（现场动作 summon/witness）。仍不成立 → 贴卡点日志，开发侧继续调。

---

## ⏱ 第五跑（commit eaea2b4 起）——盯闭环（已给花名册）

第四跑结果 `FAIL_CLOSURE_INVALID_SET_BY_NONEXISTENT_NPC`（`reports/skyglass_dynamic_prereq_fourth_run/`）：
放宽路由、前缀容忍都生效，但 GM 又**凭空编了个不存在的 NPC**（`set_by=['npc.union_steward']`，
真正的工会权威是 `npc.courier_tamsin`/`union_authority`），链第一环无人可找。

已修：arbiter prompt 现在**列出真实 NPC 花名册**（id/authority/location），并明说 set_by 只能从中选；
注册时**丢弃解析不到真实 NPC 的 set_by**，全是幽灵则不注册（日志会写明）。

**这一跑仍盯闭环，复用同一个去脚手架 pack**
（`reports/skyglass_dynamic_prereq_test/skyglass_dynamic_prereq_pack.json`）：

1. **GM 是否选对真实 NPC**：看 `+dynamic prerequisite var '<id>' (set_by=..., keywords=...)` —— set_by 是否
   都是花名册里真实存在的 id？还有没有 `NOT registered (...no-existing-set_by-NPC...)` 出现（说明 GM 仍在编人）？
2. **整链能否闭环**：逐环满足动态前置（找其真实权威 NPC 自然语言发实质请求）→ `world-change <var> → success
   → ⟳FLIP`，逐环上推到终态（`memory_purge_halted` 等）`⟳FLIP`。**目标：至少一条完整 `动态前置 ⟳FLIP →（可多环）
   → 终态 ⟳FLIP`。**
3. 反作弊抽查一次。剔除 `⚠FALLBACK` tick。

报告给出：闭环是否成立（贴完整 `⟳FLIP` 链路）或卡点；若卡，**重点区分**是"机制 bug"（路由/前缀/注册）还是
"GM 判断质量"（选了存在但满足不了该前置的 NPC、或条件本身需要现场动作才能满足）。日志放 `reports/<新目录>/`。

> 闭环成立 → **P1 站住**，接 P2（现场动作）。
> 若卡点已从"机制 bug"转为"GM 判断/需要现场动作"——那是 P2 该上的信号，不必再无限调 prompt，回报即可。

---

## ⏱ 第六跑（commit 0b760eb 起）——验证 P2b（流程成熟）能否闭环

第五跑结论 `P1_MECHANISM_OK_CLOSURE_BLOCKED_BY_P2_SITE_ACTION`
（`reports/skyglass_dynamic_prereq_fifth_run/`）：P1 机制站住（真实 NPC、路由、反作弊全过），但链卡在
"工会理事会授权"——一个**未建模机构的线下流程**，无法靠对话满足。

已上 **P2b 流程成熟**：当事人启动一个需时间/线下完成的流程（提交理事会、递申请）时，arbiter 可判
partial_success 并用 `process_started{var_id, matures_in_ticks}` 把对应动态变量标记为"已启动、N tick 后
自动成熟"；引擎每 tick 检查、到点把变量翻 True（发事件）。有界、持久化、反作弊不破（只有真实裁定能
启动流程，不是玩家声称）。

**这一跑验两件事，复用同一个去脚手架 pack**
（`reports/skyglass_dynamic_prereq_test/skyglass_dynamic_prereq_pack.json`）：

1. **`process_started` 是否被采用（uptake）**：当链卡在"需理事会审议/等回执/走流程"时，玩家请相关权威
   NPC（如 Tamsin）**启动这个流程**（自然语言，如"请你把这事提交理事会走审议"）。盯日志：
   - 是否出现 `process started → '<var>' matures at tick N`？（说明 arbiter 用了 process_started）
   - 继续往后玩几个 tick，是否出现 `pending process matured → '<var>' ⟳FLIP True`？（流程到点成熟）
   - 若**完全没出现** `process started`：和当初 new_prerequisite 一样，是 uptake 问题，回报即可，开发侧
     再硬化 prompt。
2. **整链能否闭环**：流程成熟把中间变量翻 True 后，逐环上推——`union_pause_order_received` →
   `memory_purge_halted` 能否 `success → ⟳FLIP`，跑出**完整闭环**？贴 `⟳FLIP` 链路。
3. 反作弊抽查一次（伪造"流程已完成/理事会已授权"，对应变量仍 False 时不应翻终态）。剔除 `⚠FALLBACK` tick。

报告给出：process_started 的 uptake 次数 + 是否有 `pending process matured ⟳FLIP` + 整链是否闭环（或卡点）。
日志放 `reports/<新目录>/`。

> 闭环成立 → P1+P2b 站住，按需再决定 P2c（现场/召集）。
> 若卡在"需要把某个**现有 NPC** 带到场/亲眼见证"（而非线下流程）——那是 P2c 信号，回报即可。

---

## （以下为第一跑原始简报，背景参考）

## 这轮验证什么

P1 给了 arbiter（GM）一个能力：当它要求一个世界模型里**没有的前置**时，可以在裁定输出的
`new_prerequisite` 里**当场把它声明成一个动态 world var**，引擎注册成一等世界状态（初始
`False`、可满足、随存档持久化）。本轮的核心问题：

> **不靠你手动预声明中间变量，GM 自己会不会用 `new_prerequisite` 把"白舱清单/亲眼查看/联签"
> 这类涌现条件转成可满足的动态前置，从而把奥罗那条卡死的链推得更远、甚至闭环？**

## 怎么跑

- **用原始 skyglass pack**（或只保留终态旗标），**不要**像上轮那样手动补中间前置变量——
  这轮就是要看 GM 自己造。
- 真机 + `--log`：
  ```bash
  PYTHONPATH=src python -m verisaria run fixtures/content_packs/skyglass_memory_inquest.json \
    --llm minimax --log reports/<新目录>/run.log
  ```
- 走奥罗那条之前卡死的链（联签 / 暂停清洗 / 提交禁令）。当奥罗提出实证条件时，观察 arbiter
  是否声明了动态前置；若是，再去满足那个动态前置，看链能否继续。

## 关注点（请在报告里逐条回答）

1. **GM 是否真的用了 `new_prerequisite`**：日志里找 `+dynamic prerequisite var '<id>' (set_by=...)`。
   贴出来。它造的变量合不合理（id/label/set_by 是否对得上能满足它的 NPC）？
2. **动态前置能否被满足并推进链**：去找 `set_by` 指向的 NPC 满足那个动态 var → 它是否 `⟳FLIP`
   → 终态裁定是否因此更接近 success？贴链路。
3. **反作弊仍成立**：GM 有没有**刷出一堆垃圾/重复变量**？动态变量有没有**未经 success 就自己变
   True**？（都不应发生——动态变量起始 False、只 success 翻、去重、每局上限 16。）
4. **是否仍有"够不着的死要求"**：还有没有 arbiter 提了条件、却既不翻旗也不声明动态前置、让玩家
   无路可走的情况？这类要重点贴出（说明 prompt 约束还不够）。
5. **Parser**：这轮 PARSE 失败会自动重试。长句自然语言的"我没理解"是否明显减少？仍失败的，把
   `verisaria.intent` 诊断行贴出来。
6. **关系日志**：`verisaria.relationship` 现在会记每次 appraisal 的 stance Δ + belief——长链里奥罗
   怀疑上升的原因现在可见，若它导致越来越难推进，请引用。
7. 剔除 `⚠FALLBACK` tick，不计入一致性判断。

## 产物

新 `run.log` + transcript + （若你改了 pack）改动说明，放进 `reports/<新目录>/`。

## 一句话目标

验证"**让 GM 当场声明动态前置**"能否把卡死的涌现条件转成可满足的结构、推进甚至闭环长链，
且不破坏反作弊、不刷爆世界状态。结果决定 P2（现场动作 summon/witness）要不要立刻上、以及上多少。
