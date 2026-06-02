"""The lively_market_town content pack is designed to actually trigger the
'world comes alive' features: NPC-NPC interaction (co-located NPCs), memory
compaction (busy location), and campaign pressure (sensitive driver)."""

from __future__ import annotations

from verisaria.engine.campaign_loader import CampaignLoader
from verisaria.engine.schemas import MemoryLayer

PACK = "fixtures/content_packs/lively_market_town.json"


def test_pack_loads_valid():
    pack, ws, val = CampaignLoader.load_or_fallback(PACK)
    assert val.valid, [i.message for i in val.issues]
    # Three NPCs co-located in the market square center → co-presence familiarity.
    center = [e for e in ws.entities.values()
              if e.location_id == "market_square" and e.zone_id == "center"
              and e.entity_type == "npc"]
    assert len(center) >= 3
    # A campaign driver with a low severity to fire in a busy world.
    assert pack.campaign_drivers
    assert pack.campaign_drivers[0]["severity"] <= 0.5


def test_npc_npc_interactions_fire(tmp_path):
    from verisaria.runtime.session import GameSession
    s = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
    sched_driven = 0
    for _ in range(30):
        for a in s._collect_npc_actions():
            if (a.action_type.value == "speech" and a.target_id
                    and a.target_id.startswith("npc.")
                    and a.params.get("interaction_type")):
                sched_driven += 1
        s.run_tick("")
    assert sched_driven > 0, "co-located NPCs should trigger scheduler interactions"


def test_memory_compaction_happens(tmp_path):
    from verisaria.runtime.session import GameSession
    s = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
    for _ in range(40):
        s.run_tick("")
    # At least one busy NPC should have produced a short-term summary.
    any_compacted = any(
        s.memory_store.count(eid, MemoryLayer.SHORT_TERM) > 0
        for eid in s.world.state.entities
        if eid.startswith("npc.")
    )
    assert any_compacted
