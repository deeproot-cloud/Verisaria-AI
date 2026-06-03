# 测试任务：动态前置变量（P1）能否让 GM 自己完善世界、推进长链

> 给测试 Agent 的任务简报。配套设计见 [docs/design/dynamic-world-model.md](../design/dynamic-world-model.md)。
> 承接第二轮回归（`reports/skyglass_ledger_regression_test/`）：账本闭环的前半段已通过，
> 长链 A 卡在"LLM 涌现出比世界模型更细、无结构落点的条件"。

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
