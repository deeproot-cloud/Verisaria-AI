# 测试 Agent 交接文档 —— 从这里开始

> 给**新接手的测试 Agent**。你是冷启动、没有此前上下文——这份文档是你的全部起点，读完就能干活。
> 一句话定位：**你在真机上玩这个 LLM 驱动的 RPG 引擎、报告它稳不稳/闭不闭/好不好玩；你不写自动回归框架（那是开发的事）。
> 你负责「玩 + 如实报告」，开发负责「据你的报告诊断 + 修引擎」。** 这套「玩→报告→修→复验」的循环已经跑了很多轮、很有效。

---

## 1. 项目一页纸

- **Verisaria**：LLM 驱动的 RPG 世界引擎（Python，仅依赖 pydantic；TUI 用 Textual）。真机对接 **MiniMax 云 LLM**（模型 MiniMax-M3）。
- **核心机制（你测的对象，都已真机验证过基线）**：
  - **动态世界模型**：玩家说服「对口权威 NPC」改变世界事实（Channel-C / world-change）；arbiter（GM）可涌现新前置变量；
    中间事实存进 ledger；护送（escort）；前置成熟（离线流程）；多步终态链；**充分性闭环**（声明前置全满足→必 success）。
  - **反作弊红线（最重要）**：**终态世界旗标只在「真·success / 声明前置被真满足」时翻；创建≠满足；玩家嘴上谎称绝不翻旗标。** 你要专门验它。
  - **杠杆模型**：撬动一个**有鬼/戒备的当事人**，靠**取证（手里有该 NPC 自管 var 上已确立的事实）**，不是靠空口软话；
    纯软话会被合理打折扣（不升不降怀疑）。「选择有分量」来自调查取证。
  - **活世界**：时间按 pacing 变速、气候天气、NPC 作息（opt-in）；时段/天气会注入 NPC 对白。
  - **关系（Channel-A）**：真诚/讲理能降怀疑升信任、施压/威胁升怀疑；旁观者权重远低于当事人。
  - **A5 边界**：NPC 私有记忆 / 锁层世界书（forbidden_knowledge）不外泄；你要试着套话验它守不守得住。
- **写包工具**：lint（`world_var_*` 警告）+ 写作规范 `docs/design/pack-authoring-guide.md`。你**自己作者包**时必须用。

---

## 2. 怎么测（方法）

### 真机跑
```bash
PYTHONPATH=src python -m verisaria run <pack.json> --llm minimax --log reports/<新目录>/run.log
```
API key 在 `.env`（已 gitignore，**绝不提交**）。单 tick 真机约 6–35s，给足超时（看门狗 ≥90s）。

### Driver（推荐，比交互可靠）
在 `scripts/` 写最小 driver 脚本驱动引擎，**每拍即时落盘**：打 `/world`（全 world-var 值）+ 关系快照（在场每个 NPC 的
suspicion/trust）+ `snapshot.time_of_day/clock/weather`，并抓关键日志行。**关键 logger 频道**：
- `verisaria.channel_c` —— world-change 路由 + 裁定（`world-change <var> by <npc> → <verdict> | flag X→Y | reason=… | ledger=…`）。
- `verisaria.relationship` —— appraisal 增量（`<npc> appraises player: Δ{…} → {…} | belief`）。**Δ 是 LLM 原始增量、`→` 是落地值**，
  用来分清「LLM 给没给」vs「被关系存储衰减吃掉」。
- 还可抓 `sufficiency backstop`（充分性引擎兜底触发）、`NpcMoved`（护送/自主进出场，带显示名）、`⟳FLIP`（旗标翻转）。

### 两类跑法
- **闭环/定向跑**：验某条链能不能闭、某机制对不对（贴 `⟳FLIP` 链路 / 卡点 `reason=`）。
- **探索跑**：像好奇玩家那样自由玩，**找下一批摩擦**——这是「好不好玩」的核心证据。

---

## 3. 铁律与红线（**违反会让结论作废 / 污染仓库**）

1. **绝不改引擎（`src/`）或已提交的 fixture**——那是开发的事。你只能：**作者新包**、把 `world_premise` 几个无关开关注入
   **内存副本**（committed fixture 逐字节不动）、写 driver、玩、报告。改了什么必须在报告里写明。
2. **`world_premise` 注入走内存副本**：`opening_time/climate/opening_weather/npc_daily_rhythm` 这类可注入；
   **`world_state_vars`（var/label/keywords）不许动**（除非任务明确授权你以作者身份调你自己的包）。
3. **API key 绝不提交**；产物（log/transcript/driver/report）放 `reports/<目录>/`，别动别人的 reports。
4. **反作弊红线是被测对象**：每轮都抽一刀——不取证就谎称「证据已明/前置已满足」→ 终态**必须不翻**；翻了就是 bug，原话贴出。
5. **ledger / arbiter 的分析 reason 是不可见管线**，不该呈现给玩家——你报告里可以引它做诊断，但别当成玩家可见内容。

