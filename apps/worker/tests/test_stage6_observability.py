from __future__ import annotations

from datetime import datetime

from src.main import build_worker_heartbeat


def test_worker_heartbeat_contains_operational_fields() -> None:
    payload = build_worker_heartbeat(
        "worker-1",
        active_claims=-1,
        last_success="2026-08-27T00:00:00+00:00",
        version="AgentDock",
    )
    assert payload["worker_id"] == "worker-1"
    assert payload["active_claims"] == 0
    assert payload["last_success"] == "2026-08-27T00:00:00+00:00"
    datetime.fromisoformat(str(payload["last_heartbeat"]))
