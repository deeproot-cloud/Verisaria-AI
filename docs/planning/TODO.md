# RPG World Engine — 工程计划

> 本文件记录所有已知问题、技术债务和待办任务，按优先级排序。
> 每次开始新任务前，先更新本文件中对应条目的状态。
> 测试基准：**840 passed, 2 skipped**（含 1 个需 `RPG_OLLAMA_TEST=1` 的真实 Ollama 集成测试，默认 skip）。

---

## 优先级说明

| 优先级 | 含义 | 标准 |
|---|---|---|
| **P0** | 阻断核心体验 | 玩家直接感受到的 bug 或缺失，必须立即修复 |
| **P1** | 影响沉浸感 | 玩家能察觉到的不自然，长期会降低游戏可信度 |
| **P2** | 架构债务 | 不立刻影响体验，但会阻碍后续功能扩展 |
| **P3** | 长期规划 | Phase 2+ 的功能，当前不影响 MVP |

---

## ★ 自主试玩修复（2026-06-01，frostgate / MiniMax，真实意图解析 + 重定向到文件）

> 用脚本驱动真实对局、记录每句的 ⟦解析⟧，连抓 3 个集成层 bug（单测全绿但整局崩）：
> - **Bug 1 误解析（已修）**：复合"我**走到**X面前**问他**：Y"被 LLM 判成纯 MOVEMENT，丢了问题 + 把玩家挪走 → 后置守卫：movement 带 speech content 且 target 是 NPC → 重分类为 SPEECH。+3 测试。
> - **Bug 2 过度触发（已修）**：world-change 路由命中宽泛 topic 词（"入营"），把**问句**当成开门命令 → 收紧 `request_keywords` 为命令短语 + 加 `_looks_like_question` 问句守卫。
> - **Bug 3 NPC 乱走（已修，符号化）**：被搭话的 brann 当拍 `in_conv=False` → idle 移动走人 → 对话被拆散。**确定性复现不出（会话逻辑在 repro 里正常，含强制 concurrency/streaming）→ 不盲修根因（PLAY-4 纪律）**，而是加**鲁棒不变量**：被点名 NPC 当拍强制进 `active_convs`（回应、不闲逛），无论会话簿记是否抖动。+1 测试。
> - **Bug 4（队长→voss）**：brann 走开后"队长"被错派给在场的 voss——是 Bug 3 的下游，brann 不再走后自动消失（重跑里"队长"正确解析到 brann）。
> - **真实路径验证**：修后 brann `in_conv=True`、做 speech、留在 gatehouse，对话顺畅；C 闭环可达（低关系下 brann 角色化拒绝，合理）。
> - 残留：真实解析器偶发 `parse_failed`（被 PLAY-2 优雅处理，不崩）；个别模型错字。

## ★ 可玩性诊断（2026-05-31，frostgate 真实 Ollama 完整对局，玩家带意图）

> 以玩家视角跑一局有意图的对局（探查冲突→站队→行动→查目标），诊断"演示 vs 游戏"差距。结论：**世界像个有氛围的活剧场，但还不像游戏**——玩家说"我想帮难民"，世界不记得、不在乎、什么都没变。问题分两类：**A 能不能玩下去（摩擦）**、**B 玩起来有没有意义（本质）**。待用户排优先级。

### PLAY-1 [A·致命] tick 太慢（≈190s，5 NPC 同处）

**状态：** 🟡 进行中——并发提速 **slice 1（appraisal 通道）已完成并真实验证**（2026-06-01，~2.2×）；slice 2（NPC 对话并发）待做  
**影响：** frostgate（5 NPC 同处）实测 **176~190 秒/tick**，前 3 tick 各 ~3 分钟。玩家撑不过第一分钟。  

**⚠️ 重要：先区分"部署问题"和"架构问题"，别把架构去迁就本机 Ollama 的慢。** 当前是**开发环境用本机 Ollama 串行调用**，后续可能部署到 GPU 机器或云端 API。把"慢"拆成三个独立成分：

| 成分 | 是什么 | 换 GPU/云端 API 后 | 该怎么对待 |
|---|---|---|---|
| ① 单次调用延迟 | 本机 Ollama 一次生成十几~几十秒 | **大幅缓解** | **纯部署问题，不该改架构去补偿** |
| ② 串行累加 | N 个 NPC 的调用首尾相接 = N×单次延迟（`_collect_npc_actions` 逐 NPC 同步 `llm.call()`，`OllamaProvider.call` 是阻塞 urllib） | 云端 API **天然支持并发**，串行纯浪费 | **架构问题，值得修——用"并发"而非"砍数量"** |
| ③ 调用次数 | 每 tick 每个在场 NPC 各调一次 | 次数不变，单次快了影响变小 | 多数合理（热闹世界本就该多对话），**不该为本机砍掉** |

**正确修复方向（核心是 ②，不是限流降级）：**
1. **NPC 对话并发请求**：把 `_collect_npc_actions` 里逐 NPC 的串行 `generate_line` 改为并发（线程池/异步），本机从"5×延迟"降到"≈1×延迟"，且对 GPU/云端部署正向（它们支持并发，串行是浪费）
2. **守 A10 回放红线**：并发只用于真实 Provider；FakeLLM / replay 路径仍走**确定性串行**（FakeLLM 是同步的，可保持顺序确定）——这是实现时最需要小心的点
3. **不做**：不砍 NPC 对话数量、不为本机强行降级模板——那会为临时环境永久阉割"热闹世界"卖点，上了 GPU 反而留个残废

**前置决策：** 部署方向已定（**云端 API / MiniMax**）。

> **并发提速 slice 1 已完成 + 真实验证（2026-06-01）**：
> - 机制：providers 加 `supports_concurrency`（真实网络 provider=True，FakeLLM=False，**守 A10**）；orchestrator budget 加锁（并发不丢计数）；cli 加通用 `_run_llm_jobs`（真实 provider 并发、FakeLLM 串行、**结果按输入序返回保确定性**）。
> - **首个目标：appraisal 通道**（我加 Channel A 时引入的 N 个串行调用，每 perceiving NPC 一个）改两阶段：串行收集→并发评价→串行应用。
> - **真实 MiniMax A/B**：玩家在 gatehouse 说话（2 个 NPC 感知）→ tick **54.2s（串行）→ 24.6s（并发），~2.2×**；NPC 越多差距越大（串行=Σ，并发=max）。
> - 测试：[test_concurrency.py](tests/test_concurrency.py) 2（flag/budget 线程安全）+ appraisal 并发路径 1。
> - **slice 2（🔴 待做）**：NPC 对话并发（`generate_line`）——比 appraisal 复杂，因 `_generate_for_npc` 缠着 `self._rng`/`self._seq`（必须确定性消费）。

> **延迟 slice 3：模型/思考/后台化（2026-06-01）**：
> - **默认换 `MiniMax-M2`**（实测短任务 ~3.8s vs highspeed ~8s）+ **`thinking=disabled`**（M2.7 上省 ~20%）；provider 加 `extra_params`（vendor 专属参数走配置，如 `thinking`/`max_completion_tokens`），仍是同一通用类。
> - **评价后台化**：真实 provider 下 appraisal 移出玩家关键路径（`_appraisal_executor` 后台跑），在**下一拍/读关系/存档**前 `_flush_appraisals()` barrier 应用；FakeLLM 仍 inline（A10）。+3 测试。
> - **真实整拍实测**：M2 + thinking off + 评价后台化 → t_tick=**18.4s**（评价的 7.4s 已不在路径内，否则 ~26s）。**仍超 10s**，因关键路径还有 **意图(1)+对话(2)=3 个串行调用**，且 **M2 跑真实大 prompt ~6s/次（非孤立测的 3.8s）**。

> **延迟 slice 4：对话并发预取（2026-06-01，用户拍板"速度优先、成本不重要"）**：
> - 串行 NPC loop 前，把**所有候选 speaker**（in_conversation 或有同地邻居，屏内+屏外）台词**并发预取**进 `NPCActionGenerator._line_cache`；loop **一行不改**，只把"调 LLM"换成"读缓存"——确定性安全（A10），FakeLLM 不预取走原路。屏外世界不滞后（事件这一拍照常处理）。预算上限 10→50（成本不重要，放开防可见 NPC 被节流降级）。
> - **真实整拍实测**：18.4s → **13.7s**（累计自 45s，3.3×）。关键路径已压到**理论下限：意图(1) + 并发对话(1) = 2 个串行调用**；4 个对话调用已并发、评价已后台。
> - **13.7s 仍 >10s 的原因**：M2 跑真实大 prompt **~6-7s/次**，2 个串行调用就 ~13s。**架构已无法再砍**（意图必须先于对话）。
> - **跨 <10s 墙的剩余杠杆（非架构层）**：① **意图解析快通道**（简单/指令输入走规则，省掉意图调用 → ~7s）；② **更快端点/模型**（M2 ~6s → Groq 类 ~2s，则 ~4-6s）；③ **prompt 瘦身**；④ **流式输出**——13.7s 配流式体感≈秒回，"显得 <10s"最实际。
> - 测试：+2（line cache 替换 / 并发预取端到端）。

