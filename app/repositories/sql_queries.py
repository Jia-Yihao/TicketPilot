from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.security import Identity
from app.models.sql_query import SqlQuery

__all__ = ["SqlQueryRepository", "is_staff"]


def is_staff(identity: Identity) -> bool:
    """A seat may read the whole tenant's ledger; a customer reads only their own."""
    return identity.has_role("support") or identity.has_role("approver")


class SqlQueryRepository:
    """Reads the execution ledger."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _scoped(self, identity: Identity) -> Select[tuple[SqlQuery]]:
        query = select(SqlQuery).where(SqlQuery.tenant_id == identity.tenant_id)
        if not is_staff(identity):
            query = query.where(SqlQuery.user_id == identity.user_id)
        return query

    def list(
        self,
        identity: Identity,
        *,
        dataset: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SqlQuery]:
        query = self._scoped(identity)
        if dataset:
            query = query.where(SqlQuery.dataset == dataset)
        if status:
            query = query.where(SqlQuery.status == status.upper())
        query = (
            query.order_by(SqlQuery.created_at.desc(), SqlQuery.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.execute(query).scalars().all())

    def count(
        self,
        identity: Identity,
        *,
        dataset: str | None = None,
        status: str | None = None,
    ) -> int:
        query = self._scoped(identity).with_only_columns(
            func.count(SqlQuery.id)
        )
        if dataset:
            query = query.where(SqlQuery.dataset == dataset)
        if status:
            query = query.where(SqlQuery.status == status.upper())
        return int(self.session.execute(query).scalar_one())

    def get(self, identity: Identity, query_id: str) -> SqlQuery | None:
        return self.session.execute(
            self._scoped(identity).where(SqlQuery.id == query_id)
        ).scalar_one_or_none()
