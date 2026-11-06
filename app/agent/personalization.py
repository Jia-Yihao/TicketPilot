"""The only place customer memory touches an answer.

The project keeps this module narrow on purpose: an answer that reads a
table the customer can rewrite at any moment makes the reply wording
undebuggable. This is the narrow version of that door. Everything here is an
*overlay* — a server-owned template or a change of shape — so the evidence
inside a reply (cards, task receipts, grounded sentences) is never rewritten,
and a customer without a profile gets a byte-identical answer.

Two rules the rest of the file exists to hold:

* customer free text does not reach the reply. ``history_summary`` is never
  read here, ``contact_window`` is never echoed, and an interest only counts
  once the catalog recognises it — the name that goes on screen is the
  catalog's, not the customer's;
* this path is read-only. It selects columns rather than an entity, so no row
  can be created, loaded into the session, or stamped with a new
  ``updated_at``, and it writes no audit event: ``MemoryService.get()`` audits
  a read, and an agent that answered every question would add one audit row
  per turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.turns import Draft
from app.core.security import Identity
from app.models.memory import CustomerProfile
from app.services.catalog import CatalogProduct, CatalogService

__all__ = [
    "ADDRESSED",
    "CARDS_REORDERED",
    "EFFECT_FIELDS",
    "INTEREST_SUGGESTION",
    "PersonalizationResult",
    "ProfileSnapshot",
    "SUGGESTIONS_TRIMMED",
    "load_profile",
    "personalize",
]

# The four effect codes. They are what the response body, the trace span and
# the console agree on, so they are part of the contract, not log noise.
ADDRESSED = "ADDRESSED"
CARDS_REORDERED = "CARDS_REORDERED"
SUGGESTIONS_TRIMMED = "SUGGESTIONS_TRIMMED"
INTEREST_SUGGESTION = "INTEREST_SUGGESTION"

# Which profile field each effect came from. Names only: the values never
# travel with the result, the same discipline the audit trail follows.
EFFECT_FIELDS: dict[str, str] = {
    ADDRESSED: "preferred_name",
    CARDS_REORDERED: "product_interests",
    INTEREST_SUGGESTION: "product_interests",
    SUGGESTIONS_TRIMMED: "tone",
}

_CONCISE_SUGGESTIONS = 2
_ADDRESS_PREFIX = "你好，{name}。"
_INTEREST_SUGGESTION_TEMPLATE = "{name} 的质保范围"


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    """The three profile facts the answer path is allowed to know about."""

    preferred_name: str | None
    tone: str | None
    interests: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not (self.preferred_name or self.tone or self.interests)


@dataclass(slots=True)
class PersonalizationResult:
    effects: list[str] = field(default_factory=list)
    matched_product_id: str | None = None

    @property
    def applied(self) -> bool:
        return bool(self.effects)

    def to_dict(self) -> dict[str, Any]:
        if not self.effects:
            return {}
        return {
            "effects": list(self.effects),
            "fields": sorted({EFFECT_FIELDS[effect] for effect in self.effects}),
        }


def load_profile(session: Session, identity: Identity) -> ProfileSnapshot | None:
    """Read the caller's own profile, or nothing at all.

    Columns, not the entity: a loaded ``CustomerProfile`` would join the
    session the agent is about to commit, and an accidental attribute write
    would persist it with a fresh ``updated_at``.
    """
    row = session.execute(
        select(
            CustomerProfile.preferred_name,
            CustomerProfile.preferences,
        ).where(
            CustomerProfile.tenant_id == identity.tenant_id,
            CustomerProfile.user_id == identity.user_id,
        )
    ).one_or_none()
    if row is None:
        return None
    preferences: Any = row.preferences or {}
    if not isinstance(preferences, dict):
        preferences = {}
    tone = preferences.get("tone")
    raw_interests = preferences.get("product_interests")
    interests = (
        tuple(item for item in raw_interests if isinstance(item, str))
        if isinstance(raw_interests, list)
        else ()
    )
    return ProfileSnapshot(
        preferred_name=row.preferred_name or None,
        tone=tone if isinstance(tone, str) else None,
        interests=interests,
    )


def personalize(
    draft: Draft,
    profile: ProfileSnapshot | None,
    catalog: CatalogService,
    *,
    first_turn: bool,
    product_id: str | None,
) -> PersonalizationResult:
    """Apply the allowed effects to ``draft``, in place, and report which ran.

    ``first_turn`` gates the greeting: a name is used when the conversation
    opens, not once per message. ``product_id`` is the product this turn is
    already about, so an interest in that same product adds nothing.
    """
    result = PersonalizationResult()
    if profile is None or profile.empty:
        return result

    products = _matched_products(profile.interests, catalog)

    if len(draft.cards) > 1:
        _reorder_cards(draft, products, result)

    suggestion = _interest_suggestion(draft, products, product_id)
    if suggestion is not None:
        draft.suggestions.insert(0, suggestion)
        result.effects.append(INTEREST_SUGGESTION)

    if profile.tone == "concise" and len(draft.suggestions) > _CONCISE_SUGGESTIONS:
        draft.suggestions = draft.suggestions[:_CONCISE_SUGGESTIONS]
        result.effects.append(SUGGESTIONS_TRIMMED)

    if first_turn and profile.preferred_name:
        greeting = _ADDRESS_PREFIX.format(name=profile.preferred_name)
        draft.reply = greeting + draft.reply
        result.effects.append(ADDRESSED)

    return result


def _matched_products(
    interests: tuple[str, ...], catalog: CatalogService
) -> list[CatalogProduct]:
    """Interests the catalog recognises, in profile order, one entry per product.

    An interest that is not a product — a typo, a sentence, an injection
    attempt — resolves to nothing and is indistinguishable from absence.
    """
    products: list[CatalogProduct] = []
    seen: set[str] = set()
    for interest in interests:
        product = catalog.resolve(interest)
        if product is not None and product.product_id not in seen:
            seen.add(product.product_id)
            products.append(product)
    return products


def _reorder_cards(
    draft: Draft, products: list[CatalogProduct], result: PersonalizationResult
) -> None:
    wanted = {product.product_id for product in products}
    index = next(
        (
            position
            for position, card in enumerate(draft.cards)
            if card.get("kind") == "product" and card.get("card_id") in wanted
        ),
        None,
    )
    if index is None or index == 0:
        return
    card = draft.cards.pop(index)
    draft.cards.insert(0, card)
    result.matched_product_id = str(card.get("card_id"))
    result.effects.append(CARDS_REORDERED)


def _interest_suggestion(
    draft: Draft, products: list[CatalogProduct], product_id: str | None
) -> str | None:
    """One chip for an interest the answer is not already about.

    A product the customer is already being shown — as the turn's subject or
    as a card — needs no follow-up suggestion about itself.
    """
    answered = {product_id} | {
        str(card.get("card_id")) for card in draft.cards if card.get("kind") == "product"
    }
    for product in products:
        if product.product_id in answered:
            continue
        if any(product.name in item for item in draft.suggestions):
            continue
        return _INTEREST_SUGGESTION_TEMPLATE.format(name=product.name)
    return None
