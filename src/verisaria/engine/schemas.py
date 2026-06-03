"""Core Pydantic schemas for the LLM-driven RPG world runtime.

This module defines the immutable data contracts that all other modules
must adhere to. No business logic lives here—only validation and types.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CommitmentLevel(str, Enum):
    CONSIDERING = "considering"
    PREPARING = "preparing"
    ATTEMPTING = "attempting"
    COMMITTED = "committed"


class ActionType(str, Enum):
    SPEECH = "speech"
    MOVEMENT = "movement"
    PHYSICAL = "physical"
    SOCIAL = "social"
    COMBAT = "combat"
    LOOK = "look"
    WAIT = "wait"


class EventType(str, Enum):
    SPEECH = "speech"
    MOVEMENT = "movement"
    PHYSICAL = "physical"
    SOCIAL = "social"
    COMBAT = "combat"
    SYSTEM = "system"


class Conviction(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    DOGMATIC = "dogmatic"


class MemoryLayer(str, Enum):
    WORKING = "working"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


class SuggestionMode(str, Enum):
    NONE = "none"
    SUBTLE = "subtle"
    NORMAL = "normal"
    GUIDED = "guided"


class PacingSpeed(str, Enum):
    SLOW = "slow"
    FAST = "fast"
    PAUSE = "pause"
    FORCE = "force"


# ---------------------------------------------------------------------------
# 5.2 Intent
# ---------------------------------------------------------------------------

class ParsedIntent(BaseModel):
    intent_id: str
    source: Literal["natural_language", "quick_command", "ui_interaction"]
    raw_text: str
    intent_type: ActionType
    actor_id: str
    target_ref: str | None = None
    target_id: str | None = None
    content: str | None = None
    modifiers: dict[str, Any] = Field(default_factory=dict)
    commitment: CommitmentLevel
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguities: list[str] = Field(default_factory=list)
    performed_content: str | None = None
    player_intent_note: str | None = None
    conversation_session_id: str | None = None
    timestamp: int  # tick number


# ---------------------------------------------------------------------------
# 5.3 Action
# ---------------------------------------------------------------------------

class Action(BaseModel):
    action_id: str  # format: "act_{tick}_{seq}"
    source_intent_id: str | None = None
    actor_id: str
    action_type: ActionType
    target_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    zone_id: str | None = None
    conversation_session_id: str | None = None
    tick: int

    @model_validator(mode="after")
    def validate_params_by_type(self):
        required: dict[ActionType, list[str]] = {
            ActionType.SPEECH: ["content"],
            ActionType.MOVEMENT: ["to_location"],
            ActionType.PHYSICAL: ["verb"],
            ActionType.SOCIAL: ["verb"],
            ActionType.COMBAT: ["verb"],
        }
        req = required.get(self.action_type, [])
        missing = [f for f in req if f not in self.params]
        if missing:
            raise ValueError(f"Action type {self.action_type.value} requires params: {missing}")
        return self


# ---------------------------------------------------------------------------
# 5.4 Event
# ---------------------------------------------------------------------------

class Event(BaseModel):
    event_id: str  # format: "evt_{tick}_{seq}"
    event_type: EventType
    actor_id: str
    target_id: str | None = None
    tick: int
    location_id: str
    zone_id: str | None = None
    summary: str
    canonical_facts: dict[str, Any] = Field(default_factory=dict)
    source_action_id: str | None = None
    related_events: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def no_subjective_motive(cls, v: str) -> str:
        forbidden = ["因为", "为了", "意图", "计划"]
        for word in forbidden:
            if word in v:
                raise ValueError(f"Event summary must be neutral, found: {word}")
        return v


# ---------------------------------------------------------------------------
# 5.5 Observation
# ---------------------------------------------------------------------------

class Perception(BaseModel):
    channels: list[Literal["sight", "hearing", "smell", "touch"]] = Field(
        default_factory=list
    )
    saw: str | None = None
    heard_keywords: list[str] = Field(default_factory=list)
    heard_full_content: bool = False
    distance: Literal["near", "far", "adjacent"] = "near"
    attention_level: Literal["focused", "distracted", "unaware"] = "focused"


class Interpretation(BaseModel):
    claim: str
    confidence: float = Field(ge=0.0, le=1.0)
    alternatives: list[str] = Field(default_factory=list)
    emotional_tone: str | None = None


class Observation(BaseModel):
    observation_id: str
    observer_id: str
    source_event_id: str
    tick: int
    perception: Perception
    interpretation: Interpretation | None = None
    detail_level: Literal["full", "partial", "minimal"] = "partial"


# ---------------------------------------------------------------------------
# 5.6 Memory & Belief
# ---------------------------------------------------------------------------

class Memory(BaseModel):
    memory_id: str
    owner_id: str
    source_observation_id: str | None = None
    tick: int
    content: str
    salience: float = Field(ge=0.0, le=1.0)
    decay_rate: float = Field(ge=0.0, le=1.0)
    layer: MemoryLayer
    topic_tags: list[str] = Field(default_factory=list)
    last_recalled_tick: int | None = None
    compression_of: list[str] | None = None


class Belief(BaseModel):
    belief_id: str
    owner_id: str
    claim: str
    confidence: float = Field(ge=0.0, le=1.0)
    conviction: Conviction
    source_evidence: list[str] = Field(default_factory=list)
    challenged_by: list[str] = Field(default_factory=list)
    would_revise_if: list[str] = Field(default_factory=list)
    formed_at_tick: int
    last_updated_tick: int


# ---------------------------------------------------------------------------
# 5.6a Belief Change
# ---------------------------------------------------------------------------

class BeliefChange(BaseModel):
    owner_id: str
    belief_id: str
    change_type: Literal["created", "strengthened", "weakened", "challenged", "revoked"]
    delta_confidence: float
    reason: str
    triggering_memory_id: str
    tick: int


# ---------------------------------------------------------------------------
# 5.7 Relationship Snapshot
# ---------------------------------------------------------------------------

class RelationshipSnapshot(BaseModel):
    snapshot_id: str
    npc_id: str
    target_id: str
    tick: int
    dimensions: dict[str, float] = Field(default_factory=dict)
    last_interaction_summary: str | None = None
    dominant_beliefs: list[dict[str, Any]] = Field(default_factory=list)
    updated_at_tick: int


# ---------------------------------------------------------------------------
# 5.8 Conversation Session
# ---------------------------------------------------------------------------

class ConversationSession(BaseModel):
    session_id: str
    participants: list[str] = Field(default_factory=list)
    started_at_tick: int
    turn_count: int = 0
    topic_stack: list[str] = Field(default_factory=list)
    shared_context: dict[str, Any] = Field(default_factory=dict)
    status: Literal["active", "interrupted", "resumed", "concluded", "abandoned"] = "active"
    interruptible: bool = True
    pending_interruptions: list[str] = Field(default_factory=list)
    last_activity_tick: int


# ---------------------------------------------------------------------------
# 5.9 Player Agenda
# ---------------------------------------------------------------------------

class Drive(BaseModel):
    id: str
    label: str
    strength: float = Field(ge=0.0, le=1.0)
    source: Literal["player_declared", "system_inferred", "reflection"]
    declared_at_tick: int | None = None
    confirmed: bool = False
    confirmed_at_tick: int | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class PlayerAgenda(BaseModel):
    player_id: str
    tick: int
    current_drives: list[Drive] = Field(default_factory=list)
    declared_to_world: list[str] = Field(default_factory=list)
    private_intent: list[str] = Field(default_factory=list)
    system_inferred: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    rejected_paths: list[str] = Field(default_factory=list)
    long_term_aspirations: list[str] = Field(default_factory=list)
    suggestion_mode: SuggestionMode = SuggestionMode.SUBTLE
    inference_mode: Literal["off", "reflection_only", "normal"] = "reflection_only"


# ---------------------------------------------------------------------------
# 5.10 Content Pack
# ---------------------------------------------------------------------------

class WorldPremise(BaseModel):
    era: str
    tone: str
    central_tension: str


class AccessScope(BaseModel):
    visible_to: dict[str, list[str]] = Field(default_factory=dict)
    hidden_from: dict[str, list[str]] = Field(default_factory=dict)


class WorldBookEntry(BaseModel):
    entry_id: str
    layer: Literal[
        "canonical_fact",
        "public_belief",
        "faction_propaganda",
        "forbidden_knowledge",
        "local_rumor",
        "personal_truth",
    ]
    content: str
    access: AccessScope = Field(default_factory=AccessScope)
    confidence_policy: Literal["fact", "belief_not_fact"] = "belief_not_fact"


class ContentPack(BaseModel):
    content_pack_id: str
    schema_version: str = "2.0"
    world_premise: WorldPremise
    world_book: list[WorldBookEntry] = Field(default_factory=list)
    starting_location: str
    initial_entities: list[dict[str, Any]] = Field(default_factory=list)
    initial_relationships: list[dict[str, Any]] = Field(default_factory=list)
    initial_conversations: list[dict[str, Any]] = Field(default_factory=list)
    initial_locations: list[dict[str, Any]] = Field(default_factory=list)
    player_agenda_template: dict[str, Any] = Field(default_factory=dict)
    style_guide: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    campaign_drivers: list[dict[str, Any]] = Field(default_factory=list)
    stance_topics: list[dict[str, Any]] = Field(default_factory=list)
    world_state_vars: list[dict[str, Any]] = Field(default_factory=list)
    rule_presets: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 6.4 Arbiter Output
# ---------------------------------------------------------------------------

class EvidenceRef(BaseModel):
    path: str
    value: str | float | bool | int
    source: Literal["trait", "attribute", "world_state", "relationship"]

    @field_validator("path")
    @classmethod
    def validate_path_format(cls, v: str) -> str:
        import re

        if not re.match(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$", v):
            raise ValueError(f"Invalid evidence path format: {v}")
        return v


class StateChange(BaseModel):
    field: str
    delta: float | int | bool | str
    reason: str

    @field_validator("field")
    @classmethod
    def validate_field_format(cls, v: str) -> str:
        import re

        if not re.match(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$", v):
            raise ValueError(f"Invalid field path format: {v}")
        return v


class NewPrerequisite(BaseModel):
    """A GM-declared prerequisite the arbiter wants to introduce that the current
    world model has no var for. The engine registers it as a first-class (dynamic)
    world var so the player has a structural path to satisfy it. See
    docs/design/dynamic-world-model.md."""
    var_id: str                                    # stable snake_case ascii id
    label: str = ""                                # human-readable 中文 label
    set_by: list[str] = Field(default_factory=list)          # NPC role or id who can satisfy it
    request_keywords: list[str] = Field(default_factory=list)  # how a request routes to it


class ArbiterOutput(BaseModel):
    arbiter_id: str
    source_action_id: str
    outcome: Literal["success", "partial_success", "failure", "redirect"]
    reason: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    state_changes_proposed: list[StateChange] = Field(default_factory=list)
    redirect_to_action_type: ActionType | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    narration_hint: str | None = None
    # On partial_success, an objective one-line statement of the intermediate fact
    # or condition just established (LLM-authored), remembered by the FactLedger for
    # later arbitration to build on. Empty otherwise. See emergent-fact-ledger.md.
    established_fact: str | None = None
    # True when this is a deterministic default verdict because the LLM was
    # unavailable — NOT a real adjudication (so logs/diagnostics can flag it).
    is_fallback: bool = False
    # Optionally: a prerequisite the verdict introduces that no current world var
    # represents — the engine registers it so the player can actually satisfy it
    # (rather than the demand being a dead end). See dynamic-world-model.md.
    new_prerequisite: NewPrerequisite | None = None

    @model_validator(mode="after")
    def validate_redirect_has_target(self):
        if self.outcome == "redirect" and self.redirect_to_action_type is None:
            raise ValueError("outcome='redirect' requires redirect_to_action_type")
        return self


# ---------------------------------------------------------------------------
# 6.8 Save / Load
# ---------------------------------------------------------------------------

class SaveData(BaseModel):
    save_id: str
    save_type: Literal["manual", "checkpoint", "quick", "auto"]
    created_at: str
    tick: int
    rng_state: str  # 序列化的随机数生成器状态（含 seed + 消耗计数）
    llm_fixture_version: str
    content_pack_id: str
    content_pack_version: str
    world_state: dict[str, Any] = Field(default_factory=dict)
    subjectivity_state: dict[str, Any] = Field(default_factory=dict)
    player_state: dict[str, Any] = Field(default_factory=dict)
    scheduler_state: dict[str, Any] = Field(default_factory=dict)
    combat_state: dict[str, Any] = Field(default_factory=dict)
    conversation_state: dict[str, Any] = Field(default_factory=dict)
    npc_runtime_state: dict[str, Any] = Field(default_factory=dict)
    llm_budget_state: dict[str, Any] = Field(default_factory=dict)
    snapshot_hash: str | None = None
    event_log_hash: str | None = None
