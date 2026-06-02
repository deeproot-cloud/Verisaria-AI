"""World Book Filter: scope-based access control for world knowledge.

Design doc §A7: "世界书是约束而非全知" — NPCs only see entries matching their scope.

Phase-14 minimal version:
- Filters WorldBookEntry list by entity attributes
- Supports visible_to / hidden_from rules
- Default: visible unless explicitly hidden
"""

from __future__ import annotations

from typing import Any

from verisaria.engine.schemas import WorldBookEntry
from verisaria.engine.world import EntityState


class WorldBookFilter:
    """Filter World Book entries based on entity scope."""

    @classmethod
    def filter_for_entity(
        cls,
        entries: list[WorldBookEntry],
        entity: EntityState | None,
    ) -> list[WorldBookEntry]:
        """Return entries visible to the given entity."""
        if entity is None:
            return []

        entity_scope = cls._build_entity_scope(entity)
        return [e for e in entries if cls._is_visible(e, entity_scope)]

    @staticmethod
    def _build_entity_scope(entity: EntityState) -> dict[str, set[str]]:
        """Build a scope dict from entity attributes and traits."""
        scope: dict[str, set[str]] = {}

        # Extract faction, region, education from attributes
        for key in ("faction", "region", "education", "profession", "race"):
            val = entity.attributes.get(key)
            if val:
                if isinstance(val, list):
                    scope[key] = set(str(v) for v in val)
                else:
                    scope[key] = {str(val)}

        # Also check traits for faction-like tags
        for trait in entity.traits:
            if "." in trait:
                prefix, value = trait.split(".", 1)
                if prefix in ("faction", "region", "education"):
                    scope.setdefault(prefix, set()).add(value)

        # Always include "all"
        scope["all"] = {"all"}

        return scope

    @classmethod
    def _is_visible(
        cls,
        entry: WorldBookEntry,
        entity_scope: dict[str, set[str]],
    ) -> bool:
        """Check if a single entry is visible to the entity."""
        access = entry.access
        if access is None:
            return True

        # 1. Check hidden_from first — explicit denial overrides everything
        hidden_from = access.hidden_from or {}
        if cls._matches_scope(hidden_from, entity_scope):
            return False

        # 2. Check visible_to — if specified, entity must match at least one
        visible_to = access.visible_to or {}
        if visible_to:
            return cls._matches_scope(visible_to, entity_scope)

        # 3. Default: visible if no explicit visible_to restriction
        return True

    @staticmethod
    def _matches_scope(
        rules: dict[str, Any],
        entity_scope: dict[str, set[str]],
    ) -> bool:
        """Check if entity scope matches ANY rule dimension.

        Rules format: {"faction": ["church"], "region": ["all"]}
        Entity scope: {"faction": {"church", "guard"}, "all": {"all"}}

        Returns True if at least one dimension matches.
        """
        for dimension, allowed_values in rules.items():
            if allowed_values == "all" or "all" in allowed_values:
                return True

            entity_values = entity_scope.get(dimension, set())
            allowed_set = set(allowed_values) if isinstance(allowed_values, list) else {str(allowed_values)}

            if entity_values & allowed_set:
                return True

        return False
