"""v3.2: attribute a decision to the API key, and scope replays to the principal

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('decisions', sa.Column('key_id', sa.String(length=26), nullable=True))
    op.add_column(
        'decisions',
        sa.Column(
            'principal', sa.String(length=26),
            sa.Computed("coalesce(key_id, 'token')", persisted=True), nullable=False,
        ),
    )
    op.create_index('ix_decisions_key_id_ts', 'decisions', ['key_id', 'ts'])
    op.drop_index('ux_decisions_idempotency_key', table_name='decisions')
    op.create_index(
        'ux_decisions_principal_idempotency_key', 'decisions', ['principal', 'idempotency_key'],
        unique=True, postgresql_where=sa.text('idempotency_key IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('ux_decisions_principal_idempotency_key', table_name='decisions')
    op.create_index(
        'ux_decisions_idempotency_key', 'decisions', ['idempotency_key'],
        unique=True, postgresql_where=sa.text('idempotency_key IS NOT NULL'),
    )
    op.drop_index('ix_decisions_key_id_ts', table_name='decisions')
    op.drop_column('decisions', 'principal')
    op.drop_column('decisions', 'key_id')
