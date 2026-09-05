"""The only place that knows a decision row calls its metadata column
`metadata_` -- SQLAlchemy reserves `metadata` on the declarative base.
Writes go through a Core insert keyed by column *names*, where the column
is simply `metadata`; only the ORM attribute needs the underscore.
"""

from agentgate.engine.decision import DecisionRecord
from agentgate.store.models import DecisionRow

_METADATA = "metadata"
# Generated in the database from key_id; DecisionRecord has no such field.
_GENERATED = frozenset({"principal"})


def record_from_row(row: DecisionRow) -> DecisionRecord:
    data = {
        column.name: getattr(row, column.name)
        for column in DecisionRow.__table__.columns
        if column.name != _METADATA and column.name not in _GENERATED
    }
    return DecisionRecord.model_validate(data | {_METADATA: row.metadata_})