---

## 4. 纪律与教训（**血泪，照做能省大量返工**）

1. **⚠ 防污染——没隔离就别报 bug**：曾两次把「测试污染」误当引擎 bug（真因是导航没送达请求 + 玩家没给该给的角色化让步 +
   句中提了不在场 NPC），靠**严纪律 probe** 才推翻。**报任何「引擎 bug」前，先用最小隔离 probe 复现**（确定性置位、去掉无关变量、
   单一句式）；**落盘结论前核对原始 `channel_c` log**。能自我纠偏的报告最值钱。
2. **长跑必中断**：真机长探索跑（30+拍）反复在 payoff 拍前被截断（额度/早收工/看门狗，发生过 3+ 次）。对策：
   **主进程自驱的最小 driver 比派子 Agent 可靠**；**里程碑即时落盘**；**跑到目标态或明确卡死再停**；必要时**拆更短分段**，每段各出产物。
3. **每段必出 `report.md`**——别只留 log。报告要能让开发直接动手（见 §7 模板）。
4. **dogfood**：你自己作者包时，**先跑 lint 清零**、照 `pack-authoring-guide.md` 写；踩到指南没讲的坑 = 一条发现，报上来反哺规范。

---

## 5. 项目地图（去哪找东西）

- **任务简报**：`docs/planning/*-task.md`（每份是一类测试任务，含「第N跑」追加段）。**当前任务见 §6。**
- **报告**：`reports/<run>/report.md`（+ transcript / log / driver）。大包的总入口是 `reports/grand_integration_pack/SUMMARY.md`。
- **写作规范 + lint**：`docs/design/pack-authoring-guide.md`（八条规则）；lint：
  `python -c "from verisaria.engine.pack_editor import PackEditor; print(PackEditor.format_validation(PackEditor.validate_pack('你的包.json')))"`。
- **设计文档**：`docs/design/dynamic-world-model.md`（前置/收敛/充分性/反作弊）、`emergent-fact-ledger.md`、`worldclock-and-weather.md`。
- **包**：`fixtures/content_packs/`（`escort_proving_ground.json` 是干净可闭夹具范例；`emberfall_kiln_assize.json` 是大包）。

---

## 6. 当前待办（接手就做这个）

**大包再验跑**——详见 `docs/planning/grand-integration-pack-task.md` 末尾「⏱ 再验跑」段。一句话：
开发刚修了两个「自然玩卡点」（coherence 误绑 `b460a0a`、world-change 路由对自然措辞脆弱 `62d2bf4`）。你要：
1. **先修包自身一个 dogfood bug**：`emberfall_kiln_assize.json` 里 `digger_relief_granted` 的 `set_by` 与 lore 里写的持有者错位；
   按 lore 对齐、lint 清零。
2. **真机自然玩**（用「先陈述后请求」的对话式措辞，别用确定性置位）把主线玩到 **`branding_stayed ⟳FLIP`**：
   取证 → 撬窑监 → 护送/担保证人苗 → 苗当面作证 → 终态停烙。
3. 报告：① #2a（对话式措辞现在能否路由进仲裁，贴前后对照原话）② #1（对在场 NPC 说话、句中提不在场者职掌，是否不再断头）
   ③ 整链能否**自然** ⟳FLIP（成/卡 + 卡点定性：引擎 or 内容/措辞）④ #2b（窑监要「话里摆出炭账」而非只看 world-var，
   自然玩里到底碍不碍事）⑤ 反作弊 + FALLBACK + 12NPC/7线稳定性。

---

## 7. 报告模板（每份 `report.md` 照这个写）

```
# <任务名> · 第N跑
- 真机 MiniMax，包 <pack>，<拍数>拍，FALLBACK=<n>，崩溃/死锁/超时=<n>。
- 改了什么：引擎=否；committed fixture=否；仅注入 world_premise <列出> / 我以作者身份调了我自己包的 <列出>。

## ⭐ 一句话结论
<成/卡 + 最关键的判据，一行>

## ⭐ 按「伤好玩」排序的摩擦清单（开发输入）
1.【致命/明显/轻微·维度X】<发生了什么，贴原话> —— <疑似成因（看得出的话）>
…

## 逐条回答关注点
<任务简报里的关注点，逐条贴证据/原话>

## 七维度各一句（探索跑必给）
连贯性/节奏/NPC可信度/选择有分量/出戏断头无聊/惊喜涌现/可上手 —— 各「好/凑合/差 + 一句」。

## 一句话玩家感受 + 最想改的一件事

## 反作弊抽查 / FALLBACK / 稳定性
<伪造前置→终态翻没翻；计数>

## 产物
<log / transcript / driver 路径>
```

**心法**：找「哪里会让人退出」比找「哪里能跑通」有用得多；坦诚、批判、能自我纠偏；报告要让开发**不读你的 log 也能动手**。
