"""llm usage ledger

Revision ID: d7a1f3c9b2e4
Revises: c3b8e5a71d29
Create Date: 2026-10-25 09:10:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'd7a1f3c9b2e4'
down_revision: str | None = 'c3b8e5a71d29'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'llm_usage',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=True),
        sa.Column('conversation_id', sa.String(length=40), nullable=True),
        sa.Column('task_id', sa.String(length=40), nullable=True),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('planning_source', sa.String(length=24), nullable=False),
        sa.Column('model_name', sa.String(length=120), nullable=True),
        sa.Column('requests', sa.Integer(), nullable=False),
        sa.Column('retries', sa.Integer(), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('llm_usage', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_llm_usage_tenant_id'), ['tenant_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_llm_usage_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_llm_usage_conversation_id'),
            ['conversation_id'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_llm_usage_task_id'), ['task_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_llm_usage_created_at'), ['created_at'], unique=False
        )
        batch_op.create_index(
            'ix_llm_usage_tenant_created',
            ['tenant_id', 'created_at'],
            unique=False,
        )
        batch_op.create_index(
            'ix_llm_usage_source_created',
            ['source', 'created_at'],
            unique=False,
        )
        batch_op.create_index(
            'ix_llm_usage_task',
            ['task_id', 'created_at'],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('llm_usage', schema=None) as batch_op:
        batch_op.drop_index('ix_llm_usage_task')
        batch_op.drop_index('ix_llm_usage_source_created')
        batch_op.drop_index('ix_llm_usage_tenant_created')
        batch_op.drop_index(batch_op.f('ix_llm_usage_created_at'))
        batch_op.drop_index(batch_op.f('ix_llm_usage_task_id'))
        batch_op.drop_index(batch_op.f('ix_llm_usage_conversation_id'))
        batch_op.drop_index(batch_op.f('ix_llm_usage_user_id'))
        batch_op.drop_index(batch_op.f('ix_llm_usage_tenant_id'))
    op.drop_table('llm_usage')
