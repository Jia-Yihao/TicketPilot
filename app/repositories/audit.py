from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditEvent


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            AuditEvent(
                id=f"aud_{uuid.uuid4().hex[:16]}",
                actor_id=actor_id,
                tenant_id=tenant_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome,
                detail=detail or {},
            )
        )
        self.session.flush()
