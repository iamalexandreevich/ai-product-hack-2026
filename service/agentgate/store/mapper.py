"""The only place that knows a decision row calls its metadata column
`metadata_` -- SQLAlchemy reserves `metadata` on the declarative base.
"""

from agentgate.engine.decision import DecisionRecord
from agentgate.store.models import DecisionRow

_METADATA = "metadata"


def row_from_record(record: DecisionRecord) -> DecisionRow:
    data = record.model_dump(exclude={"decision_id"})
    data["metadata_"] = data.pop(_METADATA)
    return DecisionRow(**data)


def record_from_row(row: DecisionRow) -> DecisionRecord:
    data = {
        column.name: getattr(row, column.name)
        for column in DecisionRow.__table__.columns
        if column.name != _METADATA
    }
    return DecisionRecord.model_validate(data | {_METADATA: row.metadata_})
