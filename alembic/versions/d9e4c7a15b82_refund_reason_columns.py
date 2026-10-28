"""refund reason, refund type and reason note columns

Revision ID: d9e4c7a15b82
Revises: a1c7e9d4b820
Create Date: 2026-10-26 20:40:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'd9e4c7a15b82'
down_revision: str | None = 'a1c7e9d4b820'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable only, and no backfill: historical refunds keep NULL and read
    # back as "未提供" instead of being stamped with a guess.
    with op.batch_alter_table('refunds', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'reason',
                sa.Enum(
                    'QUALITY',
                    'DAMAGED',
                    'WRONG_ITEM',
                    'NOT_AS_DESCRIBED',
                    'NO_LONGER_NEEDED',
                    'OTHER',
                    name='refundreason',
                    native_enum=False,
                    length=32,
                ),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                'refund_type',
                sa.Enum(
                    'FULL',
                    'PARTIAL',
                    'SHIPPING',
                    'PRICE_PROTECTION',
                    name='refundtype',
                    native_enum=False,
                    length=32,
                ),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column('reason_note', sa.String(length=500), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('refunds', schema=None) as batch_op:
        batch_op.drop_column('reason_note')
        batch_op.drop_column('refund_type')
        batch_op.drop_column('reason')
