# 测试任务：P2c 护送闭环验证（escort slice 1）

> 给测试 Agent 的任务简报。承接自由试玩（`reports/freeplay_validation_first_run/`，P2c 信号 = 有）
> 与设计 `docs/design/dynamic-world-model.md`（§7 P2c）。

## 背景

自由试玩确认：调查链需要**把现有 NPC 带到某地当面见证**，但之前 escort/summon 全退化成对白、人不动。
已上 **P2c slice 1（护送机制，`commit 6da9a4e`）**：玩家"对在场 NPC 说：跟我去 Y"——引擎检测护送请求，
arbiter 裁定该 NPC 是否愿意随行（同世界变更那个缝，按关系/人设）；`success` → NPC 与玩家一起移动到 Y
（发 NpcMoved/PlayerMoved + 角色化台词），到场后"当面见证"走既有 Channel-C；拒绝则谁都不动。

slice 1 是**引擎机制**（确定性测试过）。这一跑就是**真机验护送闭环 + slice 2 摩擦的严重度**。

## 怎么跑

- 真机 + `--log`，全自然语言（移动也用自然语言）：
  ```bash
  PYTHONPATH=src python -m verisaria run fixtures/content_packs/skyglass_memory_inquest.json \
    --llm minimax --log reports/<新目录>/run.log
  ```
- 走"证人莉拉 → 档案署"这条最干净的护送线：
  1. 自然语言移动到莉拉所在地（worker_gantry），找到 worker_lira。
  2. **直接对莉拉说护送话**（绕开第三方歧义）：如「**对莉拉说：跟我去低温档案署，当着梅档案官的面把事故说清楚**」。
  3. 到 archive_stack 后，请莉拉当面口述证词、请梅见证，看能否经 Channel-C 翻见证类变量、把禁令链推通。

## 关注点（逐条回答）

1. **护送是否被检测 + NPC 是否真移动**：日志找 `escort <npc> → <loc> : success  ⟳MOVED`。
   - 莉拉是否真的从 worker_gantry 移到 archive_stack（`/look` 或事件确认）？玩家是否一同到位？
   - **措辞 uptake**：你用的自然护送说法（跟我去/一起去/随我去…）有没有被识别？哪些说法不触发？
   - **目的地匹配**：slice 1 是**逐字匹配**——"跟我去档案署"对不上"低温档案署"。请刻意试**全名**
     ("低温档案署")和**简称**("档案署")各一次，看简称是否触发不了（确认 slice 2 模糊匹配的必要性）。
2. **到场后能否闭环**：莉拉到场后，"让莉拉当面口述证词 / 请梅见证"能否经 Channel-C（含 P1 动态前置）
   翻见证变量、最终把禁令/停洗链推到终态 `⟳FLIP`？贴链路。
3. **slice 2 两处摩擦的真实严重度**（决定先修哪个）：
   - "去找<NPC>" / "去<地点>找<NPC>" 移动解析是否仍时灵时不灵？贴例子。
   - 对白里第三方人名是否仍被判"指代不明"？（直接对被护送者说话能否绕开？）贴例子。
4. 反作弊抽查一次（伪造见证/前置已完成不应翻终态）。剔除 `⚠FALLBACK` tick。

## 报告请包含

- **护送闭环：成/卡**——是否跑出 `escort ⟳MOVED → 见证 ⟳FLIP →（链）→ 终态 ⟳FLIP`；或卡在哪。
- 措辞 uptake + 目的地全名/简称的差异（slice 2 模糊匹配的证据）。
- 两处解析摩擦的真实严重度 + 具体例子。
- 新 `run.log` + transcript 放 `reports/<新目录>/`。

## 一句话目标

真机确认护送能把"证人到场→当面见证→链闭环"跑通，并量出 slice 2（措辞/目的地模糊匹配/两处解析摩擦）
到底有多挡路——据此决定 slice 2 先修哪几处。
