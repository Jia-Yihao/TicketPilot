from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.enums import ToolExecutionStatus
from app.models.tool_execution import ToolExecution
from app.repositories.tool_executions import ToolExecutionRepository


class ExecutionService:
    def __init__(self, session: Session) -> None:
        self.repo = ToolExecutionRepository(session)
        self.session = session

    def create_pending(
        self,
        *,
        task_id: str,
        tool_name: str,
        idempotency_key: str,
        request_digest: str,
        request_payload: dict[str, Any],
    ) -> tuple[ToolExecution, bool]:
        existing = self.repo.get_by_idempotency(task_id, idempotency_key)
        if existing is not None:
            return existing, False
        execution = ToolExecution(
            id=f"tex_{uuid.uuid4().hex[:16]}",
            task_id=task_id,
            tool_name=tool_name,
            status=ToolExecutionStatus.PENDING,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            request_payload=request_payload,
        )
        self.repo.create(execution)
        return execution, True

    def latest(self, task_id: str) -> ToolExecution | None:
        return self.repo.latest_for_task(task_id)

    def cancel_pending(self, task_id: str, *, reason: str) -> None:
        execution = self.repo.latest_for_task(task_id)
        if execution is not None and execution.status in {
            ToolExecutionStatus.PENDING,
            ToolExecutionStatus.UNKNOWN,
        }:
            execution.status = ToolExecutionStatus.CANCELLED
            execution.error_code = "TASK_CANCELLED"
            execution.error_message = reason
            self.session.flush()
