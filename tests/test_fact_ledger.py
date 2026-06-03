"""FactLedger — emergent intermediate facts, keyed by world var.
See docs/design/emergent-fact-ledger.md."""
from __future__ import annotations

from verisaria.engine.fact_ledger import FactLedger, EstablishedFact


def test_add_and_relevant_filters_by_var_recent_first():
    led = FactLedger()
    led.add("森工愿交报告，条件是匿名", regarding="pump_failure_disclosed", npc_id="npc.foreman", tick=5)
    led.add("林局长要求听证席", regarding="tow_order_halted", npc_id="npc.director_lin", tick=6)
    led.add("森工还要审计草稿", regarding="pump_failure_disclosed", npc_id="npc.foreman", tick=8)

    pump = led.relevant("pump_failure_disclosed")
    assert [f.text for f in pump] == ["森工还要审计草稿", "森工愿交报告，条件是匿名"]  # recent first
    assert all(f.regarding == "pump_failure_disclosed" for f in pump)
    assert len(led.relevant("tow_order_halted")) == 1
    assert led.relevant("nonexistent") == []


def test_add_dedups_same_var_same_text_refreshing_tick():
    led = FactLedger()
    led.add("条件是匿名", regarding="v", npc_id="n", tick=3)
    led.add("条件是匿名", regarding="v", npc_id="n", tick=9)  # restating the standing condition
    facts = led.relevant("v")
    assert len(facts) == 1 and facts[0].tick == 9  # one entry, tick refreshed


def test_add_ignores_blank_text():
    led = FactLedger()
    led.add("", regarding="v", npc_id="n", tick=1)
    led.add("   ", regarding="v", npc_id="n", tick=1)
    assert led.all() == []


def test_relevant_caps_count():
    led = FactLedger()
    for i in range(10):
        led.add(f"fact {i}", regarding="v", npc_id="n", tick=i)
    assert len(led.relevant("v", limit=6)) == 6


def test_state_roundtrip():
    led = FactLedger()
    led.add("a", regarding="v1", npc_id="n1", tick=2)
    led.add("b", regarding="v2", npc_id="n2", tick=4)
    state = led.get_state()

    restored = FactLedger()
    restored.load_state(state)
    assert restored.get_state() == state
    assert restored.relevant("v1")[0] == EstablishedFact("a", "v1", "n1", 2)
