"""The seat side of a handoff.

The customer-facing API says "a human will take over"; this API is what the
human actually works with: the queue, the context package, the actions that
move the case along, and the numbers that show whether the agent is pulling
its weight.

Every endpoint is staff-only and tenant-scoped: a seat of another tenant sees
a missing case, not somebody else's customer.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_policy, get_session
from app.core.errors import ForbiddenError, ValidationAppError
from app.core.policy import PolicyEngine
from app.core.security import Identity, get_identity
from app.models.enums import HandoffStatus
from app.schemas.handoffs import (
    AbandonListResponse,
    AbandonSummary,
    HandoffActionRequest,
    HandoffDetail,
    HandoffMetricsResponse,
    HandoffQueueResponse,
)
from app.services.handoff import (
    OPEN_CASE_STATES,
    AbandonService,
    HandoffMetrics,
    HandoffService,
)

router = APIRouter(prefix="/api/v1/handoffs", tags=["handoffs"])


def _require_seat(identity: Identity) -> None:
    """The queue and the case files are staff-only."""
    if not identity.has_role("support"):
        raise ForbiddenError("Support role required.")


def _parse_statuses(raw: list[str] | None) -> list[HandoffStatus] | None:
    if not raw:
        return None
    try:
        return [HandoffStatus(item.upper()) for item in raw]
    except ValueError as exc:
        raise ValidationAppError(
            "Unknown handoff status.",
            details={"allowed": [status.value for status in HandoffStatus]},
        ) from exc


@router.get("/metrics", response_model=HandoffMetricsResponse)
def handoff_metrics(
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    policy: PolicyEngine = Depends(get_policy),
    window_minutes: int | None = Query(default=None, ge=1, le=60 * 24 * 90),
) -> dict[str, Any]:
    """现算转人工相关指标；需要 support 角色，窗口外的数据不计入分母。"""
    _require_seat(identity)
    return HandoffMetrics(session, policy=policy).collect(
        identity, window_minutes=window_minutes
    )


@router.get("/abandons", response_model=AbandonListResponse)
def list_abandons(
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    policy: PolicyEngine = Depends(get_policy),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """列本租户的弃单事件；需要 support 角色。"""
    _require_seat(identity)
    abandons = AbandonService(session, policy=policy)
    events = abandons.list_events(identity, limit=limit, offset=offset)
    return {
        "items": [
            AbandonSummary(
                event_id=event.id,
                conversation_id=event.conversation_id,
                intent=event.intent,
                channel=event.channel,
                reason=event.reason,
                idle_minutes=event.idle_minutes,
                created_at=event.created_at.isoformat(),
            ).model_dump()
            for event in events
        ],
        "total": abandons.count_events(identity),
    }


@router.get("", response_model=HandoffQueueResponse)
def list_handoffs(
    status: list[str] | None = Query(default=None),
    queue: str | None = Query(default=None, max_length=32),
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    policy: PolicyEngine = Depends(get_policy),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """列坐席队列，默认只看还需要人处理的案卷；需要 support 角色。"""
    _require_seat(identity)
    handoffs = HandoffService(session, policy=policy)
    # A pending case nobody claimed is already late; show the queue as it is.
    handoffs.expire_pending()
    statuses = _parse_statuses(status)
    if statuses is None:
        statuses = list(OPEN_CASE_STATES)
    events = handoffs.list_queue(
        identity, statuses=statuses, queue=queue, limit=limit, offset=offset
    )
    return {
        "items": [handoffs.summary(event) for event in events],
        "total": handoffs.count_queue(identity, statuses=statuses, queue=queue),
        "limit": limit,
        "offset": offset,
    }


@router.get("/{event_id}", response_model=HandoffDetail)
def get_handoff(
    event_id: str,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    policy: PolicyEngine = Depends(get_policy),
) -> dict[str, Any]:
    """读一条移交案卷的完整上下文包；需要 support 角色。"""
    _require_seat(identity)
    handoffs = HandoffService(session, policy=policy)
    return handoffs.detail(handoffs.require_event(identity, event_id))


@router.post("/{event_id}/claim", response_model=HandoffDetail)
def claim_handoff(
    event_id: str,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    policy: PolicyEngine = Depends(get_policy),
) -> dict[str, Any]:
    """坐席认领一个案卷，会话随之进入接管；已被认领则冲突。"""
    _require_seat(identity)
    handoffs = HandoffService(session, policy=policy)
    event = handoffs.claim(identity, event_id)
    session.commit()
    return handoffs.detail(event)


@router.post("/{event_id}/release", response_model=HandoffDetail)
def release_handoff(
    event_id: str,
    body: HandoffActionRequest | None = None,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    policy: PolicyEngine = Depends(get_policy),
) -> dict[str, Any]:
    """把会话交回智能体，并留下交接备注；需要 support 角色。"""
    _require_seat(identity)
    handoffs = HandoffService(session, policy=policy)
    note = body.note if body and body.note else ""
    event = handoffs.release(identity, event_id, note=note)
    session.commit()
    return handoffs.detail(event)


@router.post("/{event_id}/close", response_model=HandoffDetail)
def close_handoff(
    event_id: str,
    body: HandoffActionRequest | None = None,
    identity: Identity = Depends(get_identity),
    session: Session = Depends(get_session),
    policy: PolicyEngine = Depends(get_policy),
) -> dict[str, Any]:
    """结案并写结论；需要 support 角色。"""
    _require_seat(identity)
    handoffs = HandoffService(session, policy=policy)
    resolution = body.resolution if body and body.resolution else "RESOLVED"
    event = handoffs.close(identity, event_id, resolution=resolution)
    session.commit()
    return handoffs.detail(event)
