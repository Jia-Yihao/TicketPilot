from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.enums import ToolExecutionStatus
from app.models.tool_execution import ToolExecution


class ToolExecutionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, execution_id: str) -> ToolExecution | None:
        return self.session.get(ToolExecution, execution_id)

    def get_for_update(self, execution_id: str) -> ToolExecution | None:
        stmt = (
            select(ToolExecution)
            .where(ToolExecution.id == execution_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return self.session.scalars(stmt).first()

    def claim_for_send(self, execution_id: str) -> bool:
        stmt = (
            update(ToolExecution)
            .where(
                ToolExecution.id == execution_id,
                ToolExecution.status == ToolExecutionStatus.PENDING,
            )
            .values(
                status=ToolExecutionStatus.SENT,
                attempt=ToolExecution.attempt + 1,
                updated_at=utcnow(),
            )
            .returning(ToolExecution.id)
            .execution_options(synchronize_session=False)
        )
        return self.session.execute(stmt).scalar_one_or_none() is not None

    def get_by_idempotency(self, task_id: str, idempotency_key: str) -> ToolExecution | None:
        stmt = select(ToolExecution).where(
            ToolExecution.task_id == task_id,
            ToolExecution.idempotency_key == idempotency_key,
        )
        return self.session.scalars(stmt).first()

    def latest_for_task(self, task_id: str) -> ToolExecution | None:
        stmt = (
            select(ToolExecution)
            .where(ToolExecution.task_id == task_id)
            .order_by(ToolExecution.created_at.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def create(self, execution: ToolExecution) -> ToolExecution:
        self.session.add(execution)
        self.session.flush()
        return execution