> **延迟 slice 5：流式输出（2026-06-01）——感知延迟，"显得 <10s"**：
> - 架构现实：引擎输出结构化 JSON（schema 校验），逐 token 吐 JSON 没法看；**只有 NPC 台词（纯文本）能干净流式**，意图/评价/arbiter 仍阻塞 JSON。流式配 thinking off 才干净（正是 MiniMax 默认）。
> - 实现：`OpenAICompatibleProvider.call_stream`（SSE 解析、纯文本累积、`supports_streaming` flag）+ `NPCDialogueGenerator.generate_line_stream`（纯文本 prompt）+ cli 把**被点名 NPC** 的回应**前台流式**（其余 NPC 后台并发预取，重叠）+ narrative **跳过已流式的那句和玩家自己刚输入的那句**（`ResponseGenerator.skip_speech_actors`，避免重复打印）+ REPL 设 sink 逐字打印。
> - **真实 MiniMax 验证**：`MiniMax-M2` 台词流式 **TTFT=0.8s / 总 5.9s / 13 chunks**——玩家 **0.8s 就看到 voss 开口**，而非等 6s。台词质量很好（带 `*动作*` 神态）。
> - 测试：+3（call_stream SSE / supports_streaming flag / 流式渲染+narrative 跳过端到端）。
> - **进度提示已补（全程不冻屏）**：意图解析前发 `（正在领会你的意思…）`（`_emit_progress`/`_progress_sink`，**仅真实 provider 触发**，FakeLLM 不闪）。一拍全程有反馈：输入 →"领会中…"→ voss 0.8s 起逐字 → 叙事。+1 测试。
> - **试玩抓到并修了真 bug（think 泄漏）**：M2 在复杂 prompt 下**即便 `thinking=disabled` 仍吐 `<think>` 块**；`call_stream` 原本对每个 token（含 think）都触发 `on_delta` → 玩家看到整段思考。修：`call_stream` 状态机**只流式 `</think>` 之后的答案**（holdback 防部分标记闪现）。真实验证 leak=no、台词干净。+1 测试。**残留**：think 仍耗生成时间 → 答案前有停顿（不泄漏但延迟在）；在意可换模型或加"斟酌中…"提示。

> **云端 API 接入已就绪 + 真实验证（2026-06-01）**：新增**通用** `OpenAICompatibleProvider`（[llm.py](src/rpg_demo/llm.py)，纯 stdlib、零依赖、JSON mode + schema 校验）。`--llm openai` / `--llm minimax` 走同一个类，**vendor 仅是 base_url+model+key 配置（env 覆盖），不写专用 provider**。key 走 `.env`（gitignore），`play.sh` 自动 source + `--minimax`/`--openai` 开关。
> - **真实 MiniMax 验证通过**：`MiniMax-M2.7-highspeed`（默认）端到端返回合规 schema JSON，~14.7s/调用。
> - **顺手修真实 bug**：M2.7 是推理模型，content 带 `<think>…</think>`，其花括号噪声会搞挂 JSON 提取 → 共享提取器 `_extract_json_object` 现**先剥离 think 块**（对任何推理模型通用，含 gpt-oss）。+1 测试。
> - 选 OpenAI 兼容而非 Anthropic 兼容（后者主打 thinking，对结构化 JSON 是噪声）。测试 [test_openai_provider.py](tests/test_openai_provider.py) 6 个 + think 块 1 个。

**预计工作量：** 3-5 小时（并发改造 + 守住 replay 确定性）+ 真实环境计时验证

---

### PLAY-2 [A·致命] 玩家复杂意图被吞 / clarification 合并污染

**状态：** ✅ 已完成（2026-05-31，真实 Ollama 验证）  
**影响：** 富文本意图——这游戏鼓励的核心玩法——频繁失败：
- 「问布兰队长难民为什么不能进来」→ `我没理解你的意思`（整轮空转）
- 「问哨兵伏斯他怕什么」→ 被合并污染成 `「难民为什么不能进来，具体来说是：问哨兵伏斯他怕什么」`，且**问伏斯却对队长说话**（target 错位）

**根因（精确定位）：** `parse_failed`（LLM 完全解析不了）和真正的"歧义"被同等对待——都进了 `_active_clarification` 的**有状态合并循环**。下一句（哪怕是完全不同的新问题）被 `_resolve_clarification` 的 fallback 盲目拼成 `"原句，具体来说是：新句"`（`cli.py:420`），再发给错的 target。但 parse 失败时原句是垃圾，合并垃圾+新句=更糟，且后续 target 错位全是这个污染的下游。  
**已实现：** `cli.py:_execute_tick` 把 `parse_failed` 与已有的 `coherence_error` 同等处理——**不进合并循环**，只提示"请重新输入"，下一句当**全新输入**走 parse。真正的歧义（pronoun/movement_destination 等）仍走原合并逻辑（`test_free_text_clarification` 仍绿）。  
**验收标准：**
- ✅ parse_failed 不留 sticky clarification、下一句 verbatim 到 parse、无"具体来说是"污染（`test_parse_failed_does_not_enter_merge_loop`）
- ✅ **真实 Ollama before/after**：原失败序列重跑 → 第 1 句这次直接解析成功（队长正经回答）；第 2 句 `parse() got '问哨兵伏斯他怕什么'`（verbatim）、**问对了 sentry voss**、零污染
- ✅ 新增 1 个测试；全套 781 passed / 2 skipped

> 真实重跑还顺带印证了 PLAY-4：玩家问 voss，voss 没回应、反而队长又开口——留给 PLAY-4 的真实案例。

**涉及文件：** `cli.py`, `tests/test_clarification.py`

---

### PLAY-3 [B·核心] 玩家选择零后果——"演示 vs 游戏"的分水岭

**状态：** 🟢 三通道主体完成并真实验证（2026-06-01）——A 关系 / B 立场 / C 世界旗标，"选择零后果"已闭环；剩 B-slice3（公开声明）等可选增量  
**原影响：** 玩家行动 7 轮、明确站队，结果：NPC 毫无反应、`/agenda` 无已确认目标、relationship 全空 `{}`、世界零改变——玩家只是旁观者。

**确诊根因（已用代码核实）：** 回应链与"记忆沉淀"是通的，但**"记忆 → 关系/立场/世界状态"三座桥全断**：
- `update_relationship`/`calculate` 游戏循环从不调用；对玩家的 6 维关系不算、不存、不显示
- agenda 只属玩家、NPC 无 agenda；无可变世界旗标

**方案：三条后果通道（用户拍板 LLM appraisal，为"大世界真实"）**

**① Channel A — 关系（✅ 已完成）**
- 新增 [appraisal.py](src/rpg_demo/appraisal.py)：`RelationshipAppraiser`（走 LLM seam，FakeLLM/replay 确定性）+ `RelationshipStore`（累积、双 clamp）+ `AppraisalResult`（belief + 有界 deltas + reason）
- NPC 感知到有社交重量的玩家行动 → LLM 以**该 NPC 自己的 world-book/记忆**评价（A5 接地）→ 有界 deltas 累积进关系快照 + belief 写回记忆（被记住、下次对话引用）
- 玩家可见：`/relationship`；可持久化：save/load（存于 npc_runtime_state）
- 守住红线：走 Provider seam（重放安全，**非**靠规则化逻辑）；LLM 不可用 → 记 0 变化（P5 降级，不崩 tick）；慷慨门（凡感知到的 NPC 都评价）
- **真实 Ollama 双次验证**：① 评价合法且有角色逻辑（29s）；② 接地后 voss 基于完整认知（教会宣传 + "恶魔是人"canonical fact + devout/young）自洽地倒向怜悯——真实 ≠ 阵营刻板反应（24s）
- 测试：[test_relationship_appraisal.py](tests/test_relationship_appraisal.py) 7 个（方向/累积/clamp/接地/记忆/持久化/展示）

**② Channel B — 立场聚合（✅ slice 1+2 已完成；slice 3 公开声明可选）**
- **确诊根因**：CLI 的 agenda 聚合**死锁**——`aggregate_signals` 只在 reflection scene 里跑，而 reflection 又只靠 `unconfirmed_inferences` 触发（需要已存在的 proposal）⇒ 聚合从不运行 ⇒ `/agenda` 永远 "[已确认目标] 无"
- **slice 1（done）**：新增 `AgendaService.aggregate_and_autoconfirm`，每个玩家 tick 跑聚合 + 自动确认强立场（≥`MIN_SIGNAL_COUNT`=5 次同主题意图，确定性、无 LLM）；新立场确认时 narrative 内联反馈"（你逐渐确信了自己的目标：…）"；`/agenda` 显示已确认目标；agenda 已随 save/load 持久化；confirmed_drives 已自动喂入 hint context
- 测试：[test_player_stance.py](tests/test_player_stance.py) 4 个（聚合确认/单次不误确认/`/agenda` 显示/持久化）
- **slice 1 修订（试玩抓出，2026-06-01）**：原本每拍按**原话 label** 去重 → 同一立场每拍新增一个"目标"，`/agenda` 堆了 7 个逐字引语 + [系统推断] 完全重复。修：① `aggregate_signals` **按 topic 去重**（已确认主题不再 re-propose，一主题一目标）；② drive label 用 **pack 主题名**（"接纳难民"）而非原话；③ `get_agenda` 的 system_inferred **排除已确认主题**（不重复显示）；④ `_confirmed_topics` 记全部主题供去重，`get_confirmed_stance_topics` 过滤回 pack 主题供 campaign。+1 测试
- **slice 2（✅ 已完成，B2-A1）**：pack 声明 `stance_topics`（topic_id + keywords）→ agenda **按 pack 主题打标签聚类**（顺手修掉"同义不同句不聚类"的局限）→ 确认后暴露稳定旗标 `stance.<topic>` 进 campaign context → pack driver 条件可读。**A5 安全**：只有 campaign driver（世界/剧情层）读，**不进 NPC 私有认知**（NPC 对玩家行为的反应由 Channel A 覆盖）
  - 改动：`ContentPack.stance_topics` schema；`AgendaService(stance_topics=...)` + `get_confirmed_stance_topics()` + 持久化；cli `_build_campaign_context` 注入 `context["stance"]`；frostgate 加 `stance_topics` + 立场驱动 driver `watch_wary_of_refugee_ally`
  - **端到端验证**：5 拍"帮难民"→ `help_refugees` 确认 → driver 触发 → narrative `[压力事件] watch_grows_wary_of_player`——玩家反复站队真正改变了剧情压力
  - 测试：+4（同义聚类/旗标进 context/driver 条件触发/确认主题持久化），共 8 个
