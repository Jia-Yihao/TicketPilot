"""The escalation trigger table: when the agent stops and reaches for a human.

The eight signals live in ``escalation.yaml`` so the triggers, the replies and
the queue routing can be reviewed and grey-released without touching code.
The *numbers* the signals compare against stay in the policy engine
(``escalation.*``); this module only says which number to compare.

A signal is a rule, not a branch: ``trigger`` decides how it fires and the
rest of the row says what happens when it does.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from app.models.enums import HandoffReason

__all__ = [
    "EscalationSignal",
    "EscalationCatalog",
    "get_escalation_catalog",
]

Trigger = Literal["intent", "cue", "threshold", "no_evidence", "risk_level"]

_CATALOG_PATH = Path(__file__).with_name("escalation.yaml")

_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}


@dataclass(frozen=True, slots=True)
class EscalationSignal:
    """One rule of the trigger table."""

    signal_id: str
    priority: str
    reason: HandoffReason
    trigger: Trigger
    phase: Literal["pre", "post"] = "pre"
    counter: str | None = None
    intents: tuple[str, ...] = ()
    cues: tuple[str, ...] = ()
    threshold: str | None = None
    risk_level: str | None = None
    queue: str = "GENERAL"
    takeover: bool = True
    soothe: bool = False
    continue_task: bool = False
    reply_mode: Literal["replace", "append"] = "replace"
    reply: str = ""
    next_step: str = ""

    @property
    def rank(self) -> int:
        return _PRIORITY_RANK.get(self.priority, 3)

    def applies_to(self, intent_id: str) -> bool:
        """True when the signal is declared for this intent (or for any)."""
        return not self.intents or intent_id in self.intents

    def matches_cue(self, text: str) -> bool:
        """True when the message contains one of the signal's cues."""
        return any(cue in text for cue in self.cues)


class EscalationCatalog:
    """The loaded trigger table."""

    def __init__(self, version: str, signals: tuple[EscalationSignal, ...]) -> None:
        self.version = version
        self.signals = signals

    def get(self, signal_id: str) -> EscalationSignal:
        for signal in self.signals:
            if signal.signal_id == signal_id:
                return signal
        raise KeyError(f"Escalation signal is not in the table: {signal_id}.")

    def by_trigger(self, trigger: str) -> tuple[EscalationSignal, ...]:
        return tuple(signal for signal in self.signals if signal.trigger == trigger)

    def by_phase(self, phase: str) -> tuple[EscalationSignal, ...]:
        return tuple(signal for signal in self.signals if signal.phase == phase)

    def intent_signals(self, intent_id: str) -> tuple[EscalationSignal, ...]:
        """Signals that name this intent explicitly."""
        return tuple(signal for signal in self.signals if intent_id in signal.intents)

    def ordered(self) -> tuple[EscalationSignal, ...]:
        """Every signal, most urgent first; the table order breaks ties."""
        return tuple(
            sorted(
                self.signals,
                key=lambda signal: (signal.rank, self.signals.index(signal)),
            )
        )


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _load(path: Path) -> EscalationCatalog:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("The escalation table must be a mapping.")
    signals: list[EscalationSignal] = []
    seen: set[str] = set()
    for item in raw.get("signals") or []:
        signal_id = str(item["signal_id"])
        if signal_id in seen:
            raise ValueError(f"Duplicate escalation signal: {signal_id}.")
        seen.add(signal_id)
        phase = str(item.get("phase", "pre"))
        if phase not in {"pre", "post"}:
            raise ValueError(f"Escalation signal {signal_id} has an unknown phase.")
        signals.append(
            EscalationSignal(
                signal_id=signal_id,
                priority=str(item.get("priority", "P1")),
                reason=HandoffReason(str(item.get("reason", "OTHER"))),
                trigger=str(item.get("trigger", "cue")),  # type: ignore[arg-type]
                phase=phase,  # type: ignore[arg-type]
                counter=item.get("counter"),
                intents=_as_tuple(item.get("intents")),
                cues=_as_tuple(item.get("cues")),
                threshold=item.get("threshold"),
                risk_level=item.get("risk_level"),
                queue=str(item.get("queue", "GENERAL")),
                takeover=bool(item.get("takeover", True)),
                soothe=bool(item.get("soothe", False)),
                continue_task=bool(item.get("continue_task", False)),
                reply_mode=str(item.get("reply_mode", "replace")),  # type: ignore[arg-type]
                reply=str(item.get("reply", "")),
                next_step=str(item.get("next_step", "")),
            )
        )
    if not signals:
        raise ValueError("The escalation table is empty.")
    return EscalationCatalog(
        version=str(raw.get("version", "0")), signals=tuple(signals)
    )


@lru_cache(maxsize=1)
def get_escalation_catalog() -> EscalationCatalog:
    return _load(_CATALOG_PATH)
