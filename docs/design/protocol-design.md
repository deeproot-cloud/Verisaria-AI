# 引擎 ↔ 前端 协议层设计草案

> 草案 v0.1。这是"接口先行"的契约层(Step 2)。落地前先对齐设计。
> 上游方向见 [ui-architecture-direction.md](ui-architecture-direction.md)。

## 0. 目标与原则

1. **协议 = A5 授权边界**。前端**只能**拿到"玩家可感知"的信息;NPC 私有记忆、隐藏世界书、上帝视角状态**绝不跨界**。协议层就是这道闸。
2. **可序列化**。每个 DTO 都是纯数据(dataclass/pydantic),能转 JSON。Python 前端(TUI/CLI)直接拿类型对象;非 Python(Godot)拿同一份 JSON 走 IPC。
3. **引擎吐结构、前端定表现**。引擎产出**结构化事件**;CLI 渲染成文字、TUI 渲染成分栏、Godot 渲染成精灵。**预格式化字符串不跨界**——唯一例外是 `Narration.text`(引擎撰写的叙事散文,那是内容不是排版)。
4. **三族 DTO**:`Command`(前端→引擎)、`Event`(引擎→前端,事件流)、`Snapshot`(引擎→前端,可查询状态,供分栏渲染)。
5. **推 + 拉**。`Event` 是推流(这一拍发生了什么);`Snapshot` 是拉取(当前状态,渲染面板用)。一拍 = 提交 `Command` → 收到一串有序 `Event` + 更新后的 `Snapshot`。

## 1. 现状映射(协议不是凭空造,是把已有东西显式化)

| 现在(隐式) | 协议里(显式) |
|---|---|
| `run_tick() -> str`(预格式化叙事) | 一串 `Event` + `WorldSnapshot` |
| `_progress_sink("正在领会…")` | `Event.Progress` |
| `_stream_sink(token)` / `_streamed_npc` | `Event.SpeechToken` / `Event.NpcSpoke` |
| 内部 `events: list[Event]`(speech/movement/…) | `Event.NpcSpoke` / `PlayerMoved` / … |
| Channel A 评价 | `Event.RelationshipShifted` |
| Channel B stance | `Event.StanceConfirmed` |
| Channel C 世界变更 | `Event.WorldVarChanged` |
| 压力事件 | `Event.PressureEvent` |
| 澄清流程 | `Event.ClarificationNeeded` + `Command.SubmitInput` |
| `/who /world /relationship /agenda /map /status`(只读) | **`Snapshot` 查询**(非事件) |
| `/save /load /wait /skip /talk /inject`(改状态) | **`Command`** |
| `/belief /memory /inspect`(上帝视角) | **debug 通道**,默认关,不属玩家协议 |

## 2. Command(前端 → 引擎)

```
SubmitInput(text: str)         # 自然语言输入(含澄清回应)——驱动一拍
Wait()                         # 空过一拍
Skip(ticks: int = 1)          # 快进
Save(label: str | None)
Load(save_id: str)
# 只读视图不在这里——它们是 Snapshot 查询(见 §4)
```

澄清的"选项 1 / /cancel"也是 `SubmitInput`——引擎的澄清状态机已处理。

## 3. Event(引擎 → 前端,本拍事件流)

每个 Event 带 `tick: int` + 顺序。前端按序渲染进日志/面板。

```
# — 对话 —
Progress(message)                      # "正在领会你的意思…"
SpeechToken(npc_id, token)             # 流式逐字(可选,TUI/CLI 实时渲染)
NpcSpoke(npc_id, name, line)           # NPC 完整一句
PlayerSpoke(line)
# — 世界 —
PlayerMoved(from_loc, to_loc)
NpcMoved(npc_id, from_loc, to_loc)     # 仅当玩家可见(A5)
Narration(text)                        # 引擎撰写的叙事散文(唯一跨界的成品文本)
PressureEvent(event_type, source, summary)
# — 后果三通道 —
RelationshipShifted(npc_id, dimension, value)   # 他人对你的看法变化(已是玩家可见)
StanceConfirmed(topic_id, label)
WorldVarChanged(var_id, label, value)
# — 流程 —
ClarificationNeeded(question, options)
TickAdvanced(tick)
CombatEvent(...)                       # 战斗细分(沿用现有 combat 事件)
Error(message)
```