- **slice 3（🔴 可选，需设计）**：玩家**公开声明**立场 → 合法进入 NPC 认知（intent→declared_to_world→NPC 对话/评价）。当前仅 system_inferred；声明路径未建

**③ Channel C — 世界旗标（✅ slice 1a+1b 已完成）**
- **确诊现状**：`WorldState` 只有 tick/entities/locations，**无世界变量存储**；但 arbiter 已有 `state_changes_proposed → accepted_state_changes`，经 `_apply_state_changes`（仅处理 `entity.attr`）落地；driver context 支持 `world.xxx` dotted path 但无数据源
- **设计（与 A 同构）**：玩家自由行动 → arbiter(LLM) 提议 `world.<var>` 变更 → 规则按 pack 声明校验（确定性闸门，防 LLM 乱翻）→ 落地 `world_vars` → driver/NPC/叙事读 + 持久化
- **slice 1a（✅ done，全确定性）**：`ContentPack.world_state_vars` + `WorldState.world_vars`（持久化经 `_world_state_to/from_dict`）；`set_world_var`（**按声明+mutable 闸门**）/`get_world_var`；`_apply_state_changes` 加 `world.*` 分支路由到闸门；`_build_campaign_context` 注入 `context["world"]`（点亮 `world.*` driver 条件）；`/world` 展示；frostgate 加 `refugees_admitted` + 后果 driver `camp_strains_under_refugees`
  - **端到端验证（确定性）**：翻 `refugees_admitted=True` → 下一拍后果 driver 触发 `camp_overcrowding_tension`；`/world` 显示；save/load 往返保值
  - 测试：[test_world_state.py](tests/test_world_state.py) 6 个（初始化/闸门/状态变更路由/进 context+触发 driver/持久化/展示）
- **slice 1b（✅ done）**：arbiter prompt 增"## 可改变的世界状态"段（列 var_id/label/当前值/`set_by`），告诉 LLM 可提议 `world.<var>` 变更；StateValidator **本就接受** `world.*`（无 authorized_fields 限制、bool 无 bounds、非 memory/belief 不触发因果）→ 经 slice 1a 的 `_apply_state_changes` 落地。cli `_handle_arbiter_action` 在 arbitrate 前注入 `world.mutable_world_vars`
  - **真实 Ollama 验证（涌现的权限裁决）**：① 无权新人下令 → arbiter `failure`「玩家无权…只有 watch_authority 具备此权限」、零变更；② 带 watch_authority 下令 → `success`、提议+接受 `world.refugees_admitted=True`、世界真的翻牌、叙事"城门开启，难民步入营区"。**权限闸门是 LLM 从 prompt 的 `set_by` 自己裁决的，非硬编码**
  - 测试：+2（prompt 广告可变变量 / arbiter 提议被应用），共 8 个
- **slice 1c — 触发缺口修复（✅ done，试玩 save_21 暴露）**：原问题——玩家"对伏斯说:开门"被解析成 **SPEECH 到不了 arbiter**，NPC 口头同意又改不了世界（A5）→ **虚构/状态脱节**(voss"答应了"但 `/world` 仍 False)。设计=**权限闸门 + 说服驱动,把 Channel A 接到 C**：
  - pack：brann 加 `attributes.authority="watch_authority"`（原 `set_by` 是悬空引用,谁都没这权限）；world_var 加 `request_keywords`
  - cli：`_world_change_request` 检测"对**有权 NPC** 说 + 命中请求关键词"→ 路由到 `_handle_world_change_request`→ arbiter 裁决；`_world_vars_for_arbiter` 把**权威 NPC 对玩家的关系**喂进 arbiter；arbiter prompt 改为"**只有持权 NPC 基于其对当事人的态度与职责会同意时**才提议变更"
  - **真实 MiniMax A/B**：高信任(0.85)向布兰请求 → arbiter **读到关系**（"信任度高 0.85"）并权衡职责 → `partial_success`「难以仅凭一言即开城门…需更多理由」（未翻,合理:说服队长非一蹴）；FakeLLM comply fixture → 翻牌路径已测。**关系(A)成了改写世界(C)的钥匙,且裁决有层次**
  - 测试：+4（向权威请求 comply 翻牌 / 拒绝不翻 / 向无权 NPC 请求不翻 / arbiter 看到关系）
  - **残留①已修（角色化发声）**：分离裁决与发声——arbiter 静默裁决（分析理由**永不示人**），权威 NPC 回应由对话生成器用其嗓音生成（`directive` 注入"请求+决定",可流式）；据 outcome→`_AUTHORITY_STANCE`。+1 测试。**真实 MiniMax**：高关系 + 强论据("我愿共担后果")→ arbiter 同意 → `refugees_admitted=True` 翻牌 → brann「好吧，开城门。出了事你自己担着。」、**零分析泄漏**。**完整闭环跑通：说服对的人(A)+好论据 → 世界真的变(C)，发声有血有肉**
  - **残留②（门槛）**：弱请求→"需更多理由"；高关系+强论据→翻。门槛合理（响应论据质量），如需更易可调
- **内容调优（✅ done）**：原 `scarcity_xenophobia` 准入后仍报 `refugee_denied_entry`（世界自相矛盾）。修复=**通用引擎修复 + 内容拆分**：① SignalEvaluator 现支持 bool 的 `== true/false`（先前 bool vs "false" 恒为 False）——任何 pack 写 `world.X == false` 都受益；② frostgate 把 `refugee_denied_entry` 拆到新 driver `border_closure`（signal `world.refugees_admitted == false` 闸门），`scarcity_xenophobia` 只留 rations/patrol（scarcity 与准入无关，常驻）
  - **端到端验证**：未准入 → `border_closure` 报 refugee_denied；**准入后 border_closure 静默、改由 `camp_strains` 报 overcrowding**，无矛盾。+3 测试（bool 比较 / 闸门触发 / 准入后静默）

**A 调优（✅ 已做）：** 关系累加从"线性+硬 clamp"改为**边际递减**（正增量按剩余空间 `1-cur` 缩放、负增量按 `cur` 缩放）——原本 6 拍就把 trust/affection 干到 1.0，现在 `0.2→0.36→0.49→…→0.74(@6拍)`，越接近上限越难涨，渐近不撞顶。+2 测试。  
> **A+B 真实 Ollama 联跑（2026-06-01）已验证涌现叙事**：玩家连说 6 句"帮难民"——voss(devout) 信任拉满站你这边、kaze(难民)信任你；**而 brann(suspicious) 把教会宣传读出来**（"compassion may mask demonic influence / demon in disguise"）越来越提防你，且 watch 建制层 driver `watch_grows_wary_of_player` 触发。个人关系(A)+派系剧情(B)互相加码，自发讲出"你为难民发声→守军内部对你裂开"的故事。  

**残留改进（非阻塞）：** 接地能否**翻转**判断,用 captain_brann(trait suspicious)做对照更明显;暂不追。NPC 台词偏重复（voss 每拍祈祷）属对话质量,可后续。  
**预计工作量：** B 中等（复用 agenda）；C 较大（新建机制）

---

### PLAY-4 [A·体验] 被点名的 NPC 那一拍不回应

**状态：** ✅ 已查清——**不是独立 bug，是 PLAY-2 污染的下游症状**（2026-05-31）  
**影响（原观察）：** 玩家问 voss，narrative 里 voss 没出声、反而队长开口。  
**诊断结论（用真实数据，未凭假设）：**
1. **真实 Ollama 干净重跑**：玩家问 voss → voss 正确 in_conversation、生成 `「我怕失去守护的力量。」`、narrative **正确显示**。复现不出"不回应"。
2. **根因**：原观察发生在 PLAY-2 污染序列里——输入被合并污染、发给了错的 NPC，voss 从没真正被点名，自然没进对话。**PLAY-2 修好后，被点名的 NPC 可靠回应。**
3. **鲁棒性压测**：把 dialogue_generator 强制返回空（模拟 LLM 那一拍没词），40 个 seed 下被点名的 voss **全部仍产出非空回应**——`_pick_speech_content` 的降级链（LLM→对话模板→记忆→闲聊）已保证 in_conversation 的 NPC 必有台词。

