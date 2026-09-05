"""v3.3: an idempotency key belongs to a session, not to a principal at large

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index('ux_decisions_principal_idempotency_key', table_name='decisions')
    op.create_index(
        'ux_decisions_principal_session_idempotency_key', 'decisions',
        ['principal', 'session_id', 'idempotency_key'], unique=True,
        postgresql_where=sa.text('idempotency_key IS NOT NULL'),
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index('ux_decisions_principal_session_idempotency_key', table_name='decisions')
    # Narrowing: fails if one principal wrote the same idempotency_key in two
    # sessions after the upgrade. Those rows must be de-duplicated by hand
    # before this downgrade runs -- the same hazard 0007's downgrade carries.
    op.create_index(
        'ux_decisions_principal_idempotency_key', 'decisions',
        ['principal', 'idempotency_key'], unique=True,
        postgresql_where=sa.text('idempotency_key IS NOT NULL'),
    )
