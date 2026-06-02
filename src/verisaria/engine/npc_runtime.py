"""NPC Runtime: interaction scheduling, decision primitives, private goals.

Phase-6 minimal version: NPC Interaction Scheduler only (rule-based, no LLM).
P0-1: NPC Action Generator added — rule-driven action generation per tick.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from verisaria.engine.memory import MemoryStore
from verisaria.engine.schemas import Action, ActionType
from verisaria.engine.world import WorldState


# ---------------------------------------------------------------------------
# Interaction Candidate
# ---------------------------------------------------------------------------

@dataclass
class NPCPairCandidate:
    npc_a: str
    npc_b: str
    location_a: str
    location_b: str
    familiarity: float
    has_sharable_memory: bool


@dataclass
class InteractionSeed:
    seed_id: str
    tick: int
    npc_a: str
    npc_b: str
    interaction_type: str
    reason: str


# ---------------------------------------------------------------------------
# NPC Interaction Scheduler
# ---------------------------------------------------------------------------

class NPCInteractionScheduler:
    """Schedule NPC-NPC interactions based on proximity, relationships, and memory.

    Rule-driven, no LLM. All randomness is seed-controlled for replayability.
    """

    FAMILIARITY_THRESHOLD = 0.3
    BASE_INTERACTION_CHANCE = 0.3
    COOLDOWN_TICKS = 3

    INTERACTION_TYPES = ["conversation", "rumor", "trade", "conflict", "cooperation"]

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self._last_interaction: dict[str, int] = {}  # sorted_pair_key -> tick
        self._seq = 0

    def schedule(
        self,
        candidates: list[NPCPairCandidate],
        tick: int,
        max_seeds: int | None = None,
    ) -> list[InteractionSeed]:
        """Evaluate candidate pairs and return interaction seeds for this tick.

        When ``max_seeds`` is set, stop once that many seeds are produced —
        crucially *before* evaluating further pairs, so unexamined pairs do not
        accrue a cooldown they never triggered.
        """
        seeds: list[InteractionSeed] = []
        for candidate in candidates:
            if max_seeds is not None and len(seeds) >= max_seeds:
                break
            result = self._evaluate_pair(candidate, tick)
            if result:
                seeds.append(result)
        return seeds

    def _evaluate_pair(
        self,
        candidate: NPCPairCandidate,
        tick: int,
    ) -> InteractionSeed | None:
        """Check if a specific NPC pair should interact this tick."""
        # 1. Same location
        if candidate.location_a != candidate.location_b:
            return None

        # 2. Familiarity threshold
        if candidate.familiarity < self.FAMILIARITY_THRESHOLD:
            return None

        # 3. Sharable memory
        if not candidate.has_sharable_memory:
            return None

        # 4. Cooldown
        pair_key = self._pair_key(candidate.npc_a, candidate.npc_b)
        last = self._last_interaction.get(pair_key)
        if last is not None and (tick - last) < self.COOLDOWN_TICKS:
            return None

        # 5. Random chance (seed-controlled)
        if self._rng.random() >= self.BASE_INTERACTION_CHANCE:
            return None

        # Select interaction type weighted by familiarity
        interaction_type = self._select_interaction_type(candidate.familiarity)

        self._seq += 1
        self._last_interaction[pair_key] = tick

        return InteractionSeed(
            seed_id=f"npcint_{tick}_{self._seq}",
            tick=tick,
            npc_a=candidate.npc_a,
            npc_b=candidate.npc_b,
            interaction_type=interaction_type,
            reason=f"familiarity={candidate.familiarity:.2f}, shared_memory",
        )

    @staticmethod
    def _pair_key(a: str, b: str) -> str:
        """Canonical pair key (order-independent)."""
        return "|".join(sorted([a, b]))

    def _select_interaction_type(self, familiarity: float) -> str:
        """Weighted selection of interaction type based on familiarity."""
        # Higher familiarity → more cooperative interactions
        weights = {
            "conversation": 0.3,
            "rumor": 0.25,
            "trade": 0.2,
            "cooperation": 0.15 + familiarity * 0.1,
            "conflict": max(0.05, 0.2 - familiarity * 0.15),
        }
        total = sum(weights.values())
        pick = self._rng.uniform(0, total)
        cumulative = 0.0
        for itype, weight in weights.items():
            cumulative += weight
            if pick <= cumulative:
                return itype
        return "conversation"

    def get_state(self) -> dict[str, Any]:
        """Return serializable scheduler state."""
        return {
            "last_interactions": dict(self._last_interaction),
            "seq": self._seq,
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore from serialized state."""
        self._last_interaction = dict(state.get("last_interactions", {}))
        self._seq = state.get("seq", 0)

    def reset(self) -> None:
        """Clear all cooldowns."""
        self._last_interaction.clear()
        self._seq = 0