**结论：无需修复**。被点名 NPC 当 tick 必有回应这一保证已存在且经压测。**没有为复现不出的 bug 凭空写修复**（避免凭假设改代码）。  
**残留观察（归入 PLAY-1）：** 诊断发现远处（玩家看不到的 location）NPC 也在花 LLM 生成对话（如 old_sib 在 refugee_camp 对话但玩家在 gatehouse，被 location 过滤挡掉却仍消耗一次调用）——这是真实浪费，属 PLAY-1 性能范畴。

**涉及文件：** 无（查清即结案）

---

> **真实 Ollama 对局发现（2026-05-31）**：跑完整黄金路径（纯玩家输入、不手动操纵世界）暴露了下面 4 个集成层问题——单元测试全绿但端到端才浮现。核心 LLM 对话链路（P1.1）证实可玩且质量高；以下是体验硬伤。

## P0 — 阻断核心体验

### P0.3 玩家意图解析出无效动作，整轮空转

**状态：** ✅ 已完成（2026-05-31）  
**影响：** 黄金路径上玩家输入「回到广场」「跟卫兵打听」整 tick 空转，玩家"说了话却没效果"。  
**根因（两类）：**
1. **地点消歧失败**：LLM 把「广场」解析成 `square`，但世界里是 `town_square` → coherence 报 `Cannot move to unknown location 'square'`。`_resolve_target_id` 只做 exact/prefix/last-segment 匹配，对 location 没有 substring。
2. **不在场目标无友好提示**：玩家在 tavern 说「跟卫兵打听」，卫兵在 town_square → 抛 `Actor cannot interact with target` 裁决报错。

**已实现（`coherence.py` + `intent.py`）：**
1. `coherence._resolve_target_id` 加 location substring 兜底：`ref in lid or lid in ref`，**只接受唯一匹配** → `square`→`town_square`、`street`→`loc_street`
2. spatial_mismatch 消息改为叙事化："{name}不在这儿，TA在{loc}那边，你得先过去才能搭话。"
3. **（残留补丁，闭环验证发现）** `intent.py:_resolve_movement_location`：真实 LLM 把中文地点名（如「广场」）放进 `modifiers.to_location`/`target_ref` 并误标 ambiguous → 在 IntentParser **歧义检查前**用 `_CN_LOCATION_MAP`（广场→town_square、酒馆→tavern…）+ substring 解析，并清掉对应 ambiguity，不再误弹 clarification

**验收标准：**
- ✅ 部分地点名解析（`TestLocationSubstringResolution`）；歧义/不存在仍报 unknown
- ✅ 不在场目标给在世提示而非裁决黑话（`TestOffSceneTarget`）
- ✅ **走完整 parse() 链路**的中文/部分地点解析（`test_movement_chinese_location_alias_resolves` / `test_movement_location_substring_resolves`）
- ✅ 用**真实 gpt-oss 实际输出形状**（`to_location:"广场"`+`ambiguities:["广场"]`）确定性验证：解析为 town_square、ambiguities 清空、无 clarification
- ✅ 新增 6 个测试；全套 737 passed / 4 skipped

> **闭环验证教训（重要）**：P0.3 首次只在 coherence 层修 + 只测 coherence，漏了 IntentParser 路径——真实 Ollama 对局才暴露。补丁后用**捕获的真实 LLM 输出**而非假设的形状写测试。期间还发现一个**独立的既有问题**（见 P1.5）。

**涉及文件：** `coherence.py`, `intent.py`, `tests/test_coherence.py`, `tests/test_intent.py`

---

### P0.4 Arbiter 裁决黑话泄漏到玩家叙事

**状态：** ✅ 已完成（2026-05-31）  
**影响：** 「和ele打个招呼」→ 玩家看到 `"NPC略有动摇，玩家可继续尝试或换策略。"`——arbiter 的裁决元信息直接当叙事显示，严重出戏。  
**根因（两层）：**
1. `_resolve_social` 让**所有** social 动作无条件走 arbiter；打招呼被 `_infer_verb` 默认成 `persuade`
2. `_handle_arbiter_action` 把 arbiter 的 `narration_hint`（meta 提示）原样当叙事显示

**已实现（两道防线）：**
1. **根治**：`interaction.py:_infer_verb` 识别招呼/寒暄关键词 → 返回 `greet`；`rules.py:_resolve_social` 只让 contest verbs（persuade/deceive/bribe/intimidate/threaten）走 arbiter，greet/chat 直接结算、不走 arbiter
2. **兜底**：`cli.py:_handle_arbiter_action` 改为优先用 response_generator 叙事；`_is_meta_hint()` 过滤掉含「裁决/玩家可/换策略/NPC/系统/default/fallback」等 meta 措辞的 hint，绝不显示给玩家

**验收标准：**
- ✅ 招呼推断为 `greet` 且不走 arbiter（`test_greeting_infers_greet_verb` / `test_greeting_does_not_need_arbiter`）；contest verbs 仍走 arbiter
- ✅ meta-hint 不泄漏、in-world prose 保留（`test_meta_hint_not_leaked_to_player` / `test_is_meta_hint_classifier`）
- ✅ 端到端：「和ele打个招呼」→ verb=greet、requires_arbiter=False（不再出现裁决黑话）
- ✅ 新增 5 个测试；全套 725 passed / 4 skipped

**涉及文件：** `interaction.py`, `rules.py`, `cli.py`, `tests/test_rules.py`, `tests/test_interaction.py`, `tests/test_arbiter_cli_integration.py`

---

## P1 — 影响沉浸感

### P1.3 NPC 记忆/belief 文本是系统日志而非人话

**状态：** ✅ 已完成（2026-05-31）  
**影响：** NPC 记忆全是 `'看见：npc.guard_b look'`、`'看见：npc.guard_b wait'`（NPC"记住自己发呆"），belief 也是同样无意义内容。NPC 认知不可信。  
**根因：**
1. `ObservationDispatcher._find_potential_observers` 把 actor 自己也算观察者 → NPC 观察自己的 wait/look 生成记忆
2. wait/look 这种琐碎 idle 动作本不该成为任何人的记忆
3. `_build_claim` 把同一 event summary 同时塞进「看见：X；听到：X」，冗余

**已实现：**
1. `observation.py`：`wait` 事件**不产生任何 observation**（纯噪声）；`wait`/`look` 这类 trivial 动作的 **actor 自身**被排除出观察者（不记住自己发呆）；他人对 look 仍能观察
2. `subjectivity.py`：`_build_claim` 去重——同一内容经视/听两通道时合并为「看见并听到：X」，不再重复

**验收标准：**
- ✅ actor 不记自己的 wait/look；wait 事件零观察；meaningful 动作（steal/speech）仍记（`TestTrivialSelfActionFiltered` 5 个）
- ✅ 记忆文本去重（`test_claim_dedupes_same_sight_and_hearing`）
- ✅ 端到端：3 次 wait/look → 0 条垃圾记忆；只剩有意义的「看见并听到：player 对 guard 说话」
- ✅ 新增 6 个测试；全套 731 passed / 4 skipped

**说明：** 没有引入 LLM 美化记忆文本（保持确定性 + 零成本）。过滤掉噪声后剩下的都是有意义的真实记忆，文本已可读。如需进一步自然语言化可另开任务。

**涉及文件：** `observation.py`, `subjectivity.py`, `tests/test_observation.py`, `tests/test_subjectivity.py`

---

### P1.4 NPC 反应时机错位（跨 location 噪声）

**状态：** ✅ 已完成（2026-05-31）  
**影响：** 玩家「去酒馆」（还没到）触发 `"ele静观其变。"`——ele 在 tavern，玩家在 town_square，这条反应时机/位置不对。  
**根因（两类，复现确认）：**
1. **过滤用错时点**：`response_generator.generate` 用玩家**移动后**的 location 过滤 NPC 事件 → 移动 tick 里玩家"预先看到"了目的地（tavern）的 ele，反而看不到起点（town_square）的事件
2. **NPC idle 噪声**：ele 的 `wait` 动作被当叙事显示给玩家（"静观其变"）

**已实现：**
1. `response_generator.generate(viewer_location=...)`：按玩家**事件发生时**（tick 起点）的 location 过滤；`cli.py` 在队列结算前捕获玩家旧 location 传入 → 移动 tick 看起点、不预看目的地
2. NPC 的 `wait` 动作不再叙事化给玩家（与 P1.3 把 wait 当噪声一致）；玩家自己的 wait 仍显示

**验收标准：**
- ✅ 移动时不预看目的地 NPC、仍看起点 NPC（`TestMovingPlayerLocationFilter`）
- ✅ NPC wait 不叙事、玩家自己 wait 仍显示（`TestNpcIdleNotNarrated`）
- ✅ 端到端：「去酒馆」叙事从 `'ele静观其变。\n你改变了位置。'` → `'guard b环顾四周。\n你改变了位置。'`（不再预看 tavern 的 ele）
- ✅ 新增 4 个测试；全套 735 passed / 4 skipped