## 4. Snapshot(引擎 → 前端,拉取渲染面板)

`WorldSnapshot`——**只含玩家可感知**(A5 闸在这里):

```
tick: int
pacing: str
location: { id, description }
present: [ { id, name, type } ]                 # 同地点实体(= /who)
player: { hp, stamina, traits, ... }            # = /status
relationships: [ { npc_id, name, dims: {dim: value} } ]   # 他人对你看法(= /relationship)
world_vars: [ { var_id, label, value } ]        # 可变世界事实(= /world)
agenda: { drives: [...], confirmed_stances: [...] }       # = /agenda
map: { locations: [...], connections: [...] }   # = /map
```

由协议层从引擎的**结构化存储**(relationship_store / world_vars / agenda_service / world.state)拉取——和现有 formatter 用的是同一份数据,只是返回 DTO 而非字符串。

## 5. 门面 API

```python
class EngineSession:                       # 协议级门面,包住 GameSession
    def submit(self, cmd: Command) -> TickResult        # = list[Event] + WorldSnapshot
    def snapshot(self) -> WorldSnapshot
    # (Godot 侧:同样的 DTO 走 stdio JSON-lines / websocket)
```

内部调 `GameSession`,但**收集 Event** 而非格式化成字符串。

## 6. A5 在边界的强制

- Snapshot/Event **只**含:同地点实体、他人对玩家的看法、公开世界事实、玩家自己的议程、玩家可见的移动/对话。
- **绝不**含:NPC 私有记忆、隐藏世界书条目、上帝视角全量状态。
- `/belief /memory /inspect` 这类是**调试通道**,与玩家协议分离,真实游玩默认关闭。

## 7. 迁移策略(非破坏、增量)

1. **加 DTO + `event_sink`**:`GameSession` 在 `run_tick` 内**额外**把结构化 Event 推给一个可选 `event_sink`(现有 `_stream_sink`/`_progress_sink` 合并进来),**同时**保留现有 `-> str` 返回。CLI 不动照常跑。
2. **`EngineSession` 门面**:收集 Event + 暴露 `snapshot()`。TUI 走这条新路。
3. **CLI 迁移**:让 CLI 也消费 Event(自己格式化),然后去掉 `run_tick` 的字符串返回与 `repl()`/`_handle_command` 里的表现逻辑——它们本就属前端,迁到 `frontends/cli`。
4. 每步测试全绿;`repl()`/命令处理自然从 `GameSession` 被拉到前端,`GameSession` 瘦成纯引擎 API。

## 8. 决议(已拍板 2026-06)

1. **关系渲染 = 玩家全局设置,不是引擎决定**(印证原则 #3:引擎吐结构、前端定表现)。
   - 协议**同时携带**:`value: float`(裸值)+ `band: str`(档位)+ `phrase: str`(定性,"戒心很重")。
   - 前端按玩家的 **verbosity 设置**渲染:
     - `simple` → `怀疑 0.51`(裸值)
     - `normal` → `队长对你戒心很重`(定性)
     - `verbose` → `[怀疑 0.51] 队长对你戒心很重`(两者)
   - `(dimension, value) → (band, phrase)` 的阈值映射放在**协议层**(确定性),所有前端(含 Godot)共享,避免各自重造。
2. **SpeechToken 流式事件:纳入,可选订阅。** ✅
3. **只读命令全归 Snapshot 查询;Command 只留会改状态的。** ✅
4. **debug 通道(belief/memory/inspect)先搁置,标记为非协议。** ✅
5. **Narration 为唯一跨界的成品散文(LLM 撰写,不拆碎)。** ✅
