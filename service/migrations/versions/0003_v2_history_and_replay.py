"""v2: history, protocol, idempotency key

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-04 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('decisions', sa.Column('protocol', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('decisions', sa.Column('idempotency_key', sa.String(length=128), nullable=True))
    op.add_column('decisions', sa.Column('history', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'))
    op.add_column('decisions', sa.Column('history_digest', sa.String(length=64), nullable=False, server_default=''))
    op.create_index(
        'ux_decisions_idempotency_key', 'decisions', ['idempotency_key'], unique=True,
        postgresql_where=sa.text('idempotency_key IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('ux_decisions_idempotency_key', table_name='decisions')
    op.drop_column('decisions', 'history_digest')
    op.drop_column('decisions', 'history')
    op.drop_column('decisions', 'idempotency_key')
    op.drop_column('decisions', 'protocol')