**涉及文件：** `response_generator.py`, `cli.py`, `tests/test_response_generator.py`

---

### P1.5 LLM 过度标注 movement 歧义 + 编造不存在的地点

**状态：** ✅ 已完成（2026-05-31）  
**影响：** 真实 gpt-oss **编造世界里不存在的 location id**（"market"/"blacksmith"/"广场"）并标 ambiguous → 玩家移动时要么看到裸 coherence 报错（`Target 'blacksmith' not found`），要么弹无用的「尝试执行/取消动作」。  
**根因（用捕获的真实 LLM 输出确认）：**
1. **prompt 没给 LLM 合法 location 列表** → 它凭空编造目的地 id
2. `_resolve_movement_location` 在 `to_location` 已合法时早退，没清掉 LLM 仍标的 stale ambiguity
3. movement 目的地无法解析时走的是通用 clarification（尝试执行/取消动作），对玩家无用

**已实现（`intent.py` + prompt）：**
1. **根治**：prompt 模板加 `## Available Locations` + `{locations_list}`，明确"这是唯一合法目的地，禁止编造"。实测后 LLM 改用真实 id（"广场"→`town_square`、"市场"→`town_square`），不再编造
2. `_resolve_movement_location` + `_clear_destination_ambiguity`：`to_location` 合法时也清掉指向它的 stale ambiguity
3. movement 目的地不合法 **或** 仍有残留歧义 → 返回 `movement_destination` clarification，**列出真实可去的地点**（tavern / town_square）而非通用占位

**验收标准：**
- ✅ 合法 to_location 清空 stale ambiguity（`test_valid_to_location_clears_stale_ambiguity`）
- ✅ 不可解析目的地 → 列出真实地点的 clarification（`test_unresolvable_destination_gives_location_list`）
- ✅ prompt 含 location 列表（`test_prompt_includes_location_list`）
- ✅ **真实 Ollama before/after**：去铁匠铺(不存在) 从裸报错 → `「去不了那里。你想去哪个地方？ 1.tavern 2.town_square」`；前往市场/回到广场 不再编造、干净移动
- ✅ 新增 3 个测试；全套 740 passed / 4 skipped

> **备注**：clarification 合并污染（`raw_text:"去酒馆，具体来说是：回到广场"`）的根源是 LLM 过度标歧义；location 列表注入后 LLM 基本不再瞎标，该问题随之大幅缓解。若后续仍有残留，可单独优化 `_resolve_clarification` 的合并策略。

**涉及文件：** `intent.py`, `content/prompts/parse_player_intent/v1.0.0.md`, `tests/test_intent.py`

---

### P1.6 NPC 越位发言（离开房间仍听到身后 NPC 的话）

**状态：** ✅ 已完成（2026-05-31，最终对局诊断发现）  
**影响：** 玩家「回到广场」离开 tavern 的同一 tick，narrative 仍显示身后 ele 的回话（`ele开口道：「有何需求？」`）。world state 正确（ele 始终在 tavern、未移动），纯属**叙事显示的时序问题**。  
**诊断结论（用真实 Ollama 逐 tick 追踪 + 确定性复现）：**
- 排除 NPC 自主移动 / 瞬移（ele 全程 @tavern）
- 根因是 P1.4 的 `viewer_location=移动前位置`：玩家离开时 ele 在起点发言 → 按"事件发生时玩家在场"被显示
- 设计冲突：P1.4「移动看起点」与本需求「移动不回听起点」对**离开 NPC**场景相反 → 用户拍板用唯一无矛盾模型

**已实现（`response_generator.py`）：**
- **移动 tick 规则**：玩家移动的那一 tick，移动中的玩家既不在起点也不在终点 → **不叙事任何其他 actor 的事件**，只叙事玩家自己的移动。NPC 该说的话下一 tick（玩家真正到位后）再听。同时满足 P1.4（不预看目的地）+ P1.6（不回听起点）
- `generate()` 自检 events 里有无玩家自己的 movement，有则抑制其他 actor

**验收标准：**
- ✅ 移动 tick 不显示目的地/起点任何 NPC 反应；非移动 tick 同地 NPC 正常显示（`TestMovingPlayerLocationFilter` 3 个）
- ✅ 真实 Ollama before/after：「回到广场」从 `ele开口道：「有何需求？」` → 仅 `你改变了位置。`（PASS: no off-scene line）
- ✅ 全套 741 passed / 4 skipped

**涉及文件：** `response_generator.py`, `tests/test_response_generator.py`

---

### P1.7 NPC-NPC 调度器在真实玩法里从不触发（familiarity 永远到不了门槛）

**状态：** ✅ 已完成（2026-05-31，"造测试世界"的 de-risk 实验发现）  
**影响：** P1.2 的核心卖点——可控 NPC-NPC 交互（familiarity≥0.3 + cooldown + 5 种交互类型）——在真实玩法里**几乎从不触发**。玩家看到的"NPC 互动"其实全是无门槛的 idle 随机说话。P1.2 测试当时是**手动种子化 familiarity** 才过的，真实世界永远到不了。  
**根因（实验逐 tick 确诊）：**
- familiarity 来源是 `RelationshipCalculator`：数 NPC 的 belief 里"提及对方 id"的条数 × 0.1
- 但 NPC 之间**几乎不形成"提及对方"的 belief**（60 tick 才 1 条 belief，0 条提及另一 NPC——记忆多是关于玩家/事件）
- → familiarity 永远 ≈0.1，卡在 0.3 门槛下

**已实现（`npc_runtime.py`，方向：用户拍板"共处时长累积"）：**
1. `NPCActionGenerator` 维护 `_co_presence`（pair→共处熟悉度）：同处一地每 tick +0.08，分开每 tick −0.05（4 tick 共处即过 0.3）
2. `build_pair_candidates` 每 tick 调 `_update_co_presence`，familiarity 取 `max(belief 派生, 共处累积)`
3. co_presence 纳入 `get_state`/`load_state` 存档
4. belief-based familiarity 仍保留（有关系认知的 NPC 立即算熟）

**验收标准：**
- ✅ 共处累积、分开衰减、存档保留、belief 信号仍生效（`TestCoPresenceFamiliarity` 4 个）
- ✅ 纯共处也能触发调度器（`test_scheduler_fires_from_co_presence`）
- ✅ **before/after 实测**：3 NPC 同处 40 tick，scheduler-driven 交互从 **0 → 16**（含 cooperation/conversation/trade/rumor 四类）
- ✅ 新增 5 个测试；全套 765 passed / 4 skipped

> **诊断教训**：用 `_collect_npc_actions()+tick_advance()` 的简化 harness 会跳过 subjectivity 管线 → NPC 不形成记忆 → `has_sharable_memory` 门槛误判 fix 失效。改用完整 `run_tick` 才看到真实行为。**简化的复现 harness 本身可能引入假象。**

**涉及文件：** `npc_runtime.py`, `tests/test_npc_interaction.py`

---

### 内容：lively_market_town 测试世界（✅ 已完成 2026-05-31）

**目的：** 默认 frontier_town 太小（2 地点 3 NPC、NPC 分散），"世界自主运转"三大功能（NPC-NPC 交互 / 记忆压缩 / 压力事件）从没在真实运行里被触发过。造一个能自然激活它们的内容包来验证。  
**已实现：** `fixtures/content_packs/lively_market_town.json` —— 丰收节前的热闹集市，6 实体（player + 5 NPC）、3 连通地点；**3 个 NPC 同处 market_square/center**（共处累积 familiarity → 触发 P1.2/P1.7 调度器）；敏感的 `festival_bustle` driver（severity 0.4，`entity_count>=5` + `recent_event_count>=8`）。  
**验证：**
- ✅ FakeLLM 40 tick：NPC-NPC 调度交互 17 次、压力事件 7 次、记忆压缩摘要 15 条
- ✅ **真实 Ollama 长跑（10 轮，玩家大半发呆）世界真的"活"了**：NPC 间用真 LLM 对话、**传闻自发传播**（矿山墓穴/北方宝藏的涌现传闻线在 baker_lu→miller_to→ele 之间流转）、压力事件自发触发、商人性格贯穿（一直推销）
- ✅ 新增 `tests/test_lively_market_pack.py`（3 个）；全套 768 passed / 4 skipped

> **真实长跑又暴露 2 个新问题（见 P1.8 / P1.9）。**

**涉及文件：** `fixtures/content_packs/lively_market_town.json`(新), `tests/test_lively_market_pack.py`(新)

---

### 内容：frostgate_watchpost 第二世界（✅ 已完成 2026-05-31）

