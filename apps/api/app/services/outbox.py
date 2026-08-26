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
) -> OutboxEvent:
    """Add an outbox row without committing; caller owns the transaction."""
    event = OutboxEvent(
        event_type=event_type,
        aggregate_id=aggregate_id,
        dedupe_key=event_dedupe_key(event_type, aggregate_id),
        payload=payload or {"aggregate_id": str(aggregate_id)},
    )
    db.add(event)
    return event
