# 测试任务：验证「涌现事实账本」能否闭环

> 给测试 Agent 的任务简报。配套设计见 [docs/design/emergent-fact-ledger.md](../design/emergent-fact-ledger.md)。

## 背景

上一轮 skyglass 手动测试（`reports/skyglass_ledger_manual_playtest/`）证明了账本机制的**前半段**：
`partial_success` 会写入 `established_fact`，后续同一 world var 的裁定也能看到并引用先前事实，
**一致性良好（不是随机重判）**。但没跑出完整 `partial_success → 满足条件 → success → 翻旗`
闭环，卡点是：**"条件已被满足"缺少底真** —— 玩家说"我已拿到联签"，arbiter 只当口头声称，
不肯翻终态旗标。

开发侧重新定位：**arbiter 本来就看得见每个 world var 的当前值**，所以"有底真的 fulfillment
= 那个中间步骤本身是一个真翻了的 world var"。本轮验证的假设：

> **把调查链的中间前置步骤声明成 world var（而不是只存在于对话里），fulfillment 就有旗标
> 背书；账本继续管软让步；依赖关系由 LLM 从虚构+账本+可见旗标值自己推 —— 闭环就能成。**

这**不是**写 quest 状态机 / `success_conditions` / 分支图，只是**多声明几个旗标**并配好
`set_by`。红线不变：终态旗标仍只在 `success` 翻。

## 第一步：改内容包（选 skyglass 一条链先验）

挑一条之前卡住的链（例如**档案禁令链**：取得联签 → 提交禁令），在 `world_state_vars` 补
**中间前置 var**，例如：

- `clinician_cosign_obtained`（联签已取得），`set_by: ["npc.clinician_oro"]`
- 视需要再补 `array_fault_evidence_secured` 之类中间证据 var

要点：
- 每个中间 var 配好 `set_by`（对应有权限确认它的 NPC）、`label`、`request_keywords`
  （让自然语言请求能路由到它）、`initial: false`。
- **终态 var（如 `archive_injunction_filed`）保持不变** —— 它的成功要依赖中间 var 已为真。
- 别写 `success_conditions`（引擎不消费、也不该消费）；依赖让 arbiter 自己从虚构+账本推。

## 第二步：真机重跑（带 --log）

```bash
PYTHONPATH=src python -m verisaria run fixtures/content_packs/skyglass_memory_inquest.json \
  --llm minimax --log reports/<新目录>/run.log
```

走完整链：**先去找有权限的 NPC 把中间 var 翻成 true（如向 Oro 请求联签 → success →
`clinician_cosign_obtained=true`），再回到终态 NPC（Mae）请求提交禁令**，看 arbiter 是否因
"前置旗标已为真"而判 success、翻 `archive_injunction_filed`。

> CLI 自然语言 parser 这轮可能仍返回"我没理解"。**失败时 `run.log` 里会有 `verisaria.intent`
> 诊断行**（写明真实原因：budget/connection/json/schema/empty）。请把那几行**原样贴进报告** ——
> 这是开发侧定位 parser 的关键。parser 挡死时可用核心脚本绕过，但务必保留并贴出诊断行。

## 第三步：报告请包含

1. **闭环是否成立**：有没有跑出完整的 `中间var 记事实/翻 true → 终态var 因前置已满足而
   success → 翻旗`？贴 `run.log` 对应行（找 `world-change ... ⟳FLIP`）。
2. 若仍没闭环：卡在哪一步？中间 var 没翻、还是终态裁定没认前置？贴 arbiter 的 `reason=...`。
3. **一致性 / 真实感**：像谈判还是像填表？有没有"前置明明已为真、arbiter 仍无视"的例子
   （贴出来，这是 prompt 约束的关键回归）。
4. **②的回归**：非流式 world-change 的 NPC 台词，现在是否反映裁定（松口/拒绝），而不是泛化的
   "查档/编号"？
5. **CLI parser 诊断行**（如上）。
6. 新的 `run.log` + transcript + 改过的 pack 一并放进 `reports/<新目录>/`。

## 一句话目标

验证"**中间前置 = 多声明几个 world var**"这条路能否让调查链闭环。能 → 账本方向 + 引擎已基本
够用，缺的是内容结构；不能 → 把卡点 + 日志给开发侧，继续调 arbiter prompt 或检索。
