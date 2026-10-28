"""The candidate set: what the model is allowed to say, computed server-side.

The rules read a sentence with cues and patterns. When they cannot decide,
the model used to be asked to name one of six literals — a set that is not
the twelve-row catalog, and with no stated reason for any answer it gave.
This module turns the question around: the server lists the rows *it* has
evidence for, and the model picks among those or says ``NONE``.

Every string in ``Candidate.evidence`` is something the server computed from
the message, the conversation slots, the rule reading or a supplied order
state. Nothing here reads the model's own claim about itself; ``confidence``
is deliberately not an input (see ``.qoder/rules/intent-candidates.md`` §2.4).

The kernel is pure: no clock, no randomness, no database, no model. Same
input, byte-identical output, in a fixed order.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.agent.intent_catalog import IntentCatalog
from app.agent.routing import RoutePlan

__all__ = [
    "Bucket",
    "Candidate",
    "CandidateSet",
    "build_candidates",
    "describe_candidates",
]

Bucket = Literal["high", "medium", "low"]

# Rows that are always offered: asking for a human and "I did not read this"
# need no evidence to be a legitimate answer, and they never take a slot from
# the candidate cap.
_UNCONDITIONAL = frozenset({"HUMAN_HANDOFF", "UNKNOWN"})

# Evidence that names a row. Slots and order state do not: an order id only
# says "every order-scoped intent is possible", which is not information.
_CONTENT_KINDS = frozenset({"rule", "cue", "context"})

_MAX_CUES = 3
_DEFAULT_MAX_CANDIDATES = 6
_BUCKET_RANK: dict[Bucket, int] = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True, slots=True)
class Candidate:
    """One catalog row the server has evidence for."""

    intent_id: str
    evidence: tuple[str, ...]
    bucket: Bucket


@dataclass(frozen=True, slots=True)
class CandidateSet:
    """The closed answer space for one model call.

    ``narrowed`` says which kind of set this is: ``True`` when the server had
    evidence to shortlist rows, ``False`` when it had none and the model sees
    the whole catalog (row ids only, never the row definitions). Reporting it
    matters — a no-signal set is not a shortlist, and counting it as one would
    make the rules look more certain than they are.
    """

    candidates: tuple[Candidate, ...]
    allow_none: bool = True
    narrowed: bool = True

    def ids(self) -> tuple[str, ...]:
        return tuple(candidate.intent_id for candidate in self.candidates)

    def allows(self, intent_id: str) -> bool:
        """``NONE`` is a legal answer but never a candidate, so it is refused here."""
        return any(candidate.intent_id == intent_id for candidate in self.candidates)

    def bucket_of(self, intent_id: str) -> str | None:
        for candidate in self.candidates:
            if candidate.intent_id == intent_id:
                return candidate.bucket
        return None


def _bucket(evidence: tuple[str, ...]) -> Bucket:
    kinds = {term.split(":", 1)[0] for term in evidence}
    if len(kinds) >= 2 and kinds & {"rule", "cue"}:
        return "high"
    if len(kinds) == 1 and kinds <= {"rule", "cue"}:
        return "medium"
    return "low"


def _evidence(
    intent_id: str,
    text: str,
    context: Mapping[str, object],
    route: RoutePlan,
    order_state: str | None,
    catalog: IntentCatalog,
) -> tuple[str, ...]:
    spec = catalog.get(intent_id)
    parts: list[str] = []
    if route.intent.value == intent_id and intent_id != "UNKNOWN":
        parts.append(f"rule:{intent_id}")
    hits = [cue for cue in spec.cues if cue and cue in text]
    parts.extend(f"cue:{cue}" for cue in hits[:_MAX_CUES])
    if route.order_id and "order_id" in spec.required_slots:
        parts.append("slot:order_id")
    if route.amount_cents is not None and "amount_cents" in spec.required_slots:
        parts.append("slot:amount_cents")
    for key in ("pending_intent", "last_intent"):
        named = context.get(key)
        if isinstance(named, str) and named.upper() == intent_id:
            parts.append(f"context:{key}")
    if order_state and "order_id" in spec.required_slots:
        parts.append(f"order:{order_state}")
    return tuple(parts)


def build_candidates(
    message: str,
    context: Mapping[str, object] | None,
    route: RoutePlan,
    *,
    catalog: IntentCatalog,
    order_state: str | None = None,
    max_candidates: int = _DEFAULT_MAX_CANDIDATES,
) -> CandidateSet:
    """List the catalog rows this message has server-side evidence for.

    ``order_state`` is fetched by the caller: the kernel never touches the
    database. When a row is reached, candidates come back in catalog priority
    order and the cap truncates the *evidence-bearing* rows only, bucket first
    then priority.

    When no row has content evidence the set is **not** narrowed: the model
    gets every row id in the catalog (``narrowed=False``). Narrowing to
    ``HUMAN_HANDOFF`` plus ``UNKNOWN`` would be a shortlist of nothing, and the
    model's job on exactly these messages is to read what the rules could not.
    A no-signal set is reported as such so no evaluation can count it as a
    successful shortlist.
    """
    slots: Mapping[str, object] = context or {}
    text = message.lower()
    evidenced: list[Candidate] = []
    blind: list[Candidate] = []
    unconditional: list[Candidate] = []
    for spec in catalog.ordered_specs():
        evidence = _evidence(spec.intent_id, text, slots, route, order_state, catalog)
        candidate = Candidate(
            intent_id=spec.intent_id,
            evidence=evidence,
            bucket=_bucket(evidence),
        )
        if spec.intent_id in _UNCONDITIONAL:
            unconditional.append(candidate)
        elif any(e.split(":", 1)[0] in _CONTENT_KINDS for e in evidence):
            evidenced.append(candidate)
        else:
            blind.append(candidate)

    if not evidenced:
        return _ordered(unconditional + blind, catalog, narrowed=False)

    kept = evidenced
    if max_candidates >= 0 and len(evidenced) > max_candidates:
        ranked = sorted(
            evidenced,
            key=lambda candidate: (
                _BUCKET_RANK[candidate.bucket],
                catalog.get(candidate.intent_id).priority,
            ),
        )
        kept = ranked[:max_candidates]

    return _ordered(unconditional + kept, catalog, narrowed=True)


def _ordered(
    chosen: list[Candidate], catalog: IntentCatalog, *, narrowed: bool
) -> CandidateSet:
    chosen.sort(
        key=lambda candidate: (
            catalog.get(candidate.intent_id).priority,
            candidate.intent_id,
        )
    )
    return CandidateSet(candidates=tuple(chosen), allow_none=True, narrowed=narrowed)


def describe_candidates(
    candidate_set: CandidateSet, *, catalog: IntentCatalog
) -> list[dict[str, Any]]:
    """L2: the definitions behind the candidates, on demand only.

    Nothing in this round feeds the result into a prompt. It exists so the
    answer path can ask "what does this candidate actually mean" without
    injecting the whole catalog (L3), which is never sent.
    """
    described: list[dict[str, Any]] = []
    for candidate in candidate_set.candidates:
        spec = catalog.get(candidate.intent_id)
        described.append(
            {
                "intent_id": spec.intent_id,
                "bucket": candidate.bucket,
                "validation": spec.validation,
                "required_slots": tuple(spec.required_slots),
                "fallback": spec.fallback,
                "actions": tuple(spec.action),
            }
        )
    return described
