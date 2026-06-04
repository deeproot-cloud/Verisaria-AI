# TUI 设计

> v0.3(2026-06,经原型 v1 八图评审后**冻结**)。基于
> `ui_prototypes/v1/`(s1–s8)+ 协议层([protocol-design.md](protocol-design.md))。
> 上游约束见 [ui-architecture-direction.md](ui-architecture-direction.md)。

## 0. 一句话

全屏 **Textual** TUI,作为 `frontends/tui/` 下的前端,**只经 `EngineSession` 协议**驱动引擎:
发 `Command` → 订阅 `Event` 流渲染事件日志 → 拉 `WorldSnapshot` 渲染分栏。引擎保持零依赖,
**Textual 是 TUI 前端自己的可选依赖**。结构化 DTO / A5 边界 / JSON 可序列化(架构文档说的"核心工作")
=== 协议层,已建成。TUI ≈ 把 snapshot/events 面板化 + 解决延迟体感。

## 1. 面板清单:核心常驻 + 次要可折叠(布局已由 s1 验证)

| 面板 | 归类 | 数据来源(协议) | 现状 |
|---|---|---|---|
| 顶部状态条 | 核心常驻 | `snapshot.player`(hp/stamina)+ tick + location + pacing;**时段/天气/派系 = TUI 写死占位(灰显 `*`)** | ✅/占位 |
| 中间 事件流 / 叙事(**心脏**) | 核心常驻 | **Event 流**:PlayerSpoke/NpcSpoke/PlayerMoved/Narration/PressureEvent/WorldVarChanged/StanceConfirmed/RelationshipShifted + SpeechToken 打字机流式 | ✅ |
| 右上 附近 NPC + 关系 | 核心常驻 | `snapshot.present` + `snapshot.relationships`(value+band+phrase) | ✅ |
| 右下 世界状态 | 核心常驻(小) | `snapshot.world_vars` | ✅ |
| 输入行 + 状态(领会中…) | 核心常驻 | Command 入口 + Progress 事件 | ✅ |
| 左 地图(拓扑) | 可折叠 | `snapshot.map`(locations+connections,**无坐标**) | ✅ |
| 「处境 / 焦点」面板 | 可折叠 | 见 §3 | ✅ 两面齐(处境/焦点 + DEBUG) |
| 窄屏响应式折叠 | 收尾 | `on_resize` 断点 | ✅ |
| 调试行 / 命令栏 | 可折叠/开关 | debug;Textual command palette | ✅ |

