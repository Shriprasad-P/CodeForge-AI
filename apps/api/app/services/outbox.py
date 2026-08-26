from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox import OutboxEvent

EXECUTION_REQUESTED = "execution.requested"
AGENT_RUN_REQUESTED = "agent_run.requested"
PUBLICATION_REQUESTED = "publication.requested"


def event_dedupe_key(event_type: str, aggregate_id: UUID) -> str:
    return f"{event_type}:{aggregate_id}"


def add_outbox_event(
    db: AsyncSession,
    *,
    event_type: str,
    aggregate_id: UUID,
    payload: dict[str, Any] | None = None,
    workflow_correlation_id: UUID | str | None = None,
    request_id: str | None = None,
) -> OutboxEvent:
    """Add an outbox row without committing; caller owns the transaction."""
    event_payload = dict(payload or {"aggregate_id": str(aggregate_id)})
    if workflow_correlation_id is not None:
        event_payload["workflow_correlation_id"] = str(workflow_correlation_id)
    if request_id is not None:
        event_payload["request_id"] = request_id[:128]
    event = OutboxEvent(
        event_type=event_type,
        aggregate_id=aggregate_id,
        dedupe_key=event_dedupe_key(event_type, aggregate_id),
        payload=event_payload,
    )
    db.add(event)
    return event
