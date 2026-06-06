# 内容包写作规范 —— 让一条链真能闭合

> 这份规范从可玩性审计（`docs/planning/playability-audit-task.md`、记忆 `playability-audit-findings`）的血泪里来：
> 机制在干净夹具上全对，但有血肉的真包反复**卡在内容层**——链推不到结局。下面每条都对应一个
> `pack_editor` lint 规则（`CampaignLoader.validate` → `world_var_*` 警告），写包时跑一遍就能提前避坑。

## 跑 lint

```python
from verisaria.engine.pack_editor import PackEditor
print(PackEditor.format_validation(PackEditor.validate_pack("你的包.json")))
```

`world_var_*` 是**警告**不是错误——包照样能加载，但每条都是一个「这条链可能闭不了」的提示。

## 五条规则

1. **每个可变变量都要有真实可满足者** —— `set_by` 必须是**真实 NPC 的 id**（`npc.warden_kang`）
   或**某个 NPC 真有的 authority 角色**（`attributes.authority`）。set_by 指向不存在的人 = 无人能让它变 true =
   死链。 〔lint: `world_var_unsatisfiable`〕

2. **每个可变变量都要有 `request_keywords`** —— 玩家用自然语言提的请求，是靠关键词路由到变量的。没有关键词，
   请求就只能靠模糊兜底、很难命中。多写几种说法（「开闸/放水/把闸开了」）。 〔lint: `world_var_no_keywords`〕

3. **一件事只用一个变量，别造近重复** —— 审计 #1 的死结就是把「公开」拆成 `pump_failure_disclosed` 和
   arbiter 涌现的 `pump_failure_disclosed_publicly`：玩家满足了一个，权威却引用另一个。一个语义 = 一个变量。
   〔lint: `world_var_near_duplicate`；引擎侧也有 id-stem 去重兜底，但作者别主动埋雷〕

4. **终态的放行条件写进 label，清晰、可满足、不歧义** —— 例：`tow_order_halted` 的 label 写明
   「唯一前置：world.pump_failure_disclosed==true；一旦满足即应叫停」。引擎已有**充分性闭环**
   （`docs/design/dynamic-world-model.md` §5.2：prompt 规则 (d) + 引擎兜底），但 label 写清楚仍让 arbiter
   更稳地守住「已满足前置不得再加码」。

5. **前置要「够得着」** —— 若 arbiter 要引入新前置，它必须是**一两步内能满足**的真实动作 / 到场 / 短流程，
   不要「前置套前置」。天然要走很多手续的事，整体当**一个短流程**（随时间成熟），别拆成永远到不了底的链。

6. **出口要在玩家走得到的地方** —— 某可变变量的满足者 NPC，必须站在**从起点经 connections 到得了**的地点上。
   把出口 NPC 关在一个没连接的房间 = 玩家永远够不着这条线的出口。 〔lint: `world_var_unreachable`〕

## 引擎现在替你兜了什么（写包时可依赖）

- **去重复用**：arbiter 若涌现一个与已有变量 id-stem 重合的新前置，引擎复用已有变量，不新建近重复。
- **充分性闭环**：某终态的**所有已声明前置全为真**时，即便 arbiter 仍想加码，prompt 规则 (d) 会令其判 success，
  引擎兜底也会强制 success（反作弊不破——前置是真满足、非 bluff）。
- **护送意愿裁定**：「跟我去 X」走专用意愿裁定，不会被误判成「先满足某世界前置」。
- **关系双向**：真诚/讲理能**降**怀疑、**升**信任；旁观者权重远低于当事人。
- **措辞容错**：贴关键词、对口权威的请求，即便用**问句**措辞（「你敢不敢把 X 撤了？」）也照样路由进裁定；
  玩家**对口但太口语**、没形成正式请求时，会回一句**可读提示**（「TA 听着，却没把这当成明确请求…」）而非
  「什么也没发生」——但这只是兜底，关键词写全仍是作者的责任。

## 一句话

写包时把每个可变变量问三句：**谁能满足它？玩家怎么说才能触发它？它和别的变量是不是同一件事？**
跑一遍 lint，`world_var_*` 清零，链就大概率能自己闭。
