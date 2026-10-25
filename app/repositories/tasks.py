from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.time import utcnow
from app.models.enums import TaskStatus
from app.models.task import Task, TaskEvent


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, task_id: str) -> Task | None:
        return self.session.get(Task, task_id)

    def require(self, task_id: str) -> Task:
        task = self.get(task_id)
        if task is None:
            raise NotFoundError("Task not found.")
        return task

    def create(
        self,
        *,
        task_id: str,
        tenant_id: str,
        user_id: str,
        request: str,
        context: dict[str, Any],
        trace_id: str | None = None,
    ) -> Task:
        task = Task(
            id=task_id,
            tenant_id=tenant_id,
            user_id=user_id,
            request=request,
            context=context,
            status=TaskStatus.CREATED,
            version=1,
            trace_id=trace_id,
        )
        self.session.add(task)
        self.session.flush()
        return task

    def transition(
        self,
        task: Task,
        to_status: TaskStatus,
        *,
        allowed_from: set[TaskStatus],
        result: dict[str, Any] | None = None,
        expected_version: int | None = None,
    ) -> Task:
        if task.status not in allowed_from:
            raise ConflictError(f"Cannot move task from {task.status} to {to_status}.")
        if expected_version is not None and task.version != expected_version:
            raise ConflictError("Task changed concurrently; reload and retry.")
        task.status = to_status
        task.version += 1
        task.updated_at = utcnow()
        if result is not None:
            task.result = result
        self.session.flush()
        return task

    def append_event(
        self, task_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> None:
        self.session.add(
            TaskEvent(
                id=_new_id("evt"),
                task_id=task_id,
                event_type=event_type,
                payload=payload or {},
            )
        )
        self.session.flush()

    def list_for_user(
        self, tenant_id: str, user_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[Task]:
        stmt = (
            select(Task)
            .where(Task.tenant_id == tenant_id, Task.user_id == user_id)
            .order_by(Task.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt))

    def count_for_user(self, tenant_id: str, user_id: str) -> int:
        stmt = select(func.count(Task.id)).where(
            Task.tenant_id == tenant_id, Task.user_id == user_id
        )
        return int(self.session.scalar(stmt) or 0)

    def events(self, task_id: str, after_id: str | None = None) -> list[TaskEvent]:
        stmt = select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.created_at)
        rows = list(self.session.scalars(stmt))
        if after_id:
            ids = [row.id for row in rows]
            if after_id in ids:
                rows = rows[ids.index(after_id) + 1 :]
        return rows


def _new_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:16]}"
