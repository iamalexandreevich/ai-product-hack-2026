"""v4: applied spans, redaction counter, rejected model spans

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-05 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('decisions', sa.Column('spans', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'))
    op.add_column('decisions', sa.Column('redacted', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('decisions', sa.Column('spans_rejected', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('decisions', 'spans_rejected')
    op.drop_column('decisions', 'redacted')
    op.drop_column('decisions', 'spans')
