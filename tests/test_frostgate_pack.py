"""frostgate_watchpost: a second, tonally-opposite content pack proving the
engine is a generic world runtime — including faction-based layered-truth
access (the core 'NPC sees only its allowed version' design)."""

from __future__ import annotations

from verisaria.engine.campaign_loader import CampaignLoader
from verisaria.engine.world_book_filter import WorldBookFilter
from verisaria.engine.schemas import MemoryLayer

PACK = "fixtures/content_packs/frostgate_watchpost.json"


def test_pack_loads_valid():
    pack, ws, val = CampaignLoader.load_or_fallback(PACK)
    assert val.valid, [i.message for i in val.issues]
    # Two factions present.
    factions = {e.attributes.get("faction") for e in ws.entities.values()
                if e.entity_type == "npc"}
    assert "watch" in factions and "refugees" in factions


def test_layered_truth_is_faction_gated():
    """The watch sees church propaganda but NOT the massacre; refugees see the
    forbidden massacre truth but NOT the propaganda. Both share canonical fact."""
    pack, ws, _ = CampaignLoader.load_or_fallback(PACK)
    entries = pack.world_book

    def seen(nid):
        return {e.entry_id for e in
                WorldBookFilter.filter_for_entity(entries, ws.get_entity(nid))}

    watch = seen("npc.captain_brann")
    refugee = seen("npc.refugee_kaze")
    assert "church_demon_propaganda" in watch
    assert "massacre_at_the_pass" not in watch
    assert "massacre_at_the_pass" in refugee
    assert "church_demon_propaganda" not in refugee
    # Shared canonical fact reaches everyone.
    assert "demons_are_real_people" in watch and "demons_are_real_people" in refugee


def test_world_comes_alive(tmp_path):
    from verisaria.runtime.session import GameSession
    s = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
    npc_int = 0
    for _ in range(30):
        for a in s._collect_npc_actions():
            if (a.action_type.value == "speech" and a.target_id
                    and a.target_id.startswith("npc.")
                    and a.params.get("interaction_type")):
                npc_int += 1
        s.run_tick("")
    assert npc_int > 0  # NPC-NPC interactions fire in this world too


def test_npcs_stay_at_post(tmp_path):
    from verisaria.runtime.session import GameSession
    s = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
    for _ in range(40):
        s.run_tick("")
    # Each NPC anchored to its starting post (P1.8) — none should drift off.
    for nid in ("npc.captain_brann", "npc.quartermaster_hale", "npc.refugee_kaze"):
        e = s.world.state.get_entity(nid)
        assert e.location_id == e.home_location
