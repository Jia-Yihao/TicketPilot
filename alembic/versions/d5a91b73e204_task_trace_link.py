"""task trace link

Revision ID: d5a91b73e204
Revises: c8e2f5a91d47
Create Date: 2026-10-23 23:20:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'd5a91b73e204'
down_revision: str | None = 'c8e2f5a91d47'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('trace_id', sa.String(length=40), nullable=True))
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tasks_trace_id'), ['trace_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tasks_trace_id'))
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('trace_id')
