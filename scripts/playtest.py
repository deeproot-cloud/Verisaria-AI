"""Hard playtest: stress target-switching, A5 info-asymmetry (layered truth),
movement, ambiguous/compound/edge intents, sustained persuasion. Real MiniMax,
real intent parsing. Writes playtest_out.txt with rich probes + anomaly flags.

Run from anywhere:  python scripts/playtest.py  (cloud key still needs sourcing,
e.g. `set -a; . .env; set +a` first)."""
from __future__ import annotations
import os
import sys
import time

# This script lives in scripts/; anchor everything to the repo root so the
# fixture paths / output file resolve regardless of the current directory.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from verisaria.runtime.session import GameSession
from verisaria.engine.schemas import ParsedIntent, ActionType

OUT = open("playtest_out.txt", "w", buffering=1, encoding="utf-8")
def log(s: str = "", end: str = "\n") -> None:
    OUT.write(s + end); OUT.flush()

PACK = "fixtures/content_packs/frostgate_watchpost.json"
PLAYER = "player_001"
s = GameSession(PACK, save_dir="_playtest_saves", llm_backend="minimax")

orig_parse = s.intent_parser.parse
def parse_logged(raw_text, **kw):
    r = orig_parse(raw_text, **kw)
    if isinstance(r, ParsedIntent):
        flag = ""
        # anomaly: a "说/问/告诉" sentence classified as non-speech
        if any(w in raw_text for w in ("说", "问", "告诉", "请", "求")) and r.intent_type != ActionType.SPEECH:
            flag = "  ⚠️非SPEECH?"
        log(f"   ⟦解析⟧ type={r.intent_type.value} ref={r.target_ref!r}->id={r.target_id} content={(r.content or '')[:34]!r}{flag}")
    else:
        log(f"   ⟦解析⟧ 澄清: {getattr(r, 'clarifying_question', '')[:55]}")
    return r
s.intent_parser.parse = parse_logged

s._stream_sink = lambda x: (OUT.write(x), OUT.flush())
s._progress_sink = lambda m: log(m)

def here():
    p = s.world.state.get_entity(PLAYER)
    loc = p.location_id if p else "?"
    npcs = [e.entity_id.replace("npc.", "") for e in s.world.state.entities.values()
            if e.entity_type == "npc" and e.location_id == loc]
    return loc, npcs

INPUTS = [
    "/who",
    # --- 多 NPC 切换 / 目标解析 ---
    "对哨兵伏斯说：你最怕的到底是什么？",
    "转过身问队长布兰：你真的相信教会说的那套吗？",
    "再问军需官海尔：照这粮草，咱们还能撑几天？",
    # --- A5 信息不对称（核心卖点）---
    "问伏斯：二十年前隘口屠杀求和使节的事，你听说过吗？",   # watch 不该知道屠杀
    "我去难民营看看。",                                        # 移动到 refugee_camp
    "对难民卡泽说：二十年前隘口，到底发生了什么？",            # 难民该知道屠杀
    # --- 模糊 / 复合 / 边界 ---
    "跟旁边的人打听打听消息。",                                # 模糊目标
    "我回门楼去。",                                            # 移动回 gatehouse
    "告诉布兰：难民里有医者和铁匠，能帮哨站过冬，我愿以性命担保。",  # 复合论据
    "搜一下四周有没有可疑的人。",                              # 物理动作→arbiter?
    "跟那个不存在的传令兵说句话。",                            # 不存在的 NPC
    # --- 持续说服 C 闭环 ---
    "/relationship",
    "队长，我以性命和荣誉担保，求你开城门、接纳这些难民吧！",  # C 请求（强）
    "/world",
    "/agenda",
]

log("=== HARD PLAYTEST ===\n")
for inp in INPUTS:
    log(f"\n>>> {inp}")
    if inp.startswith("/"):
        log(s._handle_command(inp) or "")
        continue
    loc0, npcs0 = here()
    log(f"   [pre] player@{loc0} 在场NPC={npcs0}")
    t0 = time.time()
    try:
        out = s.run_tick(inp)
        if out and out.strip():
            log(out)
    except Exception as exc:
        import traceback
        log(f"   !! ERROR: {exc}\n{traceback.format_exc()[:300]}")
    loc1, _ = here()
    moved = f"  → player MOVED {loc0}->{loc1}" if loc1 != loc0 else ""
    log(f"   [post]{moved}  [{time.time()-t0:.0f}s]")

log("\n=== DONE ===")
OUT.close()