**目的：** 压测"通用 RPG 世界运行时"定位——换一个主题/结构都截然不同的世界，引擎是否零改动就能跑。  
**已实现：** `fixtures/content_packs/frostgate_watchpost.json` —— 阴郁的雪夜边境哨站，基调 grim/tense（vs 市集的 warm），**派系对立**（守军 watch vs 恶魔难民 refugees），**分层真相 world_book**（恶魔是人=canonical_fact、教会宣传=faction_propaganda 仅 watch 可见、隘口屠杀=forbidden_knowledge 仅 refugees 可见），排外主题的 `scarcity_xenophobia` driver。  
**验证（同一引擎、零改动）：**
- ✅ **核心定位验证——信息不对称按派系生效**：守军队长看到「教会宣传」但看不到「屠杀真相」；难民看到「屠杀真相」但看不到「教会宣传」；canonical fact 双方共享（`test_layered_truth_is_faction_gated`）。这正是设计文档"世界书是约束而非全知，NPC 只看到自己背景允许的版本"的核心
- ✅ FakeLLM 40 tick：NPC-NPC 交互 15 次、压力事件 8 次、记忆压缩 11；NPC 全部守岗（P1.8 在新世界仍生效）
- ✅ **真实 Ollama 基调换皮成功**：守军队长台词疲惫戒备（「我已经尽力了，别再来烦我了」「别多问，别让敌人注意到我们」）、年轻哨兵虔诚惊惶（「请主啊，保佑我不要被敌人发现」）——与市集商人的热情推销截然不同；P1.9（旁观难民不抢话）、压力事件（难民被拒入）均生效
- ✅ 新增 `tests/test_frostgate_pack.py`（4 个）；全套 780 passed / 2 skipped

> **结论：** "后端通用化、支持不同世界/模板/叙事风格"的定位**首次被真实验证**——换内容包，引擎零改动，世界基调、NPC 性格、信息不对称、压力主题全跟着变。

**涉及文件：** `fixtures/content_packs/frostgate_watchpost.json`(新), `tests/test_frostgate_pack.py`(新)

---

### P1.8 NPC 频繁无目的乱逛（idle move 太多）

**状态：** ✅ 已完成（2026-05-31）  
**影响：** 真实长跑里几乎每 tick 都有 NPC `改变了位置`——NPC 像无头苍蝇乱走（商人不守摊、面包师不在店）。导致玩家"去酒馆找 ele"时 ele 早已逛去 granary → "ele不在这儿"。世界显得躁动而非有秩序。  
**根因：** `_generate_for_npc` idle 分支 move 概率偏高（有人在场仍 20%、独处 30%），且 NPC 无"岗位/归属地"概念，`_make_movement` 随机选目的地 → 无差别游走。  

**已实现（方向：home 锚定）：**
1. `EntityState` 加 `home_location` 字段；`campaign_loader` 默认把 NPC 起始 location 设为 home（内容包可用 `home_location` 覆盖）
2. `_generate_for_npc`：有 home 时大幅降低 move 概率（在家时有人在场 4%、独处 8%；离家时升到 50/60% 促其回归）；无 home 保持原行为（向后兼容）
3. `_make_movement`：NPC 离家且 home 直连时**直接回家**，不再随机漂移

**验收标准：**
- ✅ 在家有伴的 NPC 极少乱走（`test_npc_at_home_with_company_rarely_moves` move率<0.10）；离家倾向回家（`test_npc_away_from_home_tends_to_return`）；无 home 行为不变
- ✅ **测试世界实测**：3 个集市 NPC 40 tick 后 **0/3 漂离岗位**（全留在 market_square）；总 move 0.40/tick（多为 ele/miller 出门后回家）
- ✅ 端到端：把 baker_lu 强制挪到 tavern → **下一 tick 即回到 market_square 岗位**
- ✅ 新增 3 个测试；全套 773 passed / 2 skipped

> **顺带缓解 P1.9**：NPC 不再乱跑 → 玩家要找的人就在原地，"去找 X 却扑空"大幅减少。

**涉及文件：** `world.py`, `npc_runtime.py`, `campaign_loader.py`, `tests/test_npc_action_generator.py`

---

### P1.9 多 NPC 同场时对话焦点丢失（旁观 NPC 抢话）

**状态：** ✅ 已完成（2026-05-31）  
**影响：** 玩家"问 baker_lu"，但同 zone 的 merchant_yan / guard_ko 也各自 idle 发言、盖过被点名对象 → narrative 杂乱，玩家的对话焦点被淹没。  
**诊断结论（精确复现）：** 被点名的 baker_lu **其实正确回应了**（in_conversation 路径正常）——问题是**另外 2 个旁观 NPC 同 tick 各自 idle 闲聊**把它埋了。不是"焦点 NPC 没回应"，是"旁观 NPC 太吵"。  

**已实现：**
1. `generate_actions(suppress_idle_speech_at=...)`：玩家对某 NPC 说话时，该 location 的**旁观 NPC 本 tick 不 idle 发言**（改为 look/wait），被点名的 NPC（in_conversation）照常回应
2. `cli._dispatch_player_action`：玩家 speech 且 target 是 NPC 时，传入玩家所在 location 作为 suppress 点
3. `_collect_npc_actions`：该 tick 同时**抑制 NPC-NPC 自主交互**，让玩家的对话独占聚光

**验收标准：**
- ✅ 玩家点名时旁观 NPC 不抢话、别处 NPC 不受影响、无抑制时行为不变（`TestConversationFocus` 3 个）
- ✅ **端到端 before/after**：拥挤集市玩家问 baker_lu → 旁观 merchant/guard 从「你好」「你听说了吗」抢话 → 改为安静 `环顾四周`，只剩 `baker lu开口道：「这事说来话长。」`
- ✅ 新增 3 个测试；全套 776 passed / 2 skipped

**涉及文件：** `npc_runtime.py`, `cli.py`, `tests/test_npc_action_generator.py`

---

## P0 — 阻断核心体验（原有）

### P0.1 对话上下文未传递给 IntentParser（代词解析失败）

**状态：** ✅ 已完成（2026-05-29）  
**影响：** 玩家在对话中说"她/他/你"时，系统反复问"指谁"，完全无视刚建立的对话关系。  
**根因：**
1. `cli.py:_execute_tick()` 调用 `IntentParser.parse()` 时未传入 `context` 参数
2. `IntentParser._try_resolve_ambiguity()` 对代词"她"的处理是纯硬编码（`options=["附近的人", "自己"]`），不读取 `ConversationSession` 数据
3. `intent.py:485-491` 尝试利用 `conversation_session_id` 解析"你"，但 `session_id` 在 intent 解析阶段通常为 `None`

**修复方案：**
1. `cli.py`：调用 `parse()` 前提取当前活跃对话的参与者列表，通过 `context` 传入
2. `intent.py`：
   - `_build_prompt()` 在 prompt 中注入对话上下文（"你正在与 npc.ele 对话"）
   - `_try_resolve_ambiguity()` 优先将代词解析为对话中的另一方
   - `_build_clarification()` 生成选项时优先列出对话参与者
3. `interaction.py`：确保 conversation_session_id 在 intent 阶段即可被推断

**验收标准：**
- 玩家与 ele 对话后说"追问她" → 直接解析为 `target_id="npc.ele"`，不触发 clarification
- FakeLLM fixture 支持带对话上下文的解析
- 新增 `test_clarification.py` 端到端测试：`test_pronoun_resolved_from_conversation_context`

**涉及文件：** `cli.py`, `intent.py`, `interaction.py`  
**预计工作量：** 2-3 小时 + 测试

---

### P0.2 Clarification 选项不智能

**状态：** ✅ 已完成（2026-05-29）  
**影响：** 即使 clarification 不循环了，选项内容（"附近的人"、"自己"）仍然让玩家困惑。  
**根因：** `_build_clarification()` 的选项生成是静态模板，不利用 world state 或对话上下文。

**已实现（`intent.py`）：**
1. 新增 `_same_location_actors()` / `_candidate_person_targets()`：候选人 = 对话另一方 + 同 location 的 actor（去重、顺序稳定，回放安全）
2. 新增 `_auto_resolve_single_candidate()`，在 `parse()` 歧义检查前调用：人称代词且只有一个候选时，直接绑定 `target_id` 并清掉歧义，**不弹 clarification 直接执行**
3. `_build_clarification()` 人称代词分支改为基于真实候选生成选项：1 个→"是指 X 吗？"确认；多个→列出全部 NPC；0 个→诚实提示换说法。不再出现 "附近的人"/"自己" 占位符

**验收标准：**
- ✅ "她/他"的选项优先是同 location 的 NPC + 对话中的另一方
- ✅ 只有一个合理候选时，直接执行（`parse` 自动解析）；直连 `_build_clarification` 时给"是指 X 吗？"单确认
- ✅ 新增 `TestPronounCandidateResolution`（6 个测试）；全套 639 passed / 3 skipped

**涉及文件：** `intent.py`, `tests/test_clarification.py`

---

## P1 — 影响沉浸感

### P1.1 NPC 回复完全模板化，无个性

**状态：** ✅ 已完成（2026-05-29，方向：接入 LLM 生成对话）  
**影响：** NPC 每次回复从固定池随机抽取，没有记忆驱动的差异化反应。  
**根因：**
- `npc_runtime.py` `CHATTER_LINES` 和 `CONVERSATION_RESPONSES` 是硬编码列表
- `_pick_speech_content()` 只读取最 salient 的记忆，不做个性化加工，无 LLM 生成

