# TUI/CLI 增强 — 待办清单

> 目标：在**零额外依赖**（只用 ANSI escape codes）前提下，把 CLI 从"能用"提升到"好用"。
> 明确边界：**不引入 curses/rich、不做全屏 TUI、不做鼠标交互**。

---

## Phase A：核心体验（必做，直接影响玩家感知）

- [x] **A-1 `/map` — ASCII 世界地图**
  - 基于 `LocationState.connections` 生成拓扑图
  - 标记：★ 玩家位置、● 已访问、○ 未访问
  - 显示连接方向（北/南/东/西）和距离（adjacent/near/far）
  - 关联文件：`cli.py` + `formatter.py` + 测试

- [x] **A-2 `/talk` 对话模式 + 附近 NPC 列表**
  - `/talk <npc>` 后进入"对话模式"，REPL prompt 变为 `[talking to npc_001] >`
  - 显示最近 5-7 轮对话轮次（说话者 + 内容 + tick）
  - `/talk` 不带参数时，自动列出附近可对话 NPC（含亲密度/态度暗示）
  - 关联文件：`cli.py` + `conversation.py`（读取 session turns）

- [x] **A-3 `/who` — 附近 NPC 雷达**
  - 列出同 location 的所有 NPC
  - 显示：距离（同 zone / 同 location 不同 zone）、可见状态、态度（基于 RelationshipSnapshot）
  - 与 `/look` 的区别：`/look` 是环境描述，`/who` 是社交雷达

- [x] **A-4 空输入智能提示**
  - 玩家输入空行时，不直接 wait，而是提示："附近似乎有动静… 输入 /look 查看，或直接回车等待。"
  - 或者：空输入连续 3 次后自动触发 `/look`

---

## Phase B：开发调试（✅ 已完成 2026-05-31）

- [x] **B-1 `/inject <action_json>` — 绕过 Intent Parser 直接执行**
  - `/inject {"verb":"look"}`、`/inject {"action_type":"speech","content":"..."}`、`/inject {"verb":"attack","target":"npc.guard_b"}`
  - 构造 Action → 复用 `_dispatch_player_action()`（与正常 tick 同一管线：NPC 反应、Event Log、叙事、tick 推进、arbiter 路由）
  - 实现：`cli.py:_handle_inject` + 抽出共享的 `_dispatch_player_action`

- [x] **B-2 `/log filter <...>` — 过滤 Event Log**
  - `/log filter combat`（按 event_type）、`/log filter actor=player_001`、`/log filter tick=3`
  - 实现：`cli.py:_handle_log`

- [x] **B-3 `/time [skip <n>]` — 时间控制**
  - `/time` 显示当前 tick；`/time skip <n>` 快进 n tick（玩家 idle，只推进世界）
  - 实现：`cli.py:_handle_time`（与 P2.3 的 `/wait`/`/skip` 互补：/wait 走完整 idle tick，/time skip 纯推进世界时间）

**测试**：新增 `tests/test_debug_phase_b.py`（11 个）；全套 752 passed / 4 skipped。
**重构红利**：抽出 `_dispatch_player_action()` 让 `/inject` 和正常 tick 共享完全相同的世界管线，零行为差异（全套零回归）。

---

## Phase C：Polish（✅ 已完成 2026-05-31）

- [x] **C-1 战斗模式状态栏变色**
  - `formatter.status_bar`：`in_combat` 时边框用 `ANSI.RED`（平时 `ANSI.DIM`），叠加已有的 `⚔ COMBAT` 徽标
  - 测试：`test_status_bar_border_red_in_combat` / `..._not_red_when_calm`

- [x] **C-2 命令 Tab 补全**
  - `cli._setup_readline()`：用 stdlib `readline` 注册 completer，`/sa<Tab>`→`/save`；兼容 macOS libedit 和 GNU readline 的 Tab 绑定
  - 测试：`TestReadlineSetup`（completer 行为 + 命令清单一致性）

- [x] **C-3 输入历史（上下箭头）**
  - 随 `readline` 导入自动启用——每条提交的输入进历史，↑↓ 可翻阅。无额外代码

- [x] **C-4 响应式窄终端**
  - `status_bar(width=...)` 框线长度可变；`cli._status_bar` 用 `shutil.get_terminal_size` 探测列数并 clamp 到 [20,60]
  - 测试：`test_status_bar_respects_width` / `..._default_width_60` / `..._narrow_width_still_has_core_info`

**测试**：C-1/C-4 在 `tests/test_formatter.py`、C-2/C-3 在 `tests/test_debug_phase_b.py`（共 8 个）；全套 760 passed / 4 skipped。
**零额外依赖**：仅用 ANSI + stdlib `readline`/`shutil`；`readline` 不可用时（如裸 Windows）REPL 优雅降级。

---

## 不做（明确边界）

1. ❌ **不引入 curses / rich / blessed** — 保持零额外依赖
2. ❌ **不做全屏 TUI** — 如 ncurses 窗口分割、面板滚动等，超出当前 CLI 架构
3. ❌ **不做鼠标交互** — 纯键盘 REPL
4. ❌ **不做图形/图片** — ASCII 艺术是上限

---

## 推荐执行顺序

```
A-1 /map        → 30 min  → 玩家空间感知质的飞跃
A-2 /talk 历史   → 30 min  → 对话体验闭环
A-3 /who        → 20 min  → 社交信息一目了然
A-4 空输入提示   → 15 min  → 降低新手门槛
─────────────────────────────────────────────
B-1 /inject     → 30 min  → 开发调试神器
B-2 /log filter → 20 min  → 排查事件流转
B-3 /time       → 15 min  → 测试 campaign 节奏
─────────────────────────────────────────────
C-1~C-4        → 有余力再做
```

**预计 Phase A 总用时：~2 小时，新增 15-20 个测试。**
