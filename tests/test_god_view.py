"""DEBUG god-view (out-of-band, A5-crossing) — what an NPC REALLY knows.

The frostgate pack sets up information asymmetry by faction: the 20-years-ago
massacre is forbidden_knowledge the refugees carry but the watch had erased;
the church propaganda is hidden_from refugees. The god-view must surface BOTH
the NPC's accessible entries and the 🔒 locked ones, so a playtester can see why
two NPCs answer the same question differently.
"""
from __future__ import annotations

from verisaria.protocol.engine_session import EngineSession

PACK = "fixtures/content_packs/frostgate_watchpost.json"


def test_god_view_locks_what_the_watch_cannot_know(tmp_path):
    es = EngineSession.start(PACK, save_dir=str(tmp_path), llm_backend="fake")
    voss = es.debug_god_view("npc.sentry_voss")
    assert voss is not None and voss.name == "哨兵伏斯"

    massacre = [k for k in voss.knowledge if "屠杀" in k.content]
    assert massacre and massacre[0].locked          # the watch had it erased → 🔒
    prop = [k for k in voss.knowledge if "圣焰教会" in k.content]
    assert prop and not prop[0].locked              # but the doctrine is theirs
    assert isinstance(voss.relationship, list)       # full stance dims (may be empty)


def test_god_view_information_asymmetry_for_refugees(tmp_path):
    es = EngineSession.start(PACK, save_dir=str(tmp_path), llm_backend="fake")
    kaze = es.debug_god_view("npc.refugee_kaze")
    assert kaze is not None

    massacre = [k for k in kaze.knowledge if "屠杀" in k.content]
    assert massacre and not massacre[0].locked      # refugees carry the memory
    prop = [k for k in kaze.knowledge if "圣焰教会" in k.content]
    assert prop and prop[0].locked                  # but church doctrine is hidden


def test_god_view_none_for_unknown_entity(tmp_path):
    es = EngineSession.start(PACK, save_dir=str(tmp_path), llm_backend="fake")
    assert es.debug_god_view("npc.nobody") is None