**已实现：**
1. 新增 `npc_dialogue.py`：`NPCDialogueGenerator` + `NPCDialogue` schema，调用 LLM 生成单句台词
2. 新增 prompt `content/prompts/generate_npc_dialogue/v1.0.0.md`
3. Prompt 只喂 NPC **自身认知**（persona/traits + 自己的记忆 + 可观察的对话上下文），**不含世界真相或他人秘密**（守 A5 无上帝视角）
4. 注入 `NPCActionGenerator(dialogue_generator=...)`，`_pick_speech_content()` 改为 **LLM 优先**；LLM 不可用/无 fixture/校验失败时**降级回原模板**（守 A10 回放确定性 + P5 错误降级）
5. cli 用 Ollama 时 NPC 说真实生成的台词；`--fake`/测试无 fixture 时走确定性模板

**验收标准：**
- ✅ LLM 可用时 NPC 台词由记忆/人设驱动（`test_speech_uses_dialogue_generator`、prompt 落地测试）
- ✅ 无 LLM 时降级模板、固定 seed 回放确定性（`test_falls_back_to_template...`、`test_deterministic_with_same_seed`）
- ✅ **gpt-oss 偶发返回 `{"line":""}` 空台词时，NPC 绝不输出空话**（降级模板）：`test_empty_llm_line_never_yields_empty_speech`
- ✅ **端到端**：`GameSession.run_tick` 全链路 NPC speech 非空（`TestDialogueEndToEnd`，FakeLLM 确定性）
- ✅ **真实 Ollama 集成验证通过**（`TestDialogueRealOllama`，`RPG_OLLAMA_TEST=1` 时运行，实测 19.8s 通过，gpt-oss 真实生成差异化台词如「是的，我会留意。」/「我最近过得不错，谢谢关心。」）
- ✅ 零额外依赖；`tests/test_npc_dialogue.py`（13 个：12 跑 + 1 Ollama 默认 skip）；全套 651 passed / 4 skipped

> **复盘（重要）**：最初误报「NPC 回复为空」是**诊断假象**——调试时用 `tr -cd '[:print:]'` 过滤输出，把中文字符全删了，使非空中文台词显示成空；叠加 gpt-oss 偶发空行。产品代码无 bug，但已据此补强：空台词降级测试 + 端到端测试 + 真实 Ollama 门控测试。

**涉及文件：** `npc_dialogue.py`(新), `npc_runtime.py`, `cli.py`, `content/prompts/generate_npc_dialogue/v1.0.0.md`(新), `tests/test_npc_dialogue.py`(新)

---

### P1.2 NPC-NPC 自主交互未调度

**状态：** ✅ 已完成（2026-05-30，真实 Ollama 验证通过）  
**影响：** 世界只有玩家在行动时才"活"着，NPC 之间不会自主产生交互。  
**根因：** `NPCInteractionScheduler`（`npc_runtime.py`）有完整实现，但 `cli.py:_collect_npc_actions()` 从未调用它。

**已实现：**
1. `npc_runtime.py`：`NPCActionGenerator` 新增
   - `build_pair_candidates()`：同 location 的 NPC 两两配对（排除 busy/对话中 NPC，按 id 排序保证确定性）
   - `pair_familiarity()`：从双方 belief 经 `RelationshipCalculator` 推导熟悉度（取双向最大值）
   - `generate_interaction_actions()`：调度 + 把 `InteractionSeed` 转成 speech Action（复用记忆/LLM 对话内容，带 `interaction_type`）
   - `generate_actions(exclude_ids=...)`：已交互的 NPC 不再生成 idle action（避免一 tick 双动作）
2. `NPCInteractionScheduler.schedule(max_seeds=...)`：每 tick 上限，且在评估前截断，避免未触发的 pair 误记 cooldown
3. `cli.py:_collect_npc_actions()`：先调度 NPC-NPC 交互，再为剩余 NPC 生成 idle，合并返回
4. 持久化：`interaction_scheduler` 的 cooldown/seq 纳入 save/load

**设计说明：** 初始内容包只有 2 个 NPC、分处不同 location、无 NPC↔NPC 关系，所以这是**涌现型**功能——需 NPC 同处一地、互相形成 belief（熟悉度≥0.3）、且持有可分享记忆、cooldown 到期后才触发。

**验收标准：**
- ✅ 满足条件时 NPC 间产生 speech Event（真实 Ollama 25 tick 跑出 11 次交互，含 conversation/cooperation/rumor/trade）
- ✅ 一个 NPC 一 tick 绝不行动两次（`test_scheduler_wired_and_no_double_acting` + 真实链路 `double_acting=false`）
- ✅ 每 tick 最多 1 个交互；seed 固定回放确定性（`test_deterministic_with_same_seed`）
- ✅ cooldown 存档往返保留（`test_scheduler_cooldown_survives_save_load`）
- ✅ 新增 `tests/test_npc_interaction.py`（16 个测试）；全套 667 passed / 4 skipped

**涉及文件：** `cli.py`, `npc_runtime.py`, `tests/test_npc_interaction.py`(新)

---

## P2 — 架构债务

### P2.1 stamina 存储位置不一致

**状态：** ✅ 已完成（2026-05-30）  
**影响：** `max_stamina` 是 `EntityState` 顶层字段，`stamina` 值却存储在 `attributes` dict 中。`CombatEngine` 初始化时从 `ent.attributes.get("stamina", 70)` 读取，且 `max_stamina` 硬编码 100。

**已实现：**
1. `world.py` `EntityState` 新增 `stamina: int = 100` 顶层字段；`_update_state_from_action()` 改为读写 `entity.stamina`
2. `combat.py` `start_combat()` 从 `ent.stamina` / `ent.max_stamina` 初始化（不再硬编码）
3. `campaign_loader.py` + `cli.py`（CSV import）设置顶层 `stamina`，并把遗留的 `attributes["stamina"]` 迁移过去（`attrs.pop`）
4. `persistence.py` 无需改动：`dataclasses.asdict` / `EntityState(**edata)` 自动覆盖新字段
5. `rules.py` 无需改动：其 `stamina_delta` 只是 ResolutionResult 元数据，从不落盘（实际消耗只在 world.py）

**验收标准：**
- ✅ 所有 stamina 读写走 `entity.stamina`；attributes dict 中不再存放 stamina（`test_loader_sets_top_level_not_attributes`）
- ✅ physical 消耗、存档往返、战斗读写一致（新增 `tests/test_stamina_storage.py` 8 个测试）
- ✅ 全套 675 passed / 4 skipped

**涉及文件：** `world.py`, `combat.py`, `campaign_loader.py`, `cli.py`, `tests/test_stamina_storage.py`(新)

---

### P2.2 Combat stamina 与世界 state 不同步

**状态：** ✅ 已完成（2026-05-30，随 P2.1 一并解决）  
**影响：** 玩家先做 physical 动作消耗 stamina，然后进入 combat，combat 引擎读取的是旧的 stamina 值。  
**根因：** `combat.py` 恢复 stamina 但只更新 `CombatParticipant`，不写回 `world.state.entities`。  
**已实现：**
1. `start_combat()` 改读顶层 `ent.stamina`（P2.1）→ 进入战斗前的 physical 消耗能被正确读取
2. `resolve_tick()` 末尾把所有参与者 `p.stamina` 同步回 `world.state.entities[*].stamina`

**验收标准：**
- ✅ 非 combat physical 动作消耗的 stamina，combat 引擎能正确读取（`test_prior_physical_drain_visible_in_combat` + 端到端 sneak−15→85 验证）
- ✅ combat tick 后 stamina 变化写回 world state（`test_combat_syncs_stamina_back_to_world`）

**涉及文件：** `combat.py`

---

### P2.3 Pacing Policy 未实际应用

**状态：** ✅ 已完成（2026-05-30）  
**影响：** `TickScheduler` 存在完整的 PacingPolicy 实现（slow/fast/pause/force），但 `cli.py` 直接调用 `world.tick_advance()`（仅 `tick += 1`），完全忽略 pacing。  

**已实现（`cli.py`）：**
1. `GameSession.__init__` 实例化 `TickScheduler` + `_latest_campaign_pressure` 字段
2. `_build_pacing_context()`：从会话状态推导 pacing flags（对话/战斗/安全区/无待处理事件/压力）
3. `_advance_tick_with_pacing()`：按 pacing 结果推进世界 PAUSE/SLOW=最小、FAST=2、FORCE=3
4. `_execute_tick()` 末尾用 pacing-aware 推进替换裸 `tick_advance()`
5. campaign 压力事件触发时把 `_latest_campaign_pressure=0.9` 喂给 FORCE 规则

**关键设计决策：** 玩家驱动的一 tick **始终推进 ≥1**（`player_driven=True` 时 `max(1,steps)`）——PAUSE 只抑制额外的自主推进，不冻结玩家自己的回合（否则游戏卡死）。combat/arbiter 单步路径保留裸 `tick_advance()`，scheduler.tick 每次推进后镜像 world tick 防漂移。

**快进策略（用户拍板）：** 普通/空输入**始终只推进 1 tick**（可预测、玩家掌控）；FAST/FORCE 快进只在显式 `/skip`、`/wait N` 时生效——`_advance_tick_with_pacing(allow_fast_forward=...)` 控制。SLOW/PAUSE（对话/战斗 hold 到 1 步）不受影响。新增命令：
- `/wait [n]`：刻意流逝 n 个 idle tick（默认 1，上限 12）
- `/skip`：在安全空旷时快进（按 FAST/FORCE），一旦有 NPC/战斗/压力出现即停，安全上限 12 tick