# ---------------------------------------------------------------------------
# NPC Action Generator
# ---------------------------------------------------------------------------

class NPCActionGenerator:
    """Generate rule-driven Actions for NPCs each tick.

    Strategy:
    - If in active conversation → speech or wait
    - If HP low (< 30%) → mostly wait/rest
    - If others nearby → may move, speak, or look around
    - Otherwise → wander or idle

    All randomness is seed-controlled for replayability.
    """

    # Idle chatter vocabulary (Chinese)
    CHATTER_LINES = [
        "今天天气不错。",
        "你听说了吗？",
        "最近有点奇怪。",
        "你好。",
        "……",
        "这里还挺安静的。",
        "我在等一个人。",
        "有什么新鲜事吗？",
    ]

    # Context-aware responses for active conversations
    CONVERSATION_RESPONSES: dict[str, list[str]] = {
        "question": [
            "你是说……？",
            "我不太确定，但听说最近有些动静。",
            "这事说来话长。",
            "为什么这么说？",
            "让我想想……",
        ],
        "greeting": [
            "你好，有什么我能帮你的吗？",
            "好久不见。",
            "近来可好？",
        ],
        "statement": [
            "原来如此。",
            "这倒是新鲜事。",
            "有意思。",
            "我也注意到了。",
            "确实是这样。",
        ],
    }

    # Co-presence familiarity (P1.7): two NPCs sharing a location grow familiar
    # over time. This is the dominant familiarity signal in normal play — beliefs
    # about *other NPCs* form too rarely to ever reach the scheduler threshold.
    CO_PRESENCE_PER_TICK = 0.08   # familiarity gained per co-located tick
    CO_PRESENCE_DECAY = 0.05      # familiarity lost per tick spent apart
    CO_PRESENCE_MAX = 1.0

    def __init__(self, seed: int = 42, dialogue_generator: Any | None = None) -> None:
        self._seed = seed
        self._rng = random.Random(seed)
        self._seq = 0
        # Optional LLM-backed dialogue generator (P1.1). When None, NPC speech
        # falls back to the deterministic template logic below.
        self._dialogue_generator = dialogue_generator
        # pair_key -> accumulated co-presence familiarity (0..1).
        self._co_presence: dict[str, float] = {}
        # Optional {npc_id: line|None} of dialogue precomputed concurrently for
        # this tick (PLAY-1). When set, the serial speech logic reads it instead
        # of issuing a live (blocking) call, so N NPCs' dialogue costs ~1 call of
        # latency. The loop itself is unchanged, so rng/seq stay deterministic.
        self._line_cache: dict[str, str | None] | None = None

    @staticmethod
    def _pair_key(a: str, b: str) -> str:
        return "|".join(sorted((a, b)))

    def get_state(self) -> dict[str, Any]:
        """Return serializable state."""
        return {
            "seed": self._seed,
            "seq": self._seq,
            "co_presence": dict(self._co_presence),
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore from serialized state."""
        self._seed = state.get("seed", self._seed)
        self._seq = state.get("seq", 0)
        self._co_presence = dict(state.get("co_presence", {}))
        self._rng = random.Random(self._seed)
        # Fast-forward consumed random values to restore sequence position
        for _ in range(self._seq):
            self._rng.random()

    def generate_actions(
        self,
        world: WorldState,
        tick: int,
        active_conversation_entity_ids: set[str] | None = None,
        memory_store: MemoryStore | None = None,
        conversation_manager=None,
        exclude_ids: set[str] | None = None,
        suppress_idle_speech_at: str | None = None,
    ) -> list[Action]:
        """Generate one idle Action per NPC for this tick.

        NPCs in ``exclude_ids`` are skipped entirely (e.g. because they were
        already assigned an NPC-NPC interaction action this tick), so an NPC is
        never given two actions in a single tick.

        ``suppress_idle_speech_at`` names a location where the player just
        addressed someone; bystander NPCs there don't all chime in with idle
        speech this tick (they look/wait instead) so the player's chosen
        conversation isn't drowned out (P1.9). The addressed NPC, being
        ``in_conversation``, still replies.

        Returns a list of Actions ready to be submitted to ActionQueue.
        """
        active_conv = active_conversation_entity_ids or set()
        excluded = exclude_ids or set()
        actions: list[Action] = []

        for entity_id, entity in world.entities.items():
            if entity.entity_type != "npc":
                continue
            if entity_id in excluded:
                continue

            suppress_speech = (
                suppress_idle_speech_at is not None
                and entity.location_id == suppress_idle_speech_at
                and entity_id not in active_conv
            )
            action = self._generate_for_npc(
                entity_id=entity_id,
                entity=entity,
                world=world,
                tick=tick,
                in_conversation=entity_id in active_conv,
                memory_store=memory_store,
                conversation_manager=conversation_manager,
                suppress_speech=suppress_speech,
            )
            if action is not None:
                actions.append(action)

        return actions

    # -- NPC-NPC autonomous interaction (P1.2) --

    @staticmethod
    def pair_familiarity(
        npc_a: str,
        npc_b: str,
        belief_engine: Any | None,
    ) -> float:
        """Symmetric familiarity between two NPCs, derived from their beliefs.

        Uses RelationshipCalculator over each NPC's beliefs about the other and
        takes the max of the two directional familiarities (either party knowing
        the other well is enough to strike up an exchange).
        """
        if belief_engine is None:
            return 0.0
        from verisaria.engine.memory import RelationshipCalculator

        best = 0.0
        for owner, target in ((npc_a, npc_b), (npc_b, npc_a)):
            beliefs = belief_engine.get_beliefs(owner)
            snap = RelationshipCalculator.calculate(owner, target, beliefs)
            best = max(best, snap.dimensions.get("familiarity", 0.0))
        return best

    def build_pair_candidates(
        self,
        world: WorldState,
        belief_engine: Any | None = None,
        memory_store: MemoryStore | None = None,
        busy_ids: set[str] | None = None,
    ) -> list[NPCPairCandidate]:
        """Build co-located NPC pair candidates for the interaction scheduler.

        Pairs are formed only between NPCs sharing a location and not currently
        ``busy`` (e.g. talking to the player). Ordering is deterministic
        (sorted by id) so seed-controlled scheduling is replayable.
        """
        # Update co-presence familiarity for every NPC pair: co-located pairs
        # gain, separated pairs decay. Called once per tick (one build per tick).
        self._update_co_presence(world)

        busy = busy_ids or set()
        npc_ids = sorted(
            eid
            for eid, e in world.entities.items()
            if e.entity_type == "npc" and eid not in busy
        )
        candidates: list[NPCPairCandidate] = []
        for i in range(len(npc_ids)):
            for j in range(i + 1, len(npc_ids)):
                a, b = npc_ids[i], npc_ids[j]
                ent_a, ent_b = world.entities[a], world.entities[b]
                if ent_a.location_id != ent_b.location_id:
                    continue
                # Familiarity = max(belief-derived, accumulated co-presence).
                familiarity = max(
                    self.pair_familiarity(a, b, belief_engine),
                    self._co_presence.get(self._pair_key(a, b), 0.0),
                )
                has_memory = self._has_sharable_memory(
                    a, memory_store
                ) or self._has_sharable_memory(b, memory_store)
                candidates.append(
                    NPCPairCandidate(
                        npc_a=a,
                        npc_b=b,
                        location_a=ent_a.location_id,
                        location_b=ent_b.location_id,
                        familiarity=familiarity,
                        has_sharable_memory=has_memory,
                    )
                )
        return candidates

    def _update_co_presence(self, world: WorldState) -> None:
        """Advance co-presence familiarity one tick: co-located NPC pairs grow,
        separated pairs decay. Keeps the map sparse (drops pairs back to 0)."""
        npc_ids = sorted(
            eid for eid, e in world.entities.items() if e.entity_type == "npc"
        )
        co_located: set[str] = set()
        for i in range(len(npc_ids)):
            for j in range(i + 1, len(npc_ids)):
                a, b = npc_ids[i], npc_ids[j]
                if world.entities[a].location_id == world.entities[b].location_id:
                    co_located.add(self._pair_key(a, b))

        # Grow co-located pairs.
        for key in co_located:
            self._co_presence[key] = min(
                self.CO_PRESENCE_MAX,
                self._co_presence.get(key, 0.0) + self.CO_PRESENCE_PER_TICK,
            )
        # Decay pairs not currently together.
        for key in list(self._co_presence.keys()):
            if key not in co_located:
                decayed = self._co_presence[key] - self.CO_PRESENCE_DECAY
                if decayed <= 0:
                    del self._co_presence[key]
                else:
                    self._co_presence[key] = decayed

    @staticmethod
    def _has_sharable_memory(npc_id: str, memory_store: MemoryStore | None) -> bool:
        if memory_store is None:
            return False
        return len(memory_store.get_all(npc_id)) > 0

    def generate_interaction_actions(
        self,
        scheduler: NPCInteractionScheduler,
        world: WorldState,
        tick: int,
        belief_engine: Any | None = None,
        memory_store: MemoryStore | None = None,
        busy_ids: set[str] | None = None,
        max_interactions: int = 1,
    ) -> list[Action]:
        """Schedule NPC-NPC interactions and convert seeds into speech Actions.

        Each interaction produces a single speech Action from ``npc_a`` to
        ``npc_b`` whose content reuses the existing memory/LLM-driven dialogue
        path. Capped at ``max_interactions`` per tick to avoid chatter floods.
        """
        candidates = self.build_pair_candidates(
            world, belief_engine=belief_engine, memory_store=memory_store,
            busy_ids=busy_ids,
        )
        seeds = scheduler.schedule(candidates, tick, max_seeds=max_interactions)

        actions: list[Action] = []
        for seed in seeds:
            actor = world.entities.get(seed.npc_a)
            content = self._pick_speech_content(
                seed.npc_a, memory_store, world=world, entity=actor
            )
            self._seq += 1
            actions.append(
                Action(
                    action_id=f"act_{tick}_npcint_{self._seq}",
                    source_intent_id=None,
                    actor_id=seed.npc_a,
                    action_type=ActionType.SPEECH,
                    target_id=seed.npc_b,
                    params={
                        "verb": "talk",
                        "content": content,
                        "volume": "normal",
                        "interaction_type": seed.interaction_type,
                    },
                    zone_id=None,
                    conversation_session_id=None,
                    tick=tick,
                )
            )
        return actions

    def _generate_for_npc(
        self,
        entity_id: str,
        entity: Any,
        world: WorldState,
        tick: int,
        in_conversation: bool,
        memory_store: MemoryStore | None = None,
        conversation_manager=None,
        suppress_speech: bool = False,
    ) -> Action | None:
        """Generate a single action for one NPC.

        When ``suppress_speech`` is set, the NPC won't open its mouth with idle
        chatter this tick (a bystander while the player addresses someone) — its
        speech branch becomes a quiet look instead. (P1.9)
        """
        self._seq += 1
        action_id = f"act_{tick}_npc_{self._seq}"

        # 1. Conversation priority — always reply when in conversation
        if in_conversation:
            return self._make_speech(
                action_id, entity_id, tick,
                memory_store=memory_store,
                conversation_manager=conversation_manager,
                world=world,
                entity=entity,
            )

        # 2. Low HP → rest
        hp_ratio = entity.hp / entity.max_hp if entity.max_hp else 1.0
        if hp_ratio < 0.3:
            if self._rng.random() < 0.8:
                return self._make_wait(action_id, entity_id, tick)
            return self._make_look(action_id, entity_id, tick)

        # Home anchoring (P1.8): an NPC away from its home tends to head back;
        # an NPC at home seldom wanders off. Without a home it keeps the original
        # (more restless) behaviour.
        home = getattr(entity, "home_location", None)
        away_from_home = home is not None and entity.location_id != home

        # 3. Nearby entities?
        nearby = [
            eid for eid, e in world.entities.items()
            if eid != entity_id and e.location_id == entity.location_id
        ]

        if nearby:
            roll = self._rng.random()
            move_chance = 0.2 if home is None else (0.5 if away_from_home else 0.04)
            if roll < move_chance:
                return self._make_movement(action_id, entity_id, entity, world, tick)
            # Re-roll the remaining behaviours over the leftover probability mass.
            r = self._rng.random()
            if r < 0.25 and not suppress_speech:
                target = self._rng.choice(nearby)
                return self._make_speech(
                    action_id, entity_id, tick, target_id=target,
                    memory_store=memory_store, world=world, entity=entity,
                )
            if r < 0.6:
                return self._make_look(action_id, entity_id, tick)
            return self._make_wait(action_id, entity_id, tick)

        # 4. Alone
        roll = self._rng.random()
        move_chance = 0.3 if home is None else (0.6 if away_from_home else 0.08)
        if roll < move_chance:
            return self._make_movement(action_id, entity_id, entity, world, tick)
        if self._rng.random() < 0.5:
            return self._make_look(action_id, entity_id, tick)
        return self._make_wait(action_id, entity_id, tick)

    # -- Action builders --

    def _make_wait(self, action_id: str, actor_id: str, tick: int) -> Action:
        return Action(
            action_id=action_id,
            source_intent_id=None,
            actor_id=actor_id,
            action_type=ActionType.PHYSICAL,
            target_id=None,
            params={"verb": "wait"},
            zone_id=None,
            conversation_session_id=None,
            tick=tick,
        )

    def _make_look(self, action_id: str, actor_id: str, tick: int) -> Action:
        return Action(
            action_id=action_id,
            source_intent_id=None,
            actor_id=actor_id,
            action_type=ActionType.PHYSICAL,
            target_id=None,
            params={"verb": "look"},
            zone_id=None,
            conversation_session_id=None,
            tick=tick,
        )

    def _make_speech(
        self,
        action_id: str,
        actor_id: str,
        tick: int,
        target_id: str | None = None,
        memory_store: MemoryStore | None = None,
        conversation_manager=None,
        world: Any | None = None,
        entity: Any | None = None,
    ) -> Action:
        content = self._pick_speech_content(
            actor_id, memory_store, conversation_manager=conversation_manager,
            world=world, entity=entity,
        )
        return Action(
            action_id=action_id,
            source_intent_id=None,
            actor_id=actor_id,
            action_type=ActionType.SPEECH,
            target_id=target_id,
            params={"verb": "talk", "content": content, "volume": "normal"},
            zone_id=None,
            conversation_session_id=None,
            tick=tick,
        )

    def _pick_speech_content(
        self,
        actor_id: str,
        memory_store: MemoryStore | None,
        conversation_manager=None,
        world: Any | None = None,
        entity: Any | None = None,
    ) -> str:
        """Pick speech content via LLM, conversation context, memory, or chatter.

        When an LLM dialogue generator is wired in (P1.1), it gets first refusal;
        if it declines (LLM unavailable/failed) we degrade to the deterministic
        template logic, which keeps FakeLLM replays reproducible.
        """
        session = None
        if conversation_manager is not None:
            session = conversation_manager.get_active_session(actor_id)

        # 0. LLM-generated, memory-driven line (P1.1). Prefer a precomputed line
        #    from the concurrent cache (PLAY-1) so the serial loop never blocks on
        #    a live call; otherwise issue the live call as before.
        line = None
        if self._line_cache is not None and actor_id in self._line_cache:
            line = self._line_cache[actor_id]
        elif self._dialogue_generator is not None:
            line = self._dialogue_generator.generate_line(
                npc_id=actor_id,
                entity=entity,
                world=world,
                memory_store=memory_store,
                conversation_session=session,
            )
        if line:
            # Remember what was actually said so the next prompt steers this NPC
            # away from repeating itself (real-LLM path only; templates below don't
            # record, keeping FakeLLM replays deterministic).
            note = getattr(self._dialogue_generator, "note_spoken", None)
            if callable(note):
                note(actor_id, line)
            return line

        # 1. Conversation context takes priority
        if session is not None:
            last_speaker = session.shared_context.get("last_speaker")
            last_content = session.shared_context.get("last_content", "")
            # Only respond if the player (not self) spoke last
            if last_speaker and last_speaker != actor_id and last_content:
                utt_type = self._classify_utterance(last_content)
                responses = self.CONVERSATION_RESPONSES.get(utt_type, self.CHATTER_LINES)
                return self._rng.choice(responses)

        # 2. Memory fallback
        if memory_store is not None:
            memories = memory_store.get_all(actor_id)
            if memories:
                def _is_speech_ready(m) -> bool:
                    text = m.content.strip()
                    if text.startswith("看见：") or text.startswith("听到："):
                        return False
                    if "npc." in text and "look" in text.lower():
                        return False
                    if "wait" in text.lower() and len(text) < 20:
                        return False
                    return True

                suitable = [m for m in memories if _is_speech_ready(m)]
                if suitable:
                    top = max(suitable, key=lambda m: m.salience)
                    text = top.content
                    if len(text) > 40:
                        text = text[:37] + "..."
                    return text

        # 3. Random chatter
        return self._rng.choice(self.CHATTER_LINES)

    @staticmethod
    def _classify_utterance(text: str) -> str:
        """Classify a player utterance into question/greeting/statement."""
        text_lower = text.lower()
        question_marks = ["?", "？", "吗", "什么", "为什么", "怎么", "多少", "谁", "哪里", "哪儿", "呢", "吗", "么"]
        greeting_marks = ["你好", "嗨", "早安", "晚安", "好久不见", "问好", "hello", "hi"]

        if any(m in text_lower for m in question_marks):
            return "question"
        if any(m in text_lower for m in greeting_marks):
            return "greeting"
        return "statement"

    def _make_movement(
        self,
        action_id: str,
        actor_id: str,
        entity: Any,
        world: WorldState,
        tick: int,
    ) -> Action | None:
        loc = world.locations.get(entity.location_id)
        if not loc or not loc.connections:
            return self._make_wait(action_id, actor_id, tick)

        # If the NPC is away from its home and home is directly reachable, head
        # back rather than drifting further away (P1.8).
        home = getattr(entity, "home_location", None)
        reachable = [c.to_location for c in loc.connections]
        if home is not None and entity.location_id != home and home in reachable:
            target_loc = home
        else:
            target_loc = self._rng.choice(loc.connections).to_location
        return Action(
            action_id=action_id,
            source_intent_id=None,
            actor_id=actor_id,
            action_type=ActionType.MOVEMENT,
            target_id=None,
            params={"verb": "go", "to_location": target_loc, "to_zone": None},
            zone_id=None,
            conversation_session_id=None,
            tick=tick,
        )
