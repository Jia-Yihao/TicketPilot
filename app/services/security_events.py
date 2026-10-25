from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.guard import SecurityAction, SecurityCategory, SecuritySeverity
from app.models.security_event import SecurityEvent

__all__ = ["SecurityEventService"]


class SecurityEventService:
    """Write security events; callers own the transaction boundary."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        tenant_id: str,
        category: SecurityCategory | str,
        action: SecurityAction | str,
        severity: SecuritySeverity | str = SecuritySeverity.MEDIUM,
        user_id: str | None = None,
        conversation_id: str | None = None,
        trace_id: str | None = None,
        rule_id: str = "",
        source: str = "input",
        excerpt: str = "",
        detail: dict[str, Any] | None = None,
    ) -> SecurityEvent:
        event = SecurityEvent(
            id=f"sec_{uuid.uuid4().hex[:16]}",
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            category=str(category),
            severity=str(severity),
            action=str(action),
            rule_id=rule_id,
            source=source,
            excerpt=excerpt[:240],
            detail=detail or {},
        )
        self.session.add(event)
        self.session.flush()
        return event