布局示意(= s1):
```
┌ Verisaria ─ ♥HP 100/100  ⚡体力 100  Tick 9  位置 门楼  节奏 normal  ·时段* ·天气* ┐
├──────────────┬────────────────────────────┬────────────────────────────────────┤
│ 地图 [-]      │ 事件流 [-]                  │ 附近 NPC [-]                        │
│  ★门楼        │ [8] 你: 你信教会那套吗？     │ captain_brann  对你戒心很重          │
│   ├─兵营      │ [8] 队长布兰: 我守的是这道门 │   怀疑▓▓▓▓░ (展开看全6维)            │
│   └─难民营    │ [8] (压力) 难民被拒门外      │ sentry_voss   有些畏惧你             │
│              │ [9] 你 → 难民营            ├────────────────────────────────────┤
│ 处境/焦点 [-] │ …(可滚动)                  │ 世界状态:  难民入营 ✗               │
├──────────────┴────────────────────────────┴────────────────────────────────────┤
│ > 对队长布兰说：你信教会那套吗？                                    (领会中…⠋)  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## 2. 延迟体感(最关键工程点;s2 已示意)

一拍真实 LLM 13–28s,`submit()` 同步阻塞 + 末尾才返回 events ⇒ 直接用会**冻死 UI**。必须:
1. **Textual worker 线程跑 tick**,UI 主线程不阻塞。
2. **事件实时推送**:把 `game._event_sink` 接成"事件发生即 `post_message`→App"(`call_from_thread`),
   让 `Progress`(转圈)、`NpcSpoke` **逐条即时**出现;newest 行带 `[本期]` 高亮(s2)。
3. tick 结束后**拉一次 `snapshot()`** 刷新分栏。
4. (later)接 `SpeechToken` → 打字机。
> 可能给 `EngineSession` 加 `submit_streaming(cmd, on_event)` 变体,与 collect-and-return 并存。

## 3. 「处境 / 焦点」面板 —— 同槽两面(已锁定;s5 示意 DEBUG 面)

- **默认 = 玩家视角**,上下文敏感:
  - 闲逛/探索 → **场景框架**:地点描述 + pack `central_tension` + 当前压力(world_vars/驱动)。
  - 聚焦某 NPC(对话中)→ **你的目标**(agenda)+(v3)**「你对该 NPC 的了解」**——
    数据源是**玩家实际看过的事件/对话历史**(前端本就持有),非 NPC 私有记忆 ⇒ A5 by construction。
- **DEBUG 开关 = 上帝视角**:该 NPC 真实所知(`WorldBookFilter.filter_for_entity`),品红"出戏"标注,正式对局关闭;
  锁条目显示为 `🔒 …(派系不可见)`(s5)。
- **同槽两面**:默认"你所知" ↔ DEBUG"它真知道",对照晒信息不对称。
- 具体显示哪些字段/文案 = **实现时细化**(§7-#2)。

## 4. 已锁定的视觉/交互规范(由 v1 八图验证)

**配色图例(全图统一):**
| 语义 | 颜色 |
|---|---|
| 玩家说话 / 当前焦点 | 琥珀 amber |
| NPC 说话 / 常规叙事 | 羊皮纸灰 parchment |
| 压力 / 紧张 / 危险 | 血红 blood-red |
| 正向变化 / 世界事实翻转 | 淡绿 pale-green |
| 次要 / 环境 / 占位 | 暗灰 dim-grey |
| DEBUG / 上帝视角(出戏) | 品红 magenta |

**折叠交互**:每个次要面板头带 `[-]/[+]` 切换;**窄终端自动**把次要面板缩成**左缘竖向 tab**(s6:`▸地图 ▸焦点`),只保核心列。
**关系栏**:默认显**主导 1–2 维条 + 定性短语**("对你戒心很重");展开看全 6 维(协议已给 band/phrase + verbosity)。
**后果内联(s4,提级到 v2)**:`WorldVarChanged`(`✗→✓` 淡绿)、`RelationshipShifted`(`怀疑+0.2` 红 / `信任+0.3` 绿)、`StanceConfirmed`(`◆ 已确认目标` 横幅)**直接内联进事件流**——这是"选择有后果"的灵魂展示。
**地图**:拓扑节点图(盒子+连线),边标 `adjacent=相邻 / near=附近`,节点态 `★当前 ●已访问 ○未访问`,**无坐标层**(那是像素 GUI 的事)。
**状态条占位**:时段/天气/派系灰显 `*`,后续接引擎(日夜/天气是值得加进引擎的活世界特性,但不阻塞 TUI)。
**澄清态**(s8):底部输入区展开成 `1) 尝试执行  2) 取消动作  或补充说明`,上方灰显原输入。

## 5. 增量构建计划(每步可跑/可测)

- ✅ **v1 — 能玩的核心回路**:Textual 骨架 + 事件流面板 + 输入行 + **worker 线程跑 tick + 实时事件推送** + 状态条。
  达成:打字 → 事件即时流出 → tick 推进,不冻屏。
- ✅ **v2 — 右栏 + 后果内联**:附近 NPC + 关系条(主导维+短语)+ 世界状态;`RelationshipShifted` 事件后果内联进事件流(s4)。
- ✅ **v3 — 左栏 + 焦点 + 打磨**:地图(`snapshot.map`)+ 处境/焦点面板 +「你对该 NPC 的了解」+ `SpeechToken` 打字机 + DEBUG 上帝视角(Ctrl+G)+ 折叠/响应式收尾 + Footer/Ctrl+Q。
  **v3 全部落地**(见 commits d474f11…0c0f928);键位用 Ctrl+ 系(单字母会被常驻输入框吞掉)。
- ✅ **v4 — 动态世界模型上屏 + 事件流过滤 + 活世界**:世界状态面板区分 pack 事实 / `· 涌现前置`(GM 涌现前置)、办理中进程读 `⏳ 办理中（≈N）`;事件流 **Ctrl+F 三态过滤**(全部/对话/后果,system 行恒显)+ **PgUp/PgDn 键盘滚动**(输入框常驻焦点下仍可翻阅历史);`NpcMoved` 带显示名,**护送 + 自主进出场**都已上事件流(玩家驻足时,NPC 走进/走出本地点 → 以 `viewer_location` 为 A5 锚发 `NpcMoved`;玩家自己移动的一拍则抑制,交由下一帧「附近」重绘)。

测试:Textual `App.run_test()` / `Pilot` 驱动按键 + 断言面板;数据映射(snapshot→面板模型)普通单测。

### 仍延后(不阻塞 v3)
- 顶部状态条「时段/天气/派系」仍是灰显 `*` 占位(无引擎数据源)。
- 双语:UI 串现为中文写死,远期再做(见记忆 `language-i18n-direction`)。

## 6. 依赖与落位

- 新增 `frontends/tui/`(Textual app);`pyproject.toml` 加 `tui` extra(`textual`),引擎主体仍零依赖。
- 入口:`verisaria tui <pack> --llm minimax`(或 `python -m verisaria.frontends.tui`)。

## 7. 留到"实现时细化"的点(原型阶段不卡死)

1. ✅ **事件流滚动 + 可选过滤**(v4):Ctrl+F 在 全部/对话/后果 三态间循环(保留全量历史,切换即重渲;system 行恒显),PgUp/PgDn 键盘滚动。按演员细分留待实战需要时再加。
2. **焦点面板的具体字段/文案**——默认起点:场景框架取 pack `central_tension` + 当前压力;聚焦 NPC 时取 agenda 目标。具体迭代。
3. **最小终端尺寸阈值**——低于某列数强制折叠到 s6 形态;响应式断点编码时调。

## 8. 原型参考

`docs/design/ui_prototypes/v1/`:s1 标准态 · s2 等待/流式 · s3 难民营换景 · s4 后果时刻 ·
s5 上帝视角(信息不对称)· s6 窄屏折叠 · s7 地图展开 · s8 澄清态。
(旧原型 `ui_prototypes/*.png` 为初版,以 v1/ 与本文为准。)
