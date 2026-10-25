"""The intent catalog: one table that says what each capability is.

The catalog lives in ``intents.yaml`` so it can be audited and versioned
without touching code. This module loads it, validates it, and gives the
rest of the system a typed view:

* which action serves an intent,
* which slots the user must supply before the action may run,
* how risky the intent is,
* how a message is matched to an intent,
* what to ask when a required slot is missing.

The two existing enums stay where they are — ``models.enums.Intent`` for the
write path and ``agent.turns.SupportIntent`` for the read path — but both
are now *views* of the catalog rather than independent definitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from app.agent.routing import (
    blocks_write_action,
    is_analytics_question,
    is_policy_question,
)
from app.models.enums import RiskLevel

__all__ = [
    "IntentSpec",
    "SlotSpec",
    "IntentCatalog",
    "catalog",
    "get_catalog",
]

Nature = Literal["info", "info_action", "action", "collaboration", "none"]
NoEvidence = Literal[
    "none",
    "list_candidates",
    "refuse_without_basis",
    "answer_as_not_started",
]
Fallback = Literal[
    "none",
    "clarify",
    "clarify_then_handoff",
    "handoff",
    "create_ticket",
    "approval",
]

_CATALOG_PATH = Path(__file__).with_name("intents.yaml")

# A field the loader does not know is a typo, and a typo in this file would
# silently change which intent wins. Unknown keys are refused instead.
_INTENT_FIELDS = frozenset(
    {
        "intent_id",
        "priority",
        "nature",
        "action",
        "required_slots",
        "clarify_prompts",
        "validation",
        "risk_level",
        "no_evidence",
        "fallback",
        "write_path",
        "cues",
        "match_policy_question",
        "match_catalog",
        "match_order_id",
        "match_request_only",
        "match_analytics_question",
    }
)
_SLOT_FIELDS = frozenset(
    {
        "type",
        "pattern",
        "prompt",
        "invalid_prompt",
        "missing_reason",
        "examples",
        "sensitive",
    }
)
_TOP_FIELDS = frozenset({"version", "slots", "intents"})


@dataclass(frozen=True, slots=True)
class SlotSpec:
    """One slot the user may have to supply."""

    name: str
    type: str
    prompt: str
    examples: tuple[str, ...] = ()
    pattern: str | None = None
    sensitive: bool = False
    invalid_prompt: str | None = None
    missing_reason: str | None = None


@dataclass(frozen=True, slots=True)
class IntentSpec:
    """One row of the catalog."""

    intent_id: str
    nature: Nature
    action: tuple[str, ...]
    required_slots: tuple[str, ...]
    validation: str
    risk_level: RiskLevel
    no_evidence: NoEvidence
    fallback: Fallback
    write_path: bool
    priority: int = 100
    cues: tuple[str, ...] = ()
    clarify_prompts: tuple[tuple[str, str], ...] = ()
    match_policy_question: bool = False
    match_catalog: bool = False
    match_order_id: bool = False
    match_request_only: bool = False
    match_analytics_question: bool = False

    def requires(self, slot: str) -> bool:
        return slot in self.required_slots

    def clarify_prompt(self, slot: str) -> str | None:
        for name, prompt in self.clarify_prompts:
            if name == slot:
                return prompt
        return None


class IntentCatalog:
    """The loaded catalog, with lookups by intent id and by slot."""

    def __init__(
        self, version: str, specs: dict[str, IntentSpec], slots: dict[str, SlotSpec]
    ) -> None:
        self.version = version
        self.specs = specs
        self.slots = slots

    def get(self, intent_id: str) -> IntentSpec:
        spec = self.specs.get(intent_id)
        if spec is None:
            raise KeyError(f"Intent is not in the catalog: {intent_id}.")
        return spec

    def has(self, intent_id: str) -> bool:
        return intent_id in self.specs

    def slot(self, name: str) -> SlotSpec:
        spec = self.slots.get(name)
        if spec is None:
            raise KeyError(f"Slot is not in the catalog: {name}.")
        return spec

    def prompt_for(self, intent_id: str, slot: str) -> str:
        """The question to ask when a required slot is missing."""
        spec = self.get(intent_id)
        return spec.clarify_prompt(slot) or self.slot(slot).prompt

    def invalid_prompt_for(self, intent_id: str, slot: str) -> str:
        """The question to ask when a slot was supplied but is malformed."""
        spec = self.slot(slot)
        return spec.invalid_prompt or spec.prompt

    def missing_reason_for(self, slot: str) -> str:
        """The machine-readable reason reported when a slot is missing."""
        spec = self.slot(slot)
        return spec.missing_reason or f"MISSING_{slot.upper()}"

    def missing_slots(self, intent_id: str, present: set[str]) -> tuple[str, ...]:
        """Required slots the user has not supplied, in catalog order."""
        spec = self.get(intent_id)
        return tuple(name for name in spec.required_slots if name not in present)

    def risk_of(self, intent_id: str) -> RiskLevel:
        return self.get(intent_id).risk_level

    def actions_of(self, intent_id: str) -> tuple[str, ...]:
        return self.get(intent_id).action

    def ordered_specs(self) -> tuple[IntentSpec, ...]:
        """Every row, most specific first."""
        return tuple(
            sorted(self.specs.values(), key=lambda spec: (spec.priority, spec.intent_id))
        )

    def order_scoped_ids(self) -> tuple[str, ...]:
        """Intents that cannot be served without an order id."""
        return tuple(
            spec.intent_id
            for spec in self.ordered_specs()
            if "order_id" in spec.required_slots
        )

    def match(
        self,
        text: str,
        *,
        has_catalog_match: bool = False,
        has_order_id: bool = False,
    ) -> IntentSpec | None:
        """Pick the row a message belongs to.

        ``text`` must already be lowercased. The first row whose cues or
        match flags fire wins, so the order of the rows is the priority.
        """
        for spec in self.ordered_specs():
            if spec.intent_id == "UNKNOWN":
                continue
            if spec.match_request_only and blocks_write_action(text):
                continue
            if spec.match_policy_question and is_policy_question(text):
                return spec
            if spec.match_analytics_question and is_analytics_question(text):
                return spec
            if spec.match_catalog and has_catalog_match:
                return spec
            if spec.match_order_id and has_order_id:
                return spec
            if any(cue in text for cue in spec.cues):
                return spec
        return None


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _as_pairs(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        return ()
    return tuple((str(key), str(item)) for key, item in value.items())


def _reject_unknown(item: Any, allowed: frozenset[str], where: str) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"The {where} entry must be a mapping.")
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise ValueError(f"Unknown field(s) in the {where}: {', '.join(unknown)}.")


def _load(path: Path) -> IntentCatalog:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("The intent catalog must be a mapping.")
    _reject_unknown(raw, _TOP_FIELDS, "catalog")
    slots: dict[str, SlotSpec] = {}
    for name, item in (raw.get("slots") or {}).items():
        _reject_unknown(item, _SLOT_FIELDS, f"slot {name!r}")
        slots[name] = SlotSpec(
            name=name,
            type=str(item.get("type", "text")),
            prompt=str(item.get("prompt", "")),
            examples=_as_tuple(item.get("examples")),
            pattern=item.get("pattern"),
            sensitive=bool(item.get("sensitive", False)),
            invalid_prompt=item.get("invalid_prompt"),
            missing_reason=item.get("missing_reason"),
        )
    specs: dict[str, IntentSpec] = {}
    for item in raw.get("intents") or []:
        intent_id = str(item["intent_id"])
        _reject_unknown(item, _INTENT_FIELDS, f"intent {intent_id!r}")
        spec = IntentSpec(
            intent_id=intent_id,
            nature=item.get("nature", "none"),
            action=_as_tuple(item.get("action")),
            required_slots=_as_tuple(item.get("required_slots")),
            validation=str(item.get("validation", "none")),
            risk_level=RiskLevel(item.get("risk_level", "R0")),
            no_evidence=item.get("no_evidence", "none"),
            fallback=item.get("fallback", "none"),
            write_path=bool(item.get("write_path", False)),
            priority=int(item.get("priority", 100)),
            cues=_as_tuple(item.get("cues")),
            clarify_prompts=_as_pairs(item.get("clarify_prompts")),
            match_policy_question=bool(item.get("match_policy_question", False)),
            match_catalog=bool(item.get("match_catalog", False)),
            match_order_id=bool(item.get("match_order_id", False)),
            match_request_only=bool(item.get("match_request_only", False)),
            match_analytics_question=bool(item.get("match_analytics_question", False)),
        )
        for slot in spec.required_slots:
            if slot not in slots:
                raise ValueError(f"Intent {intent_id} requires unknown slot {slot}.")
        for slot, _ in spec.clarify_prompts:
            if slot not in slots:
                raise ValueError(f"Intent {intent_id} prompts for unknown slot {slot}.")
        if intent_id in specs:
            raise ValueError(f"Duplicate intent in the catalog: {intent_id}.")
        specs[intent_id] = spec
    if not specs:
        raise ValueError("The intent catalog is empty.")
    return IntentCatalog(version=str(raw.get("version", "0")), specs=specs, slots=slots)


@lru_cache(maxsize=1)
def get_catalog() -> IntentCatalog:
    return _load(_CATALOG_PATH)


class _CatalogProxy:
    """Lazily loaded module-level catalog."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_catalog(), name)


catalog = _CatalogProxy()
