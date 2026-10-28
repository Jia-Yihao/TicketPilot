"""The approval tier table: which approval spec an amount falls into.

The table lives in ``approval_tiers.yaml`` so the approval spec can be
reviewed and changed without touching code. This module reads it, validates
it, and resolves an amount onto a tier.

Two questions, two sources. ``PolicyEngine.requires_approval`` answers
"does this amount need a human at all?" (``refund_approval_threshold_cents``);
this table answers "which spec does that human approve under?". A tier's
``max_cents`` never decides whether an approval is required, and the two
judgements never substitute for each other.

Everything here is a pure function of the file: no clock, no randomness, no
model, no database. The same amount resolves to the same snapshot twice, and
the snapshot is frozen once it has been resolved.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "ApprovalTier",
    "ApprovalTierTable",
    "TierResolution",
    "get_approval_tiers",
    "load_approval_tiers",
]

_TIERS_PATH = Path(__file__).with_name("approval_tiers.yaml")

_TOP_FIELDS = frozenset({"version", "tiers"})
_TIER_FIELDS = frozenset({"tier_id", "max_cents", "roles", "required_approvals"})

# The only role the approval check knows how to honour (``require_approver``).
_ALLOWED_ROLES = frozenset({"approver"})

# One signature per refund: this round does not do multi-stage countersigning.
_REQUIRED_APPROVALS = 1

# Upper snake case, e.g. ``T1`` or ``T2_LARGE``. ``\Z`` rather than ``$``:
# ``$`` also matches just before a trailing newline, so ``"T1\n"`` would
# slip through as a well-formed id.
_TIER_ID_PATTERN = re.compile(r"\A[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*\Z")


def _normalize_roles(roles: Iterable[str]) -> tuple[str, ...]:
    """The canonical writing order of a role set.

    The digest sorts keys, not the values inside a list, so the order of a
    role list travels into the digest verbatim. Normalising once gives one
    spelling per role set, so the same spec cannot drift the digest just
    because someone wrote the roles in another order.
    """
    return tuple(sorted(roles))


@dataclass(frozen=True, slots=True)
class ApprovalTier:
    """One row of the tier table.

    The roles are normalised on construction, so every tier - loaded or built
    by hand - carries the role set in its canonical order.
    """

    tier_id: str
    max_cents: int
    roles: tuple[str, ...]
    required_approvals: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "roles", _normalize_roles(self.roles))

    def snapshot(self) -> dict[str, Any]:
        """The frozen four-tuple that goes into the approval digest."""
        return {
            "tier_id": self.tier_id,
            "max_cents": self.max_cents,
            "roles": list(self.roles),
            "required_approvals": self.required_approvals,
        }


@dataclass(frozen=True, slots=True)
class TierResolution:
    """Which tier an amount falls into, and whether the table had to fall back."""

    tier: ApprovalTier
    fell_back: bool

    def snapshot(self) -> dict[str, Any]:
        return self.tier.snapshot()


@dataclass(frozen=True, slots=True)
class ApprovalTierTable:
    """The loaded tier table."""

    version: str
    tiers: tuple[ApprovalTier, ...]

    def __post_init__(self) -> None:
        if not self.tiers:
            raise ValueError("The approval tier table is empty.")

    def tier_for(self, amount_cents: int) -> TierResolution:
        """The tier an amount falls into.

        The first tier whose ``max_cents`` covers the amount wins. An amount
        above every tier falls back to the *highest* tier and says so:
        dropping a large refund onto the lowest spec is the most dangerous
        reading available.
        """
        for tier in self.tiers:
            if amount_cents <= tier.max_cents:
                return TierResolution(tier=tier, fell_back=False)
        return TierResolution(tier=self.tiers[-1], fell_back=True)


def _reject_unknown(allowed: frozenset[str], mapping: dict[str, Any], where: str) -> None:
    """A field the loader does not know is a typo, and a typo would silently
    change which tier an amount falls into."""
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ValueError(f"{where} has unknown keys: {', '.join(unknown)}.")


def _read_tier_id(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{where} has no tier_id.")
    if not _TIER_ID_PATTERN.match(raw):
        raise ValueError(f"{where} has a tier_id that is not upper snake case: {raw}.")
    return raw


def _read_max_cents(raw: Any, where: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{where} must have an integer max_cents.")
    if raw < 0:
        raise ValueError(f"{where} must not have a negative max_cents.")
    return int(raw)


def _read_roles(raw: Any, where: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{where} has no roles.")
    roles: list[str] = []
    for role in raw:
        if not isinstance(role, str) or not role.strip():
            raise ValueError(f"{where} has an empty role.")
        if role not in _ALLOWED_ROLES:
            raise ValueError(f"{where} names a role that is not allowed: {role}.")
        if role in roles:
            raise ValueError(f"{where} repeats the role {role}.")
        roles.append(role)
    return tuple(roles)


def _read_required_approvals(raw: Any, where: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{where} must have an integer required_approvals.")
    if raw != _REQUIRED_APPROVALS:
        raise ValueError(
            f"{where} must require exactly {_REQUIRED_APPROVALS} approval; "
            f"multi-stage countersigning is out of scope."
        )
    return int(raw)


def load_approval_tiers(path: Path) -> ApprovalTierTable:
    """Read and validate one tier table.

    Every row that breaks the contract is an error, not a skipped row: a
    silently dropped row would resolve to a different tier than the operator
    wrote, and the digest would approve a spec nobody reviewed.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("The approval tier table must be a mapping.")
    _reject_unknown(_TOP_FIELDS, raw, "The approval tier table")

    raw_tiers = raw.get("tiers")
    if not isinstance(raw_tiers, list) or not raw_tiers:
        raise ValueError("The approval tier table has no tiers.")

    tiers: list[ApprovalTier] = []
    seen_ids: set[str] = set()
    previous_max: int | None = None
    for index, item in enumerate(raw_tiers):
        where = f"Approval tier {index + 1}"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be a mapping.")
        _reject_unknown(_TIER_FIELDS, item, where)

        tier_id = _read_tier_id(item.get("tier_id"), where)
        if tier_id in seen_ids:
            raise ValueError(f"{where} repeats the tier_id {tier_id}.")
        seen_ids.add(tier_id)

        max_cents = _read_max_cents(item.get("max_cents"), where)
        if previous_max is not None and max_cents <= previous_max:
            raise ValueError(
                f"{where} has max_cents {max_cents}, which does not increase past "
                f"{previous_max}; the table must ascend by max_cents."
            )
        previous_max = max_cents

        # The roles are normalised by ApprovalTier itself, so the loaded tier
        # and its snapshot carry one canonical spelling of the role set.
        roles = _read_roles(item.get("roles"), where)
        required_approvals = _read_required_approvals(item.get("required_approvals"), where)

        tiers.append(
            ApprovalTier(
                tier_id=tier_id,
                max_cents=max_cents,
                roles=roles,
                required_approvals=required_approvals,
            )
        )

    return ApprovalTierTable(version=str(raw.get("version", "0")), tiers=tuple(tiers))


@lru_cache(maxsize=1)
def get_approval_tiers() -> ApprovalTierTable:
    """The process-wide tier table."""
    return load_approval_tiers(_TIERS_PATH)
