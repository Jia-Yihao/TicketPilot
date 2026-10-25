"""Customer-owned memory and the notifications it unlocks.

The read/write/export/delete surface belongs to the customer who owns the
profile. Emitting an event is a seat action: support supplies the fact,
the rule renders the words, and the customer still has to say yes before
anything leaves the system.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.core.errors import ForbiddenError
from app.core.security import Identity, get_identity
from app.schemas.memory import (
    MemoryExportResponse,
    MemoryProfileResponse,
    MemoryUpdateRequest,
    NotificationListResponse,
    NotificationView,
    ProactiveEventRequest,
    ProactiveEventResponse,
    ProactiveRuleListResponse,
    ProactiveRuleUpdateRequest,
    ProactiveRuleView,
)
from app.services.memory import MemoryService
from app.services.proactive import ProactiveService

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


def _require_seat(identity: Identity) -> None:
    if not identity.has_role("support"):
        raise ForbiddenError("Support role required.")


@router.get("", response_model=MemoryProfileResponse)
def get_memory(
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return MemoryService(session, identity).get()


@router.put("", response_model=MemoryProfileResponse)
def update_memory(
    body: MemoryUpdateRequest,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Only what the customer sends is written; unset fields stay untouched."""
    changes = body.model_dump(exclude_unset=True)
    return MemoryService(session, identity).update(changes)


@router.delete("", status_code=204)
def delete_memory(
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> Response:
    """Erase the profile and its notifications, and cancel queued sends."""
    MemoryService(session, identity).delete()
    return Response(status_code=204)


@router.get("/export", response_model=MemoryExportResponse)
def export_memory(
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return MemoryService(session, identity).export()


@router.post("/events", response_model=ProactiveEventResponse)
def emit_event(
    body: ProactiveEventRequest,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """A fact happened to ``user_id``; the tenant rule decides whether to speak."""
    _require_seat(identity)
    return ProactiveService(session, identity).emit(
        target_user_id=body.user_id,
        event_type=body.event_type,
        event_key=body.event_key,
        context=body.context,
    )


@router.get("/notifications", response_model=NotificationListResponse)
def list_notifications(
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    rows = ProactiveService(session, identity).list_notifications(limit=limit)
    return {"notifications": rows}


@router.post("/notifications/{notification_id}/confirm", response_model=NotificationView)
def confirm_notification(
    notification_id: str,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return ProactiveService(session, identity).confirm(notification_id)


@router.post("/notifications/{notification_id}/cancel", response_model=NotificationView)
def cancel_notification(
    notification_id: str,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return ProactiveService(session, identity).cancel(notification_id)


@router.get("/rules", response_model=ProactiveRuleListResponse)
def list_rules(
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_seat(identity)
    return {"rules": ProactiveService(session, identity).rules()}


@router.put("/rules/{event_type}", response_model=ProactiveRuleView)
def update_rule(
    event_type: str,
    body: ProactiveRuleUpdateRequest,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Change the words this tenant sends for one known event type.

    Upsert by ``(tenant, event_type)``, so a tenant the seed never covered can
    turn proactive service on without a code change. The template is validated
    here, which is the point of putting the surface behind an API.
    """
    _require_seat(identity)
    changes = body.model_dump(exclude_unset=True)
    return ProactiveService(session, identity).update_rule(event_type, changes)
