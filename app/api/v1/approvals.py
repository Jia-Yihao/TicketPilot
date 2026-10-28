from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_runtime
from app.core.errors import ValidationAppError
from app.core.security import Identity, get_identity
from app.models.enums import ApprovalStatus
from app.schemas.approvals import ApprovalDetailResponse, ApprovalListResponse
from app.services.runtime import TaskRuntime

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


def _parse_statuses(raw: list[str] | None) -> list[ApprovalStatus] | None:
    if not raw:
        return None
    try:
        return [ApprovalStatus(item.upper()) for item in raw]
    except ValueError as exc:
        raise ValidationAppError(
            "Unknown approval status.",
            details={"allowed": [status.value for status in ApprovalStatus]},
        ) from exc


@router.get("", response_model=ApprovalListResponse)
def list_approvals(
    status: list[str] | None = Query(default=None),
    identity: Identity = Depends(get_identity),
    runtime: TaskRuntime = Depends(get_runtime),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """列本租户的审批单，可按状态过滤；需要 approver 角色。"""
    return runtime.list_approvals(
        identity,
        statuses=_parse_statuses(status),
        limit=limit,
        offset=offset,
    )


@router.get("/{approval_id}", response_model=ApprovalDetailResponse)
def get_approval(
    approval_id: str,
    identity: Identity = Depends(get_identity),
    runtime: TaskRuntime = Depends(get_runtime),
) -> dict[str, Any]:
    """读一条审批详情，多出载荷摘要与坐席可见的补充说明；需要 approver 角色。"""
    return runtime.get_approval(identity, approval_id)
