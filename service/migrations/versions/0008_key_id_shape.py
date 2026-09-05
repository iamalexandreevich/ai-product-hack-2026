"""v3.3: the shape of a key id is a constraint, not a convention

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-05 00:00:00.000000
"""
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

# Kept literal on purpose: a migration is a frozen snapshot of the schema at
# one moment, and importing agentgate.domain.principal would let a later edit
# of that constant silently rewrite history. tests/domain/test_principal.py
# reads this file and checks the literal still equals KEY_ID_PATTERN.
_ULID = "^[0-9A-HJKMNP-TV-Z]{26}$"


def upgrade() -> None:
    # Fails, deliberately, if any historical row is outside the shape: a
    # key id that the service never minted is an incident, and silently
    # rewriting it would destroy the trace of one.
    op.create_check_constraint('ck_api_keys_id_ulid', 'api_keys', f"id ~ '{_ULID}'")
    op.create_check_constraint(
        'ck_decisions_key_id_ulid', 'decisions', f"key_id IS NULL OR key_id ~ '{_ULID}'"
    )


def downgrade() -> None:
    op.drop_constraint('ck_decisions_key_id_ulid', 'decisions', type_='check')
    op.drop_constraint('ck_api_keys_id_ulid', 'api_keys', type_='check')
