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
    op.add_column('decisions', sa.Column('history_omitted', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('decisions', sa.Column('history_digest', sa.String(length=64), nullable=False, server_default=''))
    op.add_column('decisions', sa.Column('request_digest', sa.String(length=64), nullable=False, server_default=''))
    op.create_index(
        'ux_decisions_idempotency_key', 'decisions', ['idempotency_key'], unique=True,
        postgresql_where=sa.text('idempotency_key IS NOT NULL'),
    )
    # The server defaults above exist only to backfill this migration's own
    # ALTER TABLE against a populated table; a schema `create_all` produces
    # has none, so a migrated database matching it means dropping them once
    # existing rows are backfilled -- otherwise they persist forever and
    # `alembic --autogenerate` keeps proposing to remove them.
    op.alter_column('decisions', 'protocol', server_default=None)
    op.alter_column('decisions', 'history', server_default=None)
    op.alter_column('decisions', 'history_omitted', server_default=None)
    op.alter_column('decisions', 'history_digest', server_default=None)
    op.alter_column('decisions', 'request_digest', server_default=None)


def downgrade() -> None:
    op.drop_index('ux_decisions_idempotency_key', table_name='decisions')
    op.drop_column('decisions', 'request_digest')
    op.drop_column('decisions', 'history_digest')
    op.drop_column('decisions', 'history_omitted')
    op.drop_column('decisions', 'history')
    op.drop_column('decisions', 'idempotency_key')
    op.drop_column('decisions', 'protocol')
