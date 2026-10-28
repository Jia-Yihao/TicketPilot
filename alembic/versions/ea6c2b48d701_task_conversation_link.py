"""task conversation link

Revision ID: ea6c2b48d701
Revises: d9e4c7a15b82
Create Date: 2026-10-28 09:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'ea6c2b48d701'
down_revision: str | None = 'd9e4c7a15b82'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable only, and no backfill: historical tasks keep NULL and read
    # back as "本列上线前建的单" instead of being stamped with a guess.
    # Shape copied from d5a91b73e204 (tasks.trace_id): bare column + index,
    # no foreign key.
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('conversation_id', sa.String(length=40), nullable=True)
        )
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_tasks_conversation_id'), ['conversation_id'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tasks_conversation_id'))
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('conversation_id')