**PAUSE 语义（用户拍板）：** 玩家自己的回合始终推进 ≥1，PAUSE 只抑制额外的自主世界推进（不冻结玩家回合，避免卡死）。「重大事件后真暂停+确认」属于另一套 UI 交互，不在本任务范围。

**验收标准：**
- ✅ 对话中 tick = 1（slow）；战斗中 = slow（`TestPacingAffectsAdvance` + 端到端）
- ✅ 普通/空输入 = 恰好 1 tick（`TestNormalTickNoFastForward` + 端到端实测）
- ✅ `/skip` 安全区快进、有人时拒绝；`/wait N` 推进 N（端到端实测 skip=12、wait4=4、有 NPC 时拒绝）
- ✅ 压力超临界 → force（3 步，allow_fast_forward 时）
- ✅ 新增 `tests/test_pacing_integration.py`（16 个测试）；全套 698 passed / 4 skipped

**涉及文件：** `cli.py`（含 `/skip` `/wait` + help）, `tests/test_pacing_integration.py`(新), `tests/test_cli.py`

---

## P3 — 长期规划

### P3.1 LLM Budget 机制

**状态：** ✅ 已完成（2026-05-30，提前处理——被 P1.1/P1.2 放大成回归）  
**影响：** budget 永不重置 + retry 重复计数，导致约 2-3 tick 后 LLM 调用全部失败，NPC 对话永久降级、玩家意图解析失败。  
**设计文档：** §6.7.2

**深挖发现 4 个问题，已修前 3 个：**
1. ✅ **reset 缺失**（致命回归）：`run_tick` 入口未调 `reset_tick_budget()`，`_calls_this_tick` 只增不减 → 已在 `cli.py:run_tick` 开头重置
2. ✅ **retry 重复计数**：原 `_call_with_retries` 每次重试 +1、fallback 再 +1，一次逻辑调用最多吃 4 点预算 → 改为「一次 `call()` = 一个预算单位」，retry/fallback 不再计数
3. ✅ **无优先级降级**：原「先到先得」会让 NPC 对话吃光玩家 parse 的预算 → 按设计 §6.7.2 `TASK_PRIORITY_ORDER` 实现：高优先级（arbiter/parse）始终放行，低优先级（generate_npc_dialogue）超额才拒绝、降级到模板
4. ⏸️ **budget 状态未存档**：schema 有 `llm_budget_state` 但 cli 未接——**有意不做**，budget 是 per-tick 的，存档恢复本就应是新 tick 满额

**验收标准：**
- ✅ 一次逻辑调用只耗 1 预算，retry 不放大（`test_retries_do_not_drain_extra_budget`）
- ✅ 超预算时 parse 仍放行、npc_dialogue 被拒（`TestPriorityDegradation`）
- ✅ `run_tick` 每 tick 重置（`test_run_tick_resets_budget`）
- ✅ **真实 Ollama 25 tick：11/11 交互全程 LLM 生成、0 降级**（修复前 tick 16 后退化到记忆原文）；单 tick 实际用量峰值仅 2（远低于上限 10）
- ✅ 新增 `tests/test_llm_budget.py`（7 个测试）；全套 682 passed / 4 skipped

**涉及文件：** `llm.py`, `cli.py`, `tests/test_llm_budget.py`(新)

### P3.2 State Validator 完整性/因果校验

**状态：** ✅ 已完成（2026-05-31）  
**影响：** arbiter 的 state changes 不检查矛盾状态（如同一物品在两个位置）。  

**背景：** `StateValidator` 已接入（arbiter 调用），原本只覆盖设计 §6.4.2 校验清单 6 项中的 4 项（字段存在性/数值边界/权限/Schema）。本任务补齐缺失的**状态一致性**和**因果检查**两项。

**已实现（`validator.py`）：**
1. `_validate_consistency(changes)`：跨变更矛盾检测 → 拒绝整个输出
   - 同一字段被设成两个不同绝对值（如 location_id 同时设 tavern 和 market）
   - 同一物品加入两个不同 inventory（`*.inventory.add` 同 item 不同 owner）
   - hp 被设到超过该实体 max_hp
2. `_validate_causality(change, evidence_refs)`：主观变更（field 含 belief/memory/suspicion/trust/affection）若无 evidence_refs → 拒绝（不能凭空生成 Memory/Belief）
3. `arbiter.py:_build_validator_context()` 补充 `entities` dict（hp/max_hp/stamina），供一致性检查读取

**验收标准：**
- ✅ 矛盾绝对值/物品双重归属/hp 超上限 → 拒绝（`TestStateConsistency`）
- ✅ 无证据的 belief/memory 变更 → 拒绝；有证据或普通 attribute 变更 → 通过（`TestCausality`）
- ✅ 数值 delta 累加、相同绝对值重复设置不误判（`test_numeric_deltas_to_same_field_allowed` 等）
- ✅ 端到端实测 4 场景全部正确
- ✅ 新增 `tests/test_validator_consistency.py`（10 个测试）；全套 716 passed / 4 skipped

**涉及文件：** `validator.py`, `arbiter.py`, `tests/test_validator_consistency.py`(新)
**设计文档：** §6.4.2  
**预计工作量：** 3-4 小时

### P3.3 Memory 压缩自动触发

**状态：** ✅ 已完成（2026-05-31）  
**影响：** `MemoryCompressor` 存在但 cli.py tick 流程不调用，working memory 无限增长。  

**已实现（`cli.py`）：**
1. `GameSession.__init__` 实例化 `MemoryCompressor(self.memory_store)`
2. `_compress_memories_for_all(current_tick)`：遍历每个记忆 owner，先 working→short 再 short→long；`maybe_compress_*` 返回 summary 时 **add summary + remove 原始条目**（compressor 只生成摘要、由 caller 负责增删——见 memory.py:162 注释）
3. `_execute_tick()` 在 tick 推进后调用，保证用最新 tick 判断 age 阈值

**设计说明：** compressor 的 `_seq`（摘要 id 计数器）不入存档——memory_store 本身已持久化，重启后新摘要 id 不会与已存在的数据冲突。阈值沿用 `MemoryCompressor`：working>10 且最旧>10 tick → 压缩；short>20 → 压缩。

**验收标准：**
- ✅ working 超阈值 → 压成 1 条 short-term 摘要、原始条目移除、摘要 `compression_of` 回链（`TestAutoCompactWorking`）
- ✅ short 超阈值 → 压成 long-term（`TestAutoCompactShort`）
- ✅ 未达阈值/记忆太新不触发（`test_not_triggered_when_recent` / `test_under_threshold_untouched`）
- ✅ `run_tick` 驱动压缩；长跑 working memory 有界（端到端实测：40 tick 后 working=4 而非 40）
- ✅ 新增 `tests/test_memory_compaction.py`（8 个测试）；全套 706 passed / 4 skipped

**涉及文件：** `cli.py`, `tests/test_memory_compaction.py`(新)

---

## 黄金路径测试场景（验收标准）

以下场景必须在每次重大改动后通过 FakeLLM 模式验证：

```
[Setup] 加载 frontier_town content pack

Step 1: 玩家输入 "去酒馆"
  → 玩家 location 变为 tavern
  → Event: movement
  → NPC ele 也在 tavern

Step 2: 玩家输入 "和ele说话"
  → ConversationSession 创建（player_001 + npc.ele）
  → Event: speech
  → NPC ele 回复（基于模板或上下文）

Step 3: 玩家输入 "追问她最近怎么样"
  → IntentParser 利用对话上下文解析"她"为 npc.ele
  → 不触发 clarification
  → Event: speech

Step 4: 玩家输入 "看看周围"
  → Event: physical/look
  → stamina 无消耗或少量消耗
  → Observation 分发给同 location NPC

Step 5: 玩家输入 "/history"
  → 显示最近 5 个 Event，包含对话记录

[Verify] 所有 Event 的 actor_id、location_id 正确
[Verify] NPC memory 中写入了玩家的 speech Event
[Verify] 无 clarification 无限循环
```

---

## 当前进行中的任务

| 任务 | 状态 | 负责人 |
|---|---|---|
| P0.1 对话上下文传递 | ✅ 已完成 | — |
| P0.2 Clarification 智能选项 | ✅ 已完成 | — |
| P1.1 NPC 回复个性化（LLM 生成） | ✅ 已完成（真实 Ollama 验证通过） | — |
| P1.2 NPC-NPC 自主交互调度 | ✅ 已完成（真实 Ollama 验证通过） | — |
| P2.1 stamina 存储统一 | ✅ 已完成 | — |
| P2.2 Combat stamina 同步 | ✅ 已完成（随 P2.1） | — |
| P3.1 LLM Budget 机制 | ✅ 已完成（reset+计数+优先级降级，真实 Ollama 验证） | — |
| P2.3 Pacing Policy 应用 | ✅ 已完成 | — |
| P3.3 Memory 压缩自动触发 | ✅ 已完成 | — |
| P3.2 State Validator 完整性校验 | ✅ 已完成 | — |

---

*最后更新：2026-05-31*  
*测试基准：840 passed, 2 skipped*
