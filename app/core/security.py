from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header

from app.core.errors import ForbiddenError, UnauthorizedError


@dataclass(frozen=True, slots=True)
class Identity:
    tenant_id: str
    user_id: str
    roles: frozenset[str]

    def has_role(self, role: str) -> bool:
        return role in self.roles or "admin" in self.roles


def get_identity(
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_roles: str | None = Header(default=None),
) -> Identity:
    """Dev header adapter. Replace with a verified token adapter in production."""
    if not x_tenant_id or not x_user_id:
        raise UnauthorizedError("Missing identity headers X-Tenant-Id / X-User-Id.")
    roles = frozenset(part.strip() for part in (x_roles or "").split(",") if part.strip())
    return Identity(tenant_id=x_tenant_id, user_id=x_user_id, roles=roles)


def require_approver(identity: Identity) -> None:
    if not identity.has_role("approver"):
        raise ForbiddenError("Approver role required.")
