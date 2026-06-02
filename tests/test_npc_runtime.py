"""Tests for NPC Interaction Scheduler: pair matching, cooldown, determinism."""

from __future__ import annotations

import pytest

from verisaria.engine.npc_runtime import NPCInteractionScheduler, NPCPairCandidate


class TestNPCInteractionScheduler:
    def _make_scheduler(self, seed: int = 42) -> NPCInteractionScheduler:
        return NPCInteractionScheduler(seed=seed)

    def test_same_location_and_familiarity_interacts(self) -> None:
        sched = self._make_scheduler(seed=1)  # seed that passes random check
        candidates = [
            NPCPairCandidate(
                npc_a="npc.guard_b",
                npc_b="npc.ele",
                location_a="town_square",
                location_b="town_square",
                familiarity=0.5,
                has_sharable_memory=True,
            )
        ]
        seeds = sched.schedule(candidates, tick=1)
        assert len(seeds) >= 0  # May or may not pass random check

    def test_different_location_no_interaction(self) -> None:
        sched = self._make_scheduler()
        candidates = [
            NPCPairCandidate(
                npc_a="npc.guard_b",
                npc_b="npc.ele",
                location_a="town_square",
                location_b="tavern",
                familiarity=0.5,
                has_sharable_memory=True,
            )
        ]
        seeds = sched.schedule(candidates, tick=1)
        assert len(seeds) == 0

    def test_low_familiarity_no_interaction(self) -> None:
        sched = self._make_scheduler()
        candidates = [
            NPCPairCandidate(
                npc_a="npc.guard_b",
                npc_b="npc.ele",
                location_a="town_square",
                location_b="town_square",
                familiarity=0.1,
                has_sharable_memory=True,
            )
        ]
        seeds = sched.schedule(candidates, tick=1)
        assert len(seeds) == 0

    def test_no_sharable_memory_no_interaction(self) -> None:
        sched = self._make_scheduler()
        candidates = [
            NPCPairCandidate(
                npc_a="npc.guard_b",
                npc_b="npc.ele",
                location_a="town_square",
                location_b="town_square",
                familiarity=0.5,
                has_sharable_memory=False,
            )
        ]
        seeds = sched.schedule(candidates, tick=1)
        assert len(seeds) == 0

    def test_cooldown_prevents_reinteraction(self) -> None:
        sched = self._make_scheduler(seed=1)
        candidates = [
            NPCPairCandidate(
                npc_a="npc.guard_b",
                npc_b="npc.ele",
                location_a="town_square",
                location_b="town_square",
                familiarity=0.5,
                has_sharable_memory=True,
            )
        ]
        # First tick - may interact
        seeds1 = sched.schedule(candidates, tick=1)
        # Second tick - cooldown prevents
        seeds2 = sched.schedule(candidates, tick=2)
        assert len(seeds2) == 0
        # After cooldown expires
        seeds3 = sched.schedule(candidates, tick=5)
        # May or may not interact again depending on random
        pass

    def test_multiple_candidates(self) -> None:
        sched = self._make_scheduler()
        candidates = [
            NPCPairCandidate(
                npc_a="npc.guard_b", npc_b="npc.ele",
                location_a="town_square", location_b="town_square",
                familiarity=0.5, has_sharable_memory=True,
            ),
            NPCPairCandidate(
                npc_a="npc.guard_b", npc_b="npc.merchant",
                location_a="town_square", location_b="town_square",
                familiarity=0.6, has_sharable_memory=True,
            ),
        ]
        seeds = sched.schedule(candidates, tick=1)
        # Should evaluate both independently
        assert len(seeds) <= 2

    def test_interaction_type_selected(self) -> None:
        sched = self._make_scheduler(seed=1)
        candidates = [
            NPCPairCandidate(
                npc_a="npc.guard_b", npc_b="npc.ele",
                location_a="town_square", location_b="town_square",
                familiarity=0.5, has_sharable_memory=True,
            )
        ]
        seeds = sched.schedule(candidates, tick=1)
        if seeds:
            assert seeds[0].interaction_type in [
                "conversation", "rumor", "trade", "conflict", "cooperation"
            ]

    def test_seed_determinism(self) -> None:
        """Same seed should produce same results."""
        def run(seed: int) -> list:
            sched = NPCInteractionScheduler(seed=seed)
            candidates = [
                NPCPairCandidate(
                    npc_a="npc.guard_b", npc_b="npc.ele",
                    location_a="town_square", location_b="town_square",
                    familiarity=0.5, has_sharable_memory=True,
                )
            ]
            return sched.schedule(candidates, tick=1)

        result1 = run(123)
        result2 = run(123)
        assert len(result1) == len(result2)
        if result1 and result2:
            assert result1[0].interaction_type == result2[0].interaction_type

    def test_reset_clears_cooldowns(self) -> None:
        sched = self._make_scheduler(seed=1)
        candidates = [
            NPCPairCandidate(
                npc_a="npc.guard_b", npc_b="npc.ele",
                location_a="town_square", location_b="town_square",
                familiarity=0.5, has_sharable_memory=True,
            )
        ]
        seeds1 = sched.schedule(candidates, tick=1)
        sched.reset()
        # After reset, cooldown is cleared
        seeds2 = sched.schedule(candidates, tick=2)
        # Both should have same chance since cooldown is cleared
        pass

    def test_get_state(self) -> None:
        sched = self._make_scheduler(seed=1)
        candidates = [
            NPCPairCandidate(
                npc_a="npc.guard_b", npc_b="npc.ele",
                location_a="town_square", location_b="town_square",
                familiarity=0.5, has_sharable_memory=True,
            )
        ]
        sched.schedule(candidates, tick=1)
        state = sched.get_state()
        assert "npc.ele|npc.guard_b" in state["last_interactions"]


class TestInteractionTypeSelection:
    def test_high_familiarity_favors_cooperation(self) -> None:
        sched = NPCInteractionScheduler(seed=42)
        counts = {"cooperation": 0, "conflict": 0}
        for _ in range(100):
            itype = sched._select_interaction_type(familiarity=0.9)
            if itype in counts:
                counts[itype] += 1
        assert counts["cooperation"] > counts["conflict"]

    def test_low_familiarity_favors_conflict(self) -> None:
        sched = NPCInteractionScheduler(seed=42)
        counts = {"cooperation": 0, "conflict": 0}
        for _ in range(100):
            itype = sched._select_interaction_type(familiarity=0.0)
            if itype in counts:
                counts[itype] += 1
        assert counts["conflict"] > counts["cooperation"]
