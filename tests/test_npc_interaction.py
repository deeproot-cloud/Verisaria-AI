"""Tests for P1.2: wiring NPCInteractionScheduler into the tick loop.

NPC-NPC autonomous interaction is an *emergent* feature: two NPCs interact only
when they share a location, have grown familiar (belief-derived), one of them
holds a sharable memory, and the cooldown has elapsed. These tests pin down the
candidate-building, seed→action conversion, the "replace idle action" rule, the
max-one-per-tick cap, determinism, and the GameSession integration.
"""

from __future__ import annotations

from verisaria.engine.memory import BeliefEngine, MemoryStore
from verisaria.engine.npc_runtime import (
    NPCActionGenerator,
    NPCInteractionScheduler,
    NPCPairCandidate,
)
from verisaria.engine.schemas import (
    ActionType,
    Belief,
    Conviction,
    Memory,
    MemoryLayer,
)
from verisaria.engine.world import EntityState, LocationState, WorldState


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

def _world(npc_locs: dict[str, str]) -> WorldState:
    ws = WorldState()
    ws.entities["player_001"] = EntityState(
        entity_id="player_001", entity_type="player", location_id="town_square"
    )
    for nid, loc in npc_locs.items():
        ws.entities[nid] = EntityState(entity_id=nid, entity_type="npc", location_id=loc)
        ws.locations.setdefault(loc, LocationState(location_id=loc))
    return ws


def _familiarity_beliefs(owner: str, target: str, n: int = 5) -> list[Belief]:
    """n beliefs mentioning `target` → familiarity = min(1, n*0.1) in calc."""
    return [
        Belief(
            belief_id=f"bel_{owner}_{target}_{i}",
            owner_id=owner,
            claim=f"我和 {target} 在镇上常打交道（{i}）",
            confidence=1.0,
            conviction=Conviction.MEDIUM,
            formed_at_tick=0,
            last_updated_tick=0,
        )
        for i in range(n)
    ]


def _belief_engine_with(pairs: dict[str, list[Belief]]) -> BeliefEngine:
    be = BeliefEngine()
    for owner, beliefs in pairs.items():
        be._beliefs[owner] = list(beliefs)
    return be


def _salient_memory(owner: str) -> Memory:
    return Memory(
        memory_id=f"mem_{owner}",
        owner_id=owner,
        source_observation_id="obs_1",
        tick=0,
        content="听说有人偷了铁匠的短剑",
        salience=0.8,
        decay_rate=0.05,
        layer=MemoryLayer.WORKING,
        topic_tags=["theft"],
    )


def _store_with(owners: list[str]) -> MemoryStore:
    store = MemoryStore()
    for o in owners:
        store.add(o, _salient_memory(o))
    return store


def _familiar_setup():
    """Two co-located NPCs, mutually familiar, each holding a sharable memory."""
    world = _world({"npc.guard_b": "town_square", "npc.ele": "town_square"})
    be = _belief_engine_with({
        "npc.guard_b": _familiarity_beliefs("npc.guard_b", "npc.ele"),
        "npc.ele": _familiarity_beliefs("npc.ele", "npc.guard_b"),
    })
    store = _store_with(["npc.guard_b", "npc.ele"])
    return world, be, store


def _find_interaction_seed_value(builder_kwargs) -> int:
    """Return a scheduler seed that yields >=1 interaction, scanning 0..199."""
    for s in range(200):
        gen = NPCActionGenerator(seed=42)
        sched = NPCInteractionScheduler(seed=s)
        actions = gen.generate_interaction_actions(scheduler=sched, **builder_kwargs)
        if actions:
            return s
    raise AssertionError("no scheduler seed produced an interaction in 200 tries")


# --------------------------------------------------------------------------- #
# schedule(max_seeds=...)
# --------------------------------------------------------------------------- #

class TestScheduleMaxSeeds:
    def _two_qualifying(self):
        return [
            NPCPairCandidate("npc.a", "npc.b", "sq", "sq", 0.5, True),
            NPCPairCandidate("npc.c", "npc.d", "sq", "sq", 0.6, True),
        ]

    def test_max_seeds_caps_output(self):
        sched = NPCInteractionScheduler(seed=1)
        seeds = sched.schedule(self._two_qualifying(), tick=1, max_seeds=1)
        assert len(seeds) <= 1

    def test_cooldown_only_for_produced_seeds(self):
        sched = NPCInteractionScheduler(seed=1)
        seeds = sched.schedule(self._two_qualifying(), tick=1, max_seeds=1)
        # A pair that was never evaluated (because we stopped early) must not
        # have a cooldown recorded against it.
        assert len(sched._last_interaction) == len(seeds)

    def test_default_unlimited_is_backward_compatible(self):
        sched = NPCInteractionScheduler(seed=1)
        seeds = sched.schedule(self._two_qualifying(), tick=1)
        assert isinstance(seeds, list)


