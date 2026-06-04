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
