"""The only place that knows a decision row calls its metadata column
`metadata_` -- SQLAlchemy reserves `metadata` on the declarative base.
"""

from agentgate.engine.decision import DecisionView
from agentgate.store.models import DecisionRow

_METADATA = "metadata"


def row_from_view(view: DecisionView) -> DecisionRow:
    data = view.model_dump(exclude={"decision_id"})
    data["metadata_"] = data.pop(_METADATA)
    return DecisionRow(**data)


def view_from_row(row: DecisionRow) -> DecisionView:
    data = {
        column.name: getattr(row, column.name)
        for column in DecisionRow.__table__.columns
        if column.name != _METADATA
    }
    return DecisionView.model_validate(data | {_METADATA: row.metadata_})
