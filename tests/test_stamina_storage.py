"""Tests for P2.1 / P2.2: stamina lives on EntityState.stamina (top-level), is
never stored in attributes, and stays consistent across physical actions,
combat, and save/load.
"""

from __future__ import annotations

from verisaria.engine.combat import CombatEngine, PendingCombatAction
from verisaria.engine.schemas import Action, ActionType
from verisaria.engine.world import EntityState, WorldCore, WorldState


def _actor(stamina: int = 100, **kw) -> EntityState:
    return EntityState(
        entity_id="player_001",
        entity_type="player",
        location_id="town_square",
        attributes={"dexterity": 0.7, "perception": 0.6, **kw.pop("attributes", {})},
        stamina=stamina,
        **kw,
    )


# --------------------------------------------------------------------------- #
# EntityState field
# --------------------------------------------------------------------------- #

class TestEntityStateStamina:
    def test_has_top_level_stamina_default(self):
        e = EntityState(entity_id="e", entity_type="npc", location_id="loc")
        assert e.stamina == 100
        assert "stamina" not in e.attributes


# --------------------------------------------------------------------------- #
# Physical action consumption (world.py)
# --------------------------------------------------------------------------- #

class TestPhysicalStaminaConsumption:
    def _world(self, stamina: int) -> WorldCore:
        ws = WorldState()
        ws.entities["player_001"] = _actor(stamina=stamina)
        return WorldCore(initial_state=ws)

    def _steal(self) -> Action:
        return Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.PHYSICAL,
            params={"verb": "steal", "target": "short_sword"},
            tick=1,
        )

    def test_steal_consumes_top_level_stamina(self):
        world = self._world(stamina=100)
        world.commit_action(self._steal())
        ent = world.state.get_entity("player_001")
        assert ent.stamina == 90  # steal costs 10
        assert "stamina" not in ent.attributes  # never written to attributes

    def test_stamina_floored_at_zero(self):
        world = self._world(stamina=5)
        world.commit_action(self._steal())
        assert world.state.get_entity("player_001").stamina == 0


# --------------------------------------------------------------------------- #
# Combat reads/writes top-level stamina (combat.py)
# --------------------------------------------------------------------------- #

class TestCombatStaminaSync:
    def _world(self, p_sta: int, b_sta: int) -> WorldCore:
        ws = WorldState()
        ws.entities["player_001"] = EntityState(
            entity_id="player_001", entity_type="player", location_id="sq",
            attributes={"strength": 0.8, "dexterity": 0.6}, stamina=p_sta,
            hp=100, max_hp=100,
        )
        ws.entities["npc.bandit"] = EntityState(
            entity_id="npc.bandit", entity_type="npc", location_id="sq",
            attributes={"strength": 0.5, "dexterity": 0.4}, stamina=b_sta,
            hp=80, max_hp=80,
        )
        return WorldCore(initial_state=ws)

    def test_combat_init_reads_top_level_stamina(self):
        world = self._world(p_sta=42, b_sta=60)
        engine = CombatEngine(seed=1)
        engine.start_combat("player_001", ["npc.bandit"], world.state, tick=1)
        p = engine.get_active_combat("player_001").participants["player_001"]
        assert p.stamina == 42  # not the old hardcoded 70

    def test_prior_physical_drain_visible_in_combat(self):
        """P2.2: stamina spent on a physical action before combat must carry in."""
        world = self._world(p_sta=100, b_sta=60)
        world.commit_action(Action(
            action_id="a1", actor_id="player_001", action_type=ActionType.PHYSICAL,
            params={"verb": "sneak"}, tick=1,
        ))  # sneak costs 15
        assert world.state.get_entity("player_001").stamina == 85

        engine = CombatEngine(seed=1)
        engine.start_combat("player_001", ["npc.bandit"], world.state, tick=2)
        p = engine.get_active_combat("player_001").participants["player_001"]
        assert p.stamina == 85

    def test_combat_syncs_stamina_back_to_world(self):
        """P2.2: after a combat tick, world entity stamina reflects combat usage."""
        world = self._world(p_sta=100, b_sta=100)
        engine = CombatEngine(seed=1)
        session = engine.start_combat("player_001", ["npc.bandit"], world.state, tick=1)
        engine.resolve_tick(session.combat_id, world, tick=2)
        p = engine.get_active_combat("player_001").participants["player_001"]
        ent = world.state.get_entity("player_001")
        assert ent.stamina == p.stamina  # world mirrors participant


# --------------------------------------------------------------------------- #
# Loader / persistence
# --------------------------------------------------------------------------- #

class TestLoaderStamina:
    def test_loader_sets_top_level_not_attributes(self):
        from verisaria.engine.campaign_loader import CampaignLoader

        _pack, world_state, _validation = CampaignLoader.load_or_fallback(
            "fixtures/content_packs/valid_frontier_town.json"
        )
        for ent in world_state.entities.values():
            assert "stamina" not in ent.attributes, f"{ent.entity_id} still has attr stamina"
            assert isinstance(ent.stamina, (int, float))

    def test_stamina_survives_save_load(self, tmp_path):
        from verisaria.engine.persistence import PersistenceLayer

        ws = WorldState()
        ws.entities["player_001"] = _actor(stamina=37)
        world = WorldCore(initial_state=ws)

        layer = PersistenceLayer(str(tmp_path))
        save = layer.save(world_core=world, tick=0, content_pack_id="x", save_type="manual")
        restored = layer.load(save.save_id)
        from verisaria.engine.persistence import PersistenceLayer as PL
        rebuilt = PL(str(tmp_path)).restore_world_core(restored)
        assert rebuilt.state.get_entity("player_001").stamina == 37
