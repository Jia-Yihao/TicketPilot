"""sql query ledger

Revision ID: b7c4e19a2f60
Revises: e2f6a4d81c57
Create Date: 2026-10-24 11:05:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b7c4e19a2f60'
down_revision: str | None = 'e2f6a4d81c57'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'sql_queries',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('dataset', sa.String(length=32), nullable=False),
        sa.Column('metric', sa.String(length=32), nullable=False),
        sa.Column('group_by', sa.String(length=16), nullable=True),
        sa.Column('row_status', sa.String(length=24), nullable=True),
        sa.Column('time_range', sa.String(length=32), nullable=True),
        sa.Column('sql_text', sa.Text(), nullable=False),
        sa.Column('params_digest', sa.String(length=64), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('rejected_reason', sa.String(length=48), nullable=True),
        sa.Column('trace_id', sa.String(length=40), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('sql_queries', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_sql_queries_tenant_id'), ['tenant_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_sql_queries_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_sql_queries_trace_id'), ['trace_id'], unique=False
        )
        batch_op.create_index(
            'ix_sql_queries_tenant_created', ['tenant_id', 'created_at'], unique=False
        )


def downgrade() -> None:
    op.drop_table('sql_queries')
