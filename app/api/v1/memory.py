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
    ProactiveRuleChangeListResponse,
    ProactiveRuleChangeView,
    ProactiveRuleDecisionRequest,
    ProactiveRuleListResponse,
    ProactiveRuleRollbackRequest,
    ProactiveRuleUpdateRequest,
    ProactiveRuleVersionListResponse,
)
from app.services.memory import MemoryService
from app.services.proactive import ProactiveService

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


def _require_seat(identity: Identity) -> None:
    if not identity.has_role("support"):
        raise ForbiddenError("Support role required.")


def _require_staff(identity: Identity) -> None:
    if not identity.has_role("support") and not identity.has_role("approver"):
        raise ForbiddenError("Support or approver role required.")


@router.get("", response_model=MemoryProfileResponse)
def get_memory(
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """读调用者自己的记忆画像；只能读自己的。"""
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
    """导出调用者自己的画像与通知，供客户带走自己的数据。"""
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
    """列调用者自己的主动通知，最新的在前。"""
    rows = ProactiveService(session, identity).list_notifications(limit=limit)
    return {"notifications": rows}


@router.post("/notifications/{notification_id}/confirm", response_model=NotificationView)
def confirm_notification(
    notification_id: str,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """确认一条待确认通知；确认后才写进外发队列。"""
    return ProactiveService(session, identity).confirm(notification_id)


@router.post("/notifications/{notification_id}/cancel", response_model=NotificationView)
def cancel_notification(
    notification_id: str,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """撤销一条还没发出的通知；只能撤自己的。"""
    return ProactiveService(session, identity).cancel(notification_id)


@router.get("/rules", response_model=ProactiveRuleListResponse)
def list_rules(
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """列本租户的主动规则；需要 support 角色。"""
    _require_seat(identity)
    return {"rules": ProactiveService(session, identity).rules()}


@router.get("/rules/changes", response_model=ProactiveRuleChangeListResponse)
def list_rule_changes(
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    """列规则提案，可按状态过滤；需要 support 或 approver 角色。"""
    _require_staff(identity)
    return {"changes": ProactiveService(session, identity).changes(status=status)}


@router.get("/rules/changes/{change_id}", response_model=ProactiveRuleChangeView)
def get_rule_change(
    change_id: str,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """读一条规则提案；需要 support 或 approver 角色。"""
    _require_staff(identity)
    return ProactiveService(session, identity).change(change_id)


@router.post(
    "/rules/changes/{change_id}/approve", response_model=ProactiveRuleChangeView
)
def approve_rule_change(
    change_id: str,
    body: ProactiveRuleDecisionRequest | None = None,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """A second person applies the proposal; the submitter is refused."""
    return ProactiveService(session, identity).approve_change(
        change_id, note=body.note if body is not None else None
    )


@router.post(
    "/rules/changes/{change_id}/reject", response_model=ProactiveRuleChangeView
)
def reject_rule_change(
    change_id: str,
    body: ProactiveRuleDecisionRequest | None = None,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """驳回一条规则提案；需要 approver 角色，提交人不能审自己那条。"""
    return ProactiveService(session, identity).reject_change(
        change_id, note=body.note if body is not None else None
    )


@router.get(
    "/rules/{event_type}/versions", response_model=ProactiveRuleVersionListResponse
)
def list_rule_versions(
    event_type: str,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """列某个事件类型的规则版本历史；需要 support 或 approver 角色。"""
    _require_staff(identity)
    return {"versions": ProactiveService(session, identity).versions(event_type)}


@router.post(
    "/rules/{event_type}/rollback",
    response_model=ProactiveRuleChangeView,
    status_code=202,
)
def rollback_rule(
    event_type: str,
    body: ProactiveRuleRollbackRequest,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Propose restoring a version. The approval path is not bypassed."""
    _require_seat(identity)
    return ProactiveService(session, identity).submit_rollback(
        event_type, body.version, reason=body.reason
    )


@router.put("/rules/{event_type}", response_model=ProactiveRuleChangeView, status_code=202)
def submit_rule_change(
    event_type: str,
    body: ProactiveRuleUpdateRequest,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Propose a change to the words this tenant sends for one event type.

    Nothing is applied here. The seat's proposal waits for another approver;
    until then the live rule and every customer-facing outcome stay as they
    were. The template is still validated here, which is the point of putting
    the surface behind an API.
    """
    _require_seat(identity)
    changes = body.model_dump(exclude_unset=True)
    reason = changes.pop("reason", None)
    return ProactiveService(session, identity).submit_change(
        event_type, changes, reason=reason
    )
