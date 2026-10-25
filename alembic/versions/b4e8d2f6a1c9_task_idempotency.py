"""task idempotency key

Revision ID: b4e8d2f6a1c9
Revises: d7a1f3c9b2e4
Create Date: 2026-10-25 10:40:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b4e8d2f6a1c9'
down_revision: str | None = 'd7a1f3c9b2e4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('idempotency_key', sa.String(length=200), nullable=True)
        )
        batch_op.add_column(
            sa.Column('request_digest', sa.String(length=64), nullable=True)
        )
        batch_op.create_unique_constraint(
            'uq_tasks_idempotency',
            ['tenant_id', 'user_id', 'idempotency_key'],
        )


def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_constraint('uq_tasks_idempotency', type_='unique')
        batch_op.drop_column('request_digest')
        batch_op.drop_column('idempotency_key')