# --------------------------------------------------------------------------- #
# Candidate building
# --------------------------------------------------------------------------- #

class TestBuildPairCandidates:
    def test_same_location_only(self):
        gen = NPCActionGenerator(seed=42)
        world = _world({"npc.guard_b": "town_square", "npc.ele": "tavern"})
        cands = gen.build_pair_candidates(world)
        assert cands == []  # different locations → no pair

    def test_pairs_co_located_npcs(self):
        gen = NPCActionGenerator(seed=42)
        world, be, store = _familiar_setup()
        cands = gen.build_pair_candidates(world, belief_engine=be, memory_store=store)
        assert len(cands) == 1
        c = cands[0]
        assert {c.npc_a, c.npc_b} == {"npc.guard_b", "npc.ele"}
        assert c.location_a == c.location_b == "town_square"
        assert c.has_sharable_memory is True
        assert c.familiarity >= 0.3

    def test_excludes_busy_ids(self):
        gen = NPCActionGenerator(seed=42)
        world, be, store = _familiar_setup()
        cands = gen.build_pair_candidates(
            world, belief_engine=be, memory_store=store, busy_ids={"npc.ele"}
        )
        assert cands == []  # ele is talking to the player → not pulled away

    def test_no_player_in_candidates(self):
        gen = NPCActionGenerator(seed=42)
        world, be, store = _familiar_setup()
        cands = gen.build_pair_candidates(world, belief_engine=be, memory_store=store)
        ids = {c.npc_a for c in cands} | {c.npc_b for c in cands}
        assert "player_001" not in ids


class TestPairFamiliarity:
    def test_familiarity_from_beliefs(self):
        be = _belief_engine_with({
            "npc.guard_b": _familiarity_beliefs("npc.guard_b", "npc.ele", n=5),
            "npc.ele": [],
        })
        fam = NPCActionGenerator.pair_familiarity("npc.guard_b", "npc.ele", be)
        assert fam == 0.5  # 5 beliefs * conf 1.0 * 0.1

    def test_zero_without_belief_engine(self):
        assert NPCActionGenerator.pair_familiarity("a", "b", None) == 0.0


# --------------------------------------------------------------------------- #
# Seed → Action conversion
# --------------------------------------------------------------------------- #

class TestGenerateInteractionActions:
    def _kwargs(self):
        world, be, store = _familiar_setup()
        return {
            "world": world,
            "tick": 5,
            "belief_engine": be,
            "memory_store": store,
            "max_interactions": 1,
        }

    def test_produces_speech_between_two_npcs(self):
        kwargs = self._kwargs()
        seed = _find_interaction_seed_value(kwargs)
        gen = NPCActionGenerator(seed=42)
        sched = NPCInteractionScheduler(seed=seed)
        actions = gen.generate_interaction_actions(scheduler=sched, **kwargs)
        assert len(actions) == 1
        a = actions[0]
        assert a.action_type == ActionType.SPEECH
        assert a.actor_id in {"npc.guard_b", "npc.ele"}
        assert a.target_id in {"npc.guard_b", "npc.ele"}
        assert a.actor_id != a.target_id
        assert a.params.get("content")  # non-empty utterance
        assert a.params.get("interaction_type") in NPCInteractionScheduler.INTERACTION_TYPES

    def test_max_interactions_capped_at_one(self):
        # Four co-located, mutually familiar NPCs → still at most 1 per tick.
        world = _world({n: "town_square" for n in ("npc.a", "npc.b", "npc.c", "npc.d")})
        be = _belief_engine_with({
            "npc.a": _familiarity_beliefs("npc.a", "npc.b"),
            "npc.b": _familiarity_beliefs("npc.b", "npc.a"),
            "npc.c": _familiarity_beliefs("npc.c", "npc.d"),
            "npc.d": _familiarity_beliefs("npc.d", "npc.c"),
        })
        store = _store_with(["npc.a", "npc.b", "npc.c", "npc.d"])
        produced_counts = []
        for s in range(30):
            gen = NPCActionGenerator(seed=42)
            sched = NPCInteractionScheduler(seed=s)
            actions = gen.generate_interaction_actions(
                scheduler=sched, world=world, tick=1,
                belief_engine=be, memory_store=store, max_interactions=1,
            )
            produced_counts.append(len(actions))
        assert max(produced_counts) <= 1

    def test_no_interaction_without_sharable_memory(self):
        world, be, _ = _familiar_setup()
        empty_store = MemoryStore()
        for s in range(30):
            gen = NPCActionGenerator(seed=42)
            sched = NPCInteractionScheduler(seed=s)
            actions = gen.generate_interaction_actions(
                scheduler=sched, world=world, tick=1,
                belief_engine=be, memory_store=empty_store, max_interactions=1,
            )
            assert actions == []

    def test_deterministic_with_same_seed(self):
        kwargs = self._kwargs()
        seed = _find_interaction_seed_value(kwargs)

        def run():
            gen = NPCActionGenerator(seed=42)
            sched = NPCInteractionScheduler(seed=seed)
            return [
                (a.actor_id, a.target_id, a.params.get("content"))
                for a in gen.generate_interaction_actions(scheduler=sched, **kwargs)
            ]

        assert run() == run()


