"""v3: rules_level, rules_digest, call_id, kind, provenance, replacement

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('decisions', sa.Column('kind', sa.String(length=8), nullable=False, server_default='decide'))
    op.add_column('decisions', sa.Column('call_id', sa.String(length=128), nullable=True))
    op.add_column('decisions', sa.Column('rules_level', sa.String(length=32), nullable=True))
    op.add_column('decisions', sa.Column('rules_digest', sa.String(length=64), nullable=True))
    op.add_column('decisions', sa.Column('provenance', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('decisions', sa.Column('replacement', sa.Text(), nullable=True))
    # A row whose `decision` is `pass`, `mask` or `drop` is an inspect
    # verdict, never a decide one -- `DecisionKind` has no such values -- so
    # the server default above (needed only to satisfy the NOT NULL
    # constraint while this UPDATE runs) is wrong for exactly those rows.
    # Leaving it uncorrected would let `Replay.of` build a `DecideResponse`
    # out of an `InspectVerdict`, which fails validation (see the per-record
    # restore guard in agentgate/session/replay.py).
    op.execute("UPDATE decisions SET kind = 'inspect' WHERE decision IN ('pass', 'mask', 'drop')")
    op.create_index('ix_decisions_kind_id', 'decisions', ['kind', 'id'])
    op.create_index('ix_decisions_call_id', 'decisions', ['call_id'])
    # The server default above exists only to backfill this migration's own
    # ALTER TABLE against a populated table; a schema `create_all` produces
    # has none, so a migrated database matching it means dropping it once
    # existing rows are backfilled -- otherwise it persists forever and
    # `alembic --autogenerate` keeps proposing to remove it.
    op.alter_column('decisions', 'kind', server_default=None)


def downgrade() -> None:
    op.drop_index('ix_decisions_call_id', table_name='decisions')
    op.drop_index('ix_decisions_kind_id', table_name='decisions')
    op.drop_column('decisions', 'replacement')
    op.drop_column('decisions', 'provenance')
    op.drop_column('decisions', 'rules_digest')
    op.drop_column('decisions', 'rules_level')
    op.drop_column('decisions', 'call_id')
    op.drop_column('decisions', 'kind')
