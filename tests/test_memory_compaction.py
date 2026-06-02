"""Tests for P3.3: automatic memory compaction in the tick loop.

The MemoryCompressor already implements the layered summarisation rules; these
tests pin down that GameSession actually drives it each tick — working memory
gets summarised into short-term (and short-term into long-term) once thresholds
are crossed, with the originals removed and the summary retained — so a
long-running world does not accumulate unbounded working memory.
"""

from __future__ import annotations

from verisaria.engine.memory import MemoryCompressor, MemoryStore
from verisaria.engine.schemas import Memory, MemoryLayer


def _mem(owner: str, mid: str, tick: int, layer=MemoryLayer.WORKING,
         salience: float = 0.5, tags=None) -> Memory:
    return Memory(
        memory_id=mid,
        owner_id=owner,
        source_observation_id="obs",
        tick=tick,
        content=f"记忆内容 {mid}",
        salience=salience,
        decay_rate=0.05,
        layer=layer,
        topic_tags=tags or ["general"],
    )


class _Session:
    """Lightweight helper to build a GameSession on the fake backend."""

    @staticmethod
    def build(tmp_path):
        from verisaria.runtime.session import GameSession
        return GameSession(
            "fixtures/content_packs/valid_frontier_town.json",
            save_dir=str(tmp_path),
            llm_backend="fake",
        )


class TestCompressorWired:
    def test_session_has_compressor(self, tmp_path):
        s = _Session.build(tmp_path)
        assert isinstance(s.memory_compressor, MemoryCompressor)
        # Shares the session's MemoryStore (no divergent copy).
        assert s.memory_compressor._store is s.memory_store


class TestAutoCompactWorking:
    def _overfill_working(self, store: MemoryStore, owner: str, n: int = 12,
                          tick: int = 1) -> None:
        for i in range(n):
            store.add(owner, _mem(owner, f"w{i}", tick=tick))

    def test_working_compacted_into_short_term(self, tmp_path):
        s = _Session.build(tmp_path)
        owner = "npc.guard_b"
        self._overfill_working(s.memory_store, owner, n=12, tick=1)
        assert s.memory_store.count(owner, MemoryLayer.WORKING) == 12

        # Drive the per-tick compaction far enough past WORKING_AGE_THRESHOLD.
        s._compress_memories_for_all(current_tick=20)

        working_after = s.memory_store.count(owner, MemoryLayer.WORKING)
        short_after = s.memory_store.count(owner, MemoryLayer.SHORT_TERM)
        assert working_after == 0, "compressed working memories should be removed"
        assert short_after == 1, "a single short-term summary should be created"

    def test_summary_links_back_to_originals(self, tmp_path):
        s = _Session.build(tmp_path)
        owner = "npc.guard_b"
        self._overfill_working(s.memory_store, owner, n=12, tick=1)
        s._compress_memories_for_all(current_tick=20)
        summary = s.memory_store.get(owner, MemoryLayer.SHORT_TERM)[0]
        assert summary.compression_of  # references the compressed ids
        assert len(summary.compression_of) == 12

    def test_not_triggered_when_recent(self, tmp_path):
        s = _Session.build(tmp_path)
        owner = "npc.guard_b"
        # 12 working memories but all very recent → age threshold not met.
        self._overfill_working(s.memory_store, owner, n=12, tick=18)
        s._compress_memories_for_all(current_tick=20)
        assert s.memory_store.count(owner, MemoryLayer.WORKING) == 12
        assert s.memory_store.count(owner, MemoryLayer.SHORT_TERM) == 0

    def test_under_threshold_untouched(self, tmp_path):
        s = _Session.build(tmp_path)
        owner = "npc.guard_b"
        self._overfill_working(s.memory_store, owner, n=5, tick=1)
        s._compress_memories_for_all(current_tick=50)
        assert s.memory_store.count(owner, MemoryLayer.WORKING) == 5


class TestAutoCompactShort:
    def test_short_term_compacted_into_long(self, tmp_path):
        s = _Session.build(tmp_path)
        owner = "npc.ele"
        for i in range(21):
            s.memory_store.add(owner, _mem(owner, f"s{i}", tick=1,
                                           layer=MemoryLayer.SHORT_TERM))
        s._compress_memories_for_all(current_tick=200)
        assert s.memory_store.count(owner, MemoryLayer.SHORT_TERM) == 0
        assert s.memory_store.count(owner, MemoryLayer.LONG_TERM) == 1


class TestRunTickDrivesCompaction:
    def test_run_tick_invokes_compaction(self, tmp_path):
        s = _Session.build(tmp_path)
        owner = "npc.guard_b"
        for i in range(12):
            s.memory_store.add(owner, _mem(owner, f"w{i}", tick=0))
        # Advance the world tick well past the age threshold, then run a tick.
        s.world.state.tick = 30
        s.run_tick("看看周围")
        # Working memory must have been compacted during the tick.
        assert s.memory_store.count(owner, MemoryLayer.WORKING) < 12

    def test_compaction_bounded_over_long_run(self, tmp_path):
        """Over many ticks an NPC's working memory stays bounded rather than
        growing without limit."""
        s = _Session.build(tmp_path)
        owner = "npc.guard_b"
        for t in range(60):
            # Inject a fresh working memory each tick to simulate observation.
            s.memory_store.add(owner, _mem(owner, f"w{t}", tick=t))
            s.world.state.tick = t
            s._compress_memories_for_all(current_tick=t)
        working = s.memory_store.count(owner, MemoryLayer.WORKING)
        assert working <= MemoryCompressor.WORKING_THRESHOLD + 1