# --------------------------------------------------------------------------- #
# generate_actions(exclude_ids=...)
# --------------------------------------------------------------------------- #

class TestGenerateActionsExclude:
    def test_excluded_npc_gets_no_idle_action(self):
        gen = NPCActionGenerator(seed=42)
        world = _world({"npc.guard_b": "town_square", "npc.ele": "town_square"})
        actions = gen.generate_actions(world, tick=1, exclude_ids={"npc.ele"})
        actors = {a.actor_id for a in actions}
        assert "npc.ele" not in actors
        assert "npc.guard_b" in actors


# --------------------------------------------------------------------------- #
# GameSession integration
# --------------------------------------------------------------------------- #

class TestInteractionInGameSession:
    def _session(self, tmp_path):
        from verisaria.runtime.session import GameSession
        return GameSession(
            "fixtures/content_packs/valid_frontier_town.json",
            save_dir=str(tmp_path),
            llm_backend="fake",
        )

    def test_scheduler_wired_and_no_double_acting(self, tmp_path):
        session = self._session(tmp_path)
        assert hasattr(session, "npc_interaction_scheduler")

        # Co-locate the two NPCs, make them familiar, give sharable memories.
        w = session.world.state
        for nid in ("npc.guard_b", "npc.ele"):
            w.entities[nid].location_id = "town_square"
        session.belief_engine._beliefs["npc.guard_b"] = _familiarity_beliefs(
            "npc.guard_b", "npc.ele"
        )
        session.belief_engine._beliefs["npc.ele"] = _familiarity_beliefs(
            "npc.ele", "npc.guard_b"
        )
        session.memory_store.add("npc.guard_b", _salient_memory("npc.guard_b"))
        session.memory_store.add("npc.ele", _salient_memory("npc.ele"))

        # Drive several ticks; an NPC must never appear twice in one batch.
        saw_interaction = False
        for _ in range(20):
            actions = session._collect_npc_actions()
            actors = [a.actor_id for a in actions]
            assert len(actors) == len(set(actors)), f"double-acting NPC: {actors}"
            if any(a.target_id and a.target_id.startswith("npc.") for a in actions):
                saw_interaction = True
            session.world.tick_advance()
        assert saw_interaction, "expected at least one NPC-NPC interaction over 20 ticks"

    def test_scheduler_cooldown_survives_save_load(self, tmp_path):
        session = self._session(tmp_path)
        # Seed a cooldown entry, then save.
        session.npc_interaction_scheduler._last_interaction["npc.ele|npc.guard_b"] = 7
        session.npc_interaction_scheduler._seq = 3
        session._handle_command("/save")

        saves = session.persistence.list_saves()
        assert saves
        latest = sorted(saves, key=lambda s: s["save_id"])[-1]["save_id"]

        fresh = self._session(tmp_path)
        fresh._handle_command(f"/load {latest}")
        restored = fresh.npc_interaction_scheduler.get_state()
        assert restored["last_interactions"].get("npc.ele|npc.guard_b") == 7
        assert restored["seq"] == 3


