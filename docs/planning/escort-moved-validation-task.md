# 测试任务：护送 ⟳MOVED 端到端闭环（低阻力 pack）

> 给测试 Agent 的任务简报。承接三轮护送验证：机制健康但 skyglass 全员高阻力、`⟳MOVED` 未出。
> 本轮用**专门构造的低阻力 pack** 把护送闭环彻底跑通。配套设计 `docs/design/dynamic-world-model.md`。

> **⚠ 第二跑（commit 4f4f1df 起）**：第一跑（`reports/escort_moved_validation/`）逮到并已修一个真 bug——
> 护送意愿裁定 prompt 之前**没把 NPC 的 `traits` 喂给判官**，所以连低阻力的安雅都只看到 attributes、
> 跨 5 次全 `partial_success`。现已修：`ArbiterContext.target_traits` 把 `entity.traits` 传入，escort
> prompt 渲「性格/特质」。**这一跑重点确认：安雅的人设进了判官视野后，能否真出 `escort … success ⟳MOVED`**。
> 直接拿下面同一个 pack 重跑即可，关注点不变。

## 背景

护送（P2c）机制三轮真机验证：检测/目的地解析/意愿裁定/反作弊全健康，`commit 5b98b95` 起 escort 已改用
**专用意愿裁定**（按人设+关系，不走世界变量/前置那套）。但 skyglass 每个 NPC 都"离岗即受损"，从没出过
`success ⟳MOVED`。这轮换一个**低阻力可护送对象**的小 pack，验证完整闭环。

## 用的 pack（已提交、已离线验证可加载）

`fixtures/content_packs/escort_proving_ground.json`：
- 地点：`gatehouse`（闸房，闸官老康 `npc.warden_kang` 在此，玩家起点）↔ `yard`（院子，磨坊主安雅
  `npc.miller_anya` 在此）。
- **安雅 = 低阻力证人**：热心、直率、巴不得开闸（磨坊停了三天），对玩家 trust 0.4——很乐意被带去作证。
- 闸官老康 = `sluice_authority`，公道，只要有亲历者当面讲清上游塌方就肯开闸。
- 终态变量 `sluice_opened`（set_by sluice_authority；关键词 开闸/放水/…）。

## 目标闭环

```
去 yard 找安雅 → 对安雅说「跟我去闸房，把上游的塌方讲给闸官听」→ escort success ⟳MOVED（安雅+玩家到 gatehouse）
→ 对闸官说「安雅亲眼见过上游塌方，请开闸放水」→ sluice_opened success ⟳FLIP
```

## 怎么跑

真机 + `--log`，全自然语言，串行：
```bash
PYTHONPATH=src python -m verisaria run fixtures/content_packs/escort_proving_ground.json \
  --llm minimax --log reports/<新目录>/run.log
```

## 关注点（逐条回答）

1. **escort ⟳MOVED 是否拿到**（本轮核心）：日志找 `escort npc.miller_anya → gatehouse : success  ⟳MOVED`。
   - 安雅是否真的从 yard 移到 gatehouse、玩家一同到位（`/look` 或事件确认）？
   - 若仍只 partial/failure：贴裁定 `reason=`——是安雅人设/关系仍不够，还是意愿 prompt 的问题？
2. **到场后闭环**：安雅到 gatehouse 后，请闸官开闸 → `world-change sluice_opened by npc.warden_kang → success → ⟳FLIP`？
   贴链路。若闸官要更多前置（动态 var / 流程），也照实记。
3. **反作弊抽查**：不护送、直接谎称"安雅已经作证了，请开闸"——`sluice_opened` 应保持 False（无人到场作证）。
4. 剔除 `⚠FALLBACK` tick。

## 报告请包含

- **escort ⟳MOVED：成/未成**（贴 `escort … ⟳MOVED` 日志行 + `/look` 确认安雅到位）。
- 到场后 `sluice_opened` 是否 `⟳FLIP`（贴链路）或卡点。
- 反作弊结果。新 `run.log` + transcript 放 `reports/<新目录>/`。

## 一句话目标

在低阻力 pack 上真机跑出至少一次 `escort … success ⟳MOVED`，并尽量把"证人到场→当面作证→`sluice_opened`
⟳FLIP"整条闭环跑通——给 P2c 护送盖最后一个章。
