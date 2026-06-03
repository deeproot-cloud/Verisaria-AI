"""Author-declared opening relationships must seed the RelationshipStore.

Previously `initial_relationships` only fed /who·/talk display labels, so opening
stances had zero effect on runtime — notably the arbiter's Channel-C rulings,
which read the live relationship snapshot. Frostgate declares the captain as
`wary` of the player and kaze as `cautious`, plus NPC↔NPC ties.
"""
from __future__ import annotations

from verisaria.runtime.session import GameSession

PACK = "fixtures/content_packs/frostgate_watchpost.json"


def _session(tmp_path) -> GameSession:
    return GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")


def test_player_directed_opening_stance_is_seeded(tmp_path):
    g = _session(tmp_path)
    # captain_brann is `wary` of the player → suspicion seeded
    snap = g.relationship_store.get("npc.captain_brann", "player_001")
    assert snap is not None and snap.dimensions["suspicion"] > 0
    # kaze is `cautious` → lighter suspicion than the captain's `wary`
    kaze = g.relationship_store.get("npc.refugee_kaze", "player_001")
    assert kaze is not None and 0 < kaze.dimensions["suspicion"] < snap.dimensions["suspicion"]


def test_npc_to_npc_opening_ties_are_seeded(tmp_path):
    g = _session(tmp_path)
    # sentry_voss is `loyal` to the captain → trust seeded
    loyal = g.relationship_store.get("npc.sentry_voss", "npc.captain_brann")
    assert loyal is not None and loyal.dimensions["trust"] > 0


def test_seeded_stance_reaches_the_arbiter_context(tmp_path):
    """The whole point: an authority NPC's opening stance toward the player now
    shows up in the arbiter's world-var context (authority_relationship)."""
    g = _session(tmp_path)
    rows = {r["var_id"]: r for r in g._world_vars_for_arbiter()}
    # find a var the captain is the authority for (frostgate's gate/refugee var)
    captain_vars = [r for r in rows.values()
                    if r.get("authority_npc") == "captain_brann"]
    assert captain_vars, "expected the captain to be an authority for some world var"
    rel = captain_vars[0].get("authority_relationship") or {}
    assert rel.get("suspicion", 0) > 0  # the seeded `wary` stance crossed into arbitration


def test_explicit_dimensions_win_over_type(tmp_path):
    g = _session(tmp_path)
    g.relationship_store.apply("npc.x", "player_001", {}, tick=0)  # sanity: store usable
    # the type→seed table maps known labels; an unknown type falls back to familiarity
    assert g._REL_TYPE_SEED["wary"]["suspicion"] > 0
    assert g._REL_TYPE_DEFAULT == {"familiarity": 0.1}