# --------------------------------------------------------------------------- #
# Co-presence familiarity (P1.7): NPCs grow familiar by sharing a location over
# time, even with no beliefs about each other — so the scheduler can actually
# fire in normal play (belief-based familiarity flatlines near zero).
# --------------------------------------------------------------------------- #

class TestCoPresenceFamiliarity:
    def test_familiarity_accumulates_with_co_presence(self):
        gen = NPCActionGenerator(seed=42)
        world = _world({"npc.a": "sq", "npc.b": "sq"})
        store = _store_with(["npc.a", "npc.b"])

        fams = []
        for _ in range(5):  # 5 co-located ticks
            cands = gen.build_pair_candidates(world, belief_engine=None, memory_store=store)
            assert cands, "expected one co-located pair"
            fams.append(cands[0].familiarity)

        # Monotonic non-decreasing, and crosses the scheduler threshold (0.3).
        assert fams == sorted(fams)
        assert fams[-1] >= NPCInteractionScheduler.FAMILIARITY_THRESHOLD
        assert fams[0] < fams[-1]

    def test_co_presence_resets_when_apart(self):
        gen = NPCActionGenerator(seed=42)
        store = _store_with(["npc.a", "npc.b"])
        together = _world({"npc.a": "sq", "npc.b": "sq"})
        for _ in range(4):
            gen.build_pair_candidates(together, belief_engine=None, memory_store=store)
        high = gen.build_pair_candidates(together, belief_engine=None, memory_store=store)[0].familiarity

        # Move them apart for several ticks; co-presence should decay/reset.
        apart = _world({"npc.a": "sq", "npc.b": "far"})
        for _ in range(6):
            gen.build_pair_candidates(apart, belief_engine=None, memory_store=store)

        # Back together — familiarity should have dropped from its peak.
        again = gen.build_pair_candidates(together, belief_engine=None, memory_store=store)[0].familiarity
        assert again < high

    def test_belief_familiarity_still_counts(self):
        # An NPC with rich beliefs about another is familiar immediately, even
        # without co-presence history (max of the two signals).
        gen = NPCActionGenerator(seed=42)
        world = _world({"npc.guard_b": "town_square", "npc.ele": "town_square"})
        be = _belief_engine_with({
            "npc.guard_b": _familiarity_beliefs("npc.guard_b", "npc.ele", n=5),
            "npc.ele": [],
        })
        store = _store_with(["npc.guard_b", "npc.ele"])
        cands = gen.build_pair_candidates(world, belief_engine=be, memory_store=store)
        assert cands[0].familiarity >= 0.5  # belief signal dominates on tick 1

    def test_co_presence_persisted_in_state(self):
        gen = NPCActionGenerator(seed=42)
        world = _world({"npc.a": "sq", "npc.b": "sq"})
        store = _store_with(["npc.a", "npc.b"])
        for _ in range(3):
            gen.build_pair_candidates(world, belief_engine=None, memory_store=store)
        state = gen.get_state()

        restored = NPCActionGenerator(seed=42)
        restored.load_state(state)
        # Restored generator keeps the accumulated co-presence.
        cand = restored.build_pair_candidates(world, belief_engine=None, memory_store=store)[0]
        fresh = NPCActionGenerator(seed=42).build_pair_candidates(
            world, belief_engine=None, memory_store=store)[0]
        assert cand.familiarity > fresh.familiarity


class TestSchedulerFiresInNormalPlay:
    """End-to-end-ish: co-located NPCs with no seeded familiarity eventually
    trigger a *scheduler-driven* interaction (interaction_type set)."""

    def test_scheduler_fires_from_co_presence(self):
        world = _world({"npc.a": "sq", "npc.b": "sq"})
        store = _store_with(["npc.a", "npc.b"])
        gen = NPCActionGenerator(seed=42)
        sched = NPCInteractionScheduler(seed=7)

        fired = []
        for tick in range(20):
            actions = gen.generate_interaction_actions(
                scheduler=sched, world=world, tick=tick,
                belief_engine=None, memory_store=store, max_interactions=1,
            )
            for a in actions:
                if a.params.get("interaction_type"):
                    fired.append((tick, a.params["interaction_type"]))
        assert fired, "scheduler-driven interaction should fire from co-presence alone"
