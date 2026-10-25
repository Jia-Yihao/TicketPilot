"""Security-event reads for the operations console.

The events themselves are written by the input guard, the output guard and
the transport middlewares. This API is staff-only and tenant-scoped: a seat
sees their own tenant's attempts, never another tenant's.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.core.errors import ForbiddenError, ValidationAppError
from app.core.guard import SecurityCategory
from app.core.security import Identity, get_identity
from app.core.time import utcnow
from app.models.security_event import SecurityEvent

router = APIRouter(prefix="/api/v1/security", tags=["security"])

MAX_WINDOW_MINUTES = 60 * 24 * 90
_STAFF_ROLES = ("approver", "support")
_CATEGORIES = {category.value for category in SecurityCategory}


def _require_staff(identity: Identity) -> None:
    if identity.has_role("admin") or any(
        identity.has_role(role) for role in _STAFF_ROLES
    ):
        return
    raise ForbiddenError("Staff role required.")


def _summary(row: SecurityEvent) -> dict[str, Any]:
    return {
        "event_id": row.id,
        "user_id": row.user_id,
        "conversation_id": row.conversation_id,
        "trace_id": row.trace_id,
        "category": row.category,
        "severity": row.severity,
        "action": row.action,
        "rule_id": row.rule_id,
        "source": row.source,
        "excerpt": row.excerpt,
        "detail": row.detail,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/metrics")
def security_metrics(
    window_minutes: int | None = Query(default=None, ge=1, le=MAX_WINDOW_MINUTES),
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Counts by category, action and severity for the dashboard card."""
    _require_staff(identity)
    statement = select(
        SecurityEvent.category,
        SecurityEvent.action,
        SecurityEvent.severity,
        SecurityEvent.created_at,
    ).where(SecurityEvent.tenant_id == identity.tenant_id)
    if window_minutes is not None:
        statement = statement.where(
            SecurityEvent.created_at >= utcnow() - timedelta(minutes=window_minutes)
        )
    rows = session.execute(statement).all()

    by_category: dict[str, int] = {}
    by_action: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_hour: dict[str, int] = {}
    for row in rows:
        by_category[row.category] = by_category.get(row.category, 0) + 1
        by_action[row.action] = by_action.get(row.action, 0) + 1
        by_severity[row.severity] = by_severity.get(row.severity, 0) + 1
        hour = f"{row.created_at.hour:02d}"
        by_hour[hour] = by_hour.get(hour, 0) + 1

    return {
        "window_minutes": window_minutes,
        "total": len(rows),
        "blocked": by_action.get("BLOCKED", 0),
        "redacted": by_action.get("REDACTED", 0),
        "logged": by_action.get("LOGGED", 0),
        "by_category": dict(sorted(by_category.items())),
        "by_action": dict(sorted(by_action.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "by_hour": dict(sorted(by_hour.items())),
    }


@router.get("/events")
def list_security_events(
    category: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """The newest events, newest first, with already-masked excerpts."""
    _require_staff(identity)
    filters = [SecurityEvent.tenant_id == identity.tenant_id]
    if category is not None:
        normalized = category.upper()
        if normalized not in _CATEGORIES:
            raise ValidationAppError(
                "Unknown security category.",
                details={"allowed": sorted(_CATEGORIES)},
            )
        filters.append(SecurityEvent.category == normalized)

    total = session.scalar(
        select(func.count()).select_from(SecurityEvent).where(*filters)
    )
    rows = session.scalars(
        select(SecurityEvent)
        .where(*filters)
        .order_by(SecurityEvent.created_at.desc(), SecurityEvent.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "items": [_summary(row) for row in rows],
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
    }
