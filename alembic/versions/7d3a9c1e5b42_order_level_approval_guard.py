"""order-level approval guard

Revision ID: 7d3a9c1e5b42
Revises: 322b3f652b72
Create Date: 2026-10-23 18:40:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '7d3a9c1e5b42'
down_revision: str | None = '322b3f652b72'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREDICATE = sa.text("status IN ('PENDING', 'APPROVED')")


def upgrade() -> None:
    op.create_index(
        'uq_approvals_open_per_resource',
        'approvals',
        ['resource_type', 'resource_id', 'action_type'],
        unique=True,
        sqlite_where=_PREDICATE,
        postgresql_where=_PREDICATE,
    )


def downgrade() -> None:
    op.drop_index(
        'uq_approvals_open_per_resource',
        table_name='approvals',
        sqlite_where=_PREDICATE,
        postgresql_where=_PREDICATE,
    )
