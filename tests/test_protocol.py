"""Protocol layer (Step 2): the typed, serializable engine↔frontend contract.

These cover the pure-data foundation — relationship descriptors (decision §8.1),
the three-mode rendering, and JSON round-trips for Commands/Events/Snapshot so a
non-Python client (Godot) can speak the same shapes.
"""
from __future__ import annotations

import json

from verisaria import protocol as P


# --- §8.1 relationship rendering: data in the protocol, mode picked by frontend ---

def test_relationship_descriptor_bands():
    neg = P.relationship_descriptor("suspicion", 0.05)
    assert neg.band == "negligible" and neg.phrase == ""
    sl = P.relationship_descriptor("trust", 0.2)
    assert sl.band == "slight" and sl.phrase
    strong = P.relationship_descriptor("suspicion", 0.51)
    assert strong.band == "strong"
    assert strong.label == "怀疑"
    assert "戒心很重" in strong.phrase


def test_relationship_three_render_modes_match_decision():
    """The exact example from the decision: suspicion 0.51 on '队长'."""
    d = P.relationship_descriptor("suspicion", 0.51)
    assert P.render_relationship("队长", d, "simple") == "怀疑 0.51"
    assert P.render_relationship("队长", d, "normal") == "队长对你戒心很重"
    assert P.render_relationship("队长", d, "verbose") == "[怀疑 0.51] 队长对你戒心很重"


# --- serialization: every DTO must round-trip through JSON (for Godot IPC) ---

def test_command_json_round_trip():
    for cmd in (P.SubmitInput("对队长说：你好"), P.Wait(), P.Skip(3),
                P.Save("checkpoint"), P.Load("save_7")):
        d = P.command_to_dict(cmd)
        assert json.loads(json.dumps(d)) == d          # JSON-clean
        assert P.command_from_dict(d) == cmd            # round-trips back


def test_event_to_dict_is_tagged_and_json_clean():
    ev = P.NpcSpoke(tick=5, npc_id="npc.captain_brann", name="captain_brann", line="哼。")
    d = P.event_to_dict(ev)
    assert d["event"] == "NpcSpoke" and d["tick"] == 5 and d["line"] == "哼。"
    assert json.loads(json.dumps(d)) == d

    rel = P.RelationshipShifted(
        tick=5, npc_id="npc.captain_brann", name="captain_brann",
        descriptor=P.relationship_descriptor("suspicion", 0.51),
    )
    d2 = P.event_to_dict(rel)
    assert d2["event"] == "RelationshipShifted"
    assert d2["descriptor"]["band"] == "strong"        # nested DTO serialized
    assert json.loads(json.dumps(d2)) == d2


def test_world_snapshot_serializes():
    snap = P.WorldSnapshot(
        tick=7, pacing="normal",
        location=P.LocationView(id="gatehouse", description="风雪门楼"),
        present=[P.PresentEntity(id="npc.sentry_voss", name="sentry_voss", type="npc")],
        world_vars=[P.WorldVarView(var_id="refugees_admitted", label="难民是否获准入营", value=False)],
    )
    d = P.snapshot_to_dict(snap)
    assert d["location"]["id"] == "gatehouse"
    assert d["present"][0]["name"] == "sentry_voss"
    assert json.loads(json.dumps(d)) == d
