"""The refund reason keyword table: which enum member a customer's words map to.

The table lives in ``refund_reason_rules.yaml`` so the mapping can be reviewed
without touching code. Like the escalation table, the row order *is* the
priority: the first row that matches decides, and a miss leaves the slot empty
rather than falling back to ``OTHER``.

``other_cues`` is the separate "the customer said other" table. It is checked
*before* the rule rows, because what the customer said outranks what the words
happen to match.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.models.enums import RefundReason

__all__ = [
    "RefundReasonRule",
    "RefundReasonRules",
    "get_refund_reason_rules",
    "load_refund_reason_rules",
    "match_refund_reason",
]

_RULES_PATH = Path(__file__).with_name("refund_reason_rules.yaml")

_TOP_FIELDS = frozenset({"version", "slot", "enum", "other_cues", "rules"})
_RULE_FIELDS = frozenset({"refund_reason", "cues"})
_SLOT = "refund_reason"
_ENUM = "RefundReason"


@dataclass(frozen=True, slots=True)
class RefundReasonRule:
    """One row of the keyword table."""

    refund_reason: RefundReason
    cues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RefundReasonRules:
    """The loaded keyword table."""

    version: str
    slot: str
    enum: str
    other_cues: tuple[str, ...]
    rules: tuple[RefundReasonRule, ...]

    def match(self, text: str) -> RefundReason | None:
        """The customer's own "other" wins; then the first matching row decides.

        A miss returns nothing at all: an empty slot is a clarification, while
        ``OTHER`` is a claim about the customer's reason.
        """
        lowered = text.lower()
        if any(cue in lowered for cue in self.other_cues):
            return RefundReason.OTHER
        for rule in self.rules:
            if any(cue in lowered for cue in rule.cues):
                return rule.refund_reason
        return None


def _reject_unknown(
    allowed: frozenset[str], mapping: dict[str, Any], where: str
) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ValueError(f"{where} has unknown keys: {', '.join(unknown)}.")


def load_refund_reason_rules(path: Path) -> RefundReasonRules:
    """Read and validate one keyword table.

    Every rule that breaks the contract is an error, not a skipped row: a
    silently dropped row reads as "no reason found" and would send the
    customer into a clarification loop.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("The refund reason table must be a mapping.")
    _reject_unknown(_TOP_FIELDS, raw, "The refund reason table")

    slot = str(raw.get("slot", ""))
    if slot != _SLOT:
        raise ValueError(
            f"The refund reason table must fill slot '{_SLOT}', not '{slot}'."
        )
    enum_name = str(raw.get("enum", ""))
    if enum_name != _ENUM:
        raise ValueError(
            f"The refund reason table must fill enum '{_ENUM}', not '{enum_name}'."
        )

    raw_rules = raw.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("The refund reason table has no rules.")

    raw_other = raw.get("other_cues")
    if not isinstance(raw_other, list) or not raw_other:
        raise ValueError("The refund reason table has no other_cues.")
    other_cues: list[str] = []
    for cue in raw_other:
        if not isinstance(cue, str) or not cue.strip():
            raise ValueError("other_cues must not contain an empty cue.")
        if cue != cue.lower():
            raise ValueError(f"The cue '{cue}' must be lowercase.")
        if cue in other_cues:
            raise ValueError(f"The other_cues entry '{cue}' is duplicated.")
        other_cues.append(cue)

    rules: list[RefundReasonRule] = []
    for item in raw_rules:
        if not isinstance(item, dict):
            raise ValueError("Every refund reason rule must be a mapping.")
        _reject_unknown(_RULE_FIELDS, item, "A refund reason rule")
        target = str(item.get("refund_reason", ""))
        if target == RefundReason.OTHER.value:
            raise ValueError(
                "'OTHER' cannot be a rule target: it is an explicit choice, not a fallback."
            )
        try:
            refund_reason = RefundReason(target)
        except ValueError as error:
            raise ValueError(f"'{target}' is not a { _ENUM } member.") from error
        cues = item.get("cues")
        if not isinstance(cues, list) or not cues:
            raise ValueError(f"The rule '{target}' has no cues.")
        cleaned: list[str] = []
        for cue in cues:
            if not isinstance(cue, str) or not cue.strip():
                raise ValueError(f"The rule '{target}' has an empty cue.")
            if cue != cue.lower():
                raise ValueError(f"The cue '{cue}' must be lowercase.")
            cleaned.append(cue)
        rules.append(
            RefundReasonRule(refund_reason=refund_reason, cues=tuple(cleaned))
        )

    return RefundReasonRules(
        version=str(raw.get("version", "0")),
        slot=slot,
        enum=enum_name,
        other_cues=tuple(other_cues),
        rules=tuple(rules),
    )


@lru_cache(maxsize=1)
def get_refund_reason_rules() -> RefundReasonRules:
    """The process-wide keyword table."""
    return load_refund_reason_rules(_RULES_PATH)


def match_refund_reason(text: str) -> RefundReason | None:
    """Map a customer's words onto a refund reason, or nothing at all."""
    return get_refund_reason_rules().match(text)
