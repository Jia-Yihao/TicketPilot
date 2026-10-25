"""trace spans

Revision ID: c8e2f5a91d47
Revises: b41d7c9e2f30
Create Date: 2026-10-23 22:40:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'c8e2f5a91d47'
down_revision: str | None = 'b41d7c9e2f30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'trace_spans',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('trace_id', sa.String(length=40), nullable=False),
        sa.Column('parent_span_id', sa.String(length=40), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('conversation_id', sa.String(length=40), nullable=True),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('attributes', sa.JSON(), nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('ended_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('trace_spans', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_trace_spans_trace_id'), ['trace_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_trace_spans_parent_span_id'), ['parent_span_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_trace_spans_tenant_id'), ['tenant_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_trace_spans_conversation_id'),
            ['conversation_id'],
            unique=False,
        )
        batch_op.create_index(batch_op.f('ix_trace_spans_kind'), ['kind'], unique=False)
        batch_op.create_index(
            'ix_trace_spans_trace_started', ['trace_id', 'started_at'], unique=False
        )


def downgrade() -> None:
    op.drop_table('trace_spans')
